"""合集成員跟随删片：delete_movie 家族同步清理 + 孤儿 prune 回填。"""
from app import library_paths, store
from app.store._base import _conn, _lock


def _movie(rel, tmdb_id=None, title="T"):
    mid = store.upsert_movie_by_path(rel)
    meta = {"title": title, "year": 2000}
    if tmdb_id:
        meta["tmdb_id"] = tmdb_id
    store.update_movie_meta(mid, **meta)
    return mid


def test_delete_movie_cleans_collection_members():
    a = _movie("prune-a.mkv", tmdb_id=991001, title="PruneA")
    b = _movie("prune-b.mkv", tmdb_id=991002, title="PruneB")
    col = store.create_collection("剪枝合集")
    try:
        assert store.add_collection_members(col["id"], [a, b])["total"] == 2
        assert store.delete_movie(a)
        got = store.get_collection(col["id"])
        assert [m["id"] for m in got["members"]] == [b]
        assert got["member_count"] == 1
    finally:
        store.delete_collection(col["id"])
        store.delete_movie(b)


def test_delete_movies_not_in_cleans_collection_members(tmp_path):
    """GC 按库执行：用隔离库，避免清掉套件里其它用例留在默认库的影片。"""
    root = tmp_path / "gccol"
    root.mkdir()
    lib = store.create_library(name=f"gc-col-{tmp_path.name}", path=str(root))
    library_paths.invalidate_cache()

    def _lib_movie(rel, tmdb_id, title):
        mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
        store.update_movie_meta(mid, title=title, year=2000, tmdb_id=tmdb_id)
        return mid

    a = _lib_movie("gc-a.mkv", 992001, "GcA")
    b = _lib_movie("gc-b.mkv", 992002, "GcB")
    col = store.create_collection("GC合集", media_library_id=lib["media_library_id"])
    try:
        store.add_collection_members(col["id"], [a, b])
        ma = store.get_movie(a)
        assert store.delete_movies_not_in([ma["file_path"]], library_id=lib["id"]) == 1
        got = store.get_collection(col["id"])
        assert [m["id"] for m in got["members"]] == [a]
    finally:
        store.delete_collection(col["id"])
        store.delete_movie(a)
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()


def test_prune_backfills_legacy_dangling_members():
    a = _movie("legacy-a.mkv", tmdb_id=993001, title="LegacyA")
    col = store.create_collection("遗留合集")
    try:
        store.add_collection_members(col["id"], [a])
        # 模拟 prune 前的老数据：成员指向已不存在的影片（绕过外键）
        with _lock, _conn() as c:
            c.execute("INSERT OR IGNORE INTO collection_members"
                      "(collection_id, movie_tmdb_id, movie_id, sort_order, added_at)"
                      " VALUES(?, ?, ?, ?, ?)", (col["id"], 993999, None, 0, 0))
            c.execute("INSERT OR IGNORE INTO collection_members"
                      "(collection_id, movie_tmdb_id, movie_id, sort_order, added_at)"
                      " VALUES(?, ?, ?, ?, ?)", (col["id"], None, -4242, 0, 0))
        preview = store.prune_dangling_members(dry_run=True)
        assert preview["total"] >= 2
        done = store.prune_dangling_members(dry_run=False)
        assert done["removed"] >= 2
        got = store.get_collection(col["id"])
        assert [m["id"] for m in got["members"]] == [a]
        # 全是孤儿的合集上报 empty_ids（不自动删除，由用户手动处理）
        with _lock, _conn() as c:
            c.execute("DELETE FROM collection_members WHERE collection_id=?", (col["id"],))
            c.execute("INSERT OR IGNORE INTO collection_members"
                      "(collection_id, movie_tmdb_id, movie_id, sort_order, added_at)"
                      " VALUES(?, ?, ?, ?, ?)", (col["id"], 993998, None, 0, 0))
        preview2 = store.prune_dangling_members(dry_run=True)
        assert col["id"] in preview2["empty_ids"]
        done2 = store.prune_dangling_members(dry_run=False)
        assert col["id"] in done2["empty_ids"]
        assert store.get_collection(col["id"])["members"] == []
    finally:
        store.delete_collection(col["id"])
        store.delete_movie(a)
