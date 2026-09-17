"""B11 审计补齐（评审 R02-D2/D3/B7）：WAL/busy_timeout、FTS 条件重建、补索引。"""
from app import store
from app.store import search as store_search


def test_wal_and_busy_timeout():
    with store._lock, store._conn() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert int(c.execute("PRAGMA busy_timeout").fetchone()[0]) == 5000


def test_fts_needs_rebuild_detects_mismatch():
    store.init_db()
    mid = store.upsert_movie_by_path("robust/Indexed.Movie.2001.mkv")
    store.update_movie_meta(mid, title="Indexed Movie", year=2001)   # 内含 resync_fts
    assert store_search.fts_needs_rebuild() is False
    with store._lock, store._conn() as c:
        c.execute("DELETE FROM movies_fts")
    assert store_search.fts_needs_rebuild() is True    # 行数不一致 → 启动时重建
    store.rebuild_fts()
    assert store_search.fts_needs_rebuild() is False


def test_person_and_extras_indexes_exist():
    with store._lock, store._conn() as c:
        p_idx = {r[1] for r in c.execute("PRAGMA index_list(movie_person)").fetchall()}
        e_idx = {r[1] for r in c.execute("PRAGMA index_list(extras)").fetchall()}
    assert "idx_movie_person_person" in p_idx
    assert "idx_extras_movie" in e_idx


def test_dead_settings_api_removed():
    assert not hasattr(store, "get_all_settings")
