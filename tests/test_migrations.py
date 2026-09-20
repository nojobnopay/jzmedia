"""B9/R02-D1：user_version 迁移框架（旧库升级/幂等/种子）回归网。"""
import os
import sqlite3

import pytest

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

        # v12 默认库播种 + 存量行归入库 + 离线索引/库列（v17 后为 媒体库→视频库)
        medias = [dict(r) for r in c.execute(
            "SELECT id, name, source, path FROM media_libraries")]
        assert len(medias) == 1 and medias[0]["id"] == _base.DEFAULT_LIBRARY_ID
        assert medias[0]["source"] == "local"
        libs = [dict(r) for r in c.execute(
            "SELECT id, media_library_id, name, kind, subpath, path FROM libraries")]
        assert len(libs) == 1 and libs[0]["id"] == _base.DEFAULT_LIBRARY_ID
        assert libs[0]["kind"] == "movie" and libs[0]["subpath"] == ""
        assert libs[0]["media_library_id"] == _base.DEFAULT_LIBRARY_ID
        assert c.execute("SELECT library_id FROM movies").fetchone()[0] == _base.DEFAULT_LIBRARY_ID
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"media_libraries", "libraries", "match_index", "match_index_fts",
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


def test_m16_to_v18_collections_media_level(tmp_path, monkeypatch):
    """v16→v18：collections 从视频库级迁到媒体库级（最终 UNIQUE(media_library_id, name)）。"""
    dbp = tmp_path / "v15.db"
    monkeypatch.setattr(_base, "DB_PATH", str(dbp))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    _base.init_db()
    with sqlite3.connect(dbp) as c:
        # 还原 v15 形态（全局 UNIQUE）+ 一条存量行
        c.executescript("""
        DROP TABLE collections;
        CREATE TABLE collections (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT UNIQUE NOT NULL,
          overview TEXT DEFAULT '',
          poster_path TEXT DEFAULT '',
          tmdb_collection_id INTEGER,
          library_id INTEGER NOT NULL DEFAULT 1,
          created_at INTEGER DEFAULT 0,
          updated_at INTEGER DEFAULT 0
        );
        INSERT INTO collections(name, library_id, created_at, updated_at)
          VALUES('同名合集', 1, 1, 1);
        """)
        c.execute("PRAGMA user_version = 15")
    _base.init_db()
    with sqlite3.connect(dbp) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == _base.SCHEMA_VERSION
        cols = {r[1] for r in c.execute("PRAGMA table_info(collections)")}
        assert "media_library_id" in cols and "library_id" not in cols
        assert c.execute("SELECT name, media_library_id FROM collections").fetchone() == (
            "同名合集", 1)   # 存量行保留（视频库 1 → 媒体库 1）
        idx = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_collections_media" in idx
        # 跨媒体库同名可建，同媒体库同名仍拒绝
        c.execute("INSERT INTO collections(name, media_library_id) VALUES('同名合集', 2)")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO collections(name, media_library_id)"
                      " VALUES('同名合集', 1)")
    _base.init_db()   # 幂等


def test_v18_merges_same_name_collections_in_same_media(tmp_path, monkeypatch):
    """v18：同一媒体库内两个视频库的同名合集合并（成员并集，保留最早 id）。"""
    dbp = tmp_path / "v17-collections.db"
    monkeypatch.setattr(_base, "DB_PATH", str(dbp))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    _base.init_db()
    with sqlite3.connect(dbp) as c:
        # 还原 v16 形态 collections（视频库级），同一媒体库下两个视频库各建同名合集
        c.executescript("""
        DROP TABLE collections;
        CREATE TABLE collections (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          overview TEXT DEFAULT '',
          poster_path TEXT DEFAULT '',
          tmdb_collection_id INTEGER,
          library_id INTEGER NOT NULL DEFAULT 1,
          created_at INTEGER DEFAULT 0,
          updated_at INTEGER DEFAULT 0,
          UNIQUE(library_id, name)
        );
        """)
        c.execute("INSERT INTO libraries(id, media_library_id, name, kind, subpath, path,"
                  " created_at, updated_at) VALUES(2, 1, 'Unrated', 'movie', 'Unrated',"
                  " '/tmp/x', 0, 0)")
        c.execute("INSERT INTO collections(id, name, library_id, created_at, updated_at)"
                  " VALUES(10, '同名合集', 1, 1, 1)")
        c.execute("INSERT INTO collections(id, name, library_id, created_at, updated_at)"
                  " VALUES(11, '同名合集', 2, 2, 2)")
        c.execute("INSERT INTO collection_members(collection_id, movie_tmdb_id, movie_id)"
                  " VALUES(10, 501, NULL)")
        c.execute("INSERT INTO collection_members(collection_id, movie_tmdb_id, movie_id)"
                  " VALUES(11, 502, NULL)")
        c.execute("PRAGMA user_version = 17")
    _base.init_db()
    with sqlite3.connect(dbp) as c:
        rows = c.execute("SELECT id, name, media_library_id FROM collections").fetchall()
        assert rows == [(10, "同名合集", 1)]   # 合并保留最早 id
        mems = c.execute("SELECT movie_tmdb_id FROM collection_members"
                         " ORDER BY movie_tmdb_id").fetchall()
        assert [m[0] for m in mems] == [501, 502]   # 成员并集
        # 同媒体库同名拒绝；不同媒体库可同名
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO collections(name, media_library_id)"
                      " VALUES('同名合集', 1)")
        c.execute("INSERT INTO collections(name, media_library_id) VALUES('同名合集', 2)")
    _base.init_db()   # 幂等


_V16_LIBRARIES_DDL = """
CREATE TABLE libraries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL DEFAULT 'movie',
  source TEXT NOT NULL DEFAULT 'local',
  path TEXT NOT NULL,
  read_only INTEGER NOT NULL DEFAULT 0,
  auto_mount INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  naming_profile TEXT NOT NULL DEFAULT 'kodi',
  artwork_mode TEXT NOT NULL DEFAULT 'nfo',
  organize_target TEXT NOT NULL DEFAULT '电影',
  inbox_dir TEXT NOT NULL DEFAULT '待整理',
  metadata_providers TEXT NOT NULL DEFAULT '',
  smb_host TEXT DEFAULT '', smb_share TEXT DEFAULT '', smb_subpath TEXT DEFAULT '',
  smb_domain TEXT DEFAULT '', smb_username TEXT DEFAULT '', smb_password TEXT DEFAULT '',
  smb_options TEXT DEFAULT '', smb_connect_host TEXT DEFAULT '',
  nfs_export TEXT DEFAULT '', nfs_password TEXT DEFAULT '', nfs_options TEXT DEFAULT '',
  storage_identity TEXT DEFAULT '',
  last_status TEXT DEFAULT '', last_error TEXT DEFAULT '', last_check_at INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL DEFAULT 0
);
"""


def _make_v16(dbp):
    """当前库 → 还原 v16 扁平 libraries（去掉 media_libraries），供 v17 迁移用例。"""
    with sqlite3.connect(dbp) as c:
        c.executescript("DROP TABLE media_libraries; DROP TABLE libraries;")
        c.executescript(_V16_LIBRARIES_DDL)


def test_v16_remote_library_splits_subpath(tmp_path, monkeypatch):
    """v17：远程库旧 smb_subpath 最后一段下沉为视频库；媒体库根保留其余。"""
    dbp = tmp_path / "v16-remote.db"
    monkeypatch.setattr(_base, "DB_PATH", str(dbp))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    _base.init_db()
    _make_v16(dbp)
    with sqlite3.connect(dbp) as c:
        c.execute("INSERT INTO libraries(id, name, kind, source, path, smb_host,"
                  " smb_share, smb_subpath, created_at, updated_at)"
                  " VALUES(7, 'NAS-电影', 'movie', 'smb', '/wrong/path',"
                  " 'NAS', 'video', 'Movies', 0, 0)")
        c.execute("PRAGMA user_version = 13")
    _base.init_db()
    from app.db import mount_point
    with sqlite3.connect(dbp) as c:
        c.row_factory = sqlite3.Row
        m = dict(c.execute("SELECT * FROM media_libraries WHERE id=7").fetchone())
        assert m["smb_subpath"] == "" and m["path"] == mount_point(7)
        assert m["storage_identity"] == "smb:nas/video/"
        v = dict(c.execute("SELECT * FROM libraries WHERE id=7").fetchone())
        assert v["media_library_id"] == 7 and v["name"] == "Movies"
        assert v["subpath"] == "Movies"
        assert v["path"] == os.path.join(mount_point(7), "Movies")
    _base.init_db()   # 幂等


def test_v16_local_library_auto_splits_common_subdir(tmp_path, monkeypatch):
    """v17：本地库记录全部在同一顶层子目录时自动拆分并改写相对路径。"""
    dbp = tmp_path / "v16-local.db"
    monkeypatch.setattr(_base, "DB_PATH", str(dbp))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    _base.init_db()
    _make_v16(dbp)
    root = tmp_path / "media"
    root.mkdir()
    with sqlite3.connect(dbp) as c:
        c.execute("INSERT INTO libraries(id, name, kind, source, path, created_at,"
                  " updated_at) VALUES(3, 'media', 'movie', 'local', ?, 0, 0)",
                  (str(root),))
        c.execute("INSERT INTO movies(file_path, library_id, title)"
                  " VALUES('电影/A (2020)/a.mkv', 3, 'A')")
        c.execute("INSERT INTO scan_state(file_path, library_id, mtime, size, status,"
                  " updated_at) VALUES('电影/A (2020)/a.mkv', 3, 0, 0, '', 0)")
        c.execute("PRAGMA user_version = 16")
    _base.init_db()
    with sqlite3.connect(dbp) as c:
        c.row_factory = sqlite3.Row
        v = dict(c.execute("SELECT * FROM libraries WHERE id=3").fetchone())
        assert v["name"] == "电影" and v["subpath"] == "电影"
        assert v["path"] == os.path.join(str(root), "电影")
        assert c.execute("SELECT file_path FROM movies WHERE library_id=3").fetchone()[0] == (
            "A (2020)/a.mkv")
        assert c.execute("SELECT file_path FROM scan_state WHERE library_id=3").fetchone()[0] == (
            "A (2020)/a.mkv")


def test_m14_normalizes_remote_library_path(tmp_path, monkeypatch):
    """v14+v17：存量远程库 path 归一为固定挂载点（挂载与扫描同源）。"""
    dbp = tmp_path / "v13.db"
    monkeypatch.setattr(_base, "DB_PATH", str(dbp))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    _base.init_db()
    _make_v16(dbp)
    with sqlite3.connect(dbp) as c:
        c.execute("INSERT INTO libraries(id, name, kind, source, path, created_at,"
                  " updated_at) VALUES(9, 'smb-test', 'movie', 'smb', '/wrong/path', 0, 0)")
        c.execute("PRAGMA user_version = 13")
    _base.init_db()
    from app.db import mount_point
    with sqlite3.connect(dbp) as c:
        row = c.execute("SELECT id, path FROM media_libraries WHERE name='smb-test'").fetchone()
        assert row[1] == mount_point(row[0])
