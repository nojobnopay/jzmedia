"""TV 待处理过滤（pending=1）：未匹配 / 剧级待确认 / 有未匹配集 + episode_review_count。

库工具剧集 Tab 的「② 剧集匹配」步骤依赖该过滤，避免前端在整墙列表里
自行筛选（大库分页会漏）；同时下发未匹配集数供行内徽章展示。
"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def lib(tmp_path):
    root = tmp_path / "tvpending"
    root.mkdir()
    row = store.create_library(name=f"tvpending-{tmp_path.name}", kind="tv",
                               path=str(root))
    library_paths.invalidate_cache()
    yield row, root
    store.delete_media_library(row["media_library_id"])
    library_paths.invalidate_cache()


def _seed(lid):
    """4 部剧：未匹配 / 剧级待确认 / 集级未匹配 / 完全正常。"""
    s_un = store.upsert_show(lid, "未匹配剧", 2020)
    s_rev = store.upsert_show(lid, "待确认剧", 2021)
    store.update_show_meta(s_rev, tmdb_id=100, needs_review=1)
    s_ep = store.upsert_show(lid, "集待处理剧", 2022)
    store.update_show_meta(s_ep, tmdb_id=200)
    e_ep = store.upsert_episode(s_ep, lid, "集待处理剧/Season 01/集待处理剧-S01E01.mkv",
                                1, 1)
    store.update_episode_meta(e_ep, needs_review=1)
    s_ok = store.upsert_show(lid, "正常剧", 2023)
    store.update_show_meta(s_ok, tmdb_id=300)
    store.upsert_episode(s_ok, lid, "正常剧/Season 01/正常剧-S01E01.mkv", 1, 1)
    return {"unmatched": s_un, "review": s_rev, "episode": s_ep, "ok": s_ok}


def test_pending_filter_and_counts(lib):
    librow, _root = lib
    ids = _seed(librow["id"])

    d = client.get(f"/api/tv/shows?library={librow['id']}&pending=1&limit=50").json()
    got = {s["id"] for s in d["items"]}
    assert got == {ids["unmatched"], ids["review"], ids["episode"]}
    assert d["total"] == 3
    assert d["has_more"] is False

    by_id = {s["id"]: s for s in d["items"]}
    assert by_id[ids["episode"]]["episode_review_count"] == 1
    assert by_id[ids["review"]]["episode_review_count"] == 0
    assert by_id[ids["unmatched"]]["episode_review_count"] == 0

    # 非 pending 列表仍返回全部 4 部（字段常备）
    all_d = client.get(f"/api/tv/shows?library={librow['id']}&limit=50").json()
    assert all_d["total"] == 4
    assert {s["id"] for s in all_d["items"]} == set(ids.values())
    assert all("episode_review_count" in s for s in all_d["items"])


def test_pending_scoped_to_library(lib, tmp_path):
    librow, _root = lib
    _seed(librow["id"])
    (tmp_path / "other").mkdir()
    other = store.create_library(name=f"tvpending-other-{tmp_path.name}", kind="tv",
                                 path=str(tmp_path / "other"))
    library_paths.invalidate_cache()
    try:
        d = client.get(f"/api/tv/shows?library={other['id']}&pending=1&limit=50").json()
        assert d["total"] == 0 and d["items"] == []
    finally:
        store.delete_media_library(other["media_library_id"])
        library_paths.invalidate_cache()


def test_pending_count_after_confirm(lib):
    """确认匹配（清 needs_review）后不再计入待处理；绑定 tmdb_id 同样出列。"""
    librow, _root = lib
    ids = _seed(librow["id"])
    client.post(f"/api/tv/shows/{ids['review']}/confirm-match")
    store.set_show_match(ids["unmatched"], 999)
    d = client.get(f"/api/tv/shows?library={librow['id']}&pending=1&limit=50").json()
    assert [s["id"] for s in d["items"]] == [ids["episode"]]
    assert d["total"] == 1


def test_overview_stats_use_pending_scope_and_count_seasons(lib):
    row, _ = lib
    ids = _seed(row['id'])
    store.upsert_season(ids['ok'], row['id'], 1)
    store.upsert_season(ids['ok'], row['id'], 2)
    data = client.get('/api/tv/stats', params={'library': row['id']}).json()
    assert data['shows'] == 4 and data['pending'] == 3
    assert data['episodes'] == 2 and data['episode_review'] == 1
    assert data['seasons'] == 3  # existing metadata and local season union, no double count
    assert [x['library_id'] for x in data['by_library']] == [row['id']]
    empty = client.get('/api/tv/stats?media_library=9999999').json()
    assert empty['shows'] == empty['pending'] == empty['seasons'] == 0
    assert empty['by_library'] == []
