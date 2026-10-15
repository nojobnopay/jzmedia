"""Device protection: real API, persistent sessions and revocable media grants."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import store, viewing
from app.main import app
from app.routers import viewing as viewing_router
from app.routers.stream import common


@pytest.fixture
def protected(tmp_path, monkeypatch):
    import app.store._base as base
    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "viewing.db"))
    store.init_db()
    client = TestClient(app)
    assert client.get("/api/viewing/session").json()["mode"] == "normal"
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    yield client
    client.close()


def test_password_is_hashed_and_not_general_settings(protected):
    with viewing._connection() as c:
        encoded = c.execute("SELECT password_hash FROM protection_settings").fetchone()[0]
    assert encoded.startswith("scrypt$") and "123456" not in encoded
    assert protected.get("/api/settings").status_code == 403
    assert protected.post("/api/viewing/unlock", json={"password": "123456"}).status_code == 200
    assert "scrypt$" not in protected.get("/api/settings").text


def test_children_persists_and_exit_requires_password(protected):
    assert protected.post("/api/viewing/mode", json={"mode": "children"}).json()["mode"] == "children"
    another = TestClient(app)
    another.cookies.update(protected.cookies)
    assert another.get("/api/viewing/session").json()["mode"] == "children"
    assert another.post("/api/viewing/mode", json={"mode": "normal"}).status_code == 403
    assert another.post("/api/viewing/unlock", json={"password": "123456"}).status_code == 403
    assert another.get("/api/fs/list").status_code == 403
    assert another.post("/api/viewing/mode", json={"mode": "normal", "password": "123456"}).json()["mode"] == "normal"
    assert TestClient(app).get("/api/viewing/session").json()["mode"] == "normal"


def test_management_does_not_change_browsing_mode(protected):
    result = protected.post("/api/viewing/unlock", json={"password": "123456"}).json()
    assert result["mode"] == "normal" and result["management_unlocked"]
    assert protected.post("/api/viewing/lock").json()["management_unlocked"] is False


def test_wrong_password_rate_limit_survives_new_cookies(protected):
    for _ in range(5):
        response = protected.post("/api/viewing/mode", json={"mode": "private", "password": "wrong"})
        assert response.status_code == 401
        assert response.json()["code"] == "protection_password_invalid"
    assert TestClient(app).post("/api/viewing/unlock", json={"password": "123456"}).status_code == 429


def test_idle_expiry_and_playback_lease(protected, monkeypatch):
    now = [10000.0]
    monkeypatch.setattr(viewing.time, "time", lambda: now[0])
    protected.post("/api/viewing/mode", json={"mode": "private", "password": "123456"})
    now[0] += 899
    assert protected.post("/api/viewing/activity", json={"playing": True}).json()["mode"] == "private"
    now[0] += 30
    assert protected.get("/api/viewing/session").json()["mode"] == "private"
    assert protected.post("/api/viewing/activity", json={"playing": False, "user_activity": True}).json()["mode"] == "private"
    now[0] += 901
    assert protected.get("/api/movies").json()["code"] == "viewing_locked"
    assert protected.get("/api/viewing/session").json()["mode"] == "normal"


def test_change_password_revokes_other_devices_but_keeps_children(protected):
    private = TestClient(app)
    child = TestClient(app)
    private.post("/api/viewing/mode", json={"mode": "private", "password": "123456"})
    child.post("/api/viewing/mode", json={"mode": "children"})
    assert protected.post("/api/viewing/password", json={"current_password": "123456", "new_password": "abcdef"}).status_code == 200
    assert private.get("/api/viewing/session").json()["mode"] == "normal"
    assert child.get("/api/viewing/session").json()["mode"] == "children"
    assert child.post("/api/viewing/mode", json={"mode": "normal", "password": "abcdef"}).status_code == 200


def test_tv_header_and_http_only_cookie(protected):
    cookie = protected.get("/api/viewing/session")
    assert "session_token" not in cookie.json()
    fresh = TestClient(app).get("/api/viewing/session")
    assert "httponly" in fresh.headers["set-cookie"].lower()
    client = TestClient(app)
    session = client.get("/api/viewing/session", headers={"X-Viewing-Client": "tv"}).json()
    secret = session["session_token"]
    assert "set-cookie" not in client.get("/api/viewing/session", headers={"X-Viewing-Client": "tv"}).headers
    headers = {"X-Viewing-Session": secret}
    assert client.post("/api/viewing/mode", headers=headers, json={"mode": "children"}).json()["mode"] == "children"
    assert client.get("/api/viewing/session", headers=headers).json()["mode"] == "children"
    with viewing._connection() as c:
        assert c.execute("SELECT 1 FROM viewing_sessions WHERE token_hash=?", (hashlib.sha256(secret.encode()).hexdigest(),)).fetchone()
        assert not c.execute("SELECT 1 FROM viewing_sessions WHERE token_hash=?", (secret,)).fetchone()


def test_old_write_token_still_guards_configuration(protected):
    store.set_setting("jzmedia_token", "existing-write-token")
    assert protected.post("/api/viewing/unlock", json={"password": "123456"}).status_code == 200
    assert protected.post("/api/viewing/password", json={"current_password": "123456", "new_password": "abcdef"}).status_code == 401
    assert protected.patch("/api/viewing/policy", json={"kind": "movie", "ids": [1], "is_private": True}).status_code == 401


def test_unknown_private_ids_and_resources_hidden(protected, tmp_path, monkeypatch):
    movie_id = store.upsert_movie_by_path("private.mkv")
    store.update_movie_meta(movie_id, title="Secret", tmdb_id=89001)
    protected.post("/api/viewing/unlock", json={"password": "123456"})
    assert protected.patch("/api/viewing/policy", json={"kind": "movie", "ids": [movie_id], "is_private": True}).status_code == 200
    protected.post("/api/viewing/lock")
    for path in (f"/api/movies/{movie_id}", "/posters/movies/89001.jpg", "/posters/89001.jpg",
                 f"/api/stream/{movie_id}/media", f"/api/stream/progress?version_id={movie_id}"):
        assert protected.get(path).status_code == 404, path
    assert protected.get("/api/fs/blob?path=private.mkv").status_code == 403
    assert protected.get("/api/stream/previews/jobs/latest").status_code == 403
    assert "path" not in protected.get("/api/health").json()


def test_hls_lease_revoked_on_lock(protected, monkeypatch):
    movie_id = store.upsert_movie_by_path("private.mkv")
    protected.post("/api/viewing/unlock", json={"password": "123456"})
    protected.patch("/api/viewing/policy", json={"kind": "movie", "ids": [movie_id], "is_private": True})
    protected.post("/api/viewing/mode", json={"mode": "private", "password": "123456"})
    session, _, _ = viewing.resolve(protected.cookies[viewing.COOKIE])
    monkeypatch.setitem(common._sessions, "test-protected", {"vid": movie_id, "kind": "movie",
        "viewing_owner": session["token_hash"], "viewing_generation": session["generation"]})
    protected.post("/api/viewing/lock")
    assert protected.get("/api/stream/sessions/test-protected/master.m3u8").status_code == 404


@pytest.mark.parametrize("transport", ["cookie", "tv_header"])
@pytest.mark.parametrize("previously_issued", [False, True])
def test_unknown_device_session_never_falls_back_to_normal(protected, transport, previously_issued):
    secret = "unknown-device-secret"
    if previously_issued:
        issued, secret, _ = viewing.resolve(create=True)
        with viewing._connection() as connection:
            connection.execute("DELETE FROM viewing_sessions WHERE token_hash=?", (issued["token_hash"],))
    with viewing._connection() as connection:
        initial_count = connection.execute("SELECT COUNT(*) FROM viewing_sessions").fetchone()[0]
    client = TestClient(app)
    try:
        if transport == "cookie":
            client.cookies.set(viewing.COOKIE, secret)
        else:
            client.headers[viewing.HEADER] = secret
        for method, path, kwargs in (
            ("get", "/api/movies", {}),
            ("get", "/api/viewing/session", {}),
            ("post", "/api/viewing/mode", {"json": {"mode": "children"}}),
            ("post", "/api/viewing/activity", {"json": {"playing": True}}),
        ):
            response = getattr(client, method)(path, **kwargs)
            assert response.status_code == 401, (path, response.text)
            assert response.json()["code"] == "viewing_locked"
            assert "set-cookie" not in response.headers
    finally:
        client.close()
    with viewing._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM viewing_sessions").fetchone()[0] == initial_count
    # The accepted device limitation still permits a genuinely fresh browser.
    fresh = TestClient(app)
    try:
        assert fresh.get("/api/viewing/session").json()["mode"] == "normal"
    finally:
        fresh.close()


def test_playback_renewal_does_not_extend_management_and_pause_starts_idle_window(protected, monkeypatch):
    now = [10000.0]
    monkeypatch.setattr(viewing.time, "time", lambda: now[0])
    started = protected.post("/api/viewing/mode", json={"mode": "private", "password": "123456"})
    assert started.status_code == 200
    secret = protected.cookies[viewing.COOKIE]
    initial, _, _ = viewing.resolve(secret)
    for instant in (10300.0, 10600.0, 10899.0, 10901.0, 11200.0):
        now[0] = instant
        response = protected.post("/api/viewing/activity", json={"playing": True})
        assert response.status_code == 200 and response.json()["mode"] == "private"
        session, _, _ = viewing.resolve(secret)
        assert session["management_until"] == initial["management_until"]
    assert not response.json()["management_unlocked"]
    assert protected.get("/api/settings").status_code == 403
    assert protected.get("/api/movies").status_code == 200
    now[0] += 10
    paused = protected.post("/api/viewing/activity", json={"playing": False, "user_activity": True})
    assert paused.json()["expires_at"] == now[0] + viewing.IDLE_SECONDS
    assert not paused.json()["management_unlocked"]
    session, _, _ = viewing.resolve(secret)
    assert session["playback_until"] == 0
    now[0] += viewing.IDLE_SECONDS - 1
    assert protected.get("/api/movies").status_code == 200
    now[0] += 1
    expired = protected.get("/api/movies")
    assert expired.status_code == 401 and expired.json()["code"] == "viewing_locked"
    assert protected.get("/api/viewing/session").json()["mode"] == "normal"


@pytest.mark.parametrize("path,body,initial_mode", [
    ("/api/viewing/mode", {"mode": "private", "password": "123456"}, "normal"),
    ("/api/viewing/mode", {"mode": "normal", "password": "123456"}, "children"),
    ("/api/viewing/unlock", {"password": "123456"}, "normal"),
    ("/api/viewing/password", {"current_password": "123456", "new_password": "losing-password"}, "normal"),
])
def test_password_changed_after_verification_rejects_stale_authorization(protected, monkeypatch, path, body, initial_mode):
    if initial_mode == "children":
        assert protected.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    original_verify = viewing.verify_password

    def verify_then_change(password, subject):
        revision = original_verify(password, subject)
        viewing.set_password("winning-password")
        return revision

    with monkeypatch.context() as patch:
        patch.setattr(viewing, "verify_password", verify_then_change)
        response = protected.post(path, json=body)
    assert response.status_code == 401
    assert response.json()["code"] == "protection_password_invalid"
    result = protected.get("/api/viewing/session").json()
    assert result["mode"] == initial_mode and not result["management_unlocked"]
    original_verify("winning-password", "verification-after-race")
    for rejected in ("123456", "losing-password"):
        with pytest.raises(HTTPException) as error:
            original_verify(rejected, "verification-after-race")
        assert error.value.status_code == 401


@pytest.mark.parametrize("path,body", [
    ("/api/viewing/mode", {"mode": "private", "password": "123456"}),
    ("/api/viewing/unlock", {"password": "123456"}),
])
def test_session_generation_change_after_verification_rejects_authorization(protected, monkeypatch, path, body):
    original_verify = viewing.verify_password
    key = viewing.token_hash(protected.cookies[viewing.COOKIE])

    def verify_then_enter_children(password, subject):
        revision = original_verify(password, subject)
        with viewing._connection() as connection:
            connection.execute("UPDATE viewing_sessions SET mode='children',management_until=0,"
                               "generation=generation+1 WHERE token_hash=?", (key,))
        return revision

    with monkeypatch.context() as patch:
        patch.setattr(viewing, "verify_password", verify_then_enter_children)
        response = protected.post(path, json=body)
    assert response.status_code == 401
    assert response.json()["code"] == "protection_password_invalid"
    result = protected.get("/api/viewing/session").json()
    assert result["mode"] == "children" and not result["management_unlocked"]


@pytest.mark.parametrize("playing,user_activity", [(True, False), (False, True), (True, True)])
def test_in_flight_activity_cannot_restore_password_revoked_grants(protected, monkeypatch, playing, user_activity):
    now = [10000.0]
    monkeypatch.setattr(viewing.time, "time", lambda: now[0])
    assert protected.post("/api/viewing/mode", json={"mode": "private", "password": "123456"}).status_code == 200
    original_session = viewing_router._session
    captured = []

    def session_then_revoke(request, response):
        result = original_session(request, response)
        captured.append(result[0])
        viewing.set_password("changed-password")
        return result

    now[0] += 100
    with monkeypatch.context() as patch:
        patch.setattr(viewing_router, "_session", session_then_revoke)
        response = protected.post("/api/viewing/activity", json={"playing": playing, "user_activity": user_activity})
    assert response.status_code == 200
    assert response.json()["mode"] == "normal" and not response.json()["management_unlocked"]
    session = viewing.session_by_hash(captured[0]["token_hash"])
    assert session["generation"] > captured[0]["generation"]
    assert session["management_until"] == session["playback_until"] == 0
    assert session["last_activity"] == captured[0]["last_activity"]
    assert protected.get("/api/settings").status_code == 403


def test_concurrent_failed_attempts_are_counted_without_lost_updates(protected):
    count = 8
    barrier = threading.Barrier(count)

    def attempt(_):
        barrier.wait(timeout=3)
        try:
            viewing.verify_password("incorrect", "simultaneous-subject")
        except HTTPException as error:
            return error.status_code
        pytest.fail("incorrect password was accepted")

    with ThreadPoolExecutor(max_workers=count) as executor:
        results = list(executor.map(attempt, range(count)))
    assert results.count(401) == 5 and results.count(429) == 3
    with viewing._connection() as connection:
        failures = connection.execute("SELECT failures FROM protection_attempts WHERE subject=?",
                                      (hashlib.sha256(b"simultaneous-subject").hexdigest(),)).fetchone()[0]
    assert failures == 5


def test_concurrent_initial_password_setup_preserves_the_first_winner(protected, monkeypatch):
    with viewing._connection() as connection:
        connection.execute("UPDATE protection_settings SET password_hash='' WHERE id=1")
        revision = connection.execute("SELECT revision FROM protection_settings WHERE id=1").fetchone()[0]
    original_set = viewing.set_password
    observed = []

    def set_with_competing_setup(password, *, expected_revision=None):
        observed.append(expected_revision)
        original_set("first-password", expected_revision=revision)
        return original_set(password, expected_revision=expected_revision)

    with monkeypatch.context() as patch:
        patch.setattr(viewing, "set_password", set_with_competing_setup)
        response = protected.post("/api/viewing/password", json={"new_password": "second-password"})
    assert observed == [revision]
    assert response.status_code == 401
    assert response.json()["code"] == "protection_password_invalid"
    viewing.verify_password("first-password", "setup-check")
    with pytest.raises(HTTPException) as error:
        viewing.verify_password("second-password", "setup-check")
    assert error.value.status_code == 401
