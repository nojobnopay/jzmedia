"""Multi-episode display names stay consistent without rewriting source metadata."""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, storage, store
from app.main import app
from app.routers.stream import media as stream_media

client = TestClient(app)


@pytest.fixture()
def series(tmp_path):
    root = tmp_path / "range-titles"
    root.mkdir()
    library = store.create_library(name=f"range-titles-{tmp_path.name}",
                                   kind="tv", path=str(root))
    library_paths.invalidate_cache()
    show_id = store.upsert_show(library["id"], "合成剧集", 2026)
    episodes = []
    for first in (1, 3, 5):
        episodes.append(store.upsert_episode(
            show_id, library["id"],
            f"合成剧集/Season 01/合成剧集-S01E{first:02d}-E{first + 1:02d}.mkv",
            1, first, f"第 {first} 集", episode_end=first + 1))
    yield {"library": library, "show_id": show_id, "episodes": episodes}
    store.delete_media_library(library["media_library_id"])
    library_paths.invalidate_cache()


def _get(path, **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_multi_episode_titles_consistent_across_browsing_and_navigation(series, monkeypatch):
    show_id = series["show_id"]
    first, middle, last = series["episodes"]
    store.save_progress(first, 600, 5400, kind="episode")
    original = {eid: store.get_episode(eid) for eid in series["episodes"]}

    def unexpected_storage(*args, **kwargs):
        pytest.fail("Displaying episode titles must not inspect media storage")

    monkeypatch.setattr(storage, "backend_for", unexpected_storage)
    show = _get(f"/api/tv/shows/{show_id}", include_episodes=1)
    season = _get(f"/api/tv/shows/{show_id}/seasons/1")
    detail = _get(f"/api/tv/episodes/{middle}")
    following = _get(f"/api/tv/episodes/{first}/next")["next"]

    for payload in (show, season):
        assert [row["display_title"] for row in payload["episodes"]] == [
            "第1-2集", "第3-4集", "第5-6集"]
        assert [row["title"] for row in payload["episodes"]] == [
            "第 1 集", "第 3 集", "第 5 集"]
        assert payload["next_episode"]["display_title"] == "第1-2集"
    assert season["episodes"][0]["progress"]["position"] == 600
    assert season["episode_count"] == 3
    assert season["distinct_count"] == 6
    assert detail["display_title"] == "第3-4集"
    assert detail["previous_episode"]["id"] == first
    assert detail["previous_episode"]["display_title"] == "第1-2集"
    assert detail["next_episode"]["id"] == last
    assert detail["next_episode"]["display_title"] == "第5-6集"
    assert (following["id"], following["episode"], following["episode_end"]) == (middle, 3, 4)
    assert following["title"] == "第 3 集"
    assert following["display_title"] == "第3-4集"
    assert {eid: store.get_episode(eid) for eid in series["episodes"]} == original


@pytest.mark.parametrize("title,expected", [
    ("第 1 集", "第1-2集"),
    ("", "第1-2集"),
    ("突围行动", "突围行动"),
])
def test_continue_watching_preserves_original_title_and_uses_range_display(series, title, expected):
    episode_id = series["episodes"][0]
    store.update_episode_meta(episode_id, title=title)
    progress = store.save_progress(episode_id, 600, 5400, kind="episode")
    item, = _get("/api/tv/recent-played", library=series["library"]["id"])["items"]

    assert item["title"] == "合成剧集"
    assert item["episode_title"] == title
    assert item["episode_display_title"] == expected
    assert item["subtitle"] == f"S01E01-E02 · {expected}"
    assert item["progress"]["version_id"] == episode_id
    assert item["progress"]["position"] == 600
    assert item["progress"]["duration"] == 5400
    assert store.get_episode(episode_id)["title"] == title
    assert store.get_progress(episode_id, kind="episode") == progress


def test_single_episode_and_named_multi_episode_titles_are_preserved(series):
    first, middle, _ = series["episodes"]
    store.update_episode_meta(first, episode_end=0)
    store.update_episode_meta(middle, title="突围行动")

    single = _get(f"/api/tv/episodes/{first}")
    named = _get(f"/api/tv/episodes/{middle}")
    assert single["title"] == single["display_title"] == "第 1 集"
    assert named["title"] == named["display_title"] == "突围行动"
    assert named["episode_end"] == 4


def test_episode_versions_expose_display_names_without_changing_titles(series, monkeypatch):
    first = series["episodes"][0]
    second_version = store.upsert_episode(
        series["show_id"], series["library"]["id"],
        "合成剧集/Season 01/合成剧集-V2-S01E01-E02.mkv",
        1, 1, "突围行动", episode_end=2)
    monkeypatch.setattr(stream_media, "_source_or_none", lambda row: None)

    payload = _get("/api/stream/versions", movie_id=first, kind="episode")
    versions = {row["version_id"]: row for row in payload["versions"]}
    assert set(versions) == {first, second_version}
    assert versions[first]["title"] == "第 1 集"
    assert versions[first]["display_title"] == "第1-2集"
    assert versions[second_version]["title"] == "突围行动"
    assert versions[second_version]["display_title"] == "突围行动"
    assert versions[first]["version"] == 1
    assert versions[second_version]["version"] == 2
    assert all(row["show_title"] == "合成剧集" for row in versions.values())
    assert all(row["episode_end"] == 2 for row in versions.values())
    assert all(row["playable"] is False for row in versions.values())
