"""routers.fs.routes（自 app/routers/fs.py 拆分，评审 R01-Q4；经 fs 门面使用）。"""
import os

from fastapi import HTTPException

from ... import library_paths, store
from ..files import _require_writable, _safe_component
from .classify import _classify
from .common import logger, router
from .classify import _impact_for_delete
from .ops import _exec_delete_one, _exec_move_one
from .paths import _check_inside_root, _resolve_dir
__all__ = ['fs_list', 'fs_mkdir', 'fs_rename', 'fs_move', 'fs_delete']


def _lib_param(v) -> int | None:
    """library 参数归一（缺省=None → 默认库）。"""
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(422, "library must be int")


@router.get("/list")
def fs_list(path: str = "", limit: int = 1000, offset: int = 0,
            library: int | None = None):
    """浏览目录（只读）：根用空串；返回 dirs/files（大小/mtime/定性）。
    files 支持 limit/offset 分页（评审 B8/R01-Q6：超大目录不再一次全量）。
    `library` 缺省=默认库。"""
    try:
        limit = max(1, min(int(limit or 1000), 5000))
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        limit, offset = 1000, 0
    lid = library_paths.default_id() if library is None else int(library)
    norm = _resolve_dir(path, lid)
    abs_p = library_paths.resolve(lid, norm)
    try:
        names = sorted(os.listdir(abs_p))
    except OSError as e:
        raise HTTPException(500, f"list failed: {e}")
    try:
        extras_map = {e["file_path"]: e for e in store.list_all_extras()
                      if int(e.get("library_id") or 0) == int(lid)}
    except Exception as e:
        logger.debug("extras prefetch failed: %s", e)
        extras_map = {}
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
                dirs.append({"name": n, "rel": rel, "children": c})
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
            "has_more": has_more, "limit": limit, "offset": offset}


@router.post("/mkdir")
def fs_mkdir(body: dict | None = None):
    """新建子目录：{path, name, library?}，name 为单段目录名。直接执行（可逆、无 DB 影响）。"""
    body = body or {}
    lid = _lib_param(body.get("library_id", body.get("library")))
    lid = library_paths.default_id() if lid is None else lid
    parent = _resolve_dir(str(body.get("path") or ""), lid)
    name = _safe_component(str(body.get("name") or ""))
    if not name or name in (".", "..") or "/" in str(body.get("name") or ""):
        raise HTTPException(422, "illegal dir name")
    rel = os.path.join(parent, name) if parent else name
    rel = _check_inside_root(rel, lid)
    abs_p = library_paths.resolve(lid, rel)
    _require_writable(lid)
    try:
        os.makedirs(abs_p, exist_ok=False)
    except FileExistsError:
        raise HTTPException(409, f"already exists: {rel!r}")
    except OSError as e:
        raise HTTPException(500, f"mkdir failed: {e}")
    return {"rel": rel, "status": "created"}


@router.post("/rename")
def fs_rename(body: dict | None = None):
    """文件改名（同目录）：{from, name, dry_run, library?}。改名正片会跟随字幕/花絮兄弟。"""
    body = body or {}
    dry_run = body.get("dry_run", True)
    lid = _lib_param(body.get("library_id", body.get("library")))
    lid = library_paths.default_id() if lid is None else lid
    fr = _check_inside_root(str(body.get("from") or ""), lid)
    raw_name = str(body.get("name") or "")
    name = _safe_component(raw_name)
    # 检查原始输入（sanitize 后 "/" 必不存在，评审 R01-B4）：防静默改写 a/b → ab
    if not name or "/" in raw_name or "\\" in raw_name:
        raise HTTPException(422, "illegal file name")
    if not os.path.isfile(library_paths.resolve(lid, fr)):
        raise HTTPException(404, f"not a file: {fr!r}")
    to = os.path.join(os.path.dirname(fr), name) if os.path.dirname(fr) else name
    to = _check_inside_root(to, lid)
    if os.path.normpath(fr) == os.path.normpath(to):
        raise HTTPException(422, "name unchanged")
    info = _classify(fr, library_id=lid)
    if dry_run:
        preview = {**info, "from": fr, "to": to, "status": "planned"}
        if os.path.exists(library_paths.resolve(lid, to)):
            preview["status"] = "conflict_disk_exists"
        return {"dry_run": True, "plans": [preview]}
    _require_writable(lid)
    r = _exec_move_one(fr, to, library_id=lid)
    return {"dry_run": False, "results": [r],
            "moved": 1 if r.get("status") == "moved" else 0}


@router.post("/move")
def fs_move(body: dict | None = None):
    """文件移动：{from, to_dir, dry_run, library?}，to_dir 不存在自动建。"""
    body = body or {}
    dry_run = body.get("dry_run", True)
    lid = _lib_param(body.get("library_id", body.get("library")))
    lid = library_paths.default_id() if lid is None else lid
    fr = _check_inside_root(str(body.get("from") or ""), lid)
    to_dir = _check_inside_root(str(body.get("to_dir") or ""), lid)
    if not os.path.isfile(library_paths.resolve(lid, fr)):
        raise HTTPException(404, f"not a file: {fr!r}")
    to = os.path.join(to_dir, os.path.basename(fr))
    to = _check_inside_root(to, lid)
    if os.path.normpath(fr) == os.path.normpath(to):
        raise HTTPException(422, "already there")
    info = _classify(fr, library_id=lid)
    if dry_run:
        preview = {**info, "from": fr, "to": to, "status": "planned"}
        if os.path.exists(library_paths.resolve(lid, to)):
            preview["status"] = "conflict_disk_exists"
        return {"dry_run": True, "plans": [preview]}
    _require_writable(lid)
    r = _exec_move_one(fr, to, library_id=lid)
    return {"dry_run": False, "results": [r],
            "moved": 1 if r.get("status") == "moved" else 0}


@router.post("/delete")
def fs_delete(body: dict | None = None):
    """删除文件：{paths[], dry_run, confirm}。

    - 花絮/字幕/周边：dry_run:false 即删（无二次确认）。
    - 正片 feature：预览带 requires_confirm + 影响面，必须 confirm:true 才执行；
      执行删文件 + 删库行 + NFO 收尾（海报/tmdb_cache 保留）。
    - 目录：仅允许空目录（传目录 rel，调 rmdir），非空拒绝。
    """
    body = body or {}
    raws = body.get("paths") or []
    if not isinstance(raws, list) or not raws:
        raise HTTPException(422, "paths required")
    if len(raws) > 100:
        raise HTTPException(422, "too many paths (max 100)")
    dry_run = body.get("dry_run", True)
    confirm = bool(body.get("confirm", False))
    lid_raw = _lib_param(body.get("library_id", body.get("library")))
    lid = library_paths.default_id() if lid_raw is None else lid_raw
    plans: list[dict] = []
    for r in raws:
        rel = _check_inside_root(str(r or ""), lid)
        abs_p = library_paths.resolve(lid, rel)
        if os.path.isdir(abs_p):
            try:
                empty = not os.listdir(abs_p)
            except OSError:
                empty = False
            plans.append({"rel": rel, "name": os.path.basename(rel),
                          "kind": "dir", "requires_confirm": False,
                          "library_id": lid,
                          "status": "planned_rmdir" if empty else "dir_not_empty"})
            continue
        if not os.path.lexists(abs_p):
            plans.append({"rel": rel, "name": os.path.basename(rel),
                          "kind": "other", "requires_confirm": False,
                          "library_id": lid, "status": "skipped_missing_src"})
            continue
        plans.append({**_impact_for_delete(rel, lid), "library_id": lid})
    needs_confirm = [p for p in plans if p.get("requires_confirm")]
    if dry_run or (needs_confirm and not confirm):
        return {"dry_run": True, "total": len(plans), "plans": plans,
                "needs_confirm": len(needs_confirm),
                "hint": ("含正片文件，需 confirm:true 二次确认"
                         if needs_confirm else "")}
    _require_writable(lid)
    done = []
    for p in plans:
        if p.get("kind") == "dir":
            if p.get("status") != "planned_rmdir":
                done.append({**p, "status": p.get("status") or "dir_not_empty"})
                continue
            try:
                os.rmdir(library_paths.resolve(lid, p["rel"]))
                done.append({**p, "status": "deleted"})
            except OSError as e:
                done.append({**p, "status": f"error: {e}"})
            continue
        if p.get("status") == "skipped_missing_src":
            done.append(p)
            continue
        done.append(_exec_delete_one(p))
    ok = sum(1 for r in done if r.get("status") == "deleted")
    return {"dry_run": False, "total": len(done), "deleted": ok,
            "results": done}


# ===== 复制（评审 B9 后续）：目录递归 + 冲突自动副本命名 + 后台 job 进度/取消 =====

