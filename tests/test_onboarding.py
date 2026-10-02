"""First-use state must reflect real registration, never a client completion flag."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, library_paths, onboarding, storage, store, tmdb
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, media_root):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "guide.db"))
    store.init_db()
    library_paths.invalidate_cache()
    yield
    library_paths.invalidate_cache()


def patch(**changes):
    return client.patch("/api/onboarding", json=changes)


def current():
    response = client.get("/api/onboarding")
    assert response.status_code == 200, response.text
    return response.json()


def movie(lid=1):
    mid = store.upsert_movie_by_path("sample.mkv", library_id=lid)
    store.update_movie_meta(mid, title="测试影片")
    return mid


def choose(lid=1, kind="movie"):
    response = patch(status="active", tmdb_skipped=True, library_id=lid, kind=kind, step=3)
    assert response.status_code == 200
    assert response.json()["step"] == 2
    check = client.post(f"/api/libraries/{lid}/check")
    assert check.status_code == 200, check.text
    assert check.json()["readable"] and check.json()["video"]["ok"], check.text
    response = patch(step=3)
    assert response.json()["step"] == 3, response.text


def test_seeded_default_still_needs_guide_and_get_is_local(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("GET must not inspect network or media directories")
    monkeypatch.setattr(storage, "backend_for", forbidden)
    monkeypatch.setattr(tmdb, "_client", forbidden)
    assert store.list_libraries()
    data = current()
    assert data["show_welcome"] and data["step"] == 1
    assert data["content"]["total"] == 0 and not data["can_complete"]
    assert "signature" not in json.dumps(data)


def test_existing_content_does_not_trigger_new_user_invitation():
    movie()
    assert not current()["show_welcome"]


def test_tmdb_skip_pause_and_resume():
    assert patch(status="active", tmdb_skipped=True, step=2).json()["step"] == 2
    assert current()["show_welcome"]
    assert not patch(status="deferred").json()["show_welcome"]
    assert patch(status="active").json()["step"] == 2


def test_completion_requires_connection_and_registered_content():
    movie()
    assert patch(status="completed", step=4).status_code == 409
    choose()
    data = patch(status="completed", step=4).json()
    assert data["status"] == "completed" and data["can_complete"]
    assert data["content"]["count"] == 1
    assert data["content"]["pending"] == 1
    assert data["content"]["items"][0]["title"] == "测试影片"
    assert not data["show_welcome"]


def test_subtitles_and_claimed_upload_counts_cannot_complete(media_root):
    choose()
    (media_root / "onboarding-only.srt").write_text("subtitle")
    assert patch(upload_result={"library_id": 1, "uploaded": 10, "skipped": 0, "failed": 0}).status_code == 200
    assert current()["content"]["count"] == 0
    assert patch(status="completed").status_code == 409


def test_content_in_another_library_does_not_complete_target(tmp_path):
    root = tmp_path / "other"; root.mkdir()
    lib = store.create_library(name="other", path=str(root))
    movie(lib["id"])
    choose()
    assert current()["content"]["total"] == 1
    assert not current()["can_complete"]


@pytest.mark.parametrize("change", ["disabled", "media_disabled", "kind", "path", "deleted"])
def test_resume_revalidates_target(change, tmp_path):
    choose()
    if change == "disabled":
        store.update_library(1, enabled=False)
    elif change == "media_disabled":
        store.update_media_library(1, enabled=False)
    elif change == "kind":
        store.update_library(1, kind="tv")
    elif change == "path":
        path = tmp_path / "newroot"; path.mkdir()
        store.update_media_library(1, path=str(path))
    else:
        store.delete_media_library(1)
    data = current()
    assert data["step"] == 2
    assert not data["library_verified"] and not data["can_complete"]


def test_readonly_library_and_tv_registration(tmp_path):
    root = tmp_path / "tv"; root.mkdir()
    lib = store.create_library(name="readonly-tv", path=str(root), kind="tv", read_only=True)
    choose(lib["id"], "tv")
    show = store.upsert_show(lib["id"], "新剧")
    assert current()["content"]["count"] == 0  # a show without episodes is not imported
    ep = store.upsert_episode(show, lib["id"], "新剧/S01E01.mkv", 1, 1)
    data = patch(status="completed", step=4).json()
    assert data["can_complete"] and data["content"]["count"] == 1
    assert data["content"]["items"][0]["id"] == ep


def test_library_diagnostic_failure_clears_previous_check():
    choose()
    signature = onboarding.library_signature(store.get_library(1))
    onboarding.record_check("library", signature, False, library_id=1)
    assert not current()["library_verified"]
    assert current()["step"] == 2


def test_metadata_from_nfo_is_not_marked_unmatched():
    choose()
    mid = movie()
    store.update_movie_meta(mid, match_source="nfo")
    assert current()["content"]["pending"] == 0


def test_tmdb_check_missing_config_does_not_request_network(monkeypatch):
    monkeypatch.setattr(tmdb, "_client", lambda: pytest.fail("unexpected network"))
    result = client.post("/api/tmdb/check").json()
    assert not result["ok"] and result["code"] == "not_configured"


@pytest.mark.parametrize("outcome,expected", [(200, "ok"), (401, "invalid_credentials"),
    (429, "upstream_error"), ("timeout", "timeout"), ("network", "connection_error"),
    ("invalid_json", "invalid_response")])
def test_tmdb_validation_direct_no_retry_and_redacts_errors(monkeypatch, outcome, expected):
    store.set_setting("tmdb_api_key", "secret-api-key")
    calls = []
    def respond(request):
        calls.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("secret-api-key", request=request)
        if outcome == "network":
            raise httpx.ConnectError("proxy-secret", request=request)
        if outcome == "invalid_json":
            return httpx.Response(200, content=b"not json")
        return httpx.Response(outcome, json={"success": outcome == 200})
    monkeypatch.setattr(tmdb, "_client", lambda: httpx.Client(
        base_url=tmdb.API_BASE, transport=httpx.MockTransport(respond)))
    result = client.post("/api/tmdb/check")
    assert result.status_code == 200  # upstream 401 is not the application's auth challenge
    data = result.json()
    assert data["code"] == expected
    assert len(calls) == 1 and calls[0].url.path == "/3/authentication"
    assert "secret-api-key" not in result.text and "proxy-secret" not in result.text
    assert current()["tmdb_verified"] is (expected == "ok")
    if outcome == 200:
        store.set_setting("tmdb_proxy", "http://different-proxy:1234")
        assert not current()["tmdb_verified"]


def test_preconfigured_environment_can_validate_and_only_saved_config_is_used(monkeypatch):
    monkeypatch.setattr(config.settings, "tmdb_read_token", "environment-token")
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"success": True})
    original_client = tmdb._client
    # Capture the existing client's auth headers without making a real connection.
    c = original_client()
    assert c.headers["Authorization"] == "Bearer environment-token"
    c.close()
    monkeypatch.setattr(tmdb, "_client", lambda: httpx.Client(
        base_url=tmdb.API_BASE, transport=httpx.MockTransport(respond)))
    assert client.post("/api/tmdb/check").json()["ok"]
    assert len(seen) == 1


def test_write_auth_and_unknown_fields(monkeypatch):
    monkeypatch.setattr(config, "effective_jzmedia_token", lambda: "protected-token")
    assert client.get("/api/onboarding").status_code == 200
    assert patch(status="active").status_code == 401
    assert client.post("/api/tmdb/check").status_code == 401
    assert client.patch("/api/onboarding", headers={"X-Api-Token": "protected-token"},
                        json={"status": "active", "secret": "not allowed"}).status_code == 422
    assert client.patch("/api/onboarding", headers={"X-Api-Token": "protected-token"},
                        json={"step": None}).status_code == 422


def test_diagnostic_exception_cannot_leave_previous_success(monkeypatch):
    from app.routers import libraries
    choose()
    assert current()["library_verified"]
    def fail(*args):
        raise RuntimeError("diagnostic unavailable")
    monkeypatch.setattr(libraries.mounts, "check_library", fail)
    with pytest.raises(RuntimeError, match="diagnostic unavailable"):
        libraries.check_library(1)
    assert not current()["library_verified"]


def test_verified_readable_but_unwritable_library_cannot_upload(monkeypatch):
    from app.routers import libraries
    check = libraries.mounts.check_library
    def readonly(lib):
        return {**check(lib), "writable": False}
    monkeypatch.setattr(libraries.mounts, "check_library", readonly)
    choose()
    assert current()["library_verified"]
    assert not current()["upload_allowed"]
    movie()
    assert patch(status="completed").status_code == 200


def test_corrupt_progress_recovers_with_no_check_claims():
    store.set_setting("onboarding_state", "not-json")
    assert current()["status"] == "not_started"
    assert not current()["library_verified"]


@pytest.mark.parametrize("outcome", ["file", "missing", "offline"])
def test_unusable_target_directory_revokes_previous_check(monkeypatch, outcome):
    from types import SimpleNamespace
    from app.storage.base import StorageStat
    choose()
    def stat(path):
        if outcome == "offline":
            raise storage.StorageOffline("远程存储离线")
        if outcome == "missing":
            raise storage.StorageError("目录不存在")
        return StorageStat(size=1, mtime=0, is_dir=False)
    monkeypatch.setattr(storage, "backend_for", lambda lid: SimpleNamespace(stat=stat))
    result = client.post("/api/libraries/1/check")
    assert result.status_code == 200
    assert not result.json()["video"]["ok"]
    assert not current()["library_verified"]
    assert current()["step"] == 2
