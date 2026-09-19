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


def _read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as e:
        logger.warning("read artwork source failed %s: %s", path, e)
        return b""


def _backend_join(rel_dir: str, name: str) -> str:
    return f"{rel_dir}/{name}" if rel_dir else name


def _backend_put(backend, rel_dst: str, data: bytes) -> bool:
    """内容不同才写（原子写由后端保证）；返回是否实际写入。"""
    try:
        if backend.read(rel_dst) == data:
            return False
    except Exception:
        pass
    backend.write(rel_dst, data)
    return True


def _write_for_movie_backend(m: dict, backend, rel: str, tmdb_id: int,
                             backdrops: bool = True) -> dict:
    """远程直读库：poster/fanart 经 StorageBackend 写入影片目录（远程不删任何文件）。"""
    rel = backend.norm(rel)
    movie_dir = os.path.dirname(rel)
    cache = store.get_tmdb_cached(tmdb_id) or {}
    versions = store.list_movie_paths_by_tmdb(
        tmdb_id, m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
    wrote: list[str] = []

    poster_src = _poster_src(m, tmdb_id)
    poster_data = _read_bytes(poster_src) if poster_src else b""
    if poster_data and _backend_put(
            backend, _backend_join(movie_dir, "poster.jpg"), poster_data):
        wrote.append("poster.jpg")

    backdrop = b""
    if backdrops:
        backdrop_src = _ensure_backdrop(tmdb_id,
                                        str(cache.get("backdrop_tmdb_path") or ""))
        backdrop = _read_bytes(backdrop_src) if backdrop_src else b""
    if backdrop and _backend_put(
            backend, _backend_join(movie_dir, "fanart.jpg"), backdrop):
        wrote.append("fanart.jpg")

    cleaned: list[str] = []
    if per_version_meta(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID,
                        versions) and poster_data:
        stem = os.path.splitext(os.path.basename(rel))[0]
        if stem and _backend_put(
                backend, _backend_join(movie_dir, f"{stem}-poster.jpg"), poster_data):
            wrote.append(f"{stem}-poster.jpg")
    else:
        cleaned = _cleanup_stale_backend_posters(backend, movie_dir, versions,
                                                 poster_data)
    return {"ok": True, "wrote": wrote, "cleaned": cleaned}


def write_for_movie(movie_id: int, abs_path: str | None = None,
                    backend=None, rel: str = "", backdrops: bool = True) -> dict:
    """按库级 artwork_mode 落本地图片。返回 {ok, wrote?, reason?}（失败不抛）。

    本地传 `abs_path`（None=按库根解析）；远程直读库传 `backend` + 库内相对 `rel`。
    """
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
        if backend is not None:
            return _write_for_movie_backend(m, backend, rel or m["file_path"],
                                            tmdb_id, backdrops=backdrops)
        if abs_path == "":
            # 远程直读库无 POSIX 路径：跳过媒体目录落盘（本地海报缓存不受影响）
            return {"ok": False, "reason": "no_local_path"}
        if abs_path is None:
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

        if backdrops:
            backdrop_src = _ensure_backdrop(tmdb_id,
                                            str(cache.get("backdrop_tmdb_path") or ""))
            if backdrop_src and _copy_if_changed(backdrop_src,
                                                 os.path.join(movie_dir, "fanart.jpg")):
                wrote.append("fanart.jpg")

        cleaned: list[str] = []
        if per_version_meta(lid, versions) and poster_src:
            stem = os.path.splitext(os.path.basename(abs_path))[0]
            if _copy_if_changed(poster_src,
                                os.path.join(movie_dir, f"{stem}-poster.jpg")):
                wrote.append(f"{stem}-poster.jpg")
        else:
            cleaned = _cleanup_stale_local_posters(movie_dir, versions,
                                                   _read_bytes(poster_src) if poster_src else b"")
        return {"ok": True, "wrote": wrote, "cleaned": cleaned}
    except Exception as e:
        logger.warning("write artwork failed mid=%s: %s", movie_id, e)
        return {"ok": False, "reason": str(e)[:120]}


def per_version_meta(lid, versions) -> bool:
    """多版本 per-version 策略（单一来源：library_paths.per_version_meta）。"""
    return library_paths.per_version_meta(lid, versions)


def _cleanup_stale_local_posters(movie_dir: str, versions: list,
                                 poster_data: bytes) -> list[str]:
    """不需要 per-version 海报时，删掉内容=当前海报（我们写的）的旧 `<stem>-poster.jpg`。"""
    cleaned: list[str] = []
    if not poster_data:
        return cleaned
    for v in versions or []:
        stem = os.path.splitext(os.path.basename(str(v.get("file_path") or "")))[0]
        if not stem:
            continue
        path = os.path.join(movie_dir, f"{stem}-poster.jpg")
        try:
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                if fh.read() == poster_data:
                    os.remove(path)
                    cleaned.append(f"{stem}-poster.jpg")
        except OSError as e:
            logger.debug("cleanup version poster failed %s: %s", path, e)
    return cleaned


def _cleanup_stale_backend_posters(backend, movie_dir: str, versions: list,
                                   poster_data: bytes) -> list[str]:
    cleaned: list[str] = []
    if not poster_data:
        return cleaned
    for v in versions or []:
        stem = os.path.splitext(os.path.basename(str(v.get("file_path") or "")))[0]
        if not stem:
            continue
        rel = _backend_join(movie_dir, f"{stem}-poster.jpg")
        try:
            if backend.read(rel) == poster_data:
                backend.delete(rel)
                cleaned.append(f"{stem}-poster.jpg")
        except Exception as e:
            logger.debug("cleanup version poster failed %s: %s", rel, e)
    return cleaned


__all__ = ['write_for_movie', 'per_version_meta']
