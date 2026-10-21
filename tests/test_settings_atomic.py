"""Settings validation and persistence must not leave partially applied changes."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "settings.db"))
    store.init_db()
    library_paths.invalidate_cache()
    yield
    library_paths.invalidate_cache()


@pytest.mark.parametrize("invalid", [
    {"tmdb_language": "invalid-language-value"},
    {"tmdb_image_base": "not-a-url"},
    {"jzmedia_token": "short"},
])
def test_rejected_settings_leave_all_previous_values_intact(invalid):
    original = {"tmdb_read_token": "original-test-token", "tmdb_language": "zh-CN"}
    store.set_settings(original)
    response = client.put("/api/settings", json={"tmdb_read_token": "replacement-test-token", **invalid})
    assert response.status_code == 422
    assert {key: store.get_setting(key) for key in original} == original


def test_settings_save_together_and_keep_null_values():
    store.set_setting("tmdb_api_key", "original-api-key")
    response = client.put("/api/settings", json={
        "tmdb_read_token": "replacement-test-token", "tmdb_language": "en-US",
        "tmdb_api_key": None,
    })
    assert response.status_code == 200
    assert store.get_setting("tmdb_read_token") == "replacement-test-token"
    assert store.get_setting("tmdb_language") == "en-US"
    assert store.get_setting("tmdb_api_key") == "original-api-key"


def test_settings_database_failure_rolls_back_earlier_writes():
    original = {"tmdb_read_token": "original-test-token", "tmdb_language": "zh-CN"}
    store.set_settings(original)
    with store._base._conn() as connection:
        connection.execute("""CREATE TRIGGER reject_language BEFORE INSERT ON app_settings
            WHEN NEW.key = 'tmdb_language'
            BEGIN SELECT RAISE(ABORT, 'simulated settings write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated settings write failure"):
        store.set_settings({"tmdb_read_token": "replacement-test-token", "tmdb_language": "en-US"})
    assert {key: store.get_setting(key) for key in original} == original


def test_unknown_setting_does_not_partially_save():
    store.set_setting("tmdb_language", "zh-CN")
    with pytest.raises(ValueError, match="unknown setting"):
        store.set_settings({"tmdb_language": "en-US", "unsupported-setting": "value"})
    assert store.get_setting("tmdb_language") == "zh-CN"


@pytest.mark.parametrize("token", ["测试访问令牌八个字", "café-secret", "test\nsecret", "test\x00secret"])
def test_unusable_access_token_is_rejected_before_saving(token):
    response = client.put("/api/settings", json={"jzmedia_token": token})
    assert response.status_code == 422
    assert store.get_setting("jzmedia_token") == ""
    assert client.put("/api/settings", json={"tmdb_language": "zh-CN"}).status_code == 200


@pytest.mark.parametrize("token", ["ascii-secret", "测试访问令牌八个字"])
def test_non_ascii_request_or_legacy_token_is_unauthorized_not_server_error(token):
    store.set_setting("jzmedia_token", token)
    for headers in ({}, {b"X-Api-Token": b"incorrect-\xff-secret"}):
        response = client.put("/api/settings", json={"tmdb_language": "en-US"}, headers=headers)
        assert response.status_code == 401
    assert store.get_setting("tmdb_language") == ""


def test_valid_token_can_be_replaced_and_new_token_is_required():
    store.set_setting("jzmedia_token", "original-secret")
    response = client.put("/api/settings", json={"jzmedia_token": "replacement-secret"},
                          headers={"X-Api-Token": "original-secret"})
    assert response.status_code == 200
    assert client.put("/api/settings", json={"tmdb_language": "en-US"},
                      headers={"X-Api-Token": "original-secret"}).status_code == 401
    assert client.put("/api/settings", json={"tmdb_language": "en-US"},
                      headers={"X-Api-Token": "replacement-secret"}).status_code == 200
