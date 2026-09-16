"""通用文件浏览（MVP）：浏览 MEDIA_ROOT、建子目录、改名、移动、删除。

约定与现有整理口保持一致：
- 所有相对路径必须经归一并约束在 MEDIA_ROOT 内（防 `../` 越狱），复用
  files._check_inside_root；根目录列表用空串表示。
- 新文件名经 files._safe_component 清洗（去 `\\/:*?"<>|`）。
- 破坏性操作默认 dry_run:true 只预览；删除正片视频必须 confirm:true 才执行
  （花絮/字幕/周边直接删）；目录只允许删空目录，不做递归删。
- DB 联动：正片删文件同时 store.delete_movie（含关联+FTS，海报/tmdb_cache 保留）；
  花絮删文件同时 delete_extra_by_path；改名/移动正片只改 file_path
  （经 update_movie_local，不污染 TMDB 镜像列）并走 sync_nfos_for 收敛。
"""
import os

from fastapi import APIRouter, HTTPException

from .. import store
from ..config import settings
from ..log import get_logger
from ..scanner import SUBTITLE_EXTS, is_feature_video, is_sidecar, sync_nfos_for
from .files import (_check_inside_root, _cleanup_old_dir, _rename_or_move,
                    _resync_old_dir, _safe_component, _sibling_followers)

router = APIRouter(prefix="/api/fs")
logger = get_logger("fs")


def _resolve_dir(rel: str | None) -> str:
    """目录相对路径归一：空串=根；其余必须在 ROOT 内且对应目录。"""
    raw = (rel or "").strip().strip("/")
    if not raw:
        return ""
    norm = _check_inside_root(raw)
    abs_p = os.path.join(settings.media_root, norm)
    if not os.path.isdir(abs_p):
        raise HTTPException(404, f"not a directory: {rel!r}")
    return norm


def _classify(rel: str, extras_map: dict | None = None) -> dict:
    """单文件定性：feature / sidecar / subtitle / nfo / other，附 DB 行提示。
    extras_map（{path: extra}）由列表口一次性传入（评审 B8/R08-B1：避免每文件全表扫 extras）。"""
    abs_p = os.path.join(settings.media_root, rel)
    size, mtime = 0, 0
    try:
        st = os.stat(abs_p)
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        pass
    base = {"rel": rel, "name": os.path.basename(rel),
            "size": size, "mtime": mtime}
    m = store.get_by_path(rel)
    if m:
        vers = []
        try:
            full = store.get_movie(m["id"]) or {}
            vers = full.get("versions") or []
        except Exception:
            vers = []
        return {**base, "kind": "feature", "movie_id": m["id"],
                "title": m.get("title", ""), "year": m.get("year"),
                "tmdb_id": m.get("tmdb_id"),
                "version_count": len(vers) or 1}
    if extras_map is not None:
        e = extras_map.get(rel)
        if e:
            return {**base, "kind": "sidecar",
                    "extra_id": e["id"], "movie_id": e.get("movie_id")}
    else:
        for e in store.list_all_extras():
            if e["file_path"] == rel:
                return {**base, "kind": "sidecar",
                        "extra_id": e["id"], "movie_id": e.get("movie_id")}
    _, ex = os.path.splitext(rel)
    ex = ex.lower()
    if is_sidecar(rel):
        return {**base, "kind": "sidecar"}
    if ex in SUBTITLE_EXTS:
        return {**base, "kind": "subtitle"}
    if ex == ".nfo":
        return {**base, "kind": "nfo"}
    if is_feature_video(rel):
        # 库无行但形态是正片（多为未扫描）：按 feature 对待，删时提醒
        return {**base, "kind": "feature", "movie_id": None,
                "version_count": 1}
    return {**base, "kind": "other"}


def _impact_for_delete(rel: str) -> dict:
    """删除预览：正片 requires_confirm=True 并带影响面，其余直接可删。"""
    info = _classify(rel)
    if info["kind"] == "feature":
        attached = 0
        if info.get("movie_id"):
            try:
                attached = len(store.list_extras_by_movie(info["movie_id"]))
            except Exception:
                attached = 0
        return {**info, "requires_confirm": True,
                "attached_extras": attached,
                "warn": "正片文件：删除后将从海报墙移除，关联与索引一并清理，海报/镜像缓存保留"}
    return {**info, "requires_confirm": False}


def _exec_delete_one(plan: dict) -> dict:
    """执行单文件删除（含 DB 联动与旧目录 NFO 收尾）。调用方已确认。"""
    rel = plan["rel"]
    abs_p = os.path.join(settings.media_root, rel)
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
                store.delete_extra_by_path(rel)
            except Exception:
                pass
    except Exception as e:
        return {**plan, "status": f"error: {e}"}
    _cleanup_old_dir(old_dir)
    _resync_old_dir(old_dir)
    return {**plan, "status": "deleted"}


def _move_db_follow(fr: str, to: str, info: dict) -> None:
    """移动后的 DB 联动：正片改 file_path + NFO；花絮改路径归属；其余不管。"""
    if info.get("kind") == "feature" and info.get("movie_id"):
        store.update_movie_local(info["movie_id"], file_path=to)
        try:
            sync_nfos_for(info["movie_id"],
                          os.path.join(settings.media_root, to))
        except Exception:
            pass
    elif info.get("kind") == "sidecar":
        try:
            rows = [e for e in store.list_all_extras()
                    if e["file_path"] == fr]
        except Exception:
            rows = []
        if rows:
            e = rows[0]
            try:
                store.delete_extra_by_path(fr)
            except Exception:
                pass
            try:
                store.upsert_extra(to, e.get("movie_id"), e.get("kind") or "extra")
            except Exception:
                pass


def _exec_move_one(fr: str, to: str) -> dict:
    """执行单文件改名/移动（含跟随字幕/花絮兄弟与 DB 联动）。"""
    from ..scanner import is_sidecar as _is_sidecar  # 局部引用防循环
    base = {"from": fr, "to": to}
    src = os.path.join(settings.media_root, fr)
    dst = os.path.join(settings.media_root, to)
    if not os.path.isfile(src):
        return {**base, "status": "skipped_missing_src"}
    if os.path.exists(dst):
        return {**base, "status": "conflict_disk_exists"}
    info = _classify(fr)
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        followers = _sibling_followers(src)
        _rename_or_move(src, dst)
        _move_db_follow(fr, to, info)
        # 同茎跟随：字幕/花絮兄弟随新茎改名
        new_stem = os.path.splitext(os.path.basename(dst))[0]
        old_stem = os.path.splitext(os.path.basename(src))[0]
        followed = 0
        for f in followers:
            suffix = os.path.basename(f)[len(old_stem):]
            fdst = os.path.join(os.path.dirname(dst), new_stem + suffix)
            try:
                if not os.path.exists(fdst):
                    frel_old = os.path.relpath(f, settings.media_root)
                    _rename_or_move(f, fdst)
                    frel_new = os.path.relpath(fdst, settings.media_root)
                    try:
                        rows = [e for e in store.list_all_extras()
                                if e["file_path"] == frel_old]
                        if rows:
                            store.delete_extra_by_path(frel_old)
                            store.upsert_extra(
                                frel_new, rows[0].get("movie_id"),
                                rows[0].get("kind") or "extra")
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
        _ = _is_sidecar
        return {**base, "status": "moved", "followed": followed,
                "kind": info.get("kind") or "other"}
    except Exception as e:
        return {**base, "status": f"error: {e}"}


@router.get("/list")
def fs_list(path: str = "", limit: int = 1000, offset: int = 0):
    """浏览目录（只读）：根用空串；返回 dirs/files（大小/mtime/定性）。
    files 支持 limit/offset 分页（评审 B8/R01-Q6：超大目录不再一次全量）。"""
    try:
        limit = max(1, min(int(limit or 1000), 5000))
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        limit, offset = 1000, 0
    norm = _resolve_dir(path)
    abs_p = os.path.join(settings.media_root, norm) if norm else settings.media_root
    try:
        names = sorted(os.listdir(abs_p))
    except OSError as e:
        raise HTTPException(500, f"list failed: {e}")
    try:
        extras_map = {e["file_path"]: e for e in store.list_all_extras()}
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
                files.append(_classify(rel, extras_map))
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
    """新建子目录：{path, name}，name 为单段目录名。直接执行（可逆、无 DB 影响）。"""
    body = body or {}
    parent = _resolve_dir(str(body.get("path") or ""))
    name = _safe_component(str(body.get("name") or ""))
    if not name or name in (".", "..") or "/" in str(body.get("name") or ""):
        raise HTTPException(422, "illegal dir name")
    rel = os.path.join(parent, name) if parent else name
    rel = _check_inside_root(rel)
    abs_p = os.path.join(settings.media_root, rel)
    try:
        os.makedirs(abs_p, exist_ok=False)
    except FileExistsError:
        raise HTTPException(409, f"already exists: {rel!r}")
    except OSError as e:
        raise HTTPException(500, f"mkdir failed: {e}")
    return {"rel": rel, "status": "created"}


@router.post("/rename")
def fs_rename(body: dict | None = None):
    """文件改名（同目录）：{from, name, dry_run}。改名正片会跟随字幕/花絮兄弟。"""
    body = body or {}
    dry_run = body.get("dry_run", True)
    fr = _check_inside_root(str(body.get("from") or ""))
    name = _safe_component(str(body.get("name") or ""))
    if not name or "/" in name:
        raise HTTPException(422, "illegal file name")
    if not os.path.isfile(os.path.join(settings.media_root, fr)):
        raise HTTPException(404, f"not a file: {fr!r}")
    to = os.path.join(os.path.dirname(fr), name) if os.path.dirname(fr) else name
    to = _check_inside_root(to)
    if os.path.normpath(fr) == os.path.normpath(to):
        raise HTTPException(422, "name unchanged")
    info = _classify(fr)
    if dry_run:
        preview = {**info, "from": fr, "to": to, "status": "planned"}
        if os.path.exists(os.path.join(settings.media_root, to)):
            preview["status"] = "conflict_disk_exists"
        return {"dry_run": True, "plans": [preview]}
    r = _exec_move_one(fr, to)
    return {"dry_run": False, "results": [r],
            "moved": 1 if r.get("status") == "moved" else 0}


@router.post("/move")
def fs_move(body: dict | None = None):
    """文件移动：{from, to_dir, dry_run}，to_dir 不存在自动建。"""
    body = body or {}
    dry_run = body.get("dry_run", True)
    fr = _check_inside_root(str(body.get("from") or ""))
    to_dir = _check_inside_root(str(body.get("to_dir") or ""))
    if not os.path.isfile(os.path.join(settings.media_root, fr)):
        raise HTTPException(404, f"not a file: {fr!r}")
    to = os.path.join(to_dir, os.path.basename(fr))
    to = _check_inside_root(to)
    if os.path.normpath(fr) == os.path.normpath(to):
        raise HTTPException(422, "already there")
    info = _classify(fr)
    if dry_run:
        preview = {**info, "from": fr, "to": to, "status": "planned"}
        if os.path.exists(os.path.join(settings.media_root, to)):
            preview["status"] = "conflict_disk_exists"
        return {"dry_run": True, "plans": [preview]}
    r = _exec_move_one(fr, to)
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
    plans: list[dict] = []
    for r in raws:
        rel = _check_inside_root(str(r or ""))
        abs_p = os.path.join(settings.media_root, rel)
        if os.path.isdir(abs_p):
            try:
                empty = not os.listdir(abs_p)
            except OSError:
                empty = False
            plans.append({"rel": rel, "name": os.path.basename(rel),
                          "kind": "dir", "requires_confirm": False,
                          "status": "planned_rmdir" if empty else "dir_not_empty"})
            continue
        if not os.path.lexists(abs_p):
            plans.append({"rel": rel, "name": os.path.basename(rel),
                          "kind": "other", "requires_confirm": False,
                          "status": "skipped_missing_src"})
            continue
        plans.append(_impact_for_delete(rel))
    needs_confirm = [p for p in plans if p.get("requires_confirm")]
    if dry_run or (needs_confirm and not confirm):
        return {"dry_run": True, "total": len(plans), "plans": plans,
                "needs_confirm": len(needs_confirm),
                "hint": ("含正片文件，需 confirm:true 二次确认"
                         if needs_confirm else "")}
    done = []
    for p in plans:
        if p.get("kind") == "dir":
            if p.get("status") != "planned_rmdir":
                done.append({**p, "status": p.get("status") or "dir_not_empty"})
                continue
            try:
                os.rmdir(os.path.join(settings.media_root, p["rel"]))
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
