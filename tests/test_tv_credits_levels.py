"""TV 三级演职（series aggregate / 季常驻 / 集客串+导演）：解析、落库、离线守卫。"""
import json
import time

import pytest

from app import library_paths, store
from app.scanner import tv_match, tv_persist


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvcredits"
    root.mkdir()
    lib = store.create_library(name=f"tvcredits-{tmp_path.name}", kind="tv",
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


def _detail(tmdb_id):
    return {"id": tmdb_id, "name": "测试剧", "original_name": "Test",
            "first_air_date": "2020-01-01", "origin_country": ["CN"],
            "original_language": "zh", "external_ids": {}, "networks": [],
            "production_companies": [], "episode_run_time": [],
            "created_by": [{"id": 9, "name": "编剧刘", "profile_path": None}],
            "seasons": [{"season_number": 1, "name": "第 1 季", "overview": "",
                         "air_date": "2020-01-01", "episode_count": 2, "id": 77}],
            "genres": [], "overview": "", "vote_average": 8.0,
            "poster_path": None, "backdrop_path": None, "status": "Ended",
            "last_air_date": "", "number_of_seasons": 1, "number_of_episodes": 2,
            # 注意：detail 内嵌 credits 官方仅为最新季，此处模拟 S3 阵容
            "credits": {"cast": [{"id": 3, "name": "邓超", "profile_path": None,
                                  "character": "包拯", "order": 0}], "crew": []}}


def _aggregate():
    return {"cast": [
        {"id": 1, "name": "周杰", "profile_path": None,
         "roles": [{"character": "包拯", "episode_count": 40}],
         "total_episode_count": 40},
        {"id": 2, "name": "多角王", "profile_path": None,
         "roles": [{"character": "甲", "episode_count": 5},
                   {"character": "乙", "episode_count": 3}],
         "total_episode_count": 8},
    ], "crew": []}


def _season_details():
    return {1: {
        "credits": {"cast": [
            {"id": 1, "name": "周杰", "profile_path": None,
             "character": "包拯", "order": 0}], "crew": []},
        "episodes": [
            {"id": 901, "season_number": 1, "episode_number": 1,
             "name": "第一集", "overview": "", "still_path": None,
             "air_date": "2020-01-01", "runtime": 45, "vote_average": 8.0,
             "guest_stars": [{"id": 5, "name": "客串王", "character": "路人",
                              "profile_path": None}],
             "crew": [{"id": 6, "name": "导演张", "job": "Director"}]},
            {"id": 902, "season_number": 1, "episode_number": 2,
             "name": "第二集", "overview": "", "still_path": None,
             "air_date": "2020-01-08", "runtime": 45, "vote_average": 8.0,
             "guest_stars": [], "crew": []},
        ]}}


def test_aggregate_parse_multi_role():
    credits = tv_match.tv_aggregate_credits(_aggregate())
    assert [(c["name"], c["character"], c["episode_count"]) for c in credits["cast"]] == [
        ("周杰", "包拯", 40), ("多角王", "甲", 8)]
    assert tv_match.tv_aggregate_credits({}) == {"cast": [], "crew": []}
    assert tv_match.tv_aggregate_credits(None) == {"cast": [], "crew": []}


def test_season_episode_parse():
    sd = _season_details()[1]
    assert [c["name"] for c in tv_match.tv_season_credits(sd)] == ["周杰"]
    assert tv_match.tv_season_credits({}) == []
    ec = tv_match.tv_episode_credits(sd["episodes"][0])
    assert [g["name"] for g in ec["guests"]] == ["客串王"]
    assert [d["name"] for d in ec["directors"]] == ["导演张"]
    assert tv_match.tv_episode_credits({"id": 1}) == {}  # 缺键视为无数据


def test_person_names_unions_season_regulars(tv_lib):
    # 聚合前 N 漏掉的单季主角，须由季常驻并集补回（少年包青天 S1 周杰案）
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "测试剧", 2020)
    store.upsert_episode(sid, lib["id"], "测试剧/Season 01/测试剧-S01E01.mkv",
                         1, 1, "")
    agg = {"cast": [
        {"id": 3, "name": "全剧红人", "profile_path": None,
         "roles": [{"character": "主", "episode_count": 100}],
         "total_episode_count": 100}], "crew": []}
    sd = _season_details()
    sd[1]["credits"] = {"cast": [
        {"id": 1, "name": "单季主角", "profile_path": None,
         "character": "角", "order": 0}], "crew": []}
    tv_persist.apply_tv_detail(sid, _detail(103), sd, download_art=False,
                               library_id=lib["id"], aggregate=agg)
    assert store.get_show_meta(sid)["person_names"] == "全剧红人, 单季主角, 编剧刘"


def test_apply_three_levels(tv_lib):
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "测试剧", 2020)
    store.upsert_episode(sid, lib["id"], "测试剧/Season 01/测试剧-S01E01.mkv",
                         1, 1, "")
    tv_persist.apply_tv_detail(sid, _detail(101), _season_details(),
                               download_art=False, library_id=lib["id"],
                               aggregate=_aggregate())
    # series 级：aggregate（周杰在列），而非 detail 内嵌的最新季（邓超）
    cached = store.get_tmdb_cached(101, "tv")
    assert [c["name"] for c in cached["credits"]["cast"]] == ["周杰", "多角王"]
    assert store.get_show_meta(sid)["person_names"] == "周杰, 多角王, 编剧刘"
    # 季级：本季常驻
    seasons = store.list_seasons(sid)
    assert [c["name"] for c in seasons[0]["cast"]] == ["周杰"]
    assert store.get_show(sid)["seasons"][0]["cast"][0]["character"] == "包拯"
    # 集级：客串+导演
    ep = store.list_episodes(sid)[0]
    ec = store.parse_episode_credits(ep["episode_credits"])
    assert [g["name"] for g in ec["guests"]] == ["客串王"]
    assert [d["name"] for d in ec["directors"]] == ["导演张"]


def test_offline_keeps_all_levels(tv_lib):
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "测试剧", 2020)
    eid = store.upsert_episode(sid, lib["id"], "测试剧/Season 01/测试剧-S01E01.mkv",
                               1, 1, "")
    store.update_show_meta(sid, tmdb_id=102, person_names="周杰")
    store.upsert_tmdb_cache(102, _meta(), {"cast": [
        {"id": 1, "name": "周杰", "profile_path": None,
         "character": "包拯", "order": 0}], "crew": []}, "", media_type="tv")
    store.upsert_season(sid, lib["id"], 1, cast_json=json.dumps(
        [{"id": 1, "name": "周杰"}], ensure_ascii=False))
    store.update_episode_meta(eid, episode_credits=json.dumps(
        {"guests": [{"id": 5, "name": "客串王"}], "directors": []}))
    d = _detail(102)
    del d["credits"]  # 离线回放 payload 无 credits
    tv_persist.apply_tv_detail(sid, d, {}, download_art=False,
                               offline_reason="断网", library_id=lib["id"])
    assert store.get_show_meta(sid)["person_names"] == "周杰"
    assert [c["name"] for c in
            store.get_tmdb_cached(102, "tv")["credits"]["cast"]] == ["周杰"]
    assert [c["name"] for c in store.list_seasons(sid)[0]["cast"]] == ["周杰"]
    ep = store.get_episode(eid)
    assert [g["name"] for g in
            store.parse_episode_credits(ep["episode_credits"])["guests"]] == ["客串王"]


def test_season_cast_roundtrip(tv_lib):
    lib = tv_lib
    sid = store.upsert_show(lib["id"], "测试剧", 2020)
    # None 保持旧值；"[]" 可覆盖（有数据但无常驻）
    store.upsert_season(sid, lib["id"], 1, cast_json='[{"id": 1, "name": "周杰"}]')
    store.upsert_season(sid, lib["id"], 1)
    assert [c["name"] for c in store.list_seasons(sid)[0]["cast"]] == ["周杰"]
    store.upsert_season(sid, lib["id"], 1, cast_json="[]")
    assert store.list_seasons(sid)[0]["cast"] == []
    assert store.parse_season_cast("脏数据") == []
    assert store.parse_episode_credits(None) == {"guests": [], "directors": []}
