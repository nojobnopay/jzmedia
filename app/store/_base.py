"""store._base：连接/锁/Schema/初始化/公共行转换（自 app/store.py 拆分）。

多库（MULTI_LIBRARY_PLAN v12/v13）：
- `libraries` 为库注册表；movies/extras/scan_state 以 `library_id` 分区，
  一库一根（D1），相对路径只在库内唯一。
- 旧单根库迁移时全部行归入默认库 `DEFAULT_LIBRARY_ID`（=1，由 media_root 播种），
  因此 v12 之前的调用点（默认参数）行为不变。
- media_info/playback_progress 改 `(kind, item_id)` 复合键（movie|episode）。
"""
import json
import os
import sqlite3
import threading
import time

from ..config import settings
from ..db import DB_PATH, ensure_dirs, mount_point
from ..log import get_logger

logger = get_logger("store")

_lock = threading.RLock()

# 默认库 id：v12 迁移把存量行全部归入该库；单根 API 默认参数也指向它。
DEFAULT_LIBRARY_ID = 1


_LIBRARIES_DDL = """
CREATE TABLE IF NOT EXISTS libraries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL DEFAULT 'movie' CHECK(kind IN ('movie','tv')),
  source TEXT NOT NULL DEFAULT 'local' CHECK(source IN ('local','smb','nfs')),
  path TEXT NOT NULL,
  read_only INTEGER NOT NULL DEFAULT 0,
  auto_mount INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  naming_profile TEXT NOT NULL DEFAULT 'kodi' CHECK(naming_profile IN ('plex','kodi','off')),
  artwork_mode TEXT NOT NULL DEFAULT 'nfo' CHECK(artwork_mode IN ('none','nfo','nfo_art')),
  organize_target TEXT NOT NULL DEFAULT '电影',
  inbox_dir TEXT NOT NULL DEFAULT '待整理',
  metadata_providers TEXT NOT NULL DEFAULT '',
  smb_host TEXT DEFAULT '', smb_share TEXT DEFAULT '', smb_subpath TEXT DEFAULT '',
  smb_domain TEXT DEFAULT '', smb_username TEXT DEFAULT '', smb_password TEXT DEFAULT '',
  smb_options TEXT DEFAULT '',
  nfs_export TEXT DEFAULT '', nfs_password TEXT DEFAULT '', nfs_options TEXT DEFAULT '',
  last_status TEXT DEFAULT '', last_error TEXT DEFAULT '', last_check_at INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL DEFAULT 0
);
"""

_MOVIES_DDL = """
CREATE TABLE IF NOT EXISTS movies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_path TEXT NOT NULL,
  library_id INTEGER NOT NULL DEFAULT 1,
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
  title_auto INTEGER DEFAULT 0,   -- 1=标题为扫描按文件名自动写入（可被 TMDB 覆盖），0=TMDB/手工
  watched INTEGER DEFAULT 0,
  watched_at INTEGER DEFAULT 0,
  match_source TEXT DEFAULT '',   -- tmdb|local|nfo|tvmaze|…（离线/降级匹配来源）
  nfo_hash TEXT DEFAULT '',       -- 上次写 NFO 的内容哈希（外部改动保护）
  updated_at INTEGER DEFAULT 0,
  UNIQUE(library_id, file_path)
);
"""

_MOVIE_COLUMNS = ["id", "file_path", "library_id", "title", "original_title", "year",
                  "overview", "overview_override", "tmdb_id", "imdb_id", "tmdb_rating",
                  "douban_rating", "custom_rating", "poster_path", "genres", "genre_ids",
                  "tags", "person_names", "origin_country", "origin_countries",
                  "original_language", "region", "media_type", "edition", "spec",
                  "original_file_path", "needs_review", "title_auto", "watched",
                  "watched_at", "match_source", "nfo_hash", "updated_at"]

_EXTRAS_DDL = """
CREATE TABLE IF NOT EXISTS extras (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_path TEXT NOT NULL,
  library_id INTEGER NOT NULL DEFAULT 1,
  movie_id INTEGER,
  kind TEXT DEFAULT 'extra',
  updated_at INTEGER DEFAULT 0,
  UNIQUE(library_id, file_path)
);
"""

_EXTRAS_COLUMNS = ["id", "file_path", "library_id", "movie_id", "kind", "updated_at"]

_SCAN_STATE_DDL = """
CREATE TABLE IF NOT EXISTS scan_state (
  file_path TEXT NOT NULL,
  library_id INTEGER NOT NULL DEFAULT 1,
  mtime INTEGER DEFAULT 0,
  size INTEGER DEFAULT 0,
  status TEXT DEFAULT '',
  updated_at INTEGER DEFAULT 0,
  PRIMARY KEY (library_id, file_path)
);
"""

_MEDIA_INFO_DDL = """
CREATE TABLE IF NOT EXISTS media_info (
  kind TEXT NOT NULL DEFAULT 'movie',
  item_id INTEGER NOT NULL,
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
  probed_at INTEGER DEFAULT 0,
  PRIMARY KEY (kind, item_id)
);
"""

_MEDIA_INFO_COLUMNS = ["kind", "item_id", "container", "duration", "width", "height",
                       "vcodec", "acodec", "vbitrate", "abitrate", "audio_json",
                       "sub_json", "dv_profile", "probe_ver", "video_profile",
                       "video_level", "bit_depth", "pix_fmt", "color_transfer",
                       "color_primaries", "hdr", "dv_bl_compat", "hdr10plus",
                       "attachments_json", "playable", "probe_error", "probed_at"]

_PROGRESS_DDL = """
CREATE TABLE IF NOT EXISTS playback_progress (
  kind TEXT NOT NULL DEFAULT 'movie',
  item_id INTEGER NOT NULL,
  position REAL DEFAULT 0,
  duration REAL DEFAULT 0,
  updated_at INTEGER DEFAULT 0,
  PRIMARY KEY (kind, item_id)
);
"""

_TV_SHOWS_DDL = """
CREATE TABLE IF NOT EXISTS tv_shows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  library_id INTEGER NOT NULL DEFAULT 1,
  title TEXT DEFAULT '',
  sort_title TEXT DEFAULT '',
  year INTEGER,
  tmdb_id INTEGER,
  needs_review INTEGER DEFAULT 0,
  updated_at INTEGER DEFAULT 0,
  UNIQUE(library_id, title, year)
);
"""

_TV_EPISODES_DDL = """
CREATE TABLE IF NOT EXISTS tv_episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  show_id INTEGER NOT NULL,
  library_id INTEGER NOT NULL DEFAULT 1,
  file_path TEXT NOT NULL,
  season INTEGER DEFAULT 0,
  episode INTEGER DEFAULT 0,
  title TEXT DEFAULT '',
  updated_at INTEGER DEFAULT 0,
  UNIQUE(library_id, file_path)
);
"""

_REST_DDL = """
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
CREATE INDEX IF NOT EXISTS idx_movie_person_person ON movie_person(person_id);

-- TMDB远端镜像：以 tmdb_id 为键的稳定缓存，不受 file_path/tags/评分等本地改动影响。
-- movies 表的 TMDB 列只是它的物化副本：对外只经 copy_tmdb_to_movie() 复制
-- （update_movie_meta 内部允许写这些列，供 scanner 单点场景使用）。
-- v12 扩展：source/payload_json（离线重放）/premiered/tagline/runtime/studios/图源。
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
  fetched_at INTEGER DEFAULT 0,
  source TEXT DEFAULT 'tmdb',
  payload_json TEXT DEFAULT '{}',
  premiered TEXT DEFAULT '',
  tagline TEXT DEFAULT '',
  runtime INTEGER DEFAULT 0,
  studios TEXT DEFAULT '[]',
  backdrop_tmdb_path TEXT DEFAULT '',
  logo_tmdb_path TEXT DEFAULT ''
);
-- 手工合集：成员以海报粒度存放（有 tmdb_id 存 movie_tmdb_id，无则存 movie_id），与海报墙分组键一致
CREATE TABLE IF NOT EXISTS collections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  overview TEXT DEFAULT '',
  poster_path TEXT DEFAULT '',
  tmdb_collection_id INTEGER,
  library_id INTEGER NOT NULL DEFAULT 1,
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
-- 离线/降级匹配候选索引（D7）：tmdb_cache、NFO、外部 provider 结果的统一检索面。
CREATE TABLE IF NOT EXISTS match_index (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'movie',
  title TEXT DEFAULT '',
  original_title TEXT DEFAULT '',
  year INTEGER,
  tmdb_id INTEGER,
  imdb_id TEXT DEFAULT '',
  payload TEXT DEFAULT '{}',
  fetched_at INTEGER DEFAULT 0,
  UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_match_index_tmdb ON match_index(tmdb_id);
CREATE VIRTUAL TABLE IF NOT EXISTS match_index_fts USING fts5(
  title, original_title, tokenize='unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS movies_fts USING fts5(
  title, original_title, overview, person_names, tags, genres,
  tokenize='unicode61'
);
DROP TRIGGER IF EXISTS movies_ai;
DROP TRIGGER IF EXISTS movies_ad;
DROP TRIGGER IF EXISTS movies_au;
"""

SCHEMA = "".join([_LIBRARIES_DDL, _MOVIES_DDL, _EXTRAS_DDL, _SCAN_STATE_DDL,
                  _MEDIA_INFO_DDL, _PROGRESS_DDL, _TV_SHOWS_DDL, _TV_EPISODES_DDL,
                  _REST_DDL])

_MOVIE_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_movies_year ON movies(year)",
    "CREATE INDEX IF NOT EXISTS idx_movies_region ON movies(region)",
    "CREATE INDEX IF NOT EXISTS idx_movies_origin ON movies(origin_country)",
    "CREATE INDEX IF NOT EXISTS idx_movies_tmdb ON movies(tmdb_id)",
    "CREATE INDEX IF NOT EXISTS idx_movies_watched ON movies(watched)",
]

_MOVIE_LIBRARY_INDEX_DDL = "CREATE INDEX IF NOT EXISTS idx_movies_library ON movies(library_id)"

_EXTRAS_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_extras_movie ON extras(movie_id)",
    "CREATE INDEX IF NOT EXISTS idx_extras_library ON extras(library_id)",
]


def _conn() -> sqlite3.Connection:
    ensure_dirs()
    # R02-D2：一调用一连接；5s busy_timeout 防并发写瞬时 SQLITE_BUSY，
    # synchronous=NORMAL 配合 WAL（WAL 在 init_db 里设置，持久于库文件）
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


# TMDB镜像列（movies 中的物化副本，只能经 copy_tmdb_to_movie() 从 tmdb_cache 复制）
TMDB_FIELDS = {"title", "original_title", "year", "overview",
               "tmdb_id", "imdb_id", "tmdb_rating",
               "genres", "genre_ids",
               "origin_country", "origin_countries", "original_language",
               "region", "media_type"}
# 本地自有列（PATCH/rename/tags/评分等，只能经本地写路径修改，不碰 cache）
LOCAL_FIELDS = {"file_path", "title", "title_auto", "overview_override",
                "douban_rating", "custom_rating", "tags", "needs_review",
                "edition", "spec", "original_file_path",
                "watched", "watched_at"}


# 设置页可写的配置键白名单（与 config.effective_* 对应）
APP_SETTING_KEYS = {"tmdb_read_token", "tmdb_api_key", "tmdb_proxy",
                    "tmdb_language", "tmdb_image_base", "jzmedia_token"}


SCHEMA_VERSION = 14


def _columns(c, table: str) -> set:
    return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}


def _has_column(c, table: str, col: str) -> bool:
    return col in _columns(c, table)


def _ensure_columns(c, table: str, cols) -> None:
    have = _columns(c, table)
    for name, ddl in cols:
        if name not in have:
            c.execute(ddl)


def _rebuild_table(c, table: str, ddl: str, columns: list[str],
                   copy_extra: str = "", extra_values: str = "") -> None:
    """按新 DDL 重建表并搬运交集列（SQLite 无法改内联 UNIQUE/PK）。
    copy_extra/extra_values 用于给新列填常量，如 library_id。"""
    old_cols = _columns(c, table)
    new_ddl = ddl.replace(f"CREATE TABLE IF NOT EXISTS {table}",
                          f"CREATE TABLE {table}_new", 1)
    c.execute(new_ddl)
    shared = [col for col in columns if col in old_cols]
    cols_sql = ", ".join(shared)
    extra_sql = f", {copy_extra}" if copy_extra else ""
    extra_sel = f", {extra_values}" if extra_values else ""
    c.execute(f"INSERT INTO {table}_new ({cols_sql}{extra_sql}) "
              f"SELECT {cols_sql}{extra_sel} FROM {table}")
    c.execute(f"DROP TABLE {table}")
    c.execute(f"ALTER TABLE {table}_new RENAME TO {table}")


def _seed_default_library(c) -> None:
    """libraries 为空时按 MEDIA_ROOT 播种默认库（v12 自举；幂等）。"""
    row = c.execute("SELECT COUNT(*) AS n FROM libraries").fetchone()
    if int(row["n"] or 0) > 0:
        return
    root = settings.media_root or "./sample_media"
    try:
        name = os.path.basename(os.path.normpath(root)) or "默认库"
    except (OSError, ValueError):
        name = "默认库"
    now = int(time.time())
    c.execute(
        "INSERT INTO libraries(id, name, kind, source, path, naming_profile,"
        " artwork_mode, created_at, updated_at)"
        " VALUES(?, ?, 'movie', 'local', ?, 'kodi', 'nfo', ?, ?)",
        (DEFAULT_LIBRARY_ID, name, root, now, now))
    logger.info("多库迁移：默认库 id=%s name=%s path=%s", DEFAULT_LIBRARY_ID, name, root)


def _migrate(c) -> int:
    """按 user_version 顺序应用迁移（评审 B9/R02-D1：替代启动时 ad-hoc ALTER 探测）。
    每一步都必须幂等；老库无需人工干预，新库由 SCHEMA 建全量后直接把版本推到最新。"""
    try:
        version = int(c.execute("PRAGMA user_version").fetchone()[0] or 0)
    except (TypeError, ValueError):
        version = 0
    applied = False
    for v, fn in _MIGRATION_STEPS:
        if version < v:
            fn(c)
            version = v
            applied = True
    try:
        c.execute(f"PRAGMA user_version = {int(version)}")
    except sqlite3.OperationalError as e:
        logger.warning("set user_version failed: %s", e)
    return version, applied


# v1：movies 基础补充列 + persons.avatar
def _m1(c) -> None:
    _ensure_columns(c, "movies", [
        ("person_names", "ALTER TABLE movies ADD COLUMN person_names TEXT DEFAULT ''"),
        ("needs_review", "ALTER TABLE movies ADD COLUMN needs_review INTEGER DEFAULT 0"),
    ])
    if "avatar" not in _columns(c, "persons"):
        c.execute("ALTER TABLE persons ADD COLUMN avatar TEXT DEFAULT ''")


# v2：产地/语言/大区/媒体类型
def _m2(c) -> None:
    _ensure_columns(c, "movies", [
        ("origin_country", "ALTER TABLE movies ADD COLUMN origin_country TEXT DEFAULT ''"),
        ("origin_countries", "ALTER TABLE movies ADD COLUMN origin_countries TEXT DEFAULT '[]'"),
        ("original_language", "ALTER TABLE movies ADD COLUMN original_language TEXT DEFAULT ''"),
        ("region", "ALTER TABLE movies ADD COLUMN region TEXT DEFAULT ''"),
        ("genre_ids", "ALTER TABLE movies ADD COLUMN genre_ids TEXT DEFAULT '[]'"),
        ("media_type", "ALTER TABLE movies ADD COLUMN media_type TEXT DEFAULT 'movie'"),
    ])


# v3：版本/规格/原始路径/观看
def _m3(c) -> None:
    _ensure_columns(c, "movies", [
        ("edition", "ALTER TABLE movies ADD COLUMN edition TEXT DEFAULT ''"),
        ("spec", "ALTER TABLE movies ADD COLUMN spec TEXT DEFAULT ''"),
        ("original_file_path", "ALTER TABLE movies ADD COLUMN original_file_path TEXT DEFAULT ''"),
        ("watched", "ALTER TABLE movies ADD COLUMN watched INTEGER DEFAULT 0"),
        ("watched_at", "ALTER TABLE movies ADD COLUMN watched_at INTEGER DEFAULT 0"),
    ])


# v4：人物扩展（渐进 bio 等）
def _m4(c) -> None:
    _ensure_columns(c, "persons", [
        ("biography", "ALTER TABLE persons ADD COLUMN biography TEXT DEFAULT ''"),
        ("birthday", "ALTER TABLE persons ADD COLUMN birthday TEXT DEFAULT ''"),
        ("place_of_birth", "ALTER TABLE persons ADD COLUMN place_of_birth TEXT DEFAULT ''"),
        ("profile_tmdb_path", "ALTER TABLE persons ADD COLUMN profile_tmdb_path TEXT DEFAULT ''"),
        ("fetched_at", "ALTER TABLE persons ADD COLUMN fetched_at INTEGER DEFAULT 0"),
        ("bio_fetched_at", "ALTER TABLE persons ADD COLUMN bio_fetched_at INTEGER DEFAULT 0"),
        ("bio_lang", "ALTER TABLE persons ADD COLUMN bio_lang TEXT DEFAULT ''"),
    ])


# v5：tmdb_cache 系列信息列
def _m5(c) -> None:
    _ensure_columns(c, "tmdb_cache", [
        ("collection_tmdb_id", "ALTER TABLE tmdb_cache ADD COLUMN collection_tmdb_id INTEGER"),
        ("collection_name", "ALTER TABLE tmdb_cache ADD COLUMN collection_name TEXT DEFAULT ''"),
        ("collection_poster_path", "ALTER TABLE tmdb_cache ADD COLUMN collection_poster_path TEXT DEFAULT ''"),
        ("collection_checked_at", "ALTER TABLE tmdb_cache ADD COLUMN collection_checked_at INTEGER DEFAULT 0"),
    ])


# v6：media_info 探测结构扩展（probe_ver 低会触发重探）
def _m6(c) -> None:
    _ensure_columns(c, "media_info", [
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
    ])


# v7：过滤/缓存索引
def _m7(c) -> None:
    for ddl in _MOVIE_INDEX_DDL + [
        "CREATE INDEX IF NOT EXISTS idx_cache_fetched ON tmdb_cache(fetched_at)",
        "CREATE INDEX IF NOT EXISTS idx_scan_state_updated ON scan_state(updated_at)",
    ]:
        c.execute(ddl)


# v8：原始路径种子（老行=最早已知路径；新行由 upsert 写真实原始路径）
def _m8(c) -> None:
    c.execute("UPDATE movies SET original_file_path=file_path "
              "WHERE original_file_path IS NULL OR original_file_path=''")


# v9：扫描增量状态表
def _m9(c) -> None:
    c.execute(_SCAN_STATE_DDL)


# v10：补 person 侧回查与 extras 归属索引（评审 R02-B7）
def _m10(c) -> None:
    c.execute("CREATE INDEX IF NOT EXISTS idx_movie_person_person ON movie_person(person_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_extras_movie ON extras(movie_id)")


# v11：标题来源标记 + 存量自动愈合（修复「文件名标题挡住 TMDB 标题」回归）
def _m11(c) -> None:
    _ensure_columns(c, "movies", [
        ("title_auto", "ALTER TABLE movies ADD COLUMN title_auto INTEGER DEFAULT 0"),
    ])
    try:
        from ..scanner.parse import parse_filename, normalize_title
    except Exception as e:   # 解析器不可用时跳过回填（只加列）
        logger.warning("title_auto backfill skipped: %s", e)
        return
    rows = c.execute("SELECT id, file_path, title, tmdb_id FROM movies "
                     "WHERE title IS NOT NULL AND title != ''").fetchall()
    healed = 0
    for r in rows:
        try:
            base = os.path.basename(str(r["file_path"] or "").replace("\\", "/"))
            parsed = normalize_title(parse_filename(base).get("title") or "")
        except Exception:
            continue
        if not parsed or parsed != r["title"]:
            continue
        # 标题与「按路径反解」一致 → 视为扫描自动写入，允许 TMDB 覆盖
        c.execute("UPDATE movies SET title_auto=1 WHERE id=?", (r["id"],))
        tid = r["tmdb_id"]
        if not tid:
            continue
        crow = c.execute("SELECT title FROM tmdb_cache WHERE tmdb_id=?", (tid,)).fetchone()
        cache_title = (crow["title"] if crow else "") or ""
        if cache_title and cache_title != r["title"]:
            c.execute("UPDATE movies SET title=?, title_auto=0 WHERE id=?",
                      (cache_title, r["id"]))
            healed += 1
    if healed:
        logger.info("title_auto migration healed %s titles from tmdb_cache", healed)


# v12：多库（libraries + library_id 分区）+ 离线匹配索引 + TMDB 快照字段
def _m12(c) -> None:
    _seed_default_library(c)
    if _has_column(c, "movies", "library_id"):
        _ensure_columns(c, "movies", [
            ("match_source", "ALTER TABLE movies ADD COLUMN match_source TEXT DEFAULT ''"),
            ("nfo_hash", "ALTER TABLE movies ADD COLUMN nfo_hash TEXT DEFAULT ''"),
        ])
    else:
        _rebuild_table(c, "movies", _MOVIES_DDL, _MOVIE_COLUMNS,
                       copy_extra="library_id",
                       extra_values=str(DEFAULT_LIBRARY_ID))
    for ddl in _MOVIE_INDEX_DDL:
        c.execute(ddl)
    c.execute(_MOVIE_LIBRARY_INDEX_DDL)
    if _has_column(c, "extras", "library_id"):
        pass
    else:
        _rebuild_table(c, "extras", _EXTRAS_DDL, _EXTRAS_COLUMNS,
                       copy_extra="library_id",
                       extra_values=str(DEFAULT_LIBRARY_ID))
    for ddl in _EXTRAS_INDEX_DDL:
        c.execute(ddl)
    if not _has_column(c, "scan_state", "library_id"):
        _rebuild_table(c, "scan_state", _SCAN_STATE_DDL,
                       ["file_path", "library_id", "mtime", "size", "status", "updated_at"],
                       copy_extra="library_id",
                       extra_values=str(DEFAULT_LIBRARY_ID))
    _ensure_columns(c, "collections", [
        ("library_id", "ALTER TABLE collections ADD COLUMN library_id INTEGER NOT NULL DEFAULT 1"),
    ])
    _ensure_columns(c, "tmdb_cache", [
        ("source", "ALTER TABLE tmdb_cache ADD COLUMN source TEXT DEFAULT 'tmdb'"),
        ("payload_json", "ALTER TABLE tmdb_cache ADD COLUMN payload_json TEXT DEFAULT '{}'"),
        ("premiered", "ALTER TABLE tmdb_cache ADD COLUMN premiered TEXT DEFAULT ''"),
        ("tagline", "ALTER TABLE tmdb_cache ADD COLUMN tagline TEXT DEFAULT ''"),
        ("runtime", "ALTER TABLE tmdb_cache ADD COLUMN runtime INTEGER DEFAULT 0"),
        ("studios", "ALTER TABLE tmdb_cache ADD COLUMN studios TEXT DEFAULT '[]'"),
        ("backdrop_tmdb_path", "ALTER TABLE tmdb_cache ADD COLUMN backdrop_tmdb_path TEXT DEFAULT ''"),
        ("logo_tmdb_path", "ALTER TABLE tmdb_cache ADD COLUMN logo_tmdb_path TEXT DEFAULT ''"),
    ])


def _m13_rebuild_media_info(c) -> None:
    """旧 media_info(movie_id PK) → (kind, item_id) 复合键；movie_id → item_id。"""
    old_cols = _columns(c, "media_info")
    c.execute(_MEDIA_INFO_DDL.replace(
        "CREATE TABLE IF NOT EXISTS media_info", "CREATE TABLE media_info_new", 1))
    shared = [col for col in _MEDIA_INFO_COLUMNS
              if col in old_cols and col not in ("kind", "item_id")]
    cols_sql = ", ".join(shared)
    if "movie_id" in old_cols and shared:
        c.execute(f"INSERT INTO media_info_new (kind, item_id, {cols_sql})"
                  f" SELECT 'movie', movie_id, {cols_sql} FROM media_info")
    c.execute("DROP TABLE media_info")
    c.execute("ALTER TABLE media_info_new RENAME TO media_info")


def _m13_rebuild_progress(c) -> None:
    """旧 playback_progress(version_id PK) → (kind, item_id) 复合键。"""
    old_cols = _columns(c, "playback_progress")
    c.execute(_PROGRESS_DDL.replace(
        "CREATE TABLE IF NOT EXISTS playback_progress",
        "CREATE TABLE playback_progress_new", 1))
    shared = [col for col in ("position", "duration", "updated_at") if col in old_cols]
    cols_sql = ", ".join(shared)
    if "version_id" in old_cols and shared:
        c.execute(f"INSERT INTO playback_progress_new (kind, item_id, {cols_sql})"
                  f" SELECT 'movie', version_id, {cols_sql} FROM playback_progress")
    c.execute("DROP TABLE playback_progress")
    c.execute("ALTER TABLE playback_progress_new RENAME TO playback_progress")


def _m13(c) -> None:
    """TV 只读清单表 + media_info/playback_progress 复合键。"""
    if not _has_column(c, "media_info", "kind"):
        _m13_rebuild_media_info(c)
    if not _has_column(c, "playback_progress", "kind"):
        _m13_rebuild_progress(c)
    c.execute(_TV_SHOWS_DDL)
    c.execute(_TV_EPISODES_DDL)
    c.execute("CREATE INDEX IF NOT EXISTS idx_tv_shows_library ON tv_shows(library_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tv_episodes_show ON tv_episodes(show_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tv_episodes_library ON tv_episodes(library_id)")


def _m14(c) -> None:
    """远程库 path 归一为固定挂载点（app.mounts 按 data_dir/mounts/lib_<id> 挂载，
    path 必须同源，否则扫描用错目录；本地库不动）。"""
    try:
        rows = c.execute("SELECT id, path FROM libraries"
                         " WHERE source IN ('smb','nfs')").fetchall()
    except sqlite3.OperationalError:
        return
    for r in rows:
        want = mount_point(int(r["id"]))
        if (r["path"] or "") != want:
            c.execute("UPDATE libraries SET path=? WHERE id=?",
                      (want, int(r["id"])))
            logger.info("远程库 path 归一 lib=%s -> %s", r["id"], want)


_MIGRATION_STEPS = [(1, _m1), (2, _m2), (3, _m3), (4, _m4), (5, _m5), (6, _m6),
                    (7, _m7), (8, _m8), (9, _m9), (10, _m10), (11, _m11),
                    (12, _m12), (13, _m13), (14, _m14)]


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
        _migrated = _migrate(c)
        # FTS 旧 trigger 内容表形态自愈（content= 老库直接重建）
        sql = (c.execute("SELECT sql FROM sqlite_master WHERE name='movies_fts'").fetchone() or [""])[0]
        if "content=" in sql:
            c.execute("DROP TABLE movies_fts")
            c.executescript(SCHEMA)
    from .search import rebuild_fts, fts_needs_rebuild
    from .tmdb_cache import seed_tmdb_cache_from_movies
    from .match_index import seed_match_index_from_cache
    with _lock, _conn() as c:
        c.execute("PRAGMA journal_mode=WAL")   # R02-D2：写不阻塞读、崩溃恢复更好
    seed_tmdb_cache_from_movies()
    seed_match_index_from_cache()
    # R02-D3：FTS 与 movies 行数一致时跳过全量重建（大库启动不再 O(n) 连接+写）；
    # 本次跑过迁移（可能改标题/新列）则强制重建，避免索引与数据脱节
    if _migrated[1] or fts_needs_rebuild():
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
            "writable": bool(writable), "error": err, "bytes": db_bytes,
            "schema_version": SCHEMA_VERSION}


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
    lib_id = d.get("library_id")
    if key and lib_id is not None:
        vers = [{"id": r["id"], "file_path": r["file_path"],
                 "edition": r["edition"] or "",
                 "spec": r["spec"] or "",
                 "library_id": r["library_id"]}
                for r in c.execute(
            "SELECT id, file_path, edition, spec, library_id FROM movies"
            " WHERE tmdb_id=? AND library_id=? ORDER BY file_path", (key, lib_id))]
    elif key:
        vers = [{"id": r["id"], "file_path": r["file_path"],
                 "edition": r["edition"] or "",
                 "spec": r["spec"] or "",
                 "library_id": r["library_id"]}
                for r in c.execute(
            "SELECT id, file_path, edition, spec, library_id FROM movies"
            " WHERE tmdb_id=? ORDER BY file_path", (key,))]
    else:
        vers = [{"id": d["id"], "file_path": d["file_path"],
                 "edition": d.get("edition") or "",
                 "spec": d.get("spec") or "",
                 "library_id": d.get("library_id", DEFAULT_LIBRARY_ID)}]
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


__all__ = ['_conn', 'init_db', 'SCHEMA_VERSION', 'DEFAULT_LIBRARY_ID', 'health_check',
           '_dump_list', '_row_to_dict', '_film_key', '_attach_versions',
           '_collections_for_film', '_lock', 'SCHEMA', 'TMDB_FIELDS', 'LOCAL_FIELDS',
           'APP_SETTING_KEYS', 'logger']
