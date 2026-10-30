"""TV header credentials use the real online API and isolated bounded origin."""

from contextlib import closing
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.store import _base
from scripts.online_fixture.catalog import FIXTURE_TOKEN, isolated_fixture
from test_online_catalog_fixture import add_source, detail, entry_for
from test_online_state_fixture import claim, packet, prepared, scoped, state


__all__ = ["prepared"]


@pytest.fixture
def service(prepared):
    with isolated_fixture(prepared) as app, TestClient(app) as administrator:
        administrator.headers["X-Api-Token"] = FIXTURE_TOKEN
        assert administrator.get("/api/viewing/session").status_code == 200
        yield SimpleNamespace(app=app, administrator=administrator, source=add_source(administrator))


def television(app):
    client = TestClient(app)
    client.headers.update({"X-Api-Token": FIXTURE_TOKEN, "X-Viewing-Client": "tv"})
    response = client.get("/api/viewing/session")
    assert response.status_code == 200, response.text
    assert "set-cookie" not in response.headers and not client.cookies
    token = response.json()["session_token"]
    assert token
    client.headers["X-Viewing-Session"] = token
    return client


def favorites(client):
    response = client.get("/api/online/state/library", params={"section": "favorites"})
    assert response.status_code == 200, response.text
    return response.json()


def test_cookie_less_tv_header_catalogue_favorite_session_range_and_final_progress(service):
    with closing(television(service.app)) as tv:
        result = tv.get("/api/online/catalog/search", params={"source_id": service.source["id"], "q": "合成", "page": 1})
        assert result.status_code == 200 and result.json()["items"]
        identity = entry_for(detail(tv, service.source), "mp4")["identity"]
        selected = state(tv, identity)
        favorite = scoped(tv, "PUT", f"/api/online/state/items/{identity['item_id']}/favorite",
                          selected["scope_token"], {"favorite": True})
        assert favorite.status_code == 200 and favorite.json()["favorite"]
        session, writer = claim(tv, identity)
        response = tv.get(session["media_url"], headers={"Range": "bytes=0-31"})
        assert response.status_code == 206
        assert response.headers["content-range"] == "bytes 0-31/1000"
        assert response.content == (b"0123456789" * 100)[:32]
        saved = scoped(tv, "POST", "/api/online/state/progress", writer["scope_token"], packet(writer))
        assert saved.status_code == 200, saved.text
        assert tv.delete(f"/api/online/sessions/{session['session_id']}").status_code == 204
        final = scoped(tv, "POST", "/api/online/state/progress", writer["scope_token"], packet(writer, position=13.0, seq=2))
        assert final.status_code == 200, final.text
        assert state(tv, identity)["selected_progress"]["position"] == 13.0
        assert favorites(tv)["items"][0]["item"]["id"] == identity["item_id"]
        assert not tv.cookies
        with closing(TestClient(service.app)) as reconnected:
            reconnected.headers.update(dict(tv.headers))
            restored = state(reconnected, identity)
            assert restored["favorite"] and restored["selected_progress"]["identity"] == identity
            assert restored["selected_progress"]["position"] == 13.0
        metrics = service.administrator.get("/api/fixture/metrics").json()
        assert metrics["registry"]["sessions"] == 0
        assert not any(row["app_credentials"] for row in metrics["origin_calls"])
        with closing(_base._conn()) as connection:
            for table in ("movies", "tv_episodes", "playback_progress"):
                assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_second_tv_and_browser_cannot_read_write_claim_or_stream_the_first_tv_state(service):
    with closing(television(service.app)) as first, closing(television(service.app)) as second, closing(TestClient(service.app)) as browser:
        browser.headers["X-Api-Token"] = FIXTURE_TOKEN
        response = browser.get("/api/viewing/session")
        assert response.status_code == 200 and "session_token" not in response.json()
        assert browser.cookies
        assert first.headers["X-Viewing-Session"] != second.headers["X-Viewing-Session"]
        identity = entry_for(detail(first, service.source), "mp4")["identity"]
        selected = state(first, identity)
        assert scoped(first, "PUT", f"/api/online/state/items/{identity['item_id']}/favorite",
                      selected["scope_token"], {"favorite": True}).status_code == 200
        session, writer = claim(first, identity)
        assert scoped(first, "POST", "/api/online/state/progress", writer["scope_token"], packet(writer)).status_code == 200
        for stranger in (second, browser):
            own = state(stranger, identity)
            assert not own["favorite"] and own["selected_progress"] is None and own["last_progress"] is None
            assert favorites(stranger)["total"] == 0
            denied = scoped(stranger, "POST", "/api/online/state/progress", own["scope_token"], packet(writer, position=1.0, seq=2))
            assert denied.status_code == 409 and denied.json()["code"] == "online_progress_writer_conflict"
            denied_claim = scoped(stranger, "POST", "/api/online/state/progress/claim", own["scope_token"],
                                  {"session_id": session["session_id"], "expected_revision": 0})
            assert denied_claim.status_code in (403, 404)
            assert stranger.get(session["media_url"], headers={"Range": "bytes=0-9"}).status_code in (403, 404)
            assert stranger.delete(f"/api/online/sessions/{session['session_id']}").status_code in (403, 404)
        assert first.get(session["media_url"], headers={"Range": "bytes=0-9"}).content == b"0123456789"
        assert state(first, identity)["selected_progress"]["position"] == 12.0
        assert first.delete(f"/api/online/sessions/{session['session_id']}").status_code == 204


def test_tv_normal_private_children_modes_reject_stale_scope_and_hide_private_state(service):
    password = "isolated-tv-protection"
    configured = service.administrator.post("/api/viewing/password", json={"new_password": password})
    assert configured.status_code == 200, configured.text
    with closing(television(service.app)) as tv:
        normal_identity = entry_for(detail(tv, service.source), "mp4")["identity"]
        normal = state(tv, normal_identity)
        assert scoped(tv, "PUT", f"/api/online/state/items/{normal_identity['item_id']}/favorite",
                      normal["scope_token"], {"favorite": True}).status_code == 200
        normal_session, normal_writer = claim(tv, normal_identity)
        assert scoped(tv, "POST", "/api/online/state/progress", normal_writer["scope_token"], packet(normal_writer)).status_code == 200
        assert tv.delete(f"/api/online/sessions/{normal_session['session_id']}").status_code == 204
        private_mode = tv.post("/api/viewing/mode", json={"mode": "private", "password": password})
        assert private_mode.status_code == 200 and private_mode.json()["mode"] == "private"
        assert favorites(tv)["total"] == 0 and state(tv, normal_identity)["selected_progress"] is None
        stale = scoped(tv, "PUT", f"/api/online/state/items/{normal_identity['item_id']}/favorite",
                       normal["scope_token"], {"favorite": False})
        assert stale.status_code == 409 and stale.json()["code"] == "online_state_scope_changed"
        private_identity = entry_for(detail(tv, service.source, "show-1"), "mp4")["identity"]
        private = state(tv, private_identity)
        assert private["scope_token"] != normal["scope_token"]
        assert scoped(tv, "PUT", f"/api/online/state/items/{private_identity['item_id']}/favorite",
                      private["scope_token"], {"favorite": True}).status_code == 200
        private_session, private_writer = claim(tv, private_identity)
        assert scoped(tv, "POST", "/api/online/state/progress", private_writer["scope_token"], packet(private_writer, position=7.0)).status_code == 200
        assert tv.post("/api/viewing/mode", json={"mode": "normal"}).json()["mode"] == "normal"
        restored = state(tv, normal_identity)
        assert restored["favorite"] and restored["selected_progress"]["position"] == 12.0
        assert not state(tv, private_identity)["favorite"] and state(tv, private_identity)["selected_progress"] is None
        assert scoped(tv, "POST", "/api/online/state/progress", private_writer["scope_token"],
                      packet(private_writer, position=1.0, seq=2)).status_code == 409
        assert tv.get(private_session["media_url"], headers={"Range": "bytes=0-9"}).status_code in (401, 403, 404, 410)
        child = tv.post("/api/viewing/mode", json={"mode": "children"})
        assert child.status_code == 200 and child.json()["mode"] == "children"
        assert tv.get("/api/online/catalog/sources").status_code == 403
        assert tv.get("/api/online/state/library").status_code == 403
        assert tv.post("/api/online/sessions", json=normal_identity).status_code == 403
        assert tv.post("/api/viewing/mode", json={"mode": "normal", "password": password}).status_code == 200
        assert favorites(tv)["items"][0]["item"]["id"] == normal_identity["item_id"]
        assert not tv.cookies


def test_tv_history_clear_prevents_old_writer_resurrection_until_an_explicit_new_claim(service):
    with closing(television(service.app)) as tv:
        identity = entry_for(detail(tv, service.source), "mp4")["identity"]
        old_session, old = claim(tv, identity)
        assert scoped(tv, "POST", "/api/online/state/progress", old["scope_token"], packet(old)).status_code == 200
        cleared = scoped(tv, "DELETE", f"/api/online/state/items/{identity['item_id']}/history", old["scope_token"])
        assert cleared.status_code == 200 and cleared.json()["deleted"] == 1
        current_scope = cleared.json()["scope_token"]
        assert current_scope != old["scope_token"]
        stale = scoped(tv, "POST", "/api/online/state/progress", old["scope_token"], packet(old, seq=2))
        assert stale.status_code == 409 and stale.json()["code"] == "online_state_scope_changed"
        copied_scope = scoped(tv, "POST", "/api/online/state/progress", current_scope, packet(old, seq=3))
        assert copied_scope.status_code == 409 and copied_scope.json()["code"] == "online_progress_writer_conflict"
        old_claim = scoped(tv, "POST", "/api/online/state/progress/claim", old["scope_token"],
                           {"session_id": old_session["session_id"], "expected_revision": 0})
        assert old_claim.status_code == 409 and state(tv, identity)["selected_progress"] is None
        assert tv.delete(f"/api/online/sessions/{old_session['session_id']}").status_code == 204
        session, writer = claim(tv, identity)
        assert writer["writer_id"] != old["writer_id"] and writer["scope_token"] == current_scope
        assert scoped(tv, "POST", "/api/online/state/progress", writer["scope_token"], packet(writer, position=3.0)).status_code == 200
        assert state(tv, identity)["selected_progress"]["position"] == 3.0
        assert tv.delete(f"/api/online/sessions/{session['session_id']}").status_code == 204


def test_tv_preview_composes_native_handshake_empty_home_and_pre_enabled_online_source(prepared):
    from scripts.online_fixture.tv import MARKER, MARKER_TEXT, ROOT_PREFIX
    from scripts.online_fixture.tv import isolated_fixture as tv_fixture

    root = prepared.with_name(ROOT_PREFIX + "test")
    prepared.rename(root)
    (root / MARKER).write_text(MARKER_TEXT)
    original_db = _base.DB_PATH
    with tv_fixture(root) as app, TestClient(app) as client:
        handshake = client.get("/api/stream/client-info")
        assert handshake.status_code == 200 and handshake.json()["protocol_version"] == 1
        assert {"online_vod", "viewing_modes"}.issubset(handshake.json()["features"])
        assert client.post("/api/stream/client-check").status_code in (401, 403)
        client.headers["X-Api-Token"] = FIXTURE_TOKEN
        assert client.post("/api/stream/client-check").status_code == 200
        stream_paths = {path for route in app.routes if (path := getattr(route, "path", "")).startswith("/api/stream/")}
        assert stream_paths == {"/api/stream/client-info", "/api/stream/client-check"}
        for path in ("/api/movies/recent-played", "/api/tv/recent-played", "/api/movies", "/api/tv/shows"):
            local = client.get(path)
            assert local.status_code == 200 and local.json()["items"] == [] and local.json()["synthetic"]
        with closing(television(app)) as tv:
            source_response = tv.get("/api/online/catalog/sources")
            assert source_response.status_code == 200 and source_response.json()["enabled"]
            source = source_response.json()["items"][0]
            assert source["id"] == app.state.online_source["id"] and source["provider"] == "maccms"
            identity = entry_for(detail(tv, source), "mp4")["identity"]
            session, writer = claim(tv, identity)
            assert tv.get(session["media_url"], headers={"Range": "bytes=0-9"}).content == b"0123456789"
            assert scoped(tv, "POST", "/api/online/state/progress", writer["scope_token"], packet(writer)).status_code == 200
            assert tv.delete(f"/api/online/sessions/{session['session_id']}").status_code == 204
        metrics = client.get("/api/fixture/metrics").json()
        assert metrics["catalog_rows"]["online_sources"] == 1
        assert not any(metrics["local_rows"].values())
    assert _base.DB_PATH == original_db and not list(root.glob("state-*"))


def test_tv_preview_refuses_catalog_unmarked_outside_and_symlink_roots(prepared, tmp_path):
    from pathlib import Path

    from scripts.online_fixture.tv import ROOT_PREFIX, fixture_root

    for path in (Path("/data"), prepared, tmp_path / "arbitrary"):
        with pytest.raises(ValueError):
            fixture_root(path)
    root = tmp_path / (ROOT_PREFIX + "unmarked")
    root.mkdir()
    with pytest.raises(ValueError):
        fixture_root(root, require_media=True)
    link = tmp_path / (ROOT_PREFIX + "symlink")
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        fixture_root(link)
