"""TV title search uses the complete current database, without changing web FTS."""
import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app
from app.store.tv_search import _title_keys, search_tv_titles

client = TestClient(app)


@pytest.fixture()
def catalogue(tmp_path):
    root = tmp_path / "tv-search"
    root.mkdir()
    media = store.create_media_library(
        name=f"tv-search-{tmp_path.name}", path=str(root),
        video_libraries=[{"name": "电影", "kind": "movie", "subpath": "movies"},
                         {"name": "剧集", "kind": "tv", "subpath": "shows"},
                         {"name": "备用", "kind": "movie", "subpath": "other"}])
    libraries = {row["name"]: row["id"] for row in store.video_libraries_of(media["id"])}
    yield {"media": media["id"], **libraries}
    store.delete_media_library(media["id"])


def movie(catalogue, title, *, original="", tid=None, library="电影", **meta):
    item = store.upsert_movie_by_path(
        f"{title}-{tid or 'none'}-{original}.mkv", library_id=catalogue[library])
    store.update_movie_meta(item, title=title, original_title=original,
                            tmdb_id=tid, **meta)
    return item


def find(catalogue, query, **kwargs):
    response = client.get("/api/tv-client/search", params={
        "q": query, "media_library": catalogue["media"], **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("query", ["SQ", "sq", "ＳＱ", "s q", "sha qiu", "沙丘"])
def test_chinese_initials_full_pinyin_case_width_and_original(catalogue, query):
    identifier = movie(catalogue, "沙丘", original="Dune", year=2021,
                       poster_path="posters/movies/438631.jpg")
    result = find(catalogue, query)
    assert result["total"] == 1
    assert result["items"][0] == {
        "id": identifier, "kind": "movie", "title": "沙丘", "original_title": "Dune",
        "year": 2021, "poster_path": "posters/movies/438631.jpg",
        "library_id": catalogue["电影"], "media_library_id": catalogue["media"],
        "media_library_name": store.get_media_library(catalogue["media"])["name"],
        "tmdb_rating": None,
        "version_count": 1}
    assert find(catalogue, "DUNE")["items"][0]["id"] == identifier


def test_numeric_and_phrase_pronunciations(catalogue):
    earth = movie(catalogue, "流浪地球 2")
    forest = movie(catalogue, "重庆森林")
    assert find(catalogue, "LLDQ2")["items"][0]["id"] == earth
    assert find(catalogue, "CQSL")["items"][0]["id"] == forest
    assert find(catalogue, "chongqing")["items"][0]["id"] == forest


def test_movie_versions_group_with_stable_matching_representative(catalogue):
    first = movie(catalogue, "沙丘", tid=438631)
    second = movie(catalogue, "沙丘 原画", tid=438631)
    other_library = movie(catalogue, "沙丘", tid=438631, library="备用")
    result = find(catalogue, "SQ", kind="movie")
    assert result["total"] == 2
    assert {row["id"] for row in result["items"]} == {first, other_library}
    assert next(row for row in result["items"] if row["id"] == first)["version_count"] == 2
    assert find(catalogue, "SQYH")["items"][0]["id"] == second


def test_exact_original_title_precedes_initial_match(catalogue):
    movie(catalogue, "沙丘")
    exact = movie(catalogue, "编码故事", original="SQ")
    assert find(catalogue, "SQ")["items"][0]["id"] == exact


def test_show_collection_and_kinds_keep_routes_distinct(catalogue):
    feature = movie(catalogue, "蜡笔小新", poster_path="posters/movies/shin.jpg")
    show = store.upsert_show(catalogue["剧集"], "蜡笔小新", 1992)
    store.update_show_meta(show, poster_path="tv/shin.jpg")
    collection = store.create_collection("蜡笔小新 合集", member_ids=[feature],
                                          media_library_id=catalogue["media"])
    all_results = find(catalogue, "LBXX")
    assert {(r["kind"], r["id"]) for r in all_results["items"]} == {
        ("movie", feature), ("show", show), ("collection", collection["id"])}
    item = find(catalogue, "LBXX", kind="collection")["items"][0]
    assert item["title"] == item["name"] == "蜡笔小新 合集"
    assert item["poster_path"] == item["cover"] == "posters/movies/shin.jpg"
    assert item["member_count"] == 1
    assert find(catalogue, "LBXX", kind="show")["items"][0]["poster_path"] == "tv/shin.jpg"


def test_unknown_media_is_empty_and_disabled_libraries_are_excluded(catalogue):
    movie(catalogue, "沙丘")
    movie(catalogue, "沙丘 后传", library="备用")
    store.update_library(catalogue["备用"], enabled=False)
    assert find(catalogue, "SQ")["total"] == 1
    assert client.get("/api/tv-client/search", params={
        "q": "SQ", "media_library": 2 ** 31 - 1}).json()["total"] == 0
    store.update_media_library(catalogue["media"], enabled=False)
    assert find(catalogue, "SQ")["total"] == 0
    assert not any(row["media_library_id"] == catalogue["media"]
                   for row in client.get("/api/tv-client/search", params={"q": "SQ"}).json()["items"])


def test_title_cache_does_not_cache_rows_or_visibility(catalogue):
    identifier = movie(catalogue, "沙丘")
    assert find(catalogue, "SQ")["total"] == 1
    store.update_movie_meta(identifier, title="红辣椒")
    assert find(catalogue, "SQ")["total"] == 0
    assert find(catalogue, "HLJ")["total"] == 1
    store.delete_movie(identifier)
    assert find(catalogue, "HLJ")["total"] == 0
    assert _title_keys.cache_info().maxsize == 32768


def test_existing_media_scope_and_collection_cover_do_not_cross_libraries(catalogue, tmp_path):
    root = tmp_path / "second-media"
    root.mkdir()
    other = store.create_library(name=f"other-{tmp_path.name}", path=str(root))
    try:
        remote_id = store.upsert_movie_by_path("other.mkv", library_id=other["id"])
        store.update_movie_meta(remote_id, title="沙丘", tmdb_id=438631,
                                poster_path="posters/movies/wrong.jpg", year=1984)
        local_id = movie(catalogue, "沙丘", tid=438631, year=2021,
                         poster_path="posters/movies/right.jpg")
        collection = store.create_collection("沙丘 合集", member_ids=[local_id],
                                              media_library_id=catalogue["media"])
        result = find(catalogue, "SQ")
        assert result["total"] == 2
        assert {r["media_library_id"] for r in result["items"]} == {catalogue["media"]}
        assert find(catalogue, "SQ", kind="collection")["items"][0]["poster_path"] == "posters/movies/right.jpg"
        other_results = client.get("/api/tv-client/search", params={
            "q": "SQ", "media_library": other["media_library_id"]}).json()
        assert [r["id"] for r in other_results["items"]] == [remote_id]
        store.update_library(catalogue["电影"], enabled=False)
        item = find(catalogue, "SQ", kind="collection")["items"][0]
        assert item["id"] == collection["id"]
        assert item["poster_path"] == ""
    finally:
        store.delete_media_library(other["media_library_id"])


def test_search_is_not_limited_to_first_browse_page_and_paginates_stably(catalogue):
    for number in range(65):
        movie(catalogue, f"太空故事 {number:02d}")
    target = movie(catalogue, "最后的沙丘")
    assert find(catalogue, "SQ")["items"][0]["id"] == target
    pages = [find(catalogue, "TKGS", kind="movie", limit=24, offset=n)
             for n in (0, 24, 48, 72)]
    assert [page["total"] for page in pages] == [65] * 4
    assert [len(page["items"]) for page in pages] == [24, 24, 17, 0]
    assert [page["has_more"] for page in pages] == [True, True, False, False]
    ids = [row["id"] for page in pages for row in page["items"]]
    assert len(set(ids)) == 65
    assert pages[0] == find(catalogue, "TKGS", kind="movie", limit=24, offset=0)


@pytest.mark.parametrize("query", ["", " ", "%", "_", "'\"();\\", "🙂"])
def test_empty_or_punctuation_does_not_enumerate_library(catalogue, query):
    movie(catalogue, "沙丘")
    assert find(catalogue, query)["total"] == 0


@pytest.mark.parametrize("params", [
    {"q": "X" * 81}, {"kind": "episode"}, {"kind": "movie' OR 1=1--"},
    {"limit": 0}, {"limit": 61}, {"offset": -1}, {"offset": 2 ** 63},
    {"media_library": -1}, {"media_library": 2 ** 63},
])
def test_bounded_parameters(params):
    assert client.get("/api/tv-client/search", params=params).status_code == 422


def test_title_search_does_not_change_web_search_or_match_actor(catalogue):
    identifier = movie(catalogue, "沙丘", person_names="演员甲")
    assert find(catalogue, "YYJ")["total"] == 0
    assert store.search_fts("SQ", library_ids=[catalogue["电影"]]) == []
    assert store.search_fts("沙丘", library_ids=[catalogue["电影"]])[0]["id"] == identifier
    assert search_tv_titles("%; DROP TABLE movies; --", media_library=catalogue["media"])["total"] == 0
    assert store.get_movie(identifier) is not None
