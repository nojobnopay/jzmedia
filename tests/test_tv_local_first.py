"""TV 本地优先读取：浏览态零远程 I/O、季分页、批量核验、快照 auto。

大集数剧集（如 1665 集）此前每进一次详情页触发 E+X+1 次 SMB STAT；
改造后默认只信 DB `missing` 列，核验走按目录分组的一次 list。
"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app
from app.routers import tv as tv_router

client = TestClient(app)


@pytest.fixture()
def lib(tmp_path):
    root = tmp_path / "tvlocal"
    root.mkdir()
    row = store.create_library(name=f"tvlocal-{tmp_path.name}", kind="tv",
                               path=str(root))
    library_paths.invalidate_cache()
    yield row, root
    store.delete_media_library(row["media_library_id"])
    library_paths.invalidate_cache()


def _make_show(lid, n=5, season=1, title="本地剧", year=2021):
    sid = store.upsert_show(lid, title, year)
    ids = []
    for i in range(1, n + 1):
        ids.append(store.upsert_episode(
            sid, lid, f"{title}/Season 01/{title}-S{season:02d}E{i:02d}.mkv",
            season, i, f"第{i}集"))
    return sid, ids


def test_show_detail_no_remote_io_by_default(lib, monkeypatch):
    """剧详情默认零远程 I/O：backend_for 不应被调用。"""
    librow, _root = lib
    lid = librow["id"]
    sid, _ids = _make_show(lid)
    calls = []
    orig = tv_router.storage.backend_for

    def _boom(library_id):
        calls.append(library_id)
        return orig(library_id)

    monkeypatch.setattr(tv_router.storage, "backend_for", _boom)
    d = client.get(f"/api/tv/shows/{sid}").json()
    assert d["episode_count"] == 5 and d["watched_count"] == 0
    assert "episodes" not in d  # 瘦身：不再带全量集
    assert len(d["seasons"]) == 1 and d["seasons"][0]["total"] == 5
    assert calls == []
    # next/extra 本地态同样不触网
    assert d["next_episode"]["exists"] is True


def test_show_detail_verify_batches_per_dir(lib, monkeypatch):
    """verify=1 走批量核验；缺失文件报 False 但不整体失败。"""
    librow, root = lib
    lid = librow["id"]
    sid, ids = _make_show(lid)
    # 真实建一个文件、缺一个文件的对比：删 DB 行对应的物理文件不影响本地态
    p = root / f"本地剧/Season 01/本地剧-S01E01.mkv"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    d = client.get(f"/api/tv/shows/{sid}").json()
    assert d["next_episode"]["exists"] is True  # 本地优先：缺物理文件仍 True
    d1 = client.get(f"/api/tv/shows/{sid}", params={"verify": 1}).json()
    assert d1["verified"] is True
    assert d1["next_episode"]["exists"] is True  # E01 真实存在


def test_season_pagination(lib):
    """季分页：offset/limit 切分无重叠无遗漏，表头为全季计数。"""
    librow, _root = lib
    lid = librow["id"]
    sid, _ids = _make_show(lid, n=5)
    p1 = client.get(f"/api/tv/shows/{sid}/seasons/1",
                    params={"offset": 0, "limit": 2}).json()
    p2 = client.get(f"/api/tv/shows/{sid}/seasons/1",
                    params={"offset": 2, "limit": 2}).json()
    p3 = client.get(f"/api/tv/shows/{sid}/seasons/1",
                    params={"offset": 4, "limit": 2}).json()
    assert (p1["total"], p1["episode_count"]) == (5, 5)
    assert p1["has_more"] is True and p3["has_more"] is False
    got = [e["episode"] for e in p1["episodes"] + p2["episodes"] + p3["episodes"]]
    assert got == [1, 2, 3, 4, 5]
    # 默认 limit=100 全装下
    d = client.get(f"/api/tv/shows/{sid}/seasons/1").json()
    assert d["limit"] == 100 and len(d["episodes"]) == 5


def test_season_detail_no_remote_by_default(lib, monkeypatch):
    """季详情默认不触网；_exists_map 只在 verify=1 时调用。"""
    librow, _root = lib
    lid = librow["id"]
    sid, _ids = _make_show(lid)
    calls = []
    orig = tv_router._verify_exists_map

    def _spy(rows):
        calls.append(len(rows))
        return orig(rows)

    monkeypatch.setattr(tv_router, "_verify_exists_map", _spy)
    d = client.get(f"/api/tv/shows/{sid}/seasons/1").json()
    assert calls == [] and all(e["exists"] is True for e in d["episodes"])
    d1 = client.get(f"/api/tv/shows/{sid}/seasons/1", params={"verify": 1}).json()
    assert d1["verified"] is True and calls == [5]


def test_episode_detail_uses_meta_not_full_show(lib):
    """单集详情不再拉整剧 episodes（大剧下 get_show 是全量行浪费）。"""
    librow, _root = lib
    lid = librow["id"]
    sid, ids = _make_show(lid, n=3)
    d = client.get(f"/api/tv/episodes/{ids[0]}").json()
    assert d["show_title"] == "本地剧" and d["exists"] is True
    assert d["season_name"] == ""


def test_missing_flag_drives_local_exists(lib):
    """missing=1 的行本地态即 False，无需触网。"""
    librow, _root = lib
    lid = librow["id"]
    sid, ids = _make_show(lid, n=2)
    store.update_episode_meta(ids[1], missing=1)
    d = client.get(f"/api/tv/shows/{sid}/seasons/1").json()
    by_ep = {e["episode"]: e["exists"] for e in d["episodes"]}
    assert by_ep == {1: True, 2: False}
