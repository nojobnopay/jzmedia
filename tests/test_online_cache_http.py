"""Cache HTTP contracts keep shared management and viewing permissions separate."""

from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app import library_paths, store
from app.config import settings
from app.main import app
from app.online import cache
from app.store import online


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "cache-http.db"))
    monkeypatch.setattr(store._base, "ensure_dirs", lambda: None)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "media_root", str(tmp_path / "media"))
    library_paths.invalidate_cache()
    store.init_db()
    online.update_online_settings(True)
    source = online.create_online_source({"name": "Saved source", "provider": "maccms",
        "api_base_url": "https://catalog.example/api", "enabled": False})
    item = online.upsert_online_item(source["id"], "show1", "tv", "Shared show")
    entry = online.upsert_online_episode(item["id"], "ep1", "Episode 1")
    calls = []
    monkeypatch.setattr(cache, "list_items", lambda: {"items": [], "storage": {"ok": True}})
    monkeypatch.setattr(cache, "item_detail", lambda item_id: {"external_id": "show1", "detail": {"item": {"id": item_id}},
                                                            "entries": []})

    def enqueue(*args, **kwargs):
        calls.append((args, kwargs))
        return {"tasks": []}

    def delete(ids, *, before_delete):
        calls.append((ids, before_delete))
        return {"results": [{"id": selected, "deleted": True, "error_code": ""} for selected in ids]}

    monkeypatch.setattr(cache, "enqueue", enqueue)
    monkeypatch.setattr(cache, "delete", delete)
    client = TestClient(app)
    try:
        yield SimpleNamespace(client=client, calls=calls, source=source, item=item,
            body={"source_id": source["id"], "item_id": item["id"], "line_id": "ln_" + "a" * 32,
                  "episode_ids": [entry["id"]]})
    finally:
        client.close()
        library_paths.invalidate_cache()


def test_cache_read_requires_device_session_but_not_source_enable(api):
    assert api.client.get("/api/online/cache/items").status_code == 401
    api.client.get("/api/viewing/session").raise_for_status()
    response = api.client.get("/api/online/cache/items")
    assert response.status_code == 200 and response.json()["enabled"] is True
    assert response.headers["cache-control"] == "no-store"
    response = api.client.get("/api/online/cache/lookup", params={"source_id": api.source["id"], "external_id": "show1"})
    assert response.status_code == 200 and response.json()["external_id"] == "show1"
    online.update_online_settings(False)
    response = api.client.get("/api/online/cache/items")
    assert response.status_code == 200 and response.json()["enabled"] is False
    assert api.client.post("/api/online/cache/tasks", json=api.body).status_code == 503
    cache_id = "oc_" + "b" * 32
    response = api.client.post("/api/online/cache/entries/delete", json={"ids": [cache_id]})
    assert response.status_code == 200 and response.json()["results"][0]["deleted"]


def test_enqueue_stable_identity_contract_and_device_access(api):
    api.client.get("/api/viewing/session").raise_for_status()
    response = api.client.post("/api/online/cache/tasks", json=api.body)
    assert response.status_code == 202
    assert len(api.calls) == 1 and api.calls[0][1]["access"].owner
    for changes in ({"url": "https://secret.example/media?token=private-secret"}, {"episode_ids": ["../secret"]},
                    {"episode_ids": []}, {"line_id": "file:///tmp/secret"}):
        response = api.client.post("/api/online/cache/tasks", json={**api.body, **changes})
        assert response.status_code == 422 and response.json()["code"] == "invalid_online_cache_request"
        assert "private-secret" not in response.text
    assert len(api.calls) == 1


def test_cache_writes_require_management_and_write_token_while_reads_do_not(api):
    api.client.get("/api/viewing/session").raise_for_status()
    api.client.post("/api/viewing/password", json={"new_password": "123456"}).raise_for_status()
    store.set_setting("jzmedia_token", "cache-write-token")
    headers = {"x-api-token": "cache-write-token"}
    assert api.client.get("/api/online/cache/items").status_code == 200
    assert api.client.post("/api/online/cache/tasks", json=api.body, headers=headers).status_code == 403
    api.client.post("/api/viewing/unlock", json={"password": "123456"}).raise_for_status()
    assert api.client.post("/api/online/cache/tasks", json=api.body).status_code == 401
    assert api.client.post("/api/online/cache/tasks", json=api.body, headers=headers).status_code == 202
    cache_id = "oc_" + "b" * 32
    response = api.client.post("/api/online/cache/entries/delete", json={"ids": [cache_id]}, headers=headers)
    assert response.status_code == 200 and response.json()["results"][0]["deleted"]


def test_children_cannot_read_or_mutate_shared_cache(api):
    api.client.get("/api/viewing/session").raise_for_status()
    api.client.post("/api/viewing/password", json={"new_password": "123456"}).raise_for_status()
    api.client.post("/api/viewing/mode", json={"mode": "children"}).raise_for_status()
    assert api.client.get("/api/online/cache/items").status_code == 403
    assert api.client.get(f'/api/online/cache/items/{api.item["id"]}').status_code == 403
    assert api.client.post("/api/online/cache/tasks", json=api.body).status_code == 403


def test_cache_errors_are_fixed_and_do_not_expose_paths(api, monkeypatch):
    api.client.get("/api/viewing/session").raise_for_status()

    def unavailable(_item_id):
        raise cache.CacheError("online_cache_corrupt", 409)

    monkeypatch.setattr(cache, "item_detail", unavailable)
    response = api.client.get(f'/api/online/cache/items/{api.item["id"]}')
    assert response.status_code == 409 and response.json()["code"] == "online_cache_corrupt"
    assert "tmp" not in response.text and "catalog.example" not in response.text
