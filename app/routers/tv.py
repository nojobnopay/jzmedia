"""TV 只读清单 API（F 阶段）：剧/季/集浏览（不刮削、不改名、不写 NFO）。

播放接口沿用 `/api/stream/{version_id}?kind=episode`（播放键已按 (kind,item_id) 隔离）。
"""
import os

from fastapi import APIRouter, HTTPException, Query, Request

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
def episode_blob(episode_id: int, request: Request):
    """剧集文件原样直发（本地 FileResponse / 远程 Range 流式；VLC/Kodi 直链用）。"""
    from .. import storage
    from .blob import media_response

    e = store.get_episode(episode_id)
    if not e:
        raise HTTPException(404, "episode not found")
    backend = storage.backend_for(
        e.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
    return media_response(request, backend, e.get("file_path") or "",
                          filename=os.path.basename(e.get("file_path") or ""))


@router.get("/stats")
def tv_stats(library: str | None = None):
    lid = _lib_id(library)
    return {"shows": store.count_shows(lid), "episodes": store.count_episodes(lid)}
