"""store.similar：详情页「库中类似」（Plex 式推荐）——纯本地相似度，不调网。

信号与权重（分数越高越像）：
- 同 TMDB 系列 +100；同手工合集 +30/个（封顶 60）
- 同导演 +36/人（封顶 72）；同主演按番位 6–26 分/人（封顶 60）
- 类型加权 Jaccard（IDF：冷门类型权重更高）×45
- 本地标签 +8/个（封顶 24）；同产地 +6；同原语言 +5；年份相近 ≤+8
入选门槛：至少命中一个内容信号（系列/合集/导演/主演/类型/标签）且总分 ≥ 18。
"""
import json
import math

from ._base import DEFAULT_LIBRARY_ID, _conn, _lock, _row_to_dict, logger

__all__ = ['similar_movies']

_MIN_SCORE = 18.0
_GENRE_WEIGHT = 45.0
_COLLECTION_WEIGHT = 100.0
_DIRECTOR_WEIGHT = 36.0
_DIRECTOR_CAP = 72.0
_ACTOR_CAP = 60.0
_TAG_WEIGHT = 8.0
_TAG_CAP = 24.0
_MANUAL_WEIGHT = 30.0
_MANUAL_CAP = 60.0
_REGION_WEIGHT = 6.0
_LANG_WEIGHT = 5.0
_YEAR_WEIGHT = 8.0
_YEAR_SPAN = 20.0
_CAND_GENRE = 200
_CAND_DIRECTOR = 80
_CAND_ACTOR = 120
_CAND_TAG = 80
_CAND_MANUAL = 60


def _int_set(v) -> set:
    out = set()
    for x in v or []:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out


def _json_list(s) -> list:
    try:
        v = json.loads(s or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _manual_collections(c, tmdb_id, movie_id, media_library_id=None) -> set:
    """本片所属手工合集 id（海报粒度：有 tmdb_id 按 tmdb，无按单行 id）。
    media_library_id 给定时只认同媒体库合集（合集跟随媒体库 v18，跨媒体库不参与“同合集”）。"""
    try:
        lib_sql, lib_params = "", []
        if media_library_id is not None:
            lib_sql = (" JOIN collections col ON col.id=cm.collection_id"
                       " AND col.media_library_id=?")
            lib_params = [int(media_library_id)]
        if tmdb_id:
            rows = c.execute("SELECT cm.collection_id FROM collection_members cm"
                             + lib_sql + " WHERE cm.movie_tmdb_id=?", (*lib_params, int(tmdb_id)))
        else:
            rows = c.execute("SELECT cm.collection_id FROM collection_members cm"
                             + lib_sql + " WHERE cm.movie_id=?", (*lib_params, int(movie_id)))
        return {int(r["collection_id"]) for r in rows}
    except Exception as e:
        logger.warning("similar manual collections failed mid=%s: %s", movie_id, e)
        return set()


def _score_candidates(cur: dict, reps: list[dict], links: dict, mems: dict,
                      df: dict, total: int) -> list[dict]:
    """逐候选打分并生成推荐条目（返回未排序列表）。"""
    cur_genres = cur["genres"]
    cur_genre_w = {g: math.log(1 + total / max(1, df.get(g, 1))) for g in cur_genres}
    out = []
    for d in reps:
        key = d["_key"]
        gs = d["_genres"]
        shared_genres = cur_genres & gs
        shared_dirs = cur["dirs"] & links.get(key, {}).get("dirs", set())
        shared_actors = set(cur["actors"]) & links.get(key, {}).get("actors", set())
        shared_tags = cur["tags"] & d["_tags"]
        shared_manual = cur["manual"] & mems.get(key, set())
        same_col = bool(cur["collection"] and d["_collection"] == cur["collection"])

        signals: list[tuple[float, str]] = []
        score = 0.0
        if same_col:
            score += _COLLECTION_WEIGHT
            signals.append((_COLLECTION_WEIGHT, "同系列"))
        if shared_manual:
            s = min(_MANUAL_CAP, _MANUAL_WEIGHT * len(shared_manual))
            score += s
            signals.append((s, "同合集"))
        if shared_dirs:
            s = min(_DIRECTOR_CAP, _DIRECTOR_WEIGHT * len(shared_dirs))
            score += s
            signals.append((s, "同导演"))
        if shared_actors:
            s = 0.0
            for pid in shared_actors:
                order = cur["actors"].get(pid, 10)
                s += max(6.0, 26.0 - 2.0 * min(int(order or 10), 10))
            s = min(_ACTOR_CAP, s)
            score += s
            signals.append((s, "同主演"))
        if shared_genres:
            union = cur_genres | gs
            union_w = sum(math.log(1 + total / max(1, df.get(g, 1))) for g in union) or 1.0
            s = _GENRE_WEIGHT * sum(cur_genre_w[g] for g in shared_genres) / union_w
            score += s
            signals.append((s, "同类型"))
        if shared_tags:
            s = min(_TAG_CAP, _TAG_WEIGHT * len(shared_tags))
            score += s
            signals.append((s, "同标签"))
        if not signals:      # 无内容信号：产地/语言/年份再近也不算“类似”
            continue
        if cur["region"] and d["_region"] == cur["region"]:
            score += _REGION_WEIGHT
        if cur["lang"] and d["_lang"] == cur["lang"]:
            score += _LANG_WEIGHT
        if isinstance(cur["year"], int) and isinstance(d.get("year"), int):
            score += _YEAR_WEIGHT * max(0.0, 1.0 - abs(cur["year"] - d["year"]) / _YEAR_SPAN)
        if score < _MIN_SCORE:
            continue
        signals.sort(key=lambda x: -x[0])
        out.append({
            "id": d["id"], "tmdb_id": d.get("tmdb_id"),
            "title": d.get("title") or "", "year": d.get("year"),
            "poster_path": d.get("poster_path") or "",
            "tmdb_rating": d.get("tmdb_rating"),
            "douban_rating": d.get("douban_rating"),
            "custom_rating": d.get("custom_rating"),
            "region": d["_region"], "genres": (d.get("genres") or [])[:3],
            "version_count": d["_vers"],
            "reason": " · ".join(t for _, t in signals[:2]),
            "score": round(score, 1),
        })
    return out


def similar_movies(movie_id: int, limit: int = 12) -> list[dict]:
    """库内相似推荐：排除本片全部版本，返回按相似度排序的条目。纯本地只读。"""
    try:
        limit = max(1, min(int(limit), 30))
    except (TypeError, ValueError):
        limit = 12
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not row:
            return []
        m = _row_to_dict(row)
        tid = m.get("tmdb_id")
        lib_id = m.get("library_id") or DEFAULT_LIBRARY_ID
        if tid:
            ver_ids = [int(r["id"]) for r in c.execute(
                "SELECT id FROM movies WHERE tmdb_id=? AND library_id=?",
                (tid, lib_id))]
            cur_key = ("t", int(tid))
        else:
            ver_ids = [int(m["id"])]
            cur_key = ("m", int(m["id"]))
        # ---- 本片信号 ----
        cur_col = None
        if tid:
            cr = c.execute("SELECT collection_tmdb_id AS cid FROM tmdb_cache"
                           " WHERE tmdb_id=?", (tid,)).fetchone()
            cur_col = int(cr["cid"]) if cr and cr["cid"] else None
        cur_genres = _int_set(m.get("genre_ids"))
        cur_tags = {str(x) for x in (m.get("tags") or [])}
        persons = c.execute(
            "SELECT mp.person_id, mp.role, mp.cast_order FROM movie_person mp"
            " WHERE mp.movie_id IN (%s)"
            " AND (mp.role='director'"
            " OR (mp.role='actor' AND COALESCE(mp.cast_order, 99)<=10))"
            % ",".join("?" * len(ver_ids)),
            tuple(ver_ids)).fetchall()
        cur_dirs: set = set()
        cur_actors: dict = {}
        for r in persons:
            pid = int(r["person_id"])
            if r["role"] == "director":
                cur_dirs.add(pid)
            elif r["role"] == "actor":
                order = int(r["cast_order"] if r["cast_order"] is not None else 99)
                if pid not in cur_actors or order < cur_actors[pid]:
                    cur_actors[pid] = order
        cur_actors = {pid: o for pid, o in cur_actors.items() if o <= 10}
        mrow = c.execute("SELECT media_library_id FROM libraries WHERE id=?",
                         (lib_id,)).fetchone()
        cur_manual = _manual_collections(
            c, tid, int(m["id"]), int(mrow["media_library_id"]) if mrow else None)
        # 同系列的手工合集不重复计分（避免“同系列 · 同合集”）
        if cur_col:
            try:
                series_cols = {int(r["id"]) for r in c.execute(
                    "SELECT id FROM collections WHERE tmdb_collection_id=?"
                    " AND library_id=?", (cur_col, lib_id))}
                cur_manual -= series_cols
            except Exception:
                pass
        if not (cur_col or cur_manual or cur_dirs or cur_actors or cur_genres or cur_tags):
            return []

        # ---- 候选收集（各信号分头限量，避免大库全表爆量） ----
        cand: set = set()

        def _add(rows):
            for r in rows:
                cand.add(int(r["id"]))

        if cur_col:
            _add(c.execute(
                "SELECT m.id FROM movies m JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id"
                " WHERE t.collection_tmdb_id=? AND m.library_id=?"
                " GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id)",
                (cur_col, lib_id)).fetchall())
        if cur_dirs:
            _add(c.execute(
                "SELECT m.id, MAX(m.updated_at) AS _u FROM movies m"
                " JOIN movie_person mp ON mp.movie_id=m.id"
                " WHERE mp.role='director' AND mp.person_id IN (%s)"
                " AND m.library_id=?"
                " GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id) ORDER BY _u DESC LIMIT ?"
                % ",".join("?" * len(cur_dirs)),
                (*cur_dirs, lib_id, _CAND_DIRECTOR)).fetchall())
        if cur_actors:
            _add(c.execute(
                "SELECT m.id, MAX(m.updated_at) AS _u FROM movies m"
                " JOIN movie_person mp ON mp.movie_id=m.id"
                " WHERE mp.role='actor' AND mp.person_id IN (%s)"
                " AND m.library_id=?"
                " GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id) ORDER BY _u DESC LIMIT ?"
                % ",".join("?" * len(cur_actors)),
                (*cur_actors, lib_id, _CAND_ACTOR)).fetchall())
        if cur_tags:
            _add(c.execute(
                "SELECT m.id, MAX(m.updated_at) AS _u FROM movies m"
                " WHERE m.library_id=?"
                " AND EXISTS (SELECT 1 FROM json_each(m.tags) je WHERE je.value IN (%s))"
                " GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id) ORDER BY _u DESC LIMIT ?"
                % ",".join("?" * len(cur_tags)),
                (lib_id, *sorted(cur_tags), _CAND_TAG)).fetchall())
        if cur_manual:
            _add(c.execute(
                "SELECT m.id, MAX(m.updated_at) AS _u FROM movies m"
                " JOIN collection_members cm ON"
                " ((cm.movie_tmdb_id IS NOT NULL AND cm.movie_tmdb_id=m.tmdb_id)"
                " OR (cm.movie_id IS NOT NULL AND cm.movie_id=m.id))"
                " WHERE cm.collection_id IN (%s) AND m.library_id=?"
                " GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id) ORDER BY _u DESC LIMIT ?"
                % ",".join("?" * len(cur_manual)),
                (*sorted(cur_manual), lib_id, _CAND_MANUAL)).fetchall())
        if cur_genres:
            _add(c.execute(
                "SELECT id FROM (SELECT m.id AS id,"
                " (SELECT COUNT(*) FROM json_each(m.genre_ids) je WHERE je.value IN (%s))"
                " AS hits FROM movies m WHERE m.library_id=?"
                " GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id))"
                " WHERE hits>0 ORDER BY hits DESC LIMIT ?"
                % ",".join("?" * len(cur_genres)),
                (*sorted(cur_genres), lib_id, _CAND_GENRE)).fetchall())
        cand.discard(int(m["id"]))
        if not cand:
            return []

        # ---- 候选代表行（只取打分所需列，避免大库全列 JSON 解析） ----
        ids = sorted(cand)
        rows = c.execute(
            "SELECT m.id, m.tmdb_id, m.title, m.year, m.poster_path, m.tmdb_rating,"
            " m.douban_rating, m.custom_rating, m.region, m.original_language,"
            " m.genres, m.genre_ids, m.tags, MAX(m.updated_at) AS _u,"
            " t.collection_tmdb_id AS _cid"
            " FROM movies m LEFT JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id"
            " WHERE m.id IN (%s) GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id)"
            % ",".join("?" * len(ids)), tuple(ids)).fetchall()
        reps = []
        tids, mids = [], []
        for r in rows:
            tid_v = r["tmdb_id"]
            key = ("t", int(tid_v)) if tid_v else ("m", int(r["id"]))
            if key == cur_key:
                continue      # 本片自身/其它版本
            d = {
                "id": int(r["id"]), "tmdb_id": tid_v, "title": r["title"] or "",
                "year": r["year"], "poster_path": r["poster_path"] or "",
                "tmdb_rating": r["tmdb_rating"], "douban_rating": r["douban_rating"],
                "custom_rating": r["custom_rating"], "genres": _json_list(r["genres"]),
                "_key": key,
                "_collection": int(r["_cid"]) if r["_cid"] else None,
                "_genres": _int_set(_json_list(r["genre_ids"])),
                "_tags": {str(x) for x in _json_list(r["tags"])},
                "_region": r["region"] or "", "_lang": r["original_language"] or "",
                "_vers": 1,
            }
            reps.append(d)
            if key[0] == "t":
                tids.append(key[1])
            else:
                mids.append(key[1])
        if not reps:
            return []

        # ---- 候选演职员（跨全部版本聚合） ----
        links: dict = {}
        wheres, params = [], []
        if tids:
            wheres.append("m.tmdb_id IN (%s)" % ",".join("?" * len(tids)))
            params.extend(tids)
        if mids:
            wheres.append("m.id IN (%s)" % ",".join("?" * len(mids)))
            params.extend(mids)
        wheres.append("m.library_id=?")
        params.append(lib_id)
        for r in c.execute(
                "SELECT m.id AS mid, m.tmdb_id AS mtid, mp.person_id, mp.role"
                " FROM movie_person mp JOIN movies m ON m.id=mp.movie_id"
                " WHERE (" + " OR ".join(wheres) + ")"
                " AND (mp.role='director'"
                " OR (mp.role='actor' AND COALESCE(mp.cast_order, 99)<=10))",
                tuple(params)):
            key = ("t", int(r["mtid"])) if r["mtid"] else ("m", int(r["mid"]))
            g = links.setdefault(key, {"dirs": set(), "actors": set()})
            if r["role"] == "director":
                g["dirs"].add(int(r["person_id"]))
            else:
                g["actors"].add(int(r["person_id"]))

        # ---- 候选手工合集 ----
        mems: dict = {}
        if cur_manual:
            for r in c.execute(
                    "SELECT movie_tmdb_id, movie_id, collection_id FROM collection_members"
                    " WHERE collection_id IN (%s)" % ",".join("?" * len(cur_manual)),
                    tuple(sorted(cur_manual))):
                key = ("t", int(r["movie_tmdb_id"])) if r["movie_tmdb_id"] \
                    else ("m", int(r["movie_id"]))
                mems.setdefault(key, set()).add(int(r["collection_id"]))

        # ---- 版本数（海报粒度） ----
        if tids:
            vers_by_tid = {int(r["tmdb_id"]): int(r["n"] or 1) for r in c.execute(
                "SELECT tmdb_id, COUNT(*) AS n FROM movies WHERE tmdb_id IN (%s)"
                " GROUP BY tmdb_id" % ",".join("?" * len(tids)), tuple(tids))}
            for d in reps:
                if d["_key"][0] == "t":
                    d["_vers"] = vers_by_tid.get(d["_key"][1], 1)

        # ---- 类型 IDF（库内代表行，SQL 侧聚合） ----
        df: dict = {}
        total = 0
        try:
            total = int(c.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM movies"
                " GROUP BY COALESCE(tmdb_id, -id))").fetchone()[0])
            for r in c.execute(
                    "SELECT CAST(je.value AS INTEGER) AS g,"
                    " COUNT(DISTINCT COALESCE(m.tmdb_id, -m.id)) AS n"
                    " FROM movies m, json_each(m.genre_ids) je GROUP BY g"):
                df[int(r["g"])] = int(r["n"])
        except Exception as e:
            logger.warning("similar genre idf failed mid=%s: %s", movie_id, e)

    cur = {"genres": cur_genres, "tags": cur_tags, "dirs": cur_dirs,
           "actors": cur_actors, "manual": cur_manual, "collection": cur_col,
           "region": m.get("region") or "", "lang": m.get("original_language") or "",
           "year": m.get("year")}
    items = _score_candidates(cur, reps, links, mems, df, max(1, total))

    def _rating(x):
        vals = [float(x.get(k)) for k in ("custom_rating", "douban_rating", "tmdb_rating")
                if isinstance(x.get(k), (int, float)) and x.get(k) > 0]
        return max(vals) if vals else 0.0

    items.sort(key=lambda x: (-x["score"], -_rating(x), -(x.get("year") or 0), x["id"]))
    return items[:limit]
