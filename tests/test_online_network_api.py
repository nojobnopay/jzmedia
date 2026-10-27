"""Management network APIs never connect to providers or echo proxy credentials."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import library_paths, secrets, store
from app.main import app


SOURCE = {"name": "隔离来源", "provider": "maccms", "api_base_url": "https://source.example.invalid/api"}
PROXY = {"name": "本机代理", "mode": "proxy", "proxy_url": "http://127.0.0.1:17891",
         "username": "private-proxy-user", "password": "private-proxy-password"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "network-api.db"))
    monkeypatch.setattr(secrets.settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    secrets.reset_cache()
    library_paths.invalidate_cache()
    store.init_db()

    def no_network(*_args, **_kwargs):
        pytest.fail("network configuration must not open an upstream connection")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    candidate = TestClient(app)
    try:
        yield candidate
    finally:
        candidate.close()
        library_paths.invalidate_cache()
        secrets.reset_cache()


def test_default_direct_and_no_profiles_without_changing_feature_flag(client):
    default = client.get("/api/online/network/default")
    assert default.status_code == 200
    assert default.json()["rule"] == {"mode": "direct", "profile_id": None}
    assert default.headers["cache-control"] == "no-store"
    assert client.get("/api/online/network/profiles").json() == {"items": []}
    assert client.get("/api/online/settings").json() == {"enabled": False}


def test_credentials_are_write_only_and_profile_updates_preserve_or_clear(client):
    created = client.post("/api/online/network/profiles", json=PROXY)
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["has_credentials"] is True
    path = f"/api/online/network/profiles/{record['id']}"
    changed = client.patch(path, json={"name": "改名"})
    assert changed.status_code == 200
    assert changed.json()["has_credentials"] is True
    assert changed.json()["revision"] > record["revision"]
    for response in [created, changed, client.get(path), client.get("/api/online/network/profiles")]:
        assert PROXY["username"] not in response.text
        assert PROXY["password"] not in response.text
        assert "credentials_encrypted" not in response.text
        assert response.headers["cache-control"] == "no-store"
    cleared = client.patch(path, json={"clear_credentials": True})
    assert cleared.status_code == 200
    assert cleared.json()["has_credentials"] is False
    assert client.delete(path).status_code == 204
    assert client.get(path).status_code == 404


def test_rules_reference_profiles_and_block_deletion_until_unreferenced(client):
    sid = client.post("/api/online/sources", json=SOURCE).json()["id"]
    profile = client.post("/api/online/network/profiles", json=PROXY).json()
    pid = profile["id"]
    rule = {"mode": "proxy", "profile_id": pid}
    default = client.patch("/api/online/network/default", json={"rule": rule})
    assert default.status_code == 200
    path = f"/api/online/sources/{sid}/network"
    original = client.get(path).json()
    assert original["policy"]["default"]["mode"] == "inherit"
    response = client.patch(path, json={"api": rule, "media": {"mode": "direct"}})
    assert response.status_code == 200, response.text
    assert response.json()["policy"]["api"] == rule
    assert response.json()["policy"]["media"]["mode"] == "direct"
    assert client.delete(f"/api/online/network/profiles/{pid}").status_code == 409
    assert client.patch("/api/online/network/default", json={"rule": {"mode": "direct"}}).status_code == 200
    assert client.delete(f"/api/online/network/profiles/{pid}").status_code == 409
    assert client.patch(path, json={"api": {"mode": "inherit"}}).status_code == 200
    assert client.delete(f"/api/online/network/profiles/{pid}").status_code == 204


@pytest.mark.parametrize("payload", [{}, {"rule": {"mode": "inherit"}},
                                     {"rule": None}, {"rule": {"mode": "direct"}, "other": True}])
def test_invalid_default_does_not_change_saved_rule(client, payload):
    before = client.get("/api/online/network/default").json()
    response = client.patch("/api/online/network/default", json=payload)
    assert response.status_code == 422
    assert client.get("/api/online/network/default").json() == before


@pytest.mark.parametrize("changes", [
    {"name": "不应保存", "mode": "direct"},
    {"name": "不应保存", "password": None},
    {"clear_credentials": True, "password": "private-proxy-password"},
    {"name": "不应保存", "proxy_url": "http://private-proxy-user:private-proxy-password@localhost:7890"},
])
def test_invalid_profile_patch_is_atomic_and_does_not_reflect_input(client, changes):
    original = client.post("/api/online/network/profiles", json=PROXY).json()
    path = f"/api/online/network/profiles/{original['id']}"
    response = client.patch(path, json=changes)
    assert response.status_code == 422
    assert "private-proxy-password" not in response.text
    assert "private-proxy-user" not in response.text
    assert client.get(path).json() == original


def test_invalid_source_rule_does_not_partially_save(client):
    sid = client.post("/api/online/sources", json=SOURCE).json()["id"]
    direct = client.post("/api/online/network/profiles", json={"name": "直连", "mode": "direct"}).json()
    path = f"/api/online/sources/{sid}/network"
    before = client.get(path).json()
    response = client.patch(path, json={"media": {"mode": "direct"},
                                        "api": {"mode": "proxy", "profile_id": direct["id"]}})
    assert response.status_code == 422
    assert client.get(path).json() == before
    assert client.patch(path, json={"api": None}).status_code == 422


def test_effective_route_explanation_has_no_credentials_and_does_not_connect(client):
    sid = client.post("/api/online/sources", json=SOURCE).json()["id"]
    proxy = client.post("/api/online/network/profiles", json=PROXY).json()
    base = f"/api/online/sources/{sid}/network"
    assert client.patch(base, json={"api": {"mode": "proxy", "profile_id": proxy["id"]}}).status_code == 200
    api = client.get(base + "/effective", params={"purpose": "api"})
    assert api.status_code == 200, api.text
    assert api.json()["mode"] == "proxy"
    assert api.json()["selected_by"] == "purpose"
    assert api.json()["target_dns"] == "local_pinned"
    assert api.json()["destination_validation"] == "required_per_request"
    assert "private-proxy-user" not in api.text and "private-proxy-password" not in api.text
    assert client.get(base + "/effective", params={"purpose": "media"}).json()["mode"] == "direct"
    assert client.get(base + "/effective", params={"purpose": "unknown"}).status_code == 422


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/api/online/network/profiles", None),
    ("GET", "/api/online/network/default", None),
    ("POST", "/api/online/network/profiles", PROXY),
    ("PATCH", "/api/online/network/default", {"rule": {"mode": "direct"}}),
])
def test_network_management_requires_configured_token(client, method, path, payload):
    store.set_setting("jzmedia_token", "network-api-token")
    kwargs = {"json": payload} if payload is not None else {}
    denied = client.request(method, path, **kwargs)
    assert denied.status_code == 401
    assert denied.headers["cache-control"] == "no-store"
    accepted = client.request(method, path, headers={"X-Api-Token": "network-api-token"}, **kwargs)
    assert accepted.status_code in (200, 201)


def test_network_management_requires_viewing_grant_and_excludes_children(client):
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    assert client.get("/api/online/network/profiles").status_code == 403
    assert client.post("/api/online/network/profiles", json=PROXY).status_code == 403
    assert client.post("/api/viewing/unlock", json={"password": "123456"}).status_code == 200
    assert client.post("/api/online/network/profiles", json=PROXY).status_code == 201
    assert client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    assert client.get("/api/online/network/default").status_code == 403


def test_no_external_check_or_fetch_operation_is_exposed(client):
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/online/network/check" not in paths
    assert "/api/online/network/fetch" not in paths


def test_damaged_default_is_not_silently_treated_as_direct(client):
    store.set_setting("online_network_default", "private-corrupted-value")
    response = client.get("/api/online/network/default")
    assert response.status_code == 422
    assert "private-corrupted-value" not in response.text
    repaired = client.patch("/api/online/network/default", json={"rule": {"mode": "direct"}})
    assert repaired.status_code == 200
    assert client.get("/api/online/network/default").json() == repaired.json()
