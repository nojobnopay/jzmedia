"""TV API：剧/季/集浏览（T1 扫描入库：规则阶梯解析/绝对集号/多集区间/特典 S0；
T2 起补 TMDB 刮削、匹配、已看状态与继续观看）。

播放接口沿用 `/api/stream/{version_id}?kind=episode`（播放键已按 (kind,item_id) 隔离）。
"""
import os
import threading

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import library_paths, storage, store, tmdb
from ..scanner import tv_persist
from ..scanner.parse import normalize_title

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


def _extra_payload(x: dict) -> dict:
    """花絮/剧场版行：文件存在性（同集） + 中文 kind 标签。"""
    d = _episode_payload(x)
    sub = str(x.get("kind") or "extra")
    d["label"] = store.EXTRA_LABELS.get(sub, "花絮")
    d["kind"] = sub
    return d


def _episode_payload(e: dict, progress: dict | None = None) -> dict:
    """集文件存在性：本地 POSIX / 远程直读 backend 统一（离线保守 True，§19）。
    可选附带断点（`episode_progress_map` 一次查出，避免逐集查询）。"""
    d = dict(e)
    rel = e.get("file_path") or ""
    d["version"] = store.episode_version(rel)      # 多版本分组（剧详情页/连播）
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
    if progress is not None:
        d["progress"] = progress
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
    """剧详情：季（含海报/名称）+ 全部集（存在性/断点/已看）+ 下一集。"""
    d = store.get_show(show_id)
    if not d:
        raise HTTPException(404, "show not found")
    progress = store.episode_progress_map(show_id)
    d["episodes"] = [_episode_payload(e, progress.get(int(e["id"])))
                     for e in d["episodes"]]
    d["watched_count"] = sum(1 for e in d["episodes"] if int(e.get("watched") or 0))
    d["review_count"] = sum(1 for e in d["episodes"] if int(e.get("needs_review") or 0))
    nxt = store.next_episode(show_id)
    d["next_episode"] = _episode_payload(nxt) if nxt else None
    d["extras"] = [_extra_payload(x) for x in store.list_extras_by_show(show_id)]
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


@router.get("/recent-played")
def recent_played(limit: int = 20, include_finished: bool = False,
                  library: str | None = None, media_library: int | None = None):
    """继续观看（剧集）：断点粒度、海报粒度去重（同集多版本留最近）。
    `include_finished=true` 含已看完（供「全部最近播放」）。"""
    libs = _lib_ids(library, media_library)
    if libs == []:
        return {"items": []}
    return {"items": store.list_recent_played_tv(limit, libs, include_finished)}


@router.get("/search")
def search_tv(q: str = "", year: int | None = None):
    """TMDB 剧集搜索（手动匹配用）。"""
    term = (q or "").strip()
    if not term:
        return {"items": []}
    try:
        rows = tmdb.search_tv(term, year)
    except Exception as e:
        raise HTTPException(502, f"TMDB 搜索失败：{str(e)[:200]}")
    items = []
    for r in (rows or [])[:20]:
        fd = str(r.get("first_air_date") or "")[:4]
        items.append({
            "tmdb_id": r.get("id"),
            "title": r.get("name") or "",
            "original_title": r.get("original_name") or "",
            "year": int(fd) if fd.isdigit() else None,
            "overview": r.get("overview") or "",
            "poster_path": r.get("poster_path") or "",
        })
    return {"items": items}


class MatchBody(BaseModel):
    tmdb_id: int


@router.post("/shows/{show_id}/match")
def match_show(show_id: int, body: MatchBody):
    """手动换绑 TMDB 剧集：拉详情+季详情并落库（source=manual，清待确认）。"""
    show = store.get_show_meta(show_id)
    if not show:
        raise HTTPException(404, "show not found")
    try:
        detail = tmdb.tv_detail(int(body.tmdb_id))
    except Exception as e:
        raise HTTPException(502, f"TMDB 详情失败：{str(e)[:200]}")
    local = store.list_episodes(show_id)
    season_details: dict = {}
    for sn in tv_persist.wanted_seasons(detail, local):
        try:
            season_details[sn] = tmdb.tv_season(int(body.tmdb_id), sn)
        except Exception:
            continue
    stats = tv_persist.apply_tv_detail(
        show_id, detail, season_details, source="manual", needs_review=0,
        library_id=show.get("library_id"))
    # NFO/海报落盘放后台：大剧（千集级）同步写会让浏览器请求超时
    threading.Thread(target=tv_persist.write_media_files,
                     args=(show_id, show.get("library_id")), daemon=True).start()
    return {"ok": True, **stats}


@router.post("/shows/{show_id}/refresh")
def refresh_show(show_id: int, force: bool = True):
    """重刮单剧（默认 force：即使已刮过也重写；force=false 走缓存短路）。"""
    show = store.get_show_meta(show_id)
    if not show:
        raise HTTPException(404, "show not found")
    try:
        return tv_persist.scrape_show(show, force=bool(force))
    except Exception as e:
        raise HTTPException(502, f"刮削失败：{str(e)[:200]}")


class TvPatchBody(BaseModel):
    title: str | None = None
    overview_override: str | None = None
    custom_rating: float | None = None
    tags: list[str] | None = None
    watched: bool | None = None


@router.patch("/shows/{show_id}")
def patch_show(show_id: int, body: TvPatchBody):
    """本地字段修改（allowlist，与电影 PATCH 同语义）：手工标题受保护（title_auto=0），
    此后刷新/重刮不覆盖；tags 归一去重（服务端 regions.normalize_tags）。"""
    show = store.get_show_meta(show_id)
    if not show:
        raise HTTPException(404, "show not found")
    fields: dict = {}
    if body.title is not None:
        title = " ".join((body.title or "").split())
        if not title:
            raise HTTPException(422, "title 不能为空")
        fields.update(title=title, sort_title=normalize_title(title), title_auto=0)
    if body.overview_override is not None:
        fields["overview_override"] = body.overview_override
    if body.custom_rating is not None:
        try:
            rating = float(body.custom_rating)
        except (TypeError, ValueError):
            raise HTTPException(422, "custom_rating must be number")
        if not (0 <= rating <= 10):
            raise HTTPException(422, "custom_rating must be 0-10")
        fields["custom_rating"] = rating
    if body.tags is not None:
        if not isinstance(body.tags, list):
            raise HTTPException(422, "tags must be list")
        from ..regions import normalize_tags
        fields["tags"] = normalize_tags(body.tags)
    if body.watched is not None:
        n = store.mark_show_watched(show_id, bool(body.watched))
        return {"ok": True, "episodes": n}
    if not fields:
        raise HTTPException(422, "nothing to update")
    store.update_show_meta(show_id, **fields)
    return {"ok": True}


@router.post("/shows/{show_id}/confirm-match")
def confirm_match(show_id: int):
    """确认匹配（仅清待确认，不重刮不触网；与电影 confirm-match 同语义）。"""
    if not store.get_show_meta(show_id):
        raise HTTPException(404, "show not found")
    store.update_show_meta(show_id, needs_review=0)
    return {"ok": True}


class WatchedBody(BaseModel):
    watched: bool = True


@router.post("/episodes/{episode_id}/watched")
def episode_watched(episode_id: int, body: WatchedBody | None = None):
    """标集已看/未看（标已看清断点，不再出现在继续观看）。"""
    if not store.get_episode(episode_id):
        raise HTTPException(404, "episode not found")
    watched = bool(body.watched) if body else True
    store.mark_episode_watched(episode_id, watched)
    return {"ok": True, "watched": watched}


@router.post("/shows/{show_id}/watched")
def show_watched(show_id: int, body: WatchedBody | None = None):
    """整剧标已看/未看（返回受影响集数）。"""
    if not store.get_show_meta(show_id):
        raise HTTPException(404, "show not found")
    watched = bool(body.watched) if body else True
    n = store.mark_show_watched(show_id, watched)
    return {"ok": True, "watched": watched, "episodes": n}


@router.get("/episodes/{episode_id}/next")
def episode_next(episode_id: int):
    """按播出顺序的下一集（播放器连播用）。"""
    nxt = store.episode_after(episode_id)
    if not nxt:
        return {"next": None}
    return {"next": {"id": int(nxt["id"]), "season": int(nxt["season"] or 0),
                     "episode": int(nxt["episode"] or 0),
                     "episode_end": int(nxt["episode_end"] or 0),
                     "version": store.episode_version(nxt.get("file_path")),
                     "title": nxt.get("title") or ""}}


@router.get("/extras/{extra_id}/blob")
def extra_blob(extra_id: int, request: Request):
    """花絮/剧场版原样直发（本地 FileResponse / 远程 Range）。"""
    from .blob import media_response
    x = store.get_extra(extra_id)
    if not x:
        raise HTTPException(404, "extra not found")
    backend = storage.backend_for(
        x.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
    return media_response(request, backend, x.get("file_path") or "",
                          filename=os.path.basename(x.get("file_path") or ""))


@router.get("/shows/{show_id}/tmdb-episodes")
def tmdb_episodes(show_id: int, season: int = 1):
    """手动指定集号用候选：按需拉 TMDB 某季集列表（不落库）。"""
    show = store.get_show_meta(show_id)
    if not show:
        raise HTTPException(404, "show not found")
    if not show.get("tmdb_id"):
        raise HTTPException(422, "剧未匹配 TMDB，先手动匹配剧集")
    try:
        data = tmdb.tv_season(int(show["tmdb_id"]), int(season))
    except Exception as e:
        raise HTTPException(502, f"TMDB 季详情失败：{str(e)[:200]}")
    items = []
    for ep in data.get("episodes") or []:
        try:
            num = int(ep.get("episode_number"))
        except (TypeError, ValueError):
            continue
        items.append({"tmdb_episode_id": ep.get("id"), "season": int(season),
                      "episode": num, "title": ep.get("name") or "",
                      "air_date": str(ep.get("air_date") or "")[:10],
                      "overview": (ep.get("overview") or "")[:200]})
    return {"season": int(season), "items": items}


class EpisodeMatchBody(BaseModel):
    tmdb_episode_id: int
    season: int | None = None   # 候选所在 TMDB 季（不给则用本地季）


@router.post("/episodes/{episode_id}/match-episode")
def match_episode(episode_id: int, body: EpisodeMatchBody):
    """手动指定 TMDB 集（本地季集号不变，只取元数据）：用于 TMDB 集号口径不一致。"""
    e = store.get_episode(episode_id)
    if not e:
        raise HTTPException(404, "episode not found")
    show = store.get_show_meta(e.get("show_id"))
    if not show or not show.get("tmdb_id"):
        raise HTTPException(422, "剧未匹配 TMDB，先手动匹配剧集")
    seasons = [int(body.season)] if body.season is not None else [int(e.get("season") or 0)]
    found = None
    for sn in seasons:
        try:
            data = tmdb.tv_season(int(show["tmdb_id"]), sn)
        except Exception:
            continue
        for ep in data.get("episodes") or []:
            if int(ep.get("id") or 0) == int(body.tmdb_episode_id):
                found = ep
                break
        if found:
            break
    if not found:
        raise HTTPException(404, "TMDB 未找到该集（换季或换集号再试）")
    try:
        runtime = int(found.get("runtime") or 0)
    except (TypeError, ValueError):
        runtime = 0
    store.update_episode_meta(
        episode_id,
        tmdb_episode_id=found.get("id"),
        title=(found.get("name") or "").strip() or e.get("title") or "",
        overview=found.get("overview") or "",
        still_path=found.get("still_path") or "",
        air_date=str(found.get("air_date") or "")[:10],
        runtime=runtime,
        tmdb_rating=found.get("vote_average"),
        needs_review=0,
        local_only=0,          # 重新绑定 TMDB 集 → 取消「本地确认集」
    )
    # 剧照换了 → 懒下载缓存失效（下次请求重拉）
    try:
        from ..db import POSTER_DIR
        from ..scanner.tv_persist import episode_still_name
        stale = os.path.join(POSTER_DIR, episode_still_name(int(episode_id)))
        if os.path.isfile(stale):
            os.remove(stale)
    except OSError:
        pass
    return {"ok": True, "episode_id": int(episode_id),
            "tmdb_episode_id": found.get("id"), "title": found.get("name") or ""}


class EpisodeLocalBody(BaseModel):
    title: str | None = None      # 可选：本地集名（默认保留现有标题）
    overview: str | None = None


@router.post("/episodes/{episode_id}/confirm-local")
def confirm_local(episode_id: int, body: EpisodeLocalBody | None = None):
    """确认「TMDB 无对应集」：清 needs_review 并标 local_only（重刮不覆盖、不再标待确认）。

    用于 TMDB 未收录的本地集（如《马达加斯加》第 4 部《爱登堡和巨蛋》）。"""
    e = store.get_episode(episode_id)
    if not e:
        raise HTTPException(404, "episode not found")
    # 确认为本地集 → 同时清掉可能错误的 TMDB 绑定（如文件此前按编号错绑）
    fields: dict = {"needs_review": 0, "local_only": 1, "tmdb_episode_id": None}
    body = body or EpisodeLocalBody()
    if body.title is not None and str(body.title).strip():
        fields["title"] = str(body.title).strip()
    if body.overview is not None:
        fields["overview"] = str(body.overview)
    store.update_episode_meta(episode_id, **fields)
    return {"ok": True, "episode_id": int(episode_id), "local_only": 1,
            "title": fields.get("title", e.get("title") or "")}


@router.get("/episodes/{episode_id}/still")
def episode_still(episode_id: int):
    """集剧照（懒下载 TMDB w300 到 data/posters，浏览器无需可达 TMDB 图片域名）。"""
    if not store.get_episode(episode_id):
        raise HTTPException(404, "episode not found")
    path = tv_persist.ensure_episode_still(episode_id)
    if not path:
        raise HTTPException(404, "no still")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})
