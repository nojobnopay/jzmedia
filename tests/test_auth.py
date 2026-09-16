"""P1-01 写操作访问令牌回归网：未配置全放行；配置后写需 token、读仍放行。"""
import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app

client = TestClient(app)
TOKEN = "smoke-test-token-123"


@pytest.fixture()
def auth_on():
    """开启鉴权（写库），用例结束清空恢复。"""
    store.set_setting("jzmedia_token", TOKEN)
    yield TOKEN
    store.set_setting("jzmedia_token", "")


def _create(name: str, headers=None):
    return client.post("/api/collections", json={"name": name}, headers=headers or {})


def test_write_open_when_token_unset():
    r = _create("auth-open-合集")
    assert r.status_code in (200, 201, 422), r.text
    if r.status_code == 200:
        cid = r.json().get("id")
        assert client.delete(f"/api/collections/{cid}").status_code == 200


def test_write_requires_token(auth_on):
    r = _create("auth-blocked-合集")
    assert r.status_code == 401
    assert "unauthorized" in r.json().get("detail", "")


def test_write_accepts_x_api_token(auth_on):
    r = _create("auth-x-token-合集", headers={"X-Api-Token": TOKEN})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert client.delete(f"/api/collections/{cid}",
                         headers={"X-Api-Token": TOKEN}).status_code == 200


def test_write_accepts_bearer_token(auth_on):
    r = _create("auth-bearer-合集", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, r.text
    client.delete(f"/api/collections/{r.json()['id']}",
                  headers={"Authorization": f"Bearer {TOKEN}"})


def test_write_rejects_wrong_token(auth_on):
    assert _create("auth-wrong-合集", headers={"X-Api-Token": "nope"}).status_code == 401


def test_read_stays_open(auth_on):
    assert client.get("/api/movies").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_settings_exposes_auth_state(auth_on):
    d = client.get("/api/settings").json()
    assert d["jzmedia_auth_enabled"] is True
    assert d["jzmedia_token_masked"].startswith("****")
    assert TOKEN not in str(d)          # 绝不明文回显


def test_settings_update_requires_token_when_enabled(auth_on):
    assert client.put("/api/settings", json={"tmdb_language": "zh-CN"}).status_code == 401
    r = client.put("/api/settings", json={"tmdb_language": "zh-CN"},
                   headers={"X-Api-Token": TOKEN})
    assert r.status_code == 200 and r.json()["tmdb_language"] == "zh-CN"
