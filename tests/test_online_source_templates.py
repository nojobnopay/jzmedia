"""Suggestions are protected configuration data, never automatic source import."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app
from app.online.models import OnlineSourceCreate
from app.online.source_templates import list_source_templates, reviewed_host_replacements


BAOFENG_API = "https://apibfzy.com/api.php/provide/vod/"
LIANGZI_API = "https://lzzyapi.com/api.php/provide/vod/"


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
    assert [item["api_base_url"] for item in items[1:3]] == [BAOFENG_API, LIANGZI_API]
    assert [item["known_api_urls"] for item in items] == [
        [items[0]["api_base_url"]], [BAOFENG_API, "https://bfzyapi.com/api.php/provide/vod/"],
        [LIANGZI_API, "https://cj.lziapi.com/api.php/provide/vod/"], [items[3]["api_base_url"]],
    ]
    assert [item["status"] for item in items[1:3]] == ["api_observed", "api_observed"]
    assert {"v.fengbao8.com", "p.bfp261112.com"} <= set(items[1]["allowed_hosts"])
    assert "p.bfvp26.com" not in items[1]["allowed_hosts"]
    assert {"v.cdnlz28.com", "lzv34.com", "p.lzp261118.com"} <= set(items[2]["allowed_hosts"])
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


def test_host_replacements_scope_the_operator_and_document_each_mapping():
    old_api = "https://cj.lziapi.com:443/api.php/provide/vod"
    liangzi = reviewed_host_replacements(old_api, LIANGZI_API)
    assert [(row["kind"], row["old_host"], row["new_host"]) for row in liangzi] == [
        ("media", "v.lzcdn28.com", "v.cdnlz28.com"),
        ("image", "p.lztutu.com", "p.lzp261118.com"),
    ]
    assert {row["notice_url"] for row in liangzi} == {"https://lzizy.net/index.php/macrep.html"}
    assert reviewed_host_replacements(LIANGZI_API, LIANGZI_API) == liangzi
    assert reviewed_host_replacements("https://bfzyapi.com/api.php/provide/vod/", BAOFENG_API) == [
        {"old_host": "p.bfvp26.com", "new_host": "p.bfp261112.com",
         "kind": "image", "notice_url": "https://bfzy.tv/index.php/macrep.html"},
    ]


@pytest.mark.parametrize("other", [
    BAOFENG_API, "https://lzzyapi.com.evil.example/api.php/provide/vod/",
    "http://lzzyapi.com/api.php/provide/vod/", "https://lzzyapi.com:444/api.php/provide/vod/",
    LIANGZI_API + "?ac=detail", LIANGZI_API + "at/xml/", LIANGZI_API + "/",
])
def test_host_replacements_require_both_recognized_urls(other):
    assert reviewed_host_replacements(other, LIANGZI_API) == []
    assert reviewed_host_replacements(LIANGZI_API, other) == []


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
