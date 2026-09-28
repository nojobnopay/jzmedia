"""本地图片落盘（MULTI_LIBRARY_PLAN D6）：poster.jpg / fanart.jpg 写进影片目录。

- `artwork_mode=nfo_art` 时生效（Plex 本地媒体资源优先于在线刮削，解决海报加载失败）。
- 多版本同目录另写 `<stem>-poster.jpg`（Plex per-file poster 规则）；文件夹级 poster
  只写一次。
- 原子写、内容相同不重写、只读库拒写；失败自吞（调用方无需 try）。
"""
import filecmp
import os

from . import library_paths, posters as _posters, store, tmdb
from .config import settings
from .db import POSTER_DIR
from .fsutil import atomic_write_bytes
from .log import get_logger

logger = get_logger("artwork")


def _resolve(value: str) -> str:
    """缓存图片定位：新子目录优先，旧根部文件名/旧 DB 值回退（迁移过渡）。"""
    return _posters.resolve(POSTER_DIR, value, settings.data_dir)


def _first(*names: str) -> str:
    """按序取第一个命中的缓存图（含根部旧文件回退，防迁移中断）。"""
    for n in names:
        if not n:
            continue
        hit = _resolve(n)
        if hit:
            return hit
    return ""


def _poster_src(movie: dict, tmdb_id: int) -> str:
    return _first(str(movie.get("poster_path") or ""),
                 f"movies/{int(tmdb_id)}.jpg", f"{int(tmdb_id)}.jpg")


def _ensure_backdrop(tmdb_id: int, path: str) -> str:
    """TMDB backdrop 下载到 data/posters/backdrops（w780），返回本地路径或 ''。"""
    if not path:
        return ""
    hit = _first(f"backdrops/movie_{int(tmdb_id)}.jpg",
                 f"backdrop_{int(tmdb_id)}.jpg")
    if hit:
        return hit
    dest = os.path.join(POSTER_DIR, "backdrops", f"movie_{int(tmdb_id)}.jpg")
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
    """内容不同才写（原子写由后端保证）；返回是否实际写入。
    先比 size（一次 stat 比整文件读便宜），size 不同直接写；相同才读回比对。"""
    try:
        st = backend.stat(rel_dst)
        if st.is_dir or int(st.size) != len(data):
            backend.write(rel_dst, data)
            return True
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
            st = backend.stat(rel)
            if st.is_dir or int(st.size) != len(poster_data):
                continue   # size 不同必不相同，省一次整文件读
            if backend.read(rel) == poster_data:
                backend.delete(rel)
                cleaned.append(f"{stem}-poster.jpg")
        except Exception as e:
            logger.debug("cleanup version poster failed %s: %s", rel, e)
    return cleaned


def _tv_poster_src(show: dict, tmdb_id: int) -> str:
    return _first(str(show.get("poster_path") or ""),
                 f"tv/{int(tmdb_id)}.jpg", f"tv_{int(tmdb_id)}.jpg")


def _tv_backdrop_src(show: dict, tmdb_id: int) -> str:
    return _first(str(show.get("backdrop_path") or ""),
                 f"backdrops/tv_{int(tmdb_id)}.jpg",
                 f"tv_backdrop_{int(tmdb_id)}.jpg")


def _tv_season_poster_src(season_row: dict | None, tmdb_id: int, season: int) -> str:
    return _first(str((season_row or {}).get("poster_path") or ""),
                 f"tv/{int(tmdb_id)}_s{int(season)}.jpg",
                 f"tv_{int(tmdb_id)}_s{int(season)}.jpg")


def _cleanup_tv_wrapper_art(backend, show_dir: str,
                            poster: bytes, backdrop: bytes) -> int:
    """删除发布包装层里与我们写的内容一致的海报/背景（旧 show_dir_of 误放残留），
    否则包装层永远非空、规范化工具无法清掉它。只处理剧根下「不含正片、非季目录」的
    子目录，且只删内容完全相同的文件（子剧目录含正片，天然跳过）。"""
    from .scanner.tv_parse import season_from_dir
    from .scanner.classify import VIDEO_EXTS
    ours = {}
    if poster:
        ours["poster.jpg"] = poster
    if backdrop:
        ours["fanart.jpg"] = backdrop
    if not ours or not show_dir:
        return 0
    try:
        kids = backend.list(show_dir)
    except Exception as e:
        logger.debug("tv wrapper art list failed dir=%s: %s", show_dir, e)
        return 0
    removed = 0
    for e in kids:
        if not e.get("is_dir"):
            continue
        name = str(e["name"] or "")
        if not name or season_from_dir(name) is not None:
            continue
        child = _backend_join(show_dir, name)
        try:
            inner = backend.list(child)
        except Exception:
            continue
        if any(not x.get("is_dir")
               and os.path.splitext(str(x["name"]))[1].lower() in VIDEO_EXTS
               for x in inner):
            continue          # 含正片（子剧/电影目录）不动
        for fname, data in ours.items():
            try:
                if backend.read(f"{child}/{fname}") == data:
                    backend.delete(f"{child}/{fname}")
                    removed += 1
            except Exception:
                continue
    return removed


def write_for_show(show_id: int, backend=None, thumbs: bool = False) -> dict:
    """剧集海报落盘（T3）：剧根 `poster.jpg`/`fanart.jpg`、季目录 `seasonNN-poster.jpg`；
    `thumbs=True` 时另写每集 `<stem>-thumb.jpg`（3877 集级别，默认关）。

    与电影同门槛：`artwork_mode=nfo_art` 且库非只读；本地/远程统一经 StorageBackend。
    失败自吞返回 {ok, wrote[], reason?}。"""
    try:
        from . import library_paths as _lp
        from . import storage
        from .scanner import tv_nfo_link
        show = store.get_show_meta(int(show_id))
        if not show or not show.get("tmdb_id"):
            return {"ok": False, "reason": "unmatched"}
        lid = show.get("library_id") or _lp.DEFAULT_LIBRARY_ID
        if _lp.artwork_mode(lid) != "nfo_art":
            return {"ok": False, "reason": "disabled"}
        if _lp.is_read_only(lid):
            return {"ok": False, "reason": "read_only"}
        episodes = store.list_episodes(int(show_id))
        if not episodes:
            return {"ok": False, "reason": "no_episodes"}
        tmdb_id = int(show["tmdb_id"])
        if backend is None:
            backend = storage.backend_for(int(lid))
        roots = tv_nfo_link.show_dirs_for(int(show_id), episodes)
        wrote: list[str] = []
        poster = _read_bytes(_tv_poster_src(show, tmdb_id))
        backdrop = _read_bytes(_tv_backdrop_src(show, tmdb_id))
        for show_dir in roots:
            if poster and _backend_put(backend, _backend_join(show_dir, "poster.jpg"), poster):
                wrote.append("poster.jpg")
            if backdrop and _backend_put(backend, _backend_join(show_dir, "fanart.jpg"), backdrop):
                wrote.append("fanart.jpg")
            try:
                _cleanup_tv_wrapper_art(backend, show_dir, poster, backdrop)
            except Exception as e:
                logger.debug("tv wrapper art cleanup failed show=%s: %s", show_id, e)
        season_rows = {int(s.get("season") or 0): s for s in store.list_seasons(int(show_id))}
        seen: set[tuple] = set()
        for e in episodes:
            try:
                sn = int(e.get("season") or 0)
            except (TypeError, ValueError):
                continue
            sdir = tv_nfo_link.season_dir_of(e['file_path'])
            if sn <= 0 or (sn, sdir) in seen:
                continue
            seen.add((sn, sdir))
            src = _tv_season_poster_src(season_rows.get(sn), tmdb_id, sn)
            data = _read_bytes(src) if src else b""
            if not data:
                continue
            sdir = tv_nfo_link.season_dir_of(e["file_path"])
            name = f"season{sn:02d}-poster.jpg"
            if _backend_put(backend, _backend_join(sdir, name), data):
                wrote.append(name)
        if thumbs:
            from .scanner import tv_persist
            for e in episodes:
                if not e.get("still_path"):
                    continue
                local = tv_persist.ensure_episode_still(int(e["id"]))
                data = _read_bytes(local) if local else b""
                if not data:
                    continue
                stem = os.path.splitext(os.path.basename(e["file_path"]))[0]
                if not stem:
                    continue
                name = stem + "-thumb.jpg"
                if _backend_put(backend, _backend_join(
                        os.path.dirname(e["file_path"]), name), data):
                    wrote.append(name)
        return {"ok": True, "wrote": wrote}
    except Exception as e:
        logger.warning("write_for_show failed show=%s: %s", show_id, e)
        return {"ok": False, "reason": "error", "error": str(e)[:200]}


__all__ = ['write_for_movie', 'write_for_show', 'per_version_meta']
