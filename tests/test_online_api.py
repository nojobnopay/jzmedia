"""The initial online APIs manage configuration without accessing any provider."""

import pytest
import httpx
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app


SOURCE = {"name": "隔离免费来源", "provider": "maccms",
          "api_base_url": "https://source.example.invalid/api.php/provide/vod/"}
TOKEN = "online-admin-test-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "online-api.db"))
    library_paths.invalidate_cache()
    store.init_db()

    def no_network(*_args, **_kwargs):
        pytest.fail("online configuration must not open an external connection")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    candidate = TestClient(app, raise_server_exceptions=True)
    try:
        # Deliberately do not enter lifespan: configuration needs no background
        # scanner, preview worker, backend detection or provider.
        yield candidate
    finally:
        candidate.close()
        library_paths.invalidate_cache()


def test_online_is_disabled_and_has_no_sources_by_default(client):
    response = client.get("/api/online/settings")
    assert response.status_code == 200
    assert response.json() == {"enabled": False}
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/online/sources").json() == {"items": []}


def test_openapi_exposes_typed_source_configuration(client):
    schema = client.get("/openapi.json").json()
    body = schema["paths"]["/api/online/sources"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"].endswith("/OnlineSourceCreate")
    assert "api_base_url" in schema["components"]["schemas"]["OnlineSourceCreate"]["properties"]
    playback = schema["paths"]["/api/online/sessions"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert playback["$ref"].endswith("/OnlinePlaybackIdentity")
    assert "url" not in schema["components"]["schemas"]["OnlinePlaybackIdentity"]["properties"]


def test_configuration_works_while_disabled_and_keeps_source_identity(client):
    response = client.post("/api/online/sources", json=SOURCE)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["enabled"] is False
    sid = created["id"]
    assert client.patch("/api/online/settings", json={"enabled": True}).json() == {"enabled": True}
    assert client.patch(f"/api/online/sources/{sid}", json={"enabled": True}).json()["id"] == sid
    assert client.patch("/api/online/settings", json={"enabled": False}).json() == {"enabled": False}
    response = client.patch(f"/api/online/sources/{sid}", json={"name": "新名称", "enabled": False})
    assert response.json()["id"] == sid
    assert client.get(f"/api/online/sources/{sid}").json()["enabled"] is False
    assert client.get("/api/online/sources").json()["items"][0]["id"] == sid
    with store._base._conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM movies").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tv_episodes").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM playback_progress").fetchone()[0] == 0


@pytest.mark.parametrize("method,path,payload", [
    ("get", "/api/online/settings", None),
    ("get", "/api/online/sources", None),
    ("post", "/api/online/sources", SOURCE),
    ("patch", "/api/online/settings", {"enabled": True}),
])
def test_configured_token_guards_reads_and_writes(client, method, path, payload):
    store.set_setting("jzmedia_token", TOKEN)
    kwargs = {"json": payload} if payload is not None else {}
    for headers in ({}, {"X-Api-Token": "wrong"}):
        response = client.request(method, path, headers=headers, **kwargs)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
    for headers in ({"X-Api-Token": TOKEN}, {"Authorization": f"Bearer {TOKEN}"}):
        response = client.request(method, path, headers=headers, **kwargs)
        assert response.status_code in (200, 201), response.text
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/settings").status_code == 200


def test_source_detail_also_requires_token(client):
    sid = client.post("/api/online/sources", json=SOURCE).json()["id"]
    store.set_setting("jzmedia_token", TOKEN)
    assert client.get(f"/api/online/sources/{sid}").status_code == 401
    assert client.get(f"/api/online/sources/{sid}", headers={"X-Api-Token": TOKEN}).status_code == 200


@pytest.mark.parametrize("payload", [{}, {"enabled": None}, {"enabled": "true"},
                                     {"enabled": 1}, {"enabled": True, "other": 1}])
def test_invalid_settings_are_atomic(client, payload):
    response = client.patch("/api/online/settings", json=payload)
    assert response.status_code == 422
    assert client.get("/api/online/settings").json() == {"enabled": False}


@pytest.mark.parametrize("payload", [
    {**SOURCE, "api_base_url": "https://private-user:private-password@example.invalid/"},
    {**SOURCE, "api_base_url": "https://example.invalid/api?token=private-password"},
    {**SOURCE, "headers": {"Authorization": "Bearer private-password"}},
    ["private-password"],
])
def test_rejected_source_input_is_not_reflected(client, payload):
    response = client.post("/api/online/sources", json=payload)
    assert response.status_code == 422
    assert "private-password" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/online/sources").json() == {"items": []}


def test_patch_rejects_provider_or_null_without_partial_save(client):
    original = client.post("/api/online/sources", json=SOURCE).json()
    path = f"/api/online/sources/{original['id']}"
    for changes in ({"name": "改名", "provider": "open_catalog"},
                    {"name": "改名", "api_base_url": None},
                    {"name": "改名", "api_base_url": "file:///private-password"}):
        response = client.patch(path, json=changes)
        assert response.status_code == 422
        assert "private-password" not in response.text
        assert client.get(path).json() == original


def test_unknown_source_is_not_created_by_patch(client):
    path = "/api/online/sources/src_" + "0" * 32
    for response in (client.get(path), client.patch(path, json={"enabled": True})):
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/online/sources").json() == {"items": []}


def test_viewing_protection_and_write_token_are_both_required(client):
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    store.set_setting("jzmedia_token", TOKEN)
    headers = {"X-Api-Token": TOKEN}
    assert client.get("/api/online/settings", headers=headers).status_code == 403
    assert client.patch("/api/online/settings", headers=headers, json={"enabled": True}).status_code == 403
    assert client.post("/api/viewing/unlock", json={"password": "123456"}).status_code == 200
    assert client.get("/api/online/settings").status_code == 401
    assert client.get("/api/online/settings", headers=headers).status_code == 200
    assert client.patch("/api/online/settings", headers=headers, json={"enabled": True}).status_code == 200
    assert client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    assert client.get("/api/online/sources", headers=headers).status_code == 403
    assert client.post("/api/online/sources", headers=headers, json=SOURCE).status_code == 403
