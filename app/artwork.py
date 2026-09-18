"""本地图片落盘（MULTI_LIBRARY_PLAN D6）：poster.jpg / fanart.jpg 写进影片目录。

- `artwork_mode=nfo_art` 时生效（Plex 本地媒体资源优先于在线刮削，解决海报加载失败）。
- 多版本同目录另写 `<stem>-poster.jpg`（Plex per-file poster 规则）；文件夹级 poster
  只写一次。
- 原子写、内容相同不重写、只读库拒写；失败自吞（调用方无需 try）。
"""
import filecmp
import os

from . import library_paths, store, tmdb
from .db import POSTER_DIR
from .fsutil import atomic_write_bytes
from .log import get_logger

logger = get_logger("artwork")


def _poster_src(movie: dict, tmdb_id: int) -> str:
    p = str(movie.get("poster_path") or "")
    if p:
        cand = os.path.join(POSTER_DIR, p)
        if os.path.isfile(cand) and os.path.getsize(cand) > 0:
            return cand
    cand = os.path.join(POSTER_DIR, f"{int(tmdb_id)}.jpg")
    if os.path.isfile(cand) and os.path.getsize(cand) > 0:
        return cand
    return ""


def _ensure_backdrop(tmdb_id: int, path: str) -> str:
    """TMDB backdrop 下载到 data/posters（w780），返回本地路径或 ''。"""
    if not path:
        return ""
    dest = os.path.join(POSTER_DIR, f"backdrop_{int(tmdb_id)}.jpg")
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest
    if tmdb.download_image(path, dest, size="w780"):
        return dest
    return ""


def _copy_if_changed(src: str, dst: str) -> bool:
    """内容不同才写（原子替换）；返回是否实际写入。"""
    try:
        if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src) \
                and filecmp.cmp(src, dst, shallow=False):
            return False
        with open(src, "rb") as fh:
            atomic_write_bytes(dst, fh.read())
        return True
    except OSError as e:
        logger.warning("copy artwork failed %s -> %s: %s", src, dst, e)
        return False


def write_for_movie(movie_id: int, abs_path: str | None = None) -> dict:
    """按库级 artwork_mode 落本地图片。返回 {ok, wrote?, reason?}（失败不抛）。"""
    try:
        m = store.get_movie(int(movie_id))
        if not m or not m.get("tmdb_id"):
            return {"ok": False, "reason": "unmatched"}
        lid = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
        if library_paths.artwork_mode(lid) != "nfo_art":
            return {"ok": False, "reason": "disabled"}
        if library_paths.is_read_only(lid):
            return {"ok": False, "reason": "read_only"}
        tmdb_id = int(m["tmdb_id"])
        if not abs_path:
            abs_path = library_paths.resolve(lid, m["file_path"])
        movie_dir = os.path.dirname(abs_path)
        if not os.path.isdir(movie_dir):
            return {"ok": False, "reason": "dir_missing"}
        cache = store.get_tmdb_cached(tmdb_id) or {}
        versions = store.list_movie_paths_by_tmdb(tmdb_id, lid)
        wrote: list[str] = []

        poster_src = _poster_src(m, tmdb_id)
        if poster_src and _copy_if_changed(poster_src, os.path.join(movie_dir, "poster.jpg")):
            wrote.append("poster.jpg")

        backdrop_src = _ensure_backdrop(tmdb_id, str(cache.get("backdrop_tmdb_path") or ""))
        if backdrop_src and _copy_if_changed(backdrop_src, os.path.join(movie_dir, "fanart.jpg")):
            wrote.append("fanart.jpg")

        if len(versions) > 1 and poster_src:
            stem = os.path.splitext(os.path.basename(abs_path))[0]
            if _copy_if_changed(poster_src,
                                os.path.join(movie_dir, f"{stem}-poster.jpg")):
                wrote.append(f"{stem}-poster.jpg")
        return {"ok": True, "wrote": wrote}
    except Exception as e:
        logger.warning("write artwork failed mid=%s: %s", movie_id, e)
        return {"ok": False, "reason": str(e)[:120]}


__all__ = ['write_for_movie']
