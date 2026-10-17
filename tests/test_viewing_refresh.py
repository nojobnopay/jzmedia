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


def saved_row(table, item_id):
    with store._conn() as connection:
        return dict(connection.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone())


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


def test_refresh_updates_same_library_versions_without_touching_other_libraries(catalog, monkeypatch, isolated_refresh_db):
    tid = catalog["Mist_tid"]
    sibling = store.upsert_movie_by_path("sibling.mkv", catalog["movies"])
    store.update_movie_meta(sibling, tmdb_id=tid)
    outside = store.upsert_movie_by_path("same-identity.mkv", catalog["other"])
    store.update_movie_meta(outside, tmdb_id=tid)
    other = store.upsert_movie_by_path("outside.mkv", catalog["other"])
    store.update_movie_meta(other, tmdb_id=tid + 100000)
    outside_before = saved_row("movies", outside)
    other_before = saved_row("movies", other)
    calls = []
    synced = []
    original_resync = store.resync_fts

    def resync(movie_id):
        synced.append(movie_id)
        original_resync(movie_id)

    monkeypatch.setattr(refresh.tmdb, "movie_detail", lambda value: calls.append(value) or movie_detail(value))
    monkeypatch.setattr(refresh.store, "resync_fts", resync)
    unlock(catalog)
    started = request_ok(catalog["client"], "post", "/api/viewing/refresh", json={"library_id": catalog["movies"]})
    result = wait_job(catalog, started, isolated_refresh_db)
    assert calls == [tid] and result["total"] == result["done"] == 1 and result["changed"] == 2
    assert result["library_id"] == catalog["movies"]
    assert set(synced) == {catalog["Mist"], sibling} and len(synced) == 2
    assert store.get_movie(catalog["Mist"])["children_auto_status"] == "allow"
    assert store.get_movie(sibling)["children_auto_status"] == "allow"
    assert saved_row("movies", outside) == outside_before
    assert saved_row("movies", other) == other_before
    assert json.loads(store.get_tmdb_cached(tid)["payload_json"])["content_evidence"]["certifications"]


def test_refresh_tv_updates_selected_show_and_episodes_only(catalog, monkeypatch, isolated_refresh_db):
    show_id = catalog["Archive"]
    episode_id = catalog["Archive_episode"]
    store.update_show_meta(show_id, content_evidence={})
    tid = store.get_show_meta(show_id)["tmdb_id"]
    episode_evidence = normalize_nfo_evidence("US:TV-Y7", ["cartoon violence"])
    store.update_episode_meta(episode_id, content_evidence=episode_evidence)
    outside_library = store.create_video_library(catalog["media"], name="other-tv", kind="tv", subpath="other-tv")["id"]
    outside_show = store.upsert_show(outside_library, "Same show")
    store.update_show_meta(outside_show, tmdb_id=tid)
    outside_episode = store.upsert_episode(outside_show, outside_library, "outside.mkv", 1, 1)
    store.update_episode_meta(outside_episode, content_evidence=episode_evidence)
    outside_before = saved_row("tv_shows", outside_show)
    outside_episode_before = saved_row("tv_episodes", outside_episode)
    episode_before = saved_row("tv_episodes", episode_id)
    assert store.get_show_meta(show_id)["children_auto_status"] == "unknown"
    assert episode_before["children_auto_status"] == "unknown"
    calls = []

    def detail(value):
        calls.append(value)
        return {"id": value, "adult": False, "genres": [{"id": 16}, {"id": 10762}],
                "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-Y"}]},
                "keywords": {"results": []}}

    monkeypatch.setattr(refresh.tmdb, "tv_detail", detail)
    unlock(catalog)
    started = request_ok(catalog["client"], "post", "/api/viewing/refresh", json={"library_id": catalog["tv"]})
    job = wait_job(catalog, started, isolated_refresh_db)
    assert calls == [tid] and job["changed"] == 1
    row = store.get_show_meta(show_id)
    assert row["children_auto_status"] == "allow" and row["is_private"] == 1
    episode = saved_row("tv_episodes", episode_id)
    assert episode["children_auto_status"] == "allow"
    for key in ("content_evidence", "title", "updated_at"):
        assert episode[key] == episode_before[key]
    assert saved_row("tv_shows", outside_show) == outside_before
    assert saved_row("tv_episodes", outside_episode) == outside_episode_before
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


@pytest.mark.parametrize("scoped", [False, True])
def test_refresh_cancel_is_single_flight_and_does_not_apply_pending_response(catalog, monkeypatch, isolated_refresh_db,
                                                                         scoped):
    entered = threading.Event()
    release = threading.Event()
    body = {"library_id": catalog["movies"]} if scoped else {}
    different_scope = {} if scoped else {"library_id": catalog["movies"]}
    outside = store.upsert_movie_by_path("cancelled-outside.mkv", catalog["other"])
    store.update_movie_meta(outside, tmdb_id=catalog["Mist_tid"])
    outside_before = saved_row("movies", outside)

    def detail(value):
        entered.set()
        assert release.wait(5)
        return movie_detail(value)

    monkeypatch.setattr(refresh.tmdb, "movie_detail", detail)
    unlock(catalog)
    client = catalog["client"]
    started = request_ok(client, "post", "/api/viewing/refresh", json=body)
    try:
        assert entered.wait(3)
        same = request_ok(client, "post", "/api/viewing/refresh", json=body)
        assert same["resumed"] and same["job_id"] == started["job_id"]
        assert same["library_id"] == body.get("library_id")
        assert client.post("/api/viewing/refresh", json=different_scope).status_code == 409
        cancelled = request_ok(client, "post", f"/api/viewing/refresh/{started['job_id']}/cancel")
        assert cancelled["state"] == "cancelled"
        assert client.post("/api/viewing/refresh", json=body).status_code == 409
    finally:
        release.set()
    job = wait_job(catalog, started, isolated_refresh_db)
    assert job["state"] == "cancelled" and job["done"] == job["changed"] == 0
    assert job["library_id"] == body.get("library_id")
    assert store.get_movie(catalog["Mist"])["children_auto_status"] == "unknown"
    assert saved_row("movies", outside) == outside_before
    assert not store.get_tmdb_cached(catalog["Mist_tid"])


def test_refresh_empty_scope_finishes_without_network_and_rejects_bad_library(catalog, monkeypatch):
    def unexpected(*args):
        pytest.fail("empty scope must not contact TMDB")

    monkeypatch.setattr(refresh.tmdb, "movie_detail", unexpected)
    unlock(catalog)
    result = request_ok(catalog["client"], "post", "/api/viewing/refresh", json={"library_id": catalog["other"]})
    assert result["state"] == "done" and result["total"] == 0
    assert catalog["client"].post("/api/viewing/refresh", json={"library_id": 987654321}).status_code == 404


@pytest.mark.parametrize("change", ["rematch", "remove"])
def test_scoped_item_changed_during_fetch_cannot_update_other_library(catalog, monkeypatch, isolated_refresh_db, change):
    old_tid = catalog["Mist_tid"]
    outside = store.upsert_movie_by_path("same-identity.mkv", catalog["other"])
    store.update_movie_meta(outside, tmdb_id=old_tid)
    outside_before = saved_row("movies", outside)
    store.upsert_tmdb_cache(old_tid, {"title": "Keep shared cache"})
    catalog["cache"].append((old_tid, "movie"))
    cache_before = store.get_tmdb_cached(old_tid)

    def detail(value):
        if change == "rematch":
            store.update_movie_meta(catalog["Mist"], tmdb_id=old_tid + 10000)
        else:
            store.delete_movie(catalog["Mist"])
        return movie_detail(value)

    monkeypatch.setattr(refresh.tmdb, "movie_detail", detail)
    unlock(catalog)
    started = request_ok(catalog["client"], "post", "/api/viewing/refresh", json={"library_id": catalog["movies"]})
    result = wait_job(catalog, started, isolated_refresh_db)
    assert result["total"] == result["done"] == 1 and result["changed"] == 0
    assert not result["failed"]
    assert saved_row("movies", outside) == outside_before
    assert store.get_tmdb_cached(old_tid) == cache_before
    row = store.get_movie(catalog["Mist"])
    if change == "rematch":
        assert row["tmdb_id"] == old_tid + 10000 and row["children_auto_status"] == "unknown"
    else:
        assert row is None


def test_evidence_backfill_cannot_erase_episode_risk(catalog):
    sid = catalog["Meadow"]
    tid = store.get_show_meta(sid)["tmdb_id"]
    store.update_episode_meta(catalog["Meadow_episode"], content_evidence=normalize_nfo_evidence("US:TV-MA"))
    evidence = normalize_tmdb_evidence("tv", {
        "id": tid, "adult": False, "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-G"}]},
        "keywords": {"results": []}})
    assert refresh._save_evidence("tv", tid, evidence) == 1
    assert store.get_show_meta(sid)["children_auto_status"] == "block"
