"""Saved episodes remain playable when every upstream operation is forbidden."""

import asyncio
import json
import threading
from types import SimpleNamespace

import anyio
from fastapi.testclient import TestClient
import pytest

from app import library_paths, store
from app.config import settings
from app.main import app
from app.online import cache, providers, service
from app.online.sessions import SessionError, SessionRegistry
from app.store import online


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "local.db"))
    monkeypatch.setattr(store._base, "ensure_dirs", lambda: None)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    library_paths.invalidate_cache()
    store.init_db()
    registry = SessionRegistry()
    monkeypatch.setattr(service, "registry", registry)
    source = online.create_online_source({"name": "Saved source", "provider": "maccms",
        "api_base_url": "https://catalog.example/api", "allowed_hosts": ["media.example"], "enabled": False})
    item = online.upsert_online_item(source["id"], "saved-show", "tv", "Saved show")
    episode = online.upsert_online_episode(item["id"], "line-episode", "Episode 1")
    online.update_online_settings(True)
    identity = {"source_id": source["id"], "item_id": item["id"], "kind": "episode",
                "episode_id": episode["id"], "line_id": "ln_" + "a" * 32}
    cache_id = "oc_" + "b" * 32
    package = {"entry_name": "movie.mp4", "transport": "mp4", "files": ["movie.mp4"]}
    (tmp_path / "movie.mp4").write_bytes(b"0123456789abcdef")
    marked = []

    def forbidden(*_args, **_kwargs):
        pytest.fail("a local playback operation attempted an upstream request")

    def lookup(value):
        value = value.model_dump() if hasattr(value, "model_dump") else value
        return {"id": cache_id, "duration_seconds": 12.0} if value == identity else None

    def asset_path(cid, name):
        if cid != cache_id or name not in package["files"]:
            raise cache.CacheError("online_cache_not_found", 404)
        return tmp_path / name

    monkeypatch.setattr(providers, "resolve", forbidden)
    monkeypatch.setattr(service, "create_online_client", forbidden)
    monkeypatch.setattr(cache, "lookup", lookup)
    monkeypatch.setattr(cache, "package_files", lambda cid: package if cid == cache_id else None)
    monkeypatch.setattr(cache, "asset_path", asset_path)
    monkeypatch.setattr(cache, "mark_corrupt", lambda cid, *_args: marked.append(cid))
    with store._base._conn() as connection:
        connection.execute("INSERT INTO online_cache_tasks VALUES(?,?,?,?,?,?,?,?)",
                           (cache_id, json.dumps(identity, sort_keys=True), item["id"], source["id"], "ready", "{}", 1, 1))
    client = TestClient(app)
    client.get("/api/viewing/session").raise_for_status()
    try:
        yield SimpleNamespace(client=client, identity=identity, source=source, item=item,
                              registry=registry, package=package, root=tmp_path, id=cache_id, marked=marked)
    finally:
        registry.close_all()
        client.close()
        library_paths.invalidate_cache()


def create(local):
    response = local.client.post("/api/online/sessions", json=local.identity)
    assert response.status_code == 201, response.text
    return response.json()


def test_source_disabled_local_descriptor_and_full_head_ranges(local):
    descriptor = create(local)
    assert descriptor["processing"] == "passthrough"
    assert "cache_id" not in descriptor and "storage" not in descriptor
    url = descriptor["media_url"]
    response = local.client.get(url)
    assert response.status_code == 200 and response.content == b"0123456789abcdef"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["accept-ranges"] == "bytes"
    response = local.client.head(url)
    assert response.status_code == 200 and response.content == b""
    assert response.headers["content-length"] == "16"
    for value, expected in [("bytes=2-5", b"2345"), ("bytes=12-", b"cdef"),
                            ("bytes=-3", b"def"), ("bytes=14-100", b"ef")]:
        response = local.client.get(url, headers={"Range": value})
        assert response.status_code == 206 and response.content == expected
    for value in ("bytes=20-", "bytes=4-2", "bytes=-0", "bytes=0-1,3-4", "bad"):
        response = local.client.get(url, headers={"Range": value})
        assert response.status_code == 416 and response.headers["content-range"] == "bytes */16"
    assert local.registry.stats()["active_requests"] == 0


def test_hls_rewrites_every_local_reference_to_owned_opaque_resources(local):
    local.package.update(entry_name="master.m3u8", transport="hls",
                         files=["master.m3u8", "video.m3u8", "init.mp4", "segment.ts", "sub.vtt"])
    (local.root / "master.m3u8").write_text(
        '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nvideo.m3u8\n')
    (local.root / "video.m3u8").write_text(
        '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MAP:URI="init.mp4"\n'
        '#EXTINF:6,\n#EXT-X-BYTERANGE:4@0\nsegment.ts\n#EXT-X-ENDLIST\n')
    (local.root / "init.mp4").write_bytes(b"init")
    (local.root / "segment.ts").write_bytes(b"data")
    (local.root / "sub.vtt").write_text("WEBVTT\n\n")
    descriptor = create(local)
    master = local.client.get(descriptor["media_url"])
    assert master.status_code == 200
    variant_url = next(line for line in master.text.splitlines() if line.startswith("/api/"))
    variant = local.client.get(variant_url)
    assert variant.status_code == 200 and "#EXT-X-BYTERANGE:4@0" in variant.text
    assert "cache.invalid" not in variant.text and "segment.ts" not in variant.text
    segment_url = next(line for line in variant.text.splitlines() if line.startswith("/api/"))
    assert local.client.get(segment_url, headers={"Range": "bytes=0-3"}).content == b"data"
    session = local.registry.sessions()[0]
    assert session.client is None
    assert local.client.get(session._local_files["sub.vtt"].path(session.id)).content == b"WEBVTT\n\n"


def test_local_identity_never_matches_another_line(local):
    response = local.client.post("/api/online/sessions", json={**local.identity, "line_id": "ln_" + "c" * 32})
    assert response.status_code == 503
    assert response.json()["code"] == "online_source_disabled"


def test_cache_only_intent_never_falls_back_when_cache_deleted_or_not_ready(local, monkeypatch):
    online.update_online_source(local.source["id"], {"enabled": True})
    monkeypatch.setattr(cache, "lookup", lambda _identity: None)
    response = local.client.post("/api/online/sessions", json=local.identity,
                                 headers={"X-Online-Cache-Only": "true"})
    assert response.status_code == 409 and response.json()["code"] == "online_cache_not_ready"
    assert local.registry.stats()["sessions"] == 0


def test_local_ownership_feature_and_permission_revocation(local):
    descriptor = create(local)
    other = TestClient(app)
    try:
        other.get("/api/viewing/session").raise_for_status()
        assert other.get(descriptor["media_url"]).status_code == 404
    finally:
        other.close()
    local.registry.revoke_source(local.source["id"])
    service.sweep()
    assert local.client.get(descriptor["media_url"]).status_code == 200
    online.update_online_settings(False)
    response = local.client.get(descriptor["media_url"])
    assert response.status_code == 503
    assert local.registry.stats()["sessions"] == 0


def test_viewing_generation_revokes_a_local_session(local):
    descriptor = create(local)
    with store._base._conn() as connection:
        connection.execute("UPDATE viewing_sessions SET generation=generation+1")
    response = local.client.get(descriptor["media_url"])
    assert response.status_code == 401
    assert local.registry.stats()["sessions"] == 0


def test_cached_source_disabled_episode_retains_device_progress_claim(local):
    descriptor = create(local)
    state = local.client.get(f'/api/online/state/items/{local.item["id"]}')
    assert state.status_code == 200 and state.json()["available"]
    response = local.client.post("/api/online/state/progress/claim", json={
        "session_id": descriptor["session_id"], "expected_revision": 0},
        headers={"X-Online-State-Scope": state.json()["scope_token"]})
    assert response.status_code == 200, response.text


def test_delete_closes_local_file_readers_and_only_matching_cache_sessions(local):
    descriptor = create(local)
    session = local.registry.sessions()[0]
    source = (local.root / "movie.mp4").open("rb")
    release = local.registry.attach_local_reader(session, source.close)
    local.registry.close_cache("oc_" + "e" * 32)
    assert not source.closed
    local.registry.close_cache(local.id)
    assert source.closed
    release()
    assert local.client.get(descriptor["media_url"]).status_code == 404


@pytest.mark.parametrize("spec_version", ["2.0", "2.4"])
def test_cancel_before_body_iteration_closes_local_file_and_reader(local, spec_version):
    from app.online.cache_playback import _LocalResponse, _open

    create(local)
    session = local.registry.sessions()[0]
    source, _size, release = _open(local.registry, session, session.entry)
    iterated = []

    def chunks():
        try:
            iterated.append(True)
            yield b"payload"
        finally:
            release()

    async def scenario():
        started = anyio.Event()

        async def receive():
            await anyio.sleep_forever()

        async def send(message):
            if message["type"] == "http.response.start":
                started.set()
                await anyio.sleep_forever()

        response = _LocalResponse(chunks(), release)
        async with anyio.create_task_group() as group:
            group.start_soon(response, {"type": "http", "asgi": {"spec_version": spec_version}}, receive, send)
            await started.wait()
            group.cancel_scope.cancel()

    try:
        anyio.run(scenario)
        assert not iterated
        assert source.closed
        assert not session._local_readers
    finally:
        release()


def test_cancel_during_local_open_reclaims_late_worker_result(local, monkeypatch):
    from app.online.cache_playback import stream_response

    create(local)
    session = local.registry.sessions()[0]
    opened, continue_worker, released = threading.Event(), threading.Event(), threading.Event()
    sources = []
    attach = local.registry.attach_local_reader

    def delayed_attach(current, close):
        detach = attach(current, close)
        sources.append(close.__self__)
        opened.set()
        assert continue_worker.wait(5)

        def release():
            detach()
            released.set()

        return release

    monkeypatch.setattr(local.registry, "attach_local_reader", delayed_attach)

    async def scenario():
        task = asyncio.create_task(stream_response(SimpleNamespace(method="GET", headers={}),
                                                  local.registry, session, session.entry))
        try:
            assert await asyncio.to_thread(opened.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert session._local_readers
            continue_worker.set()
            assert await asyncio.to_thread(released.wait, 5)
        finally:
            continue_worker.set()

    try:
        asyncio.run(scenario())
        assert len(sources) == 1 and sources[0].closed
        assert not session._local_readers
    finally:
        continue_worker.set()


def test_local_reader_limits_close_rejected_fds_and_reuse_capacity_after_release(local):
    create(local)
    first = local.registry.sessions()[0]
    second = local.registry.create_local(local.identity, "second-local-owner", local.id, local.package)
    third = local.registry.create_local(local.identity, "third-local-owner", local.id, local.package)
    opened, releases = [], []

    def attach(session):
        source = (local.root / "movie.mp4").open("rb")
        opened.append(source)
        release = local.registry.attach_local_reader(session, source.close)
        releases.append(release)
        return source, release

    def rejected(session):
        source = (local.root / "movie.mp4").open("rb")
        opened.append(source)
        with pytest.raises(SessionError, match="request_limit"):
            local.registry.attach_local_reader(session, source.close)
        assert source.closed

    try:
        per_session = local.registry.limits.max_source_active
        first_readers = [attach(first) for _index in range(per_session)]
        rejected(first)
        # Explicit release returns a slot, and repeating it cannot free another reader's slot.
        first_readers[0][1]()
        first_readers[0][1]()
        assert first_readers[0][0].closed
        replacement, _release = attach(first)
        assert not replacement.closed
        rejected(first)
        for _index in range(local.registry.limits.max_active - per_session):
            attach(second)
        rejected(third)
        assert local.registry.stats()["active_requests"] == 0  # Disk readers do not consume upstream leases.

        # Closing a session returns all its global slots; old detach callbacks remain harmless.
        local.registry.close(first.id, first.owner)
        assert all(source.closed for source, _release in first_readers)
        assert replacement.closed
        resumed, _release = attach(third)
        for release in releases[:-1]:
            release()
        assert not resumed.closed
    finally:
        local.registry.close_all()
        for release in releases:
            release()
    assert all(source.closed for source in opened)


def test_missing_file_reports_corruption_and_never_resolves_source(local):
    descriptor = create(local)
    (local.root / "movie.mp4").unlink()
    response = local.client.get(descriptor["media_url"])
    assert response.status_code == 409 and response.json()["code"] == "online_cache_corrupt"
    assert local.marked == [local.id]


def test_unknown_local_manifest_reference_is_corrupt(local):
    local.package.update(entry_name="video.m3u8", transport="hls", files=["video.m3u8"])
    (local.root / "video.m3u8").write_text(
        '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nhttps://media.example/leak.ts\n#EXT-X-ENDLIST\n')
    response = local.client.get(create(local)["media_url"])
    assert response.status_code == 409 and "media.example" not in response.text
    assert local.marked == [local.id]
