"""Large uploads check current access without transferring a multipart body."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app import store, viewing
from app.main import app

CHECK = "/api/uploads/check-access"
PASSWORD = "upload-test-password"


@pytest.fixture
def device(tmp_path, monkeypatch):
    import app.store._base as base

    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "upload-access.db"))
    store.init_db()
    client = TestClient(app)
    client.get("/api/viewing/session")
    yield client
    client.close()


def protect(client):
    response = client.post("/api/viewing/password", json={"new_password": PASSWORD})
    assert response.status_code == 200, response.text


def unlock(client):
    response = client.post("/api/viewing/unlock", json={"password": PASSWORD})
    assert response.status_code == 200, response.text


def test_check_requires_no_file_when_protection_disabled(device):
    response = device.post(CHECK)
    assert response.status_code == 200
    assert response.json() == {"allowed": True}


def test_check_uses_current_management_permission_without_changing_mode(device):
    protect(device)
    denied = device.post(CHECK)
    assert denied.status_code == 403
    assert denied.json()["code"] == "management_required"

    unlock(device)
    response = device.post(CHECK)
    assert response.status_code == 200
    session = device.get("/api/viewing/session").json()
    assert session["mode"] == "normal" and session["management_unlocked"]

    device.post("/api/viewing/lock")
    assert device.post(CHECK).status_code == 403


def test_check_rejects_children_mode(device):
    protect(device)
    unlock(device)
    changed = device.post("/api/viewing/mode", json={"mode": "children"})
    assert changed.status_code == 200
    response = device.post(CHECK)
    assert response.status_code == 403
    assert response.json()["code"] == "management_required"


def test_check_rejects_expired_management_and_does_not_renew_it(device, monkeypatch):
    protect(device)
    clock = [10000.0]
    monkeypatch.setattr(viewing.time, "time", lambda: clock[0])
    unlock(device)
    session, _, _ = viewing.resolve(device.cookies[viewing.COOKIE])
    original_until = session["management_until"]

    clock[0] += viewing.IDLE_SECONDS - 1
    assert device.post(CHECK).status_code == 200
    assert viewing.session_by_hash(session["token_hash"])["management_until"] == original_until

    clock[0] += 1
    denied = device.post(CHECK)
    assert denied.status_code == 403
    assert denied.json()["code"] == "management_required"


@pytest.mark.parametrize("protection_enabled", [False, True])
def test_check_keeps_existing_write_token_gate(device, protection_enabled):
    if protection_enabled:
        protect(device)
        unlock(device)
    store.set_setting("jzmedia_token", "upload-write-token")
    assert device.post(CHECK).status_code == 401
    assert device.post(CHECK, headers={"X-Api-Token": "wrong-token"}).status_code == 401
    response = device.post(CHECK, headers={"X-Api-Token": "upload-write-token"})
    assert response.status_code == 200
    if protection_enabled:
        device.post("/api/viewing/lock")
        denied = device.post(CHECK, headers={"X-Api-Token": "upload-write-token"})
        assert denied.status_code == 403
        assert denied.json()["code"] == "management_required"


def test_check_rejects_unknown_viewing_session(device):
    protect(device)
    device.cookies.set(viewing.COOKIE, "expired-device-secret")
    response = device.post(CHECK)
    assert response.status_code == 401
    assert response.json()["code"] == "viewing_locked"


@pytest.mark.parametrize("path", [CHECK, "/api/uploads", "/api/movies/1/upload"])
def test_locked_upload_is_rejected_before_reading_multipart(device, path):
    protect(device)
    body_reads = []
    messages = []

    async def receive():
        body_reads.append(True)
        return {"type": "http.request", "body": b"must not be read", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
        "headers": [(b"host", b"testserver"),
                    (b"content-type", b"multipart/form-data; boundary=unread")],
        "client": ("testclient", 50000), "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    assert next(message["status"] for message in messages
                if message["type"] == "http.response.start") == 403
    assert not body_reads
