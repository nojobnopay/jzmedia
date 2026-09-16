"""B6-CRYPTO：摘要算法与 SQLite 特性自检。"""
import sqlite3

from app import store
from app.caps import caps_hash


def test_caps_hash_stable_blake2_12hex():
    a = caps_hash({"video": {"h264": True}, "audio": {"aac": True}})
    b = caps_hash({"video": {"h264": True}, "audio": {"aac": True}})
    assert a == b and len(a) == 12
    assert all(ch in "0123456789abcdef" for ch in a)
    assert a != caps_hash({"video": {"h264": False}, "audio": {"aac": True}})
    assert caps_hash(None) == caps_hash({})


def test_init_db_fts_probe_not_persisted():
    store.init_db()          # 幂等；缺 JSON1/FTS5 会在此抛 RuntimeError
    with sqlite3.connect(store.DB_PATH) as c:
        row = c.execute(
            "SELECT name FROM sqlite_master WHERE name='__fts_probe'").fetchone()
    assert row is None
