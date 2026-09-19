"""routers.files.executor（自 app/routers/files.py 拆分，评审 B9/R09-Q1；经 files 门面使用）。"""
import os
from ... import library_paths
from ... import store
from ...scanner import SUBTITLE_EXTS
from ...scanner import extras_dir_name
from ...scanner import is_feature_video
from ...scanner import is_sidecar
from ...scanner import sync_nfos_for
from ...log import get_logger
logger = get_logger("files.executor")
from .paths import _rename_or_move, _safe_component
__all__ = ['_write_nfos', '_sibling_followers', '_cleanup_old_dir', 'move_attached_extras',
           '_resync_old_dir', '_move_one', '_move_one_remote',
           '_remote_cleanup_old_dir', '_remote_resync_old_dir', '_remote_move_extras']

def _write_nfos(movie_id: int, dst_abs: str) -> None:
    """整理后 NFO 收敛：委托 scanner.sync_nfos_for（单版本只留 movie.nfo，
    同片多版本才补同名，共享目录只写当前同名）。失败自吞。"""
    try:
        sync_nfos_for(movie_id, dst_abs)
    except Exception as e:
        logger.debug("write nfos failed id=%s dst=%s: %s", movie_id, dst_abs, e)


def _sibling_followers(src_abs: str) -> list[str]:
    """同名前缀跟随文件：同目录下以正片 stem 开头、本身是花絮/样片/字幕的兄弟。"""
    src_dir = os.path.dirname(src_abs)
    stem = os.path.splitext(os.path.basename(src_abs))[0]
    out = []
    try:
        names = os.listdir(src_dir)
    except OSError as e:
        logger.debug("list sibling dir failed dir=%s: %s", src_dir, e)
        return out
    for n in names:
        full = os.path.join(src_dir, n)
        if full == src_abs or not os.path.isfile(full):
            continue
        st, ex = os.path.splitext(n)
        if st == stem or st.startswith(stem + "-") or st.startswith(stem + ".") \
                or st.startswith(stem + "_") or st.startswith(stem + " "):
            _lib, rel_probe = library_paths.locate(full)
            if rel_probe is None:
                continue
            if is_sidecar(rel_probe) or ex.lower() in SUBTITLE_EXTS:
                out.append(full)
    return out


def _cleanup_old_dir(old_dir_abs: str) -> None:
    """旧目录无正片残留时清掉 NFO 残留；空目录则删掉（共享大目录不会为空，无动作）。"""
    try:
        names = os.listdir(old_dir_abs)
    except OSError as e:
        logger.debug("list old dir failed dir=%s: %s", old_dir_abs, e)
        return
    has_feature = False
    for n in names:
        full = os.path.join(old_dir_abs, n)
        if not os.path.isfile(full):
            continue
        _lib, rel = library_paths.locate(full)
        if rel is None:
            continue
        if is_feature_video(rel):
            has_feature = True
            break
    if has_feature:
        return
    for n in names:
        if n == "movie.nfo" or n.endswith(".nfo"):
            try:
                os.remove(os.path.join(old_dir_abs, n))
            except OSError as e:
                logger.debug("remove stale nfo failed dir=%s name=%s: %s", old_dir_abs, n, e)
    try:
        if not os.listdir(old_dir_abs):
            os.rmdir(old_dir_abs)
    except OSError as e:
        logger.debug("rmdir old dir failed dir=%s: %s", old_dir_abs, e)


def _repath_followed_extra(lib_id, old_rel: str, new_rel: str, movie_id) -> None:
    """跟随改名的花絮同步 extras 行（按 basename 认领；非花絮/无行则无操作）。"""
    try:
        from ...scanner import extra_kind
        kind = extra_kind(old_rel) or "extra"
        store.repath_extra_by_basename(os.path.basename(old_rel), new_rel,
                                       int(movie_id), kind,
                                       library_id=int(lib_id))
    except Exception as e:
        logger.debug("repath followed extra failed %s -> %s: %s", old_rel, new_rel, e)


def _rel_is_file(backend, rel: str) -> bool:
    try:
        return not backend.stat(rel).is_dir
    except Exception:
        return False


def _remote_sibling_followers(backend, rel_src: str) -> list[str]:
    """远程同前缀跟随文件（库内相对路径列表）。"""
    rel_dir = os.path.dirname(rel_src)
    stem = os.path.splitext(os.path.basename(rel_src))[0]
    out: list[str] = []
    try:
        entries = backend.list(rel_dir)
    except Exception as e:
        logger.debug("list sibling dir failed dir=%s: %s", rel_dir, e)
        return out
    for e in sorted(entries, key=lambda x: x["name"]):
        n = e["name"]
        if e["is_dir"]:
            continue
        rel = f"{rel_dir}/{n}" if rel_dir else n
        if rel == rel_src:
            continue
        st, ex = os.path.splitext(n)
        if (st == stem or st.startswith(stem + "-") or st.startswith(stem + ".")
                or st.startswith(stem + "_") or st.startswith(stem + " ")):
            if is_sidecar(rel) or ex.lower() in SUBTITLE_EXTS:
                out.append(rel)
    return out


def _remote_cleanup_old_dir(backend, rel_dir: str) -> None:
    """远程旧目录清场：无正片残留时删 NFO 残留，目录为空则删目录。"""
    if not rel_dir:
        return
    try:
        entries = backend.list(rel_dir)
    except Exception as e:
        logger.debug("list old dir failed dir=%s: %s", rel_dir, e)
        return
    for e in entries:
        if e["is_dir"]:
            continue
        rel = f"{rel_dir}/{e['name']}"
        if is_feature_video(rel):
            return
    for e in entries:
        n = e["name"]
        if n == "movie.nfo" or n.endswith(".nfo"):
            try:
                backend.delete(f"{rel_dir}/{n}")
            except Exception as e:
                logger.debug("remove stale nfo failed dir=%s name=%s: %s",
                             rel_dir, n, e)
    try:
        if not backend.list(rel_dir):
            backend.delete(rel_dir)
    except Exception as e:
        logger.debug("rmdir old dir failed dir=%s: %s", rel_dir, e)


def _remote_resync_old_dir(backend, rel_dir: str, lib_id) -> None:
    """远程旧目录重收敛：还有正片残留时以剩余行重调 NFO 收敛。失败自吞。"""
    try:
        try:
            rows = store.list_movies_in_dir(rel_dir, library_id=lib_id)
        except Exception as e:
            logger.debug("list old dir movies failed dir=%s: %s", rel_dir, e)
            return
        remaining = [r for r in rows
                     if r.get("file_path") and is_feature_video(r["file_path"])
                     and _rel_is_file(backend, r["file_path"])]
        if not remaining:
            return
        remaining.sort(key=lambda r: int(r.get("id", 0)))
        first = remaining[0]
        try:
            sync_nfos_for(int(first["id"]), backend=backend, rel=first["file_path"])
        except Exception as e:
            logger.debug("resync nfos failed dir=%s: %s", rel_dir, e)
    except Exception as e:
        logger.debug("resync old dir failed dir=%s: %s", rel_dir, e)


def _remote_move_extras(movie_id: int, backend, rel_movie_dir: str) -> dict:
    """远程：已归属花絮搬进影片目录的 extras/ 子目录（与本地语义一致）。"""
    moved = 0
    skipped: list[dict] = []
    try:
        rows = store.list_extras_by_movie(movie_id)
    except Exception as e:
        logger.debug("list extras failed movie_id=%s: %s", movie_id, e)
        return {"moved": 0, "skipped": []}
    home = store.get_movie(movie_id) or {}
    profile = library_paths.naming_profile(
        home.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
    for e in rows:
        lib_id = e.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
        rel = backend.norm(e["file_path"])
        if not _rel_is_file(backend, rel):
            continue
        edst_dir = (f"{rel_movie_dir}/{extras_dir_name(e.get('kind'), profile)}"
                    if rel_movie_dir else extras_dir_name(e.get("kind"), profile))
        base = _safe_component(os.path.splitext(os.path.basename(rel))[0])
        if not base:
            continue
        edst = f"{edst_dir}/{base}{os.path.splitext(rel)[1]}"
        try:
            if os.path.normpath(rel) == os.path.normpath(edst):
                continue
            if backend.exists(edst):
                skipped.append({"file_path": e["file_path"],
                                "reason": "target_exists"})
                continue
            backend.mkdir(edst_dir, parents=True)
            backend.rename(rel, edst)
            store.upsert_extra(edst, movie_id, e.get("kind") or "extra",
                               library_id=lib_id)
            try:
                store.delete_extra_by_path(e["file_path"], library_id=lib_id)
            except Exception as ex:
                logger.debug("delete old extra row failed path=%s: %s",
                             e["file_path"], ex)
            moved += 1
        except Exception as ex:
            skipped.append({"file_path": e["file_path"], "reason": f"error: {ex}"})
            continue
    return {"moved": moved, "skipped": skipped}


def move_attached_extras(movie_id: int, movie_dir_abs: str,
                         backend=None, movie_dir_rel: str = "") -> dict:
    """把已归属花絮搬进指定影片目录的 extras/ 子目录（茎名清洗保留），更新归属路径。
    供 _move_one 跟随与 /api/extras/collect 共用；远程直读传 backend + 库内相对目录。
    返回 {moved, skipped}；skipped=目标已存在等未搬项（评审 R08-D3：不再静默跳过）。"""
    if backend is not None:
        return _remote_move_extras(movie_id, backend, movie_dir_rel)
    moved = 0
    skipped: list[dict] = []
    try:
        rows = store.list_extras_by_movie(movie_id)
    except Exception as e:
        logger.debug("list extras failed movie_id=%s: %s", movie_id, e)
        return {"moved": 0, "skipped": []}
    home = store.get_movie(movie_id) or {}
    profile = library_paths.naming_profile(
        home.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
    for e in rows:
        lib_id = e.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
        esrc = library_paths.resolve(lib_id, e["file_path"])
        if not os.path.isfile(esrc):
            continue
        edst_dir = os.path.join(movie_dir_abs,
                                extras_dir_name(e.get("kind"), profile))
        base = _safe_component(os.path.splitext(os.path.basename(esrc))[0])
        if not base:
            continue
        edst = os.path.join(edst_dir, base + os.path.splitext(esrc)[1])
        try:
            if os.path.normpath(esrc) == os.path.normpath(edst):
                continue
            os.makedirs(edst_dir, exist_ok=True)
            if not os.path.exists(edst):
                _rename_or_move(esrc, edst)
                store.upsert_extra(os.path.relpath(
                    edst, library_paths.library_root(lib_id)),
                    movie_id, e.get("kind") or "extra", library_id=lib_id)
                try:
                    store.delete_extra_by_path(e["file_path"], library_id=lib_id)
                except Exception as e:
                    logger.debug("delete old extra row failed path=%s: %s", e["file_path"], e)
                moved += 1
            else:
                skipped.append({"file_path": e["file_path"],
                                "reason": "target_exists"})
        except OSError as ex:
            skipped.append({"file_path": e["file_path"], "reason": f"error: {ex}"})
            continue
    return {"moved": moved, "skipped": skipped}


def _resync_old_dir(old_dir_abs: str) -> None:
    """搬迁后旧目录重收敛：还有正片残留（如多版本搬走其一）时，以剩余行重调
    sync（多→单自动删多余同名 NFO）；空了则沿用 _cleanup_old_dir 清场。失败自吞。"""
    try:
        if not os.path.isdir(old_dir_abs):
            return
        lib, old_rel = library_paths.locate(old_dir_abs)
        if lib is None or old_rel is None:
            return
        if old_rel == ".":
            old_rel = ""
        try:
            rows = store.list_movies_in_dir(old_rel, library_id=lib["id"])
        except Exception as e:
            logger.debug("list old dir movies failed dir=%s: %s", old_rel, e)
            return
        remaining = []
        for r in rows:
            try:
                fp = r.get("file_path", "")
                if not fp or not is_feature_video(fp):
                    continue
                if os.path.isfile(library_paths.resolve(lib["id"], fp)):
                    remaining.append(r)
            except Exception as e:
                logger.debug("probe old dir row failed path=%s: %s", r.get("file_path"), e)
                continue
        if not remaining:
            return
        remaining.sort(key=lambda r: int(r.get("id", 0)))
        first = remaining[0]
        try:
            sync_nfos_for(int(first["id"]),
                          library_paths.resolve(lib["id"], first["file_path"]))
        except Exception as e:
            logger.debug("resync nfos failed dir=%s: %s", old_dir_abs, e)
    except Exception as e:
        logger.debug("resync old dir failed dir=%s: %s", old_dir_abs, e)


def _move_one_remote(p: dict, backend) -> dict:
    """远程直读库的改名/搬迁（库内相对路径，经 StorageBackend；无挂载依赖）。"""
    lib_id = p.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    src, dst = backend.norm(p["from"]), backend.norm(p["to"])
    try:
        if not _rel_is_file(backend, src):
            return {**p, "status": "skipped_missing_src"}
        if backend.exists(dst):
            return {**p, "status": "conflict_disk_exists"}
    except Exception as e:
        logger.warning("remote move precheck failed id=%s: %s", p.get("id"), e)
        return {**p, "status": f"error: {e}"}
    existing = store.get_by_path(p["to"], library_id=lib_id)
    if existing is not None and int(existing.get("id") or -1) != int(p["id"]):
        return {**p, "status": "conflict_db_occupied"}
    try:
        parent = os.path.dirname(dst)
        if parent:
            backend.mkdir(parent, parents=True)
        followers = _remote_sibling_followers(backend, src)
        backend.rename(src, dst)
        try:
            store.update_movie_local(p["id"], file_path=p["to"])
        except Exception as e:
            logger.warning("update db after move failed id=%s %s -> %s: %s",
                           p.get("id"), p.get("from"), p.get("to"), e)
            try:
                backend.rename(dst, src)
            except Exception as rb:
                logger.error("move rollback failed id=%s dst=%s src=%s: %s",
                             p["id"], p["to"], p["from"], rb)
            raise
        try:
            cur = store.get_movie(p["id"]) or {}
        except Exception as e:
            logger.debug("get movie for persist failed id=%s: %s", p.get("id"), e)
            cur = {}
        persist: dict = {}
        if p.get("edition") and not cur.get("edition"):
            persist["edition"] = p["edition"]
        if not cur.get("spec"):
            if p.get("numbered"):
                full = ((p.get("spec") or "") + "-" if p.get("spec") else "") + p["numbered"]
                persist["spec"] = full
            elif p.get("spec"):
                persist["spec"] = p["spec"]
        if persist:
            try:
                store.update_movie_local(p["id"], **persist)
            except Exception as e:
                logger.debug("persist spec/edition failed id=%s: %s", p.get("id"), e)
        try:
            sync_nfos_for(p["id"], backend=backend, rel=dst)
        except Exception as e:
            logger.debug("write nfos failed id=%s dst=%s: %s", p["id"], dst, e)
        new_stem = os.path.splitext(os.path.basename(dst))[0]
        old_stem = os.path.splitext(os.path.basename(src))[0]
        followed = 0
        for f in followers:
            suffix = os.path.basename(f)[len(old_stem):]
            fdst = (f"{os.path.dirname(dst)}/{new_stem}{suffix}"
                    if os.path.dirname(dst) else f"{new_stem}{suffix}")
            try:
                if not backend.exists(fdst):
                    backend.rename(f, fdst)
                    followed += 1
                    _repath_followed_extra(lib_id, f, fdst, p["id"])
            except Exception as e:
                logger.debug("follow sidecar failed %s -> %s: %s", f, fdst, e)
                continue
        _remote_cleanup_old_dir(backend, os.path.dirname(src))
        _ex = _remote_move_extras(p["id"], backend, os.path.dirname(dst))
        extras_moved = _ex.get("moved", 0)
        _remote_cleanup_old_dir(backend, os.path.dirname(src))
        if (os.path.normpath(os.path.dirname(src))
                != os.path.normpath(os.path.dirname(dst))):
            _remote_resync_old_dir(backend, os.path.dirname(src), lib_id)
        if os.path.dirname(src) == os.path.dirname(dst):
            old_nfo = (f"{os.path.dirname(dst)}/{old_stem}.nfo"
                       if os.path.dirname(dst) else f"{old_stem}.nfo")
            new_nfo = (f"{os.path.dirname(dst)}/{new_stem}.nfo"
                       if os.path.dirname(dst) else f"{new_stem}.nfo")
            if old_nfo != new_nfo:
                try:
                    if backend.exists(old_nfo):
                        backend.delete(old_nfo)
                except Exception as e:
                    logger.debug("remove old nfo failed path=%s: %s", old_nfo, e)
        return {**p, "status": "moved", "followed": followed,
                "extras_moved": extras_moved}
    except Exception as e:
        logger.warning("remote move failed id=%s %s -> %s: %s", p.get("id"),
                       p.get("from"), p.get("to"), e)
        return {**p, "status": f"error: {e}"}


def _move_one(p: dict) -> dict:
    lib_id = p.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    from ... import storage
    try:
        backend = storage.backend_for(lib_id)
    except storage.StorageError as e:
        return {**p, "status": f"error: {e}"}
    if backend.abs_path(p["from"]) is None:
        return _move_one_remote(p, backend)
    src = library_paths.resolve(lib_id, p["from"])
    dst = library_paths.resolve(lib_id, p["to"])
    if not os.path.exists(src):
        return {**p, "status": "skipped_missing_src"}
    if os.path.exists(dst):
        return {**p, "status": "conflict_disk_exists"}
    # 库内占用保护（评审 P1-06）：目标路径若挂在另一条库行上（哪怕文件缺失），
    # 先改库后挪盘会撞 UNIQUE(file_path) → 盘已动库未动。此处提前拒绝。
    existing = store.get_by_path(p["to"], library_id=lib_id)
    if existing is not None and int(existing.get("id") or -1) != int(p["id"]):
        return {**p, "status": "conflict_db_occupied"}
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        followers = _sibling_followers(src)
        _rename_or_move(src, dst)
        # 本地写：只改 file_path，不碰 TMDB 镜像列
        try:
            store.update_movie_local(p["id"], file_path=p["to"])
        except Exception as e:
            # 库写失败回滚移动，保证盘/库一致（正常路径已被上面的占用检查挡住）
            logger.warning("update db after move failed id=%s %s -> %s: %s",
                           p.get("id"), p.get("from"), p.get("to"), e)
            try:
                _rename_or_move(dst, src)
            except OSError:
                logger.error("move rollback failed id=%s dst=%s src=%s", p["id"],
                             p["to"], p["from"])
            raise
        # 规划期识别的版本/编号后缀落库（DB 为空才写，手工值优先），防下次预览回环
        try:
            cur = store.get_movie(p["id"]) or {}
        except Exception as e:
            logger.debug("get movie for persist failed id=%s: %s", p.get("id"), e)
            cur = {}
        persist: dict = {}
        if p.get("edition") and not cur.get("edition"):
            persist["edition"] = p["edition"]
        if not cur.get("spec"):
            if p.get("numbered"):
                # 存完整渲染串（含前面的规格段），保证下次预览收敛
                full = ((p.get("spec") or "") + "-" if p.get("spec") else "") + p["numbered"]
                persist["spec"] = full
            elif p.get("spec"):
                persist["spec"] = p["spec"]
        if persist:
            try:
                store.update_movie_local(p["id"], **persist)
            except Exception as e:
                logger.debug("persist spec/edition failed id=%s: %s", p.get("id"), e)
        _write_nfos(p["id"], dst)
        # 花絮/字幕跟随：新 stem + 原后缀
        new_stem = os.path.splitext(os.path.basename(dst))[0]
        old_stem = os.path.splitext(os.path.basename(src))[0]
        followed = 0
        for f in followers:
            suffix = os.path.basename(f)[len(old_stem):]
            fdst = os.path.join(os.path.dirname(dst), new_stem + suffix)
            try:
                if not os.path.exists(fdst):
                    _rename_or_move(f, fdst)
                    followed += 1
                    _repath_followed_extra(lib_id,
                                           os.path.relpath(
                                               f, library_paths.library_root(lib_id)),
                                           os.path.relpath(
                                               fdst, library_paths.library_root(lib_id)),
                                           p["id"])
            except OSError as e:
                logger.debug("follow sidecar failed %s -> %s: %s", f, fdst, e)
                continue
        _cleanup_old_dir(os.path.dirname(src))
        # 归属花絮跟随：搬进目标花絮子目录（kodi=extras/；plex=Trailers/Featurettes 等）
        _ex = move_attached_extras(p["id"], os.path.dirname(dst))
        extras_moved = _ex.get("moved", 0)
        # 旧目录收尾（花絮搬走后再清一次）：无正片则清 NFO，空目录删掉
        _cleanup_old_dir(os.path.dirname(src))
        # 跨目录搬迁：旧目录还有正片残留则重收敛（多→单删多余同名 NFO）
        if os.path.normpath(os.path.dirname(src)) != os.path.normpath(os.path.dirname(dst)):
            _resync_old_dir(os.path.dirname(src))
        # 同目录改名：删掉旧 stem 的 per-file NFO 残留（movie.nfo 已重写）
        if os.path.dirname(src) == os.path.dirname(dst):
            old_nfo = os.path.join(os.path.dirname(dst), old_stem + ".nfo")
            new_nfo = os.path.join(os.path.dirname(dst), new_stem + ".nfo")
            if old_nfo != new_nfo:
                try:
                    if os.path.exists(old_nfo):
                        os.remove(old_nfo)
                except OSError as e:
                    logger.debug("remove old nfo failed path=%s: %s", old_nfo, e)
        return {**p, "status": "moved", "followed": followed,
                "extras_moved": extras_moved}
    except Exception as e:
        logger.warning("move failed id=%s %s -> %s: %s", p.get("id"),
                       p.get("from"), p.get("to"), e)
        return {**p, "status": f"error: {e}"}

