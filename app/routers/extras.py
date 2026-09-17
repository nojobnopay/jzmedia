"""花絮归属：orphan 列表 + 手工认领。"""
import os

from fastapi import APIRouter, HTTPException

from .. import store
from ..config import settings
from ..log import get_logger

router = APIRouter(prefix="/api/extras")
logger = get_logger("extras")


@router.get("/orphans")
def orphans():
    """未归属花絮：标题/年份对不上库内任何影片，文件原地保留，人工认领。"""
    items = []
    for e in store.list_orphan_extras():
        items.append({"id": e["id"], "file_path": e["file_path"],
                      "kind": e.get("kind") or "extra"})
    return {"total": len(items), "items": items}


@router.post("/{extra_id}/attach")
def attach(extra_id: int, body: dict):
    """手工认领：把 orphan 花絮归到指定影片下（下次整理跟随搬迁）。
    已归属花絮改挂需显式 force:true（评审 B6/R08-D5），响应回带 previous_movie_id。"""
    body = body or {}
    movie_id = body.get("movie_id")
    if not movie_id:
        raise HTTPException(422, "movie_id required")
    try:
        movie_id = int(movie_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "movie_id must be int")
    if not (0 < movie_id <= 2 ** 63 - 1):
        raise HTTPException(422, "movie_id out of range")
    if not store.get_movie(movie_id):
        raise HTTPException(404, "movie not found")
    e = store.get_extra(extra_id)
    if not e:
        raise HTTPException(404, "extra not found")
    prev = e.get("movie_id")
    if prev and not bool(body.get("force")):
        raise HTTPException(409, "extra already attached; pass force:true to re-attach")
    if not store.update_extra_movie(extra_id, movie_id):
        raise HTTPException(404, "extra not found")
    return {"id": extra_id, "movie_id": movie_id, "previous_movie_id": prev}


@router.post("/collect")
def collect(body: dict | None = None):
    """归位已归属花絮：影片已归档但花絮散落在外的（如 待整理/），搬进各片 extras/。
    dry_run 默认 true 只预览。"""
    from .files import _only_ids, move_attached_extras
    body = body or {}
    dry_run = body.get("dry_run", True)
    only = _only_ids(body)
    # 一次取全量 extras 再按影片分组（评审 B8/R08-D2：不再每片一次查询）
    try:
        by_movie: dict = {}
        for e in store.list_all_extras():
            if e.get("movie_id"):
                by_movie.setdefault(e["movie_id"], []).append(e)
    except Exception as e:
        logger.warning("list extras failed: %s", e)
        by_movie = {}
    cands = []
    for m in store.list_movies(grouped=False, limit=100000):
        if only is not None and m["id"] not in only:
            continue
        rows = by_movie.get(m["id"], [])
        pending = [e for e in rows if os.path.dirname(e["file_path"])
                   != os.path.join(os.path.dirname(m["file_path"]), "extras")]
        if pending:
            cands.append({"id": m["id"], "title": m.get("title", ""),
                          "dir": os.path.dirname(m["file_path"]),
                          "extras": [e["file_path"] for e in pending]})
    if dry_run:
        return {"dry_run": True, "total": len(cands), "plans": cands}
    done = []
    for c in cands:
        try:
            n = move_attached_extras(
                c["id"], os.path.join(settings.media_root, c["dir"]))
            done.append({"id": c["id"], "moved": n})
        except Exception as e:
            done.append({"id": c["id"], "moved": 0, "error": str(e)})
    return {"dry_run": False, "total": len(cands),
            "moved": sum(d.get("moved", 0) for d in done), "results": done}
