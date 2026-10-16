"""Protected media remain revocable after a response has started."""
import asyncio
import json

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app import store, viewing
from app.main import app
from app.routers.stream import common, previews, session
from app.routers.blob import media_response
from app.storage import LocalStorageBackend


@pytest.fixture
def device(tmp_path, monkeypatch):
    import app.store._base as base
    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "resources.db"))
    store.init_db()
    client = TestClient(app)
    client.get("/api/viewing/session")
    client.post("/api/viewing/password", json={"new_password": "123456"})
    movie_id = store.upsert_movie_by_path("synthetic.mkv")
    current, _, _ = viewing.resolve(client.cookies[viewing.COOKIE])
    directory = tmp_path / "hls"
    directory.mkdir()
    data = b"synthetic-media-" * 200000
    (directory / "media.mp4").write_bytes(data)
    lease = {"vid": movie_id, "kind": "movie", "sdir": str(directory), "proc": None,
             "viewing_owner": current["token_hash"], "viewing_generation": current["generation"]}
    monkeypatch.setitem(common._sessions, "isolated-resource", lease)
    yield client, movie_id, viewing.context_for(current), directory, data
    client.close()


def test_hls_range_and_owner_isolation(device):
    client, _, _, _, data = device
    response = client.get("/api/stream/sessions/isolated-resource/media.mp4", headers={"Range": "bytes=13-31"})
    assert response.status_code == 206
    assert response.content == data[13:32]
    assert response.headers["cache-control"] == "no-store"
    other = TestClient(app)
    try:
        assert other.get("/api/stream/sessions/isolated-resource/media.mp4").status_code == 404
        assert other.delete("/api/stream/sessions/isolated-resource").status_code == 404
    finally:
        other.close()
    assert "isolated-resource" in common._sessions


@pytest.mark.parametrize("revocation", ["password", "content"])
def test_inflight_mp4_stops_after_revocation(device, revocation):
    _, _, access, _, data = device
    token = viewing.current.set(access)
    try:
        response = session.hls_session_file("isolated-resource", "media.mp4", Request({"type": "http", "headers": []}))
    finally:
        viewing.current.reset(token)

    async def consume():
        first = await anext(response.body_iterator)
        assert 0 < len(first) < len(data)
        if revocation == "password":
            viewing.set_password("changed-password")
        else:
            with viewing._connection() as c:
                viewing.invalidate_content_access(c)
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)
        await response.background()

    asyncio.run(consume())


def test_preview_cache_needs_visible_owner_even_for_old_urls(device, monkeypatch, tmp_path):
    client, mid, _, _, _ = device
    cache = tmp_path / "previews"
    key = "a" * 32
    directory = cache / key
    directory.mkdir(parents=True)
    monkeypatch.setattr(previews, "_root", lambda: cache)
    (directory / "page_0000.jpg").write_bytes(b"synthetic-image")
    path = f"/api/stream/previews/cache/{key}/page_0000.jpg"
    assert client.get(path).status_code == 404
    (directory / "manifest.json").write_text(json.dumps({"item_id": mid, "item_kind": "movie",
                                                        "version": previews.VERSION,
                                                        "pages": ["page_0000.jpg"], "count": 1}))
    assert client.get(path).status_code == 200
    client.post("/api/viewing/unlock", json={"password": "123456"})
    client.patch("/api/viewing/policy", json={"kind": "movie", "ids": [mid], "is_private": True})
    assert client.get(path).status_code == 404
    client.post("/api/viewing/mode", json={"mode": "private", "password": "123456"})
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_completed_previews_require_management_and_never_leak_private_titles(device, monkeypatch, tmp_path):
    client, mid, _, _, _ = device
    backend = LocalStorageBackend({"id": 1, "path": str(tmp_path)})
    source = tmp_path / "synthetic.mkv"
    source.write_bytes(b"isolated-source")
    monkeypatch.setattr(previews.storage, "backend_for", lambda _lid: backend)
    cache = tmp_path / "completed-previews"
    monkeypatch.setattr(previews, "_root", lambda: cache)
    src = previews.storage.MediaSource(backend, "synthetic.mkv", "", None,
                                      source.stat().st_size, source.stat().st_mtime)
    key = previews._cache_key(store.get_playable("movie", mid), src, 10)
    directory = cache / key
    directory.mkdir(parents=True)
    (directory / "page_0000.jpg").write_bytes(b"isolated-sprite")
    previews._write_manifest(directory, dict(version=previews.VERSION, quality_revision=previews.QUALITY_REVISION,
                                            state="ready", interval=10, duration=10, count=1,
                                            width=previews.WIDTH, height=previews.HEIGHT,
                                            columns=previews.COLUMNS, rows=previews.ROWS,
                                            pages=["page_0000.jpg"], item_kind="movie", item_id=mid))
    client.post("/api/viewing/unlock", json={"password": "123456"})
    client.patch("/api/viewing/policy", json={"kind": "movie", "ids": [mid], "is_private": True})
    path = "/api/stream/previews/completed"
    allowed = client.get(path)
    assert allowed.status_code == 200 and allowed.json()["total"] == 1
    assert allowed.json()["items"][0]["file_name"] == "synthetic.mkv"
    assert allowed.headers["cache-control"] == "no-store"

    client.post("/api/viewing/lock")
    denied = client.get(path)
    assert denied.status_code == 403 and denied.json()["code"] == "management_required"
    assert "synthetic.mkv" not in denied.text and "items" not in denied.json()
    client.post("/api/viewing/mode", json={"mode": "private", "password": "123456"})
    assert client.get(path).status_code == 200  # Entering private mode grants management temporarily.
    current, _, _ = viewing.resolve(client.cookies[viewing.COOKIE])
    with viewing._connection() as c:
        c.execute("UPDATE viewing_sessions SET management_until=0 WHERE token_hash=?", (current["token_hash"],))
    assert client.get(path).status_code == 403
    client.post("/api/viewing/mode", json={"mode": "children"})
    denied = client.get(path)
    assert denied.status_code == 403 and denied.json()["code"] == "management_required"
    assert "synthetic.mkv" not in denied.text


def test_management_file_stream_stops_when_temporary_grant_expires(device, monkeypatch):
    client, _, _, directory, data = device
    client.post("/api/viewing/unlock", json={"password": "123456"})
    current, _, _ = viewing.resolve(client.cookies[viewing.COOKIE])
    request = Request({"type": "http", "headers": []})
    request.state.viewing_context = viewing.context_for(current)
    response = media_response(request, LocalStorageBackend({"path": str(directory)}), "media.mp4")

    async def consume():
        first = await anext(response.body_iterator)
        assert 0 < len(first) < len(data)
        monkeypatch.setattr(viewing.time, "time", lambda: current["management_until"] + 1)
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)
        await response.background()

    asyncio.run(consume())


def test_orphan_extra_is_a_management_file_not_an_unprotected_playback_route(device):
    client, mid, _, _, _ = device
    client.post("/api/viewing/unlock", json={"password": "123456"})
    client.patch("/api/viewing/policy", json={"kind": "movie", "ids": [mid], "is_private": True})
    extra_id = store.upsert_extra("synthetic-bonus.mkv", mid)
    store.delete_movie(mid)
    assert store.get_extra(extra_id)["movie_id"] is None
    for path in (f"/api/tv/extras/{extra_id}/blob",
                 f"/api/stream/{extra_id}/media?kind=extra",
                 f"/api/stream/progress?version_id={extra_id}&kind=extra"):
        assert client.get(path).status_code == 404


def test_legacy_extra_with_two_parents_cannot_bypass_the_private_parent(device):
    client, mid, _, _, _ = device
    client.post("/api/viewing/unlock", json={"password": "123456"})
    client.patch("/api/viewing/policy", json={"kind": "movie", "ids": [mid], "is_private": True})
    show_id = store.upsert_show(1, "Public synthetic show")
    extra_id = store.upsert_extra("old-dual-owner.mkv", mid)
    with viewing._connection() as c:
        c.execute("UPDATE extras SET show_id=? WHERE id=?", (show_id, extra_id))
    assert client.get(f"/api/stream/progress?version_id={extra_id}&kind=extra").status_code == 404
