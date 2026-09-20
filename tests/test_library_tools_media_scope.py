"""媒体库级库工具（2026-09 重构）：作用域聚合、归档顶层=视频库根、分表字段。

覆盖：unmatched/missing/restore-candidates 的 media_library 聚合与 library_id 字段、
organize（就地保留父目录 / 搬到顶层到视频库根 / 媒体库作用域不泄漏）、
维护 job 的 media_library_id、扫描 summary 分库统计、fs 媒体根只读浏览。
"""
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def media(tmp_path):
    """媒体库：电影（subpath 电影）+ 剧集（subpath 剧集）两个视频库。"""
    root = tmp_path / "medialib"
    root.mkdir()
    m = store.create_media_library(
        name=f"media-scope-{tmp_path.name}", path=str(root),
        video_libraries=[
            {"name": "电影", "subpath": "电影", "kind": "movie"},
            {"name": "剧集", "subpath": "剧集", "kind": "tv"},
        ])
    library_paths.invalidate_cache()
    yield m
    store.delete_media_library(m["id"])
    library_paths.invalidate_cache()


def _libs(media) -> dict:
    return {v["name"]: v for v in store.video_libraries_of(media["id"])}


def _mk(library_id, rel, *, write=True, **meta):
    lib = store.get_library(library_id)
    root = pathlib.Path(lib["path"])
    p = root / rel
    if write:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel, library_id=library_id)
    if meta:
        store.update_movie_meta(mid, **meta)
    return mid


def _other_library(tmp_path, name) -> dict:
    """对比用独立媒体库（不与默认库路径重叠）。"""
    root = tmp_path / ("other-" + name)
    root.mkdir(exist_ok=True)
    return store.create_library(name=name, path=str(root))


def test_organize_media_scope_relocate_to_library_root(media, media_root, tmp_path):
    libs = _libs(media)
    movie_lib = libs["电影"]["id"]
    mid = _mk(movie_lib, "周星驰/功夫.Kung.Fu.Hustle.2004/Old.mkv",
              title="功夫", year=2004, tmdb_id=9470)
    other = _other_library(tmp_path, f"other-{movie_lib}")
    oid = _mk(other["id"], "十二猴子.12.Monkeys.1995/Old2.mkv",
              title="十二猴子", year=1995, tmdb_id=63)
    library_paths.invalidate_cache()
    try:
        d = client.post("/api/files/organize", json={
            "mode": "relocate", "dry_run": True, "media_library_id": media["id"]}).json()
        plans = {p["id"]: p for p in d["plans"]}
        assert mid in plans
        # 顶层 = 视频库根（相对媒体库作用域，无“待整理/电影”前缀）
        p = plans[mid]
        assert p["library_id"] == movie_lib
        to = p["to"] if p.get("kind") != "dir" else p["files"][0]["to"]
        assert to == "功夫 (2004)/功夫 (2004).mkv"
        assert oid not in plans          # 其他媒体库不泄漏
        # to_dir 仅单库兼容：媒体库作用域明确 422
        r = client.post("/api/files/organize", json={
            "mode": "relocate", "dry_run": True, "media_library_id": media["id"],
            "to_dir": "电影"})
        assert r.status_code == 422
    finally:
        store.delete_movie(mid)
        store.delete_media_library(other["media_library_id"])
        library_paths.invalidate_cache()


def test_organize_inplace_keeps_user_parent_dir(media):
    libs = _libs(media)
    movie_lib = libs["电影"]["id"]
    mid = _mk(movie_lib, "周星驰/功夫.Kung.Fu.Hustle.2004/功夫.Kung.Fu.Hustle.2004.mkv",
              title="功夫", year=2004, tmdb_id=9470)
    try:
        d = client.post("/api/files/organize", json={
            "mode": "inplace", "dry_run": True, "media_library_id": media["id"]}).json()
        p = next(x for x in d["plans"] if x["id"] == mid)
        files = p["files"] if p.get("kind") == "dir" else [p]
        assert all(f["to"].startswith("周星驰/") for f in files)
        assert files[0]["to"] == "周星驰/功夫 (2004)/功夫 (2004).mkv"
    finally:
        store.delete_movie(mid)


def test_unmatched_and_missing_media_scope(media, tmp_path):
    libs = _libs(media)
    movie_lib = libs["电影"]["id"]
    mid = _mk(movie_lib, "u/未匹配.2020/未匹配.mkv")           # 无 tmdb → unmatched
    gone = _mk(movie_lib, "u/已删.2020/已删.mkv", title="已删", year=2020,
               tmdb_id=888201)   # 已匹配 → 不进 unmatched，只进 missing
    (pathlib.Path(store.get_library(movie_lib)["path"]) / "u/已删.2020/已删.mkv").unlink()
    other = _other_library(tmp_path, f"other2-{movie_lib}")
    oid = _mk(other["id"], "u/别库.2020/别库.mkv")
    library_paths.invalidate_cache()
    try:
        d = client.get(f"/api/files/unmatched?media_library={media['id']}").json()
        assert [x["id"] for x in d["unmatched"]] == [mid]
        assert d["unmatched"][0]["library_id"] == movie_lib
        assert client.get("/api/files/unmatched?media_library=999999").json()["total_unmatched"] == 0

        m = client.get(f"/api/files/missing?media_library={media['id']}").json()
        assert [x["id"] for x in m["items"]] == [gone]
        assert m["items"][0]["library_id"] == movie_lib
    finally:
        store.delete_movie(mid)
        store.delete_movie(gone)
        store.delete_media_library(other["media_library_id"])
        library_paths.invalidate_cache()


def test_restore_candidates_items_have_library_id(media, tmp_path):
    libs = _libs(media)
    movie_lib = libs["电影"]["id"]
    mid = _mk(movie_lib, "old/搬过.2020/Old.mkv", title="搬过", year=2020)
    root = pathlib.Path(store.get_library(movie_lib)["path"])
    new_rel = "new/搬过 (2020)/搬过 (2020).mkv"
    (root / new_rel).parent.mkdir(parents=True, exist_ok=True)
    (root / new_rel).write_bytes(b"x")
    store.update_movie_local(mid, file_path=new_rel)
    other = _other_library(tmp_path, f"other3-{movie_lib}")
    oid = _mk(other["id"], "old/别库搬过.2020/Old.mkv", title="别库搬过", year=2020)
    (pathlib.Path(other["path"]) / "new/别库搬过 (2020)").mkdir(parents=True, exist_ok=True)
    (pathlib.Path(other["path"]) / "new/别库搬过 (2020)/别库搬过 (2020).mkv").write_bytes(b"x")
    store.update_movie_local(oid, file_path="new/别库搬过 (2020)/别库搬过 (2020).mkv")
    library_paths.invalidate_cache()
    try:
        d = client.get(f"/api/files/restore-candidates?media_library={media['id']}").json()
        assert [x["id"] for x in d["items"]] == [mid]
        assert d["items"][0]["library_id"] == movie_lib
    finally:
        store.delete_movie(mid)
        store.delete_media_library(other["media_library_id"])
        library_paths.invalidate_cache()


def test_maintenance_media_scope(media, tmp_path, monkeypatch):
    libs = _libs(media)
    movie_lib = libs["电影"]["id"]
    a = _mk(movie_lib, "m/A (2020)/A.mkv", title="A", year=2020, tmdb_id=888101)
    other = _other_library(tmp_path, f"other4-{movie_lib}")
    b = _mk(other["id"], "m/B (2020)/B.mkv", title="B", year=2020, tmdb_id=888102)
    store.upsert_tmdb_cache(888101, {"title": "A", "year": 2020, "media_type": "movie"}, {}, "")
    store.upsert_tmdb_cache(888102, {"title": "B", "year": 2020, "media_type": "movie"}, {}, "")
    seen: list[int] = []
    monkeypatch.setattr(scanner, "refresh_tmdb_id",
                        lambda tid: seen.append(int(tid)) or {"changed": False})
    library_paths.invalidate_cache()
    try:
        d = client.post("/api/jobs/backfill-meta",
                        json={"media_library_id": media["id"]}).json()
        assert [r["id"] for r in d["results"]] == [a]
        r = client.post("/api/jobs/tmdb-refresh",
                        json={"media_library_id": media["id"]}).json()
        assert r["total"] == 1 and seen == [888101]
        n = client.post("/api/jobs/rebuild-nfo",
                        json={"media_library_id": media["id"], "dry_run": True}).json()
        assert n["total"] == 1 and n["ok"] == 1
        bd = client.post("/api/jobs/clean-bdmv",
                         json={"media_library_id": media["id"], "dry_run": True}).json()
        assert bd["total"] == 0
    finally:
        store.delete_movie(a)
        store.delete_media_library(other["media_library_id"])
        library_paths.invalidate_cache()


def test_scan_summary_by_library():
    from app.routers.jobs import _scan_summary
    out = _scan_summary([
        {"status": "ok", "library_id": 11},
        {"status": "ok", "library_id": 11},
        {"status": "no_match", "library_id": 12},
        {"status": "library_offline", "library_id": 12},
        {"status": "error: boom"},
    ])
    assert out["counts"]["ok"] == 2
    assert out["by_library"] == {11: {"ok": 2}, 12: {"no_match": 1, "library_offline": 1}}
    assert len(out["errors"]) == 1


def test_fs_media_root_listing(media):
    libs = _libs(media)
    d = client.get(f"/api/fs/list?media_library={media['id']}").json()
    assert d["root_kind"] == "media"
    assert d["fs_writable"] is False
    assert d["media_library_id"] == media["id"]
    by_name = {x["name"]: x for x in d["dirs"]}
    assert by_name["电影"]["video_library_id"] == libs["电影"]["id"]
    assert by_name["电影"]["kind"] == "movie"
    assert by_name["剧集"]["video_library_id"] == libs["剧集"]["id"]
    assert by_name["剧集"]["kind"] == "tv"
    # 媒体根写操作不进 DB：列表只读、未知媒体库 404
    assert client.get("/api/fs/list?media_library=999999").status_code == 404
    # 进入视频库目录仍走原 library 上下文（可写判定与分类不变）
    libd = client.get(f"/api/fs/list?library={libs['电影']['id']}").json()
    assert libd.get("root_kind") is None
    from app import storage
    backend = storage.backend_for_media(media["id"])
    assert backend.read_only is True
    assert any(e["name"] == "电影" for e in backend.list(""))
