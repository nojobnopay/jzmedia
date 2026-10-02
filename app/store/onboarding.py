"""Small, local-only snapshots for the first-use guide (no filesystem scans)."""
from ._base import _conn, _lock


def snapshot(library_id=None, kind="movie"):
    with _lock, _conn() as c:
        total = c.execute("SELECT (SELECT COUNT(*) FROM movies) + "
                          "(SELECT COUNT(*) FROM tv_episodes)").fetchone()[0]
        if library_id is None:
            return {"total": total, "count": 0, "pending": 0, "items": []}
        if kind == "tv":
            where = "e.library_id=? AND COALESCE(e.missing,0)=0"
            pending = "(e.needs_review=1 OR s.needs_review=1 OR (s.tmdb_id IS NULL AND COALESCE(s.match_source,'')=''))"
            row = c.execute(
                f"SELECT COUNT(*), COALESCE(SUM({pending}),0) FROM tv_episodes e "
                f"JOIN tv_shows s ON s.id=e.show_id WHERE {where}", (library_id,)).fetchone()
            items = c.execute(
                "SELECT e.id, e.show_id, e.season, e.episode, e.title, s.title AS show_title, "
                f"{pending} AS pending FROM tv_episodes e JOIN tv_shows s ON s.id=e.show_id "
                f"WHERE {where} ORDER BY e.id DESC LIMIT 5", (library_id,)).fetchall()
        else:
            pending = "(needs_review=1 OR (tmdb_id IS NULL AND COALESCE(match_source,'')=''))"
            row = c.execute(f"SELECT COUNT(*), COALESCE(SUM({pending}),0) FROM movies "
                            "WHERE library_id=?", (library_id,)).fetchone()
            items = c.execute(f"SELECT id, title, {pending} AS pending FROM movies "
                              "WHERE library_id=? ORDER BY id DESC LIMIT 5", (library_id,)).fetchall()
        return {"total": total, "count": row[0], "pending": row[1],
                "items": [dict(item) for item in items]}
