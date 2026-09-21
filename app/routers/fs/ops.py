"""routers.fs.ops（自 app/routers/fs.py 拆分，评审 R01-Q4；经 fs 门面使用）。"""
import os

from ... import library_paths, storage, store
from ...scanner import is_sidecar, sync_nfos_for
from ..files import (_cleanup_old_dir, _rename_or_move, _resync_old_dir,
                     _sibling_followers)
from ..files.executor import (_remote_cleanup_old_dir, _remote_resync_old_dir,
                              _remote_sibling_followers, _repath_followed_extra)
from .classify import _classify
from .common import logger
__all__ = ['_exec_delete_one', '_move_db_follow', '_exec_move_one',
           '_exec_delete_one_remote', '_exec_move_one_remote']


def _exec_delete_one(plan: dict) -> dict:
    """执行单文件删除（含 DB 联动与旧目录 NFO 收尾）。调用方已确认。"""
    rel = plan["rel"]
    lid = int(plan.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
    abs_p = library_paths.resolve(lid, rel)
    old_dir = os.path.dirname(abs_p)
    if not os.path.isfile(abs_p) and not os.path.lexists(abs_p):
        return {**plan, "status": "skipped_missing_src"}
    try:
        os.remove(abs_p)
    except OSError as e:
        return {**plan, "status": f"error: {e}"}
    kind = plan.get("kind") or "other"
    try:
        if kind == "feature" and plan.get("movie_id"):
            store.delete_movie(plan["movie_id"])
        elif kind == "sidecar":
            try:
                store.delete_extra_by_path(rel, library_id=lid)
            except Exception:
                pass
    except Exception as e:
        return {**plan, "status": f"error: {e}"}
    _cleanup_old_dir(old_dir)
    _resync_old_dir(old_dir)
    return {**plan, "status": "deleted"}


def _move_db_follow(fr: str, to: str, info: dict, library_id=None,
                    backend=None) -> None:
    """移动后的 DB 联动：正片改 file_path + NFO；花絮改路径归属；其余不管。
    backend 给定时 NFO 经 StorageBackend 落盘（远程直读库）。"""
    lid = int(library_id or library_paths.DEFAULT_LIBRARY_ID)
    if info.get("kind") == "feature" and info.get("movie_id"):
        store.update_movie_local(info["movie_id"], file_path=to)
        try:
            if backend is not None:
                sync_nfos_for(info["movie_id"], backend=backend, rel=to)
            else:
                sync_nfos_for(info["movie_id"], library_paths.resolve(lid, to))
        except Exception:
            pass
    elif info.get("kind") == "sidecar":
        try:
            rows = [e for e in store.list_all_extras()
                    if e["file_path"] == fr
                    and int(e.get("library_id") or 0) == lid]
        except Exception:
            rows = []
        if rows:
            e = rows[0]
            try:
                store.delete_extra_by_path(fr, library_id=lid)
            except Exception:
                pass
            try:
                store.upsert_extra(to, e.get("movie_id"), e.get("kind") or "extra",
                                   library_id=lid)
            except Exception:
                pass


def _exec_move_one(fr: str, to: str, library_id=None) -> dict:
    """执行单文件改名/移动（含跟随字幕/花絮兄弟与 DB 联动）。"""
    lid = int(library_id or library_paths.DEFAULT_LIBRARY_ID)
    base = {"from": fr, "to": to, "library_id": lid}
    src = library_paths.resolve(lid, fr)
    dst = library_paths.resolve(lid, to)
    if not os.path.isfile(src):
        return {**base, "status": "skipped_missing_src"}
    if os.path.exists(dst):
        return {**base, "status": "conflict_disk_exists"}
    info = _classify(fr, library_id=lid)
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        followers = _sibling_followers(src)
        _rename_or_move(src, dst)
        _move_db_follow(fr, to, info, lid)
        # 同茎跟随：字幕/花絮兄弟随新茎改名
        new_stem = os.path.splitext(os.path.basename(dst))[0]
        old_stem = os.path.splitext(os.path.basename(src))[0]
        followed = 0
        for f in followers:
            suffix = os.path.basename(f)[len(old_stem):]
            fdst = os.path.join(os.path.dirname(dst), new_stem + suffix)
            try:
                if not os.path.exists(fdst):
                    frel_old = os.path.relpath(f, library_paths.library_root(lid))
                    _rename_or_move(f, fdst)
                    frel_new = os.path.relpath(fdst, library_paths.library_root(lid))
                    try:
                        rows = [e for e in store.list_all_extras()
                                if e["file_path"] == frel_old
                                and int(e.get("library_id") or 0) == lid]
                        if rows:
                            store.delete_extra_by_path(frel_old, library_id=lid)
                            store.upsert_extra(
                                frel_new, rows[0].get("movie_id"),
                                rows[0].get("kind") or "extra", library_id=lid)
                    except Exception:
                        pass
                    followed += 1
            except OSError:
                continue
        # NFO 残留与旧目录收尾（正片才有意义，其余调用自吞无影响）
        try:
            if info.get("kind") == "feature":
                old_nfo = os.path.join(os.path.dirname(dst), old_stem + ".nfo")
                new_nfo = os.path.join(os.path.dirname(dst), new_stem + ".nfo")
                if (old_nfo != new_nfo and os.path.dirname(src) == os.path.dirname(dst)
                        and os.path.exists(old_nfo)):
                    try:
                        os.remove(old_nfo)
                    except OSError:
                        pass
        except Exception:
            pass
        _cleanup_old_dir(os.path.dirname(src))
        if os.path.normpath(os.path.dirname(src)) != os.path.normpath(os.path.dirname(dst)):
            _resync_old_dir(os.path.dirname(src))
        return {**base, "status": "moved", "followed": followed,
                "kind": info.get("kind") or "other"}
    except Exception as e:
        return {**base, "status": f"error: {e}"}


def _exec_delete_one_remote(plan: dict, backend) -> dict:
    """远程直读库删除（文件/空目录，库内相对路径）；DB 联动与旧目录收尾同本地。"""
    rel = plan["rel"]
    lid = int(plan.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
    kind = plan.get("kind") or "other"
    try:
        st = backend.stat(rel)
    except storage.StorageNotFound:
        return {**plan, "status": "skipped_missing_src"}
    except storage.StorageError as e:
        return {**plan, "status": f"error: {e}"}
    if st.is_dir:
        try:
            if backend.list(rel):
                return {**plan, "status": "dir_not_empty"}
            backend.delete(rel)
        except storage.StorageError as e:
            return {**plan, "status": f"error: {e}"}
        return {**plan, "status": "deleted"}
    try:
        backend.delete(rel)
    except storage.StorageError as e:
        return {**plan, "status": f"error: {e}"}
    try:
        if kind == "feature" and plan.get("movie_id"):
            store.delete_movie(plan["movie_id"])
        elif kind == "sidecar":
            try:
                store.delete_extra_by_path(rel, library_id=lid)
            except Exception as e:
                logger.debug("delete extra row failed rel=%s: %s", rel, e)
    except Exception as e:
        return {**plan, "status": f"error: {e}"}
    old_dir = os.path.dirname(rel)
    _remote_cleanup_old_dir(backend, old_dir)
    _remote_resync_old_dir(backend, old_dir, lid)
    return {**plan, "status": "deleted"}


def _exec_move_one_remote(fr: str, to: str, library_id, backend) -> dict:
    """远程直读库改名/移动（含同茎跟随与 DB 联动）；与本地 `_exec_move_one` 语义一致。"""
    lid = int(library_id or library_paths.DEFAULT_LIBRARY_ID)
    base = {"from": fr, "to": to, "library_id": lid}
    try:
        st = backend.stat(fr)
    except storage.StorageNotFound:
        return {**base, "status": "skipped_missing_src"}
    except storage.StorageError as e:
        return {**base, "status": f"error: {e}"}
    if st.is_dir:
        return {**base, "status": "error: is a directory"}
    try:
        if backend.exists(to):
            return {**base, "status": "conflict_disk_exists"}
    except storage.StorageError as e:
        return {**base, "status": f"error: {e}"}
    info = _classify(fr, library_id=lid, backend=backend)
    old_dir = os.path.dirname(fr)
    new_dir = os.path.dirname(to)
    old_stem = os.path.splitext(os.path.basename(fr))[0]
    new_stem = os.path.splitext(os.path.basename(to))[0]
    try:
        if new_dir:
            backend.mkdir(new_dir, parents=True)
        followers = _remote_sibling_followers(backend, fr)
        backend.rename(fr, to)
        _move_db_follow(fr, to, info, lid, backend=backend)
        followed = 0
        for f in followers:
            suffix = os.path.basename(f)[len(old_stem):]
            fdst = f"{new_dir}/{new_stem}{suffix}" if new_dir else f"{new_stem}{suffix}"
            try:
                if not backend.exists(fdst):
                    backend.rename(f, fdst)
                    _repath_followed_extra(lid, f, fdst, info.get("movie_id") or 0)
                    followed += 1
            except storage.StorageError as e:
                logger.debug("follow sidecar failed %s -> %s: %s", f, fdst, e)
                continue
        _remote_cleanup_old_dir(backend, old_dir)
        if os.path.normpath(old_dir) != os.path.normpath(new_dir):
            _remote_resync_old_dir(backend, old_dir, lid)
        return {**base, "status": "moved", "followed": followed,
                "kind": info.get("kind") or "other"}
    except storage.StorageError as e:
        return {**base, "status": f"error: {e}"}


