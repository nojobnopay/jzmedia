"""Persistent file-operation journal, acknowledged by scan start watermarks."""
import time

from ._base import _conn, _lock

__all__ = ['record_fs_change', 'fs_change_summary', 'clear_fs_changes']


def record_fs_change(library_id: int, action: str, path: str) -> int:
    """Call only after an actual filesystem mutation, including partial success."""
    with _lock, _conn() as c:
        cur = c.execute(
            "INSERT INTO fs_changes(library_id, action, path, created_at) VALUES(?,?,?,?)",
            (int(library_id), action, path, int(time.time())))
        return int(cur.lastrowid)


def fs_change_summary(library_id: int) -> dict:
    lid = int(library_id)
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS count, COALESCE(MAX(id),0) AS revision,"
            " COALESCE(MAX(created_at),0) AS last_changed_at FROM fs_changes"
            " WHERE library_id=?", (lid,)).fetchone()
        actions = {r['action']: r['n'] for r in c.execute(
            "SELECT action, COUNT(*) AS n FROM fs_changes WHERE library_id=? GROUP BY action",
            (lid,))}
        paths = [r['path'] for r in c.execute(
            "SELECT path FROM fs_changes WHERE library_id=? GROUP BY path"
            " ORDER BY MAX(id) DESC LIMIT 20", (lid,))]
    return {'library_id': lid, 'pending': bool(row['count']), **dict(row),
            'actions': actions, 'paths': paths}


def clear_fs_changes(library_id: int, revision: int) -> int:
    """Never remove a change made after the scan took its filesystem snapshot."""
    with _lock, _conn() as c:
        cur = c.execute("DELETE FROM fs_changes WHERE library_id=? AND id<=?",
                        (int(library_id), int(revision)))
        return int(cur.rowcount)
