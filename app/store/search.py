"""store.search（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。"""
import re
import sqlite3
import time

from ..log import get_logger
from ._base import DEFAULT_LIBRARY_ID, _attach_versions, _conn, _like_esc, _lock, _row_to_dict

logger = get_logger("store.search")

__all__ = ['get_scan_state', 'set_scan_state', 'fts_needs_rebuild', 'rebuild_fts', 'resync_fts', 'list_movies', '_query_terms', '_fts_query',
           '_search_like', 'suggest_titles', 'suggest_people', 'search_fts',
           '_split_multi', '_split_ints', '_rating_col', '_structured_where', 'get_facets']

def get_scan_state(file_path: str,
                   library_id: int = DEFAULT_LIBRARY_ID) -> dict | None:
    """扫描增量状态（评审 B9/R03-Q3）。无行/表缺失返回 None。"""
    with _lock, _conn() as c:
        try:
            row = c.execute("SELECT * FROM scan_state WHERE file_path=? AND library_id=?",
                            (file_path, int(library_id))).fetchone()
        except sqlite3.OperationalError:
            return None
        return dict(row) if row else None


def set_scan_state(file_path: str, mtime: int, size: int, status: str,
                   library_id: int = DEFAULT_LIBRARY_ID) -> None:
    with _lock, _conn() as c:
        try:
            c.execute(
                "INSERT INTO scan_state(file_path, library_id, mtime, size, status,"
                " updated_at) VALUES(?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(library_id, file_path) DO UPDATE SET mtime=excluded.mtime,"
                " size=excluded.size, status=excluded.status,"
                " updated_at=excluded.updated_at",
                (file_path, int(library_id), int(mtime or 0), int(size or 0),
                 str(status or ""), int(time.time())))
        except sqlite3.OperationalError as e:
            logger.debug("set scan_state failed path=%s: %s", file_path, e)


def fts_needs_rebuild() -> bool:
    """FTS 行数与 movies 不一致（或表缺失）→ 需要全量重建（评审 R02-D3）。"""
    with _lock, _conn() as c:
        try:
            n_movies = int(c.execute("SELECT COUNT(*) FROM movies").fetchone()[0])
            n_fts = int(c.execute("SELECT COUNT(*) FROM movies_fts").fetchone()[0])
        except sqlite3.OperationalError:
            return True
    return n_movies != n_fts


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


def list_movies(grouped: bool = True, genres: list | None = None,
                regions: list | None = None, countries: list | None = None,
                years: list | None = None, decades: list | None = None,
                tags: list | None = None, limit: int = 500,
                min_rating: float | None = None,
                rating_source: str | None = None,
                watched: int | None = None,
                collection_ids: list | None = None,
                library_ids: list | int | None = None,
                offset: int = 0) -> list[dict]:
    where, params = _structured_where("movies", genres=genres, regions=regions,
                                      countries=countries, years=years,
                                      decades=decades, tags=tags,
                                      min_rating=min_rating,
                                      rating_source=rating_source,
                                      watched=watched,
                                      collection_ids=collection_ids,
                                      library_ids=library_ids)
    try:
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        off = 0
    with _lock, _conn() as c:
        if not grouped:
            rows = c.execute(
                f"SELECT * FROM movies WHERE {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (*params, limit, off))
            return [_row_to_dict(r) for r in rows]
        rows = c.execute(
            f"SELECT *, MAX(updated_at) AS _u FROM movies WHERE {where} "
            f"GROUP BY library_id, COALESCE(tmdb_id, -id) ORDER BY _u DESC LIMIT ? OFFSET ?",
            (*params, limit, off))
        return [_attach_versions(c, _row_to_dict(r)) for r in rows]


def _query_terms(q: str) -> list[str]:
    """提取查询词（unicode \\w：中英文均可），丢弃引号/冒号等 FTS 语法字符。"""
    return re.findall(r"\w+", q or "")


def _fts_query(q: str) -> str:
    """转成安全的 FTS5 查询：逐词加引号，末词前缀（边输边搜）。无有效词返回 ''。"""
    toks = _query_terms(q)
    if not toks:
        return ""
    return " ".join(f'"{t}"' + ("*" if i == len(toks) - 1 else "")
                    for i, t in enumerate(toks))


def _search_like(c: sqlite3.Connection, q: str, fwhere: str, fparams: list,
                 limit: int, grouped: bool, offset: int = 0):
    """FTS 无命中/语法异常时的兜底：标题/原名/演员按词 AND 子串匹配（中文部分词可用）。"""
    toks = _query_terms(q)
    if not toks:
        return []
    conds, params = [], []
    for t in toks:
        p = f"%{_like_esc(t)}%"
        conds.append("(m.title LIKE ? ESCAPE '\\' OR m.original_title LIKE ? ESCAPE '\\'"
                     " OR m.person_names LIKE ? ESCAPE '\\')")
        params.extend([p, p, p])
    where = " AND ".join(conds)
    prefix = f"{_like_esc(toks[0])}%"
    if not grouped:
        return c.execute(
            f"SELECT m.* FROM movies m WHERE ({where}) AND ({fwhere}) "
            "ORDER BY (CASE WHEN m.title LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END),"
            " m.year DESC, m.id LIMIT ? OFFSET ?",
            (*params, *fparams, prefix, limit, offset)).fetchall()
    return c.execute(
        f"SELECT m.*, MIN(CASE WHEN m.title LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END) AS _pref "
        f"FROM movies m WHERE ({where}) AND ({fwhere}) "
        "GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id) "
        "ORDER BY _pref, MAX(m.year) DESC, m.id LIMIT ? OFFSET ?",
        (prefix, *params, *fparams, limit, offset)).fetchall()


def suggest_titles(q: str, limit: int = 8, library_ids=None) -> list[dict]:
    """搜索框联想：本地库标题/原名子串匹配（LIKE，中文/部分词可用），
    前缀命中优先、年份降序，按 tmdb_id 归并多版本。返回 [{id, tmdb_id, title, original_title, year}]。"""
    toks = _query_terms(q)
    if not toks:
        return []
    limit = max(1, min(int(limit or 8), 20))
    conds, params = [], []
    for t in toks:
        p = f"%{_like_esc(t)}%"
        conds.append("(m.title LIKE ? ESCAPE '\\' OR m.original_title LIKE ? ESCAPE '\\')")
        params.extend([p, p])
    libs = _split_ints(library_ids) if library_ids is not None else []
    if libs:
        conds.append("m.library_id IN (%s)" % ",".join("?" * len(libs)))
        params.extend(libs)
    where = " AND ".join(conds)
    prefix = f"{_like_esc(toks[0])}%"
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT m.id, m.tmdb_id, m.title, m.original_title, m.year, "
            "MIN(CASE WHEN m.title LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END) AS _pref "
            "FROM movies m WHERE " + where +
            " GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id) "
            "ORDER BY _pref, MAX(m.year) DESC, m.id LIMIT ?",
            (prefix, *params, limit)).fetchall()
        return [{"id": r["id"], "tmdb_id": r["tmdb_id"], "title": r["title"],
                 "original_title": r["original_title"], "year": r["year"]}
                for r in rows]


def suggest_people(q: str, limit: int = 5, library_ids=None) -> list[dict]:
    """搜索框联想（演员）：persons.name 子串匹配，名字前缀优先、库内参演数降序。
    返回 [{tmdb_id, name, count}]。"""
    toks = _query_terms(q)
    if not toks:
        return []
    limit = max(1, min(int(limit or 5), 20))
    conds, params = [], []
    for t in toks:
        p = f"%{_like_esc(t)}%"
        conds.append("p.name LIKE ? ESCAPE '\\'")
        params.append(p)
    libs = _split_ints(library_ids) if library_ids is not None else []
    if libs:
        conds.append("m.library_id IN (%s)" % ",".join("?" * len(libs)))
        params.extend(libs)
    where = " AND ".join(conds)
    prefix = f"{_like_esc(toks[0])}%"
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT p.tmdb_id, p.name, COUNT(mp.movie_id) AS n "
            "FROM persons p LEFT JOIN movie_person mp ON mp.person_id = p.id "
            "LEFT JOIN movies m ON m.id = mp.movie_id "
            f"WHERE {where} "
            "GROUP BY p.tmdb_id "
            "ORDER BY (p.name LIKE ? ESCAPE '\\') DESC, n DESC, p.name LIMIT ?",
            (*params, prefix, limit)).fetchall()
        return [{"tmdb_id": r["tmdb_id"], "name": r["name"], "count": int(r["n"] or 0)}
                for r in rows]


def search_fts(q: str, limit: int = 50, grouped: bool = True,
               genres: list | None = None, regions: list | None = None,
               countries: list | None = None, years: list | None = None,
               decades: list | None = None, tags: list | None = None,
               min_rating: float | None = None,
               rating_source: str | None = None,
               watched: int | None = None,
               collection_ids: list | None = None,
               library_ids: list | int | None = None,
               offset: int = 0) -> list[dict]:
    fwhere, fparams = _structured_where("m", genres=genres, regions=regions,
                                        countries=countries, years=years,
                                        decades=decades, tags=tags,
                                        min_rating=min_rating,
                                        rating_source=rating_source,
                                        watched=watched,
                                        collection_ids=collection_ids,
                                        library_ids=library_ids)
    try:
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        off = 0
    q = (q or "").strip()
    if not q:
        return list_movies(grouped=grouped, genres=genres, regions=regions,
                           countries=countries, years=years, decades=decades,
                           tags=tags, limit=limit, min_rating=min_rating,
                           rating_source=rating_source, watched=watched,
                           collection_ids=collection_ids, library_ids=library_ids,
                           offset=off)
    fts_q = _fts_query(q)
    with _lock, _conn() as c:
        rows = []
        if fts_q:
            try:
                if not grouped:
                    rows = c.execute(
                        "SELECT m.* FROM movies_fts f JOIN movies m ON m.id=f.rowid "
                        f"WHERE movies_fts MATCH ? AND ({fwhere}) ORDER BY rank LIMIT ? OFFSET ?",
                        (fts_q, *fparams, limit, off)).fetchall()
                else:
                    rows = c.execute(
                        "SELECT m.*, MIN(rank) AS _r FROM movies_fts f JOIN movies m ON m.id=f.rowid "
                        f"WHERE movies_fts MATCH ? AND ({fwhere}) "
                        "GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id) "
                        "ORDER BY _r LIMIT ? OFFSET ?", (fts_q, *fparams, limit, off)).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            # FTS 零命中时用 LIKE 兜底（同一查询各页行为一致，OFFSET 可继续翻页）
            rows = _search_like(c, q, fwhere, fparams, limit, grouped, off)
        if not grouped:
            return [_row_to_dict(r) for r in rows]
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
                      watched=None, collection_ids=None,
                      library_ids=None) -> tuple[str, tuple]:
    """结构化过滤：facet内OR、facet间AND；tags多选为AND；min_rating为单阈值（>=）。
    `library_ids`（int/列表）为库分区条件（多库 v12）。返回 (where_sql, params)。"""
    from ..regions import REGION_UNKNOWN
    conds: list[str] = []
    params: list = []
    libs = _split_ints(library_ids) if library_ids is not None else []
    if libs:
        conds.append(f"{alias}.library_id IN (%s)" % ",".join("?" * len(libs)))
        params.extend(libs)
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


def get_facets(grouped: bool = True, library_ids=None) -> dict:
    """库内实际计数的动态facets：只返回 count>0 项，供前端直接渲染。
    `library_ids` 限定统计范围（多库 v12）。"""
    from collections import Counter
    from ..regions import REGION_ORDER, REGION_UNKNOWN, country_name
    libs = _split_ints(library_ids) if library_ids is not None else []
    where, params = "", []
    if libs:
        where = " WHERE library_id IN (%s)" % ",".join("?" * len(libs))
        params.extend(libs)
    cwhere, cparams = "", []
    if libs:
        # 合集跟随媒体库（v18）：按所选视频库所属媒体库统计
        cwhere = (" WHERE c.media_library_id IN (SELECT media_library_id FROM libraries"
                  " WHERE id IN (%s))" % ",".join("?" * len(libs)))
        cparams.extend(libs)
    with _lock, _conn() as c:
        # 全量取行后 Python 内分组（组内 tags 取并集，代表行取最新），避免代表行漏掉打在旧版本上的标签；
        # 只取聚合所需列（评审 B8/R02-D4：不再把 overview/persons 大文本全捞进内存）
        rows = c.execute(
            "SELECT id, library_id, tmdb_id, updated_at, watched, genres, tags, region,"
            " origin_country, origin_countries, year, tmdb_rating, douban_rating,"
            " custom_rating FROM movies" + where, params).fetchall()
        try:
            collections = [{"id": r["id"], "name": r["name"], "count": int(r["n"] or 0)}
                           for r in c.execute(
                               "SELECT c.id, c.name, COUNT(cm.rowid) AS n FROM collections c"
                               " LEFT JOIN collection_members cm ON cm.collection_id=c.id"
                               + cwhere +
                               " GROUP BY c.id ORDER BY c.name", cparams).fetchall()]
        except Exception as e:
            logger.warning("facets collections failed: %s", e)
            collections = []
    gc, rc, cc, yc, dc, tc, ic, sc = (Counter() for _ in range(8))
    wc = Counter()
    if grouped:
        # 同库同 tmdb_id 的多版本取代表行计数；tags 取组内并集（避免标签打在非代表版本上被漏计）
        groups: dict = {}
        for r in rows:
            d = _row_to_dict(r)
            key = (d.get("library_id"), d.get("tmdb_id") or -d["id"])
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

