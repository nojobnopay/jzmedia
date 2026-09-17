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
import shutil
import threading

from fastapi import APIRouter, HTTPException

from .. import scanner, store
from ..config import settings
from ..jobkit import JobRegistry
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
    raw_name = str(body.get("name") or "")
    name = _safe_component(raw_name)
    # 检查原始输入（sanitize 后 "/" 必不存在，评审 R01-B4）：防静默改写 a/b → ab
    if not name or "/" in raw_name or "\\" in raw_name:
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


# ===== 复制（评审 B9 后续）：目录递归 + 冲突自动副本命名 + 后台 job 进度/取消 =====

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


def _measure(items: list[str]) -> tuple[int, int, int]:
    """(总字节, 文件数, 目录数)；符号链接按 0 字节且不跟随。"""
    total = files = dirs = 0
    for rel in items:
        abs_p = os.path.join(settings.media_root, rel)
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


def _copy_worker(jid: str, items: list[str], to_dir: str, hints: dict) -> None:
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
            src_abs = os.path.join(settings.media_root, rel)
            base = os.path.basename(rel.rstrip("/"))
            dst_abs, was_renamed = _unique_dst(
                os.path.join(settings.media_root, to_dir, base) if to_dir
                else os.path.join(settings.media_root, base))
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
                    rel_dst = os.path.relpath(dst_abs, settings.media_root)
                    if is_feature_video(rel_dst):
                        # 正片复制 → 登记（源已匹配则直绑 tmdb，避免重搜/误配）
                        hint = hints.get(rel)
                        try:
                            r = scanner.scan_one(dst_abs, tmdb_hint=hint)
                            item_status = str(r.get("status") or "copied")
                            if r.get("status") in ("ok", "ok_needs_review"):
                                registered += 1
                        except Exception as e:
                            logger.warning("register copied feature failed rel=%s: %s", rel_dst, e)
                            item_status = f"copied_scan_warn: {e}"
                except OSError as e:
                    item_status = f"error: {e}"
            results.append({"from": rel, "to": os.path.relpath(dst_abs, settings.media_root),
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
    # 目标目录可不存在（粘贴到新目录），空串=根；_check_inside_root 拒绝空串，故单独处理
    raw_to = str(body.get("to_dir") or "").strip().strip("/")
    to_dir = _check_inside_root(raw_to) if raw_to else ""
    dst_root = os.path.join(settings.media_root, to_dir) if to_dir else settings.media_root
    if os.path.exists(dst_root) and not os.path.isdir(dst_root):
        raise HTTPException(422, f"target is not a directory: {to_dir!r}")
    items: list[str] = []
    hints: dict = {}
    conflicts: list[str] = []
    for r in raws:
        rel = _check_inside_root(str(r or ""))
        abs_p = os.path.join(settings.media_root, rel)
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
        info = _classify(rel)
        if info.get("kind") == "feature" and info.get("tmdb_id"):
            hints[rel] = int(info["tmdb_id"])
        items.append(rel)
    total_bytes, files, dirs = _measure(items)
    if body.get("dry_run", True):
        needs_confirm = (total_bytes >= COPY_CONFIRM_BYTES
                         or (files + dirs) >= COPY_CONFIRM_ITEMS)
        return {"dry_run": True, "total": len(items), "files": files, "dirs": dirs,
                "bytes": total_bytes, "conflicts": conflicts[:50],
                "needs_confirm": needs_confirm,
                "hint": (f"约 {files} 个文件 / {round(total_bytes / 1024 / 1024 / 1024, 2)} GB"
                         + ("，同名的会自动改「(副本)」" if conflicts else ""))}
    job = _COPY_JOBS.create(total=len(items), bytes_total=total_bytes)
    jid = job["job_id"]
    threading.Thread(target=_copy_worker, args=(jid, items, to_dir, hints),
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
