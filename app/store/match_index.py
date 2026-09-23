"""store.match_index：离线/降级匹配候选索引（MULTI_LIBRARY_PLAN D7 §9）。

- `tmdb_cache` 是 TMDB 结果的身份级镜像；`match_index` 是**跨来源候选检索面**：
  local/tmdb/nfo/embedded/tvmaze/wikidata/douban/imdb 的标题年份统一入索引。
- 有 tmdb_id 的结果同时写 `tmdb_cache`（由调用方负责），本模块只管候选面。
- FTS5 表与主表手工同步（仓库约定：不用 trigger）。
"""
import json
import sqlite3
import time

from ._base import _conn, _lock, logger

__all__ = ['seed_match_index_from_cache', 'upsert_match_entry', 'search_match_index',
           'list_match_entries', 'delete_match_entries', 'import_imdb_tsv']


def _fts_sync(c, rowid: int, title: str, original_title: str) -> None:
    try:
        c.execute("DELETE FROM match_index_fts WHERE rowid=?", (rowid,))
        c.execute("INSERT INTO match_index_fts(rowid, title, original_title)"
                  " VALUES(?, ?, ?)", (rowid, title or "", original_title or ""))
    except sqlite3.OperationalError as e:
        logger.debug("match_index fts sync failed rowid=%s: %s", rowid, e)


def upsert_match_entry(source: str, source_id, kind: str = "movie",
                       title: str = "", original_title: str = "",
                       year: int | None = None, tmdb_id: int | None = None,
                       imdb_id: str = "", payload: dict | None = None) -> int:
    """写入/更新候选（source+source_id 幂等），返回 match_index.id。"""
    now = int(time.time())
    try:
        payload_s = json.dumps(payload or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        payload_s = "{}"
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM match_index WHERE source=? AND source_id=?",
                        (str(source), str(source_id))).fetchone()
        if row:
            rid = int(row["id"])
            c.execute(
                "UPDATE match_index SET kind=?, title=?, original_title=?, year=?,"
                " tmdb_id=?, imdb_id=?, payload=?, fetched_at=? WHERE id=?",
                (str(kind or "movie"), title or "", original_title or "", year,
                 tmdb_id, imdb_id or "", payload_s, now, rid))
        else:
            cur = c.execute(
                "INSERT INTO match_index(source, source_id, kind, title, original_title,"
                " year, tmdb_id, imdb_id, payload, fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (str(source), str(source_id), str(kind or "movie"), title or "",
                 original_title or "", year, tmdb_id, imdb_id or "", payload_s, now))
            rid = int(cur.lastrowid)
        _fts_sync(c, rid, title, original_title)
        return rid


def seed_match_index_from_cache() -> int:
    """启动种子：把 tmdb_cache 存量标题补进候选面（不调网、幂等）。
    先用行数对账短路（大库启动 O(1)，不做 N 次存在性查询）。"""
    inserted = 0
    with _lock, _conn() as c:
        try:
            n_cache = int(c.execute(
                "SELECT COUNT(*) FROM tmdb_cache WHERE tmdb_id IS NOT NULL"
                " AND title != ''").fetchone()[0])
            n_idx = int(c.execute(
                "SELECT COUNT(*) FROM match_index WHERE source='tmdb'").fetchone()[0])
            if n_idx >= n_cache:
                return 0
            rows = c.execute(
                "SELECT tmdb_id, title, original_title, year, imdb_id, media_type"
                " FROM tmdb_cache WHERE tmdb_id IS NOT NULL AND title != ''").fetchall()
        except sqlite3.OperationalError:
            return 0
        for r in rows:
            tid = int(r["tmdb_id"])
            mt = str(r["media_type"] or "movie")
            sid = str(tid) if mt == "movie" else f"{mt}:{tid}"
            exists = c.execute(
                "SELECT id FROM match_index WHERE source='tmdb' AND source_id=?",
                (sid,)).fetchone()
            if exists:
                continue
            cur = c.execute(
                "INSERT INTO match_index(source, source_id, kind, title, original_title,"
                " year, tmdb_id, imdb_id, payload, fetched_at)"
                " VALUES('tmdb', ?, ?, ?, ?, ?, ?, ?, '{}', 0)",
                (sid, r["media_type"] or "movie", r["title"] or "",
                 r["original_title"] or "", r["year"], tid, r["imdb_id"] or ""))
            _fts_sync(c, int(cur.lastrowid), r["title"] or "", r["original_title"] or "")
            inserted += 1
    if inserted:
        logger.info("match_index 播种 %s 条（来自 tmdb_cache）", inserted)
    return inserted


def list_match_entries(kind: str | None = None, limit: int = 200) -> list[dict]:
    with _lock, _conn() as c:
        if kind:
            rows = c.execute("SELECT * FROM match_index WHERE kind=? ORDER BY year DESC"
                             " LIMIT ?", (kind, max(1, min(int(limit), 2000)))).fetchall()
        else:
            rows = c.execute("SELECT * FROM match_index ORDER BY fetched_at DESC"
                             " LIMIT ?", (max(1, min(int(limit), 2000)),)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except Exception:
                d["payload"] = {}
            out.append(d)
        return out


def search_match_index(term: str, kind: str | None = None, limit: int = 20,
                       year: int | None = None, year_tolerance: int = 1) -> list[dict]:
    """离线候选检索：LIKE 标题（CJK 部分词在 FTS unicode61 下召回差，直接用 LIKE），
    可选 kind 与年份容差过滤。评分由调用方（metadata 层）按归一标题算。"""
    q = (term or "").strip()
    if not q:
        return []
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{esc}%"
    sql = ("SELECT * FROM match_index WHERE (title LIKE ? ESCAPE '\\'"
           " OR original_title LIKE ? ESCAPE '\\')")
    args: list = [like, like]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if year:
        sql += " AND (year IS NULL OR ABS(year - ?) <= ?)"
        args += [int(year), max(0, int(year_tolerance))]
    sql += " ORDER BY (year IS NULL), ABS(COALESCE(year, 9999) - ?), tmdb_id IS NULL LIMIT ?"
    args += [int(year or 0), max(1, min(int(limit), 100))]
    with _lock, _conn() as c:
        rows = c.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except Exception:
                d["payload"] = {}
            out.append(d)
        return out


def delete_match_entries(source: str, source_id=None) -> int:
    with _lock, _conn() as c:
        if source_id is None:
            rows = [r["id"] for r in c.execute(
                "SELECT id FROM match_index WHERE source=?", (str(source),))]
            c.execute("DELETE FROM match_index WHERE source=?", (str(source),))
        else:
            rows = [r["id"] for r in c.execute(
                "SELECT id FROM match_index WHERE source=? AND source_id=?",
                (str(source), str(source_id)))]
            c.execute("DELETE FROM match_index WHERE source=? AND source_id=?",
                      (str(source), str(source_id)))
        for rid in rows:
            try:
                c.execute("DELETE FROM match_index_fts WHERE rowid=?", (rid,))
            except sqlite3.OperationalError:
                pass
        return len(rows)


def import_imdb_tsv(path: str, limit: int | None = None, progress_cb=None,
                    should_stop=None) -> dict:
    """导入 IMDb `title.basics.tsv(.gz)` → match_index(source='imdb')。

    - 只收 movie/tvMovie/tvSeries 且有 primaryTitle 的行；幂等（source+source_id）。
    - 手动触发（设置页/接口），数据集 GB 级，导入期间不锁库（逐行 upsert）。
    - progress_cb(count) 每 500 行回调一次；should_stop() 协作取消。
    """
    import csv
    import gzip
    opener = gzip.open if str(path).endswith(".gz") else open
    imported = 0
    try:
        with opener(path, "rt", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                if should_stop and should_stop():
                    break
                if (row.get("titleType") or "") not in ("movie", "tvMovie", "tvSeries"):
                    continue
                title = (row.get("primaryTitle") or "").strip()
                tconst = (row.get("tconst") or "").strip()
                if not title or not tconst:
                    continue
                y = (row.get("startYear") or "").strip()
                year = int(y) if y.isdigit() else None
                upsert_match_entry("imdb", tconst, "movie", title,
                                   (row.get("originalTitle") or "").strip(),
                                   year, None, tconst, {"imdb": tconst})
                imported += 1
                if limit and imported >= int(limit):
                    break
                if progress_cb and imported % 500 == 0:
                    try:
                        progress_cb(imported)
                    except Exception:
                        pass
    except (OSError, csv.Error) as e:
        logger.warning("import imdb tsv failed path=%s: %s", path, e)
        raise
    logger.info("IMDb 数据集导入完成：%s 条（path=%s）", imported, path)
    return {"imported": imported}
