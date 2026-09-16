"""routers.files.routes（自 app/routers/files.py 拆分，评审 B9/R09-Q1；经 files 门面使用）。"""
import os
import re
from fastapi import APIRouter, HTTPException
from ... import store
from ...config import settings
from ...scanner import is_sidecar
from ...scanner import sync_nfos_for
from ...log import get_logger
logger = get_logger("files.routes")
from .paths import _check_inside_root, _only_ids
from .planner import _collect_plans
from .executor import _cleanup_old_dir, _resync_old_dir
__all__ = ['router', '_organize', 'organize', 'preview', 'unmatched', 'clean_sidecars', 'clean_episodes', 'missing', 'clean', '_restore_candidates', '_restore_one', 'restore_candidates', 'restore_original']

router = APIRouter(prefix="/api/files")


def _organize(mode: str, from_prefix: str | None = None,
              to_dir: str | None = None, group_by_region: bool = True,
              only: set | None = None, dry_run: bool = True) -> dict:
    """统一整理入口：inplace=就地归档（target_root=None），relocate=顶层搬迁。"""
    if mode not in ("inplace", "relocate"):
        raise HTTPException(422, "mode must be inplace|relocate")
    if mode == "inplace":
        plans, conflicts = _collect_plans(only=only)
        base: dict = {"dry_run": dry_run, "mode": mode}
    else:
        fp = _check_inside_root(str(from_prefix or "待整理"))
        td = _check_inside_root(str(to_dir or "电影"))
        if os.path.normpath(fp) == os.path.normpath(td):
            raise HTTPException(422, "from_prefix == to_dir, nothing to do")
        rows = store.list_movies(grouped=False, limit=100000)
        if only:
            rows = [m for m in rows if m["id"] in only]
        else:
            # 全量收敛：from_prefix 子树 + 已在目标根下但尚未规范的行
            #（分区过期如改绑换产地、两级分区前的平铺残留）；目标已规范的行无计划
            def _under(m, root: str) -> bool:
                return (os.path.normpath(m["file_path"]) == root
                        or m["file_path"].startswith(root.rstrip("/") + "/"))
            rows = [m for m in rows if _under(m, fp) or _under(m, td)]
        scoped = {m["id"] for m in rows}
        plans, conflicts = _collect_plans(target_root=td,
                                          group_by_region=bool(group_by_region),
                                          only=scoped or {-1})
        base = {"dry_run": dry_run, "mode": mode, "from_prefix": fp,
                "to_dir": td, "group_by_region": bool(group_by_region)}
    if dry_run:
        return {**base, "plans": plans, "conflicts": conflicts}
    return {**base, "results": [_move_one(p) for p in plans],
            "conflicts": conflicts}


@router.post("/organize")
def organize(body: dict | None = None):
    """统一整理口：mode=inplace 就地归档；mode=relocate 顶层搬迁
    （from_prefix→to_dir[/region]/标题 (年份)/文件）。dry_run 默认 true 只预览。"""
    body = body or {}
    return _organize(mode=str(body.get("mode") or "inplace"),
                     from_prefix=body.get("from_prefix"),
                     to_dir=body.get("to_dir"),
                     group_by_region=(True if body.get("group_by_region") is None
                                      else bool(body.get("group_by_region"))),
                     only=_only_ids(body),
                     dry_run=body.get("dry_run", True))


@router.get("/preview")
def preview():
    return _organize("inplace", dry_run=True)


@router.get("/unmatched")
def unmatched():
    """待处理明细（只读，供设置页展示+跳转处理）：
    unmatched=TMDB 无命中；needs_review=模糊命中待确认；
    suspect_title=标题无 CJK（high=非英语片，疑似错配/缺译；info=英语片，多为正常/陈旧）；
    orphan_extras=未归属花絮（文件原地保留）。"""
    import re as _re
    un, nr, high, info, orphans = [], [], [], [], []
    for m in store.list_movies(grouped=False, limit=100000):
        item = {"id": m["id"], "title": m.get("title", ""),
                "original_title": m.get("original_title", ""),
                "year": m.get("year"), "file_path": m["file_path"],
                "tmdb_id": m.get("tmdb_id"),
                "original_language": m.get("original_language", "")}
        if not m.get("tmdb_id"):
            un.append(item)
        else:
            if m.get("needs_review"):
                item["needs_review"] = 1
                nr.append(item)
            title = m.get("title") or ""
            if (title and _re.search(r"[A-Za-z]", title)
                    and not _re.search(r"[\u4e00-\u9fff]", title)):
                (high if (m.get("original_language") or "")
                 not in ("en", "") else info).append(item)
    un.sort(key=lambda x: x["file_path"])
    for e in store.list_orphan_extras():
        orphans.append({"id": e["id"], "file_path": e["file_path"],
                        "kind": e.get("kind") or "extra"})
    return {"unmatched": un, "needs_review": nr,
            "suspect_title_high": high, "suspect_title_info": info,
            "orphan_extras": orphans,
            "total_unmatched": len(un), "total_needs_review": len(nr),
            "total_suspect_high": len(high), "total_suspect_info": len(info),
            "total_orphan_extras": len(orphans)}


@router.post("/clean-sidecars")
def clean_sidecars(body: dict | None = None):
    """清理历史脏行：file_path 命中现行花絮/样片规则的 movies 行，删行+关联+FTS。
    视频文件原地保留（下次扫描按花絮归属），海报与 tmdb_cache 保留。默认 dry_run 预览。"""
    from ...scanner import is_sidecar
    body = body or {}
    dry_run = body.get("dry_run", True)
    only = _only_ids(body)
    # 已匹配行不清理（评审 P1-05）：花絮规则演进可能把真实影片判成花絮/样片，
    # 有 tmdb_id 的行必是扫描/人工确认过的电影，宁可漏清也不能误删。
    cands = [m for m in store.list_movies(grouped=False, limit=100000)
             if (only is None or m["id"] in only) and not m.get("tmdb_id")
             and is_sidecar(m["file_path"])]
    plans = [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year"),
              "file_path": m["file_path"], "tmdb_id": m.get("tmdb_id")}
             for m in cands]
    if dry_run:
        return {"dry_run": True, "total": len(plans), "plans": plans}
    done, failed = [], []
    for m in cands:
        try:
            ok = store.delete_movie(m["id"])
            done.append({"id": m["id"], "file_path": m["file_path"],
                         "status": "deleted" if ok else "already_gone"})
        except Exception as e:
            failed.append({"id": m["id"], "file_path": m["file_path"],
                           "error": str(e)})
    return {"dry_run": False, "total": len(cands), "deleted": len(done),
            "failed": failed, "results": done}


@router.post("/clean-episodes")
def clean_episodes(body: dict | None = None):
    """清理历史剧集脏行（评审 P1-03 配套）：未匹配（tmdb_id 为空）且文件名解析为剧集的
    movies 行，删行+关联+FTS；视频文件原地保留，海报与 tmdb_cache 保留。
    已匹配行不清理（可能是人工改判为电影）。默认 dry_run 预览。"""
    from ...scanner import parse_filename
    body = body or {}
    dry_run = body.get("dry_run", True)
    only = _only_ids(body)
    cands = []
    for m in store.list_movies(grouped=False, limit=100000):
        if only is not None and m["id"] not in only:
            continue
        if m.get("tmdb_id"):
            continue
        try:
            parsed = parse_filename(os.path.basename(m["file_path"]))
        except Exception:
            continue
        if parsed.get("type") == "episode":
            cands.append(m)
    plans = [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year"),
              "file_path": m["file_path"]} for m in cands]
    if dry_run:
        return {"dry_run": True, "total": len(plans), "plans": plans}
    done, failed = [], []
    for m in cands:
        try:
            ok = store.delete_movie(m["id"])
            done.append({"id": m["id"], "file_path": m["file_path"],
                         "status": "deleted" if ok else "already_gone"})
        except Exception as e:
            failed.append({"id": m["id"], "file_path": m["file_path"],
                           "error": str(e)})
    return {"dry_run": False, "total": len(cands), "deleted": len(done),
            "failed": failed, "results": done}


@router.get("/missing")
def missing():
    """预览失效条目：库中有记录但文件已不存在的行（软件外删片/移动后产生）。"""
    out = []
    for m in store.list_movies(grouped=False, limit=100000):
        if not os.path.exists(os.path.join(settings.media_root, m["file_path"])):
            out.append({"id": m["id"], "title": m.get("title", ""),
                        "year": m.get("year"), "file_path": m["file_path"],
                        "tmdb_id": m.get("tmdb_id")})
    return {"total": len(out), "items": out}


@router.post("/clean")
def clean(body: dict | None = None):
    """清理失效条目：彻底删除DB行+演职员关联+FTS（海报与tmdb_cache保留供重扫复用）。
    默认 dry_run 预览（评审 B5a-4/R09-D4：与 clean-sidecars 等保持一致）；
    body.ids 不传则清理全部缺失行；建议先 GET /missing 预览勾选。"""
    body = body or {}
    dry_run = body.get("dry_run", True)
    only_set = _only_ids(body)
    cands = [m for m in store.list_movies(grouped=False, limit=100000)
             if (only_set is None or m["id"] in only_set)
             and not os.path.exists(os.path.join(settings.media_root, m["file_path"]))]
    plans = [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year"),
              "file_path": m["file_path"],
              "tmdb_id": m.get("tmdb_id")} for m in cands]
    if dry_run:
        return {"dry_run": True, "total": len(plans), "plans": plans}
    done, failed = [], []
    for m in cands:
        try:
            ok = store.delete_movie(m["id"])
            done.append({"id": m["id"], "file_path": m["file_path"],
                         "status": "deleted" if ok else "already_gone"})
        except Exception as e:
            failed.append({"id": m["id"], "file_path": m["file_path"],
                           "error": str(e)})
    return {"dry_run": False, "total": len(cands), "deleted": len(done),
            "failed": failed, "results": done}


def _restore_candidates(only: set | None) -> list[dict]:
    """偏离原始位置的行：有原始路径、与当前位置不一致、当前文件仍存在。"""
    out = []
    for m in store.list_movies(grouped=False, limit=100000):
        if only is not None and m.get("id") not in only:
            continue
        orig = (m.get("original_file_path") or "").strip()
        cur = m.get("file_path", "")
        if not orig or os.path.normpath(orig) == os.path.normpath(cur):
            continue
        if not os.path.isfile(os.path.join(settings.media_root, cur)):
            continue
        out.append(m)
    out.sort(key=lambda m: (m.get("file_path", ""), m.get("id", 0)))
    return out


def _restore_one(m: dict, dry_run: bool) -> dict:
    """单行恢复到原始路径。dry_run 只规划；执行时复用 NFO 收敛+旧目录清理。"""
    base = {"id": m["id"], "title": m.get("title", ""),
            "from": m["file_path"], "to": m.get("original_file_path") or ""}
    # 目标边界守卫（评审 B6/R09-B4）：to 来自库内历史值，理论上合法；
    # 防御性再校验一次，防止库被外部工具改坏后恢复到 MEDIA_ROOT 之外
    try:
        base["to"] = _check_inside_root(base["to"])
    except HTTPException as e:
        return {**base, "status": f"error: illegal target ({e.detail})"}
    src = os.path.join(settings.media_root, base["from"])
    dst = os.path.join(settings.media_root, base["to"])
    if not os.path.isfile(src):
        return {**base, "status": "skipped_missing_src"}
    if os.path.exists(dst):
        return {**base, "status": "conflict_disk_exists"}
    # 库内占用保护（评审 P1-06）：同 _move_one，目标挂在别的库行上时先拒绝
    existing = store.get_by_path(base["to"]) if base["to"] else None
    if existing is not None and int(existing.get("id") or -1) != int(m["id"]):
        return {**base, "status": "conflict_db_occupied"}
    if dry_run:
        return {**base, "status": "planned"}
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        old_dir = os.path.dirname(src)
        _rename_or_move(src, dst)
        # 本地写：只改 file_path，不碰 TMDB 镜像列与 original_file_path
        try:
            store.update_movie_local(m["id"], file_path=base["to"])
        except Exception:
            try:
                _rename_or_move(dst, src)
            except OSError:
                pass
            raise
        try:
            sync_nfos_for(m["id"], dst)
        except Exception:
            pass
        _cleanup_old_dir(old_dir)
        if os.path.normpath(old_dir) != os.path.normpath(os.path.dirname(dst)):
            _resync_old_dir(old_dir)
        return {**base, "status": "restored"}
    except Exception as e:
        logger.warning("restore failed id=%s %s -> %s: %s", m.get("id"),
                       base.get("from"), base.get("to"), e)
        return {**base, "status": f"error: {e}"}


@router.get("/restore-candidates")
def restore_candidates():
    """预览偏离原始位置的行（只读，供设置页恢复区展示）。"""
    cands = _restore_candidates(None)
    return {"total": len(cands),
            "items": [{"id": m["id"], "title": m.get("title", ""),
                       "year": m.get("year"),
                       "file_path": m["file_path"],
                       "original_file_path": m.get("original_file_path") or ""}
                      for m in cands]}


@router.post("/restore-original")
def restore_original(body: dict | None = None):
    """恢复到原始位置：把整理/搬迁后偏离原始路径的影片搬回 original_file_path。
    默认 dry_run:true 只预览；确认后 dry_run:false 执行。目标被占/源缺失则跳过上报，绝不覆盖。"""
    body = body or {}
    only = _only_ids(body)
    dry_run = body.get("dry_run", True)
    if dry_run:
        plans = [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year"),
                  "from": m["file_path"], "to": m.get("original_file_path") or "",
                  "status": _restore_one(m, dry_run=True)["status"]}
                 for m in _restore_candidates(only)]
        return {"dry_run": True, "total": len(plans), "plans": plans}
    results = [_restore_one(m, dry_run=False) for m in _restore_candidates(only)]
    ok = sum(1 for r in results if r["status"] == "restored")
    return {"dry_run": False, "total": len(results),
            "restored": ok, "results": results}

