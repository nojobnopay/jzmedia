"""Suggestions are protected configuration data, never automatic source import."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app
from app.online.models import OnlineSourceCreate
from app.online.source_templates import list_source_templates


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "templates.db"))
    library_paths.invalidate_cache()
    store.init_db()

    def no_network(*_args, **_kwargs):
        pytest.fail("listing templates may not access their websites")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    candidate = TestClient(app)
    try:
        yield candidate
    finally:
        candidate.close()
        library_paths.invalidate_cache()


def test_template_directory_does_not_enable_feature_import_sources_or_access_network(client):
    response = client.get("/api/online/source-templates")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    assert [item["id"] for item in items] == ["blender-open-example", "baofeng", "liangzi", "feifan"]
    assert client.get("/api/online/settings").json() == {"enabled": False}
    assert client.get("/api/online/sources").json() == {"items": []}
    assert items[1]["status"] == "api_observed"
    assert all("未验证" in item["network_note"] or "需验证" in item["network_note"]
               or "未通过" in item["network_note"] for item in items)


def test_template_fields_make_valid_explicit_source_creates():
    for item in list_source_templates()["items"]:
        draft = OnlineSourceCreate.model_validate({key: item[key] for key in (
            "name", "provider", "api_base_url", "allowed_hosts", "provider_options")})
        assert draft.allowed_hosts == item["allowed_hosts"]
        assert all("*" not in host and "/" not in host for host in draft.allowed_hosts)
        assert item["homepage"].startswith("https://")


def test_returned_templates_cannot_mutate_later_requests():
    response = list_source_templates()
    response["items"][0]["allowed_hosts"].append("attacker.example")
    response["items"][1]["provider_options"]["search_action"] = "videolist"
    later = list_source_templates()
    assert "attacker.example" not in later["items"][0]["allowed_hosts"]
    assert later["items"][1]["provider_options"]["search_action"] == "list"


def test_template_reads_require_the_same_write_token_as_source_management(client):
    store.set_setting("jzmedia_token", "template-admin-token")
    assert client.get("/api/online/source-templates").status_code == 401
    response = client.get("/api/online/source-templates", headers={"X-Api-Token": "template-admin-token"})
    assert response.status_code == 200


def test_templates_require_management_unlock_and_are_unavailable_in_children_mode(client):
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    assert client.get("/api/online/source-templates").status_code == 403
    assert client.post("/api/viewing/unlock", json={"password": "123456"}).status_code == 200
    assert client.get("/api/online/source-templates").status_code == 200
    assert client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    assert client.get("/api/online/source-templates").status_code == 403
