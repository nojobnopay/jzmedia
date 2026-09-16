"""store.progress（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。"""
import time
from ._base import _conn, _lock
__all__ = ['get_progress', 'save_progress', 'clear_progress']

def get_progress(version_id: int) -> dict | None:
    """读单版本断点。无行返回 None。"""
    with _lock, _conn() as c:
        try:
            row = c.execute("SELECT * FROM playback_progress WHERE version_id=?",
                            (version_id,)).fetchone()
        except Exception:
            return None
        return dict(row) if row else None


def save_progress(version_id: int, position: float, duration: float) -> dict:
    """写单版本断点（position/duration 秒，钳制 0<=position<=duration）。返回行。"""
    try:
        dur = max(0.0, float(duration or 0))
    except (TypeError, ValueError):
        dur = 0.0
    try:
        pos = max(0.0, float(position or 0))
    except (TypeError, ValueError):
        pos = 0.0
    if dur > 0:
        pos = min(pos, dur)
    now = int(time.time())
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO playback_progress(version_id, position, duration, updated_at)"
            " VALUES(?, ?, ?, ?)"
            " ON CONFLICT(version_id) DO UPDATE SET position=excluded.position,"
            " duration=excluded.duration, updated_at=excluded.updated_at",
            (int(version_id), pos, dur, now))
        row = c.execute("SELECT * FROM playback_progress WHERE version_id=?",
                        (int(version_id),)).fetchone()
        return dict(row)


def clear_progress(version_id: int) -> bool:
    """清单版本断点（用户选“从头开始”）。返回行是否存在过。"""
    with _lock, _conn() as c:
        try:
            row = c.execute("SELECT 1 FROM playback_progress WHERE version_id=?",
                            (version_id,)).fetchone()
            c.execute("DELETE FROM playback_progress WHERE version_id=?", (version_id,))
            return bool(row)
        except Exception:
            return False

