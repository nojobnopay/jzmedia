"""routers.fs.copy（自 app/routers/fs.py 拆分，评审 R01-Q4；经 fs 门面使用）。"""
import os
import shutil
import threading

from fastapi import HTTPException

from ... import library_paths, scanner, storage, store
from ...jobkit import JobRegistry
from ...scanner import is_feature_video
from ..files import _require_writable
from .classify import _classify
from .common import logger, router
from .paths import _check_inside_root
__all__ = ['fs_copy', 'fs_copy_status', 'fs_copy_cancel']


class _CopyCancelled(Exception):
    """协作取消信号：触发临时文件清理后由 worker 捕获。"""


_COPY_JOBS = JobRegistry(prefix="copy")

#: 超过该体积/数量时 dry_run 提示前端需要二次确认（复制很贵，别误触）
COPY_CONFIRM_BYTES = 2 * 1024 * 1024 * 1024
COPY_CONFIRM_ITEMS = 200


def _unique_dst(path: str) -> tuple[str, bool]:
    """冲突自动副本命名（Windows 语义）：x.mkv → x (副本).mkv → x (副本 2).mkv。绝不覆盖。"""
    if not os.path.exists(path):
        return path, False
    root, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = f"{root}{' (副本)' if i == 1 else f' (副本 {i})'}{ext}"
        if not os.path.exists(cand):
            return cand, True
        i += 1


def _copy_file(src: str, dst: str, on_bytes, should_stop) -> bool:
    """分块复制（1MB：可报进度、可协作取消）。成功 True；取消时删除半成品。"""
    with open(src, "rb") as fr, open(dst, "wb") as fw:
        while True:
            if should_stop and should_stop():
                try:
                    fw.close(); os.remove(dst)
                except OSError:
                    pass
                return False
            chunk = fr.read(1024 * 1024)
            if not chunk:
                break
            fw.write(chunk)
            if on_bytes:
                on_bytes(len(chunk))
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass
    return True


def _measure(items: list[str], library_id=None) -> tuple[int, int, int]:
    """(总字节, 文件数, 目录数)；符号链接按 0 字节且不跟随。"""
    lid = int(library_id or library_paths.DEFAULT_LIBRARY_ID)
    total = files = dirs = 0
    for rel in items:
        abs_p = library_paths.resolve(lid, rel)
        if os.path.isdir(abs_p) and not os.path.islink(abs_p):
            dirs += 1
            for root, dnames, fnames in os.walk(abs_p):
                dirs += len(dnames)
                for f in fnames:
                    fp = os.path.join(root, f)
                    files += 1
                    if not os.path.islink(fp):
                        try:
                            total += os.path.getsize(fp)
                        except OSError:
                            pass
        else:
            files += 1
            if not os.path.islink(abs_p):
                try:
                    total += os.path.getsize(abs_p)
                except OSError:
                    pass
    return total, files, dirs


def _rel_exists(backend, rel: str) -> bool:
    try:
        return bool(backend.exists(rel))
    except storage.StorageError:
        return False


def _unique_dst_rel(backend, to_dir: str, base: str) -> tuple[str, bool]:
    """远程冲突自动副本命名（与本地 `_unique_dst` 同语义，绝不覆盖）。"""
    rel = f"{to_dir}/{base}" if to_dir else base
    if not _rel_exists(backend, rel):
        return rel, False
    root, ext = os.path.splitext(rel)
    i = 1
    while True:
        cand = f"{root}{' (副本)' if i == 1 else f' (副本 {i})'}{ext}"
        if not _rel_exists(backend, cand):
            return cand, True
        i += 1


def _measure_remote(backend, items: list[str]) -> tuple[int, int, int]:
    """远程 (总字节, 文件数, 目录数)：iter_tree 每目录一次 list。"""
    total = files = dirs = 0
    for rel in items:
        try:
            st = backend.stat(rel)
        except storage.StorageError as e:
            logger.debug("measure stat failed rel=%s: %s", rel, e)
            continue
        if st.is_dir:
            dirs += 1
            try:
                for e in backend.iter_tree(rel):
                    if e.is_dir:
                        dirs += 1
                    else:
                        files += 1
                        total += int(e.size or 0)
            except storage.StorageError as e:
                logger.debug("measure tree failed rel=%s: %s", rel, e)
        else:
            files += 1
            total += int(st.size)
    return total, files, dirs


def _copy_file_remote(backend, src_rel: str, dst_rel: str,
                      on_bytes, should_stop) -> bool:
    """远程分块复制（1MB：进度/取消）；取消返回 False（临时文件由 open_write 清理），
    存储错误上抛由 worker 按项记 error。"""
    try:
        with backend.open_read(src_rel) as fr, backend.open_write(dst_rel) as fw:
            while True:
                if should_stop and should_stop():
                    raise _CopyCancelled()
                chunk = fr.read(1024 * 1024)
                if not chunk:
                    break
                fw.write(chunk)
                if on_bytes:
                    on_bytes(len(chunk))
        return True
    except _CopyCancelled:
        return False


def _copy_worker_remote(jid: str, items: list[str], to_dir: str, hints: dict,
                        library_id=None) -> None:
    """远程直读库复制后台任务：目录递归经 iter_tree，文件 1MB 分块流式复制。"""
    lid = int(library_id or library_paths.DEFAULT_LIBRARY_ID)

    def _stop() -> bool:
        job = _COPY_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    bytes_done = 0

    def _on_bytes(n: int) -> None:
        nonlocal bytes_done
        bytes_done += n
        _COPY_JOBS.update(jid, bytes_done=bytes_done)

    results: list[dict] = []
    renamed = 0
    registered = 0
    try:
        backend = storage.backend_for(lid)
        for rel in items:
            if _stop():
                return
            base = os.path.basename(rel.rstrip("/"))
            dst_rel, was_renamed = _unique_dst_rel(backend, to_dir, base)
            if was_renamed:
                renamed += 1
            item_status = "copied"
            try:
                st = backend.stat(rel)
                if st.is_dir:
                    # 目录递归：远程无 POSIX 符号链接语义（服务器侧链接按普通项处理）
                    backend.mkdir(dst_rel, parents=True)
                    prefix = rel.rstrip("/") + "/"
                    for e in backend.iter_tree(rel):
                        sub = e.rel[len(prefix):] if e.rel.startswith(prefix) else e.name
                        target = f"{dst_rel}/{sub}"
                        if e.is_dir:
                            backend.mkdir(target, parents=True)
                        else:
                            if _stop():
                                return
                            if not _copy_file_remote(backend, e.rel, target,
                                                     _on_bytes, _stop):
                                return
                else:
                    if _stop():
                        return
                    if not _copy_file_remote(backend, rel, dst_rel, _on_bytes, _stop):
                        return
                    if is_feature_video(dst_rel, library_id=lid, backend=backend):
                        # 正片复制 → 登记（源已匹配则直绑 tmdb，避免重搜/误配）
                        hint = hints.get(rel)
                        try:
                            r = scanner.scan_file(backend, dst_rel, tmdb_hint=hint)
                            item_status = str(r.get("status") or "copied")
                            if r.get("status") in ("ok", "ok_needs_review"):
                                registered += 1
                        except Exception as e:
                            logger.warning("register copied feature failed rel=%s: %s",
                                           dst_rel, e)
                            item_status = f"copied_scan_warn: {e}"
            except storage.StorageError as e:
                item_status = f"error: {e}"
            results.append({"from": rel, "to": dst_rel, "status": item_status,
                            "renamed": was_renamed})
            _COPY_JOBS.update(jid, done=len(results), renamed=renamed,
                              registered=registered)
        if _stop():
            return
        _COPY_JOBS.update(jid, state="done", done=len(results), renamed=renamed,
                          registered=registered, results=results[:200])
    except Exception as e:
        logger.warning("remote copy job failed jid=%s: %s", jid, e)
        _COPY_JOBS.update(jid, state="failed", error=str(e)[:300])


def _copy_worker(jid: str, items: list[str], to_dir: str, hints: dict,
                 library_id=None) -> None:
    lid = int(library_id or library_paths.DEFAULT_LIBRARY_ID)
    def _stop() -> bool:
        job = _COPY_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    bytes_done = 0

    def _on_bytes(n: int) -> None:
        nonlocal bytes_done
        bytes_done += n
        _COPY_JOBS.update(jid, bytes_done=bytes_done)

    results: list[dict] = []
    renamed = 0
    registered = 0
    try:
        for rel in items:
            if _stop():
                return
            src_abs = library_paths.resolve(lid, rel)
            base = os.path.basename(rel.rstrip("/"))
            dst_abs, was_renamed = _unique_dst(
                library_paths.resolve(lid, os.path.join(to_dir, base)) if to_dir
                else library_paths.resolve(lid, base))
            if was_renamed:
                renamed += 1
            item_status = "copied"
            if os.path.isdir(src_abs) and not os.path.islink(src_abs):
                # 目录递归：保留符号链接；复制过程不自动登记（完成后由前端引导「扫描新文件」）
                try:
                    for root, dnames, fnames in os.walk(src_abs):
                        rel_root = os.path.relpath(root, src_abs)
                        target = dst_abs if rel_root == "." else os.path.join(dst_abs, rel_root)
                        os.makedirs(target, exist_ok=True)
                        for d in list(dnames):
                            p = os.path.join(root, d)
                            if os.path.islink(p):
                                os.symlink(os.readlink(p), os.path.join(target, d))
                                dnames.remove(d)
                        for f in fnames:
                            if _stop():
                                return
                            fp = os.path.join(root, f)
                            tp = os.path.join(target, f)
                            if os.path.islink(fp):
                                os.symlink(os.readlink(fp), tp)
                                continue
                            os.makedirs(os.path.dirname(tp), exist_ok=True)
                            if not _copy_file(fp, tp, _on_bytes, _stop):
                                return
                except OSError as e:
                    item_status = f"error: {e}"
            else:
                # 单文件（含符号链接）
                try:
                    if os.path.islink(src_abs):
                        os.symlink(os.readlink(src_abs), dst_abs)
                    else:
                        os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
                        if not _copy_file(src_abs, dst_abs, _on_bytes, _stop):
                            return
                    rel_dst = os.path.relpath(dst_abs, library_paths.library_root(lid))
                    if is_feature_video(rel_dst, library_id=lid):
                        # 正片复制 → 登记（源已匹配则直绑 tmdb，避免重搜/误配）
                        hint = hints.get(rel)
                        try:
                            r = scanner.scan_one(dst_abs, tmdb_hint=hint,
                                                 library_id=lid)
                            item_status = str(r.get("status") or "copied")
                            if r.get("status") in ("ok", "ok_needs_review"):
                                registered += 1
                        except Exception as e:
                            logger.warning("register copied feature failed rel=%s: %s", rel_dst, e)
                            item_status = f"copied_scan_warn: {e}"
                except OSError as e:
                    item_status = f"error: {e}"
            results.append({"from": rel,
                            "to": os.path.relpath(dst_abs, library_paths.library_root(lid)),
                            "status": item_status, "renamed": was_renamed})
            _COPY_JOBS.update(jid, done=len(results), renamed=renamed, registered=registered)
        if _stop():
            return
        _COPY_JOBS.update(jid, state="done", done=len(results), renamed=renamed,
                          registered=registered, results=results[:200])
    except Exception as e:
        logger.warning("copy job failed jid=%s: %s", jid, e)
        _COPY_JOBS.update(jid, state="failed", error=str(e)[:300])


@router.post("/copy")
def fs_copy(body: dict | None = None):
    """复制文件/目录到目标目录：{from[], to_dir, dry_run}。

    - dry_run（默认 true）只预览：总字节/文件数/冲突项/是否需要二次确认。
    - dry_run:false 启动后台任务（大文件不卡请求），GET /copy/{job_id} 看进度，
      POST /copy/{job_id}/cancel 取消。
    - 冲突一律自动副本命名，绝不覆盖；正片复制后自动登记（同 tmdb 归为同一张卡）。
    - 目录不允许复制进自身（防递归膨胀）。
    """
    body = body or {}
    raws = body.get("from") or []
    if not isinstance(raws, list) or not raws:
        raise HTTPException(422, "from required")
    if len(raws) > 100:
        raise HTTPException(422, "too many paths (max 100)")
    try:
        lid_raw = body.get("library_id", body.get("library"))
        lid = int(lid_raw) if lid_raw not in (None, "") else library_paths.default_id()
    except (TypeError, ValueError):
        raise HTTPException(422, "library must be int")
    try:
        backend = storage.backend_for(lid)
    except storage.StorageError as e:
        raise HTTPException(503, f"library unavailable: {e}")
    remote = backend.abs_path("") is None
    # 目标目录可不存在（粘贴到新目录），空串=根；_check_inside_root 拒绝空串，故单独处理
    raw_to = str(body.get("to_dir") or "").strip().strip("/")
    to_dir = _check_inside_root(raw_to, lid) if raw_to else ""
    items: list[str] = []
    hints: dict = {}
    conflicts: list[str] = []
    if remote:
        if to_dir and _rel_exists(backend, to_dir):
            try:
                if not backend.is_dir(to_dir):
                    raise HTTPException(422, f"target is not a directory: {to_dir!r}")
            except HTTPException:
                raise
            except storage.StorageError as e:
                raise HTTPException(503, f"target stat failed: {e}")
        for r in raws:
            rel = _check_inside_root(str(r or ""), lid)
            try:
                st = backend.stat(rel)
            except storage.StorageNotFound:
                raise HTTPException(404, f"not found: {rel!r}")
            except storage.StorageError as e:
                raise HTTPException(503, f"stat failed: {e}")
            if st.is_dir and (to_dir == rel or to_dir.startswith(rel.rstrip("/") + "/")):
                raise HTTPException(422, f"target inside source: {rel!r}")
            base = os.path.basename(rel.rstrip("/"))
            if _rel_exists(backend, f"{to_dir}/{base}" if to_dir else base):
                conflicts.append(base)
            info = _classify(rel, library_id=lid, backend=backend)
            if info.get("kind") == "feature" and info.get("tmdb_id"):
                hints[rel] = int(info["tmdb_id"])
            items.append(rel)
        total_bytes, files, dirs = _measure_remote(backend, items)
    else:
        dst_root = library_paths.resolve(lid, to_dir) if to_dir \
            else library_paths.library_root(lid)
        if os.path.exists(dst_root) and not os.path.isdir(dst_root):
            raise HTTPException(422, f"target is not a directory: {to_dir!r}")
        for r in raws:
            rel = _check_inside_root(str(r or ""), lid)
            abs_p = library_paths.resolve(lid, rel)
            if not os.path.exists(abs_p):
                raise HTTPException(404, f"not found: {rel!r}")
            if os.path.isdir(abs_p) and not os.path.islink(abs_p):
                norm_src = os.path.normpath(abs_p)
                norm_dst = os.path.normpath(dst_root)
                if norm_dst == norm_src or norm_dst.startswith(norm_src + os.sep):
                    raise HTTPException(422, f"target inside source: {rel!r}")
            base = os.path.basename(rel.rstrip("/"))
            if os.path.exists(os.path.join(dst_root, base)):
                conflicts.append(base)
            info = _classify(rel, library_id=lid)
            if info.get("kind") == "feature" and info.get("tmdb_id"):
                hints[rel] = int(info["tmdb_id"])
            items.append(rel)
        total_bytes, files, dirs = _measure(items, lid)
    if body.get("dry_run", True):
        needs_confirm = (total_bytes >= COPY_CONFIRM_BYTES
                         or (files + dirs) >= COPY_CONFIRM_ITEMS)
        return {"dry_run": True, "total": len(items), "files": files, "dirs": dirs,
                "bytes": total_bytes, "conflicts": conflicts[:50],
                "library_id": lid,
                "needs_confirm": needs_confirm,
                "hint": (f"约 {files} 个文件 / {round(total_bytes / 1024 / 1024 / 1024, 2)} GB"
                         + ("，同名的会自动改「(副本)」" if conflicts else ""))}
    _require_writable(lid)
    job = _COPY_JOBS.create(total=len(items), bytes_total=total_bytes,
                            library_id=lid)
    jid = job["job_id"]
    worker = _copy_worker_remote if remote else _copy_worker
    threading.Thread(target=worker, args=(jid, items, to_dir, hints, lid),
                     daemon=True).start()
    return {"dry_run": False, "job_id": jid, "total": len(items), "bytes": total_bytes,
            "conflicts": conflicts[:50]}


@router.get("/copy/{job_id}")
def fs_copy_status(job_id: str):
    job = _COPY_JOBS.get(job_id) if job_id else _COPY_JOBS.latest()
    if not job:
        return {"job_id": job_id, "state": "idle"}
    return job


@router.post("/copy/{job_id}/cancel")
def fs_copy_cancel(job_id: str = ""):
    if not job_id:
        running = _COPY_JOBS.running()
        job_id = running["job_id"] if running else ""
    return {"job_id": job_id, "state": _COPY_JOBS.cancel(job_id)}
