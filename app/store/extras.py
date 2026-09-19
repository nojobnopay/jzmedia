"""store.extras（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。"""
import os
import time
from ._base import DEFAULT_LIBRARY_ID, _conn, _lock, _row_to_dict
__all__ = ['upsert_extra', 'list_extras_by_movie', 'list_orphan_extras', 'list_all_extras', 'get_extra', 'update_extra_movie', 'delete_extra_by_path', 'repath_extra_by_basename', 'repath_extras_prefix', 'find_movie_for_extra']

def upsert_extra(file_path: str, movie_id: int | None,
                 kind: str = "extra",
                 library_id: int = DEFAULT_LIBRARY_ID) -> int:
    """花絮归属记录（按 库+路径 幂等）。movie_id 为 None = 未归属。"""
    with _lock, _conn() as c:
        c.execute("INSERT OR IGNORE INTO extras(file_path, library_id, updated_at)"
                  " VALUES(?, ?, ?)",
                  (file_path, int(library_id), int(time.time())))
        c.execute("UPDATE extras SET movie_id=?, kind=?, updated_at=?"
                  " WHERE file_path=? AND library_id=?",
                  (movie_id, kind or "extra", int(time.time()), file_path,
                   int(library_id)))
        row = c.execute("SELECT id FROM extras WHERE file_path=? AND library_id=?",
                        (file_path, int(library_id))).fetchone()
        return int(row["id"])


def list_extras_by_movie(movie_id: int) -> list[dict]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM extras WHERE movie_id=? ORDER BY file_path", (movie_id,))]


def list_orphan_extras() -> list[dict]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM extras WHERE movie_id IS NULL ORDER BY file_path")]


def list_all_extras() -> list[dict]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM extras ORDER BY file_path")]


def get_extra(extra_id: int) -> dict | None:
    """按 id 取花絮行（评审 B6/R08-D5：改挂前拿 previous 归属）。"""
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM extras WHERE id=?", (extra_id,)).fetchone()
        return dict(row) if row else None


def update_extra_movie(extra_id: int, movie_id: int) -> bool:
    """手工认领：orphan 花絮归到指定影片。返回是否有行被更新（rowcount）。"""
    with _lock, _conn() as c:
        cur = c.execute("UPDATE extras SET movie_id=?, updated_at=? WHERE id=?",
                        (movie_id, int(time.time()), extra_id))
        return int(cur.rowcount or 0) > 0


def _abs_in_library(library_id: int, rel: str) -> str:
    """库内相对路径 → 绝对路径（延迟导入 library_paths，避免 store↔library_paths 循环）。"""
    try:
        from ..library_paths import resolve
        return resolve(int(library_id), rel)
    except Exception:
        from ..config import settings
        return os.path.join(settings.media_root, rel)


def repath_extras_prefix(library_id: int, old_dir: str, new_dir: str) -> int:
    """影片目录整体改名后批量改花絮行：该目录下所有 extras.file_path 前缀替换。"""
    old = os.path.normpath((old_dir or "").strip().strip("/"))
    new = os.path.normpath((new_dir or "").strip().strip("/"))
    if old in ("", ".") or old == new:
        return 0
    like = old.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
    with _lock, _conn() as c:
        cur = c.execute(
            "UPDATE extras SET file_path = ? || substr(file_path, ?), updated_at=? "
            "WHERE library_id=? AND file_path LIKE ? ESCAPE '\\'",
            (new, len(old) + 1, int(time.time()),
             int(library_id), like))
        return int(cur.rowcount or 0)


def delete_extra_by_path(file_path: str,
                         library_id: int = DEFAULT_LIBRARY_ID) -> bool:
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM extras WHERE file_path=? AND library_id=?",
                        (file_path, int(library_id))).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM extras WHERE file_path=? AND library_id=?",
                  (file_path, int(library_id)))
        return True


def repath_extra_by_basename(basename: str, new_path: str,
                             movie_id: int | None, kind: str,
                             library_id: int = DEFAULT_LIBRARY_ID) -> dict | None:
    """已入库花絮被搬迁改路径后按 basename 认领：仅认领原文件已消失的行
    （同名不同文件不误认），更新路径+归属/kind，避免删建抖动。
    返回更新后的行，无可认领返回 None。"""
    with _lock, _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM extras WHERE library_id=?", (int(library_id),)).fetchall()]
    same = [r for r in rows if os.path.basename(r["file_path"]) == basename]
    if not same:
        return None
    if any(r["file_path"] == new_path for r in same):
        return next(r for r in same if r["file_path"] == new_path)
    gone = [r for r in same if not os.path.exists(
        _abs_in_library(library_id, r["file_path"]))]
    if not gone:
        return None
    keep = sorted(gone, key=lambda r: r["id"])[0]
    with _lock, _conn() as c:
        try:
            c.execute("UPDATE extras SET file_path=?, movie_id=?, kind=?,"
                      " updated_at=? WHERE id=?",
                      (new_path, movie_id, kind or "extra",
                       int(time.time()), keep["id"]))
        except Exception:
            return keep
        for r in same:
            if r["id"] != keep["id"] and r["file_path"] != new_path:
                try:
                    c.execute("DELETE FROM extras WHERE id=?", (r["id"],))
                except Exception:
                    pass
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM extras WHERE id=?", (keep["id"],)).fetchone()
        return dict(row) if row else None


def find_movie_for_extra(title: str, year: int | None,
                         library_id: int = DEFAULT_LIBRARY_ID) -> dict | None:
    """花絮归属：归一标题对 movies.title/original_title，年份±1（年份缺失则只比标题）。
    命中多行取最早入库（id 最小，多版本同 tmdb 归代表无妨，跟随搬迁以行为准逐个比对）。
    返回 movie 行 dict 或 None。"""
    from ..scanner import normalize_title
    norm = normalize_title(title or "")
    if not norm:
        return None
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM movies WHERE library_id=? ORDER BY id",
                         (int(library_id),)).fetchall()
    cands = []
    for r in rows:
        d = _row_to_dict(r)
        if year is not None and d.get("year") is not None:
            try:
                if abs(int(d["year"]) - int(year)) > 1:
                    continue
            except (TypeError, ValueError):
                pass
        for key in (d.get("title") or "", d.get("original_title") or ""):
            if key and normalize_title(key) == norm:
                cands.append(d)
                break
    return cands[0] if cands else None

