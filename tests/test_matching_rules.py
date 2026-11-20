from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app
from app.metadata import chain


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "rules.db"))
    store.init_db()
    library_paths.invalidate_cache()
    test_client = TestClient(app)
    yield test_client
    test_client.close()
    library_paths.invalidate_cache()


DEFAULT = list(chain.DEFAULT_CHAIN)
URL = f"/api/libraries/{store.DEFAULT_LIBRARY_ID}/matching-rules"


@pytest.mark.parametrize(("raw", "expected"), [
    ("", DEFAULT), ("not json", DEFAULT), ("{}", DEFAULT), ('["retired"]', DEFAULT),
    ('[" WIKIDATA ", "old", "wikidata", "local"]', ["wikidata", "local"]),
])
def test_conditional_save_from_existing_or_default_rule(client, raw, expected):
    path = f"/api/libraries/{store.DEFAULT_LIBRARY_ID}"
    assert client.patch(path, json={"metadata_providers": raw}).status_code == 200
    chain.chain_for(store.DEFAULT_LIBRARY_ID)  # prime the runtime library cache
    new = ["tmdb", "local"]
    response = client.put(URL, json={"expected": expected, "providers": new})
    assert response.status_code == 200
    assert response.json() == {"providers": new}
    assert chain.chain_for(store.DEFAULT_LIBRARY_ID) == new
    assert client.put(URL, json={"expected": expected, "providers": ["nfo"]}).status_code == 409
    assert chain.chain_for(store.DEFAULT_LIBRARY_ID) == new


@pytest.mark.parametrize("invalid", [
    {"providers": []}, {"expected": []}, {"providers": ["local", "local"]},
    {"expected": [" LOCAL "]}, {"providers": ["unknown"]}, {"providers": [2]},
    {"providers": "tmdb"}, {"enabled": False},
])
def test_invalid_changes_leave_the_library_untouched(client, invalid):
    body = {"expected": DEFAULT, "providers": ["tmdb"], **invalid}
    assert client.put(URL, json=body).status_code == 422
    assert store.get_library(store.DEFAULT_LIBRARY_ID)["metadata_providers"] == ""


def test_matching_rules_require_current_write_and_management_access(client):
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    store.set_setting("jzmedia_token", "rules-test-token")
    body = {"expected": DEFAULT, "providers": ["tmdb"]}
    headers = {"X-Api-Token": "rules-test-token"}
    assert client.put(URL, json=body).status_code == 401
    assert client.put(URL, json=body, headers=headers).status_code == 403
    assert chain.chain_for(store.DEFAULT_LIBRARY_ID) == DEFAULT
    assert client.post("/api/viewing/unlock", json={"password": "123456"}).status_code == 200
    assert client.put("/api/libraries/999999/matching-rules", json=body, headers=headers).status_code == 404
    assert client.put(URL, json=body, headers=headers).status_code == 200
    assert client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    assert client.put(URL, json={"expected": ["tmdb"], "providers": DEFAULT}, headers=headers).status_code == 403
    assert chain.chain_for(store.DEFAULT_LIBRARY_ID) == ["tmdb"]


def test_only_one_concurrent_confirm_can_replace_the_reviewed_rule(client):
    barrier = Barrier(2)

    def confirm(providers):
        barrier.wait(timeout=5)
        return client.put(URL, json={"expected": DEFAULT, "providers": providers})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(confirm, (["local"], ["tmdb"])))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response for response in responses if response.status_code == 200)
    assert chain.chain_for(store.DEFAULT_LIBRARY_ID) == winner.json()["providers"]
