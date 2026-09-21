"""store.progress（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。

多库 v13：断点键为 `(kind, item_id)`（movie|episode），与 media_info 一致。
"""
import time
from ._base import _attach_versions, _conn, _lock, _row_to_dict
from .search import _split_ints
__all__ = ['get_progress', 'save_progress', 'clear_progress', 'list_recent_played',
           'CONTINUE_MIN_POSITION', 'CONTINUE_MAX_PERCENT', 'CONTINUE_MIN_REMAIN']

# 「继续观看」判定（与 PlayerModal 断点/自动标看阈值对齐）：
# - position < 15s 不算开看（PlayerModal 也只对 >15s 的断点提示续播）
# - 剩余 <5% 或 <300s 视为已看完（PlayerModal 在这一刻会自动标 watched）
CONTINUE_MIN_POSITION = 15.0
CONTINUE_MAX_PERCENT = 0.95
CONTINUE_MIN_REMAIN = 300.0


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


def list_recent_played(limit: int = 20, library_ids=None,
                       include_finished: bool = False) -> list[dict]:
    """最近播放（海报粒度，按最后播放时间倒序）。

    默认只返回「未看完」：`movies.watched=0` 且未到自动标看阈值
    （剩余 <5% 或 <300s）；include_finished=True 时含已看完（供“全部最近播放”）。
    每个影片（library_id + COALESCE(tmdb_id,-id)）只留最近播放的那个版本，
    返回项带 `progress:{version_id, position, duration, percent, remaining_sec,
    last_played_at}`，version_id 即 stream 接口的 version_id（可直接续播）。
    """
    lim = max(1, min(int(limit or 20), 100))
    libs = _split_ints(library_ids) if library_ids is not None else []
    conds = ["p.kind='movie'", "p.position >= ?"]
    params: list = [CONTINUE_MIN_POSITION]
    if libs:
        conds.append("m.library_id IN (%s)" % ",".join("?" * len(libs)))
        params.extend(libs)
    if not include_finished:
        conds.append("COALESCE(m.watched, 0)=0")
        conds.append("NOT (p.duration > 0 AND (p.position / p.duration >= ?"
                     " OR p.duration - p.position <= ?))")
        params.extend([CONTINUE_MAX_PERCENT, CONTINUE_MIN_REMAIN])
    where = " AND ".join(conds)
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT m.*, p.item_id AS _pvid, p.position AS _ppos,"
            " p.duration AS _pdur, p.updated_at AS _pplayed"
            " FROM playback_progress p JOIN movies m ON m.id = p.item_id"
            f" WHERE {where} ORDER BY p.updated_at DESC, p.item_id DESC LIMIT ?",
            (*params, lim * 5)).fetchall()
        out, seen = [], set()
        for r in rows:
            key = (r["library_id"], r["tmdb_id"] or -r["id"])
            if key in seen:
                continue
            seen.add(key)
            d = _row_to_dict(r)
            vid = int(d.pop("_pvid"))
            pos = float(d.pop("_ppos") or 0)
            dur = float(d.pop("_pdur") or 0)
            played = int(d.pop("_pplayed") or 0)
            d["progress"] = {
                "version_id": vid,
                "position": round(pos, 3),
                "duration": round(dur, 3),
                "percent": round(min(1.0, pos / dur), 4) if dur > 0 else 0.0,
                "remaining_sec": max(0, int(dur - pos)) if dur > 0 else 0,
                "last_played_at": played,
            }
            out.append(_attach_versions(c, d))
            if len(out) >= lim:
                break
        return out
