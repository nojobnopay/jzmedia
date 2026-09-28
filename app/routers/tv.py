"""TV API：剧/季/集浏览（T1 扫描入库：规则阶梯解析/绝对集号/多集区间/特典 S0；
T2 起补 TMDB 刮削、匹配、已看状态与继续观看）。

播放接口沿用 `/api/stream/{version_id}?kind=episode`（播放键已按 (kind,item_id) 隔离）。
"""
import hashlib
import json
import os
import re
import threading
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config, library_paths, scanner, storage, store, tmdb
from ..log import get_logger
from ..scanner import tv_match, tv_persist
from ..scanner.parse import normalize_title

logger = get_logger("tv")

router = APIRouter(prefix="/api/tv")

# 本地优先快照（大集数剧集提速）：(library_id, show_root) → (fingerprint, expires)。
# fingerprint 为剧根目录一次 list 的轻量摘要；TTL 默认 600s（季分页 limit 默认 100）。
_SNAP_TTL = 600.0
_SNAP: dict[tuple, tuple] = {}
_SNAP_LOCK = threading.Lock()


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


def _local_exists(e: dict) -> bool:
    """本地优先存在性：只信 DB `missing` 列，零远程 I/O。

    显示即对、手动刷新：浏览态默认走这里；播放/用户点校验时才触网核验。
    离线/错误一律保守 True 的旧语义由核验路径保持（§19）。"""
    return not int(e.get("missing") or 0)


def _episode_payload(e: dict, progress: dict | None = None,
                     exists: bool | None = None) -> dict:
    """集行载荷：默认本地优先（`missing` 列），零远程 I/O。

    `exists` 显式传入时直接采用（批量核验结果）；不传即本地态。
    远程核验走 `_verify_exists_map`（按父目录分组一次 list，N stat → D list）。
    可选附带断点（`episode_progress_map` 一次查出，避免逐集查询）。"""
    d = dict(e)
    rel = e.get("file_path") or ""
    d["version"] = store.episode_version(rel)      # 多版本分组（剧详情页/连播）
    d["exists"] = bool(exists) if exists is not None else _local_exists(e)
    if progress is not None:
        d["progress"] = progress
    return d


def _extra_payload(x: dict, exists: bool | None = None) -> dict:
    """花絮/剧场版行：文件存在性（同集，默认本地优先） + 中文 kind 标签。"""
    d = _episode_payload(x, exists=exists)
    sub = str(x.get("kind") or "extra")
    d["label"] = store.EXTRA_LABELS.get(sub, "花絮")
    d["kind"] = sub
    return d


def _verify_exists_map(rows: list[dict]) -> dict[str, bool]:
    """批量远程核验：按 (library_id, 父目录) 分组，一次 backend.list 建集合。

    语义与 files.paths._exists_map 对齐：StorageNotFound → False；
    离线/其它存储错误保守 True（§19，绝不把离线当删除）。
    本地后端走 os.path.isfile（syscall 便宜）。"""
    from .files.paths import _exists_map
    out: dict[str, bool] = {}
    groups: dict[int, list[str]] = {}
    for r in rows or ():
        rel = str(r.get("file_path") or "")
        if not rel:
            continue
        lid = int(r.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        groups.setdefault(lid, []).append(rel)
    for lid, rels in groups.items():
        try:
            out.update(_exists_map(lid, rels))
        except Exception as exc:
            logger.warning("tv batch verify failed lib=%s n=%d: %s", lid, len(rels), exc)
            for rel in rels:
                out.setdefault(rel, True)
    return out


def _show_root_of(rel: str) -> str:
    """剧根目录（快照键）：相对路径首段，如 Show/Season 01/x.mkv → Show。"""
    seg = str(rel or "").split("/", 1)[0].strip()
    return seg or ""


def _snapshot_fingerprint(backend, root: str):
    """剧根一次 list 的轻量指纹：(条目数, 名称hash, 最大mtime)，失败返回 None。"""
    try:
        entries = backend.list(root)
    except Exception:
        return None
    names = sorted(str(e.get("name") or "") for e in (entries or []))
    mtimes = [float(e.get("mtime") or 0) for e in (entries or [])]
    return (len(names), hash(tuple(names)),
            max(mtimes) if mtimes else 0.0)


def _snapshot_hit(lib_id: int, root: str, backend) -> bool | None:
    """快照比对：True=无变化（可信本地态）；False=有变化；None=无法判断。

    命中且未过期直接返回缓存指纹比对；未命中/过期则 list 一次并刷新快照。
    任何存储错误返回 None（调用方保守按本地态 + stale=True 返回）。"""
    if not root:
        return None
    key = (int(lib_id), root)
    now = time.monotonic()
    try:
        with _SNAP_LOCK:
            hit = _SNAP.get(key)
            if hit is not None and hit[1] > now:
                cached_fp = hit[0]
            else:
                cached_fp = None
        fp = _snapshot_fingerprint(backend, root)
        if fp is None:
            return None
        with _SNAP_LOCK:
            _SNAP[key] = (fp, now + _SNAP_TTL)
            if len(_SNAP) > 2000:  # 防无界增长：清过期，仍满则整体清空
                for k in [k for k, (_f, exp) in _SNAP.items() if exp <= now]:
                    _SNAP.pop(k, None)
                if len(_SNAP) > 2000:
                    _SNAP.clear()
        if cached_fp is None:
            return None  # 首次建快照：无法断定无变化，按需核验由调用方决定
        return bool(cached_fp == fp)
    except storage.StorageOffline:
        return None
    except Exception as exc:
        logger.debug("tv snapshot failed lib=%s root=%s: %s", lib_id, root, exc)
        return None


def _backend_for(lib_id: int):
    """取后端；不可用返回 None（调用方保守按本地态 + stale 返回）。"""
    try:
        return storage.backend_for(lib_id)
    except storage.StorageError as exc:
        logger.debug("tv backend unavailable lib=%s: %s", lib_id, exc)
        return None


@router.get("/shows")
def list_shows(library: str | None = None, media_library: int | None = None,
               q: str = "", limit: int = 200, offset: int = 0,
               genre: list[str] | None = Query(default=None),
               region: list[str] | None = Query(default=None),
               country: list[str] | None = Query(default=None),
               year: list[str] | None = Query(default=None),
               decade: list[str] | None = Query(default=None),
               tag: list[str] | None = Query(default=None),
               min_rating: float | None = None,
               rating_source: str = "tmdb",
               watched: int | None = None,
               pending: int | None = None,
               status: list[str] | None = Query(default=None),
               sort: str | None = None, order: str | None = None):
    """剧集列表：media_library=整个媒体库（其全部剧集类视频库并集）；
    library=单个/多个视频库；都缺省=全库。带集数/季数。

    过滤语义与电影墙一致（facet 内 OR、跨维度 AND、tags 多选 AND；
    选了具体国家时建议前端让大区让位）；`status` 为连载状态桶
    （continuing/ended/other）；`pending=1` 只返回待处理剧（未匹配/剧级待
    确认/有未匹配集），行上带 `episode_review_count`；`sort` 白名单
    added/updated/year/title/rating（缺省保持历史 sort_title 排序）。"""
    libs = _lib_ids(library, media_library)
    try:
        limit = max(1, min(int(limit or 200), 2000))
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        limit, offset = 200, 0
    if rating_source not in ("tmdb", "custom"):
        raise HTTPException(422, "rating_source must be tmdb|custom")
    f = {"genres": store._split_multi(genre), "regions": store._split_multi(region),
         "countries": store._split_multi(country), "years": store._split_ints(year),
         "decades": store._split_ints(decade), "tags": store._split_multi(tag),
         "min_rating": min_rating, "rating_source": rating_source,
         "watched": watched, "status": store._split_multi(status)}
    items = store.list_shows(libs, q, limit, offset, **f,
                             sort=sort, order=order, pending=pending)
    total = store.count_shows(libs, q, **f, pending=pending)
    return {"items": items, "total": total,
            "has_more": offset + len(items) < total,
            "limit": limit, "offset": offset}


@router.get("/facets")
def tv_facets(library: str | None = None, media_library: int | None = None):
    """剧集动态分类计数（全库口径，只返有剧的项；与电影 /api/facets 同形状，
    ratings 只有 tmdb/custom，另带 status 三桶）。"""
    libs = _lib_ids(library, media_library)
    return store.get_tv_facets(libs)


@router.get("/suggest")
def tv_suggest(q: str = "", limit: int = 8,
               library: str | None = None, media_library: int | None = None):
    """剧集搜索联想：本地剧名/原名 + 演职员（人名回填搜索，不跳人物页；
    与电影 /api/search/suggest 同形状）。"""
    libs = _lib_ids(library, media_library)
    try:
        lim = max(1, min(int(limit or 8), 20))
    except (TypeError, ValueError):
        lim = 8
    return {"q": q, "items": store.suggest_tv_shows(q, lim, library_ids=libs),
            "persons": store.suggest_tv_people(q, 5, library_ids=libs)}


def _norm_tv_person(c: dict) -> dict:
    """TV 演职单条归一（P1）：补电影侧别名 tmdb_id/character_name/avatar，
    前端 cast.js 只认归一字段，新老客户端兼容。"""
    try:
        pid = int(c.get("id") or c.get("tmdb_id") or 0)
    except (TypeError, ValueError):
        pid = 0
    character = str(c.get("character") or c.get("character_name") or "").strip()
    profile = c.get("profile_path") or c.get("avatar") or ""
    out = dict(c)
    out["id"] = pid
    out["tmdb_id"] = pid
    out["name"] = str(c.get("name") or "").strip()
    out["character"] = character
    out["character_name"] = character
    out["profile_path"] = profile
    out["avatar"] = profile
    return out


def _show_cast(show: dict) -> list[dict]:
    """演职员（详情页展示用）：读 `tmdb_cache(tv).credits` 前 10，
    返回归一场 [{id, tmdb_id, name, character, character_name,
    profile_path, avatar}]；未匹配/无缓存返回 []。

    纯本地读缓存、不触网；`id` 为 TMDB 人物 id（无 id 条目跳过），供前端
    点击进人物页（先 `POST /api/persons/ensure` 建档再跳 `/p/:id`）。"""
    try:
        tid = int(show.get("tmdb_id") or 0)
    except (TypeError, ValueError):
        return []
    if not tid:
        return []
    credits = (store.get_tmdb_cached(tid, "tv") or {}).get("credits") or {}
    out = []
    for c in (credits.get("cast") or [])[:10]:
        name = str(c.get("name") or "").strip()
        try:
            pid = int(c.get("id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if not name or pid <= 0:
            continue
        out.append(_norm_tv_person({
            "id": pid, "name": name,
            "character": str(c.get("character") or "").strip(),
            "profile_path": c.get("profile_path") or ""}))
    return out


@router.get("/shows/{show_id}/similar")
def similar_shows(show_id: int, limit: int = 12):
    """相关节目（Plex 式剧详情行）：纯本地打分（电视网/类型/主创/主演），
    不触网；至少命中一个内容信号且总分达标才返回（见 store.similar_shows）。"""
    if not store.get_show_meta(show_id):
        raise HTTPException(404, "show not found")
    try:
        lim = max(1, min(int(limit or 12), 30))
    except (TypeError, ValueError):
        lim = 12
    return {"items": store.similar_shows(show_id, lim)}


def _season_cast_with_fallback(show_tmdb_id, season_cast: list) -> tuple[list, str]:
    """季演职：本季常驻优先，缺席回退全剧聚合（aggregate），皆无为 none。
    返回条目均为归一形态（见 _norm_tv_person）。"""
    if season_cast:
        return [_norm_tv_person(x) for x in season_cast
                if isinstance(x, dict) and x.get("name")], "season"
    try:
        tid = int(show_tmdb_id or 0)
    except (TypeError, ValueError):
        tid = 0
    if tid:
        cached = store.get_tmdb_cached(tid, "tv") or {}
        credits = cached.get("credits") or {}
        agg = [x for x in (credits.get("cast") or [])[:10]
               if isinstance(x, dict) and x.get("name")]
        if agg:
            return [_norm_tv_person(x) for x in agg], "aggregate"
    return [], "none"


@router.get("/shows/{show_id}/seasons/{season}")
def season_detail(show_id: int, season: int, offset: int = 0, limit: int = 100,
                 verify: str = "0", version: int | None = Query(default=None, ge=1)):
    """季详情（Plex 式季页）：季元数据 + 演职（本季→全剧回退）+ 本季集分页
    （存在性/断点/已看）+ 本季下一集（季内连播）+ 已看计数。

    本地优先：默认 `verify=0` 只信 DB（零远程 I/O），按 `offset/limit`
   （默认 100）懒加载；`verify=1` 对本页批量核验（按父目录分组一次 list）；
    `verify=auto` 先比 10min 剧根快照，无变化直接本地态。离线/失败保守
    本地态 + `stale=True`。表头计数恒为全季（不受分页影响）。"""
    show = store.get_show_meta(show_id)
    if not show:
        raise HTTPException(404, "show not found")
    try:
        sn = int(season)
    except (TypeError, ValueError):
        raise HTTPException(422, "season must be int")
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    versions = store.season_versions(show_id, sn)
    all_total = sum(v["count"] for v in versions)
    total = sum(v["count"] for v in versions if version is None or v["version"] == version)
    meta = store.get_season(show_id, sn)
    if meta is None and total == 0:
        raise HTTPException(404, "season not found")
    if meta is None:
        meta = {"show_id": int(show_id), "season": sn, "name": "",
                "overview": "", "air_date": "", "poster_path": "",
                "episode_count": total, "cast": []}
    eps = store.list_season_episodes(show_id, sn, offset=offset, limit=limit, version=version)
    progress = store.episode_progress_map(show_id)
    verified, stale, emap = False, False, {}
    mode = str(verify or "0").lower()
    if mode not in ("0", "1", "auto"):
        raise HTTPException(422, "verify must be 0|1|auto")
    if mode == "1":
        emap = _verify_exists_map(eps)
        verified = True
    elif mode == "auto" and eps:
        lib_id = int(eps[0].get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        backend = _backend_for(lib_id)
        hit = _snapshot_hit(lib_id, _show_root_of(eps[0].get("file_path") or ""),
                             backend) if backend is not None else None
        if hit is True:
            verified = False  # 快照一致：本地态可信，无需触网
        elif hit is False:
            emap = _verify_exists_map(eps)
            verified = True
        else:
            stale = True  # 无法判断（首建快照/后端不可用）：本地态 + 标 stale
    payloads = [_episode_payload(
        e, progress.get(int(e["id"])),
        emap.get(str(e.get("file_path") or "")) if verified else None)
        for e in eps]
    cast, cast_source = _season_cast_with_fallback(
        show.get("tmdb_id"), list(meta.get("cast") or []))
    nxt = store.season_next_episode(show_id, sn, version=version)
    return {
        "show_id": int(show_id),
        "show_title": show.get("title") or "",
        "show_year": show.get("year"),
        "show_poster": show.get("poster_path") or "",
        "show_backdrop_path": show.get("backdrop_path") or "",
        "original_language": show.get("original_language") or "",
        "season": sn,
        "name": meta.get("name") or "",
        "overview": meta.get("overview") or "",
        "air_date": meta.get("air_date") or "",
        "poster_path": meta.get("poster_path") or "",
        "episode_count": all_total,
        "versions": versions,
        "distinct_count": len({n for e in store.list_season_episodes(show_id, sn)
                               for n in range(int(e["episode"]), max(int(e["episode"]), int(e.get("episode_end") or 0)) + 1)}),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(payloads) < total,
        "watched_count": store.count_season_watched(show_id, sn),
        "cast": cast,
        "cast_source": cast_source,
        "episodes": payloads,
        "next_episode": _episode_payload(nxt) if nxt else None,
        "verified": verified,
        "stale": stale,
    }


@router.get("/shows/{show_id}")
def show_detail(show_id: int, verify: str = "0", include_episodes: int = 0):
    """剧详情：季（含海报/名称/按季聚合）+ 下一集 + 花絮/剧场版。

    本地优先：默认零远程 I/O（`exists` 只信 DB `missing` 列）。
    全量 `episodes[]` 已瘦身（1665 集大剧首屏从 E+X+1 次 STAT 降到 0~1 次）：
    季卡墙用 `seasons[]`（带 total/watched/distinct/versions/has_partial/
    next_episode 聚合）+ `season_stats`，集列表请按季调季详情分页接口。
    `include_episodes=1` 兼容旧端（全量本地态，不触网，已废弃）。
    `verify=1` 对花絮/next 做批量核验；`verify=auto` 先比 10min 剧根快照。"""
    mode = str(verify or "0").lower()
    if mode not in ("0", "1", "auto"):
        raise HTTPException(422, "verify must be 0|1|auto")
    show = store.get_show_meta(show_id)
    if not show:
        raise HTTPException(404, "show not found")
    d = dict(show)
    seasons_meta = store.list_seasons(show_id)
    stats = {s["season"]: s for s in store.show_season_stats(show_id)}
    seasons = []
    for m in seasons_meta:
        sn = int(m.get("season") or 0)
        st = stats.pop(sn, {})
        seasons.append({
            "season": sn,
            "episode_count": int(m.get("episode_count") or st.get("total") or 0),
            "total": int(st.get("total") or m.get("episode_count") or 0),
            "watched_count": int(st.get("watched") or 0),
            "distinct": int(st.get("distinct") or 0),
            "versions": int(st.get("versions") or 0),
            "done": bool(st.get("done", False)),
            "has_partial": bool(st.get("has_partial", False)),
            "next_episode_num": st.get("next_episode"),
            "name": m.get("name") or "",
            "overview": m.get("overview") or "",
            "air_date": m.get("air_date") or "",
            "poster_path": m.get("poster_path") or "",
            "cast": m.get("cast") or [],
        })
    for sn in sorted(stats):  # 有集无季元数据行：合成季卡
        st = stats[sn]
        seasons.append({
            "season": sn,
            "episode_count": int(st.get("total") or 0),
            "total": int(st.get("total") or 0),
            "watched_count": int(st.get("watched") or 0),
            "distinct": int(st.get("distinct") or 0),
            "versions": int(st.get("versions") or 0),
            "done": bool(st.get("done", False)),
            "has_partial": bool(st.get("has_partial", False)),
            "next_episode_num": st.get("next_episode"),
            "name": "", "overview": "", "air_date": "", "poster_path": "",
            "cast": [],
        })
    seasons.sort(key=lambda s: s["season"])
    d["seasons"] = seasons
    d["season_count"] = len(seasons)
    total_eps = store.count_show_episodes(show_id)
    d["episode_count"] = total_eps
    d["watched_count"] = store.count_show_watched(show_id)
    d["review_count"] = store.count_show_review(show_id)
    nxt = store.next_episode(show_id)
    extras = store.list_extras_by_show(show_id)
    verified, stale = False, False
    if mode == "1" and (nxt is not None or extras):
        emap = _verify_exists_map(
            ([nxt] if nxt else []) + [{"file_path": x.get("file_path"),
                                       "library_id": x.get("library_id")} for x in extras])
        d["next_episode"] = _episode_payload(
            nxt, None, emap.get(str(nxt.get("file_path") or ""))) if nxt else None
        d["extras"] = [_extra_payload(
            x, emap.get(str(x.get("file_path") or ""))) for x in extras]
        verified = True
    elif mode == "auto":
        probe_rows = (([nxt] if nxt else []) + extras)[:1]
        snap_hit = None
        if probe_rows:
            lid = int(probe_rows[0].get("library_id")
                      or library_paths.DEFAULT_LIBRARY_ID)
            backend = _backend_for(lid)
            snap_hit = _snapshot_hit(
                lid, _show_root_of(probe_rows[0].get("file_path") or ""),
                backend) if backend is not None else None
        if snap_hit is False:
            emap = _verify_exists_map(
                ([nxt] if nxt else []) + extras)
            d["next_episode"] = _episode_payload(
                nxt, None, emap.get(str(nxt.get("file_path") or ""))) if nxt else None
            d["extras"] = [_extra_payload(
                x, emap.get(str(x.get("file_path") or ""))) for x in extras]
            verified = True
        else:
            d["next_episode"] = _episode_payload(nxt) if nxt else None
            d["extras"] = [_extra_payload(x) for x in extras]
            stale = snap_hit is None
    else:
        d["next_episode"] = _episode_payload(nxt) if nxt else None
        d["extras"] = [_extra_payload(x) for x in extras]
    d["verified"] = verified
    d["stale"] = stale
    if int(include_episodes or 0):
        progress = store.episode_progress_map(show_id)
        rows = store.list_episodes(show_id)
        d["episodes"] = [_episode_payload(e, progress.get(int(e["id"])))
                         for e in rows]
    d["cast"] = _show_cast(d)
    return d


@router.get("/episodes/{episode_id}")
def episode_detail(episode_id: int, verify: str = "0"):
    """单集详情：默认本地优先（零远程 I/O）；`verify=1` 远程核验本集。

    附带剧/季轻量元数据（`get_show_meta`，不再拉整剧 episodes 行）。"""
    e = store.get_episode(episode_id)
    if not e:
        raise HTTPException(404, "episode not found")
    mode = str(verify or "0").lower()
    if mode not in ("0", "1"):
        raise HTTPException(422, "verify must be 0|1")
    exists = None
    if mode == "1":
        emap = _verify_exists_map([e])
        exists = emap.get(str(e.get("file_path") or ""))
    out = _episode_payload(e, exists=exists)
    show = store.get_show_meta(e["show_id"]) or {}
    out["show_title"] = show.get("title", "")
    out["show_year"] = show.get("year")
    out["show_poster"] = show.get("poster_path") or ""
    out["show_backdrop_path"] = show.get("backdrop_path") or ""
    out["original_language"] = show.get("original_language") or ""
    season_meta = store.get_season(int(e["show_id"]), int(e.get("season") or 0))
    out["season_name"] = (season_meta or {}).get("name") or ""
    out["season_poster"] = (season_meta or {}).get("poster_path") or ""
    # 单集演职成品：本季常驻 + 本集客串（去重，客串打标），缺席回退聚合
    out.update(store.episode_cast(episode_id))
    prev_ep, next_ep = store.episode_neighbors(episode_id)
    out["previous_episode"] = _episode_payload(prev_ep) if prev_ep else None
    out["next_episode"] = _episode_payload(next_ep) if next_ep else None
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
    return store.tv_library_stats(libs)


@router.get("/recent-played")
def recent_played(limit: int = 20, include_finished: bool = False,
                  library: str | None = None, media_library: int | None = None):
    """继续观看（剧集）：断点粒度、海报粒度去重（同集多版本留最近）。
    `include_finished=true` 含已看完（供「全部最近播放」）。"""
    libs = _lib_ids(library, media_library)
    if libs == []:
        return {"items": []}
    return {"items": store.list_recent_played_tv(limit, libs, include_finished)}


_TV_AVATAR_PATH_RE = re.compile(r"^/[A-Za-z0-9]{6,}\.(?:jpg|jpeg|png)$")


@router.get("/cast-avatar")
def tv_cast_avatar(path: str = Query(default="")):
    """演职员头像代理（剧/季/集详情页用）：TMDB profile_path → h632 下载缓存
    到 `data/posters/tvcast/`，FileResponse 返回（浏览器无需可达 TMDB 图片域名）。

    h632（高约 632px）供 180px 圆形展示清晰；缓存键带尺寸前缀 `h632_`，
    与旧 w185 键隔离（旧文件自然失效，不串图）。
    人物级缓存（按 path 去重，多剧复用同一人只存一份）；失败抛 502，
    前端 `onerror` 回退首字母占位。"""
    from fastapi.responses import FileResponse
    from ..db import POSTER_DIR
    p = str(path or "").strip()
    if not _TV_AVATAR_PATH_RE.match(p):
        raise HTTPException(422, f"illegal avatar path: {path!r}")
    name = hashlib.sha1(p.encode("utf-8")).hexdigest()[:12]
    dest = os.path.join(POSTER_DIR, "tvcast", f"h632_{name}.jpg")
    if not os.path.isfile(dest):
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
        except OSError as e:
            logger.warning("tv avatar cache dir failed: %s", e)
            raise HTTPException(500, f"cache dir failed: {e}")
        try:
            ok = tmdb.download_image(p, dest, size="h632")
        except Exception as e:
            logger.warning("tv avatar download failed path=%s: %s", p, e)
            raise HTTPException(502, "avatar download failed")
        if not ok:
            logger.warning("tv avatar download failed path=%s: tmdb returned false", p)
            raise HTTPException(502, "avatar download failed")
    return FileResponse(dest, media_type="image/jpeg",
                        headers={"Cache-Control": "no-cache"})


@router.get("/search")
def search_tv(q: str = "", year: int | None = None,
              library: str | None = None):
    """剧集搜索（手动匹配用）：TMDB 优先，失败/无凭据回退降级链（P2.4 外源）。"""
    term = (q or "").strip()
    if not term:
        return {"items": [], "source": "none"}
    items: list[dict] = []
    source = "none"
    if (config.effective_tmdb_read_token() or config.effective_tmdb_api_key()):
        try:
            rows = tmdb.search_tv(term, year)
            for r in (rows or [])[:20]:
                fd = str(r.get("first_air_date") or "")[:4]
                items.append({
                    "tmdb_id": r.get("id"),
                    "title": r.get("name") or "",
                    "original_title": r.get("original_name") or "",
                    "year": int(fd) if fd.isdigit() else None,
                    "overview": r.get("overview") or "",
                    "poster_path": r.get("poster_path") or "",
                    "source": "tmdb", "source_id": str(r.get("id") or ""),
                })
            if items:
                source = "tmdb"
        except Exception as e:
            logger.warning("tv search failed q=%s: %s", term, e)
    if not items:
        try:
            from ..metadata import chain as meta_chain
            libs = store._split_ints(library)
            lib_id = libs[0] if len(libs) == 1 else None
            for c in meta_chain.search(term, year, "tv", library_id=lib_id, limit=10):
                items.append({**c.to_dict(), "overview": "", "poster_path": "",
                              "external": True})
            source = "offline" if items else "none"
        except Exception as e:
            logger.warning("tv offline search failed q=%s: %s", term, e)
    return {"items": items, "source": source}


class MatchBody(BaseModel):
    tmdb_id: int


# 匹配后 NFO/海报落盘策略：集数 ≤ 该阈值同步写（前端一次 load 即见新海报），
# 大剧放后台线程防请求超时（响应 media.queued=true，前端轮询补齐）。
_TV_MATCH_SYNC_EPISODES = 120


def _match_show_snapshot(show: dict) -> dict:
    """匹配响应携带的剧快照（前端免一次 reload 即可刷新标题/简介/海报）。"""
    return {
        "tmdb_id": show.get("tmdb_id"),
        "title": show.get("title") or "",
        "poster_path": show.get("poster_path") or "",
        "backdrop_path": show.get("backdrop_path") or "",
        "has_overview": bool((show.get("overview_override") or show.get("overview") or "").strip()),
        "fetched_at": show.get("fetched_at") or 0,
        "needs_review": int(show.get("needs_review") or 0),
        "match_source": show.get("match_source") or "",
    }


def _apply_tv_cached_offline(show_id: int, tmdb_id: int, cached: dict) -> dict:
    """离线换绑：按 tmdb_cache 镜像回填剧行（与电影 manual_match 离线分支同语义）。

    无季/集详情，不碰集行；本地海报文件存在才指向（不存在留空由前端占位）。"""
    from ..db import POSTER_DIR
    payload: dict = {}
    try:
        payload = json.loads(cached.get("payload_json") or "{}")
    except (TypeError, ValueError):
        payload = {}
    src = payload if payload.get("title") else (cached or {})
    fields: dict = {"tmdb_id": int(tmdb_id), "needs_review": 0,
                    "match_source": "manual", "title_auto": 0,
                    "fetched_at": int(time.time())}
    for k in ("original_title", "year", "overview", "imdb_id", "tvdb_id",
              "tmdb_rating", "genres", "genre_ids", "origin_country",
              "origin_countries", "original_language", "region", "status",
              "first_air_date", "last_air_date", "number_of_seasons",
              "number_of_episodes", "episode_run_time", "networks",
              "created_by"):
        if src.get(k) not in (None, ""):
            fields[k] = src[k]
    if src.get("title"):
        fields["title"] = str(src["title"])
        fields["sort_title"] = normalize_title(str(src["title"]))
    for key, namer in (("poster_path", tv_persist.tv_poster_name),
                       ("backdrop_path", tv_persist.tv_backdrop_name)):
        try:
            rel = namer(int(tmdb_id))
            dest = os.path.join(POSTER_DIR, rel)
            if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                fields[key] = rel
        except OSError as e:
            logger.debug("tv offline art check failed tmdb_id=%s: %s", tmdb_id, e)
    store.update_show_meta(show_id, **fields)
    return store.get_show_meta(show_id) or {}


@router.post("/shows/{show_id}/match")
def match_show(show_id: int, body: MatchBody):
    """手动换绑 TMDB 剧集：拉详情+季详情并落库（source=manual，清待确认）。

    快回包：文字元数据同步落库，响应自带剧快照 + 落盘状态 media
    （小剧同步写 NFO/海报，大剧后台线程并置 media.queued=true）。
    TMDB 不可用时用 tmdb_cache 离线绑定（{offline:true}，与电影同语义）。"""
    try:
        tmdb_id = int(body.tmdb_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "tmdb_id must be int")
    if not (0 < tmdb_id <= 2 ** 31 - 1):
        raise HTTPException(422, "tmdb_id out of range")
    show = store.get_show_meta(show_id)
    if not show:
        raise HTTPException(404, "show not found")
    if store.list_tv_bindings(show_id=show_id) and show.get('tmdb_id') != tmdb_id:
        raise HTTPException(409, '这部剧已有目录归属，请通过“归属与季号”预览并调整')
    try:
        detail = tmdb.tv_detail(tmdb_id)
    except Exception as e:
        cached = store.get_tmdb_cached(tmdb_id, "tv")
        if not cached:
            raise HTTPException(502, f"TMDB 详情失败：{str(e)[:200]}")
        logger.warning("tv match offline bind show=%s tmdb_id=%s: %s",
                       show_id, tmdb_id, e)
        fresh = _apply_tv_cached_offline(show_id, tmdb_id, cached)
        return {"ok": True, "offline": True, "show_id": int(show_id),
                "tmdb_id": int(tmdb_id), "episodes_matched": 0, "remapped": 0,
                "media": {"queued": False, "offline": True},
                "show": _match_show_snapshot(fresh)}
    local = store.list_episodes(show_id)
    season_details: dict = {}
    failed_seasons: list[int] = []
    for sn in tv_persist.wanted_seasons(detail, local):
        try:
            season_details[sn] = tmdb.tv_season(int(body.tmdb_id), sn)
        except Exception as e:
            # 单季拉取失败不静默：漏一季会让整季集号停在待确认（换绑可见性）
            failed_seasons.append(int(sn))
            logger.warning("tv match season fetch failed tmdb_id=%s season=%s: %s",
                           tmdb_id, sn, e)
    try:
        aggregate = tmdb.tv_aggregate_credits(int(body.tmdb_id))
    except Exception:
        aggregate = None
    stats = tv_persist.apply_tv_detail(
        show_id, detail, season_details, source="manual", needs_review=0,
        library_id=show.get("library_id"), aggregate=aggregate,
        force_title=True)      # 显式换绑：标题必须跟新条目（电影 manual_match 同语义）
    fresh = store.get_show_meta(show_id) or {}
    try:
        sync_limit = int(os.getenv("TV_MATCH_SYNC_EPISODES",
                                   str(_TV_MATCH_SYNC_EPISODES)) or _TV_MATCH_SYNC_EPISODES)
    except (TypeError, ValueError):
        sync_limit = _TV_MATCH_SYNC_EPISODES
    if len(local) <= sync_limit:
        # 小剧同步落盘：返回时 NFO/海报已写完，前端一次 load 即见新图
        try:
            media = tv_persist.write_media_files(show_id, show.get("library_id"))
        except Exception as e:
            logger.warning("tv match media write failed show=%s: %s", show_id, e)
            media = {"nfo": False, "nfo_wrote": 0, "nfo_skipped": 0,
                     "artwork": {"ok": False, "reason": "error"}}
    else:
        # 大剧（千集级）同步写会让浏览器请求超时：放后台，前端轮询补齐
        threading.Thread(target=tv_persist.write_media_files,
                         args=(show_id, show.get("library_id")), daemon=True).start()
        media = {"nfo": False, "nfo_wrote": 0, "nfo_skipped": 0,
                 "artwork": {"ok": False, "reason": "queued"}, "queued": True}
    return {"ok": True, "offline": False, **stats, "media": media,
            "seasons_failed": failed_seasons,
            "show": _match_show_snapshot(fresh)}


@router.post("/shows/{show_id}/bind-external")
def bind_external_show(show_id: int, body: dict):
    """外部元数据候选（wikidata/tvmaze/bgm/nfo）显式绑定（P2.4）。

    拉取外源 detail（tvmaze/bgm 含剧集）落库；带 tmdb_id 的候选仅作为提示，
    后续仍可用「匹配 TMDB」升级。"""
    show = store.get_show_meta(show_id)
    if not show:
        raise HTTPException(404, "show not found")
    if store.list_tv_bindings(show_id=show_id):
        raise HTTPException(409, '这部剧已有目录归属，请通过“归属与季号”预览并调整')
    source = str((body or {}).get("source") or "").strip()
    source_id = str((body or {}).get("source_id") or "").strip()
    if not source or not source_id:
        raise HTTPException(422, "source/source_id required")
    from ..metadata.base import Candidate
    cand = Candidate(source=source, source_id=source_id,
                     title=show.get("title") or "", year=show.get("year"))
    out = tv_persist._apply_external_candidate(show, cand, 0)
    if out is None:
        raise HTTPException(502, "外部详情获取失败")
    fresh = store.get_show_meta(show_id) or {}
    return {"ok": True, "show": _match_show_snapshot(fresh), **out}


def _is_tv_lib_read_only(library_id) -> bool:
    try:
        return library_paths.is_read_only(int(library_id or 0))
    except Exception:
        return False


@router.post("/shows/{show_id}/discover")
def discover_show_files(show_id: int):
    """发现当前剧目录中的新增视频，再由详情页生成整理预览。

    只遍历本剧剧根，不触发整库扫描和失效 GC。已缓存的季详情可直接补齐新增集的
    TMDB 集号与集名；缓存较旧时保留待确认状态，文件本身仍会立即出现在详情页。
    """
    show = store.get_show_meta(show_id)
    if not show:
        raise HTTPException(404, "show not found")
    try:
        result = scanner.scan_tv_show(show_id)
    except storage.StorageNotFound as e:
        raise HTTPException(409, f"show directory not found: {e}") from e
    except storage.StorageOffline as e:
        raise HTTPException(503, f"library offline: {e}") from e
    except storage.StorageError as e:
        raise HTTPException(502, f"library unavailable: {e}") from e
    filled = 0
    if result.get("added"):
        try:
            filled = tv_persist.backfill_cached_episodes(show_id)
        except Exception as e:
            logger.warning("cached episode backfill failed show=%s: %s", show_id, e)
    result["metadata_filled"] = int(filled) + int(result.get("metadata_filled") or 0)
    result["episode_count"] = store.count_show_episodes(show_id)
    return {"ok": True, **result}


@router.get("/shows/{show_id}/organize-hint")
def show_organize_hint(show_id: int, actions: str | None = None,
                       allow_absolute: bool = False):
    """单剧整理预览（对标电影 organize-hint）：复用 plan_tv_organize 同步返回。

    返回 {needs, matched, absolute_risk, blocked, counts, groups, manual,
    conflicts, untouched, params}；params 可直接回传 POST /api/jobs/tv-organize
    执行（ids + actions + allow_absolute_shows）。
    TV 无跨库搬迁语义：needs=有可执行移动/改名；绝对集号风险剧 needs=false
    但仍带 absolute/manual 解释，前端照样提示去设置页勾选。"""
    from ..scanner import tv_organize
    show = store.get_show_meta(show_id)
    if not show:
        raise HTTPException(404, "show not found")
    if actions:
        acts = [a.strip() for a in str(actions).split(",")
                if a.strip() in tv_organize.TV_ORGANIZE_ACTIONS]
        acts = acts or list(tv_organize.TV_ORGANIZE_ACTIONS)
    else:
        acts = list(tv_organize.TV_ORGANIZE_ACTIONS)
    allow_abs = [int(show_id)] if allow_absolute else []
    plan = tv_organize.plan_tv_organize(ids=[int(show_id)], actions=acts,
                                        allow_absolute_shows=allow_abs)
    plans = plan.get("plans") or []
    lib = None
    try:
        lib = store.get_library(int(show.get("library_id") or 0))
    except Exception:
        lib = None
    base = {"show_id": int(show_id), "title": show.get("title") or "",
            "library_id": show.get("library_id"),
            "media_library_id": (lib or {}).get("media_library_id"),
            "matched": bool(show.get("tmdb_id")),
            "params": {"ids": [int(show_id)], "actions": acts,
                       "allow_absolute_shows": allow_abs}}
    p = plans[0] if plans else None
    if p is None:
        reason = ("read_only" if _is_tv_lib_read_only(show.get("library_id"))
                  else "no_plan")
        return {**base, "needs": False, "reason": reason, "show_dir": "",
                "absolute_risk": False, "blocked": False, "warnings": [],
                "counts": {}, "groups": [], "dir_totals": [],
                "manual": [], "manual_more": 0,
                "kept": [], "kept_count": 0,
                "conflicts": [], "untouched": [], "untouched_count": 0}
    summary = tv_organize.summarize_plan(p)
    needs = bool(p.get("file_moves") or p.get("dir_moves")
                 or p.get("dir_renames") or p.get("root_move"))
    if not needs:
        if not show.get("tmdb_id"):
            reason = "unmatched"
        elif summary.get("absolute_risk"):
            reason = "absolute"
        elif (summary.get("manual") or summary.get("manual_more")):
            reason = "manual"
        elif summary.get("blocked"):
            reason = "blocked"
        elif summary.get("conflicts"):
            reason = "conflicts"
        else:
            reason = "no_plan"
    else:
        reason = "ok"
    return {**base, "needs": needs, "reason": reason,
            "show_dir": p.get("show_dir") or "",
            "absolute_risk": bool(summary.get("absolute_risk")),
            "blocked": bool(summary.get("blocked")),
            "warnings": summary.get("warnings") or [],
            "counts": summary.get("counts") or {},
            "groups": summary.get("groups") or [],
            "dir_totals": summary.get("dir_totals") or [],
            "manual": summary.get("manual") or [],
            "manual_more": int(summary.get("manual_more") or 0),
            "kept": summary.get("kept") or [],
            "kept_count": int(summary.get("kept_count") or 0),
            "conflicts": summary.get("conflicts") or [],
            "untouched": summary.get("untouched") or [],
            "untouched_count": int(summary.get("untouched_count") or 0)}


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


@router.post("/shows/{show_id}/seasons/{season}/watched")
def season_watched(show_id: int, season: int, body: WatchedBody | None = None):
    """标本季已看/未看（返回受影响集数；标已看清本季断点）。"""
    if not store.get_show_meta(show_id):
        raise HTTPException(404, "show not found")
    try:
        sn = int(season)
    except (TypeError, ValueError):
        raise HTTPException(422, "season must be int")
    watched = bool(body.watched) if body else True
    n = store.mark_season_watched(show_id, sn, watched)
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
    ep_credits = tv_match.tv_episode_credits(found)
    store.update_episode_meta(
        episode_id,
        tmdb_episode_id=found.get("id"),
        match_source="manual", binding_conflict=0,
        title=(found.get("name") or "").strip() or e.get("title") or "",
        overview=found.get("overview") or "",
        still_path=found.get("still_path") or "",
        air_date=str(found.get("air_date") or "")[:10],
        runtime=runtime,
        tmdb_rating=found.get("vote_average"),
        needs_review=0,
        local_only=0,          # 重新绑定 TMDB 集 → 取消「本地确认集」
        **({"episode_credits": json.dumps(ep_credits, ensure_ascii=False)}
           if ep_credits else {}),
    )
    # 剧照换了 → 懒下载缓存失效（下次请求重拉；兼容迁移前根部旧文件）
    try:
        from ..db import POSTER_DIR
        from ..scanner.tv_persist import episode_still_name
        stale = os.path.join(POSTER_DIR, episode_still_name(int(episode_id)))
        if os.path.isfile(stale):
            os.remove(stale)
        legacy = os.path.join(POSTER_DIR, f"tv_e{int(episode_id)}.jpg")
        if os.path.isfile(legacy):
            os.remove(legacy)
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
