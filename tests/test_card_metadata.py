"""Card provenance and ratings across browsing endpoints, with isolated libraries."""
import json

import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def catalogue(tmp_path):
    made = []
    actor_tid = 1920348011
    actor_id = store.upsert_person(actor_tid, "卡片演员")
    for index in range(2):
        root = tmp_path / str(index)
        root.mkdir()
        media = store.create_media_library(
            name=f"卡片媒体库 {index}", path=str(root), video_libraries=[
                {"name": "电影目录", "kind": "movie", "subpath": "movies"},
                {"name": "剧集目录", "kind": "tv", "subpath": "tv"},
            ])
        made.append({"media": media, "actor": actor_tid})
        libs = {row["kind"]: row["id"] for row in store.video_libraries_of(media["id"])}
        movie_id = store.upsert_movie_by_path("卡片电影.mkv", library_id=libs["movie"])
        store.update_movie_meta(movie_id, title="卡片电影", tmdb_id=1920348001,
                                tmdb_rating=8.3, genres=["剧情"], genre_ids=[18])
        store.link_person(movie_id, actor_id, "actor")
        store.resync_fts(movie_id)
        other = store.upsert_movie_by_path("卡片续集.mkv", library_id=libs["movie"])
        store.update_movie_meta(other, title="卡片续集", tmdb_rating=None,
                                genres=["剧情"], genre_ids=[18])
        store.link_person(other, actor_id, "actor")
        store.resync_fts(other)
        show_id = store.upsert_show(libs["tv"], "卡片剧集", 2026)
        store.update_show_meta(show_id, tmdb_rating=9.1, genres=["剧情"], networks=["演示台"])
        store.upsert_season(show_id, libs["tv"], 1,
                            cast_json=json.dumps([{"id": actor_tid, "name": "卡片演员"}]))
        other_show = store.upsert_show(libs["tv"], "卡片第二剧集", 2025)
        store.update_show_meta(other_show, genres=["剧情"], networks=["演示台"])
        episode = store.upsert_episode(show_id, libs["tv"], "卡片剧集/S01E01.mkv", 1, 1)
        store.update_episode_meta(episode, tmdb_rating=7.2)
        store.save_progress(movie_id, 600, 7200)
        store.save_progress(episode, 600, 2400, kind="episode")
        collection = store.create_collection("卡片合集", member_ids=[movie_id, other],
                                              media_library_id=media["id"])
        made[-1].update(movie=movie_id, other=other, show=show_id, episode=episode,
                        collection=collection["id"], libs=libs)
    yield made
    for item in made:
        store.delete_media_library(item["media"]["id"])
    with store._lock, store._conn() as connection:
        connection.execute("DELETE FROM persons WHERE id=?", (actor_id,))


def get(path, **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def assert_card(card, library, rating):
    assert card["media_library_id"] == library["media"]["id"]
    assert card["media_library_name"] == library["media"]["name"]
    assert card["media_library_name"] not in ("电影目录", "剧集目录")
    assert card["tmdb_rating"] == rating


@pytest.mark.parametrize("path,kind,rating,params", [
    ("/api/movies", "movie", 8.3, {}),
    ("/api/search", "movie", 8.3, {"q": "卡片电影"}),
    ("/api/movies/recent-played", "movie", 8.3, {}),
    ("/api/tv/shows", "show", 9.1, {}),
    ("/api/tv/recent-played", "show", 9.1, {}),
    ("/api/tv-client/search", "movie", 8.3, {"q": "卡片电影"}),
    ("/api/tv-client/search", "show", 9.1, {"q": "卡片剧集"}),
])
def test_all_library_cards_keep_distinct_provenance(catalogue, path, kind, rating, params):
    cards = {row["id"]: row for row in get(path, **params)["items"]}
    for library in catalogue:
        assert_card(cards[library[kind]], library, rating)
    # Names follow current configuration; no stale cache or per-card client request.
    renamed = catalogue[0]
    store.update_media_library(renamed["media"]["id"], name="媒体库改名后")
    renamed["media"]["name"] = "媒体库改名后"
    cards = {row["id"]: row for row in get(path, **params)["items"]}
    assert_card(cards[renamed[kind]], renamed, rating)


def test_collections_members_recommendations_and_actor_works(catalogue):
    for library in catalogue:
        scope = {"media_library": library["media"]["id"]}
        collection = get(f"/api/collections/{library['collection']}")
        assert collection["media_library_name"] == library["media"]["name"]
        assert "tmdb_rating" not in collection
        members = {row["id"]: row for row in collection["members"]}
        assert_card(members[library["movie"]], library, 8.3)
        assert_card(members[library["other"]], library, None)
        for path in ("/api/collections", "/api/tv-client/search"):
            collections = get(path, q="卡片合集", **scope)["items"]
            assert collections[0]["media_library_name"] == library["media"]["name"]
            assert "tmdb_rating" not in collections[0]
        related = get(f"/api/movies/{library['movie']}/similar")["items"]
        assert_card(next(row for row in related if row["id"] == library["other"]), library, None)
        related_shows = get(f"/api/tv/shows/{library['show']}/similar")["items"]
        assert related_shows
        for row in related_shows:
            source = next(x for x in catalogue if x["media"]["id"] == row["media_library_id"])
            assert row["media_library_name"] == source["media"]["name"]
        works = get("/api/tv-client/actor-works", actor=f"tmdb:{library['actor']}", **scope)["items"]
        cards = {(row["kind"], row["id"]): row for row in works}
        assert_card(cards["movie", library["movie"]], library, 8.3)
        assert_card(cards["show", library["show"]], library, 9.1)
        person = get(f"/api/persons/{library['actor']}", **scope)
        assert_card(next(row for row in person["acting"] if row["id"] == library["movie"]), library, 8.3)
        assert_card(next(row for row in person["tv_works"] if row["show_id"] == library["show"]), library, 9.1)
