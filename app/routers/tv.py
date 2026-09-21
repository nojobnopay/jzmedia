"""TV 只读清单 API（F 阶段）：剧/季/集浏览（不刮削、不改名、不写 NFO）。

播放接口沿用 `/api/stream/{version_id}?kind=episode`（播放键已按 (kind,item_id) 隔离）。
"""
import os

from fastapi import APIRouter, HTTPException, Query, Request

from .. import library_paths, storage, store

router = APIRouter(prefix="/api/tv")


def _lib_ids(library, media_library) -> list | None:
    """范围解析（v18 媒体库聚合）：media_library 优先 → 其全部视频库 id；
    library（视频库，可逗号）→ 指定 id 列表；都缺省=None（全库）。
    媒体库不存在/无视频库时返回 []（明确空结果，绝不退化成全库）。"""
    if media_library is not None and str(media_library).strip() != "":
        try:
            mid = int(media_library)
        except (TypeError, ValueError):
            return []
        return store.library_ids_for_media(mid)
    return store._split_ints(library) or None


def _episode_payload(e: dict) -> dict:
    """集文件存在性：本地 POSIX / 远程直读 backend 统一（离线保守 True，§19）。"""
    d = dict(e)
    rel = e.get("file_path") or ""
    lib_id = e.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    try:
        backend = storage.backend_for(lib_id)
        local = backend.abs_path(rel)
        d["exists"] = (os.path.isfile(local) if local is not None
                       else bool(backend.exists(rel)))
    except storage.StorageOffline:
        d["exists"] = True
    except Exception:
        d["exists"] = False
    return d


@router.get("/shows")
def list_shows(library: str | None = None, media_library: int | None = None,
               q: str = "", limit: int = 200, offset: int = 0):
    """剧集列表：media_library=整个媒体库（其全部剧集类视频库并集）；
    library=单个/多个视频库；都缺省=全库。带集数/季数。"""
    libs = _lib_ids(library, media_library)
    try:
        limit = max(1, min(int(limit or 200), 2000))
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        limit, offset = 200, 0
    items = store.list_shows(libs, q, limit, offset)
    total = store.count_shows(libs)
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
def tv_stats(library: str | None = None, media_library: int | None = None):
    libs = _lib_ids(library, media_library)
    return {"shows": store.count_shows(libs), "episodes": store.count_episodes(libs)}
