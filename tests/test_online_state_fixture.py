"""The shared catalog fixture composes real state, viewing and media routers without NAS data."""

from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.store import _base
from scripts.online_fixture.catalog_origin import MARKER, MARKER_TEXT, ROOT_PREFIX, fixture_root
from scripts.online_fixture.catalog import FIXTURE_TOKEN, isolated_fixture
from test_online_catalog_fixture import add_source, detail, entry_for


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / (ROOT_PREFIX + "state-test")
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


def state(client, identity):
    params = {key: identity[key] for key in ("line_id", "episode_id") if identity.get(key)}
    response = client.get(f"/api/online/state/items/{identity['item_id']}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def scoped(client, method, path, scope, body=None):
    options = {"headers": {"X-Online-State-Scope": scope}}
    if body is not None:
        options["json"] = body
    return client.request(method, path, **options)


def claim(client, identity, revision=0):
    current = state(client, identity)
    created = client.post("/api/online/sessions", json=identity)
    assert created.status_code == 201, created.text
    session = created.json()
    result = scoped(client, "POST", "/api/online/state/progress/claim", current["scope_token"],
                    {"session_id": session["session_id"], "expected_revision": revision})
    assert result.status_code in (200, 201), result.text
    return session, result.json()


def packet(writer, *, position=12.0, seq=1):
    return {"writer_id": writer["writer_id"], "writer_revision": writer["writer_revision"], "seq": seq,
            "position": position, "duration": 24.0, "paused": True, "completed": False}


def test_catalog_state_retains_root_guard_and_restores_all_globals(prepared, tmp_path):
    original = _base.DB_PATH
    with pytest.raises(ValueError):
        fixture_root(Path("/data"))
    foreign_player = tmp_path / "jzmedia-online-player-other"
    foreign_player.mkdir()
    (foreign_player / MARKER).write_text(MARKER_TEXT)
    with pytest.raises(ValueError):
        with isolated_fixture(foreign_player):
            pytest.fail("a player root must never serve through the catalog entrypoint")
    with isolated_fixture(prepared) as app, TestClient(app) as browser:
        assert Path(_base.DB_PATH).is_relative_to(prepared)
        browser.get("/api/viewing/session")
        empty = browser.get("/api/online/state/library", params={"section": "favorites"})
        assert empty.status_code == 200 and empty.json()["items"] == []
    assert _base.DB_PATH == original
    assert not list(prepared.glob("state-*"))
    assert (prepared / "media" / "ep-1" / "movie.mp4").exists()


def test_actual_formal_session_and_state_survive_cookie_reconnect_without_local_writes(client):
    source = add_source(client)
    identity = entry_for(detail(client, source), "mp4")["identity"]
    current = state(client, identity)
    favorite = scoped(client, "PUT", f"/api/online/state/items/{identity['item_id']}/favorite",
                      current["scope_token"], {"favorite": True})
    assert favorite.status_code == 200 and favorite.json()["favorite"] is True
    session, writer = claim(client, identity)
    response = scoped(client, "POST", "/api/online/state/progress", writer["scope_token"], packet(writer))
    assert response.status_code == 200, response.text
    assert client.get(session["media_url"], headers={"Range": "bytes=0-9"}).content == b"0123456789"
    assert client.delete(f"/api/online/sessions/{session['session_id']}").status_code == 204
    reconnected = TestClient(client.app)
    try:
        reconnected.cookies.update(dict(client.cookies))
        restored = state(reconnected, identity)
        assert restored["favorite"] is True
        assert restored["selected_progress"]["position"] == 12.0
        assert restored["selected_progress"]["identity"] == identity
    finally:
        reconnected.close()
    with closing(_base._conn()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM online_favorites").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM online_progress").fetchone()[0] == 1
        for table in ("movies", "tv_episodes", "playback_progress"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    metrics = client.get("/api/fixture/metrics").json()
    assert metrics["registry"]["sessions"] == 0
    assert metrics["online_state_rows"] == {"online_favorites": 1, "online_progress": 1}
    assert not any(row["app_credentials"] for row in metrics["origin_calls"])


def test_real_device_owner_cannot_read_or_write_another_cookie_progress(client):
    source = add_source(client)
    identity = entry_for(detail(client, source), "mp4")["identity"]
    _session, writer = claim(client, identity)
    assert scoped(client, "POST", "/api/online/state/progress", writer["scope_token"], packet(writer)).status_code == 200
    stranger = TestClient(client.app)
    try:
        stranger.headers["X-Api-Token"] = FIXTURE_TOKEN
        stranger.get("/api/viewing/session")
        own = state(stranger, identity)
        assert own["selected_progress"] is None and own["favorite"] is False
        denied = scoped(stranger, "POST", "/api/online/state/progress", own["scope_token"], packet(writer, position=1.0, seq=2))
        assert denied.status_code in (403, 404, 409)
    finally:
        stranger.close()
    assert state(client, identity)["selected_progress"]["position"] == 12.0


def test_new_real_session_claim_rejects_stale_writer_without_replacing_saved_position(client):
    source = add_source(client)
    identity = entry_for(detail(client, source), "mp4")["identity"]
    first_session, first = claim(client, identity)
    assert scoped(client, "POST", "/api/online/state/progress", first["scope_token"], packet(first)).status_code == 200
    client.delete(f"/api/online/sessions/{first_session['session_id']}")
    _second_session, second = claim(client, identity, first["writer_revision"])
    assert second["writer_revision"] > first["writer_revision"]
    assert scoped(client, "POST", "/api/online/state/progress", second["scope_token"], packet(second, position=7.0)).status_code == 200
    rejected = scoped(client, "POST", "/api/online/state/progress", first["scope_token"], packet(first, position=1.0, seq=999))
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "online_progress_writer_conflict"
    assert state(client, identity)["selected_progress"]["position"] == 7.0


def test_disabled_source_retains_its_state_but_allows_explicit_removal(client):
    source = add_source(client)
    identity = entry_for(detail(client, source), "mp4")["identity"]
    current = state(client, identity)
    assert scoped(client, "PUT", f"/api/online/state/items/{identity['item_id']}/favorite",
                  current["scope_token"], {"favorite": True}).status_code == 200
    _session, writer = claim(client, identity)
    assert scoped(client, "POST", "/api/online/state/progress", writer["scope_token"], packet(writer)).status_code == 200
    assert client.patch(f"/api/online/sources/{source['id']}", json={"enabled": False}).status_code == 200
    kept = state(client, identity)
    assert kept["favorite"] and kept["selected_progress"] and not kept["available"]
    assert scoped(client, "PUT", f"/api/online/state/items/{identity['item_id']}/favorite",
                  kept["scope_token"], {"favorite": False}).status_code == 200
    removed = scoped(client, "DELETE", f"/api/online/state/items/{identity['item_id']}/history", kept["scope_token"])
    assert removed.status_code == 200, removed.text
    assert state(client, identity)["selected_progress"] is None
    assert not state(client, identity)["favorite"]
