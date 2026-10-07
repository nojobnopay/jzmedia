"""store.collections（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。

v18：合集跟随**媒体库**（成员可跨同一媒体库内的视频库；同名合集按媒体库唯一）。
"""
import sqlite3
import time
from ._base import (DEFAULT_LIBRARY_ID, _like_esc, _attach_media_libraries, _attach_versions,
                    _collections_for_film, _conn, _film_key, _lock,
                    _row_to_dict, logger)
__all__ = ['list_collections_for_movie', '_collection_cover', '_collection_covers', 'list_collections', 'get_collection', 'create_collection', 'update_collection', 'delete_collection', 'add_collection_members', 'remove_collection_members', 'prune_dangling_members', 'collection_hint_for_movie', 'suggest_series_collections', 'collected_series_new_members', 'top_up_collection']

# 媒体库 → 其视频库集合（SQL 子查询片段；配合 media_library_id 参数）
_LIB_IN_MEDIA = "SELECT id FROM libraries WHERE media_library_id=?"


def list_collections_for_movie(movie_id: int) -> list[dict]:
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT m.tmdb_id, m.id, l.media_library_id FROM movies m"
            " JOIN libraries l ON l.id=m.library_id WHERE m.id=?",
            (movie_id,)).fetchone()
        if not row:
            return []
        return _collections_for_film(c, row["tmdb_id"], row["id"],
                                     row["media_library_id"])


def _collection_cover(c: sqlite3.Connection, cid: int) -> str:
    """合集封面：最早成员代表行的海报（无则空）。"""
    return _collection_covers(c).get(int(cid), "")


def _collection_covers(c: sqlite3.Connection) -> dict:
    """全部合集封面一次算完（评审 B8/R02-D6）：窗口函数取每合集最早有海报的成员。
    封面候选限合集所在媒体库（同名影片在多库时不串封面）。"""
    try:
        rows = c.execute(
            "SELECT collection_id, poster_path FROM ("
            " SELECT cm.collection_id AS collection_id, m.poster_path AS poster_path,"
            " ROW_NUMBER() OVER (PARTITION BY cm.collection_id"
            "   ORDER BY m.year IS NULL, m.year, m.id) AS rn"
            " FROM collection_members cm"
            " JOIN collections col ON col.id=cm.collection_id"
            " JOIN movies m ON ((cm.movie_tmdb_id IS NOT NULL AND m.tmdb_id=cm.movie_tmdb_id)"
            "   OR (cm.movie_id IS NOT NULL AND m.id=cm.movie_id))"
            "   AND m.library_id IN (SELECT id FROM libraries"
            " WHERE media_library_id=col.media_library_id)"
            " WHERE m.poster_path IS NOT NULL AND m.poster_path!='')"
            " WHERE rn=1").fetchall()
    except Exception as e:
        logger.warning("collection covers failed: %s", e)
        return {}
    return {int(r["collection_id"]): r["poster_path"] for r in rows}


def list_collections(q: str = "", media_library_id: int | None = None) -> list[dict]:
    """合集列表；media_library_id 限定所属媒体库（v18，缺省=全部媒体库）。"""
    params: list = []
    where = ""
    if media_library_id is not None:
        where = " AND media_library_id=?"
        params.append(int(media_library_id))
    with _lock, _conn() as c:
        if (q or "").strip():
            # LIKE 通配符转义（评审 B6/R02-B1）：否则搜 "_"/"%" 会全匹配
            rows = c.execute(
                "SELECT * FROM collections WHERE name LIKE ? ESCAPE '\\'" + where +
                " ORDER BY updated_at DESC",
                (f"%{_like_esc((q or '').strip())}%", *params)).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM collections WHERE 1=1" + where +
                " ORDER BY updated_at DESC", params).fetchall()
        covers = _collection_covers(c)
        out = []
        for r in rows:
            d = dict(r)
            d["member_count"] = int(c.execute(
                "SELECT COUNT(*) AS n FROM collection_members WHERE collection_id=?",
                (d["id"],)).fetchone()["n"])
            d["cover"] = d.get("poster_path") or covers.get(int(d["id"]), "")
            out.append(d)
        return _attach_media_libraries(c, out)


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
        # 成员一次取回（评审 B8/R02-D6：不再每个成员一次聚合查询）
        tids = sorted({int(mm["movie_tmdb_id"]) for mm in mems if mm["movie_tmdb_id"]})
        mids = sorted({int(mm["movie_id"]) for mm in mems if not mm["movie_tmdb_id"]
                       and mm["movie_id"]})
        by_key: dict = {}
        if tids or mids:
            conds, params = [], []
            if tids:
                conds.append("tmdb_id IN (%s)" % ",".join("?" * len(tids)))
                params.extend(tids)
            if mids:
                conds.append("id IN (%s)" % ",".join("?" * len(mids)))
                params.extend(mids)
            sql = "SELECT * FROM movies WHERE (" + " OR ".join(conds) + ")"
            if d.get("media_library_id") is not None:
                sql += " AND library_id IN (" + _LIB_IN_MEDIA + ")"
                params.append(int(d["media_library_id"]))
            for r in c.execute(sql, tuple(params)):
                d0 = _row_to_dict(r)
                key = ("t", int(d0["tmdb_id"])) if d0.get("tmdb_id") else ("m", int(d0["id"]))
                cur = by_key.get(key)
                if cur is None or int(d0.get("updated_at") or 0) > int(cur.get("updated_at") or 0):
                    by_key[key] = d0
        items = []
        seen = set()
        for mm in mems:
            tid, mid = mm["movie_tmdb_id"], mm["movie_id"]
            key = ("t", int(tid)) if tid else ("m", int(mid))
            if key in seen or key not in by_key:
                continue
            seen.add(key)
            items.append(_attach_versions(c, by_key[key]))
        # 无自定义排序时按年份正序兜底（系列合集如功夫熊猫按上映顺序看）
        if all(m["sort_order"] == 0 for m in mems) if mems else False:
            items.sort(key=lambda x: ((x.get("year") is None), x.get("year") or 0, x.get("id")))
        d["members"] = items
        _attach_media_libraries(c, [d, *items])
        d["member_count"] = len(items)
        d["cover"] = d.get("poster_path") or _collection_cover(c, cid)
        return d


def _default_media_id() -> int:
    """缺省媒体库解析：默认视频库所属媒体库，其次第一个启用媒体库，最后兜底 id=1。"""
    from .media_libraries import default_media_id
    try:
        mid = default_media_id()
    except Exception as e:
        logger.warning("default media library resolve failed: %s", e)
        mid = None
    return int(mid) if mid else DEFAULT_LIBRARY_ID


def create_collection(name: str, overview: str = "",
                      tmdb_collection_id: int | None = None,
                      member_ids: list | None = None,
                      media_library_id: int | None = None) -> dict:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("name required")
    if len(name) > 60:
        name = name[:60]
    mid = int(media_library_id) if media_library_id is not None else _default_media_id()
    now = int(time.time())
    with _lock, _conn() as c:
        try:
            cur = c.execute(
                "INSERT INTO collections(name, overview, tmdb_collection_id,"
                " media_library_id, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?)",
                (name, overview or "", tmdb_collection_id, mid, now, now))
            cid = int(cur.lastrowid)
        except sqlite3.IntegrityError:
            raise ValueError("collection name exists")
    if member_ids:
        add_collection_members(cid, member_ids)
    out = get_collection(cid)
    if out is None:   # 理论不可达（同事务刚写）；不用 assert（-O 会被剥离，评审 R02-B5）
        raise RuntimeError("collection lost after insert")
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
    """海报粒度加入：代表 id 归一为 film key 后幂等插入。返回 {added, total, skipped}。
    成员限合集所属**媒体库**（v18：同媒体库的任意视频库均可；跨媒体库 id 计入 skipped）。
    sort_order 目前恒 0（预留人工排序；读取端“全 0 按年份兜底”，评审 R06-Q3）。"""
    with _lock, _conn() as c:
        crow = c.execute("SELECT media_library_id FROM collections WHERE id=?",
                         (cid,)).fetchone()
        if not crow:
            raise LookupError("collection not found")
        media_id = crow["media_library_id"]
        ids = [int(x) for x in rep_ids] if rep_ids else []
        rows = []
        if ids:
            sql = "SELECT id, tmdb_id FROM movies WHERE id IN (%s)" % ",".join("?" * len(ids))
            params: list = list(ids)
            if media_id is not None:
                sql += " AND library_id IN (" + _LIB_IN_MEDIA + ")"
                params.append(int(media_id))
            rows = c.execute(sql, params).fetchall()
        keys = set()
        for r in (rows or []):
            keys.add(_film_key(r["tmdb_id"], r["id"]))
        skipped = max(0, len(set(ids)) - len(rows or []))
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
            except Exception as e:
                logger.warning("add member failed cid=%s key=%s: %s", cid, (tid, mid), e)
                continue
        c.execute("UPDATE collections SET updated_at=? WHERE id=?", (now, cid))
        total = c.execute("SELECT COUNT(*) AS n FROM collection_members WHERE collection_id=?",
                          (cid,)).fetchone()["n"]
    return {"added": added, "total": int(total), "skipped": int(skipped)}


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
                cur = c.execute("DELETE FROM collection_members WHERE collection_id=? AND movie_tmdb_id=?",
                                (cid, tid))
            else:
                cur = c.execute("DELETE FROM collection_members WHERE collection_id=? AND movie_id=?",
                                (cid, mid))
            n += int(cur.rowcount or 0)   # 显式 rowcount（评审 B6/R02-B1：不再依赖 total_changes）
        c.execute("UPDATE collections SET updated_at=? WHERE id=?", (int(time.time()), cid))
        total = c.execute("SELECT COUNT(*) AS n FROM collection_members WHERE collection_id=?",
                          (cid,)).fetchone()["n"]
    return {"removed": int(n), "total": int(total)}


def prune_dangling_members(dry_run: bool = True,
                           media_library_id: int | None = None) -> dict:
    """清理指向已删影片的合集成员（删片前未同步的老数据，如美国派系列残留）。

    悬挂判定：tmdb 键在 movies 中已无该 tmdb_id，或 id 键在 movies 中已无该行。
    只删成员行、不自动删合集（空合集由用户手动删除，返回 empty_ids 供提示）。
    media_library_id 限定所属媒体库时只处理该库合集。"""
    with _lock, _conn() as c:
        params: list = []
        scope = ""
        if media_library_id is not None:
            scope = " WHERE col.media_library_id=?"
            params.append(int(media_library_id))
        mems = c.execute(
            "SELECT cm.rowid AS rid, cm.collection_id AS cid, col.name AS cname,"
            " cm.movie_tmdb_id AS tid, cm.movie_id AS mid"
            " FROM collection_members cm"
            " JOIN collections col ON col.id=cm.collection_id" + scope,
            tuple(params)).fetchall()
        live_tmdb = {int(r["tmdb_id"]) for r in
                     c.execute("SELECT tmdb_id FROM movies WHERE tmdb_id IS NOT NULL").fetchall()
                     if r["tmdb_id"]}
        live_ids = {int(r["id"]) for r in
                    c.execute("SELECT id FROM movies").fetchall()}
        dangling = []
        for m in mems:
            tid, mid = m["tid"], m["mid"]
            if tid:
                if int(tid) not in live_tmdb:
                    dangling.append(m)
            elif mid:
                if int(mid) not in live_ids:
                    dangling.append(m)
        per_coll_total: dict = {}
        for m in mems:
            per_coll_total[int(m["cid"])] = per_coll_total.get(int(m["cid"]), 0) + 1
        per_coll_dead: dict = {}
        for m in dangling:
            per_coll_dead[int(m["cid"])] = per_coll_dead.get(int(m["cid"]), 0) + 1
        empty_ids = sorted(cid for cid, n in per_coll_total.items()
                           if per_coll_dead.get(cid, 0) >= n)
        sample = [{"collection_id": int(m["cid"]), "collection_name": m["cname"],
                   "movie_tmdb_id": m["tid"], "movie_id": m["mid"]}
                  for m in dangling[:20]]
        if dry_run:
            return {"dry_run": True, "total": len(dangling), "sample": sample,
                    "empty_ids": empty_ids}
        removed = 0
        if dangling:
            rids = [int(m["rid"]) for m in dangling]
            for i in range(0, len(rids), 400):
                chunk = rids[i:i + 400]
                ph = ",".join("?" for _ in chunk)
                cur = c.execute(
                    f"DELETE FROM collection_members WHERE rowid IN ({ph})", chunk)
                removed += int(cur.rowcount or 0)
            c.execute("UPDATE collections SET updated_at=? WHERE id IN "
                      "(SELECT DISTINCT collection_id FROM collection_members)",
                      (int(time.time()),))
            # 已删光成员的合集也刷新 updated_at，保证列表排序反映清理
            if empty_ids:
                eph = ",".join("?" for _ in empty_ids)
                c.execute(f"UPDATE collections SET updated_at=? WHERE id IN ({eph})",
                          (int(time.time()), *empty_ids))
        return {"dry_run": False, "total": len(dangling), "removed": removed,
                "empty_ids": empty_ids}


def collection_hint_for_movie(movie_id: int) -> dict | None:
    """TMDB 系列提示：本片 cache 的系列 + 媒体库内同系列兄弟（供一键建合集）。"""
    with _lock, _conn() as c:
        mrow = c.execute(
            "SELECT m.*, l.media_library_id FROM movies m"
            " JOIN libraries l ON l.id=m.library_id WHERE m.id=?",
            (movie_id,)).fetchone()
        if not mrow or not mrow["tmdb_id"]:
            return None
        crow = c.execute("SELECT collection_tmdb_id, collection_name, collection_poster_path"
                         " FROM tmdb_cache WHERE media_type='movie' AND tmdb_id=?",
                         (mrow["tmdb_id"],)).fetchone()
        if not crow or not crow["collection_tmdb_id"]:
            return None
        cid, cname = crow["collection_tmdb_id"], crow["collection_name"] or ""
        media_id = (mrow["media_library_id"] if "media_library_id" in mrow.keys()
                    else _default_media_id())
        try:
            collected = bool(c.execute(
                "SELECT 1 FROM collections WHERE media_library_id=?"
                " AND (tmdb_collection_id=? OR name=?)",
                (media_id, cid, cname)).fetchone())
        except Exception:
            collected = False
        sibs = c.execute(
            "SELECT m.*, MAX(m.updated_at) AS _u FROM movies m "
            "JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id AND t.media_type='movie' "
            "WHERE t.collection_tmdb_id=? AND m.library_id IN (" + _LIB_IN_MEDIA + ")"
            " GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id) "
            "ORDER BY m.year IS NULL, m.year", (cid, media_id)).fetchall()
        items = [_attach_versions(c, _row_to_dict(r)) for r in sibs]
        return {"collection_tmdb_id": cid, "collection_name": cname,
                "collection_poster_path": crow["collection_poster_path"] or "",
                "already_collected": collected,
                "media_library_id": media_id,
                "in_library": [{"id": x["id"], "title": x.get("title", ""),
                                 "year": x.get("year")} for x in items],
                "in_library_count": len(items)}


def suggest_series_collections(min_members: int = 2,
                               media_library_id: int | None = None) -> dict:
    """TMDB 系列自动推荐（纯本地、只读）：按 tmdb_cache.collection_tmdb_id 聚类，
    媒体库内同系列海报数达标即推荐一项。人物合集 TMDB 给不出，不在此列（纯手动）。
    已被合集收录的系列直接过滤（按 tmdb_collection_id 或同名匹配），不占推荐区。
    media_library_id 限定媒体库范围（v18；缺省=全部）。"""
    try:
        min_members = max(2, int(min_members))
    except (TypeError, ValueError):
        min_members = 2
    media_id = int(media_library_id) if media_library_id is not None else None
    mwhere, mparams = "", []
    if media_id is not None:
        mwhere = " AND m.library_id IN (" + _LIB_IN_MEDIA + ")"
        mparams = [media_id]
    with _lock, _conn() as c:
        series = c.execute(
            "SELECT t.collection_tmdb_id AS cid, MAX(t.collection_name) AS name,"
            " MAX(t.collection_poster_path) AS poster"
            " FROM movies m JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id AND t.media_type='movie'"
            " WHERE t.collection_tmdb_id IS NOT NULL" + mwhere +
            " GROUP BY t.collection_tmdb_id", mparams).fetchall()
        try:
            ewhere = " WHERE media_library_id=?" if media_id is not None else ""
            existing = {(r["tmdb_collection_id"], (r["name"] or "").strip())
                        for r in c.execute(
                            "SELECT tmdb_collection_id, name FROM collections" + ewhere,
                            ([media_id] if media_id is not None else []))}
        except Exception:
            existing = set()
        # 系列内成员一次取回再分组（评审 B8/R02-D4：不再每系列一次查询）
        all_mems = c.execute(
            "SELECT m.*, t.collection_tmdb_id AS _cid, MAX(m.updated_at) AS _u"
            " FROM movies m JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id AND t.media_type='movie'"
            " WHERE t.collection_tmdb_id IS NOT NULL" + mwhere +
            " GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id)"
            " ORDER BY m.year IS NULL, m.year, m.id", mparams).fetchall()
        group: dict = {}
        for r in all_mems:
            group.setdefault(int(r["_cid"]), []).append(r)
        items = []
        for s in series:
            cid = s["cid"]
            mems = group.get(int(cid), [])
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
            " JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id AND t.media_type='movie'"
            " WHERE t.collection_tmdb_id IS NOT NULL" + mwhere, mparams).fetchone()["n"]
        if media_id is not None:
            total = c.execute(
                "SELECT COUNT(DISTINCT COALESCE(tmdb_id, -id)) AS n FROM movies"
                " WHERE library_id IN (" + _LIB_IN_MEDIA + ")", (media_id,)).fetchone()["n"]
        else:
            total = c.execute(
                "SELECT COUNT(DISTINCT COALESCE(tmdb_id, -id)) AS n FROM movies").fetchone()["n"]
        try:
            standalone = c.execute(
                "SELECT COUNT(DISTINCT COALESCE(m.tmdb_id, -m.id)) AS n FROM movies m"
                " JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id AND t.media_type='movie'"
                " WHERE t.collection_tmdb_id IS NULL"
                " AND COALESCE(t.collection_checked_at, 0) > 0" + mwhere,
                mparams).fetchone()["n"]
        except Exception:
            standalone = 0
    return {"items": items,
            "coverage": {"with_collection": int(cov or 0),
                         "without_collection": int((total or 0) - (cov or 0)),
                         "standalone": int(standalone or 0),
                         "unchecked": int((total or 0) - (cov or 0) - (standalone or 0))},
            "topups": collected_series_new_members(media_id)}


def collected_series_new_members(media_library_id: int | None = None) -> list[dict]:
    """已收录合集的新片差集（纯本地只读）：仅系列建的合集（有 tmdb_collection_id）可匹配；
    媒体库内同系列但尚未入成员的海报即“可补齐”。纯手动合集无法匹配，直接跳过。
    media_library_id 限定媒体库范围（v18；缺省=全部）。"""
    media_id = int(media_library_id) if media_library_id is not None else None
    mwhere, mparams = "", []
    if media_id is not None:
        mwhere = " AND m.library_id IN (" + _LIB_IN_MEDIA + ")"
        mparams = [media_id]
    with _lock, _conn() as c:
        try:
            cwhere, cparams = " WHERE tmdb_collection_id IS NOT NULL", []
            if media_id is not None:
                cwhere += " AND media_library_id=?"
                cparams.append(media_id)
            cols = c.execute(
                "SELECT id, name, tmdb_collection_id FROM collections" + cwhere,
                cparams).fetchall()
        except Exception:
            return []
        all_mems = c.execute(
            "SELECT m.*, t.collection_tmdb_id AS _cid, MAX(m.updated_at) AS _u"
            " FROM movies m JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id AND t.media_type='movie'"
            " WHERE t.collection_tmdb_id IS NOT NULL" + mwhere +
            " GROUP BY m.library_id, COALESCE(m.tmdb_id, -m.id)"
            " ORDER BY m.year IS NULL, m.year, m.id", mparams).fetchall()
        by_series: dict = {}
        for r in all_mems:
            by_series.setdefault(int(r["_cid"]), []).append(r)
        members_by_col: dict = {}
        for mm in c.execute(
                "SELECT collection_id, movie_tmdb_id, movie_id FROM collection_members"):
            members_by_col.setdefault(int(mm["collection_id"]), set()).add(
                (mm["movie_tmdb_id"], mm["movie_id"]))
        out = []
        for col in cols:
            cid = col["id"]
            have = members_by_col.get(cid, set())
            new = []
            for r in by_series.get(int(col["tmdb_collection_id"]), []):
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
        row = c.execute(
            "SELECT tmdb_collection_id, media_library_id FROM collections WHERE id=?",
            (cid,)).fetchone()
        if not row:
            raise LookupError("collection not found")
        if not row["tmdb_collection_id"]:
            raise ValueError("manual collection cannot top up")
        media_id = row["media_library_id"]
    fresh = [t for t in collected_series_new_members(media_id)
             if t["collection_id"] == cid]
    if not fresh:
        with _lock, _conn() as c:
            total = c.execute("SELECT COUNT(*) AS n FROM collection_members"
                              " WHERE collection_id=?", (cid,)).fetchone()["n"]
        return {"added": 0, "total": int(total), "skipped": 0}
    rep_ids = [m["id"] for m in fresh[0]["new_members"]]
    r = add_collection_members(cid, rep_ids)
    return r
