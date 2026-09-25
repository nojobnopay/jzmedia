"""store.external（v27，Phase 2）：外部元数据源落库 + 离线 id 桥。

- 身份是 `(source, source_id)`（tvmaze/bgm/wikidata/nfo…），`tmdb_id` 可空：
  不伪造 TMDB 身份，业务行可独立显示外部来源元数据。
- `payload_json` 存标准化 detail（`metadata.external` 的规范形态：overview/genres/
  countries/people/episodes…），供 `apply_external_*` 幂等回放与再次绑定。
- 每次写入同步 `match_index`（候选面），启动时自愈播种。
"""
import json
import sqlite3
import time

from ._base import _conn, _lock, logger

__all__ = ['upsert_external', 'get_external', 'list_external', 'delete_external',
           'find_tmdb_by_imdb', 'seed_match_index_from_external']


def _alt_titles_of(payload: dict) -> str:
    """payload.aliases → 换行拼接别名串（match_index 召用）。"""
    out: list[str] = []
    for v in (payload or {}).get("aliases") or []:
        v = str(v or "").strip()
        if v and v not in out:
            out.append(v)
    return "\n".join(out)


def _index_match(source: str, source_id: str, kind: str, row: dict,
                 payload: dict) -> None:
    try:
        from .match_index import upsert_match_entry
        upsert_match_entry(source, source_id, kind,
                           row.get("title") or "", row.get("original_title") or "",
                           row.get("year"), row.get("tmdb_id"),
                           row.get("imdb_id") or "",
                           {"external": True, "poster_url": row.get("poster_url") or ""},
                           alt_titles=_alt_titles_of(payload))
    except Exception as e:
        logger.debug("index external entry failed %s:%s: %s", source, source_id, e)


def upsert_external(source: str, source_id, kind: str = "movie", *,
                    title: str = "", original_title: str = "", year=None,
                    tmdb_id=None, imdb_id: str = "", tvdb_id=None,
                    poster_url: str = "", backdrop_url: str = "",
                    payload: dict | None = None) -> bool:
    """幂等写入外部元数据；返回 True=内容有变化（False=仅刷新 fetched_at）。

    与 tmdb_cache 同节奏：有变化才索引 match_index，防高频重扫刷库。"""
    src = str(source or "").strip()
    sid = str(source_id or "").strip()
    if not src or not sid:
        return False
    try:
        payload_s = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload_s = "{}"
    try:
        tid = int(tmdb_id) if tmdb_id else None
    except (TypeError, ValueError):
        tid = None
    try:
        tvid = int(tvdb_id) if tvdb_id else None
    except (TypeError, ValueError):
        tvid = None
    now = int(time.time())
    row = {"title": title or "", "original_title": original_title or "",
           "year": year, "tmdb_id": tid, "imdb_id": imdb_id or "",
           "tvdb_id": tvid, "poster_url": poster_url or "",
           "backdrop_url": backdrop_url or ""}
    with _lock, _conn() as c:
        cur = c.execute("SELECT * FROM external_meta WHERE source=? AND source_id=?",
                        (src, sid)).fetchone()
        if cur is not None:
            same = (
                (cur["title"] or "") == row["title"]
                and (cur["original_title"] or "") == row["original_title"]
                and cur["year"] == row["year"]
                and cur["tmdb_id"] == row["tmdb_id"]
                and (cur["imdb_id"] or "") == row["imdb_id"]
                and cur["tvdb_id"] == row["tvdb_id"]
                and (cur["poster_url"] or "") == row["poster_url"]
                and (cur["backdrop_url"] or "") == row["backdrop_url"]
                and (cur["kind"] or "movie") == str(kind or "movie")
                and (cur["payload_json"] or "{}") == payload_s
            )
            if same:
                c.execute("UPDATE external_meta SET fetched_at=?"
                          " WHERE source=? AND source_id=?", (now, src, sid))
                return False
            c.execute(
                "UPDATE external_meta SET kind=?, title=?, original_title=?, year=?,"
                " tmdb_id=?, imdb_id=?, tvdb_id=?, poster_url=?, backdrop_url=?,"
                " payload_json=?, fetched_at=? WHERE source=? AND source_id=?",
                (str(kind or "movie"), row["title"], row["original_title"], row["year"],
                 row["tmdb_id"], row["imdb_id"], row["tvdb_id"], row["poster_url"],
                 row["backdrop_url"], payload_s, now, src, sid))
        else:
            c.execute(
                "INSERT INTO external_meta(source, source_id, kind, title, original_title,"
                " year, tmdb_id, imdb_id, tvdb_id, poster_url, backdrop_url, payload_json,"
                " fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (src, sid, str(kind or "movie"), row["title"], row["original_title"],
                 row["year"], row["tmdb_id"], row["imdb_id"], row["tvdb_id"],
                 row["poster_url"], row["backdrop_url"], payload_s, now))
    _index_match(src, sid, str(kind or "movie"), row, payload or {})
    return True


def _jsonify(d: dict) -> dict:
    try:
        v = json.loads(d.get("payload_json") or "{}")
        d["payload"] = v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        d["payload"] = {}
    return d


def get_external(source: str, source_id) -> dict | None:
    src = str(source or "").strip()
    sid = str(source_id or "").strip()
    if not src or not sid:
        return None
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM external_meta WHERE source=? AND source_id=?",
                        (src, sid)).fetchone()
        return _jsonify(dict(row)) if row else None


def list_external(kind: str | None = None, source: str | None = None,
                  limit: int = 200) -> list[dict]:
    where, args = [], []
    if kind:
        where.append("kind=?")
        args.append(str(kind))
    if source:
        where.append("source=?")
        args.append(str(source))
    sql = "SELECT * FROM external_meta"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY fetched_at DESC LIMIT ?"
    args.append(max(1, min(int(limit), 2000)))
    with _lock, _conn() as c:
        rows = c.execute(sql, args).fetchall()
        return [_jsonify(dict(r)) for r in rows]


def delete_external(source: str, source_id=None) -> int:
    with _lock, _conn() as c:
        if source_id is None:
            cur = c.execute("DELETE FROM external_meta WHERE source=?", (str(source),))
        else:
            cur = c.execute("DELETE FROM external_meta WHERE source=? AND source_id=?",
                            (str(source), str(source_id)))
        return int(cur.rowcount or 0)


def find_tmdb_by_imdb(imdb_id: str, kind: str = "movie") -> int | None:
    """imdb_id → tmdb_id 离线桥（P1.2）：movies 行 → tmdb_cache → external_meta。
    只返回可继续走缓存/详情链路的 tmdb_id；找不到返回 None（不触网）。"""
    imdb = str(imdb_id or "").strip()
    if not imdb:
        return None
    mt = "tv" if str(kind or "movie") == "tv" else "movie"
    with _lock, _conn() as c:
        if mt == "movie":
            row = c.execute(
                "SELECT tmdb_id FROM movies WHERE imdb_id=? AND tmdb_id IS NOT NULL"
                " ORDER BY id LIMIT 1", (imdb,)).fetchone()
            if row and row["tmdb_id"]:
                return int(row["tmdb_id"])
        row = c.execute(
            "SELECT tmdb_id FROM tmdb_cache WHERE imdb_id=? AND media_type=?"
            " AND tmdb_id IS NOT NULL LIMIT 1", (imdb, mt)).fetchone()
        if row and row["tmdb_id"]:
            return int(row["tmdb_id"])
        row = c.execute(
            "SELECT tmdb_id FROM external_meta WHERE imdb_id=? AND kind=?"
            " AND tmdb_id IS NOT NULL LIMIT 1", (imdb, mt)).fetchone()
        if row and row["tmdb_id"]:
            return int(row["tmdb_id"])
    return None


def seed_match_index_from_external() -> int:
    """启动播种：external_meta → match_index（不调网、幂等）。LEFT JOIN 找缺失项。"""
    inserted = 0
    with _lock, _conn() as c:
        try:
            rows = c.execute(
                "SELECT e.source, e.source_id, e.kind, e.title, e.original_title,"
                " e.year, e.tmdb_id, e.imdb_id, e.payload_json"
                " FROM external_meta e"
                " LEFT JOIN match_index m ON m.source=e.source AND m.source_id=e.source_id"
                " WHERE m.id IS NULL AND e.title != ''").fetchall()
        except sqlite3.OperationalError:
            return 0
        for r in rows:
            try:
                payload = json.loads(r["payload_json"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            alt = _alt_titles_of(payload)
            cur = c.execute(
                "INSERT INTO match_index(source, source_id, kind, title, original_title,"
                " alt_titles, year, tmdb_id, imdb_id, payload, fetched_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                (r["source"], r["source_id"], r["kind"] or "movie", r["title"] or "",
                 r["original_title"] or "", alt or "", r["year"], r["tmdb_id"],
                 r["imdb_id"] or "", "{}"))
            c.execute("INSERT INTO match_index_fts(rowid, title, original_title)"
                      " VALUES(?, ?, ?)",
                      (int(cur.lastrowid), r["title"] or "", r["original_title"] or ""))
            inserted += 1
    if inserted:
        logger.info("match_index 播种 %s 条（来自 external_meta）", inserted)
    return inserted
