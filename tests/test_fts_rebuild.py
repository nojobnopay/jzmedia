"""rebuild_fts 自愈收敛：旁路删行后的残留 FTS 行必须被清除。

回归来源：NAS 迁移用 SQL 直删本地库/待评分库影片行后，movies_fts 仍残留
701 行（movies 仅 370），rebuild_fts() 跑完行数依然不一致，
fts_needs_rebuild() 永久为 True，搜索可命中已不存在的影片。
"""
from app import store
from app.store import _base


def _fts_count(rowid: int) -> int:
    with _base._lock, _base._conn() as c:
        return int(c.execute(
            "SELECT COUNT(*) FROM movies_fts WHERE rowid=?", (rowid,)).fetchone()[0])


def test_rebuild_prunes_orphan_fts_rows():
    m1 = store.upsert_movie_by_path("fts/orphan-keep.mkv")
    m2 = store.upsert_movie_by_path("fts/orphan-gone.mkv")
    store.update_movie_meta(m1, title="FtsKeepUnique", title_auto=0)
    store.update_movie_meta(m2, title="FtsGoneUnique", title_auto=0)
    store.resync_fts(m1)
    store.resync_fts(m2)
    assert _fts_count(m1) == 1 and _fts_count(m2) == 1

    # 旁路删行（绕过 delete_movie 的 FTS 级联，模拟迁移/手工 SQL）
    with _base._lock, _base._conn() as c:
        c.execute("DELETE FROM movies WHERE id=?", (m2,))
    # 残留行仍在（逐行 resync 够不着它；此处不断言全局 needs_rebuild，
    # 其他用例的未同步行会影响全局计数，只验证本行残留与最终收敛）
    assert _fts_count(m2) == 1

    assert store.rebuild_fts() >= 1
    assert not store.fts_needs_rebuild()
    assert _fts_count(m2) == 0
    assert _fts_count(m1) == 1
    # 残留标题不再可搜，存活标题仍可搜
    assert m2 not in {m["id"] for m in store.search_fts("FtsGoneUnique", limit=50)}
    assert m1 in {m["id"] for m in store.search_fts("FtsKeepUnique", limit=50)}

    store.delete_movie(m1)
