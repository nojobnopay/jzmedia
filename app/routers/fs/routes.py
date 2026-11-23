"""routers.fs.routes（自 app/routers/fs.py 拆分，评审 R01-Q4；经 fs 门面使用）。"""
import hashlib
import json
import os
import re
from functools import wraps
from typing import Literal

from fastapi import HTTPException, Query, Request

from ... import library_paths, storage, store
from ...library_mutex import library_mutation_lock
from ..blob import guess_media_type, media_response, text_response
from ..files import _require_writable, _safe_component
from .classify import _classify
from .common import logger, router
from .classify import _impact_for_delete
from .ops import (_exec_delete_one, _exec_delete_one_remote, _exec_move_one, _follow_plan, _db_path_occupied,
                  _exec_move_one_remote)
from .paths import _check_inside_root, _resolve_dir
__all__ = ['fs_list', 'fs_blob', 'fs_mkdir', 'fs_rename', 'fs_move', 'fs_delete', 'fs_changes']

# Explicit MIME values also apply to remote streams; SVG/HTML are deliberately
# excluded so user files cannot execute in the application's origin.
_PREVIEW_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.webp': 'image/webp', '.gif': 'image/gif', '.pdf': 'application/pdf',
    '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mkv': 'video/x-matroska',
    '.webm': 'video/webm', '.mov': 'video/quicktime', '.avi': 'video/x-msvideo',
    '.ts': 'video/mp2t', '.m2ts': 'video/mp2t', '.flv': 'video/x-flv',
}


@router.get('/blob')
def fs_blob(request: Request, library: int = Query(..., gt=0),
            path: str = Query(..., min_length=1), mode: Literal['raw', 'text'] = 'raw',
            inline: bool = False):
    """Read an exact library-relative file without requiring a catalogue record."""
    # Resolve this exact library before validating or opening the requested path.
    lib = store.get_library(library)
    if lib is None:
        raise HTTPException(404, 'library not found')
    if ('\\' in path or '\x00' in path or path != path.strip()
            or any(part in ('', '.', '..') for part in path.split('/'))
            or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', path)):
        raise HTTPException(422, 'path must be a relative file path')
    try:
        backend = storage.backend_for_library(lib)
        if backend.driver == 'mount':
            mount_root = lib.get('media_mount_point') or lib.get('media_path') or backend.root
            if not os.path.ismount(mount_root):
                raise storage.StorageOffline('source is not mounted')
        rel = backend.norm(path)
        if not rel or rel != path:
            raise storage.StorageInvalidPath('path must be a canonical relative file path')
        # Local stat is intentionally lax for browsing. Content access must
        # resolve symlinks against this exact library root before stat/response.
        backend.abs_path(rel)
    except storage.StorageInvalidPath as e:
        raise HTTPException(422, 'invalid file path') from e
    except storage.StorageNotFound as e:
        raise HTTPException(404, 'file missing') from e
    except storage.StorageDenied as e:
        raise HTTPException(403, 'file access denied') from e
    except storage.StorageError as e:
        raise HTTPException(503, 'library unavailable') from e
    _stat_file_or_404(backend, rel)
    if mode == 'text':
        return text_response(backend, rel)
    preview_type = _PREVIEW_TYPES.get(os.path.splitext(rel)[1].lower())
    return media_response(request, backend, rel, filename=os.path.basename(rel),
                          media_type=preview_type or guess_media_type(rel),
                          inline=inline and preview_type is not None)


def _lib_param(v) -> int | None:
    """library 参数归一（缺省=None → 默认库）。"""
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(422, "library must be int")


def _locked_write(fn):
    @wraps(fn)
    def run(body: dict | None = None):
        value = body or {}
        lid = _lib_param(value.get('library_id', value.get('library')))
        lid = library_paths.default_id() if lid is None else lid
        with library_mutation_lock(lid):
            return fn(body)
    return run


def _backend(lid: int):
    try:
        return storage.backend_for(lid)
    except storage.StorageNotFound as e:
        raise HTTPException(404, "library not found") from e
    except storage.StorageError as e:
        raise HTTPException(503, f"library unavailable: {e}")


def _is_remote(backend) -> bool:
    """直读远程库（无 POSIX 路径）→ 写操作走 StorageBackend。"""
    return backend.abs_path("") is None


def _rel_exists(backend, rel: str) -> bool:
    try:
        return bool(backend.exists(rel))
    except storage.StorageError:
        return False


def _rel_is_file(backend, rel: str) -> bool:
    try:
        return not backend.stat(rel).is_dir
    except storage.StorageError:
        return False


def _stat_file_or_404(backend, rel: str) -> None:
    """文件存在性校验：缺失/目录 → 404；存储错误映射 HTTP。"""
    try:
        st = backend.stat(rel)
    except storage.StorageNotFound:
        raise HTTPException(404, f"not a file: {rel!r}")
    except storage.StorageError as e:
        raise _http_storage(e, "stat failed")
    if st.is_dir:
        raise HTTPException(404, f"not a file: {rel!r}")


def _http_storage(e: storage.StorageError, action: str) -> HTTPException:
    """存储错误 → HTTP（远程写操作统一映射）。"""
    if isinstance(e, storage.StorageOffline):
        return HTTPException(503, f"{action}: source offline")
    if isinstance(e, storage.StorageDenied):
        return HTTPException(403, f"{action}: {e}")
    if isinstance(e, storage.StorageReadOnly):
        return HTTPException(409, str(e))
    return HTTPException(500, f"{action}: {e}")


def _list_remote(backend, norm: str, lid: int, extras_map: dict,
                 limit: int, offset: int) -> dict:
    """直读远程库目录浏览：StorageBackend.list（每目录一次网络往返）。"""
    from ... import storage
    try:
        entries = backend.list(norm)
    except storage.StorageNotFound:
        raise HTTPException(404, f"not found: {norm or '/'}")
    except storage.StorageOffline as e:
        raise HTTPException(503, f"source offline: {e}")
    except storage.StorageError as e:
        raise HTTPException(500, f"list failed: {e}")
    dirs, files = [], []
    for e in sorted(entries, key=lambda x: x["name"]):
        n = e["name"]
        if n.startswith("."):
            continue
        rel = os.path.join(norm, n) if norm else n
        if e["is_dir"]:
            dirs.append({"name": n, "rel": rel, "children": None, "mtime": e.get('mtime')})
        else:
            files.append(_classify(rel, extras_map, library_id=lid,
                                   entry=e, backend=backend))
    parent = os.path.dirname(norm) if norm else ""
    crumbs = []
    if norm:
        acc = []
        for part in norm.split("/"):
            acc.append(part)
            crumbs.append({"name": part, "rel": "/".join(acc)})
    total_files = len(files)
    has_more = offset + limit < total_files
    return {"path": norm, "parent": parent, "crumbs": crumbs,
            "dirs": dirs, "files": files[offset:offset + limit],
            "total_dirs": len(dirs), "total_files": total_files,
            "has_more": has_more, "limit": limit, "offset": offset,
            # 直读远程库写操作已经 StorageBackend（mkdir/rename/move/delete/copy）
            "fs_writable": not backend.read_only, "driver": backend.driver}


def _media_norm(path: str) -> str:
    """媒体根内相对路径归一（空=根）；越界/绝对路径 422。"""
    raw = str(path or "").strip().replace("\\", "/")
    if os.path.isabs(raw):
        raise HTTPException(422, f"illegal path: {path!r}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise HTTPException(422, f"path escapes media root: {path!r}")
    return "/".join(parts)


def _list_media_root(mid: int, path: str, limit: int, offset: int) -> dict:
    """媒体库根浏览（只读，文件浏览媒体根模式）：真实列目录 + 视频库 subpath 徽章。
    不进 DB 分类（媒体根不属于任何视频库），文件一律 kind=other、不可写。"""
    from ... import storage
    lib = store.get_media_library(mid)
    if not lib:
        raise HTTPException(404, "media library not found")
    norm = _media_norm(path)
    try:
        backend = storage.backend_for_media(mid)
    except storage.StorageError as e:
        raise HTTPException(503, f"media library unavailable: {e}")
    if norm:
        try:
            if not backend.is_dir(norm):
                raise HTTPException(404, f"not a directory: {path!r}")
        except HTTPException:
            raise
        except storage.StorageNotFound:
            raise HTTPException(404, f"not found: {path!r}")
        except storage.StorageOffline as e:
            raise HTTPException(503, f"source offline: {e}")
        except storage.StorageError as e:
            raise HTTPException(500, f"stat failed: {e}")
    try:
        entries = backend.list(norm)
    except storage.StorageNotFound:
        raise HTTPException(404, f"not found: {path!r}")
    except storage.StorageOffline as e:
        raise HTTPException(503, f"source offline: {e}")
    except storage.StorageError as e:
        raise HTTPException(500, f"list failed: {e}")
    vids = [(str(v.get("subpath") or "").strip().strip("/"), v)
            for v in store.video_libraries_of(mid)]
    exact = {sub: v for sub, v in vids if sub}
    dirs, files = [], []
    for e in sorted(entries, key=lambda x: x["name"]):
        n = str(e.get("name") or "")
        if not n or n.startswith("."):
            continue
        rel = f"{norm}/{n}" if norm else n
        if e.get("is_dir"):
            item = {"name": n, "rel": rel, "children": None, "mtime": e.get('mtime')}
            v = exact.get(rel)
            if v:
                item["video_library_id"] = v.get("id")
                item["video_library_name"] = v.get("name")
                item["kind"] = v.get("kind") or "movie"
            elif any(sub.startswith(rel + "/") for sub, _ in vids if sub):
                item["contains_video_library"] = True
            dirs.append(item)
        else:
            files.append({"rel": rel, "name": n,
                          "size": int(e.get("size") or 0),
                          "mtime": float(e.get("mtime") or 0),
                          "kind": "other", "registered": False, "match_status": None})
    parent = os.path.dirname(norm) if norm else ""
    crumbs = []
    if norm:
        acc = []
        for part in norm.split("/"):
            acc.append(part)
            crumbs.append({"name": part, "rel": "/".join(acc)})
    total_files = len(files)
    has_more = offset + limit < total_files
    return {"path": norm, "parent": parent, "crumbs": crumbs,
            "dirs": dirs, "files": files[offset:offset + limit],
            "total_dirs": len(dirs), "total_files": total_files,
            "has_more": has_more, "limit": limit, "offset": offset,
            "fs_writable": False, "driver": backend.driver,
            "root_kind": "media", "media_library_id": mid,
            "media_name": lib.get("name")}


@router.get("/list")
def fs_list(path: str = "", limit: int = 1000, offset: int = 0,
            library: int | None = None,
            media_library: int | None = None):
    """浏览目录（只读）：根用空串；返回 dirs/files（大小/mtime/定性）。
    files 支持 limit/offset 分页（评审 B8/R01-Q6：超大目录不再一次全量）。
    `library` 缺省=默认库；直读远程库同样可浏览（fs_writable=false，写入操作 501）。
    `media_library` 列媒体库根（文件浏览媒体根模式）：首层目录带 `video_library_id`
    徽章，进入视频库目录后前端切换回 `library` 上下文；媒体根一律只读。"""
    try:
        limit = max(1, min(int(limit or 1000), 5000))
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        limit, offset = 1000, 0
    if media_library is not None and str(media_library).strip() != "":
        try:
            mid = int(media_library)
        except (TypeError, ValueError):
            raise HTTPException(422, "media_library must be int")
        return _list_media_root(mid, path, limit, offset)
    lid = library_paths.default_id() if library is None else int(library)
    norm = _resolve_dir(path, lid)
    backend = _backend(lid)
    try:
        extras_map = {e["file_path"]: e for e in store.list_all_extras()
                      if int(e.get("library_id") or 0) == int(lid)}
    except Exception as e:
        logger.debug("extras prefetch failed: %s", e)
        extras_map = {}
    if backend.abs_path(norm) is None:
        return _list_remote(backend, norm, lid, extras_map, limit, offset)
    abs_p = library_paths.resolve(lid, norm)
    try:
        names = sorted(os.listdir(abs_p))
    except OSError as e:
        raise HTTPException(500, f"list failed: {e}")
    dirs, files = [], []
    for n in names:
        # 跳过系统/隐藏文件，保持列表干净
        if n.startswith("."):
            continue
        full = os.path.join(abs_p, n)
        rel = os.path.join(norm, n) if norm else n
        try:
            if os.path.isdir(full):
                try:
                    c = len(os.listdir(full))
                except OSError:
                    c = 0
                dirs.append({"name": n, "rel": rel, "children": c, "mtime": os.stat(full).st_mtime})
            elif os.path.isfile(full):
                files.append(_classify(rel, extras_map, library_id=lid))
        except OSError:
            continue
    parent = os.path.dirname(norm) if norm else ""
    crumbs = []
    if norm:
        acc = []
        for part in norm.split("/"):
            acc.append(part)
            crumbs.append({"name": part, "rel": "/".join(acc)})
    total_files = len(files)
    has_more = offset + limit < total_files
    return {"path": norm, "parent": parent, "crumbs": crumbs,
            "dirs": dirs, "files": files[offset:offset + limit],
            "total_dirs": len(dirs), "total_files": total_files,
            "has_more": has_more, "limit": limit, "offset": offset,
            "fs_writable": not backend.read_only, "driver": backend.driver}


@router.get('/changes')
def fs_changes(library: int | None = None):
    """Pending physical changes and recoverable copy jobs, scoped by video library."""
    from .copy import active_copy_jobs
    libs = [store.get_library(library)] if library is not None else store.list_libraries()
    if library is not None and not libs[0]:
        raise HTTPException(404, 'library not found')
    jobs = active_copy_jobs()
    items = []
    for lib in libs:
        item = store.fs_change_summary(lib['id'])
        item.update(library_name=lib['name'], kind=lib['kind'],
                    active_jobs=[j for j in jobs if j.get('library_id') == lib['id']])
        items.append(item)
    return items[0] if library is not None else {'items': items, 'count': sum(i['count'] for i in items)}


@router.post("/mkdir")
@_locked_write
def fs_mkdir(body: dict | None = None):
    """新建子目录：{path, name, library?}，name 为单段目录名。直接执行（可逆、无 DB 影响）。"""
    body = body or {}
    lid = _lib_param(body.get("library_id", body.get("library")))
    lid = library_paths.default_id() if lid is None else lid
    backend = _backend(lid)
    parent = _resolve_dir(str(body.get("path") or ""), lid)
    name = _safe_component(str(body.get("name") or ""))
    if not name or name in (".", "..") or "/" in str(body.get("name") or ""):
        raise HTTPException(422, "illegal dir name")
    rel = os.path.join(parent, name) if parent else name
    rel = _check_inside_root(rel, lid)
    _require_writable(lid)
    if _is_remote(backend):
        if _rel_exists(backend, rel):
            raise HTTPException(409, f"already exists: {rel!r}")
        try:
            backend.mkdir(rel)
        except storage.StorageError as e:
            raise _http_storage(e, "mkdir failed")
        store.record_fs_change(lid, 'mkdir', rel)
        return {"rel": rel, "status": "created"}
    abs_p = library_paths.resolve(lid, rel)
    try:
        os.makedirs(abs_p, exist_ok=False)
    except FileExistsError:
        raise HTTPException(409, f"already exists: {rel!r}")
    except OSError as e:
        raise HTTPException(500, f"mkdir failed: {e}")
    store.record_fs_change(lid, 'mkdir', rel)
    return {"rel": rel, "status": "created"}


@router.post("/rename")
@_locked_write
def fs_rename(body: dict | None = None):
    """文件改名（同目录）：{from, name, dry_run, library?}。改名正片会跟随字幕/花絮兄弟。"""
    body = body or {}
    dry_run = body.get("dry_run", True)
    lid = _lib_param(body.get("library_id", body.get("library")))
    lid = library_paths.default_id() if lid is None else lid
    backend = _backend(lid)
    remote = _is_remote(backend)
    fr = _check_inside_root(str(body.get("from") or ""), lid)
    raw_name = str(body.get("name") or "")
    name = _safe_component(raw_name)
    # 检查原始输入（sanitize 后 "/" 必不存在，评审 R01-B4）：防静默改写 a/b → ab
    if not name or "/" in raw_name or "\\" in raw_name:
        raise HTTPException(422, "illegal file name")
    _stat_file_or_404(backend, fr)
    to = os.path.join(os.path.dirname(fr), name) if os.path.dirname(fr) else name
    to = _check_inside_root(to, lid)
    if os.path.normpath(fr) == os.path.normpath(to):
        raise HTTPException(422, "name unchanged")
    info = _classify(fr, library_id=lid, backend=backend if remote else None)
    if dry_run:
        preview = {**info, "from": fr, "to": to, "status": "planned",
                   "followers": _follow_plan(backend, fr, to, info)}
        if _rel_exists(backend, to):
            preview["status"] = "conflict_disk_exists"
        elif _db_path_occupied(lid, to):
            preview['status'] = 'conflict_db_occupied'
        return {"dry_run": True, "plans": [preview]}
    _require_writable(lid)
    r = (_exec_move_one_remote(fr, to, lid, backend) if remote
         else _exec_move_one(fr, to, library_id=lid))
    return {"dry_run": False, "results": [r],
            "moved": 1 if r.get("status") == "moved" else 0}


@router.post("/move")
@_locked_write
def fs_move(body: dict | None = None):
    """文件移动：{from, to_dir, dry_run, library?}，to_dir 不存在自动建。"""
    body = body or {}
    dry_run = body.get("dry_run", True)
    lid = _lib_param(body.get("library_id", body.get("library")))
    lid = library_paths.default_id() if lid is None else lid
    backend = _backend(lid)
    remote = _is_remote(backend)
    fr = _check_inside_root(str(body.get("from") or ""), lid)
    raw_to_dir = str(body.get("to_dir") or "").strip()
    to_dir = _check_inside_root(raw_to_dir, lid) if raw_to_dir else ''
    _stat_file_or_404(backend, fr)
    to = os.path.join(to_dir, os.path.basename(fr))
    to = _check_inside_root(to, lid)
    if os.path.normpath(fr) == os.path.normpath(to):
        raise HTTPException(422, "already there")
    info = _classify(fr, library_id=lid, backend=backend if remote else None)
    if dry_run:
        preview = {**info, "from": fr, "to": to, "status": "planned",
                   "followers": _follow_plan(backend, fr, to, info)}
        if _rel_exists(backend, to):
            preview["status"] = "conflict_disk_exists"
        elif _db_path_occupied(lid, to):
            preview['status'] = 'conflict_db_occupied'
        return {"dry_run": True, "plans": [preview]}
    _require_writable(lid)
    r = (_exec_move_one_remote(fr, to, lid, backend) if remote
         else _exec_move_one(fr, to, library_id=lid))
    return {"dry_run": False, "results": [r],
            "moved": 1 if r.get("status") == "moved" else 0}


@router.post("/delete")
@_locked_write
def fs_delete(body: dict | None = None):
    """Preview exact entries, then confirm the returned preview_id."""
    body = body or {}
    paths = body.get("paths")
    if not isinstance(paths, list) or not 1 <= len(paths) <= 100:
        raise HTTPException(422, "paths must contain 1 to 100 entries")
    lid = _lib_param(body.get("library_id", body.get("library")))
    lid = library_paths.default_id() if lid is None else lid
    return delete_paths(body, lid, paths)


def _delete_plan(backend, rel):
    lid = backend.library_id
    base = {"rel": rel, "name": os.path.basename(rel), "library_id": lid, "requires_confirm": False}
    try:
        identity = backend.delete_entry_identity(rel)
    except storage.StorageNotFound:
        return {**base, "kind": "other", "status": "skipped_missing_src"}
    if identity is None:
        return {**base, "kind": "other", "status": "unsupported"}
    kind, size, revision = identity
    base.update(size=size, revision=revision)
    if kind == "dir":
        status = "dir_not_empty" if backend.list(rel) else "planned_rmdir"
        return {**base, "kind": "dir", "status": status}
    return {**_impact_for_delete(rel, lid, backend=backend), "library_id": lid,
            "size": size, "revision": revision, "entry_kind": kind}


def delete_paths(body, lid, paths, *, movie_id=None):
    backend = _backend(lid)
    rels = list(dict.fromkeys(_check_inside_root(str(path or ""), lid) for path in paths))
    storage.clear_meta_cache(lid)
    try:
        plans = [_delete_plan(backend, rel) for rel in rels]
    except storage.StorageError as exc:
        raise _http_storage(exc, "无法核对删除清单") from exc
    digest = hashlib.sha256(json.dumps([lid, movie_id, plans], sort_keys=True,
                                       ensure_ascii=False).encode()).hexdigest()
    needs_confirm = sum(bool(p["requires_confirm"]) for p in plans)
    if body.get("dry_run", True) or (needs_confirm and not body.get("confirm")):
        return {"dry_run": True, "total": len(plans), "plans": plans, "preview_id": digest,
                "needs_confirm": needs_confirm, "hint": "请核对清单后确认删除"}
    _require_writable(lid)
    if body.get("preview_id") != digest:
        raise HTTPException(409, "删除清单已变化或缺少预览，请重新预览后确认")
    results = []
    for plan in plans:
        if plan.get("status") not in (None, "planned_rmdir"):
            results.append(plan)
            continue
        rel = plan["rel"]
        try:
            identity = backend.delete_entry_identity(rel)
            if not identity or identity[2] != plan["revision"]:
                raise storage.StorageSourceChanged("文件已变化，请重新预览")
            if plan.get("status") == "planned_rmdir":
                backend.delete_empty_dir(rel)
                store.record_fs_change(lid, "delete", rel)
                result = {**plan, "status": "deleted"}
            else:
                result = (_exec_delete_one_remote(plan, backend) if _is_remote(backend)
                          else _exec_delete_one(plan))
        except (storage.StorageError, OSError) as exc:
            result = {**plan, "status": f"error: {exc}"}
        results.append(result)
    return {"dry_run": False, "total": len(results), "deleted": sum(r["status"] == "deleted" for r in results),
            "results": results}


# ===== 复制（评审 B9 后续）：目录递归 + 冲突自动副本命名 + 后台 job 进度/取消 =====
