"""局域网发现：server_id 稳定持久，握手携带展示与认亲字段。"""
from fastapi.testclient import TestClient

from app import store
from app.main import app
from app.store import _base

client = TestClient(app)


def test_server_id_stable_and_persisted():
    first = store.get_server_id()
    assert first and len(first) == 32
    assert store.get_server_id() == first
    with _base._lock, _base._conn() as c:
        row = c.execute("SELECT value FROM app_settings WHERE key='server_id'").fetchone()
    assert row and row["value"] == first


def test_client_info_carries_discovery_fields():
    body = client.get("/api/stream/client-info").json()
    assert body["server_id"] == store.get_server_id()
    assert body["name"]  # 默认媒体库名，回落 jzmedia
    assert body["version"]  # 与 version.properties 一致
    assert client.post("/api/stream/client-check").json() == body
