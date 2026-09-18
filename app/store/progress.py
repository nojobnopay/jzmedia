"""store.progress（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。

多库 v13：断点键为 `(kind, item_id)`（movie|episode），与 media_info 一致。
"""
import time
from ._base import _conn, _lock
__all__ = ['get_progress', 'save_progress', 'clear_progress']

def get_progress(item_id: int, kind: str = "movie") -> dict | None:
    """读单项断点。无行返回 None。"""
    with _lock, _conn() as c:
        try:
            row = c.execute("SELECT * FROM playback_progress WHERE kind=? AND item_id=?",
                            (str(kind or "movie"), int(item_id))).fetchone()
        except Exception:
            return None
        return dict(row) if row else None


def save_progress(item_id: int, position: float, duration: float,
                  kind: str = "movie") -> dict:
    """写单项断点（position/duration 秒，钳制 0<=position<=duration）。返回行。"""
    kind = str(kind or "movie")
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
            "INSERT INTO playback_progress(kind, item_id, position, duration, updated_at)"
            " VALUES(?, ?, ?, ?, ?)"
            " ON CONFLICT(kind, item_id) DO UPDATE SET position=excluded.position,"
            " duration=excluded.duration, updated_at=excluded.updated_at",
            (kind, int(item_id), pos, dur, now))
        row = c.execute("SELECT * FROM playback_progress WHERE kind=? AND item_id=?",
                        (kind, int(item_id))).fetchone()
        return dict(row)


def clear_progress(item_id: int, kind: str = "movie") -> bool:
    """清单项断点（用户选“从头开始”）。返回行是否存在过。"""
    kind = str(kind or "movie")
    with _lock, _conn() as c:
        try:
            row = c.execute("SELECT 1 FROM playback_progress WHERE kind=? AND item_id=?",
                            (kind, int(item_id))).fetchone()
            c.execute("DELETE FROM playback_progress WHERE kind=? AND item_id=?",
                      (kind, int(item_id)))
            return bool(row)
        except Exception:
            return False
