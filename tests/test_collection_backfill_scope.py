"""Collection enrichment selects and resumes only the requested media scope."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import library_paths, store
from app.routers import collections


@pytest.fixture()
def libraries(tmp_path):
    result = []
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        media = store.create_media_library(
            name=f"backfill-{name}-{tmp_path.name}", path=str(root), video_libraries=[
                {"name": "Movies 1", "subpath": "one", "kind": "movie"},
                {"name": "Movies 2", "subpath": "two", "kind": "movie"},
            ])
        result.append((media, store.video_libraries_of(media["id"])))
    yield result
    for media, _ in result:
        store.delete_media_library(media["id"])
    library_paths.invalidate_cache()


@pytest.fixture()
def jobs(monkeypatch):
    with collections._JOBS_LOCK:
        saved = dict(collections._JOBS)
        collections._JOBS.clear()
    workers = []

    def thread(*, target, daemon):
        assert daemon
        workers.append(target)
        return SimpleNamespace(start=lambda: None)

    monkeypatch.setattr(collections, "threading", SimpleNamespace(Thread=thread))
    yield workers
    with collections._JOBS_LOCK:
        collections._JOBS.clear()
        collections._JOBS.update(saved)


def add_movie(library, tid, title):
    movie_id = store.upsert_movie_by_path(f"{title}.mkv", library_id=library["id"])
    store.update_movie_meta(movie_id, tmdb_id=tid, title=title)
    return movie_id


def seed(libraries):
    (media_a, libs_a), (media_b, libs_b) = libraries
    add_movie(libs_a[0], 987_651_001, "Scoped A1")
    add_movie(libs_a[1], 987_651_002, "Scoped A2")
    add_movie(libs_a[1], 987_651_001, "Scoped A1 version")
    add_movie(libs_b[0], 987_651_003, "Outside B")
    return media_a, media_b


def test_store_collection_backfill_filters_before_limit_and_keeps_force_semantics(libraries):
    media_a, media_b = seed(libraries)
    assert store.tmdb_ids_missing_collection(200, True, media_a["id"]) == [987_651_001, 987_651_002]
    assert store.tmdb_ids_missing_collection(1, True, media_b["id"]) == [987_651_003]
    assert store.tmdb_ids_missing_collection(200, True, 999_999_999) == []
    store.upsert_tmdb_cache(987_651_001, {"title": "Checked standalone", "media_type": "movie"})
    try:
        assert store.tmdb_ids_missing_collection(200, False, media_a["id"]) == [987_651_002]
        assert store.tmdb_ids_missing_collection(200, True, media_a["id"]) == [987_651_001, 987_651_002]
    finally:
        with store._lock, store._conn() as connection:
            connection.execute("DELETE FROM tmdb_cache WHERE tmdb_id=? AND media_type='movie'", (987_651_001,))


@pytest.mark.parametrize("scope_key", ["media_library", "media_library_id", "library_id"])
def test_backfill_worker_and_status_keep_requested_media_scope(libraries, jobs, monkeypatch, scope_key):
    media_a, _ = seed(libraries)
    scope_id = libraries[0][1][0]["id"] if scope_key == "library_id" else media_a["id"]
    refreshed = []

    def refresh(tid):
        status = collections.suggest_backfill_status()
        assert status["media_library_id"] == media_a["id"]
        assert status["force"] is True
        assert status["current_title"].startswith("Scoped A")
        refreshed.append(tid)
        return {"changed": False}, []

    monkeypatch.setattr(collections.scanner, "refresh_tmdb_id_fast", refresh)
    start = collections.suggest_backfill({scope_key: scope_id, "force": True, "limit": 25})
    assert start["media_library_id"] == media_a["id"] and start["force"] is True
    assert start["total"] == 2 and not start["resumed"]
    worker, = jobs
    worker()
    status = collections.suggest_backfill_status(start["job_id"])
    assert status["state"] == "done" and status["done"] == status["total"] == 2
    assert status["media_library_id"] == media_a["id"] and status["force"] is True
    assert status["failed"] == [] and refreshed == [987_651_001, 987_651_002]


def test_backfill_only_resumes_identical_scope_and_parameters(libraries, jobs):
    media_a, media_b = seed(libraries)
    body = {"media_library": media_a["id"], "limit": 10, "force": True}
    start = collections.suggest_backfill(body)
    resumed = collections.suggest_backfill(body)
    assert resumed["resumed"] and resumed["job_id"] == start["job_id"]
    assert resumed["media_library_id"] == media_a["id"] and resumed["force"] is True
    for conflict in ({**body, "media_library": media_b["id"]},
                     {**body, "force": False}, {**body, "limit": 11}, {}):
        with pytest.raises(HTTPException) as error:
            collections.suggest_backfill(conflict)
        assert error.value.status_code == 409
    assert len(jobs) == 1


@pytest.mark.parametrize("scope", [{"media_library": 999_999_999}, {"library_id": 999_999_999}])
def test_missing_backfill_scope_does_not_expand_to_all_libraries(libraries, jobs, scope):
    seed(libraries)
    result = collections.suggest_backfill({**scope, "force": True})
    assert result["total"] == 0 and result["job_id"] == "" and result["force"] is True
    assert result["media_library_id"] is not None
    assert jobs == []


def test_unscoped_backfill_explicitly_reports_all_libraries(libraries, jobs):
    seed(libraries)
    result = collections.suggest_backfill({"force": True, "limit": 200})
    assert result["media_library_id"] is None and result["force"] is True
    assert result["total"] >= 3
    status = collections.suggest_backfill_status(result["job_id"])
    assert status["media_library_id"] is None and status["force"] is True


def test_expired_job_id_cannot_cancel_a_new_task_in_another_library(libraries, jobs):
    media_a, media_b = seed(libraries)
    first = collections.suggest_backfill({"media_library": media_a["id"], "force": True})
    assert collections.suggest_backfill_cancel({"job_id": first["job_id"]})["state"] == "cancelled"
    second = collections.suggest_backfill({"media_library": media_b["id"], "force": True})
    with collections._JOBS_LOCK:
        collections._JOBS.pop(first["job_id"])
    with pytest.raises(HTTPException) as status_error:
        collections.suggest_backfill_status(first["job_id"])
    assert status_error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        collections.suggest_backfill_cancel({"job_id": first["job_id"]})
    assert error.value.status_code == 404
    assert collections.suggest_backfill_status(second["job_id"])["state"] == "running"
    assert collections.suggest_backfill_cancel({"job_id": second["job_id"]})["state"] == "cancelled"


def test_omitted_cancel_job_id_preserves_latest_task_compatibility(libraries, jobs):
    media_a, _ = seed(libraries)
    first = collections.suggest_backfill({"media_library": media_a["id"], "force": True})
    assert collections.suggest_backfill_cancel()["job_id"] == first["job_id"]
    assert collections.suggest_backfill_status(first["job_id"])["state"] == "cancelled"


def test_same_tmdb_versions_keep_shared_refresh_and_manual_fields(libraries, monkeypatch):
    from app.scanner import persist

    (_, libs_a), (_, libs_b) = libraries
    tid = 987_651_004
    scoped_id = add_movie(libs_a[0], tid, "Scoped manual title")
    outside_id = add_movie(libs_b[0], tid, "Outside manual title")
    store.update_movie_meta(outside_id, overview="Outside existing overview", tags=["manual"])
    detail = {"id": tid, "title": "Refreshed upstream title", "original_title": "Upstream",
              "release_date": "2020-01-01", "overview": "Refreshed overview", "genres": [],
              "credits": {"cast": [], "crew": []}, "poster_path": ""}
    monkeypatch.setattr(persist.tmdb, "movie_detail", lambda requested: detail)
    changed, media_jobs = persist.refresh_tmdb_id_fast(tid)
    assert set(changed["affected_ids"]) == {scoped_id, outside_id}
    assert {job["mid"] for job in media_jobs} == {scoped_id, outside_id}
    for movie_id in (scoped_id, outside_id):
        assert store.get_movie(movie_id)["overview"] == "Refreshed overview"
    assert store.get_movie(scoped_id)["title"] == "Scoped manual title"
    assert store.get_movie(outside_id)["title"] == "Outside manual title"
    assert store.get_movie(outside_id)["tags"] == ["manual"]
    assert store.get_tmdb_cached(tid)["overview"] == "Refreshed overview"
    unchanged, forced_jobs = persist.refresh_tmdb_id_fast(tid, force=True)
    assert unchanged["changed"] is False
    assert {job["mid"] for job in forced_jobs} == {scoped_id, outside_id}
