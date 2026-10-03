"""Actor-first TV discovery stays local and uses explicit acting relationships."""
import itertools
import json

import pytest
from fastapi.testclient import TestClient

from app import store, tmdb
from app.main import app
from app.store.tv_search import _title_keys

client = TestClient(app)
_ids = itertools.count(1900000000)


@pytest.fixture()
def catalogue(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("actor discovery must not fetch remote data")

    monkeypatch.setattr(tmdb, "person_detail", no_network)
    monkeypatch.setattr(tmdb, "download_url", no_network)
    monkeypatch.setattr("app.scanner.persist.save_person_avatar", no_network)
    root = tmp_path / "actors"
    root.mkdir()
    media = store.create_media_library(
        name=f"actors-{tmp_path.name}", path=str(root),
        video_libraries=[{"name": "movie", "kind": "movie", "subpath": "movies"},
                         {"name": "tv", "kind": "tv", "subpath": "tv"},
                         {"name": "other", "kind": "movie", "subpath": "other"}])
    libraries = {row["name"]: row["id"] for row in store.video_libraries_of(media["id"])}
    data = {"media": media["id"], "people": [], "cache": [], **libraries}
    yield data
    store.delete_media_library(media["id"])
    with store._lock, store._conn() as connection:
        for pid in data["people"]:
            connection.execute("DELETE FROM persons WHERE tmdb_id=?", (pid,))
        for tid, kind in data["cache"]:
            connection.execute("DELETE FROM tmdb_cache WHERE tmdb_id=? AND media_type=?", (tid, kind))


def movie(catalogue, title="测试电影", tid=None, library="movie", **meta):
    identifier = store.upsert_movie_by_path(f"fixture-{next(_ids)}.mkv", library_id=catalogue[library])
    store.update_movie_meta(identifier, title=title, tmdb_id=tid, **meta)
    return identifier


def person(catalogue, name="刘德华", avatar="posters/persons/actor.jpg"):
    tid = next(_ids)
    pid = store.upsert_person(tid, name, avatar=avatar)
    catalogue["people"].append(tid)
    return pid, tid


def link(movie_id, pid, role="actor"):
    store.link_person(movie_id, pid, role)
    store.resync_fts(movie_id)


def cache(catalogue, tid, credits, kind="movie"):
    store.upsert_tmdb_cache(tid, {"title": f"Fixture {tid}"}, credits, "", media_type=kind)
    catalogue["cache"].append((tid, kind))


def show(catalogue, title="测试剧集", tid=None, **meta):
    sid = store.upsert_show(catalogue["tv"], title, 2020)
    store.update_show_meta(sid, tmdb_id=tid, **meta)
    return sid


def actors(catalogue, query="", **params):
    response = client.get("/api/tv-client/actors", params={
        "q": query, "media_library": catalogue["media"], **params})
    assert response.status_code == 200, response.text
    return response.json()


def works(catalogue, key, **params):
    response = client.get("/api/tv-client/actor-works", params={
        "actor": key, "media_library": catalogue["media"], **params})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("query", ["刘德华", "LDH", "ldh", "ＬＤＨ", "liudehua", "liu de hua"])
def test_actor_query_spellings_and_local_avatar(catalogue, query):
    mid = movie(catalogue, "无间道", year=2002, poster_path="posters/movies/infernal.jpg")
    pid, tid = person(catalogue)
    link(mid, pid)
    result = actors(catalogue, query)
    assert result["items"] == [{"key": f"tmdb:{tid}", "name": "刘德华",
                                "avatar_path": "posters/persons/actor.jpg",
                                "movie_count": 1, "show_count": 0, "work_count": 1}]
    filmography = works(catalogue, result["items"][0]["key"])
    assert filmography["actor"] == result["items"][0]
    assert filmography["items"] == [{"id": mid, "kind": "movie", "title": "无间道",
                                    "original_title": "", "year": 2002,
                                    "poster_path": "posters/movies/infernal.jpg",
                                    "library_id": catalogue["movie"],
                                    "media_library_id": catalogue["media"], "version_count": 1}]


def test_actor_combines_movie_aggregate_season_guests_and_known_aliases(catalogue):
    pid, actor_tid = person(catalogue)
    movie_tid, show_tid = next(_ids), next(_ids)
    movie_id = movie(catalogue, tid=movie_tid)
    link(movie_id, pid)
    cache(catalogue, movie_tid, {"cast": [{"id": actor_tid, "name": "Andy Lau"}]})
    show_id = show(catalogue, tid=show_tid)
    cache(catalogue, show_tid, {"cast": [{"id": actor_tid, "name": "刘德华"}]}, kind="tv")
    store.upsert_season(show_id, catalogue["tv"], 1,
                        cast_json=json.dumps([{"id": actor_tid, "name": "刘德华"}]))
    episode = store.upsert_episode(show_id, catalogue["tv"], "show/s01e01.mkv", 1, 1, "第一集")
    store.update_episode_meta(episode, episode_credits=json.dumps({
        "guests": [{"id": actor_tid, "name": "刘德华"}], "directors": []}))
    result = actors(catalogue, "Andy Lau")["items"]
    assert len(result) == 1
    assert result[0]["name"] == "刘德华"
    assert (result[0]["movie_count"], result[0]["show_count"], result[0]["work_count"]) == (1, 1, 2)
    assert {(row["kind"], row["id"]) for row in works(catalogue, result[0]["key"])["items"]} == {
        ("movie", movie_id), ("show", show_id)}


def test_original_name_alias_without_person_creation(catalogue):
    actor_tid, show_tid = next(_ids), next(_ids)
    show(catalogue, tid=show_tid)
    cache(catalogue, show_tid, {"cast": [{"id": actor_tid, "name": "刘德华",
                                        "original_name": "Andy Lau", "profile_path": "/remote.jpg",
                                        "avatar": "/also-remote.jpg"}]}, kind="tv")
    assert store.person_exists(actor_tid) is False
    candidate = actors(catalogue, "Andy")["items"][0]
    assert candidate["key"] == f"tmdb:{actor_tid}"
    assert candidate["avatar_path"] == ""
    assert works(catalogue, candidate["key"])["total"] == 1
    assert store.person_exists(actor_tid) is False


@pytest.mark.parametrize("name,initials,spelling", [
    ("曾志伟", "ZZW", "zengzhiwei"), ("单立文", "SLW", "shanliwen"),
    ("解晓东", "XXD", "xiexiaodong"), ("仇云波", "QYB", "qiuyunbo"),
])
def test_polyphonic_surname_search_keeps_film_spelling_unchanged(catalogue, name, initials, spelling):
    original_title_keys = _title_keys(name)
    pid, actor_tid = person(catalogue, name=name)
    link(movie(catalogue), pid)
    for query in (initials, spelling, original_title_keys[1]):
        assert [row["key"] for row in actors(catalogue, query)["items"]] == [f"tmdb:{actor_tid}"]
    _title_keys.cache_clear()
    assert _title_keys(name) == original_title_keys


def test_directors_creators_and_mixed_person_names_are_not_actors(catalogue):
    pid, director_tid = person(catalogue, "导演甲")
    mid = movie(catalogue, person_names="导演甲, 未分角色的人名")
    link(mid, pid, "director")
    movie_tid, show_tid = next(_ids), next(_ids)
    movie(catalogue, tid=movie_tid)
    cache(catalogue, movie_tid, {"cast": [], "crew": [{"id": director_tid, "name": "导演甲"}]})
    sid = show(catalogue, tid=show_tid, person_names="创作者乙, 导演甲",
               created_by=[{"id": director_tid, "name": "创作者乙"}])
    cache(catalogue, show_tid, {"cast": [], "crew": [{"id": director_tid, "name": "导演甲"}]}, kind="tv")
    eid = store.upsert_episode(sid, catalogue["tv"], "roles/s01e01.mkv", 1, 1, "第一集")
    store.update_episode_meta(eid, episode_credits=json.dumps({
        "guests": [], "directors": [{"id": director_tid, "name": "导演甲"}]}))
    assert actors(catalogue)["total"] == 0


def test_same_name_distinct_ids_and_name_only_remain_distinct(catalogue):
    movie_tid = next(_ids)
    movie(catalogue, tid=movie_tid)
    actor_a, actor_b = next(_ids), next(_ids)
    cache(catalogue, movie_tid, {"cast": [
        {"id": actor_a, "name": "Tom Hanks"}, {"id": actor_b, "name": "Tom Hanks"},
        {"name": "Tom   Hanks"}, {"name": "Ｔｏｍ Hanks"}]})
    result = actors(catalogue, "tom hanks")
    assert result["total"] == 3
    assert {row["key"] for row in result["items"]} == {f"tmdb:{actor_a}", f"tmdb:{actor_b}", "name:tom hanks"}
    assert works(catalogue, "name:tom hanks")["total"] == 1


def test_movie_versions_count_once_and_keep_different_video_libraries(catalogue):
    pid, actor_tid = person(catalogue)
    movie_tid = next(_ids)
    old = movie(catalogue, "作品原版", tid=movie_tid, year=2000)
    new = movie(catalogue, "作品新版", tid=movie_tid, year=2000)
    alternate = movie(catalogue, "另一个视频库", tid=movie_tid, library="other", year=2000)
    link(old, pid)
    link(new, pid)
    link(alternate, pid)
    candidate = actors(catalogue)["items"][0]
    assert candidate["work_count"] == 2
    result = works(catalogue, f"tmdb:{actor_tid}")
    assert {row["id"] for row in result["items"]} == {new, alternate}
    assert next(row for row in result["items"] if row["id"] == new)["version_count"] == 2


def test_empty_query_orders_by_work_count_and_paginates_actors(catalogue):
    tid = next(_ids)
    movie(catalogue, tid=tid)
    cast = [{"id": next(_ids), "name": f"演员 {number:02d}"} for number in range(30)]
    cache(catalogue, tid, {"cast": cast})
    show_tid = next(_ids)
    show(catalogue, tid=show_tid)
    cache(catalogue, show_tid, {"cast": [cast[-1]]}, kind="tv")
    first, second, past = (actors(catalogue, offset=offset) for offset in (0, 24, 48))
    assert [first["total"], second["total"], past["total"]] == [30, 30, 30]
    assert [len(first["items"]), len(second["items"]), len(past["items"])] == [24, 6, 0]
    assert [first["has_more"], second["has_more"], past["has_more"]] == [True, False, False]
    assert first["items"][0]["key"] == f"tmdb:{cast[-1]['id']}"
    assert len({row["key"] for row in first["items"] + second["items"]}) == 30
    assert first == actors(catalogue)


def test_actor_works_filter_sort_and_complete_pagination(catalogue):
    pid, actor_tid = person(catalogue)
    for number in range(27):
        link(movie(catalogue, f"电影 {number:02d}", year=2000 + number), pid)
    show_tid = next(_ids)
    sid = show(catalogue, tid=show_tid)
    store.update_show_meta(sid, year=2027)
    cache(catalogue, show_tid, {"cast": [{"id": actor_tid, "name": "刘德华"}]}, kind="tv")
    key = f"tmdb:{actor_tid}"
    first, second = (works(catalogue, key, offset=offset) for offset in (0, 24))
    assert first["total"] == second["total"] == 28
    assert first["items"][0]["kind"] == "show"
    assert len(first["items"]) == 24 and len(second["items"]) == 4
    assert len({(row["kind"], row["id"]) for row in first["items"] + second["items"]}) == 28
    assert works(catalogue, key, kind="movie")["total"] == 27
    assert works(catalogue, key, kind="show")["total"] == 1


def test_library_scope_enabled_state_and_unreachable_cache(catalogue, tmp_path):
    pid, actor_tid = person(catalogue)
    local = movie(catalogue)
    link(local, pid)
    unseen_tid = next(_ids)
    cache(catalogue, unseen_tid, {"cast": [{"name": "未入库演员"}]})
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    other = store.create_library(name=f"actor-outside-{tmp_path.name}", path=str(outside_root))
    try:
        other_movie = store.upsert_movie_by_path("outside.mkv", library_id=other["id"])
        store.update_movie_meta(other_movie, title="另一媒体库作品")
        link(other_movie, pid)
        key = f"tmdb:{actor_tid}"
        assert actors(catalogue)["items"][0]["work_count"] == 1
        assert [row["id"] for row in works(catalogue, key)["items"]] == [local]
        all_scope = client.get("/api/tv-client/actor-works", params={"actor": key}).json()
        assert all_scope["total"] == 2
        assert actors(catalogue, "未入库演员")["total"] == 0
        assert actors(catalogue, media_library=2 ** 31 - 1)["total"] == 0
        store.update_library(catalogue["movie"], enabled=False)
        assert actors(catalogue)["total"] == 0
        assert client.get("/api/tv-client/actor-works", params={
            "actor": key, "media_library": catalogue["media"]}).status_code == 404
        store.update_media_library(other["media_library_id"], enabled=False)
        assert client.get("/api/tv-client/actor-works", params={"actor": key}).status_code == 404
    finally:
        store.delete_media_library(other["media_library_id"])


def test_actor_membership_is_live_after_removing_cast(catalogue):
    pid, actor_tid = person(catalogue)
    mid = movie(catalogue)
    link(mid, pid)
    assert actors(catalogue, "LDH")["total"] == 1
    store.clear_movie_persons(mid)
    store.resync_fts(mid)
    assert actors(catalogue, "LDH")["total"] == 0
    assert client.get("/api/tv-client/actor-works", params={
        "actor": f"tmdb:{actor_tid}", "media_library": catalogue["media"]}).status_code == 404


def test_malformed_credits_do_not_promote_wrong_role_or_crash(catalogue):
    movie_tid = next(_ids)
    movie(catalogue, tid=movie_tid)
    cache(catalogue, movie_tid, {"cast": []})
    sid = show(catalogue)
    store.upsert_season(sid, catalogue["tv"], 1, cast_json="{broken")
    eid = store.upsert_episode(sid, catalogue["tv"], "broken/s01e01.mkv", 1, 1, "第一集")
    store.update_episode_meta(eid, episode_credits=json.dumps([{"name": "不是明确的客串演员"}]))
    with store._lock, store._conn() as connection:
        connection.execute("UPDATE tmdb_cache SET credits=? WHERE tmdb_id=? AND media_type='movie'",
                           (json.dumps([{"name": "不是明确的电影演员"}]), movie_tid))
    assert actors(catalogue)["total"] == 0


def test_external_name_strings_and_ambiguous_external_cache_are_not_actor_evidence(catalogue):
    movie(catalogue, "同名电影", person_names="导演甲, 演员乙", match_source="nfo")
    show(catalogue, "同名剧集", person_names="导演甲, 演员乙", match_source="nfo")
    source_id = f"test-{next(_ids)}"
    store.upsert_external("nfo", source_id, "movie", title="同名电影",
                          payload={"people": {"cast": [{"name": "演员乙"}]}})
    try:
        assert actors(catalogue)["total"] == 0
    finally:
        store.delete_external("nfo", source_id)


@pytest.mark.parametrize("avatar", ["-", "https://example.org/remote.jpg", "../private.jpg"])
def test_avatar_is_only_local_cache(catalogue, avatar):
    pid, _ = person(catalogue, avatar=avatar)
    link(movie(catalogue), pid)
    assert actors(catalogue)["items"][0]["avatar_path"] == ""


def test_name_key_casefold_expansion_stays_within_route_limit(catalogue):
    tid = next(_ids)
    movie(catalogue, tid=tid)
    cache(catalogue, tid, {"cast": [{"name": "ß" * 300}, {"name": "正常演员"}]})
    result = actors(catalogue)
    assert [row["name"] for row in result["items"]] == ["正常演员"]
    assert works(catalogue, result["items"][0]["key"])["total"] == 1


@pytest.mark.parametrize("query", ["%", "_", "'\"();\\", "🙂"])
def test_symbol_only_query_returns_no_actors(catalogue, query):
    pid, _ = person(catalogue)
    link(movie(catalogue), pid)
    assert actors(catalogue, query)["total"] == 0


@pytest.mark.parametrize("path,params", [
    ("actors", {"q": "x" * 81}), ("actors", {"limit": 61}),
    ("actors", {"offset": -1}), ("actors", {"media_library": 2 ** 63}),
    ("actor-works", {}), ("actor-works", {"actor": ""}),
    ("actor-works", {"actor": "x" * 513}),
    ("actor-works", {"actor": "name:演员", "kind": "collection"}),
    ("actor-works", {"actor": "name:演员", "limit": 0}),
])
def test_actor_parameters_are_bounded(path, params):
    assert client.get(f"/api/tv-client/{path}", params=params).status_code == 422


def test_unknown_actor_key_is_not_interpreted_as_sql(catalogue):
    assert client.get("/api/tv-client/actor-works", params={
        "actor": "tmdb:1 OR 1=1", "media_library": catalogue["media"]}).status_code == 404
