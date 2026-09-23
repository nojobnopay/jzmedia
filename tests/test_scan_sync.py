"""扫描自动同步（Plex 式删除识别）：电影 GC + 取消保护 + 汇总计数 + TV 链式。"""
import shutil

from app import library_paths, scanner, store
from app.routers import jobs as jobs_router


def _touch(root, rel, data=b"x"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _lib(tmp_path, kind="movie"):
    root = tmp_path / f"libsync-{tmp_path.name}-{kind}"
    root.mkdir(parents=True, exist_ok=True)
    lib = store.create_library(name=f"sync-{tmp_path.name}-{kind}", kind=kind,
                               path=str(root))
    library_paths.invalidate_cache()
    return lib, root


def _teardown(lib):
    try:
        store.delete_media_library(lib["media_library_id"])
    finally:
        library_paths.invalidate_cache()


def test_movie_gc_deletes_missing_and_cascades(tmp_path, monkeypatch):
    """删文件→扫→行消失（含断点/scan_state），汇总上报 removed_movie。"""
    monkeypatch.setattr(scanner.scan, "search_with_fallback",
                        lambda title, year: (None, title, False))
    lib, root = _lib(tmp_path)
    try:
        _touch(root, "Keep/Keep.2001.mkv")
        _touch(root, "Gone/Gone.2002.mkv")
        scanner.scan_all(library_id=lib["id"])
        keep = store.get_by_path("Keep/Keep.2001.mkv", library_id=lib["id"])
        gone = store.get_by_path("Gone/Gone.2002.mkv", library_id=lib["id"])
        assert keep and gone
        store.save_progress(int(gone["id"]), 30.0, 100.0, kind="movie")
        assert store.get_scan_state("Gone/Gone.2002.mkv",
                                    library_id=lib["id"]) is not None

        shutil.rmtree(root / "Gone")
        out = scanner.scan_all(library_id=lib["id"])
        assert store.get_by_path("Gone/Gone.2002.mkv",
                                 library_id=lib["id"]) is None
        assert store.get_by_path("Keep/Keep.2001.mkv",
                                 library_id=lib["id"]) is not None
        # 级联：断点与增量状态一起清
        assert store.get_progress(int(gone["id"]), kind="movie") is None
        assert store.get_scan_state("Gone/Gone.2002.mkv",
                                    library_id=lib["id"]) is None
        # 聚合条目回传给 summary
        gc = [r for r in out if r.get("status") == "removed_movie"
              and int(r.get("library_id")) == lib["id"]]
        assert gc and sum(int(r.get("count") or 0) for r in gc) == 1
        summary = jobs_router._scan_summary(out)
        assert summary["counts"].get("removed_movie", 0) >= 1
        assert summary["by_library"][lib["id"]].get("removed_movie", 0) == 1
    finally:
        _teardown(lib)


def test_movie_dir_rename_leaves_no_ghost(tmp_path, monkeypatch):
    """整目录改名→扫→旧行零残留、新路径建行，总数不变。"""
    monkeypatch.setattr(scanner.scan, "search_with_fallback",
                        lambda title, year: (None, title, False))
    lib, root = _lib(tmp_path)
    try:
        _touch(root, "Old Name/Old Name.2003.mkv")
        scanner.scan_all(library_id=lib["id"])
        assert store.get_by_path("Old Name/Old Name.2003.mkv",
                                 library_id=lib["id"]) is not None
        (root / "Old Name").rename(root / "New Name")
        # 文件名也跟目录一起变（用户手改的常见形态）
        (root / "New Name" / "Old Name.2003.mkv").rename(
            root / "New Name" / "New Name.2003.mkv")
        out = scanner.scan_all(library_id=lib["id"])
        rows = [m for m in store.list_movies(grouped=False, limit=100000)
                if int(m.get("library_id") or 0) == lib["id"]]
        assert len(rows) == 1
        assert rows[0]["file_path"] == "New Name/New Name.2003.mkv"
        assert store.get_by_path("Old Name/Old Name.2003.mkv",
                                 library_id=lib["id"]) is None
        assert any(r.get("status") == "removed_movie" for r in out)
    finally:
        _teardown(lib)


def test_cancelled_scan_skips_movie_gc(tmp_path, monkeypatch):
    """取消的扫描不删行：幽灵行在 should_stop=True 时保留。"""
    monkeypatch.setattr(scanner.scan, "search_with_fallback",
                        lambda title, year: (None, title, False))
    lib, root = _lib(tmp_path)
    try:
        ghost = store.upsert_movie_by_path("ghost/Cancelled.2004.mkv",
                                           library_id=lib["id"])
        out = scanner.scan_all(library_id=lib["id"],
                               should_stop=lambda: True)
        assert out == []
        assert store.get_by_path("ghost/Cancelled.2004.mkv",
                                 library_id=lib["id"])["id"] == ghost
    finally:
        _teardown(lib)


def test_scan_tv_lib_ids_only_tv(tmp_path):
    """链式刮削作用域只含 TV 库。"""
    movie_lib, _mroot = _lib(tmp_path, kind="movie")
    tv_lib, _troot = _lib(tmp_path, kind="tv")
    try:
        only_tv = jobs_router._scan_tv_lib_ids()
        assert tv_lib["id"] in only_tv and movie_lib["id"] not in only_tv
        assert jobs_router._scan_tv_lib_ids(
            library_id=movie_lib["id"]) == []
        assert jobs_router._scan_tv_lib_ids(
            library_id=tv_lib["id"]) == [tv_lib["id"]]
    finally:
        _teardown(movie_lib)
        _teardown(tv_lib)


def test_chained_tv_scrape_merges_summary(tmp_path, monkeypatch):
    """扫描 worker 在 TV 作用域下链式刮削并合并 summary；已有任务在跑则跳过。"""
    import threading

    lib, root = _lib(tmp_path, kind="tv")
    try:
        d = root / "Some Show (2020)" / "Season 01"
        d.mkdir(parents=True)
        (d / "Some.Show.S01E01.mkv").write_bytes(b"x")
        calls = []

        def _fake_scrape(library_ids=None, force=False, should_stop=None,
                         progress_cb=None):
            calls.append(list(library_ids or []))
            assert force is False
            return [{"show_id": 1, "status": "ok", "library_id": lib["id"]}]

        monkeypatch.setattr("app.scanner.tv_persist.scrape_pending", _fake_scrape)
        monkeypatch.setattr(jobs_router._TV_JOBS, "running", lambda: None)
        monkeypatch.setattr(jobs_router._SCAN_JOBS, "get",
                            lambda jid: {"state": "running"})
        seen = {}

        def _update(jid, **kw):
            seen.update(kw)

        monkeypatch.setattr(jobs_router._SCAN_JOBS, "update", _update)
        jobs_router._scan_worker("jid-test", library_id=lib["id"])
        assert calls and calls[0] == [lib["id"]]
        assert seen.get("state") == "done"
        assert seen["summary"]["tv_scrape"]["counts"] == {"ok": 1}

        # 已有 tv-scrape 在跑 → 跳过且不触网
        calls.clear()
        monkeypatch.setattr(jobs_router._TV_JOBS, "running",
                            lambda: {"job_id": "tv-running"})
        seen.clear()
        jobs_router._scan_worker("jid-test2", library_id=lib["id"])
        assert calls == []
        assert seen["summary"]["tv_scrape"] == {"skipped": "running"}
    finally:
        _teardown(lib)
