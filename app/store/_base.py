"""store._base：连接/锁/Schema/初始化/公共行转换（自 app/store.py 拆分）。"""
import json
import os
import sqlite3
import threading

from ..db import DB_PATH, ensure_dirs
from ..log import get_logger

logger = get_logger("store")

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
-- movies 表的 TMDB 列只是它的物化副本：对外只经 copy_tmdb_to_movie() 复制
-- （update_movie_meta 内部允许写这些列，供 scanner 单点场景使用）。
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
-- probe_ver 为探测结构版本：低版本行在播放时自动重探（新字段上线自愈，免手动 backfill）。
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
  dv_profile INTEGER DEFAULT 0,
  probe_ver INTEGER DEFAULT 0,
  video_profile TEXT DEFAULT '',
  video_level INTEGER DEFAULT 0,
  bit_depth INTEGER DEFAULT 0,
  pix_fmt TEXT DEFAULT '',
  color_transfer TEXT DEFAULT '',
  color_primaries TEXT DEFAULT '',
  hdr TEXT DEFAULT '',
  dv_bl_compat INTEGER DEFAULT 0,
  hdr10plus INTEGER DEFAULT 0,
  attachments_json TEXT DEFAULT '[]',
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
                    "tmdb_language", "tmdb_image_base", "jzmedia_token"}


def init_db() -> None:
    with _lock, _conn() as c:
        # 启动自检 SQLite 特性（评审 B6/R02-B4）：JSON1 与 FTS5 缺失时给明确错误，
        # 而不是运行到一半抛 OperationalError 让人摸不着头脑
        try:
            c.execute("SELECT json_valid('1')")
        except sqlite3.OperationalError as e:
            raise RuntimeError("SQLite 缺少 JSON1 扩展，jzmedia 无法运行") from e
        try:
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS __fts_probe USING fts5(x)")
            c.execute("DROP TABLE IF EXISTS __fts_probe")
        except sqlite3.OperationalError as e:
            raise RuntimeError("SQLite 缺少 FTS5 扩展，jzmedia 无法运行") from e
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
        # media_info 列自愈（老库缺列时补上）。probe_ver 默认 0 < media.PROBE_VERSION：
        # 旧探测行播放时自动重探补新字段（dv_profile/hdr/bit_depth 等），无需全量 backfill。
        micols = [r["name"] for r in c.execute("PRAGMA table_info(media_info)")]
        for col, ddl in (
            ("dv_profile", "ALTER TABLE media_info ADD COLUMN dv_profile INTEGER DEFAULT 0"),
            ("probe_ver", "ALTER TABLE media_info ADD COLUMN probe_ver INTEGER DEFAULT 0"),
            ("video_profile", "ALTER TABLE media_info ADD COLUMN video_profile TEXT DEFAULT ''"),
            ("video_level", "ALTER TABLE media_info ADD COLUMN video_level INTEGER DEFAULT 0"),
            ("bit_depth", "ALTER TABLE media_info ADD COLUMN bit_depth INTEGER DEFAULT 0"),
            ("pix_fmt", "ALTER TABLE media_info ADD COLUMN pix_fmt TEXT DEFAULT ''"),
            ("color_transfer", "ALTER TABLE media_info ADD COLUMN color_transfer TEXT DEFAULT ''"),
            ("color_primaries", "ALTER TABLE media_info ADD COLUMN color_primaries TEXT DEFAULT ''"),
            ("hdr", "ALTER TABLE media_info ADD COLUMN hdr TEXT DEFAULT ''"),
            ("dv_bl_compat", "ALTER TABLE media_info ADD COLUMN dv_bl_compat INTEGER DEFAULT 0"),
            ("hdr10plus", "ALTER TABLE media_info ADD COLUMN hdr10plus INTEGER DEFAULT 0"),
            ("attachments_json", "ALTER TABLE media_info ADD COLUMN attachments_json TEXT DEFAULT '[]'"),
        ):
            if col not in micols:
                c.execute(ddl)
        sql = (c.execute("SELECT sql FROM sqlite_master WHERE name='movies_fts'").fetchone() or [""])[0]
        if "content=" in sql:
            c.execute("DROP TABLE movies_fts")
            c.executescript(SCHEMA)
    from .search import rebuild_fts
    from .tmdb_cache import seed_tmdb_cache_from_movies
    seed_tmdb_cache_from_movies()
    rebuild_fts()


def health_check() -> dict:
    """健康自检（评审 B6/R01-D4）：DB 可读 + 数据目录可写；不抛错。"""
    ok, err = True, ""
    try:
        with _lock, _conn() as c:
            c.execute("SELECT 1")
    except Exception as e:
        ok, err = False, str(e)[:200]
    writable = os.access(os.path.dirname(DB_PATH) or ".", os.W_OK)
    db_bytes = 0
    try:
        db_bytes = os.path.getsize(DB_PATH)
    except OSError:
        pass
    return {"ok": bool(ok and writable), "readable": bool(ok),
            "writable": bool(writable), "error": err, "bytes": db_bytes}


def _dump_list(v) -> str:
    try:
        return json.dumps(v or [], ensure_ascii=False)
    except Exception as e:
        logger.warning("dump list failed, write []: %s", e)
        return "[]"


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in ("genres", "tags", "origin_countries", "genre_ids"):
        try:
            v = json.loads(d.get(k) or "[]")
            d[k] = v if isinstance(v, list) else []
        except Exception:
            d[k] = []
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


def _like_esc(s: str) -> str:
    """转义 LIKE 通配符（配合 ESCAPE '\\' 使用）。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


__all__ = ['_conn', 'init_db', 'health_check', '_dump_list', '_row_to_dict', '_film_key', '_attach_versions', '_collections_for_film', '_lock', 'SCHEMA', 'TMDB_FIELDS', 'LOCAL_FIELDS', 'APP_SETTING_KEYS', 'logger']
