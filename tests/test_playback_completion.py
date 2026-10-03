"""Completion boundaries stay consistent across recents, series selection and clients."""
import math

import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app
from app.playback_completion import is_playback_complete
from app.routers import tv
from app.store import _base

client = TestClient(app)


@pytest.mark.parametrize("position,duration,expected", [
    (0, 180, False), (10, 180, False), (143.9, 180, False), (144, 180, True),
    (800, 1200, False), (960, 1200, True), (4800, 6000, False),
    (5699, 6000, False), (5700, 6000, True), (180, 180, True), (181, 180, True),
    (-1, 180, False), (150, -1, False), (150, 0, False), (None, 180, False),
    (150, None, False), (math.nan, 180, False), (150, math.nan, False),
    (math.inf, 180, False), (150, math.inf, False), (-math.inf, 180, False),
])
def test_completion_boundaries(position, duration, expected):
    assert is_playback_complete(position, duration) is expected


@pytest.fixture()
def completion_library(tmp_path):
    root = tmp_path / "completion"
    root.mkdir()
    lib = store.create_library(name=f"completion-{tmp_path.name}", kind="tv", path=str(root))
    library_paths.invalidate_cache()
    yield lib
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _episodes(lib):
    show = store.upsert_show(lib["id"], "短片完成阈值", 2026)
    first = store.upsert_episode(show, lib["id"], "Show/S01E01.mkv", 1, 1, "第一集")
    second = store.upsert_episode(show, lib["id"], "Show/S01E02.mkv", 1, 2, "第二集")
    return show, first, second


def test_short_episode_stays_in_recents_and_next_until_completion(completion_library):
    lib = completion_library
    show, first, second = _episodes(lib)
    store.save_progress(first, 60, 180, kind="episode")
    assert store.next_episode(show)["id"] == first
    assert store.season_next_episode(show, 1)["id"] == first
    stats = store.show_season_stats(show)[0]
    assert stats["has_partial"] is True and stats["next_episode"] == 1
    rows = client.get("/api/tv/recent-played", params={"library": lib["id"]}).json()["items"]
    # Cards identify the show; the resumable episode is progress.version_id.
    assert [row["id"] for row in rows] == [show]
    assert first in [row["progress"]["version_id"] for row in rows]

    store.save_progress(first, 144, 180, kind="episode")
    assert store.next_episode(show)["id"] == second
    assert store.season_next_episode(show, 1)["id"] == second
    stats = store.show_season_stats(show)[0]
    assert stats["has_partial"] is False and stats["next_episode"] == 2
    rows = client.get("/api/tv/recent-played", params={"library": lib["id"]}).json()["items"]
    assert first not in [row["progress"]["version_id"] for row in rows]


def test_short_movie_recents_use_the_same_rule(completion_library):
    # The storage query operates on rows; no media file or probe is needed.
    lib = completion_library
    movie = store.upsert_movie_by_path("short.mkv", library_id=lib["id"])
    store.save_progress(movie, 60, 180)
    assert movie in [row["id"] for row in store.list_recent_played(library_ids=[lib["id"]])]
    store.save_progress(movie, 144, 180)
    assert movie not in [row["id"] for row in store.list_recent_played(library_ids=[lib["id"]])]
    assert movie in [row["id"] for row in store.list_recent_played(library_ids=[lib["id"]], include_finished=True)]


def test_invalid_stored_progress_cannot_poison_completion(completion_library):
    movie = store.upsert_movie_by_path("invalid.mkv", library_id=completion_library["id"])
    progress = store.save_progress(movie, math.inf, math.inf)
    assert progress["position"] == progress["duration"] == 0
    # Legacy invalid rows remain unfinished; the SQL callback shares the finite-value guard.
    with _base._lock, _base._conn() as conn:
        conn.execute("UPDATE playback_progress SET position=30, duration=? WHERE kind='movie' AND item_id=?", (math.inf, movie))
    assert movie in [row["id"] for row in store.list_recent_played(library_ids=[completion_library["id"]])]


def test_next_payload_reports_known_offline_without_probing_or_skipping(completion_library, monkeypatch):
    _, first, second = _episodes(completion_library)

    def no_probe(*args, **kwargs):
        pytest.fail("next metadata must remain local")

    monkeypatch.setattr(tv, "_verify_exists_map", no_probe)
    monkeypatch.setattr(tv, "_backend_for", no_probe)
    assert client.get(f"/api/tv/episodes/{first}/next").json()["next"]["exists"] is True
    with _base._lock, _base._conn() as conn:
        conn.execute("UPDATE tv_episodes SET missing=1 WHERE id=?", (second,))
    next_item = client.get(f"/api/tv/episodes/{first}/next").json()["next"]
    assert next_item["id"] == second
    assert next_item["exists"] is False and next_item["missing"] == 1
