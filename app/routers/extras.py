"""花絮归属：orphan 列表 + 手工认领。"""
import os

from fastapi import APIRouter, HTTPException

from .. import library_paths, store
from ..log import get_logger

router = APIRouter(prefix="/api/extras")
logger = get_logger("extras")


@router.get("/orphans")
def orphans(library: str | None = None):
    """未归属花絮：标题/年份对不上库内任何影片，文件原地保留，人工认领。
    附文件名解析的猜测标题/年份（评审 R08-D4），认领时便于在库里搜索确认。
    library 缺省=全库。"""
    from ..scanner.parse import parse_filename, normalize_title
    libs = store._split_ints(library)
    items = []
    for e in store.list_orphan_extras():
        if libs and int(e.get("library_id") or 0) not in libs:
            continue
        rel = e["file_path"]
        guess = {}
        try:
            parsed = parse_filename(os.path.basename(rel))
            t = normalize_title(parsed.get("title") or "")
            if t:
                guess["guessed_title"] = t
            if parsed.get("year"):
                guess["guessed_year"] = parsed["year"]
        except Exception as ex:
            logger.debug("guess orphan title failed id=%s path=%s: %s", e.get("id"), rel, ex)
        items.append({"id": e["id"], "file_path": rel,
                      "kind": e.get("kind") or "extra", **guess})
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
    dry_run 默认 true 只预览。body.library_id/library 可限定库（缺省=全库）。"""
    from .files import _only_ids, move_attached_extras
    body = body or {}
    dry_run = body.get("dry_run", True)
    only = _only_ids(body)
    libs = store._split_ints(body.get("library_id", body.get("library")))
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
        if libs and int(m.get("library_id") or 0) not in libs:
            continue
        rows = by_movie.get(m["id"], [])
        pending = [e for e in rows if os.path.dirname(e["file_path"])
                   != os.path.join(os.path.dirname(m["file_path"]), "extras")]
        if pending:
            cands.append({"id": m["id"], "title": m.get("title", ""),
                          "dir": os.path.dirname(m["file_path"]),
                          "library_id": m.get("library_id")
                          or library_paths.DEFAULT_LIBRARY_ID,
                          "extras": [e["file_path"] for e in pending]})
    if dry_run:
        return {"dry_run": True, "total": len(cands), "plans": cands}
    done = []
    skipped: list[dict] = []
    for c in cands:
        from .files import _require_writable
        from .. import storage
        lib_id = c.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
        _require_writable(lib_id)
        try:
            backend = storage.backend_for(lib_id)
            if backend.abs_path(c["dir"] or "") is None:
                # 远程直读库：整条链路走 backend（无挂载依赖）
                r = move_attached_extras(c["id"], "", backend=backend,
                                         movie_dir_rel=c["dir"] or "")
            else:
                r = move_attached_extras(c["id"], library_paths.resolve(lib_id, c["dir"]))
            done.append({"id": c["id"], "moved": r.get("moved", 0),
                         "skipped": len(r.get("skipped") or [])})
            for sk in (r.get("skipped") or []):
                skipped.append({"id": c["id"], **sk})
        except Exception as e:
            done.append({"id": c["id"], "moved": 0, "error": str(e)})
    # skipped 多为目标已存在（不覆盖）；带明细回报，避免用户反复点整理（评审 R08-D3）
    return {"dry_run": False, "total": len(cands),
            "moved": sum(d.get("moved", 0) for d in done),
            "skipped": skipped, "results": done}
