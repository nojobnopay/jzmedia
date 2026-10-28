"""The formal P2 path parses isolated MacCMS HTTP JSON, never provider stubs."""

from contextlib import closing
from pathlib import Path
from threading import Event
import re

from fastapi.testclient import TestClient
import pytest

from app import store
from app.online import health
from scripts.online_p2.fixtures import MARKER, MARKER_TEXT, fixture_root
from scripts.online_p2.server import FIXTURE_TOKEN, isolated_fixture


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "jzmedia-online-p2-test"
    root.mkdir()
    (root / MARKER).write_text(MARKER_TEXT)
    for episode in ("ep-1", "ep-2"):
        folder = root / "media" / episode
        variants = folder / "hls" / "variants"
        variants.mkdir(parents=True)
        (folder / "movie.mp4").write_bytes(b"0123456789" * 100)
        (folder / "hls" / "master.m3u8").write_text(
            '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nvariants/low.m3u8\n')
        (folder / "hls" / "init.mp4").write_bytes(b"init-bytes")
        (variants / "part-000.m4s").write_bytes(b"segment-bytes")
        (variants / "low.m3u8").write_text(
            '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MAP:URI="../init.mp4"\n'
            '#EXTINF:4,\npart-000.m4s\n#EXT-X-ENDLIST\n')
    return root


@pytest.fixture
def client(prepared):
    with isolated_fixture(prepared) as app, TestClient(app) as browser:
        browser.headers["X-Api-Token"] = FIXTURE_TOKEN
        assert browser.get("/api/viewing/session").status_code == 200
        yield browser


def add_source(client, *, name="隔离MacCMS"):
    bootstrap = client.get("/api/p2/bootstrap").json()
    response = client.post("/api/online/sources", json={"provider": "maccms", "name": name,
        "api_base_url": bootstrap["api_base_url"], "allowed_hosts": bootstrap["allowed_hosts"], "enabled": True})
    assert response.status_code == 201, response.text
    assert client.patch("/api/online/settings", json={"enabled": True}).status_code == 200
    return response.json()


def detail(client, source, external_id="movie-1"):
    response = client.get("/api/online/catalog/detail", params={"source_id": source["id"], "external_id": external_id})
    assert response.status_code == 200, response.text
    return response.json()


def entry_for(value, transport):
    return next(entry for line in value["lines"] for entry in line["entries"] if entry["transport"] == transport)


def test_health_observes_actual_catalogue_and_media_reads_without_probing(client):
    source = add_source(client)
    path = "/api/online/catalog/health"
    params = {"source_id": source["id"]}
    initial = client.get(path, params=params)
    assert initial.status_code == 200
    assert initial.json()["purposes"]["api"]["state"] == "unknown"
    value = detail(client, source)
    response = client.get(path, params=params)
    assert response.json()["purposes"]["api"]["state"] == "success"
    assert response.json()["purposes"]["media"]["state"] == "unknown"
    descriptor = client.post("/api/online/sessions", json=entry_for(value, "mp4")["identity"]).json()
    assert client.get(descriptor["media_url"], headers={"Range": "bytes=0-31"}).content == (b"0123456789" * 100)[:32]
    before = len(client.get("/api/p2/metrics").json()["origin_calls"])
    observed = client.get(path, params=params)
    assert observed.status_code == 200
    assert observed.json()["cache_scope"] == "process"
    media = observed.json()["purposes"]["media"]
    assert media["state"] == "success" and media["mode"] == "direct" and media["stage"] == "bytes"
    assert "fixture.invalid" not in observed.text and "proxy_url" not in observed.text
    assert len(client.get("/api/p2/metrics").json()["origin_calls"]) == before
    assert client.delete(f"/api/online/sessions/{descriptor['session_id']}").status_code == 204


def test_health_requires_a_viewing_device_and_rejects_extra_targets(client):
    source = add_source(client)
    stranger = TestClient(client.app)
    try:
        response = stranger.get("/api/online/catalog/health", params={"source_id": source["id"]})
        assert response.status_code == 401
    finally:
        stranger.close()
    response = client.get("/api/online/catalog/health", params={"source_id": source["id"], "url": "https://private.invalid/secret"})
    assert response.status_code == 422 and "private.invalid" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/online/catalog/health", params={"source_id": "src_" + "f" * 32}).status_code == 404


def test_health_bookkeeping_failure_does_not_change_catalogue_or_streamed_bytes(client, monkeypatch):
    observed_stages = []
    media_observed = Event()

    def broken_finish(observation, error=None):
        observed_stages.append(observation.stage)
        if observation.stage == "bytes":
            media_observed.set()
        raise RuntimeError("health bookkeeping failed")

    monkeypatch.setattr(health.registry, "finish", broken_finish)
    source = add_source(client)
    value = detail(client, source)
    descriptor_response = client.post("/api/online/sessions", json=entry_for(value, "mp4")["identity"])
    assert descriptor_response.status_code == 201, descriptor_response.text
    descriptor = descriptor_response.json()
    response = client.get(descriptor["media_url"], headers={"Range": "bytes=0-31"})
    assert response.status_code == 206
    assert response.headers["Content-Range"] == "bytes 0-31/1000"
    assert response.content == (b"0123456789" * 100)[:32]
    assert media_observed.wait(2), "The byte producer finishes its observation after delivering the body"
    assert "catalogue" in observed_stages and "bytes" in observed_stages
    assert client.delete(f"/api/online/sessions/{descriptor['session_id']}").status_code == 204
    assert client.get("/api/p2/metrics").json()["registry"]["sessions"] == 0


def test_fixture_guard_refuses_unmarked_and_production_paths(tmp_path):
    for path in (Path("/data"), tmp_path, Path("/tmp/arbitrary-directory")):
        with pytest.raises(ValueError):
            fixture_root(path)
    root = tmp_path / "jzmedia-online-p2-empty"
    root.mkdir()
    with pytest.raises(ValueError, match="generate"):
        fixture_root(root, require_media=True)


def test_initial_feature_and_local_media_are_empty(client):
    response = client.get("/api/online/catalog/sources")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "items": []}
    metrics = client.get("/api/p2/metrics").json()
    assert metrics["catalog_rows"] == {"online_sources": 0, "online_items": 0, "online_episodes": 0}
    assert metrics["local_rows"] == {"movies": 0, "tv_episodes": 0, "playback_progress": 0}


def test_real_maccms_search_detail_and_owned_mp4_passthrough(client):
    source = add_source(client)
    public = client.get("/api/online/catalog/sources").json()
    assert public["items"][0]["id"] == source["id"]
    assert "api_base_url" not in str(public) and "fixture.invalid" not in str(public)
    searched = client.get("/api/online/catalog/search", params={"source_id": source["id"], "q": "合成", "page": 1})
    assert searched.status_code == 200
    assert len(searched.json()["items"]) == 3
    value = detail(client, source)
    assert len(value["lines"]) == 2
    assert "fixture.invalid" not in str(value) and "fixture_signature" not in str(value)
    identity = entry_for(value, "mp4")["identity"]
    created = client.post("/api/online/sessions", json=identity)
    assert created.status_code == 201, created.text
    descriptor = created.json()
    assert descriptor["processing"] == "passthrough" and descriptor["time_offset_seconds"] == 0
    response = client.get(descriptor["media_url"], headers={"Range": "bytes=0-9"})
    assert response.status_code == 206 and response.content == b"0123456789"
    assert client.delete(f"/api/online/sessions/{descriptor['session_id']}").status_code == 204
    metrics = client.get("/api/p2/metrics").json()
    assert metrics["registry"]["sessions"] == 0
    assert {call["purpose"] for call in metrics["client_calls"]} == {"api", "media"}
    assert {call["mode"] for call in metrics["client_calls"]} == {"direct"}
    assert not any(call["app_credentials"] for call in metrics["origin_calls"])
    assert any(call["action"] == "list" for call in metrics["origin_calls"])
    assert any(call["action"] == "detail" for call in metrics["origin_calls"])


def test_real_maccms_hls_rewrites_nested_assets(client):
    source = add_source(client)
    identity = entry_for(detail(client, source), "hls")["identity"]
    created = client.post("/api/online/sessions", json=identity)
    assert created.status_code == 201, created.text
    master = client.get(created.json()["media_url"])
    variant = next(line for line in master.text.splitlines() if line and not line.startswith("#"))
    media = client.get(variant)
    initial = re.search(r'#EXT-X-MAP:URI="([^"]+)"', media.text)[1]
    assert client.get(initial).content == b"init-bytes"
    segment = next(line for line in media.text.splitlines() if line and not line.startswith("#"))
    assert client.get(segment).content == b"segment-bytes"
    assert "fixture.invalid" not in media.text


def test_line_reordering_and_signed_url_refresh_keep_episode_identities(client):
    source = add_source(client)
    first = detail(client, source, "show-1")
    before = {(line["label"], entry["label"]): entry["identity"] for line in first["lines"] for entry in line["entries"]}
    assert len(before) == 4
    assert client.post("/api/p2/scenario", json={"revision": 2, "reverse_lines": True}).status_code == 200
    second = detail(client, source, "show-1")
    after = {(line["label"], entry["label"]): entry["identity"] for line in second["lines"] for entry in line["entries"]}
    assert before == after
    assert first["item"]["id"] == second["item"]["id"]
    assert len({identity["episode_id"] for identity in after.values()}) == 4
    created = client.post("/api/online/sessions", json=entry_for(second, "mp4")["identity"])
    assert created.status_code == 201, created.text
    assert client.get(created.json()["media_url"], headers={"Range": "bytes=0-9"}).status_code == 206


def test_unsupported_web_lines_are_not_playable_identities(client):
    source = add_source(client)
    value = detail(client, source, "unsupported-1")
    entries = [entry for line in value["lines"] for entry in line["entries"]]
    assert entries and all(not entry["supported"] and entry["identity"] is None for entry in entries)
    assert "fixture.invalid" not in str(value)


@pytest.mark.parametrize("mode", ["malformed", "xml", "status_error"])
def test_controlled_upstream_errors_are_sanitized_and_retryable(client, mode):
    source = add_source(client)
    assert client.post("/api/p2/scenario", json={"next_mode": mode}).status_code == 200
    response = client.get("/api/online/catalog/search", params={"source_id": source["id"], "q": "合成", "page": 1})
    assert response.status_code in {502, 503}, response.text
    assert "fixture.invalid" not in response.text and "upstream failure" not in response.text
    assert client.get("/api/online/catalog/search", params={"source_id": source["id"], "q": "none", "page": 1}).json()["items"] == []


def test_disable_source_revokes_owned_playback_and_no_arbitrary_url_api(client):
    source = add_source(client)
    identity = entry_for(detail(client, source), "mp4")["identity"]
    created = client.post("/api/online/sessions", json=identity).json()
    assert client.patch(f"/api/online/sources/{source['id']}", json={"enabled": False}).status_code == 200
    assert client.get(created["media_url"], headers={"Range": "bytes=0-9"}).status_code == 404
    rejected = client.post("/api/online/sessions", json={**identity, "url": "http://127.0.0.1/private.mp4"})
    assert rejected.status_code == 422 and "127.0.0.1" not in rejected.text
    assert client.get("/api/p2/metrics").json()["registry"]["sessions"] == 0


def test_fixture_restores_database_global_and_only_removes_its_state(prepared):
    original = store._base.DB_PATH
    with isolated_fixture(prepared) as app, TestClient(app) as client:
        assert Path(store._base.DB_PATH).is_relative_to(prepared)
        assert client.get("/api/p2/health").json()["synthetic"] is True
        with closing(store._base._conn()) as connection:
            assert connection.execute("SELECT COUNT(*) FROM online_sources").fetchone()[0] == 0
    assert store._base.DB_PATH == original
    assert not list(prepared.glob("state-*"))
    assert (prepared / "media" / "ep-1" / "movie.mp4").exists()
