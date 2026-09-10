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
        ):
            if col not in pcols:
                c.execute(ddl)
        c.execute("CREATE INDEX IF NOT EXISTS idx_movies_year ON movies(year)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_movies_region ON movies(region)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_movies_origin ON movies(origin_country)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_movies_tmdb ON movies(tmdb_id)")
        sql = (c.execute("SELECT sql FROM sqlite_master WHERE name='movies_fts'").fetchone() or [""])[0]
        if "content=" in sql:
            c.execute("DROP TABLE movies_fts")
            c.executescript(SCHEMA)
    rebuild_fts()


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


def upsert_person(tmdb_id: int, name: str, avatar: str | None = None) -> int:
    """avatar 非 None 时更新（含 '-' 标记“确认无照片”，避免回填反复重试）。"""
    with _lock, _conn() as c:
        c.execute("INSERT OR IGNORE INTO persons(tmdb_id, name) VALUES(?, ?)", (tmdb_id, name))
        c.execute("UPDATE persons SET name=? WHERE tmdb_id=?", (name, tmdb_id))
        if avatar is not None:
            c.execute("UPDATE persons SET avatar=? WHERE tmdb_id=?", (avatar, tmdb_id))
        row = c.execute("SELECT id FROM persons WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        return int(row["id"])


def persons_missing_avatar(movie_id: int) -> bool:
    """该影片是否还有未确认头像的关联人物（avatar 为空即未尝试过）。"""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM movie_person mp JOIN persons p ON p.id=mp.person_id "
            "WHERE mp.movie_id=? AND (p.avatar IS NULL OR p.avatar='')",
            (movie_id,)).fetchone()
        return int(row["n"]) > 0


def update_person_bio(tmdb_id: int, biography: str = "",
                      birthday: str = "", place_of_birth: str = "") -> None:
    with _lock, _conn() as c:
        c.execute("UPDATE persons SET biography=?, birthday=?, place_of_birth=? "
                  "WHERE tmdb_id=?", (biography or "", birthday or "",
                                      place_of_birth or "", tmdb_id))


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
