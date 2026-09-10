"""SQLite存储层（标准库sqlite3 + FTS5全文检索：片名/原名/简介/演员/标签/类型）"""
import json
import sqlite3
import threading
import time

from .config import settings
from .db import DB_PATH, ensure_dirs

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
  needs_review INTEGER DEFAULT 0,
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
  fetched_at INTEGER DEFAULT 0
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
                "douban_rating", "custom_rating", "tags", "needs_review"}


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
        c.execute("CREATE INDEX IF NOT EXISTS idx_cache_fetched ON tmdb_cache(fetched_at)")
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
    meta 为 meta_from_detail() 产出的 TMDB 列字典。返回 True=内容变化。"""
    now = int(time.time())
    genres_s = _dump_list(meta.get("genres"))
    genre_ids_s = _dump_list(meta.get("genre_ids"))
    origin_countries_s = _dump_list(meta.get("origin_countries"))
    credits_s = json.dumps(credits or {"cast": [], "crew": []}, ensure_ascii=False, sort_keys=True)
    poster_tmdb_path = poster_tmdb_path or ""
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM tmdb_cache WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        if not row:
            c.execute(
                "INSERT INTO tmdb_cache(tmdb_id, title, original_title, year, overview,"
                " imdb_id, tmdb_rating, genres, genre_ids, origin_country, origin_countries,"
                " original_language, region, media_type, poster_tmdb_path, credits, fetched_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tmdb_id, meta.get("title", "") or "", meta.get("original_title", "") or "",
                 meta.get("year"), meta.get("overview", "") or "",
                 meta.get("imdb_id", "") or "", meta.get("tmdb_rating"),
                 genres_s, genre_ids_s,
                 meta.get("origin_country", "") or "", origin_countries_s,
                 meta.get("original_language", "") or "", meta.get("region", "") or "",
                 meta.get("media_type", "") or "movie",
                 poster_tmdb_path, credits_s, now))
            return True
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
        )
        if same:
            c.execute("UPDATE tmdb_cache SET fetched_at=? WHERE tmdb_id=?", (now, tmdb_id))
            return False
        c.execute(
            "UPDATE tmdb_cache SET title=?, original_title=?, year=?, overview=?,"
            " imdb_id=?, tmdb_rating=?, genres=?, genre_ids=?, origin_country=?,"
            " origin_countries=?, original_language=?, region=?, media_type=?,"
            " poster_tmdb_path=?, credits=?, fetched_at=? WHERE tmdb_id=?",
            (meta.get("title", "") or "", meta.get("original_title", "") or "",
             meta.get("year"), meta.get("overview", "") or "",
             meta.get("imdb_id", "") or "", meta.get("tmdb_rating"),
             genres_s, genre_ids_s,
             meta.get("origin_country", "") or "", origin_countries_s,
             meta.get("original_language", "") or "", meta.get("region", "") or "",
             meta.get("media_type", "") or "movie",
             poster_tmdb_path, credits_s, now, tmdb_id))
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
        return int(row["id"])


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
               "origin_country", "origin_countries", "original_language",
               "region", "media_type"}
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


def _attach_versions(c: sqlite3.Connection, d: dict) -> dict:
    key = d.get("tmdb_id")
    if key:
        vers = [{"id": r["id"], "file_path": r["file_path"]} for r in c.execute(
            "SELECT id, file_path FROM movies WHERE tmdb_id=? ORDER BY file_path", (key,))]
    else:
        vers = [{"id": d["id"], "file_path": d["file_path"]}]
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
        return _attach_versions(c, d)


def list_movies(grouped: bool = True, genres: list | None = None,
                regions: list | None = None, countries: list | None = None,
                years: list | None = None, decades: list | None = None,
                tags: list | None = None, limit: int = 500,
                min_rating: float | None = None,
                rating_source: str | None = None) -> list[dict]:
    where, params = _structured_where("movies", genres=genres, regions=regions,
                                      countries=countries, years=years,
                                      decades=decades, tags=tags,
                                      min_rating=min_rating,
                                      rating_source=rating_source)
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
               rating_source: str | None = None) -> list[dict]:
    fwhere, fparams = _structured_where("m", genres=genres, regions=regions,
                                        countries=countries, years=years,
                                        decades=decades, tags=tags,
                                        min_rating=min_rating,
                                        rating_source=rating_source)
    q = (q or "").strip()
    if not q:
        return list_movies(grouped=grouped, genres=genres, regions=regions,
                           countries=countries, years=years, decades=decades,
                           tags=tags, limit=limit, min_rating=min_rating,
                           rating_source=rating_source)
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
                      min_rating=None, rating_source=None) -> tuple[str, tuple]:
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
    gc, rc, cc, yc, dc, tc, ic, sc = (Counter() for _ in range(8))
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
        "ratings": {src: [{"min": s, "count": sc.get((src, s), 0)} for s in RATING_STEPS]
                    for src in RATING_SOURCES},
    }
