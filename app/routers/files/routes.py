"""routers.files.routes（自 app/routers/files.py 拆分，评审 B9/R09-Q1；经 files 门面使用）。"""
import os
import re
from fastapi import APIRouter, HTTPException
from ... import store
from ...config import settings
from ...scanner import sync_nfos_for
from ...log import get_logger
logger = get_logger("files.routes")
from .paths import _check_inside_root, _only_ids, _rename_or_move
from .planner import _collect_plans, _ordered_plans
from .executor import _cleanup_old_dir, _resync_old_dir, _move_one
__all__ = ['router', '_organize', 'organize', 'preview', 'unmatched', 'missing', 'clean', '_restore_candidates', '_restore_one', 'restore_candidates', 'restore_original', '_under_root', '_delete_rows']

router = APIRouter(prefix="/api/files")


def _under_root(path: str, root: str) -> bool:
    """路径是否在 root 子树内（含自身）。两侧 normpath 归一（评审 B11/R09-B7）：
    `./电影/x`、`电影//x` 等非规范写法不再漏判。"""
    p = os.path.normpath(path or "")
    r = os.path.normpath(root or "")
    return p == r or p.startswith(r.rstrip("/") + "/")


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
        all_rows = store.list_movies(grouped=False, limit=100000)
        if only:
            scoped_rows = [m for m in all_rows if m["id"] in only]
        else:
            # 全量收敛：from_prefix 子树 + 已在目标根下但尚未规范的行
            #（分区过期如改绑换产地、两级分区前的平铺残留）；目标已规范的行无计划
            scoped_rows = [m for m in all_rows
                           if _under_root(m["file_path"], fp)
                           or _under_root(m["file_path"], td)]
        scoped = {m["id"] for m in scoped_rows}
        plans, conflicts = _collect_plans(target_root=td,
                                          group_by_region=bool(group_by_region),
                                          only=scoped or {-1},
                                          all_rows=all_rows)
        base = {"dry_run": dry_run, "mode": mode, "from_prefix": fp,
                "to_dir": td, "group_by_region": bool(group_by_region)}
    if dry_run:
        return {**base, "plans": plans, "conflicts": conflicts}
    return {**base, "results": [_move_one(p) for p in _ordered_plans(plans)],
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


def _delete_rows(cands: list) -> dict:
    """执行删行（只删库，文件保留）：逐行上报结果与失败原因。clean 使用。"""
    done, failed = [], []
    for m in cands:
        try:
            ok = store.delete_movie(m["id"])
            done.append({"id": m["id"], "file_path": m["file_path"],
                         "status": "deleted" if ok else "already_gone"})
        except Exception as e:
            logger.warning("delete movie row failed id=%s path=%s: %s",
                           m.get("id"), m.get("file_path"), e)
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
    默认 dry_run 预览（评审 B5a-4/R09-D4）；
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
    return _delete_rows(cands)


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
        except Exception as e:
            logger.warning("update db after restore failed id=%s %s -> %s: %s",
                           m.get("id"), base.get("from"), base.get("to"), e)
            try:
                _rename_or_move(dst, src)
            except OSError as rb:
                logger.warning("restore rollback failed id=%s dst=%s: %s", m.get("id"), dst, rb)
            raise
        try:
            sync_nfos_for(m["id"], dst)
        except Exception as e:
            logger.debug("restore nfos sync failed id=%s dst=%s: %s", m.get("id"), dst, e)
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

