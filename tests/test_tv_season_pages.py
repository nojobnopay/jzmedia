"""Plex 式三层浏览后端：季详情/季已看/季内连播/相关节目/集演职。"""
import json

import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def lib(tmp_path):
    root = tmp_path / "tvseasons"
    root.mkdir()
    row = store.create_library(name=f"tvseasons-{tmp_path.name}", kind="tv",
                               path=str(root))
    library_paths.invalidate_cache()
    yield row
    store.delete_media_library(row["media_library_id"])
    library_paths.invalidate_cache()


def _show(lid, title, year, **meta):
    sid = store.upsert_show(lid, title, year)
    if meta:
        store.update_show_meta(sid, **meta)
    return sid


@pytest.fixture()
def two_seasons(lib):
    lid = lib["id"]
    sid = _show(lid, "测试剧", 2020, tmdb_id=801,
                genres=["剧情"], networks=["HBO"], region="欧美",
                original_language="en",
                person_names="主演甲, 主演乙")
    store.upsert_tmdb_cache(801, {"title": "测试剧", "year": 2020}, {
        "cast": [{"id": 11, "name": "聚合老", "profile_path": None,
                  "character": "老角", "order": 0}], "crew": []}, "",
        media_type="tv")
    store.upsert_season(sid, lid, 1, name="第 1 季", cast_json=json.dumps(
        [{"id": 1, "name": "主演甲", "character": "角甲", "order": 0}]),
        tmdb_season_id=71)
    store.upsert_season(sid, lid, 2, name="第 2 季")  # 无 cast → 回退聚合
    e11 = store.upsert_episode(sid, lid, "测试剧/Season 01/测试剧-S01E01.mkv",
                               1, 1, "一")
    e12 = store.upsert_episode(sid, lid, "测试剧/Season 01/测试剧-S01E02.mkv",
                               1, 2, "二")
    e21 = store.upsert_episode(sid, lid, "测试剧/Season 02/测试剧-S02E01.mkv",
                               2, 1, "三")
    store.update_episode_meta(e11, episode_credits=json.dumps(
        {"guests": [{"id": 5, "name": "客串王", "character": "路人"}],
         "directors": [{"id": 6, "name": "导演张"}]}))
    return {"lib": lib, "sid": sid, "e11": e11, "e12": e12, "e21": e21}


def test_season_detail(two_seasons):
    sid = two_seasons["sid"]
    d = client.get(f"/api/tv/shows/{sid}/seasons/1").json()
    assert d["name"] == "第 1 季" and d["episode_count"] == 2
    assert [c["name"] for c in d["cast"]] == ["主演甲"]
    assert d["cast_source"] == "season"
    assert d["next_episode"]["episode"] == 1  # 全未看 → 首集
    assert d["watched_count"] == 0
    assert d["original_language"] == "en"  # 前端饰演角色展示规则用    # 无 cast 的季回退聚合
    d2 = client.get(f"/api/tv/shows/{sid}/seasons/2").json()
    assert [c["name"] for c in d2["cast"]] == ["聚合老"]
    assert d2["cast_source"] == "aggregate"
    assert client.get(f"/api/tv/shows/{sid}/seasons/9").status_code == 404
    assert client.get("/api/tv/shows/999999/seasons/1").status_code == 404


def test_season_next_partial_first(two_seasons):
    sid, e11, e12 = two_seasons["sid"], two_seasons["e11"], two_seasons["e12"]
    store.save_progress(e12, 600, 3600, kind="episode")  # E02 看一半
    nxt = store.season_next_episode(sid, 1)
    assert int(nxt["id"]) == e12  # 断点优先于 E01
    store.mark_episode_watched(e12, True)
    assert int(store.season_next_episode(sid, 1)["id"]) == e11
    assert store.season_next_episode(sid, 2)["episode"] == 1  # 季隔离
    assert store.season_next_episode(sid, 9) is None


def test_season_watched(two_seasons):
    sid = two_seasons["sid"]
    store.save_progress(two_seasons["e11"], 600, 3600, kind="episode")
    r = client.post(f"/api/tv/shows/{sid}/seasons/1/watched",
                    json={"watched": True}).json()
    assert r == {"ok": True, "watched": True, "episodes": 2}
    assert store.get_progress(two_seasons["e11"], kind="episode") is None
    d = client.get(f"/api/tv/shows/{sid}/seasons/1").json()
    assert d["watched_count"] == 2 and d["next_episode"] is None
    r = client.post(f"/api/tv/shows/{sid}/seasons/1/watched",
                    json={"watched": False}).json()
    assert r["episodes"] == 2


def test_similar(two_seasons):
    lib = two_seasons["lib"]
    sid = two_seasons["sid"]
    other = _show(lib["id"], "同门剧", 2021, genres=["剧情"],
                  networks=["HBO"], person_names="主演甲")
    _show(lib["id"], "无关剧", 1990, genres=["纪录片"], region="华语")
    items = client.get(f"/api/tv/shows/{sid}/similar").json()["items"]
    assert [m["title"] for m in items] == ["同门剧"]
    assert "同电视网" in items[0]["reason"]
    assert client.get("/api/tv/shows/999999/similar").status_code == 404
    # 自身排除
    assert all(m["id"] != sid for m in items)


def test_cross_media_related_show_exposes_library_context(two_seasons, tmp_path):
    root = tmp_path / "other-tv"
    root.mkdir()
    other = store.create_library(name="另一个媒体库", kind="tv", path=str(root))
    library_paths.invalidate_cache()
    try:
        context = store.get_library(other["id"])
        other_id = _show(other["id"], "跨库同门剧", 2021,
                         genres=["剧情"], networks=["HBO"])
        items = client.get(
            f"/api/tv/shows/{two_seasons['sid']}/similar").json()["items"]
        item = next(x for x in items if x["id"] == other_id)
        assert item["media_library_id"] == context["media_library_id"]
        assert item["media_name"] == context["media_name"]
        assert item["library_name"] == context["name"]
        detail = client.get(f"/api/tv/shows/{other_id}").json()
        assert detail["media_library_id"] == context["media_library_id"]
        assert detail["media_name"] == context["media_name"]
        assert detail["library_name"] == context["name"]
    finally:
        store.delete_media_library(other["media_library_id"])
        library_paths.invalidate_cache()


def test_episode_detail_cast(two_seasons):
    e11 = two_seasons["e11"]
    d = client.get(f"/api/tv/episodes/{e11}").json()
    assert [(c["name"], bool(c.get("guest"))) for c in d["cast"]] == [
        ("主演甲", False), ("客串王", True)]
    assert d["cast_source"] == "season"
    assert [x["name"] for x in d["directors"]] == ["导演张"]
    assert d["season_name"] == "第 1 季"


def test_season_and_episode_share_show_backdrop(two_seasons):
    sid, eid = two_seasons["sid"], two_seasons["e11"]
    urls = [f"/api/tv/shows/{sid}/seasons/1", f"/api/tv/episodes/{eid}"]
    for url in urls:
        assert client.get(url).json()["show_backdrop_path"] == ""
    store.update_show_meta(sid, backdrop_path="posters/backdrops/tv_801.jpg")
    for url in urls:
        data = client.get(url).json()
        assert data["show_backdrop_path"] == "posters/backdrops/tv_801.jpg"
        assert data["show_title"] == "测试剧"
