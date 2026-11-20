"""The initial online APIs manage configuration without accessing any provider."""

import pytest
import httpx
from fastapi.testclient import TestClient
import test_online_state as playback_state

from app import library_paths, store
from app.main import app


SOURCE = {"name": "隔离免费来源", "provider": "maccms",
          "api_base_url": "https://source.example.invalid/api.php/provide/vod/"}
TOKEN = "online-admin-test-token"
viewing_device = playback_state.environment


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
    created = client.post("/api/online/sources", json=SOURCE).json()
    path = f"/api/online/sources/{created['id']}"
    original = client.get(path).json()
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


def test_host_migration_reviews_existing_hosts_and_applies_only_selected(viewing_device):
    client = viewing_device.client
    path = "/api/online/sources/" + viewing_device.source["id"]
    client.patch(path, json={
        "api_base_url": "https://cj.lziapi.com/api.php/provide/vod/",
        "allowed_hosts": ["cj.lziapi.com", "v.lzcdn28.com", "p.lztutu.com", "custom.example"],
    }).raise_for_status()
    playback_state.favorite(viewing_device)
    writer, _ = playback_state.claim(viewing_device, item=viewing_device.show, episode=viewing_device.episodes[0])
    playback_state.save(viewing_device, writer)
    progress = viewing_device.scope(item=viewing_device.show)["last_progress"]
    assert progress["position"] == 12 and progress["identity"]["episode_id"] == viewing_device.episodes[0]["id"]
    policy = client.patch(path + "/network", json={"media": {"mode": "direct"}}).json()
    assert policy["policy"]["media"]["mode"] == "direct"
    original = client.get(path).json()
    url = "https://lzzyapi.com/api.php/provide/vod/"
    draft = {"api_base_url": url, "allowed_hosts": [*original["allowed_hosts"], "new.custom.example"]}
    preview = client.post(path + "/host-migration-preview",
                          json={**draft, "expected_fingerprint": original["configuration_fingerprint"]})
    assert preview.status_code == 200
    report = preview.json()
    changes = {change["old_host"]: change for change in report["changes"]}
    assert {old: (row["new_host"], row["kind"]) for old, row in changes.items()} == {
        "cj.lziapi.com": ("lzzyapi.com", "api"),
        "v.lzcdn28.com": ("v.cdnlz28.com", "media"),
        "p.lztutu.com": ("p.lzp261118.com", "image"),
    }
    assert changes["p.lztutu.com"]["notice_url"].startswith("https://")
    assert client.get(path).json() == original
    assert viewing_device.registry.stats()["sessions"] == 1
    saved = client.patch(path, json={**draft, "host_migration": {
        "expected_fingerprint": report["expected_fingerprint"], "selected_old_hosts": ["p.lztutu.com"]}})
    assert viewing_device.registry.stats()["sessions"] == 0
    assert saved.status_code == 200 and saved.json()["id"] == viewing_device.source["id"]
    assert viewing_device.scope()["favorite"] is True
    assert viewing_device.scope(item=viewing_device.show)["last_progress"] == progress
    assert client.get(path + "/network").json() == policy
    assert saved.json()["allowed_hosts"] == ["cj.lziapi.com", "v.lzcdn28.com", "p.lzp261118.com",
                                            "custom.example", "new.custom.example", "lzzyapi.com"]


def test_host_migration_compares_editor_snapshot_draft_and_candidate_choices(client):
    sid = client.post("/api/online/sources", json=SOURCE).json()["id"]
    path = "/api/online/sources/" + sid
    endpoint = path + "/host-migration-preview"
    original = client.get(path).json()
    draft = {"api_base_url": "https://new.example/vod", "allowed_hosts": original["allowed_hosts"]}
    editor = {"expected_fingerprint": original["configuration_fingerprint"]}
    report = client.post(endpoint, json={**draft, **editor}).json()
    selection = {"expected_fingerprint": report["expected_fingerprint"], "selected_old_hosts": ["source.example.invalid"]}
    invalid = client.patch(path, json={**draft, "host_migration": {**selection, "selected_old_hosts": ["other.example"]}})
    assert invalid.status_code == 422 and client.get(path).json() == original
    for change in ({"allowed_hosts": []}, {"api_base_url": "https://other.example/vod"}):
        response = client.patch(path, json={**draft, **change, "host_migration": selection})
        assert response.status_code == 409 and response.json()["code"] == "online_source_changed"
    assert client.get(path).json() == original
    client.patch(path, json={"priority": 7})
    current = client.get(path).json()
    for response in (client.post(endpoint, json={**draft, **editor}),
                     client.patch(path, json={**draft, "priority": 1, "host_migration": selection})):
        assert response.status_code == 409 and response.json()["code"] == "online_source_changed"
    assert client.get(path).json() == current


def test_host_migration_at_capacity_and_legacy_patches(client):
    hosts = ["source.example.invalid", *(f"cdn{i}.example" for i in range(127))]
    sid = client.post("/api/online/sources", json={**SOURCE, "allowed_hosts": hosts}).json()["id"]
    path = "/api/online/sources/" + sid
    draft = {"api_base_url": "https://new.example/vod", "allowed_hosts": hosts}
    report = client.post(path + "/host-migration-preview", json=draft).json()
    selection = {"expected_fingerprint": report["expected_fingerprint"], "selected_old_hosts": []}
    assert client.patch(path, json={**draft, "host_migration": selection}).status_code == 422
    assert client.get(path).json()["allowed_hosts"] == hosts
    selection["selected_old_hosts"] = [hosts[0]]
    saved = client.patch(path, json={**draft, "host_migration": selection})
    assert saved.status_code == 200 and saved.json()["allowed_hosts"] == ["new.example", *hosts[1:]]
    changed = client.patch(path, json={"api_base_url": SOURCE["api_base_url"]})
    assert changed.status_code == 200 and changed.json()["allowed_hosts"] == hosts
    legacy = client.patch(path, json={**draft, "allowed_hosts": [hosts[0], hosts[1]]})
    assert legacy.status_code == 200 and legacy.json()["allowed_hosts"] == [hosts[0], hosts[1], "new.example"]


def test_host_migration_ignores_custom_endpoints_and_newly_typed_media_hosts(client):
    source = {**SOURCE, "api_base_url": "https://cj.lziapi.com/api.php/provide/vod/",
              "allowed_hosts": ["cj.lziapi.com", "p.lztutu.com"]}
    sid = client.post("/api/online/sources", json=source).json()["id"]
    path = "/api/online/sources/" + sid
    for url, kinds in (("http://cj.lziapi.com:8080/custom", []),
                       ("https://lzzyapi.com/custom", ["api"]),
                       ("https://api.other.example/api.php/provide/vod/", ["api"]),
                       ("https://lzzyapi.com/api.php/provide/vod/", ["api", "image"])):
        draft = {"api_base_url": url, "allowed_hosts": [*source["allowed_hosts"], "v.lzcdn28.com"]}
        response = client.post(path + "/host-migration-preview", json=draft)
        assert response.status_code == 200 and [row["kind"] for row in response.json()["changes"]] == kinds
    # A URL-only patch should not introduce a notice's new media or image host.
    response = client.patch(path, json={"api_base_url": "https://lzzyapi.com/api.php/provide/vod/"})
    assert response.status_code == 200 and response.json()["allowed_hosts"] == ["lzzyapi.com", "p.lztutu.com"]


def test_host_migration_preview_is_management_only_and_rejects_invalid_input_privately(client):
    sid = client.post("/api/online/sources", json=SOURCE).json()["id"]
    endpoint = f"/api/online/sources/{sid}/host-migration-preview"
    draft = {"api_base_url": SOURCE["api_base_url"], "allowed_hosts": []}
    assert client.post(endpoint.replace(sid, "src_" + "0" * 32), json=draft).status_code == 404
    store.set_setting("jzmedia_token", TOKEN)
    assert client.post(endpoint, json=draft).status_code == 401
    headers = {"X-Api-Token": TOKEN}
    assert client.post("/api/viewing/password", headers=headers, json={"new_password": "123456"}).status_code == 200
    assert client.post(endpoint, json=draft, headers=headers).status_code == 403
    assert client.post("/api/viewing/unlock", json={"password": "123456"}).status_code == 200
    invalid = client.post(endpoint, headers=headers,
                          json={**draft, "api_base_url": "https://admin:secret-value@example.invalid"})
    assert invalid.status_code == 422 and "secret-value" not in invalid.text
    assert invalid.headers["cache-control"] == "no-store"
