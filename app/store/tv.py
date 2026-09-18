"""store.tv（F 阶段）：TV 只读清单——剧/季/集，入库但不刮削、不改名、不写 NFO。

- 剧名分组键 `(library_id, title, COALESCE(year,0))`（SQLite UNIQUE 对 NULL 不去重，
  因此 upsert 手动查重，不用内联 UNIQUE）。
- 集以 `(library_id, file_path)` 幂等；file_path 为库内相对路径。
"""
import os
import time

from ._base import DEFAULT_LIBRARY_ID, _conn, _lock

__all__ = ['upsert_show', 'upsert_episode', 'list_shows', 'get_show',
           'list_episodes', 'get_episode', 'count_shows', 'count_episodes',
           'delete_episode_by_path', 'prune_missing']


def upsert_show(library_id: int, title: str, year: int | None = None,
                sort_title: str = "") -> int:
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    title = " ".join(str(title or "").split())
    now = int(time.time())
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT id FROM tv_shows WHERE library_id=? AND title=?"
            " AND COALESCE(year,0)=COALESCE(?,0)", (lib_id, title, year)).fetchone()
        if row:
            c.execute("UPDATE tv_shows SET sort_title=?, updated_at=? WHERE id=?",
                      (sort_title or title, now, int(row["id"])))
            return int(row["id"])
        cur = c.execute(
            "INSERT INTO tv_shows(library_id, title, sort_title, year, updated_at)"
            " VALUES(?, ?, ?, ?, ?)",
            (lib_id, title, sort_title or title, year, now))
        return int(cur.lastrowid)


def upsert_episode(show_id: int, library_id: int, file_path: str,
                   season: int, episode: int, title: str = "") -> int:
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    now = int(time.time())
    with _lock, _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO tv_episodes(show_id, library_id, file_path,"
            " season, episode, title, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (int(show_id), lib_id, file_path, int(season), int(episode),
             title or "", now))
        c.execute(
            "UPDATE tv_episodes SET show_id=?, season=?, episode=?, title=?, updated_at=?"
            " WHERE library_id=? AND file_path=?",
            (int(show_id), int(season), int(episode), title or "", now,
             lib_id, file_path))
        row = c.execute("SELECT id FROM tv_episodes WHERE library_id=? AND file_path=?",
                        (lib_id, file_path)).fetchone()
        return int(row["id"])


def _show_row(r) -> dict:
    d = dict(r)
    d["episode_count"] = int(d.get("episode_count") or 0)
    d["season_count"] = int(d.get("season_count") or 0)
    return d


def list_shows(library_id=None, q: str = "", limit: int = 500,
               offset: int = 0) -> list[dict]:
    where, params = [], []
    if library_id is not None:
        where.append("s.library_id=?")
        params.append(int(library_id))
    if (q or "").strip():
        where.append("s.title LIKE ? ESCAPE '\\'")
        params.append(f"%{''.join(ch for ch in q.strip() if ch not in '%_\\')}%")
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    limit = max(1, min(int(limit or 500), 2000))
    offset = max(0, int(offset or 0))
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT s.*, COUNT(e.id) AS episode_count,"
            " COUNT(DISTINCT e.season) AS season_count"
            " FROM tv_shows s LEFT JOIN tv_episodes e ON e.show_id=s.id"
            + wsql +
            " GROUP BY s.id ORDER BY s.sort_title, s.year IS NULL, s.year, s.id"
            " LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        return [_show_row(r) for r in rows]


def count_shows(library_id=None) -> int:
    where, params = "", []
    if library_id is not None:
        where, params = " WHERE library_id=?", [int(library_id)]
    with _lock, _conn() as c:
        return int(c.execute("SELECT COUNT(*) FROM tv_shows" + where,
                             params).fetchone()[0])


def count_episodes(library_id=None) -> int:
    where, params = "", []
    if library_id is not None:
        where, params = " WHERE library_id=?", [int(library_id)]
    with _lock, _conn() as c:
        return int(c.execute("SELECT COUNT(*) FROM tv_episodes" + where,
                             params).fetchone()[0])


def get_show(show_id: int) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM tv_shows WHERE id=?", (int(show_id),)).fetchone()
        if not row:
            return None
        d = dict(row)
        eps = c.execute(
            "SELECT * FROM tv_episodes WHERE show_id=?"
            " ORDER BY season, episode, file_path", (int(show_id),)).fetchall()
        d["episodes"] = [dict(r) for r in eps]
        d["episode_count"] = len(d["episodes"])
        seasons: dict[int, int] = {}
        for e in d["episodes"]:
            seasons[int(e.get("season") or 0)] = seasons.get(int(e.get("season") or 0), 0) + 1
        d["seasons"] = [{"season": k, "episode_count": v}
                        for k, v in sorted(seasons.items())]
        return d


def list_episodes(show_id: int) -> list[dict]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM tv_episodes WHERE show_id=?"
            " ORDER BY season, episode, file_path", (int(show_id),)).fetchall()]


def get_episode(episode_id: int) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM tv_episodes WHERE id=?",
                        (int(episode_id),)).fetchone()
        return dict(row) if row else None


def delete_episode_by_path(file_path: str, library_id=None) -> bool:
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    with _lock, _conn() as c:
        cur = c.execute("DELETE FROM tv_episodes WHERE library_id=? AND file_path=?",
                        (lib_id, file_path))
        return int(cur.rowcount or 0) > 0


def prune_missing(library_id=None) -> int:
    """清理文件已不存在的剧集行（读操作，调用方传存在性判断）。"""
    from .. import library_paths
    where, params = "", []
    if library_id is not None:
        where, params = " WHERE library_id=?", [int(library_id)]
    removed = 0
    with _lock, _conn() as c:
        rows = c.execute("SELECT id, library_id, file_path FROM tv_episodes"
                         + where, params).fetchall()
    for r in rows:
        try:
            if os.path.exists(library_paths.resolve(r["library_id"], r["file_path"])):
                continue
        except OSError:
            continue
        with _lock, _conn() as c:
            c.execute("DELETE FROM tv_episodes WHERE id=?", (int(r["id"]),))
        removed += 1
    return removed
