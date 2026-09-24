"""剧集演员信息回填：person_names 离线回填 + 离线刮削不清空人名。"""
import time

import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app
from app.scanner import tv_persist

client = TestClient(app)


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvpersons"
    root.mkdir()
    lib = store.create_library(name=f"tvpersons-{tmp_path.name}", kind="tv",
                               path=str(root))
    library_paths.invalidate_cache()
    yield lib
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _meta(title="测试剧"):
    return {"title": title, "original_title": title, "year": 2020,
            "overview": "", "imdb_id": "", "tmdb_rating": 8.0, "genres": [],
            "genre_ids": [], "origin_country": "CN", "origin_countries": ["CN"],
            "original_language": "zh", "region": "华语"}


def _credits(*names):
    return {"cast": [{"id": i + 1, "name": n, "profile_path": None,
                      "character": "", "order": i} for i, n in enumerate(names)],
            "crew": []}


def _detail(tmdb_id, with_credits=True):
    d = {"id": tmdb_id, "name": "测试剧", "original_name": "Test",
         "first_air_date": "2020-01-01", "origin_country": ["CN"],
         "original_language": "zh", "external_ids": {}, "networks": [],
         "production_companies": [], "episode_run_time": [],
         "created_by": [], "seasons": [], "genres": [], "overview": "",
         "vote_average": 8.0, "poster_path": None, "backdrop_path": None,
         "status": "Ended", "last_air_date": "", "number_of_seasons": 1,
         "number_of_episodes": 2}
    if with_credits:
        d["credits"] = {"cast": [{"id": 7, "name": "新人",
                                  "profile_path": None, "character": "主角"}],
                        "crew": []}
    return d


def test_backfill_from_cache(tv_lib):
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "测试剧", 2020)
    store.update_show_meta(sid, tmdb_id=111, fetched_at=int(time.time()))
    store.upsert_tmdb_cache(111, _meta(), _credits("姚晨", "闫妮"), "", media_type="tv")
    rows = tv_persist.backfill_person_names(library_ids=[lib["id"]])
    assert [(r["show_id"], r["status"]) for r in rows] == [(sid, "ok_backfilled")]
    assert store.get_show_meta(sid)["person_names"] == "姚晨, 闫妮"
    # 回填后可搜到人
    assert store.suggest_tv_people("姚晨", library_ids=lib["id"])[0]["name"] == "姚晨"
    assert [r["title"] for r in store.list_shows(lib["id"], q="闫妮")] == ["测试剧"]


def test_backfill_no_cache(tv_lib):
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "无缓存剧", 2021)
    store.update_show_meta(sid, tmdb_id=222, fetched_at=int(time.time()))
    rows = tv_persist.backfill_person_names(library_ids=[lib["id"]])
    assert [(r["show_id"], r["status"]) for r in rows] == [(sid, "no_cache_credits")]
    assert store.get_show_meta(sid)["person_names"] == ""


def test_backfill_skips_filled(tv_lib):
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "已有人名剧", 2022)
    store.update_show_meta(sid, tmdb_id=333, fetched_at=int(time.time()),
                           person_names="老人")
    rows = tv_persist.backfill_person_names(library_ids=[lib["id"]])
    assert rows == []


def test_offline_apply_keeps_names(tv_lib):
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "测试剧", 2020)
    store.update_show_meta(sid, tmdb_id=444, person_names="老人")
    store.upsert_tmdb_cache(444, _meta(), _credits("老人"), "", media_type="tv")
    stats = tv_persist.apply_tv_detail(sid, _detail(444, with_credits=False),
                                       download_art=False, offline_reason="断网",
                                       library_id=lib["id"])
    assert stats["offline"] is True
    assert store.get_show_meta(sid)["person_names"] == "老人"
    assert [c["name"] for c in
            store.get_tmdb_cached(444, "tv")["credits"]["cast"]] == ["老人"]


def test_online_apply_refreshes_names(tv_lib):
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "测试剧", 2020)
    store.update_show_meta(sid, tmdb_id=555, person_names="老人")
    tv_persist.apply_tv_detail(sid, _detail(555, with_credits=True),
                               download_art=False, library_id=lib["id"])
    assert store.get_show_meta(sid)["person_names"] == "新人"


def test_scrape_pending_pure_backfill_separate(tv_lib):    # scrape_pending 保持纯净（只做刮削）；回填由任务层另行调用
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "测试剧", 2020)
    store.update_show_meta(sid, tmdb_id=666, fetched_at=int(time.time()))
    store.upsert_tmdb_cache(666, _meta(), _credits("姚晨"), "", media_type="tv")
    assert tv_persist.scrape_pending(library_ids=[lib["id"]]) == []
    out = tv_persist.backfill_person_names(library_ids=[lib["id"]])
    assert [(r["show_id"], r["status"]) for r in out] == [(sid, "ok_backfilled")]
    assert store.get_show_meta(sid)["person_names"] == "姚晨"


def test_show_detail_cast(tv_lib):
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "测试剧", 2020)
    store.update_show_meta(sid, tmdb_id=777)
    store.upsert_tmdb_cache(777, _meta(), {"cast": [
        {"id": 1, "name": "周杰", "profile_path": "/zhou.jpg",
         "character": "包拯", "order": 0},
        {"id": 2, "name": "", "profile_path": None,
         "character": "", "order": 1},
    ], "crew": []}, "", media_type="tv")
    d = client.get(f"/api/tv/shows/{sid}").json()
    assert d["cast"] == [{"id": 1, "tmdb_id": 1, "name": "周杰",
                          "character": "包拯", "character_name": "包拯",
                          "profile_path": "/zhou.jpg", "avatar": "/zhou.jpg"}]


def test_show_detail_cast_empty_without_match(tv_lib):
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "未匹配剧", 2020)
    d = client.get(f"/api/tv/shows/{sid}").json()
    assert d["cast"] == []
