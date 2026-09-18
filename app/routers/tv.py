"""TV 只读清单 API（F 阶段）：剧/季/集浏览（不刮削、不改名、不写 NFO）。

播放接口沿用 `/api/stream/{version_id}?kind=episode`（播放键已按 (kind,item_id) 隔离）。
"""
import os

from fastapi import APIRouter, HTTPException, Query

from .. import library_paths, store

router = APIRouter(prefix="/api/tv")


def _lib_id(library) -> int | None:
    libs = store._split_ints(library)
    return libs[0] if len(libs) == 1 else None


def _episode_payload(e: dict) -> dict:
    d = dict(e)
    try:
        d["exists"] = os.path.isfile(
            library_paths.resolve(e.get("library_id"), e.get("file_path") or ""))
    except Exception:
        d["exists"] = False
    return d


@router.get("/shows")
def list_shows(library: str | None = None, q: str = "",
               limit: int = 200, offset: int = 0):
    """剧集列表：library 缺省=全库；带集数/季数。"""
    lid = _lib_id(library)
    try:
        limit = max(1, min(int(limit or 200), 2000))
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        limit, offset = 200, 0
    items = store.list_shows(lid, q, limit, offset)
    total = store.count_shows(lid)
    return {"items": items, "total": total,
            "has_more": offset + len(items) < total,
            "limit": limit, "offset": offset}


@router.get("/shows/{show_id}")
def show_detail(show_id: int):
    """剧详情：含全部集（按季/集排序）与文件存在性。"""
    d = store.get_show(show_id)
    if not d:
        raise HTTPException(404, "show not found")
    d["episodes"] = [_episode_payload(e) for e in d["episodes"]]
    return d


@router.get("/episodes/{episode_id}")
def episode_detail(episode_id: int):
    e = store.get_episode(episode_id)
    if not e:
        raise HTTPException(404, "episode not found")
    out = _episode_payload(e)
    show = store.get_show(e["show_id"]) or {}
    out["show_title"] = show.get("title", "")
    out["show_year"] = show.get("year")
    return out


@router.get("/episodes/{episode_id}/blob")
def episode_blob(episode_id: int):
    """剧集文件原样直发（支持 Range；direct 播放/VLC/Kodi 直链用）。"""
    from fastapi.responses import FileResponse

    e = store.get_episode(episode_id)
    if not e:
        raise HTTPException(404, "episode not found")
    abs_p = library_paths.resolve(e.get("library_id") or library_paths.DEFAULT_LIBRARY_ID,
                                  e.get("file_path") or "")
    if not os.path.isfile(abs_p):
        raise HTTPException(410, "file missing")
    return FileResponse(abs_p, filename=os.path.basename(abs_p))


@router.get("/stats")
def tv_stats(library: str | None = None):
    lid = _lib_id(library)
    return {"shows": store.count_shows(lid), "episodes": store.count_episodes(lid)}
