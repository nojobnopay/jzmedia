"""Evidence-only backfill uses real routes and an isolated database."""
import json
import threading

import pytest

from app import store, viewing
from app.content_rules import normalize_nfo_evidence, normalize_tmdb_evidence
from app.jobkit import JobRegistry
from app.routers import viewing_refresh as refresh
from test_content_metadata import movie_detail
from test_viewing_catalog import PASSWORD, catalog as catalog_fixture, mode, request_ok

catalog = catalog_fixture


@pytest.fixture(autouse=True)
def isolated_refresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store._base.DB_PATH", str(tmp_path / "refresh.db"))
    store.init_db()
    monkeypatch.setattr(refresh, "_JOBS", JobRegistry(prefix="viewing-refresh-test"))
    finished = threading.Event()
    original = refresh._worker

    def worker(*args):
        try:
            original(*args)
        finally:
            finished.set()

    monkeypatch.setattr(refresh, "_worker", worker)
    yield finished
    # Every test joins its worker before restoring the database location.
    assert not any(not job.get("worker_finished", True) for job in refresh._JOBS.snapshot())


def unlock(catalog):
    request_ok(catalog["client"], "post", "/api/viewing/unlock", json={"password": PASSWORD})


def wait_job(catalog, started, finished):
    assert finished.wait(5), "evidence worker did not finish"
    return request_ok(catalog["client"], "get", f"/api/viewing/refresh/{started['job_id']}")


def test_refresh_deduplicates_and_changes_only_evidence(catalog, monkeypatch, isolated_refresh_db):
    tid = catalog["Mist_tid"]
    sibling = store.upsert_movie_by_path("same-movie.mkv", catalog["other"])
    store.update_movie_meta(sibling, tmdb_id=tid, title="Custom sibling title")
    with store._conn() as connection:
        connection.execute("UPDATE movies SET is_private=1,children_override='allow' WHERE id=?", (sibling,))
    store.upsert_tmdb_cache(tid, {"title": "Keep cached title", "overview": "Keep summary"},
                            {"cast": [{"id": 991, "name": "Keep actor"}]}, poster_tmdb_path="/keep.jpg")
    catalog["cache"].append((tid, "movie"))
    original_cache = store.get_tmdb_cached(tid)
    original = store.get_movie(sibling)
    calls = []

    def detail(value):
        assert viewing.current.get() is None
        calls.append(value)
        result = movie_detail(value)
        result["genres"] = [{"id": 28, "name": "Action"}]
        return result

    monkeypatch.setattr(refresh.tmdb, "movie_detail", detail)
    mode(catalog, "private")
    before_generation = request_ok(catalog["client"], "get", "/api/viewing/session")["generation"]
    started = request_ok(catalog["client"], "post", "/api/viewing/refresh", json={})
    job = wait_job(catalog, started, isolated_refresh_db)
    assert calls == [tid] and job["total"] == job["done"] == 1
    assert job["state"] == "done" and job["changed"] == 2 and not job["failed"]
    assert request_ok(catalog["client"], "get", "/api/viewing/session")["generation"] > before_generation
    updated = store.get_movie(sibling)
    assert updated["children_auto_status"] == "block"
    for key in ("title", "overview", "poster_path", "genres", "genre_ids", "is_private", "children_override", "updated_at"):
        assert updated[key] == original[key]
    cached = store.get_tmdb_cached(tid)
    assert {k: v for k, v in cached.items() if k != "payload_json"} == {
        k: v for k, v in original_cache.items() if k != "payload_json"}
    assert json.loads(cached["payload_json"])["content_evidence"]["genre_ids"] == [28]


def test_refresh_selects_library_but_propagates_same_identity(catalog, monkeypatch, isolated_refresh_db):
    tid = catalog["Mist_tid"]
    sibling = store.upsert_movie_by_path("sibling.mkv", catalog["other"])
    store.update_movie_meta(sibling, tmdb_id=tid)
    other = store.upsert_movie_by_path("outside.mkv", catalog["other"])
    store.update_movie_meta(other, tmdb_id=tid + 100000)
    calls = []
    monkeypatch.setattr(refresh.tmdb, "movie_detail", lambda value: calls.append(value) or movie_detail(value))
    unlock(catalog)
    started = request_ok(catalog["client"], "post", "/api/viewing/refresh", json={"library_id": catalog["movies"]})
    result = wait_job(catalog, started, isolated_refresh_db)
    assert calls == [tid] and result["changed"] == 2
    assert store.get_movie(sibling)["children_auto_status"] == "allow"
    assert store.get_movie(other)["children_auto_status"] == "unknown"


def test_refresh_tv_uses_show_identity_and_preserves_private_choice(catalog, monkeypatch, isolated_refresh_db):
    show_id = catalog["Archive"]
    store.update_show_meta(show_id, content_evidence={})
    tid = store.get_show_meta(show_id)["tmdb_id"]
    calls = []

    def detail(value):
        calls.append(value)
        return {"id": value, "adult": False, "genres": [],
                "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-Y"}]},
                "keywords": {"results": []}}

    monkeypatch.setattr(refresh.tmdb, "tv_detail", detail)
    unlock(catalog)
    started = request_ok(catalog["client"], "post", "/api/viewing/refresh", json={"library_id": catalog["tv"]})
    job = wait_job(catalog, started, isolated_refresh_db)
    assert calls == [tid] and job["changed"] == 1
    row = store.get_show_meta(show_id)
    assert row["children_auto_status"] == "allow" and row["is_private"] == 1
    assert store.get_tmdb_cached(tid, "tv") and not store.get_tmdb_cached(tid, "movie")


@pytest.mark.parametrize("failure", ["network", "partial", "identity"])
def test_refresh_failure_keeps_existing_evidence(catalog, monkeypatch, isolated_refresh_db, failure):
    tid = catalog["Mist_tid"]
    original = store.get_movie(catalog["Mist"])

    def detail(value):
        if failure == "network":
            raise RuntimeError("upstream token=must-not-appear")
        result = movie_detail(value + 1 if failure == "identity" else value)
        if failure == "partial":
            result.pop("keywords")
        return result

    monkeypatch.setattr(refresh.tmdb, "movie_detail", detail)
    unlock(catalog)
    started = request_ok(catalog["client"], "post", "/api/viewing/refresh", json={})
    job = wait_job(catalog, started, isolated_refresh_db)
    assert job["failed_count"] == 1 and job["changed"] == 0
    assert "must-not-appear" not in json.dumps(job)
    assert store.get_movie(catalog["Mist"])["content_evidence"] == original["content_evidence"]
    assert not store.get_tmdb_cached(tid)


def test_refresh_routes_require_management_and_cannot_run_in_children(catalog):
    client = catalog["client"]
    for method, path in (("post", "/api/viewing/refresh"), ("get", "/api/viewing/refresh/unknown"),
                         ("post", "/api/viewing/refresh/unknown/cancel")):
        assert getattr(client, method)(path).status_code == 403
    mode(catalog, "children")
    assert client.post("/api/viewing/refresh", json={}).status_code == 403


def test_refresh_cancel_is_single_flight_and_does_not_apply_pending_response(catalog, monkeypatch, isolated_refresh_db):
    entered = threading.Event()
    release = threading.Event()

    def detail(value):
        entered.set()
        assert release.wait(5)
        return movie_detail(value)

    monkeypatch.setattr(refresh.tmdb, "movie_detail", detail)
    unlock(catalog)
    client = catalog["client"]
    started = request_ok(client, "post", "/api/viewing/refresh", json={})
    try:
        assert entered.wait(3)
        same = request_ok(client, "post", "/api/viewing/refresh", json={})
        assert same["resumed"] and same["job_id"] == started["job_id"]
        assert client.post("/api/viewing/refresh", json={"library_id": catalog["movies"]}).status_code == 409
        cancelled = request_ok(client, "post", f"/api/viewing/refresh/{started['job_id']}/cancel")
        assert cancelled["state"] == "cancelled"
        assert client.post("/api/viewing/refresh", json={}).status_code == 409
    finally:
        release.set()
    job = wait_job(catalog, started, isolated_refresh_db)
    assert job["state"] == "cancelled" and job["done"] == job["changed"] == 0
    assert store.get_movie(catalog["Mist"])["children_auto_status"] == "unknown"


def test_refresh_empty_scope_finishes_without_network_and_rejects_bad_library(catalog, monkeypatch):
    def unexpected(*args):
        pytest.fail("empty scope must not contact TMDB")

    monkeypatch.setattr(refresh.tmdb, "movie_detail", unexpected)
    unlock(catalog)
    result = request_ok(catalog["client"], "post", "/api/viewing/refresh", json={"library_id": catalog["other"]})
    assert result["state"] == "done" and result["total"] == 0
    assert catalog["client"].post("/api/viewing/refresh", json={"library_id": 987654321}).status_code == 404


def test_rematch_during_fetch_is_not_overwritten(catalog, monkeypatch, isolated_refresh_db):
    old_tid = catalog["Mist_tid"]

    def detail(value):
        store.update_movie_meta(catalog["Mist"], tmdb_id=old_tid + 10000)
        return movie_detail(value)

    monkeypatch.setattr(refresh.tmdb, "movie_detail", detail)
    unlock(catalog)
    started = request_ok(catalog["client"], "post", "/api/viewing/refresh", json={})
    result = wait_job(catalog, started, isolated_refresh_db)
    assert result["changed"] == 0
    row = store.get_movie(catalog["Mist"])
    assert row["tmdb_id"] == old_tid + 10000 and row["children_auto_status"] == "unknown"


def test_evidence_backfill_cannot_erase_episode_risk(catalog):
    sid = catalog["Meadow"]
    tid = store.get_show_meta(sid)["tmdb_id"]
    store.update_episode_meta(catalog["Meadow_episode"], content_evidence=normalize_nfo_evidence("US:TV-MA"))
    evidence = normalize_tmdb_evidence("tv", {
        "id": tid, "adult": False, "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-G"}]},
        "keywords": {"results": []}})
    assert refresh._save_evidence("tv", tid, evidence) == 1
    assert store.get_show_meta(sid)["children_auto_status"] == "block"
