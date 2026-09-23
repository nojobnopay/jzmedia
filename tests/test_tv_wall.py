"""剧集墙筛选/联想（对齐电影墙）：结构化过滤、排序、facets、suggest。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvwall"
    root.mkdir()
    lib = store.create_library(name=f"tvwall-{tmp_path.name}", kind="tv",
                               path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _show(lib_id, title, year, **meta):
    sid = store.upsert_show(lib_id, title, year)
    if meta:
        store.update_show_meta(sid, **meta)
    return sid


@pytest.fixture()
def wall_shows(tv_lib):
    lib, _root = tv_lib
    lid = lib["id"]
    a = _show(lid, "星际迷航", 2020,
              genres=["科幻"], region="欧美", origin_country="US",
              origin_countries=["US"], tags=["太空"],
              tmdb_rating=8.5, status="Returning Series",
              person_names="张三, 李四")
    e1 = store.upsert_episode(a, lid, "星际迷航/Season 01/星际迷航-S01E01.mkv",
                              1, 1, "首播")
    e2 = store.upsert_episode(a, lid, "星际迷航/Season 01/星际迷航-S01E02.mkv",
                              1, 2, "次回")
    store.mark_episode_watched(e1, True)
    store.mark_episode_watched(e2, True)
    b = _show(lid, "武林外传", 2006,
              genres=["喜剧"], region="华语", origin_country="CN",
              origin_countries=["CN"], tags=["情景喜剧"],
              tmdb_rating=9.2, custom_rating=9.5, status="Ended",
              person_names="闫妮, 姚晨")
    store.upsert_episode(b, lid, "武林外传/Season 01/武林外传-S01E01.mkv",
                         1, 1, "第一回")
    c = _show(lid, "未刮削剧", None)
    store.upsert_episode(c, lid, "未刮削剧/Season 01/S01E01.mkv", 1, 1, "")
    return {"lib": lib, "a": a, "b": b, "c": c}


def test_filter_genre(wall_shows):
    lib = wall_shows["lib"]
    rows = store.list_shows(lib["id"], genres=["科幻"])
    assert [r["title"] for r in rows] == ["星际迷航"]
    assert store.count_shows(lib["id"], genres=["科幻"]) == 1


def test_filter_region_country(wall_shows):
    lib = wall_shows["lib"]
    assert [r["title"] for r in store.list_shows(lib["id"], regions=["华语"])] == ["武林外传"]
    rows = store.list_shows(lib["id"], countries=["US"])
    assert [r["title"] for r in rows] == ["星际迷航"]


def test_filter_year_decade(wall_shows):
    lib = wall_shows["lib"]
    assert [r["title"] for r in store.list_shows(lib["id"], years=[2006])] == ["武林外传"]
    rows = store.list_shows(lib["id"], decades=[2020])
    assert [r["title"] for r in rows] == ["星际迷航"]
    rows = store.list_shows(lib["id"], decades=[2000])
    assert [r["title"] for r in rows] == ["武林外传"]


def test_filter_tag_and(wall_shows):
    lib = wall_shows["lib"]
    assert store.count_shows(lib["id"], tags=["太空"]) == 1
    assert store.count_shows(lib["id"], tags=["太空", "情景喜剧"]) == 0


def test_filter_rating(wall_shows):
    lib = wall_shows["lib"]
    rows = store.list_shows(lib["id"], min_rating=9.0, rating_source="tmdb")
    assert [r["title"] for r in rows] == ["武林外传"]
    rows = store.list_shows(lib["id"], min_rating=9.0, rating_source="custom")
    assert [r["title"] for r in rows] == ["武林外传"]
    rows = store.list_shows(lib["id"], min_rating=9.0, rating_source="douban")
    # 未知来源回落 tmdb
    assert [r["title"] for r in rows] == ["武林外传"]


def test_filter_watched(wall_shows):
    lib = wall_shows["lib"]
    done = store.list_shows(lib["id"], watched=1)
    assert [r["title"] for r in done] == ["星际迷航"]
    todo = store.list_shows(lib["id"], watched=0)
    assert sorted(r["title"] for r in todo) == ["未刮削剧", "武林外传"]
    assert store.count_shows(lib["id"], watched=1) == 1


def test_filter_status(wall_shows):
    lib = wall_shows["lib"]
    assert [r["title"] for r in store.list_shows(lib["id"], status=["continuing"])] == ["星际迷航"]
    assert [r["title"] for r in store.list_shows(lib["id"], status=["ended"])] == ["武林外传"]
    others = store.list_shows(lib["id"], status=["other"])
    assert [r["title"] for r in others] == ["未刮削剧"]
    both = store.list_shows(lib["id"], status=["continuing", "ended"])
    assert sorted(r["title"] for r in both) == ["星际迷航", "武林外传"]


def test_q_matches_person_names(wall_shows):
    lib = wall_shows["lib"]
    rows = store.list_shows(lib["id"], q="姚晨")
    assert [r["title"] for r in rows] == ["武林外传"]


def test_sort_rating_year(wall_shows):
    lib = wall_shows["lib"]
    rows = store.list_shows(lib["id"], sort="rating", order="desc",
                             rating_source="tmdb")
    assert rows[0]["title"] == "武林外传"
    rows = store.list_shows(lib["id"], sort="year", order="asc")
    assert rows[0]["title"] == "武林外传"


def test_legacy_default_order_unchanged(wall_shows):
    lib = wall_shows["lib"]
    rows = store.list_shows(lib["id"])
    titles = [r["title"] for r in rows]
    assert titles == sorted(titles)


def test_facets(wall_shows):
    lib = wall_shows["lib"]
    f = store.get_tv_facets(lib["id"])
    assert {g["value"] for g in f["genres"]} == {"科幻", "喜剧"}
    assert f["watched"] == {"watched": 1, "unwatched": 2}
    buckets = {s["value"]: s["count"] for s in f["status"]}
    assert buckets == {"continuing": 1, "ended": 1, "other": 1}
    assert set(f["ratings"].keys()) == {"tmdb", "custom"}
    tmdb9 = [x for x in f["ratings"]["tmdb"] if x["min"] == 9][0]
    assert tmdb9["count"] == 1


def test_suggest(wall_shows):
    lib = wall_shows["lib"]
    assert store.suggest_tv_shows("武林", library_ids=lib["id"])[0]["title"] == "武林外传"
    assert store.suggest_tv_shows("", library_ids=lib["id"]) == []
    people = store.suggest_tv_people("姚晨", library_ids=lib["id"])
    assert people and people[0]["name"] == "姚晨"
    assert store.suggest_tv_people("", library_ids=lib["id"]) == []


def test_api_shows_filters_facets_suggest(wall_shows):
    lib = wall_shows["lib"]
    mid = lib["media_library_id"]
    r = client.get(f"/api/tv/shows?media_library={mid}&genre=科幻")
    assert r.status_code == 200 and r.json()["total"] == 1
    r = client.get(f"/api/tv/shows?media_library={mid}&status=ended")
    assert r.json()["total"] == 1
    r = client.get(f"/api/tv/shows?media_library={mid}&watched=1")
    assert r.json()["total"] == 1
    r = client.get(f"/api/tv/shows?media_library={mid}&sort=rating&order=desc")
    assert r.json()["items"][0]["title"] == "武林外传"
    r = client.get(f"/api/tv/facets?media_library={mid}")
    assert r.status_code == 200 and r.json()["watched"]["watched"] == 1
    r = client.get(f"/api/tv/suggest?q=武林&media_library={mid}")
    assert r.json()["items"][0]["title"] == "武林外传"
    r = client.get(f"/api/tv/suggest?q=姚晨&media_library={mid}")
    assert r.json()["persons"][0]["name"] == "姚晨"
    # 未知媒体库 → 空结果哨兵，绝不退化全库
    r = client.get("/api/tv/shows?media_library=999999999")
    assert r.json()["items"] == [] and r.json()["total"] == 0
    r = client.get("/api/tv/facets?media_library=999999999")
    assert r.json()["genres"] == []
    r = client.get("/api/tv/shows?rating_source=douban")
    assert r.status_code == 422
