"""B9/R02-D1：user_version 迁移框架（旧库升级/幂等/种子）回归网。"""
import sqlite3

from app.store import _base

# 代表“最老生产库”形态：迁移新增列全部缺失，其余原始列齐全
LEGACY_SCHEMA = """
CREATE TABLE movies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_path TEXT UNIQUE NOT NULL,
  title TEXT DEFAULT '',
  original_title TEXT DEFAULT '',
  year INTEGER,
  overview TEXT DEFAULT '',
  overview_override TEXT DEFAULT '',
  tmdb_id INTEGER,
  imdb_id TEXT DEFAULT '',
  tmdb_rating REAL,
  douban_rating REAL,
  custom_rating REAL,
  poster_path TEXT DEFAULT '',
  genres TEXT DEFAULT '[]',
  tags TEXT DEFAULT '[]',
  updated_at INTEGER DEFAULT 0
);
CREATE TABLE tmdb_cache (
  tmdb_id INTEGER PRIMARY KEY,
  fetched_at INTEGER DEFAULT 0
);
CREATE TABLE media_info (movie_id INTEGER PRIMARY KEY);
CREATE TABLE persons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tmdb_id INTEGER UNIQUE,
  name TEXT DEFAULT ''
);
CREATE TABLE movie_person (
  movie_id INTEGER NOT NULL,
  person_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  character_name TEXT DEFAULT '',
  cast_order INTEGER DEFAULT 99,
  PRIMARY KEY (movie_id, person_id, role)
);
"""


def _init_legacy(dbp):
    with sqlite3.connect(dbp) as c:
        c.executescript(LEGACY_SCHEMA)
        c.execute("INSERT INTO movies(file_path, title) VALUES('old/a.mkv', 'A')")
        c.execute("INSERT INTO media_info(movie_id) VALUES(1)")
        c.execute("PRAGMA user_version = 0")


def test_migration_from_legacy_db(tmp_path, monkeypatch):
    dbp = tmp_path / "old.db"
    _init_legacy(dbp)
    monkeypatch.setattr(_base, "DB_PATH", str(dbp))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)

    _base.init_db()   # 迁移 + 种子 + FTS 重建

    with sqlite3.connect(dbp) as c:
        c.row_factory = sqlite3.Row
        mv = {r[1] for r in c.execute("PRAGMA table_info(movies)")}
        pv = {r[1] for r in c.execute("PRAGMA table_info(persons)")}
        assert {"person_names", "origin_country", "region", "edition", "spec",
                "original_file_path", "watched", "watched_at", "media_type",
                "library_id", "match_source", "nfo_hash"} <= mv
        assert {"avatar", "biography", "bio_fetched_at", "profile_tmdb_path"} <= pv
        assert c.execute("PRAGMA user_version").fetchone()[0] == _base.SCHEMA_VERSION
        assert c.execute("SELECT original_file_path FROM movies").fetchone()[0] == "old/a.mkv"
        # 索引已建
        idx = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"idx_movies_tmdb", "idx_movies_region", "idx_movies_watched",
                "idx_movies_library"} <= idx

        # v12：默认库播种 + 存量行归入库 + 离线索引/库列
        libs = [dict(r) for r in c.execute(
            "SELECT id, name, kind, source, path FROM libraries")]
        assert len(libs) == 1 and libs[0]["id"] == _base.DEFAULT_LIBRARY_ID
        assert libs[0]["kind"] == "movie" and libs[0]["source"] == "local"
        assert c.execute("SELECT library_id FROM movies").fetchone()[0] == _base.DEFAULT_LIBRARY_ID
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"libraries", "match_index", "match_index_fts",
                "tv_shows", "tv_episodes"} <= tables
        assert "library_id" in {r[1] for r in c.execute("PRAGMA table_info(extras)")}
        assert "library_id" in {r[1] for r in c.execute("PRAGMA table_info(scan_state)")}

        # v13：media_info/playback_progress 复合键，存量行映射为 movie
        mi_cols = {r[1] for r in c.execute("PRAGMA table_info(media_info)")}
        assert {"kind", "item_id"} <= mi_cols
        assert c.execute("SELECT kind, item_id FROM media_info").fetchone()[:] == (
            "movie", 1)
        pp_cols = {r[1] for r in c.execute("PRAGMA table_info(playback_progress)")}
        assert {"kind", "item_id"} <= pp_cols

    _base.init_db()   # 二次执行幂等
    with sqlite3.connect(dbp) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == _base.SCHEMA_VERSION
        assert c.execute("SELECT COUNT(*) FROM libraries").fetchone()[0] == 1


def test_fresh_db_is_at_current_version(monkeypatch):
    # 会话库（conftest 已 init）应已是当前版本，且查询不重跑种子
    with sqlite3.connect(_base.DB_PATH) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == _base.SCHEMA_VERSION
