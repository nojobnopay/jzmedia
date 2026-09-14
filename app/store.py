"""SQLite存储层（标准库sqlite3 + FTS5全文检索：片名/原名/简介/演员/标签/类型）"""
import json
import os
import sqlite3
import threading
import time

from .config import settings
from .db import DB_PATH, POSTER_DIR, ensure_dirs

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
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
  genre_ids TEXT DEFAULT '[]',
  tags TEXT DEFAULT '[]',
  person_names TEXT DEFAULT '',
  origin_country TEXT DEFAULT '',
  origin_countries TEXT DEFAULT '[]',
  original_language TEXT DEFAULT '',
  region TEXT DEFAULT '',
  media_type TEXT DEFAULT 'movie',
  edition TEXT DEFAULT '',
  spec TEXT DEFAULT '',
  original_file_path TEXT DEFAULT '',
  needs_review INTEGER DEFAULT 0,
  watched INTEGER DEFAULT 0,
  watched_at INTEGER DEFAULT 0,
  updated_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS persons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tmdb_id INTEGER UNIQUE,
  name TEXT DEFAULT '',
  avatar TEXT DEFAULT '',
  biography TEXT DEFAULT '',
  birthday TEXT DEFAULT '',
  place_of_birth TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS movie_person (
  movie_id INTEGER NOT NULL,
  person_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  character_name TEXT DEFAULT '',
  cast_order INTEGER DEFAULT 99,
  PRIMARY KEY (movie_id, person_id, role)
);
-- 花絮归属：file_path 唯一；movie_id 为 NULL 表示未归属（orphan）；kind 见 scanner.extra_kind
CREATE TABLE IF NOT EXISTS extras (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_path TEXT UNIQUE NOT NULL,
  movie_id INTEGER,
  kind TEXT DEFAULT 'extra',
  updated_at INTEGER DEFAULT 0
);
-- TMDB远端镜像：以 tmdb_id 为键的稳定缓存，不受 file_path/tags/评分等本地改动影响。
-- movies 表的 TMDB 列只是它的物化副本，只经 copy_tmdb_to_movie() 复制。
CREATE TABLE IF NOT EXISTS tmdb_cache (
  tmdb_id INTEGER PRIMARY KEY,
  title TEXT DEFAULT '',
  original_title TEXT DEFAULT '',
  year INTEGER,
  overview TEXT DEFAULT '',
  imdb_id TEXT DEFAULT '',
  tmdb_rating REAL,
  genres TEXT DEFAULT '[]',
  genre_ids TEXT DEFAULT '[]',
  origin_country TEXT DEFAULT '',
  origin_countries TEXT DEFAULT '[]',
  original_language TEXT DEFAULT '',
  region TEXT DEFAULT '',
  media_type TEXT DEFAULT 'movie',
  poster_tmdb_path TEXT DEFAULT '',
  credits TEXT DEFAULT '{"cast":[],"crew":[]}',
  collection_tmdb_id INTEGER,
  collection_name TEXT DEFAULT '',
  collection_poster_path TEXT DEFAULT '',
  collection_checked_at INTEGER DEFAULT 0,
  fetched_at INTEGER DEFAULT 0
);
-- 手工合集：成员以海报粒度存放（有 tmdb_id 存 movie_tmdb_id，无则存 movie_id），与海报墙分组键一致
CREATE TABLE IF NOT EXISTS collections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  overview TEXT DEFAULT '',
  poster_path TEXT DEFAULT '',
  tmdb_collection_id INTEGER,
  created_at INTEGER DEFAULT 0,
  updated_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS collection_members (
  collection_id INTEGER NOT NULL,
  movie_tmdb_id INTEGER,
  movie_id INTEGER,
  sort_order INTEGER DEFAULT 0,
  added_at INTEGER DEFAULT 0,
  PRIMARY KEY (collection_id, movie_tmdb_id, movie_id)
);
CREATE INDEX IF NOT EXISTS idx_members_collection ON collection_members(collection_id);
CREATE INDEX IF NOT EXISTS idx_members_tmdb ON collection_members(movie_tmdb_id);
CREATE INDEX IF NOT EXISTS idx_members_movie ON collection_members(movie_id);
-- 应用配置 KV（设置页可写）：TMDB 密钥/代理/语言等。DB 非空值优先于环境变量，
-- 缺 key/空串一律回落 env（.env 只做首次启动兜底）。
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT DEFAULT '',
  updated_at INTEGER DEFAULT 0
);
-- 在线播放：版本粒度媒体信息（ffprobe 本地派生，不进 TMDB 镜像，不进 FTS）。
-- movie_id 即 versions 行 id（每个文件版本一行），键稳定抗搬迁改名。
CREATE TABLE IF NOT EXISTS media_info (
  movie_id INTEGER PRIMARY KEY,
  container TEXT DEFAULT '',
  duration REAL DEFAULT 0,
  width INTEGER DEFAULT 0,
  height INTEGER DEFAULT 0,
  vcodec TEXT DEFAULT '',
  acodec TEXT DEFAULT '',
  vbitrate INTEGER DEFAULT 0,
  abitrate INTEGER DEFAULT 0,
  audio_json TEXT DEFAULT '[]',
  sub_json TEXT DEFAULT '[]',
  playable INTEGER DEFAULT 0,
  probe_error TEXT DEFAULT '',
  probed_at INTEGER DEFAULT 0
);
-- 在线播放：按版本 id 存断点（position/duration 秒），删版本行时级联清理。
CREATE TABLE IF NOT EXISTS playback_progress (
  version_id INTEGER PRIMARY KEY,
  position REAL DEFAULT 0,
  duration REAL DEFAULT 0,
  updated_at INTEGER DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS movies_fts USING fts5(
  title, original_title, overview, person_names, tags, genres,
  tokenize='unicode61'
);
DROP TRIGGER IF EXISTS movies_ai;
DROP TRIGGER IF EXISTS movies_ad;
DROP TRIGGER IF EXISTS movies_au;
"""


def _conn() -> sqlite3.Connection:
    ensure_dirs()
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


# TMDB镜像列（movies 中的物化副本，只能经 copy_tmdb_to_movie() 从 tmdb_cache 复制）
TMDB_FIELDS = {"title", "original_title", "year", "overview",
               "tmdb_id", "imdb_id", "tmdb_rating",
               "genres", "genre_ids",
               "origin_country", "origin_countries", "original_language",
               "region", "media_type"}
# 本地自有列（PATCH/rename/tags/评分等，只能经本地写路径修改，不碰 cache）
LOCAL_FIELDS = {"file_path", "title", "overview_override",
                "douban_rating", "custom_rating", "tags", "needs_review",
                "edition", "spec", "original_file_path",
                "watched", "watched_at"}


# 设置页可写的配置键白名单（与 config.effective_* 对应）
APP_SETTING_KEYS = {"tmdb_read_token", "tmdb_api_key", "tmdb_proxy",
                    "tmdb_language", "tmdb_image_base"}


def get_setting(key: str) -> str:
    """读单项应用配置（设置页写入的值）。缺 key/空串一律返回 ''，调用方回落 env。"""
    with _lock, _conn() as c:
        try:
            c.execute("CREATE TABLE IF NOT EXISTS app_settings ("
                      "key TEXT PRIMARY KEY, value TEXT DEFAULT '',"
                      " updated_at INTEGER DEFAULT 0)")
            row = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        except Exception:
            return ""
        if not row:
            return ""
        try:
            return row["value"] or ""
        except Exception:
            return ""


def get_all_settings() -> dict:
    """读出已存的配置项（仅返回白名单内、值非空的行）。"""
    with _lock, _conn() as c:
        try:
            c.execute("CREATE TABLE IF NOT EXISTS app_settings ("
                      "key TEXT PRIMARY KEY, value TEXT DEFAULT '',"
                      " updated_at INTEGER DEFAULT 0)")
            rows = c.execute("SELECT key, value FROM app_settings").fetchall()
        except Exception:
            return {}
    out: dict = {}
    for r in rows:
        try:
            k, v = r["key"], r["value"] or ""
        except Exception:
            continue
        if k in APP_SETTING_KEYS and v != "":
            out[k] = v
    return out


def set_setting(key: str, value: str) -> str:
    """写单项应用配置。空串表示清空（恢复跟随 env）。返回落库后的 strip 值。"""
    if key not in APP_SETTING_KEYS:
        raise ValueError(f"unknown setting: {key}")
    v = (value or "").strip()
    now = int(time.time())
    with _lock, _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS app_settings ("
                  "key TEXT PRIMARY KEY, value TEXT DEFAULT '',"
                  " updated_at INTEGER DEFAULT 0)")
        c.execute("INSERT INTO app_settings(key, value, updated_at) VALUES(?, ?, ?)"
                  " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                  " updated_at=excluded.updated_at",
                  (key, v, now))
    return v


def init_db() -> None:
    with _lock, _conn() as c:
        c.executescript(SCHEMA)
        cols = [r["name"] for r in c.execute("PRAGMA table_info(movies)")]
        for col, ddl in (
            ("person_names", "ALTER TABLE movies ADD COLUMN person_names TEXT DEFAULT ''"),
            ("needs_review", "ALTER TABLE movies ADD COLUMN needs_review INTEGER DEFAULT 0"),
            ("origin_country", "ALTER TABLE movies ADD COLUMN origin_country TEXT DEFAULT ''"),
            ("origin_countries", "ALTER TABLE movies ADD COLUMN origin_countries TEXT DEFAULT '[]'"),
            ("original_language", "ALTER TABLE movies ADD COLUMN original_language TEXT DEFAULT ''"),
            ("region", "ALTER TABLE movies ADD COLUMN region TEXT DEFAULT ''"),
            ("genre_ids", "ALTER TABLE movies ADD COLUMN genre_ids TEXT DEFAULT '[]'"),
            ("media_type", "ALTER TABLE movies ADD COLUMN media_type TEXT DEFAULT 'movie'"),
            ("edition", "ALTER TABLE movies ADD COLUMN edition TEXT DEFAULT ''"),
            ("spec", "ALTER TABLE movies ADD COLUMN spec TEXT DEFAULT ''"),
            ("original_file_path", "ALTER TABLE movies ADD COLUMN original_file_path TEXT DEFAULT ''"),
            ("watched", "ALTER TABLE movies ADD COLUMN watched INTEGER DEFAULT 0"),
            ("watched_at", "ALTER TABLE movies ADD COLUMN watched_at INTEGER DEFAULT 0"),
        ):
            if col not in cols:
                c.execute(ddl)
        pcols = [r["name"] for r in c.execute("PRAGMA table_info(persons)")]
        if "avatar" not in pcols:
            c.execute("ALTER TABLE persons ADD COLUMN avatar TEXT DEFAULT ''")
        for col, ddl in (
            ("biography", "ALTER TABLE persons ADD COLUMN biography TEXT DEFAULT ''"),
            ("birthday", "ALTER TABLE persons ADD COLUMN birthday TEXT DEFAULT ''"),
            ("place_of_birth", "ALTER TABLE persons ADD COLUMN place_of_birth TEXT DEFAULT ''"),
            ("profile_tmdb_path", "ALTER TABLE persons ADD COLUMN profile_tmdb_path TEXT DEFAULT ''"),
            ("fetched_at", "ALTER TABLE persons ADD COLUMN fetched_at INTEGER DEFAULT 0"),
            ("bio_fetched_at", "ALTER TABLE persons ADD COLUMN bio_fetched_at INTEGER DEFAULT 0"),
            ("bio_lang", "ALTER TABLE persons ADD COLUMN bio_lang TEXT DEFAULT ''"),
        ):
            if col not in pcols:
                c.execute(ddl)
        c.execute("CREATE INDEX IF NOT EXISTS idx_movies_year ON movies(year)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_movies_region ON movies(region)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_movies_origin ON movies(origin_country)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_movies_tmdb ON movies(tmdb_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_movies_watched ON movies(watched)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cache_fetched ON tmdb_cache(fetched_at)")
        # tmdb_cache 系列列自愈（老库无这几列时补上）
        ccols = [r["name"] for r in c.execute("PRAGMA table_info(tmdb_cache)")]
        for col, ddl in (
            ("collection_tmdb_id", "ALTER TABLE tmdb_cache ADD COLUMN collection_tmdb_id INTEGER"),
            ("collection_name", "ALTER TABLE tmdb_cache ADD COLUMN collection_name TEXT DEFAULT ''"),
            ("collection_poster_path", "ALTER TABLE tmdb_cache ADD COLUMN collection_poster_path TEXT DEFAULT ''"),
            ("collection_checked_at", "ALTER TABLE tmdb_cache ADD COLUMN collection_checked_at INTEGER DEFAULT 0"),
        ):
            if col not in ccols:
                c.execute(ddl)
        # 存量自愈：尚无原始路径的行用当前路径种子（老行=最早已知路径，新行由 upsert 写入真值）
        c.execute("UPDATE movies SET original_file_path=file_path "
                  "WHERE original_file_path IS NULL OR original_file_path=''")
        sql = (c.execute("SELECT sql FROM sqlite_master WHERE name='movies_fts'").fetchone() or [""])[0]
        if "content=" in sql:
            c.execute("DROP TABLE movies_fts")
            c.executescript(SCHEMA)
    seed_tmdb_cache_from_movies()
    rebuild_fts()


def _dump_list(v) -> str:
    try:
        return json.dumps(v or [], ensure_ascii=False)
    except Exception:
        return "[]"


def seed_tmdb_cache_from_movies() -> int:
    """离线种子：用 movies 现有行补 tmdb_cache 缺失项，不调网。
    credits 为空（人物链接已在 movie_person 中，新版本复用时走 sibling 复制），fetched_at=0 标记“本地种子”。"""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT *, MAX(updated_at) AS _u FROM movies WHERE tmdb_id IS NOT NULL "
            "GROUP BY tmdb_id").fetchall()
        inserted = 0
        for r in rows:
            tid = r["tmdb_id"]
            if tid is None:
                continue
            exists = c.execute("SELECT 1 FROM tmdb_cache WHERE tmdb_id=?", (tid,)).fetchone()
            if exists:
                continue
            c.execute(
                "INSERT INTO tmdb_cache(tmdb_id, title, original_title, year, overview,"
                " imdb_id, tmdb_rating, genres, genre_ids, origin_country, origin_countries,"
                " original_language, region, media_type, poster_tmdb_path, credits, fetched_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, r["title"] or "", r["original_title"] or "", r["year"],
                 r["overview"] or "", r["imdb_id"] or "", r["tmdb_rating"],
                 r["genres"] or "[]", r["genre_ids"] or "[]",
                 r["origin_country"] or "", r["origin_countries"] or "[]",
                 r["original_language"] or "", r["region"] or "",
                 r["media_type"] or "movie", "", '{"cast":[],"crew":[]}', 0))
            inserted += 1
        return inserted


def get_tmdb_cached(tmdb_id: int) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM tmdb_cache WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("genres", "genre_ids", "origin_countries"):
            try:
                v = json.loads(d.get(k) or "[]")
                d[k] = v if isinstance(v, list) else []
            except Exception:
                d[k] = []
        try:
            cr = json.loads(d.get("credits") or '{"cast":[],"crew":[]}')
            d["credits"] = cr if isinstance(cr, dict) else {"cast": [], "crew": []}
        except Exception:
            d["credits"] = {"cast": [], "crew": []}
        return d


def upsert_tmdb_cache(tmdb_id: int, meta: dict,
                      credits: dict | None = None,
                      poster_tmdb_path: str = "") -> bool:
    """写入镜像。无变化时仅刷新 fetched_at 并返回 False（调用方应跳过 movies 传播）。
    meta 为 meta_from_detail() 产出的 TMDB 列字典。返回 True=内容变化。
    每次成功写入都盖 collection_checked_at（本次抓取已确认系列状态，
    含“确认无系列”的阴性结论；失败抛异常走不到这里，下次继续排查）。"""
    now = int(time.time())
    genres_s = _dump_list(meta.get("genres"))
    genre_ids_s = _dump_list(meta.get("genre_ids"))
    origin_countries_s = _dump_list(meta.get("origin_countries"))
    credits_s = json.dumps(credits or {"cast": [], "crew": []}, ensure_ascii=False, sort_keys=True)
    poster_tmdb_path = poster_tmdb_path or ""
    col_id = meta.get("collection_tmdb_id")
    try:
        col_id = int(col_id) if col_id is not None else None
    except (TypeError, ValueError):
        col_id = None
    col_name = meta.get("collection_name") or ""
    col_poster = meta.get("collection_poster_path") or ""
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM tmdb_cache WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        if not row:
            c.execute(
                "INSERT INTO tmdb_cache(tmdb_id, title, original_title, year, overview,"
                " imdb_id, tmdb_rating, genres, genre_ids, origin_country, origin_countries,"
                " original_language, region, media_type, poster_tmdb_path, credits,"
                " collection_tmdb_id, collection_name, collection_poster_path,"
                " collection_checked_at, fetched_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tmdb_id, meta.get("title", "") or "", meta.get("original_title", "") or "",
                 meta.get("year"), meta.get("overview", "") or "",
                 meta.get("imdb_id", "") or "", meta.get("tmdb_rating"),
                 genres_s, genre_ids_s,
                 meta.get("origin_country", "") or "", origin_countries_s,
                 meta.get("original_language", "") or "", meta.get("region", "") or "",
                 meta.get("media_type", "") or "movie",
                 poster_tmdb_path, credits_s, col_id, col_name, col_poster, now, now))
            return True
        # 兼容老库：SELECT * 可能无新列
        try:
            old_col_id = row["collection_tmdb_id"]
        except Exception:
            old_col_id = None
        try:
            old_col_name = row["collection_name"] or ""
        except Exception:
            old_col_name = ""
        try:
            old_col_poster = row["collection_poster_path"] or ""
        except Exception:
            old_col_poster = ""
        same = (
            (row["title"] or "") == (meta.get("title", "") or "")
            and (row["original_title"] or "") == (meta.get("original_title", "") or "")
            and row["year"] == meta.get("year")
            and (row["overview"] or "") == (meta.get("overview", "") or "")
            and (row["imdb_id"] or "") == (meta.get("imdb_id", "") or "")
            and (row["tmdb_rating"] == meta.get("tmdb_rating"))
            and (row["genres"] or "[]") == genres_s
            and (row["genre_ids"] or "[]") == genre_ids_s
            and (row["origin_country"] or "") == (meta.get("origin_country", "") or "")
            and (row["origin_countries"] or "[]") == origin_countries_s
            and (row["original_language"] or "") == (meta.get("original_language", "") or "")
            and (row["region"] or "") == (meta.get("region", "") or "")
            and (row["media_type"] or "movie") == (meta.get("media_type", "") or "movie")
            and (row["poster_tmdb_path"] or "") == poster_tmdb_path
            and (row["credits"] or '{"cast":[],"crew":[]}') == credits_s
            and (old_col_id == col_id)
            and (old_col_name == col_name)
            and (old_col_poster == col_poster)
        )
        if same:
            c.execute("UPDATE tmdb_cache SET fetched_at=?, collection_checked_at=? WHERE tmdb_id=?",
                      (now, now, tmdb_id))
            return False
        c.execute(
            "UPDATE tmdb_cache SET title=?, original_title=?, year=?, overview=?,"
            " imdb_id=?, tmdb_rating=?, genres=?, genre_ids=?, origin_country=?,"
            " origin_countries=?, original_language=?, region=?, media_type=?,"
            " poster_tmdb_path=?, credits=?, collection_tmdb_id=?,"
            " collection_name=?, collection_poster_path=?,"
            " collection_checked_at=?, fetched_at=? WHERE tmdb_id=?",
            (meta.get("title", "") or "", meta.get("original_title", "") or "",
             meta.get("year"), meta.get("overview", "") or "",
             meta.get("imdb_id", "") or "", meta.get("tmdb_rating"),
             genres_s, genre_ids_s,
             meta.get("origin_country", "") or "", origin_countries_s,
             meta.get("original_language", "") or "", meta.get("region", "") or "",
             meta.get("media_type", "") or "movie",
             poster_tmdb_path, credits_s, col_id, col_name, col_poster, now, now, tmdb_id))
        return True


def list_movie_ids_by_tmdb(tmdb_id: int) -> list[int]:
    with _lock, _conn() as c:
        return [int(r["id"]) for r in
                c.execute("SELECT id FROM movies WHERE tmdb_id=? ORDER BY id", (tmdb_id,))]


def copy_tmdb_to_movie(movie_id: int, old_title: str | None = None) -> bool:
    """从 tmdb_cache 向单行 movies 复制 TMDB 列（不含 poster_path，海报由 scanner 按文件存在性处理）。
    标题保护：old_title=None（新建/离线补齐）时空标题才写入；old_title!=None（刷新路径）时
    仅当当前标题==old_title 或为空才跟随新标题，否则视为手工改过予以保留。返回是否实际写入。"""
    with _lock, _conn() as c:
        mrow = c.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not mrow or not mrow["tmdb_id"]:
            return False
        crow = c.execute("SELECT * FROM tmdb_cache WHERE tmdb_id=?",
                         (mrow["tmdb_id"],)).fetchone()
        if not crow:
            return False
        cur_title = (mrow["title"] or "")
        new_title = (crow["title"] or "")
        if old_title is None:
            want_title = not cur_title
        else:
            want_title = (not cur_title) or (cur_title == (old_title or ""))
        fields: dict = {
            "original_title": crow["original_title"] or "",
            "year": crow["year"],
            "overview": crow["overview"] or "",
            "tmdb_id": crow["tmdb_id"],
            "imdb_id": crow["imdb_id"] or "",
            "tmdb_rating": crow["tmdb_rating"],
            "genres": crow["genres"] or "[]",
            "genre_ids": crow["genre_ids"] or "[]",
            "origin_country": crow["origin_country"] or "",
            "origin_countries": crow["origin_countries"] or "[]",
            "original_language": crow["original_language"] or "",
            "region": crow["region"] or "",
            "media_type": crow["media_type"] or "movie",
        }
        if want_title:
            fields["title"] = new_title
    if not fields:
        return False
    # 走 update_movie_meta 以复用 updated_at+FTS 逻辑（调用方已判定确需写入）
    update_movie_meta(movie_id, **{k: (json.loads(v) if k in ("genres", "genre_ids", "origin_countries") and isinstance(v, str) else v)
                                   for k, v in fields.items()})
    return True


def rebuild_fts() -> int:
    """全量重建FTS（自愈：启动时调用，消除历史trigger残留）。"""
    with _lock, _conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM movies")]
    for mid in ids:
        resync_fts(mid)
    return len(ids)


def resync_fts(movie_id: int) -> None:
    """演员/标签/元数据变更后刷新该行的FTS（显式delete+insert，无trigger）。"""
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not row:
            c.execute("DELETE FROM movies_fts WHERE rowid=?", (movie_id,))
            return
        names = " ".join(
            r["name"] for r in c.execute(
                "SELECT p.name FROM persons p JOIN movie_person mp ON mp.person_id=p.id "
                "WHERE mp.movie_id=? ORDER BY mp.cast_order", (movie_id,))
        )
        c.execute("UPDATE movies SET person_names=? WHERE id=?", (names, movie_id))
        c.execute("DELETE FROM movies_fts WHERE rowid=?", (movie_id,))
        c.execute(
            "INSERT INTO movies_fts(rowid, title, original_title, overview,"
            " person_names, tags, genres) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (row["id"], row["title"], row["original_title"], row["overview"],
             names, row["tags"], row["genres"]))


def upsert_movie_by_path(file_path: str) -> int:
    with _lock, _conn() as c:
        c.execute("INSERT OR IGNORE INTO movies(file_path, updated_at) VALUES(?, ?)",
                  (file_path, int(time.time())))
        row = c.execute("SELECT id FROM movies WHERE file_path=?", (file_path,)).fetchone()
        mid = int(row["id"])
        # 原始路径审计：仅首次入库（空值）时写入，之后搬迁改 file_path 也不碰它
        c.execute("UPDATE movies SET original_file_path=? WHERE id=? "
                  "AND (original_file_path IS NULL OR original_file_path='')",
                  (file_path, mid))
        return mid


def update_movie_local(movie_id: int, **fields) -> None:
    """本地写专用：只允许 LOCAL_FIELDS（file_path/手工标题/覆盖简介/评分/tags/待确认），
    传入 TMDB 镜像列会被静默丢弃，从机制上保证路径/标签小改动不污染镜像。"""
    safe = {k: v for k, v in fields.items() if k in LOCAL_FIELDS}
    if safe:
        update_movie_meta(movie_id, **safe)


def update_movie_meta(movie_id: int, **fields) -> None:
    allowed = {"file_path", "title", "original_title", "year", "overview", "overview_override",
               "tmdb_id", "imdb_id", "tmdb_rating", "douban_rating", "custom_rating",
               "poster_path", "genres", "genre_ids", "tags", "needs_review",
               "watched", "watched_at",
               "origin_country", "origin_countries", "original_language",
               "region", "media_type", "edition", "spec", "original_file_path"}
    data = {k: (json.dumps(v, ensure_ascii=False) if k in ("genres", "genre_ids", "tags", "origin_countries") else v)
            for k, v in fields.items() if k in allowed}
    if not data:
        return
    data["updated_at"] = int(time.time())
    cols = ", ".join(f"{k}=?" for k in data)
    with _lock, _conn() as c:
        c.execute(f"UPDATE movies SET {cols} WHERE id=?", (*data.values(), movie_id))
    resync_fts(movie_id)


def upsert_person(tmdb_id: int, name: str, avatar: str | None = None,
                  profile_tmdb_path: str | None = None,
                  fetched_at: int | None = None) -> int:
    """人物镜像 upsert（以 tmdb_id 为键）。
    avatar 非 None 时更新（含 '-' 标记“确认无照片”，避免回填反复重试）；
    profile_tmdb_path 非 None 时更新（远端原图路径，用于感知远端换头像）；
    fetched_at 非 None 时更新（credits 来源时间）。"""
    now = int(time.time())
    with _lock, _conn() as c:
        c.execute("INSERT OR IGNORE INTO persons(tmdb_id, name) VALUES(?, ?)", (tmdb_id, name))
        c.execute("UPDATE persons SET name=? WHERE tmdb_id=?", (name, tmdb_id))
        if avatar is not None:
            c.execute("UPDATE persons SET avatar=? WHERE tmdb_id=?", (avatar, tmdb_id))
        if profile_tmdb_path is not None:
            c.execute("UPDATE persons SET profile_tmdb_path=? WHERE tmdb_id=?",
                      (profile_tmdb_path or "", tmdb_id))
        if fetched_at is not None:
            c.execute("UPDATE persons SET fetched_at=? WHERE tmdb_id=?",
                      (int(fetched_at) if fetched_at else now, tmdb_id))
        row = c.execute("SELECT id FROM persons WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        return int(row["id"])


def get_person_raw(tmdb_id: int) -> dict | None:
    """人物镜像原始行（含 fetched_at/bio_fetched_at 水位，不含作品列表）。"""
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM persons WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        return dict(row) if row else None


def persons_missing_avatar(movie_id: int) -> bool:
    """该影片是否还有未确认头像的关联人物（avatar 为空即未尝试过）。"""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM movie_person mp JOIN persons p ON p.id=mp.person_id "
            "WHERE mp.movie_id=? AND (p.avatar IS NULL OR p.avatar='')",
            (movie_id,)).fetchone()
        return int(row["n"]) > 0


def update_person_bio(tmdb_id: int, biography: str = "",
                      birthday: str = "", place_of_birth: str = "",
                      lang: str = "") -> None:
    """人物详情缓存写入（简介/生日/出生地）。无论有无结果都刷新 bio_fetched_at，
    空简介不再每次访问重试，只经手动刷新入口更新。"""
    now = int(time.time())
    with _lock, _conn() as c:
        c.execute("UPDATE persons SET biography=?, birthday=?, place_of_birth=?,"
                  " bio_fetched_at=?, bio_lang=? WHERE tmdb_id=?",
                  (biography or "", birthday or "", place_of_birth or "",
                   now, lang or "", tmdb_id))


def get_person(tmdb_id: int) -> dict | None:
    """人物详情＋库内作品（参演/执导分开，同 tmdb 去重，年份倒序）。"""
    with _lock, _conn() as c:
        prow = c.execute("SELECT * FROM persons WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        if not prow:
            return None
        p = dict(prow)
        acting, directing = [], []
        seen = set()
        for r in c.execute(
                "SELECT m.*, mp.role, mp.character_name, mp.cast_order FROM movies m "
                "JOIN movie_person mp ON mp.movie_id=m.id "
                "JOIN persons p ON p.id=mp.person_id "
                "WHERE p.tmdb_id=? ORDER BY mp.role, m.year IS NULL, m.year DESC",
                (tmdb_id,)):
            d = _row_to_dict(r)
            key = d.get("tmdb_id") or -d["id"]
            role = r["role"]
            gkey = (role, key)
            if gkey in seen:
                continue
            seen.add(gkey)
            item = {"id": d["id"], "title": d.get("title", ""), "year": d.get("year"),
                    "poster_path": d.get("poster_path", ""),
                    "tmdb_rating": d.get("tmdb_rating"),
                    "original_language": d.get("original_language", ""),
                    "character_name": r["character_name"] or ""}
            (acting if role == "actor" else directing).append(item)
        p["acting"] = acting
        p["directing"] = directing
        return p


def link_person(movie_id: int, person_id: int, role: str,
                character_name: str = "", cast_order: int = 99) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO movie_person(movie_id, person_id, role,"
                  " character_name, cast_order) VALUES(?, ?, ?, ?, ?)",
                  (movie_id, person_id, role, character_name, cast_order))


def clear_movie_persons(movie_id: int) -> None:
    """清空单片演职员关联（手动换绑到不同 tmdb_id 时调用，避免旧阵容残留）。"""
    with _lock, _conn() as c:
        c.execute("DELETE FROM movie_person WHERE movie_id=?", (movie_id,))


def get_movie_person_links(movie_id: int) -> list[dict]:
    """单片现有演职员关联（含 person.tmdb_id），供 sync 幂等比对。"""
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT p.tmdb_id AS person_tmdb_id, mp.role, mp.character_name, mp.cast_order"
            " FROM movie_person mp JOIN persons p ON p.id=mp.person_id"
            " WHERE mp.movie_id=?", (movie_id,))]


def copy_person_links(src_movie_id: int, dst_movie_id: int) -> int:
    """同 tmdb_id 多版本复用：把源行的 movie_person 原样复制到目标行（人物已存在，无需调网）。
    返回复制条数。"""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT person_id, role, character_name, cast_order FROM movie_person"
            " WHERE movie_id=?", (src_movie_id,)).fetchall()
        n = 0
        for r in rows:
            c.execute("INSERT OR IGNORE INTO movie_person(movie_id, person_id, role,"
                      " character_name, cast_order) VALUES(?, ?, ?, ?, ?)",
                      (dst_movie_id, r["person_id"], r["role"],
                       r["character_name"] or "", r["cast_order"]))
            n += 1
        return n


def find_sibling_with_persons(tmdb_id: int, exclude_movie_id: int) -> int | None:
    """找同 tmdb_id 下已有演职员关联的兄弟行，供新版本免网络复用人物。"""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT m.id FROM movies m WHERE m.tmdb_id=? AND m.id!=?"
            " AND EXISTS(SELECT 1 FROM movie_person mp WHERE mp.movie_id=m.id)"
            " ORDER BY m.updated_at DESC LIMIT 1", (tmdb_id, exclude_movie_id)).fetchone()
        return int(row["id"]) if row else None


def upsert_extra(file_path: str, movie_id: int | None,
                 kind: str = "extra") -> int:
    """花絮归属记录（按 file_path 幂等）。movie_id 为 None = 未归属。"""
    with _lock, _conn() as c:
        c.execute("INSERT OR IGNORE INTO extras(file_path, updated_at) VALUES(?, ?)",
                  (file_path, int(time.time())))
        c.execute("UPDATE extras SET movie_id=?, kind=?, updated_at=? WHERE file_path=?",
                  (movie_id, kind or "extra", int(time.time()), file_path))
        row = c.execute("SELECT id FROM extras WHERE file_path=?", (file_path,)).fetchone()
        return int(row["id"])


def list_extras_by_movie(movie_id: int) -> list[dict]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM extras WHERE movie_id=? ORDER BY file_path", (movie_id,))]


def list_orphan_extras() -> list[dict]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM extras WHERE movie_id IS NULL ORDER BY file_path")]


def list_all_extras() -> list[dict]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM extras ORDER BY file_path")]


def update_extra_movie(extra_id: int, movie_id: int) -> bool:
    """手工认领：orphan 花絮归到指定影片。返回行是否存在。"""
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM extras WHERE id=?", (extra_id,)).fetchone()
        if not row:
            return False
        c.execute("UPDATE extras SET movie_id=?, updated_at=? WHERE id=?",
                  (movie_id, int(time.time()), extra_id))
        return True


def delete_extra_by_path(file_path: str) -> bool:
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM extras WHERE file_path=?", (file_path,)).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM extras WHERE file_path=?", (file_path,))
        return True


def repath_extra_by_basename(basename: str, new_path: str,
                             movie_id: int | None, kind: str) -> dict | None:
    """已入库花絮被搬迁改路径后按 basename 认领：仅认领原文件已消失的行
    （同名不同文件不误认），更新路径+归属/kind，避免删建抖动。
    返回更新后的行，无可认领返回 None。"""
    import os as _os
    from .config import settings as _settings
    with _lock, _conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM extras").fetchall()]
    same = [r for r in rows if _os.path.basename(r["file_path"]) == basename]
    if not same:
        return None
    if any(r["file_path"] == new_path for r in same):
        return next(r for r in same if r["file_path"] == new_path)
    gone = [r for r in same if not _os.path.exists(
        _os.path.join(_settings.media_root, r["file_path"]))]
    if not gone:
        return None
    keep = sorted(gone, key=lambda r: r["id"])[0]
    with _lock, _conn() as c:
        try:
            c.execute("UPDATE extras SET file_path=?, movie_id=?, kind=?,"
                      " updated_at=? WHERE id=?",
                      (new_path, movie_id, kind or "extra",
                       int(time.time()), keep["id"]))
        except Exception:
            return keep
        for r in same:
            if r["id"] != keep["id"] and r["file_path"] != new_path:
                try:
                    c.execute("DELETE FROM extras WHERE id=?", (r["id"],))
                except Exception:
                    pass
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM extras WHERE id=?", (keep["id"],)).fetchone()
        return dict(row) if row else None


def find_movie_for_extra(title: str, year: int | None) -> dict | None:
    """花絮归属：归一标题对 movies.title/original_title，年份±1（年份缺失则只比标题）。
    命中多行取最早入库（id 最小，多版本同 tmdb 归代表无妨，跟随搬迁以行为准逐个比对）。
    返回 movie 行 dict 或 None。"""
    from .scanner import normalize_title
    norm = normalize_title(title or "")
    if not norm:
        return None
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM movies ORDER BY id").fetchall()
    cands = []
    for r in rows:
        d = _row_to_dict(r)
        if year is not None and d.get("year") is not None:
            try:
                if abs(int(d["year"]) - int(year)) > 1:
                    continue
            except (TypeError, ValueError):
                pass
        for key in (d.get("title") or "", d.get("original_title") or ""):
            if key and normalize_title(key) == norm:
                cands.append(d)
                break
    return cands[0] if cands else None


def delete_movie(movie_id: int) -> bool:
    """彻底删除单行（软件外删片/移动后产生）：删关联+主行+FTS行。
    海报与 tmdb_cache 保留（多版本/重扫复用）。播放侧 media_info/progress 级联清理。返回行是否存在。"""
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM movie_person WHERE movie_id=?", (movie_id,))
        c.execute("DELETE FROM movies WHERE id=?", (movie_id,))
        c.execute("DELETE FROM movies_fts WHERE rowid=?", (movie_id,))
        try:
            c.execute("DELETE FROM media_info WHERE movie_id=?", (movie_id,))
        except Exception:
            pass
        try:
            c.execute("DELETE FROM playback_progress WHERE version_id=?", (movie_id,))
        except Exception:
            pass
        return True


def get_media_info(movie_id: int) -> dict | None:
    """读版本媒体信息缓存（含 audio_json/sub_json 已 parse 为 list）。无行返回 None。"""
    with _lock, _conn() as c:
        try:
            row = c.execute("SELECT * FROM media_info WHERE movie_id=?", (movie_id,)).fetchone()
        except Exception:
            return None
        if not row:
            return None
        d = dict(row)
        for k in ("audio_json", "sub_json"):
            v = d.pop(k, "[]")
            try:
                lst = json.loads(v or "[]")
                d["audio" if k == "audio_json" else "subs"] = lst if isinstance(lst, list) else []
            except Exception:
                d["audio" if k == "audio_json" else "subs"] = []
        d["playable"] = bool(d.get("playable"))
        return d


def upsert_media_info(movie_id: int, info: dict) -> dict:
    """写版本媒体信息缓存（ffprobe 本地派生）。info 含 container/duration/width/height/
    vcodec/acodec/vbitrate/abitrate/audio(list)/subs(list)/playable/probe_error。返回落库后行。"""
    now = int(time.time())
    audio_s = _dump_list(info.get("audio"))
    subs_s = _dump_list(info.get("subs"))
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO media_info(movie_id, container, duration, width, height,"
            " vcodec, acodec, vbitrate, abitrate, audio_json, sub_json,"
            " playable, probe_error, probed_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(movie_id) DO UPDATE SET container=excluded.container,"
            " duration=excluded.duration, width=excluded.width, height=excluded.height,"
            " vcodec=excluded.vcodec, acodec=excluded.acodec,"
            " vbitrate=excluded.vbitrate, abitrate=excluded.abitrate,"
            " audio_json=excluded.audio_json, sub_json=excluded.sub_json,"
            " playable=excluded.playable, probe_error=excluded.probe_error,"
            " probed_at=excluded.probed_at",
            (int(movie_id), str(info.get("container") or "")[:16],
             float(info.get("duration") or 0),
             int(info.get("width") or 0), int(info.get("height") or 0),
             str(info.get("vcodec") or "")[:32], str(info.get("acodec") or "")[:32],
             int(info.get("vbitrate") or 0), int(info.get("abitrate") or 0),
             audio_s, subs_s, 1 if info.get("playable") else 0,
             str(info.get("probe_error") or "")[:300], now))
    out = get_media_info(int(movie_id))
    assert out is not None
    return out


def get_progress(version_id: int) -> dict | None:
    """读单版本断点。无行返回 None。"""
    with _lock, _conn() as c:
        try:
            row = c.execute("SELECT * FROM playback_progress WHERE version_id=?",
                            (version_id,)).fetchone()
        except Exception:
            return None
        return dict(row) if row else None


def save_progress(version_id: int, position: float, duration: float) -> dict:
    """写单版本断点（position/duration 秒，钳制 0<=position<=duration）。返回行。"""
    try:
        dur = max(0.0, float(duration or 0))
    except (TypeError, ValueError):
        dur = 0.0
    try:
        pos = max(0.0, float(position or 0))
    except (TypeError, ValueError):
        pos = 0.0
    if dur > 0:
        pos = min(pos, dur)
    now = int(time.time())
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO playback_progress(version_id, position, duration, updated_at)"
            " VALUES(?, ?, ?, ?)"
            " ON CONFLICT(version_id) DO UPDATE SET position=excluded.position,"
            " duration=excluded.duration, updated_at=excluded.updated_at",
            (int(version_id), pos, dur, now))
        row = c.execute("SELECT * FROM playback_progress WHERE version_id=?",
                        (int(version_id),)).fetchone()
        return dict(row)


def clear_progress(version_id: int) -> bool:
    """清单版本断点（用户选“从头开始”）。返回行是否存在过。"""
    with _lock, _conn() as c:
        try:
            row = c.execute("SELECT 1 FROM playback_progress WHERE version_id=?",
                            (version_id,)).fetchone()
            c.execute("DELETE FROM playback_progress WHERE version_id=?", (version_id,))
            return bool(row)
        except Exception:
            return False


def _dir_size(path: str) -> int:
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def library_stats() -> dict:
    """库状态一览（设置页展示用，纯本地聚合）。"""
    with _lock, _conn() as c:
        movies = c.execute("SELECT COUNT(*) AS n FROM movies").fetchone()["n"]
        versions = movies
        grouped = c.execute("SELECT COUNT(*) AS n FROM "
                            "(SELECT 1 FROM movies GROUP BY COALESCE(tmdb_id, -id))").fetchone()["n"]
        needs_review = c.execute("SELECT COUNT(*) AS n FROM movies WHERE needs_review=1").fetchone()["n"]
        no_match = c.execute("SELECT COUNT(*) AS n FROM movies WHERE tmdb_id IS NULL").fetchone()["n"]
        cache = c.execute("SELECT COUNT(*) AS n FROM tmdb_cache").fetchone()["n"]
        persons = c.execute("SELECT COUNT(*) AS n FROM persons").fetchone()["n"]
    try:
        db_bytes = os.path.getsize(DB_PATH)
    except OSError:
        db_bytes = 0
    return {"movies": movies, "versions": versions, "grouped": grouped,
            "needs_review": int(needs_review), "no_match": int(no_match),
            "tmdb_cache": cache, "persons": persons,
            "db_bytes": db_bytes, "posters_bytes": _dir_size(POSTER_DIR)}


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in ("genres", "tags", "origin_countries", "genre_ids"):
        try:
            v = json.loads(d.get(k) or "[]")
            d[k] = v if isinstance(v, list) else []
        except Exception:
            d[k] = []
    return d


def get_by_path(file_path: str) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM movies WHERE file_path=?", (file_path,)).fetchone()
        return _row_to_dict(row) if row else None


def list_movies_in_dir(rel_dir: str) -> list[dict]:
    """同目录顶层 movies 行（不递归子目录），供 NFO 独占/共享判定用。
    只返回轻量列；调用方再按需 get_movie() 取全量（含人物）。"""
    norm = os.path.normpath((rel_dir or "").strip().strip("/"))
    with _lock, _conn() as c:
        if norm in ("", "."):
            rows = c.execute(
                "SELECT id, file_path, tmdb_id, title, year FROM movies "
                "WHERE file_path NOT LIKE '%/%'").fetchall()
            return [dict(r) for r in rows]
        esc = norm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = c.execute(
            "SELECT id, file_path, tmdb_id, title, year FROM movies "
            "WHERE file_path LIKE ? ESCAPE '\\'", (esc + "/%",)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if os.path.dirname(d.get("file_path", "").replace("\\", "/")) == norm:
                out.append(d)
        return out


def _attach_versions(c: sqlite3.Connection, d: dict) -> dict:
    key = d.get("tmdb_id")
    if key:
        vers = [{"id": r["id"], "file_path": r["file_path"],
                 "edition": r["edition"] or "",
                 "spec": r["spec"] or ""} for r in c.execute(
            "SELECT id, file_path, edition, spec FROM movies WHERE tmdb_id=? ORDER BY file_path", (key,))]
    else:
        vers = [{"id": d["id"], "file_path": d["file_path"],
                 "edition": d.get("edition") or "",
                 "spec": d.get("spec") or ""}]
    d["version_count"] = len(vers)
    d["versions"] = vers
    return d


def get_movie(movie_id: int) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        d["persons"] = [dict(r) for r in c.execute(
            "SELECT p.name, p.tmdb_id, p.avatar, mp.role, mp.character_name, mp.cast_order "
            "FROM persons p JOIN movie_person mp ON mp.person_id=p.id "
            "WHERE mp.movie_id=? ORDER BY mp.cast_order", (movie_id,))]
        if d["overview_override"]:
            d["overview_display"] = d["overview_override"]
        else:
            d["overview_display"] = d["overview"]
        d = _attach_versions(c, d)
        d["collections"] = _collections_for_film(c, d.get("tmdb_id"), d.get("id"))
        return d


def _film_key(tmdb_id, movie_id) -> tuple:
    """海报粒度键：有 tmdb_id 按 tmdb，无按单行 id（与分组 GROUP BY 一致）。"""
    try:
        tid = int(tmdb_id) if tmdb_id is not None else None
    except (TypeError, ValueError):
        tid = None
    if tid:
        return (tid, None)
    return (None, int(movie_id))


def expand_ids_to_versions(rep_ids: list[int]) -> dict[int, list[int]]:
    """代表 id → 该海报全版本 id 列表（海报粒度批量操作的展开）。不存在的 id 映射为空列表。"""
    out: dict[int, list[int]] = {}
    with _lock, _conn() as c:
        for rid in rep_ids:
            try:
                rid = int(rid)
            except (TypeError, ValueError):
                continue
            row = c.execute("SELECT id, tmdb_id FROM movies WHERE id=?", (rid,)).fetchone()
            if not row:
                out[rid] = []
                continue
            if row["tmdb_id"]:
                vers = [int(r["id"]) for r in c.execute(
                    "SELECT id FROM movies WHERE tmdb_id=? ORDER BY id", (row["tmdb_id"],))]
                out[rid] = vers
            else:
                out[rid] = [rid]
    return out


def _collections_for_film(c: sqlite3.Connection, tmdb_id, movie_id) -> list[dict]:
    tid, mid = _film_key(tmdb_id, movie_id)
    if tid:
        rows = c.execute(
            "SELECT col.id, col.name FROM collections col "
            "JOIN collection_members cm ON cm.collection_id=col.id "
            "WHERE cm.movie_tmdb_id=? ORDER BY col.name", (tid,)).fetchall()
    else:
        rows = c.execute(
            "SELECT col.id, col.name FROM collections col "
            "JOIN collection_members cm ON cm.collection_id=col.id "
            "WHERE cm.movie_id=? ORDER BY col.name", (mid,)).fetchall()
    return [dict(r) for r in rows]


def list_collections_for_movie(movie_id: int) -> list[dict]:
    with _lock, _conn() as c:
        row = c.execute("SELECT id, tmdb_id FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not row:
            return []
        return _collections_for_film(c, row["tmdb_id"], row["id"])


def _collection_cover(c: sqlite3.Connection, cid: int) -> str:
    """合集封面：最早成员代表行的海报（无则空）。"""
    row = c.execute(
        "SELECT m.poster_path FROM collection_members cm "
        "LEFT JOIN movies m ON ((cm.movie_tmdb_id IS NOT NULL AND m.tmdb_id=cm.movie_tmdb_id) "
        "OR (cm.movie_id IS NOT NULL AND m.id=cm.movie_id)) "
        "WHERE cm.collection_id=? AND m.poster_path IS NOT NULL AND m.poster_path!='' "
        "ORDER BY m.year IS NULL, m.year, m.id LIMIT 1", (cid,)).fetchone()
    return (row["poster_path"] if row else "") or ""


def list_collections(q: str = "") -> list[dict]:
    with _lock, _conn() as c:
        if (q or "").strip():
            rows = c.execute(
                "SELECT * FROM collections WHERE name LIKE ? ORDER BY updated_at DESC",
                (f"%{(q or '').strip()}%",)).fetchall()
        else:
            rows = c.execute("SELECT * FROM collections ORDER BY updated_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["member_count"] = c.execute(
                "SELECT COUNT(*) AS n FROM collection_members WHERE collection_id=?",
                (d["id"],)).fetchone()["n"]
            d["cover"] = d.get("poster_path") or _collection_cover(c, d["id"])
            out.append(d)
        return out


def get_collection(cid: int) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM collections WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        mems = c.execute(
            "SELECT movie_tmdb_id, movie_id, sort_order FROM collection_members "
            "WHERE collection_id=? ORDER BY sort_order, added_at, movie_tmdb_id, movie_id",
            (cid,)).fetchall()
        items = []
        seen = set()
        for mm in mems:
            tid, mid = mm["movie_tmdb_id"], mm["movie_id"]
            if tid:
                mrow = c.execute(
                    "SELECT *, MAX(updated_at) AS _u FROM movies WHERE tmdb_id=? "
                    "GROUP BY COALESCE(tmdb_id, -id)", (tid,)).fetchone()
                key = ("t", tid)
            else:
                mrow = c.execute("SELECT * FROM movies WHERE id=?", (mid,)).fetchone()
                key = ("m", mid)
            if not mrow or key in seen:
                continue
            seen.add(key)
            items.append(_attach_versions(c, _row_to_dict(mrow)))
        # 无自定义排序时按年份正序兜底（系列合集如功夫熊猫按上映顺序看）
        if all(m["sort_order"] == 0 for m in mems) if mems else False:
            items.sort(key=lambda x: ((x.get("year") is None), x.get("year") or 0, x.get("id")))
        d["members"] = items
        d["member_count"] = len(items)
        d["cover"] = d.get("poster_path") or _collection_cover(c, cid)
        return d


def create_collection(name: str, overview: str = "",
                      tmdb_collection_id: int | None = None,
                      member_ids: list | None = None) -> dict:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("name required")
    if len(name) > 60:
        name = name[:60]
    now = int(time.time())
    with _lock, _conn() as c:
        try:
            cur = c.execute(
                "INSERT INTO collections(name, overview, tmdb_collection_id, created_at, updated_at)"
                " VALUES(?, ?, ?, ?, ?)",
                (name, overview or "", tmdb_collection_id, now, now))
            cid = int(cur.lastrowid)
        except sqlite3.IntegrityError:
            raise ValueError("collection name exists")
    if member_ids:
        add_collection_members(cid, member_ids)
    out = get_collection(cid)
    assert out is not None
    return out


def update_collection(cid: int, **fields) -> dict | None:
    allowed = {"name", "overview", "poster_path", "tmdb_collection_id"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if "name" in data:
        data["name"] = " ".join(str(data["name"] or "").split())[:60]
        if not data["name"]:
            raise ValueError("name required")
    if not data:
        return get_collection(cid)
    data["updated_at"] = int(time.time())
    with _lock, _conn() as c:
        try:
            c.execute(f"UPDATE collections SET {', '.join(f'{k}=?' for k in data)} WHERE id=?",
                      (*data.values(), cid))
        except sqlite3.IntegrityError:
            raise ValueError("collection name exists")
    return get_collection(cid)


def delete_collection(cid: int) -> bool:
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM collections WHERE id=?", (cid,)).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM collection_members WHERE collection_id=?", (cid,))
        c.execute("DELETE FROM collections WHERE id=?", (cid,))
        return True


def add_collection_members(cid: int, rep_ids: list) -> dict:
    """海报粒度加入：代表 id 归一为 film key 后幂等插入。返回 {added, total}。"""
    with _lock, _conn() as c:
        if not c.execute("SELECT 1 FROM collections WHERE id=?", (cid,)).fetchone():
            raise LookupError("collection not found")
        rows = c.execute("SELECT id, tmdb_id FROM movies WHERE id IN (%s)" % ",".join("?" * len(rep_ids)),
                         tuple(int(x) for x in rep_ids)) if rep_ids else []
        keys = set()
        for r in (rows or []):
            keys.add(_film_key(r["tmdb_id"], r["id"]))
    now = int(time.time())
    added = 0
    with _lock, _conn() as c:
        for tid, mid in keys:
            try:
                cur = c.execute("INSERT OR IGNORE INTO collection_members"
                                "(collection_id, movie_tmdb_id, movie_id, sort_order, added_at)"
                                " VALUES(?, ?, ?, ?, ?)",
                                (cid, tid, mid, 0, now))
                if cur.rowcount:
                    added += 1
            except Exception:
                continue
        c.execute("UPDATE collections SET updated_at=? WHERE id=?", (now, cid))
        total = c.execute("SELECT COUNT(*) AS n FROM collection_members WHERE collection_id=?",
                          (cid,)).fetchone()["n"]
    return {"added": added, "total": int(total)}


def remove_collection_members(cid: int, rep_ids: list) -> dict:
    with _lock, _conn() as c:
        if not c.execute("SELECT 1 FROM collections WHERE id=?", (cid,)).fetchone():
            raise LookupError("collection not found")
        rows = c.execute("SELECT id, tmdb_id FROM movies WHERE id IN (%s)" % ",".join("?" * len(rep_ids)),
                         tuple(int(x) for x in rep_ids)) if rep_ids else []
        n = 0
        for r in (rows or []):
            tid, mid = _film_key(r["tmdb_id"], r["id"])
            if tid:
                c.execute("DELETE FROM collection_members WHERE collection_id=? AND movie_tmdb_id=?",
                          (cid, tid))
            else:
                c.execute("DELETE FROM collection_members WHERE collection_id=? AND movie_id=?",
                          (cid, mid))
            n += c.total_changes
        c.execute("UPDATE collections SET updated_at=? WHERE id=?", (int(time.time()), cid))
        total = c.execute("SELECT COUNT(*) AS n FROM collection_members WHERE collection_id=?",
                          (cid,)).fetchone()["n"]
    return {"removed": int(n), "total": int(total)}


def get_movie_by_tmdb(tmdb_id: int) -> dict | None:
    """同 tmdb_id 的代表行（最新更新），供回填进度展示标题用。"""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT *, MAX(updated_at) AS _u FROM movies WHERE tmdb_id=?"
            " GROUP BY COALESCE(tmdb_id, -id)", (int(tmdb_id),)).fetchone()
        if not row:
            return None
        return _attach_versions(c, _row_to_dict(row))


def collection_hint_for_movie(movie_id: int) -> dict | None:
    """TMDB 系列提示：本片 cache 的系列 + 库内同系列兄弟（供一键建合集）。"""
    with _lock, _conn() as c:
        mrow = c.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not mrow or not mrow["tmdb_id"]:
            return None
        crow = c.execute("SELECT collection_tmdb_id, collection_name, collection_poster_path"
                         " FROM tmdb_cache WHERE tmdb_id=?", (mrow["tmdb_id"],)).fetchone()
        if not crow or not crow["collection_tmdb_id"]:
            return None
        cid, cname = crow["collection_tmdb_id"], crow["collection_name"] or ""
        try:
            collected = bool(c.execute(
                "SELECT 1 FROM collections WHERE tmdb_collection_id=? OR name=?",
                (cid, cname)).fetchone())
        except Exception:
            collected = False
        sibs = c.execute(
            "SELECT m.*, MAX(m.updated_at) AS _u FROM movies m "
            "JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id "
            "WHERE t.collection_tmdb_id=? GROUP BY COALESCE(m.tmdb_id, -m.id) "
            "ORDER BY m.year IS NULL, m.year", (cid,)).fetchall()
        items = [_attach_versions(c, _row_to_dict(r)) for r in sibs]
        return {"collection_tmdb_id": cid, "collection_name": cname,
                "collection_poster_path": crow["collection_poster_path"] or "",
                "already_collected": collected,
                "in_library": [{"id": x["id"], "title": x.get("title", ""),
                                 "year": x.get("year")} for x in items],
                "in_library_count": len(items)}


def suggest_series_collections(min_members: int = 2) -> dict:
    """TMDB 系列自动推荐（纯本地、只读）：按 tmdb_cache.collection_tmdb_id 聚类，
    库内同系列海报数达标即推荐一项。人物合集 TMDB 给不出，不在此列（纯手动）。
    已被合集收录的系列直接过滤（按 tmdb_collection_id 或同名匹配），不占推荐区。"""
    try:
        min_members = max(2, int(min_members))
    except (TypeError, ValueError):
        min_members = 2
    with _lock, _conn() as c:
        series = c.execute(
            "SELECT t.collection_tmdb_id AS cid, MAX(t.collection_name) AS name,"
            " MAX(t.collection_poster_path) AS poster"
            " FROM movies m JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id"
            " WHERE t.collection_tmdb_id IS NOT NULL"
            " GROUP BY t.collection_tmdb_id").fetchall()
        try:
            existing = {(r["tmdb_collection_id"], (r["name"] or "").strip())
                        for r in c.execute("SELECT tmdb_collection_id, name FROM collections")}
        except Exception:
            existing = set()
        # 系列内成员（海报粒度去重，年份正序）
        items = []
        for s in series:
            cid = s["cid"]
            mems = c.execute(
                "SELECT m.*, MAX(m.updated_at) AS _u FROM movies m"
                " JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id"
                " WHERE t.collection_tmdb_id=?"
                " GROUP BY COALESCE(m.tmdb_id, -m.id)"
                " ORDER BY m.year IS NULL, m.year, m.id", (cid,)).fetchall()
            reps = [_attach_versions(c, _row_to_dict(r)) for r in mems]
            if len(reps) < min_members:
                continue
            cover = ""
            for r in reps:
                if r.get("poster_path"):
                    cover = r["poster_path"]
                    break
            cname = (s["name"] or "").strip() or f"系列 {cid}"
            if any((tid == cid or nm == cname) for tid, nm in existing):
                continue
            items.append({
                "collection_tmdb_id": cid,
                "collection_name": cname,
                "cover": cover,
                "members": [{"id": x["id"], "title": x.get("title", ""),
                             "year": x.get("year")} for x in reps],
                "member_count": len(reps),
            })
        items.sort(key=lambda x: (-x["member_count"], x["collection_name"]))
        cov = c.execute(
            "SELECT COUNT(DISTINCT COALESCE(m.tmdb_id, -m.id)) AS n FROM movies m"
            " JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id"
            " WHERE t.collection_tmdb_id IS NOT NULL").fetchone()["n"]
        total = c.execute(
            "SELECT COUNT(DISTINCT COALESCE(tmdb_id, -id)) AS n FROM movies").fetchone()["n"]
        try:
            standalone = c.execute(
                "SELECT COUNT(DISTINCT COALESCE(m.tmdb_id, -m.id)) AS n FROM movies m"
                " JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id"
                " WHERE t.collection_tmdb_id IS NULL"
                " AND COALESCE(t.collection_checked_at, 0) > 0").fetchone()["n"]
        except Exception:
            standalone = 0
    return {"items": items,
            "coverage": {"with_collection": int(cov or 0),
                         "without_collection": int((total or 0) - (cov or 0)),
                         "standalone": int(standalone or 0),
                         "unchecked": int((total or 0) - (cov or 0) - (standalone or 0))},
            "topups": collected_series_new_members()}


def collected_series_new_members() -> list[dict]:
    """已收录合集的新片差集（纯本地只读）：仅系列建的合集（有 tmdb_collection_id）可匹配；
    库内同系列但尚未入成员的海报即“可补齐”。纯手动合集无法匹配，直接跳过。"""
    with _lock, _conn() as c:
        try:
            cols = c.execute(
                "SELECT id, name FROM collections WHERE tmdb_collection_id IS NOT NULL"
            ).fetchall()
        except Exception:
            return []
        out = []
        for col in cols:
            cid = col["id"]
            tmdb_cid = c.execute(
                "SELECT tmdb_collection_id FROM collections WHERE id=?", (cid,)
            ).fetchone()["tmdb_collection_id"]
            mems = c.execute(
                "SELECT movie_tmdb_id, movie_id FROM collection_members"
                " WHERE collection_id=?", (cid,)).fetchall()
            have = set()
            for mm in mems:
                have.add((mm["movie_tmdb_id"], mm["movie_id"]))
            sibs = c.execute(
                "SELECT m.*, MAX(m.updated_at) AS _u FROM movies m"
                " JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id"
                " WHERE t.collection_tmdb_id=?"
                " GROUP BY COALESCE(m.tmdb_id, -m.id)"
                " ORDER BY m.year IS NULL, m.year, m.id", (tmdb_cid,)).fetchall()
            new = []
            for r in sibs:
                d = _row_to_dict(r)
                tid, mid = _film_key(d.get("tmdb_id"), d["id"])
                if (tid, mid) in have:
                    continue
                new.append({"id": d["id"], "title": d.get("title", ""),
                            "year": d.get("year"),
                            "poster_path": d.get("poster_path", "")})
            if new:
                out.append({"collection_id": cid, "name": col["name"] or "",
                            "new_members": new, "new_count": len(new)})
        out.sort(key=lambda x: (-x["new_count"], x["name"]))
        return out


def top_up_collection(cid: int) -> dict:
    """一键补齐：服务端实时重算差集后写入（不信任客户端 id，防列表过期加错）。"""
    with _lock, _conn() as c:
        row = c.execute("SELECT tmdb_collection_id FROM collections WHERE id=?",
                        (cid,)).fetchone()
        if not row:
            raise LookupError("collection not found")
        if not row["tmdb_collection_id"]:
            raise ValueError("manual collection cannot top up")
    fresh = [t for t in collected_series_new_members() if t["collection_id"] == cid]
    if not fresh:
        with _lock, _conn() as c:
            total = c.execute("SELECT COUNT(*) AS n FROM collection_members"
                              " WHERE collection_id=?", (cid,)).fetchone()["n"]
        return {"added": 0, "total": int(total)}
    rep_ids = [m["id"] for m in fresh[0]["new_members"]]
    r = add_collection_members(cid, rep_ids)
    return r


def tmdb_ids_missing_collection(limit: int = 200, force: bool = False) -> list[int]:
    """待排查系列信息的 tmdb_id 列表（cache 缺失或系列为空且未确认过），供回填口用。
    确认无系列的独立片已盖 collection_checked_at，不再重复检查；force=True 忽略盖戳全量重查。"""
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 200
    with _lock, _conn() as c:
        if force:
            rows = c.execute(
                "SELECT DISTINCT m.tmdb_id AS tid FROM movies m"
                " LEFT JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id"
                " WHERE m.tmdb_id IS NOT NULL"
                " AND (t.tmdb_id IS NULL OR t.collection_tmdb_id IS NULL)"
                " LIMIT ?", (limit,)).fetchall()
        else:
            try:
                rows = c.execute(
                    "SELECT DISTINCT m.tmdb_id AS tid FROM movies m"
                    " LEFT JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id"
                    " WHERE m.tmdb_id IS NOT NULL"
                    " AND (t.tmdb_id IS NULL"
                    " OR (t.collection_tmdb_id IS NULL"
                    " AND COALESCE(t.collection_checked_at, 0) = 0))"
                    " LIMIT ?", (limit,)).fetchall()
            except Exception:
                # 极老库无盖戳列时退化为旧口径
                rows = c.execute(
                    "SELECT DISTINCT m.tmdb_id AS tid FROM movies m"
                    " LEFT JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id"
                    " WHERE m.tmdb_id IS NOT NULL"
                    " AND (t.tmdb_id IS NULL OR t.collection_tmdb_id IS NULL)"
                    " LIMIT ?", (limit,)).fetchall()
        return [int(r["tid"]) for r in rows if r["tid"]]


def list_movies(grouped: bool = True, genres: list | None = None,
                regions: list | None = None, countries: list | None = None,
                years: list | None = None, decades: list | None = None,
                tags: list | None = None, limit: int = 500,
                min_rating: float | None = None,
                rating_source: str | None = None,
                watched: int | None = None,
                collection_ids: list | None = None) -> list[dict]:
    where, params = _structured_where("movies", genres=genres, regions=regions,
                                      countries=countries, years=years,
                                      decades=decades, tags=tags,
                                      min_rating=min_rating,
                                      rating_source=rating_source,
                                      watched=watched,
                                      collection_ids=collection_ids)
    with _lock, _conn() as c:
        if not grouped:
            rows = c.execute(
                f"SELECT * FROM movies WHERE {where} ORDER BY updated_at DESC LIMIT ?",
                (*params, limit))
            return [_row_to_dict(r) for r in rows]
        rows = c.execute(
            f"SELECT *, MAX(updated_at) AS _u FROM movies WHERE {where} "
            f"GROUP BY COALESCE(tmdb_id, -id) ORDER BY _u DESC LIMIT ?",
            (*params, limit))
        return [_attach_versions(c, _row_to_dict(r)) for r in rows]


def search_fts(q: str, limit: int = 50, grouped: bool = True,
               genres: list | None = None, regions: list | None = None,
               countries: list | None = None, years: list | None = None,
               decades: list | None = None, tags: list | None = None,
               min_rating: float | None = None,
               rating_source: str | None = None,
               watched: int | None = None,
               collection_ids: list | None = None) -> list[dict]:
    fwhere, fparams = _structured_where("m", genres=genres, regions=regions,
                                        countries=countries, years=years,
                                        decades=decades, tags=tags,
                                        min_rating=min_rating,
                                        rating_source=rating_source,
                                        watched=watched,
                                        collection_ids=collection_ids)
    q = (q or "").strip()
    if not q:
        return list_movies(grouped=grouped, genres=genres, regions=regions,
                           countries=countries, years=years, decades=decades,
                           tags=tags, limit=limit, min_rating=min_rating,
                           rating_source=rating_source, watched=watched,
                           collection_ids=collection_ids)
    with _lock, _conn() as c:
        if not grouped:
            rows = c.execute(
                "SELECT m.* FROM movies_fts f JOIN movies m ON m.id=f.rowid "
                f"WHERE movies_fts MATCH ? AND ({fwhere}) ORDER BY rank LIMIT ?",
                (q, *fparams, limit))
            return [_row_to_dict(r) for r in rows]
        rows = c.execute(
            "SELECT m.*, MIN(rank) AS _r FROM movies_fts f JOIN movies m ON m.id=f.rowid "
            f"WHERE movies_fts MATCH ? AND ({fwhere}) GROUP BY COALESCE(m.tmdb_id, -m.id) "
            "ORDER BY _r LIMIT ?", (q, *fparams, limit))
        return [_attach_versions(c, _row_to_dict(r)) for r in rows]


def _split_multi(v) -> list[str]:
    """逗号/多值参数归一：'科幻,动作' / ['科幻','动作'] → ['科幻','动作']。"""
    if v is None:
        return []
    items = v if isinstance(v, list) else [v]
    out = []
    for it in items:
        for part in str(it).split(","):
            s = " ".join(part.split())
            if s:
                out.append(s)
    return out


def _split_ints(v) -> list[int]:
    out = []
    for s in _split_multi(v):
        try:
            out.append(int(s))
        except ValueError:
            continue
    return out


RATING_SOURCES = {"tmdb": "tmdb_rating", "douban": "douban_rating",
                  "custom": "custom_rating"}
RATING_STEPS = (9, 8, 7, 6)


def _rating_col(source) -> str:
    return RATING_SOURCES.get(str(source or "tmdb").lower(), "tmdb_rating")


def _structured_where(alias: str, genres=None, regions=None, countries=None,
                      years=None, decades=None, tags=None,
                      min_rating=None, rating_source=None,
                      watched=None, collection_ids=None) -> tuple[str, tuple]:
    """结构化过滤：facet内OR、facet间AND；tags多选为AND；min_rating为单阈值（>=）。返回 (where_sql, params)。"""
    from .regions import REGION_UNKNOWN
    conds: list[str] = []
    params: list = []
    gs = _split_multi(genres)
    if gs:
        conds.append("(%s)" % " OR ".join(
            f"EXISTS (SELECT 1 FROM json_each({alias}.genres) je WHERE je.value=?)" for _ in gs))
        params.extend(gs)
    rs = _split_multi(regions)
    if rs:
        parts = []
        known = [r for r in rs if r != REGION_UNKNOWN]
        if known:
            parts.append(f"{alias}.region IN (%s)" % ",".join("?" * len(known)))
            params.extend(known)
        if REGION_UNKNOWN in rs:
            parts.append(f"({alias}.region IS NULL OR {alias}.region='')")
        conds.append("(%s)" % " OR ".join(parts))
    cs = [c.upper() for c in _split_multi(countries)]
    if cs:
        parts = []
        known = [c for c in cs if c not in (REGION_UNKNOWN, "")]
        if known:
            parts.append(f"{alias}.origin_country IN (%s)" % ",".join("?" * len(known)))
            params.extend(known)
            parts.append(
                "EXISTS (SELECT 1 FROM json_each(%s.origin_countries) je WHERE je.value IN (%s))"
                % (alias, ",".join("?" * len(known))))
            params.extend(known)
        if REGION_UNKNOWN in cs or "" in _split_multi(countries):
            parts.append(f"({alias}.origin_country IS NULL OR {alias}.origin_country='')")
        conds.append("(%s)" % " OR ".join(parts))
    ys = _split_ints(years)
    if ys:
        conds.append(f"{alias}.year IN (%s)" % ",".join("?" * len(ys)))
        params.extend(ys)
    ds = _split_ints(decades)
    if ds:
        parts = []
        for d in ds:
            parts.append(f"({alias}.year>=? AND {alias}.year<=?)")
            params.extend([d, d + 9])
        conds.append("(%s)" % " OR ".join(parts))
    ts = _split_multi(tags)
    for t in ts:  # 标签多选为 AND（逐个收窄）
        conds.append(
            f"EXISTS (SELECT 1 FROM json_each({alias}.tags) je WHERE je.value=?)")
        params.append(t)
    if min_rating is not None:
        try:
            conds.append(f"({alias}.{_rating_col(rating_source)}>=?)")
            params.append(float(min_rating))
        except (TypeError, ValueError):
            pass
    if watched is not None:
        try:
            w = int(watched)
            conds.append(f"({alias}.watched=?)")
            params.append(1 if w else 0)
        except (TypeError, ValueError):
            pass
    cids = _split_ints(collection_ids) if collection_ids is not None else []
    if cids:
        # 合集内 OR：成员以海报粒度存放（tmdb_id 有则按 tmdb，无则按单行 id）
        conds.append(
            "(%s)" % " OR ".join(
                f"EXISTS (SELECT 1 FROM collection_members cm WHERE cm.collection_id=? "
                f"AND ((cm.movie_tmdb_id IS NOT NULL AND {alias}.tmdb_id=cm.movie_tmdb_id) "
                f"OR (cm.movie_id IS NOT NULL AND {alias}.id=cm.movie_id)))"
                for _ in cids))
        params.extend(cids)
    if not conds:
        return "1=1", ()
    return " AND ".join(f"({x})" for x in conds), tuple(params)


def get_facets(grouped: bool = True) -> dict:
    """库内实际计数的动态facets：只返回 count>0 项，供前端直接渲染。"""
    from collections import Counter
    from .regions import REGION_ORDER, REGION_UNKNOWN, country_name
    with _lock, _conn() as c:
        # 全量取行后 Python 内分组（组内 tags 取并集，代表行取最新），避免代表行漏掉打在旧版本上的标签
        rows = c.execute("SELECT * FROM movies").fetchall()
        try:
            col_rows = c.execute("SELECT id, name FROM collections ORDER BY name").fetchall()
            collections = [{"id": r["id"], "name": r["name"],
                            "count": c.execute("SELECT COUNT(*) AS n FROM collection_members"
                                               " WHERE collection_id=?", (r["id"],)).fetchone()["n"]}
                           for r in col_rows]
        except Exception:
            collections = []
    gc, rc, cc, yc, dc, tc, ic, sc = (Counter() for _ in range(8))
    wc = Counter()
    if grouped:
        # 同 tmdb_id 的多版本取代表行计数；tags 取组内并集（避免标签打在非代表版本上被漏计）
        groups: dict = {}
        for r in rows:
            d = _row_to_dict(r)
            key = d.get("tmdb_id") or -d["id"]
            g = groups.setdefault(key, {"rep": None, "tags": set()})
            if g["rep"] is None or (d.get("updated_at", 0) or 0) > (g["rep"].get("updated_at", 0) or 0):
                g["rep"] = d
            g["tags"].update(d.get("tags") or [])
        reps = [g["rep"] for g in groups.values()]
        for t_set in (g["tags"] for g in groups.values()):
            for t in t_set:
                tc[t] += 1
    else:
        reps = [_row_to_dict(r) for r in rows]
        for d in reps:
            for t in d.get("tags") or []:
                tc[t] += 1
    for d in reps:
        wc[1 if d.get("watched") else 0] += 1
        for g in d.get("genres") or []:
            gc[g] += 1
        reg = d.get("region") or REGION_UNKNOWN
        rc[reg] += 1
        codes = d.get("origin_countries") or []
        primary = d.get("origin_country") or (codes[0] if codes else "")
        cc[primary or REGION_UNKNOWN] += 1
        # 国家facet按“参与”口径计数（与 country 过滤语义一致：合拍片各参与国都+1）
        involved = set(codes) | ({primary} if primary else set())
        for code in involved or {REGION_UNKNOWN}:
            ic[code] += 1
        y = d.get("year")
        if isinstance(y, int):
            yc[y] += 1
            dc[(y // 10) * 10] += 1
        for src, col in RATING_SOURCES.items():
            v = d.get(col)
            if isinstance(v, (int, float)) and v > 0:
                for step in RATING_STEPS:
                    if v >= step:
                        sc[(src, step)] += 1
    order = {v: i for i, v in enumerate(REGION_ORDER)}
    return {
        "genres": [{"value": k, "count": v} for k, v in gc.most_common()],
        "regions": sorted(({"value": k, "count": v} for k, v in rc.items()),
                          key=lambda x: (order.get(x["value"], 99), -x["count"])),
        "countries": [{"code": ("" if k == REGION_UNKNOWN else k),
                       "name": (REGION_UNKNOWN if k == REGION_UNKNOWN else country_name(k)),
                       "count": v} for k, v in ic.most_common()],
        "primary_countries": [{"code": ("" if k == REGION_UNKNOWN else k),
                       "name": (REGION_UNKNOWN if k == REGION_UNKNOWN else country_name(k)),
                       "count": v} for k, v in cc.most_common()],
        "years": [{"value": k, "count": v} for k, v in sorted(yc.items(), reverse=True)],
        "decades": [{"value": k, "count": v} for k, v in sorted(dc.items(), reverse=True)],
        "tags": [{"value": k, "count": v} for k, v in tc.most_common()],
        "watched": {"watched": wc.get(1, 0), "unwatched": wc.get(0, 0)},
        "collections": collections,
        "ratings": {src: [{"min": s, "count": sc.get((src, s), 0)} for s in RATING_STEPS]
                    for src in RATING_SOURCES},
    }
