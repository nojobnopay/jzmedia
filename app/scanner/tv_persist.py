"""TV 刮削持久化（T2）：TMDB detail + 季详情 → tmdb_cache(media_type='tv') →
`tv_shows` 镜像 / `tv_seasons` / `tv_episodes` 元数据，海报落 `data/posters`。

- 离线降级：detail 拉取失败时用 `tmdb_cache.payload_json` 回放（无集详情，标 ok_offline）；
- 绝对集号：先写季（含 TMDB 集数）→ `store.remap_absolute_episodes` 映射 (season, episode)
  → 再按映射后的季集回填集元数据（蜡笔小新/七龙珠/钢炼）；
- 剧照不预下载（3,877 集太重）：只存 TMDB 路径，`GET /api/tv/episodes/{id}/still`
  首次请求懒下载到 `data/posters/tv_e<id>.jpg`；
- 重刮不覆盖用户手工标题（T2 无剧集标题编辑，故 TMDB 标题直接写；`title_auto` 保留列）。
"""
import concurrent.futures
import json
import os
import time

from .. import store, tmdb
from ..db import POSTER_DIR
from ..log import get_logger
from . import tv_match
from .parse import normalize_title
from .tv_parse import map_absolute

logger = get_logger("scanner.tv_persist")
__all__ = ['scrape_show', 'scrape_pending', 'apply_tv_detail', 'ensure_episode_still',
           'wanted_seasons', 'write_media_files', 'tv_poster_name', 'tv_backdrop_name',
           'tv_season_poster_name', 'episode_still_name', 'backfill_person_names',
           'person_names_from_credits']

_STILL_FAIL: dict[int, float] = {}   # 剧照下载失败短冷却（防坏路径反复打 TMDB）
_STILL_FAIL_TTL = 600.0


def tv_poster_name(tmdb_id: int) -> str:
    """剧集海报（POSTER_DIR 相对路径，`tv/<id>.jpg`；入库值）。"""
    return f"tv/{int(tmdb_id)}.jpg"


def tv_backdrop_name(tmdb_id: int) -> str:
    """剧集背景（POSTER_DIR 相对路径，`backdrops/tv_<id>.jpg`；入库值）。"""
    return f"backdrops/tv_{int(tmdb_id)}.jpg"


def tv_season_poster_name(tmdb_id: int, season: int) -> str:
    """季海报（POSTER_DIR 相对路径，`tv/<id>_s<N>.jpg`；入库值）。"""
    return f"tv/{int(tmdb_id)}_s{int(season)}.jpg"


def episode_still_name(episode_id: int) -> str:
    """集剧照（POSTER_DIR 相对路径，`stills/<episode_id>.jpg`；不入库）。"""
    return f"stills/{int(episode_id)}.jpg"


def _download(path: str, dest: str, size: str) -> str:
    """TMDB 图片 → 本地（已存在且非空直接复用）；失败返回 ''。"""
    if not path:
        return ""
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest
    if tmdb.download_image(path, dest, size=size):
        return dest
    return ""


def ensure_episode_still(episode_id: int) -> str:
    """懒下载集剧照（w300）；失败有 10 分钟冷却。返回本地路径或 ''。"""
    ep = store.get_episode(episode_id)
    if not ep or not (ep.get("still_path") or ""):
        return ""
    dest = os.path.join(POSTER_DIR, episode_still_name(episode_id))
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest
    # 迁移前根部旧文件（tv_e<id>.jpg）：懒搬到新位置，省一次 TMDB 下载
    legacy = os.path.join(POSTER_DIR, f"tv_e{int(episode_id)}.jpg")
    try:
        if os.path.isfile(legacy) and os.path.getsize(legacy) > 0:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.replace(legacy, dest)
            return dest
    except OSError as e:
        logger.debug("still legacy move failed episode=%s: %s", episode_id, e)
    failed_at = _STILL_FAIL.get(int(episode_id), 0.0)
    if failed_at and (time.time() - failed_at) < _STILL_FAIL_TTL:
        return ""
    if tmdb.download_image(ep["still_path"], dest, size="w300"):
        _STILL_FAIL.pop(int(episode_id), None)
        return dest
    _STILL_FAIL[int(episode_id)] = time.time()
    return ""


def _episode_index(season_details: dict) -> tuple[dict, dict]:
    """季详情 → (season_index, abs_index)。

    - season_index: {(season, episode_number): episode}（精确匹配）；
    - abs_index: {episode_number: episode}，仅收**跨季唯一**的集号且排除特典季——
      TMDB 部分剧集跨季连续编号（越狱兔 S2 编号 14–26、AoT S4 编号 60–87 的写法），
      本地按季编号时要靠它回退。重复集号（Friends 每季都有 E01）不入索引。
    """
    season_index: dict = {}
    abs_index: dict = {}
    dup: set = set()
    for sn, data in (season_details or {}).items():
        try:
            season = int(sn)
        except (TypeError, ValueError):
            continue
        for ep in (data or {}).get("episodes") or []:
            try:
                n = int(ep.get("episode_number"))
            except (TypeError, ValueError):
                continue
            season_index[(season, n)] = ep
            if season <= 0:
                continue
            if n in abs_index:
                dup.add(n)
            else:
                abs_index[n] = ep
    for n in dup:
        abs_index.pop(n, None)
    return season_index, abs_index


def _season_counts(detail: dict) -> list[tuple[int, int]]:
    out = []
    for s in detail.get("seasons") or []:
        try:
            sn, cnt = int(s.get("season_number")), int(s.get("episode_count") or 0)
        except (TypeError, ValueError):
            continue
        if sn > 0 and cnt > 0:
            out.append((sn, cnt))
    return sorted(out)


def wanted_seasons(detail: dict, local: list[dict]) -> list[int]:
    """要拉季详情的季号：本地出现的季 + 绝对集号覆盖所需的季。"""
    want: set[int] = set()
    max_abs = 0
    for e in local:
        try:
            sn = int(e.get("season") or 0)
        except (TypeError, ValueError):
            sn = 0
        want.add(sn)
        try:
            max_abs = max(max_abs, int(e.get("absolute_number") or 0))
        except (TypeError, ValueError):
            pass
    if max_abs:
        covered = 0
        for sn, cnt in _season_counts(detail):
            if covered >= max_abs:
                break
            want.add(sn)
            covered += cnt
    return sorted(want)


def person_names_from_credits(credits, created_by=None, extra=()) -> str:
    """演职员人名串（搜索联想/q 搜人用）：全剧聚合 + 各季常驻 + 创作者，去重拼接。

    只取聚合前 N 会漏掉单季主角（如少年包青天 S1 周杰只演 40 集、排不进
    全剧前 10），故与季常驻取并集；与电影 `person_names` 同用途。
    `extra` 为季常驻名序列（调用方从季详情 credits 同源提取）。"""
    cast_names = [c.get("name") for c in ((credits or {}).get("cast") or [])
                  if c.get("name")]
    season_names = [str(n or "").strip() for n in (extra or []) if str(n or "").strip()]
    creator_names = [c.get("name") for c in (created_by or []) if c.get("name")]
    return ", ".join(dict.fromkeys(cast_names + season_names + creator_names))


def apply_tv_detail(show_id: int, detail: dict, season_details: dict | None = None,
                    source: str = "tmdb", needs_review: int = 0,
                    download_art: bool = True, offline_reason: str = "",
                    library_id: int | None = None,
                    aggregate: dict | None = None,
                    force_title: bool = False) -> dict:
    """把 TMDB 详情落到 tv_shows/tv_seasons/tv_episodes（幂等）。返回统计。

    三级演职（与 TMDB 口径对齐）：series 级存 `aggregate_credits` 全剧聚合
    （`tv_detail` 自带的 credits 官方定义仅为最新季，不可用）；季级存各季
    credits；集级存对应集 `guest_stars` + 导演（季详情免费带来）。
    `aggregate=None`（离线/未拉取）时三级一律不覆盖旧值。
    `force_title=True`（显式换绑，与电影 manual_match 同语义）：忽略
    `title_auto=0` 的保护标记，用新条目的 TMDB 标题覆盖（防换绑后标题停留在旧条目）。"""
    now = int(time.time())
    tmdb_id = int(detail.get("id"))
    lib_id = int(library_id or 0) or int(store.DEFAULT_LIBRARY_ID)
    meta = tv_match.tv_meta_from_detail(detail)
    # series 级：aggregate 优先；其缺席时回退 detail credits（旧行为，仍好于无）；
    # 两者皆无（离线回放）则沿用缓存、不覆盖（否则一次断网重刮清空演员信息）。
    if isinstance(aggregate, dict):
        credits = tv_match.tv_aggregate_credits(aggregate)
        credits["crew"] = tv_match.tv_credits(detail).get("crew", [])
        has_credits = True
    elif isinstance(detail.get("credits"), dict):
        credits = tv_match.tv_credits(detail)
        has_credits = True
    else:
        credits = {"cast": [], "crew": []}
        has_credits = False
    if has_credits:
        store.upsert_tmdb_cache(tmdb_id, meta, credits,
                                meta.get("poster_tmdb_path") or "", media_type="tv")
    else:
        cached = store.get_tmdb_cached(tmdb_id, "tv")
        store.upsert_tmdb_cache(tmdb_id, meta,
                                (cached or {}).get("credits") or {"cast": [], "crew": []},
                                meta.get("poster_tmdb_path") or "", media_type="tv")
    # v27：季详情压缩缓存（离线重刮回放集名/简介/剧照；仅在线拉到时覆盖）
    if season_details:
        try:
            store.set_tmdb_cache_seasons(tmdb_id, season_details, media_type="tv")
        except Exception as e:
            logger.debug("cache seasons failed tmdb_id=%s: %s", tmdb_id, e)
    # 剧集镜像列（标题保护：手工改过的标题不被 TMDB 覆盖，title_auto=0 表示受保护；
    # 显式换绑 force_title=True 例外——新条目标题必须生效，否则标题会停在旧条目）
    cur = store.get_show_meta(show_id) or {}
    keep_title = ((not force_title) and bool(cur.get("title"))
                  and not int(cur.get("title_auto") or 0))
    title = str(cur.get("title")) if keep_title else meta["title"]
    fields = {
        "title": title,
        "sort_title": normalize_title(title),
        "title_auto": 0,
        "original_title": meta["original_title"],
        "year": meta["year"],
        "overview": meta["overview"],
        "tmdb_id": tmdb_id,
        "imdb_id": meta.get("imdb_id") or "",
        "tvdb_id": meta.get("tvdb_id"),
        "tmdb_rating": meta.get("tmdb_rating"),
        "genres": meta.get("genres") or [],
        "genre_ids": meta.get("genre_ids") or [],
        "origin_country": meta.get("origin_country") or "",
        "origin_countries": meta.get("origin_countries") or [],
        "original_language": meta.get("original_language") or "",
        "region": meta.get("region") or "",
        "status": meta.get("status") or "",
        "first_air_date": meta.get("first_air_date") or "",
        "last_air_date": meta.get("last_air_date") or "",
        "number_of_seasons": meta.get("number_of_seasons") or 0,
        "number_of_episodes": meta.get("number_of_episodes") or 0,
        "episode_run_time": meta.get("episode_run_time") or 0,
        "networks": meta.get("networks") or [],
        "created_by": meta.get("created_by") or [],
        "fetched_at": now,
        "needs_review": int(needs_review or 0),
        "match_source": source or "tmdb",
    }
    if has_credits:
        season_names: list[str] = []
        for _sd in (season_details or {}).values():
            _cr = _sd.get("credits") if isinstance(_sd, dict) else None
            for _c in ((_cr or {}).get("cast") or []):
                if isinstance(_c, dict) and _c.get("name"):
                    season_names.append(_c["name"])
        fields["person_names"] = person_names_from_credits(
            credits, meta.get("created_by"), season_names)
    seasons = detail.get("seasons") or []
    # 海报/背景/季海报并发下载（先下载再写路径，失败留空由前端回落占位）
    art: dict = {}
    if download_art:
        jobs: list[tuple[str, str, str, tuple]] = []
        if meta.get("poster_tmdb_path"):
            jobs.append((meta["poster_tmdb_path"],
                         os.path.join(POSTER_DIR, tv_poster_name(tmdb_id)),
                         "w500", ("poster",)))
        if meta.get("backdrop_tmdb_path"):
            jobs.append((meta["backdrop_tmdb_path"],
                         os.path.join(POSTER_DIR, tv_backdrop_name(tmdb_id)),
                         "w780", ("backdrop",)))
        for s in seasons:
            try:
                sn = int(s.get("season_number"))
            except (TypeError, ValueError):
                continue
            if s.get("poster_path"):
                jobs.append((s["poster_path"],
                             os.path.join(POSTER_DIR, tv_season_poster_name(tmdb_id, sn)),
                             "w300", ("season", sn)))
        if jobs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                futs = {ex.submit(_download, p, d, sz): key for p, d, sz, key in jobs}
                for fut, key in futs.items():
                    try:
                        art[key] = bool(fut.result())
                    except Exception as e:
                        logger.debug("artwork download failed key=%s: %s", key, e)
                        art[key] = False
        if art.get(("poster",)):
            fields["poster_path"] = tv_poster_name(tmdb_id)
        if art.get(("backdrop",)):
            fields["backdrop_path"] = tv_backdrop_name(tmdb_id)
    store.update_show_meta(show_id, **fields)
    # 季元数据（含 TMDB 集数 → 绝对集号偏移；本季常驻阵容随季详情免费到来）
    for s in seasons:
        try:
            sn = int(s.get("season_number"))
        except (TypeError, ValueError):
            continue
        poster = tv_season_poster_name(tmdb_id, sn) if art.get(("season", sn)) else ""
        sd = (season_details or {}).get(sn)
        cast_json = None
        if isinstance(sd, dict) and isinstance(sd.get("credits"), dict):
            cast_json = json.dumps(tv_match.tv_season_credits(sd),
                                   ensure_ascii=False)
        store.upsert_season(show_id, lib_id, sn,
                            name=s.get("name") or "", overview=s.get("overview") or "",
                            air_date=str(s.get("air_date") or "")[:10],
                            poster_path=poster,
                            episode_count=int(s.get("episode_count") or 0),
                            tmdb_season_id=s.get("id"),
                            cast_json=cast_json)
    # 绝对集号 → (season, episode)（季已写，偏移可用）
    remapped = store.remap_absolute_episodes(show_id)
    # 集元数据回填：先精确 (季,集)，再按 TMDB 编号习惯回退（跨季连续编号 / 本地绝对编号）
    season_index, abs_index = _episode_index(season_details or {})
    counts = dict(_season_counts(detail))
    offsets = _season_counts(detail)
    matched = 0
    applied_ids: set = set()

    def _episode_credits_for(e: dict) -> str | None:
        """本地集（含多集区间）→ episode_credits JSON；对应 TMDB 条目缺
        `guest_stars` 键视为无数据（返回 None，调用方跳过覆盖）。"""
        try:
            s0 = int(e.get("season") or 0)
            e0 = int(e.get("episode") or 0)
            e1 = int(e.get("episode_end") or 0) or e0
        except (TypeError, ValueError):
            return None
        guests, directors, seen = [], [], set()
        found_key = False
        for n in range(e0, e1 + 1):
            t2 = season_index.get((s0, n))
            if not isinstance(t2, dict) or "guest_stars" not in t2:
                continue
            found_key = True
            for g in tv_match.tv_episode_credits(t2)["guests"]:
                if g["id"] not in seen:
                    seen.add(g["id"])
                    guests.append(g)
            for d in tv_match.tv_episode_credits(t2)["directors"]:
                if ("d", d["id"]) not in seen:
                    seen.add(("d", d["id"]))
                    directors.append(d)
        if not found_key:
            return None
        return json.dumps({"guests": guests, "directors": directors},
                          ensure_ascii=False)

    def _apply(e: dict, t: dict) -> None:
        nonlocal matched
        applied_ids.add(int(e["id"]))
        try:
            runtime = int(t.get("runtime") or 0)
        except (TypeError, ValueError):
            runtime = 0
        extra: dict = {}
        ec = _episode_credits_for(e)
        if ec is not None:
            extra["episode_credits"] = ec
        store.update_episode_meta(
            int(e["id"]),
            tmdb_episode_id=t.get("id"),
            title=(t.get("name") or "").strip() or e.get("title") or "",
            overview=t.get("overview") or "",
            still_path=t.get("still_path") or "",
            air_date=str(t.get("air_date") or "")[:10],
            runtime=runtime,
            tmdb_rating=t.get("vote_average"),
            needs_review=0,
            **extra,
        )
        matched += 1

    if season_index:
        claimed: set = set()
        pending: list = []
        rows = store.list_episodes(show_id)
        local_counts: dict[int, int] = {}
        for e in rows:
            try:
                s0 = int(e.get("season") or 0)
                e0 = max(int(e.get("episode") or 0), int(e.get("episode_end") or 0))
            except (TypeError, ValueError):
                continue
            local_counts[s0] = max(local_counts.get(s0, 0), e0)
        for e in rows:
            if e.get("local_only"):
                continue          # 已确认「TMDB 无对应集」：不参与自动匹配
            try:
                key = (int(e.get("season") or 0), int(e.get("episode") or 0))
            except (TypeError, ValueError):
                continue
            t = season_index.get(key)
            if t is not None:
                claimed.add(key)
                _apply(e, t)
            else:
                pending.append((e, key))
        for e, (season, episode) in pending:
            # 三种编号习惯的回退候选（按可信度排序）：
            # ① TMDB 跨季连续编号（越狱兔 S2E1 → TMDB S2E14）；
            # ② 本地季拼接编号（咒术 S2E1 → TMDB S1E25，本地季切分与 TMDB 不同）；
            # ③ 本地绝对编号（AoT S4E60 → TMDB S4E1；怪兽 S2E13 → S1E13）。
            fits = season in counts and episode <= counts[season]
            # 跨季回退守卫（AoT S01E26 → S04E26 误绑教训）：本地 (季,集) 超出 TMDB
            # 该季集数、且文件不是绝对编号解析时，不查跨季 abs_index——静默绑到毫不
            # 相干的另一季不如标待确认（沿用电影侧“不像不绑”门）。同季合理（fits）
            # 与绝对号解析的行不受影响。
            cross_ok = fits or e.get("absolute_number") is not None
            local_before = sum(c for sn, c in local_counts.items() if sn < season)
            cands: list[int] = []
            if not cross_ok:
                continue  # 直接落到后面的 needs_review=1（不占用 claimed）
            if fits:
                cands.append(sum(c for sn, c in offsets if sn < season) + episode)
            cands.append(local_before + episode)
            cands.append(episode)
            target = None
            for cand in cands:
                target = abs_index.get(cand)
                if target is not None:
                    break
            if target is None and not fits:
                m = map_absolute(episode, offsets)
                target = season_index.get(m) if m else None
            if target is None:
                continue
            try:
                key = (int(target.get("season_number") or 0),
                       int(target.get("episode_number") or 0))
            except (TypeError, ValueError):
                continue
            if key in claimed:
                continue
            claimed.add(key)
            _apply(e, target)
        # 仍无 TMDB 集的本地行标待确认（TMDB 集号口径不一致：老友记 E24/25、
        # 进击 S04E85/S05、黑镜特典等）。本次已匹配与手工绑定的行（有 tmdb_episode_id）
        # 都不重复标。
        for e in rows:
            if int(e["id"]) in applied_ids:
                continue
            if e.get("tmdb_episode_id") or e.get("local_only"):
                continue
            store.update_episode_meta(int(e["id"]), needs_review=1)
    return {"show_id": show_id, "tmdb_id": tmdb_id, "seasons": len(seasons),
            "episodes_matched": matched, "remapped": remapped,
            "offline": bool(offline_reason), "offline_reason": offline_reason}


def write_media_files(show_id: int, library_id=None, thumbs: bool = False,
                      episode_nfo: bool | None = None) -> dict:
    """NFO + 海报落 NAS（T3，幂等失败自吞）：`tvshow.nfo`/季 NFO/每集 `.nfo` +
    （`artwork_mode=nfo_art` 时）`poster.jpg`/`fanart.jpg`/`seasonNN-poster.jpg`。"""
    from .. import artwork, storage
    from . import tv_nfo_link
    try:
        backend = storage.backend_for(int(library_id or 0) or store.DEFAULT_LIBRARY_ID)
    except Exception as e:
        logger.debug("tv media backend resolve failed show=%s: %s", show_id, e)
        return {"nfo": False, "nfo_wrote": 0, "nfo_skipped": 0,
                "artwork": {"ok": False, "reason": "no_backend"}}
    nfo = tv_nfo_link.sync_tv_nfos_for(int(show_id), backend=backend,
                                       episode_nfo=episode_nfo)
    art = artwork.write_for_show(int(show_id), backend=backend, thumbs=thumbs)
    return {"nfo": bool(nfo.get("ok")),
            "nfo_wrote": len(nfo.get("wrote") or []),
            "nfo_skipped": len(nfo.get("skipped") or []),
            "nfo_failed": len(nfo.get("failed") or []),
            "artwork": art}


def _detail_with_fallback(tmdb_id: int) -> tuple[dict, str]:
    """detail 拉取；网络失败回落 tmdb_cache.payload_json（离线重放）。"""
    try:
        return tmdb.tv_detail(int(tmdb_id)), ""
    except Exception as e:
        cached = store.get_tmdb_cached(int(tmdb_id), "tv")
        payload = {}
        if cached:
            try:
                payload = json.loads(cached.get("payload_json") or "{}")
            except (TypeError, ValueError):
                payload = {}
        if payload.get("id"):
            logger.warning("tv detail offline fallback tmdb_id=%s: %s", tmdb_id, e)
            return payload, str(e)[:200]
        raise


def _apply_external_candidate(show: dict, cand, needs_review: int) -> dict | None:
    """外源候选（wikidata/tvmaze/bgm/nfo）→ detail → 剧/季/集落库。

    成功返回与 `scrape_show` 同形结果（status=ok_external）；失败返回 None
    （调用方继续原 TMDB/离线链路）。"""
    from ..metadata import chain as meta_chain
    from ..metadata import external as meta_external
    detail = meta_chain.detail_for(cand)
    if not detail or not detail.get("title"):
        return None
    show_id = int(show["id"])
    try:
        out = meta_external.apply_external_show(
            show_id, detail, source=cand.source, source_id=cand.source_id,
            library_id=show.get("library_id"), needs_review=int(needs_review or 0),
            set_tmdb_id=False, write_nfo=False)
    except Exception as e:
        logger.warning("external show apply failed show=%s src=%s: %s",
                       show_id, cand.source, e)
        return None
    media = write_media_files(show_id, show.get("library_id"))
    return {"show_id": show_id, "status": "ok_external",
            "title": show.get("title") or "", "tmdb_id": out.get("tmdb_id"),
            "match_source": cand.source,
            "needs_review": int(needs_review or 0),
            "episodes_filled": out.get("episodes_filled", 0), "media": media}


def _nfo_show_candidate(show: dict):
    """同目录 `tvshow.nfo` → Candidate（离线回放，source='nfo'；无文件返回 None）。"""
    from .. import storage
    from ..metadata import nfo_import as meta_nfo
    from ..metadata.base import Candidate
    from ..metadata.external import detail_from_nfo_tvshow
    from . import tv_nfo_link
    show_id = int(show["id"])
    lid = int(show.get("library_id") or 0) or store.DEFAULT_LIBRARY_ID
    try:
        backend = storage.backend_for(lid)
    except Exception:
        return None
    dir_rel = ""
    for ep in store.list_episodes(show_id) or []:
        rel = str(ep.get("file_path") or "")
        if rel:
            dir_rel = tv_nfo_link.show_dir_of(rel)
            break
    if not dir_rel:
        return None
    rel = f"{dir_rel}/tvshow.nfo"
    try:
        data = backend.read(rel)
    except Exception:
        return None
    parsed = meta_nfo.parse_tvshow_bytes(data)
    if not parsed:
        return None
    parsed["_nfo_name"] = "tvshow.nfo"
    detail = detail_from_nfo_tvshow(parsed)
    return Candidate(title=parsed.get("title") or "",
                     original_title=parsed.get("original_title") or "",
                     year=parsed.get("year"), tmdb_id=parsed.get("tmdb_id"),
                     imdb_id=parsed.get("imdb_id") or "", source="nfo",
                     source_id=rel, score=60.0,
                     payload={"detail": detail})


def scrape_show(show: dict, force: bool = False, download_art: bool = True) -> dict:
    """刮削单剧：匹配（如未绑定）→ detail → 季详情 → 落库。"""
    show_id = int(show["id"])
    title = show.get("title") or ""
    if not force and int(show.get("fetched_at") or 0) > 0 and show.get("tmdb_id"):
        return {"show_id": show_id, "status": "skipped_cached", "title": title}
    tmdb_id = show.get("tmdb_id")
    source = show.get("match_source") or ""
    needs_review = int(show.get("needs_review") or 0)
    cand = None
    if not tmdb_id:
        # 仅当 tvdb/imdb 来自目录 hint（match_source=hint）时才作为匹配提示；
        # 刮削回填的 tvdb_id 属于上一条匹配，换绑时必须忽略（防错配残留）。
        hints = ({"tvdb": show.get("tvdb_id"), "imdb": show.get("imdb_id")}
                 if str(show.get("match_source") or "") == "hint" else {})
        # force 重解析：不采信库里可能来自上一条错配的年份，只用标题+热度
        res = tv_match.resolve_show(title, None if force else show.get("year"), hints,
                                    use_library=not force,
                                    library_id=show.get("library_id"))
        cand = res.get("candidate")
        if not res.get("tmdb_id") and cand is None:
            # 无外源命中：同目录 tvshow.nfo 离线回放（P1.1 TV）
            nfo_c = _nfo_show_candidate(show)
            if nfo_c is not None:
                ext = _apply_external_candidate(show, nfo_c, 0)
                if ext is not None:
                    return ext
            return {"show_id": show_id, "status": "no_match", "title": title,
                    "query": res.get("query") or title}
        tmdb_id = int(res["tmdb_id"]) if res.get("tmdb_id") else 0
        source = res.get("source") or "tmdb"
        needs_review = int(res.get("needs_review") or 0)
        detail = res.get("detail")
    else:
        detail = None
    # 无 key 外源直落（P2.4）：没有 tmdb_id 或没有 TMDB 缓存时，直接用外部 detail
    if cand is not None and (not tmdb_id
                             or not store.get_tmdb_cached(int(tmdb_id), "tv")):
        ext = _apply_external_candidate(show, cand, needs_review)
        if ext is not None:
            return ext
    if not tmdb_id:
        # 外源详情不可用且无 tmdb_id：tvshow.nfo 兜底，再没有就 no_match（不调 /tv/0）
        nfo_c = _nfo_show_candidate(show)
        if nfo_c is not None:
            ext = _apply_external_candidate(show, nfo_c, needs_review)
            if ext is not None:
                return ext
        return {"show_id": show_id, "status": "no_match", "title": title,
                "query": title}
    if detail is None:
        try:
            detail, offline_reason = _detail_with_fallback(int(tmdb_id))
        except Exception:
            # TMDB 不可用且外源此前未试（已绑定剧 force 重刮）：最后兜底外源/NFO
            if cand is None:
                c2, r2, _w = tv_match._external_show_candidate(
                    title, None if force else show.get("year"),
                    show.get("library_id"))
                if c2 is None:
                    c2 = _nfo_show_candidate(show)
                if c2 is not None:
                    needs_review = max(needs_review, int(r2 or 0))
                    ext = _apply_external_candidate(show, c2, needs_review)
                    if ext is not None:
                        return ext
            raise
    else:
        offline_reason = ""
    local = store.list_episodes(show_id)
    season_details: dict = {}
    aggregate: dict | None = None
    if offline_reason:
        # v27：离线用已缓存季详情回放集级元数据（此前只能拿到剧级）
        try:
            season_details = store.get_tmdb_cache_seasons(int(tmdb_id))
        except Exception as e:
            logger.debug("offline seasons load failed tmdb_id=%s: %s", tmdb_id, e)
    if not offline_reason:
        for sn in wanted_seasons(detail, local):
            try:
                season_details[sn] = tmdb.tv_season(int(tmdb_id), sn)
            except Exception as e:
                logger.warning("tv season fetch failed tmdb_id=%s season=%s: %s",
                               tmdb_id, sn, e)
        # 全剧聚合演职（单季 credits 官方仅为最新季，不可用；失败回退 detail 内嵌）
        try:
            aggregate = tmdb.tv_aggregate_credits(int(tmdb_id))
        except Exception as e:
            logger.warning("tv aggregate credits failed tmdb_id=%s: %s", tmdb_id, e)
            aggregate = None
    stats = apply_tv_detail(show_id, detail, season_details, source=source,
                            needs_review=needs_review, download_art=download_art,
                            offline_reason=offline_reason,
                            library_id=show.get("library_id"),
                            aggregate=aggregate)
    status = "ok_offline" if offline_reason else "ok"
    media = write_media_files(show_id, show.get("library_id"))
    return {"show_id": show_id, "status": status, "title": title,
            "tmdb_id": int(tmdb_id), "match_source": source,
            "needs_review": needs_review, "media": media,
            **{k: v for k, v in stats.items()
               if k in ("seasons", "episodes_matched", "remapped")}}


def backfill_person_names(library_ids=None, ids=None, progress_cb=None,
                          should_stop=None) -> list[dict]:
    """离线回填 `person_names`：人名串为空 + 有 TMDB 绑定 + 缓存 credits 非空的剧。

    背景：`person_names` 列是后加的，存量已刮削剧（`fetched_at>0`）会被普通刮削
    跳过，导致人名搜索/联想长期无数据。本函数纯读缓存、不触网。
    返回与 `scrape_pending` 同形的行（status=`ok_backfilled`/`no_cache_credits`），
    供任务汇总直接计入。"""
    shows = store.list_shows_for_scrape(library_ids, ids=ids, force=True)
    targets = [s for s in shows
               if s.get("tmdb_id") and not (s.get("person_names") or "").strip()]
    out: list[dict] = []
    total = len(targets)
    for i, show in enumerate(targets):
        if should_stop and should_stop():
            logger.info("tv person backfill cancelled: %s/%s", i, total)
            break
        sid = int(show["id"])
        try:
            cached = store.get_tmdb_cached(int(show["tmdb_id"]), "tv")
            credits = (cached or {}).get("credits") or {}
            season_names = []
            for _s in store.list_seasons(sid):
                for _c in _s.get("cast") or []:
                    if isinstance(_c, dict) and _c.get("name"):
                        season_names.append(_c["name"])
            names = person_names_from_credits(credits, show.get("created_by"),
                                              season_names)
            if not names:
                r = {"show_id": sid, "status": "no_cache_credits",
                     "title": show.get("title") or ""}
            else:
                store.update_show_meta(sid, person_names=names)
                r = {"show_id": sid, "status": "ok_backfilled",
                     "title": show.get("title") or "", "names": names}
        except Exception as e:
            logger.warning("tv person backfill failed show=%s: %s", sid, e)
            r = {"show_id": sid, "status": f"error: {e}",
                 "title": show.get("title") or ""}
        r["library_id"] = show.get("library_id")
        out.append(r)
        if progress_cb:
            try:
                progress_cb(i + 1, total)
            except Exception as e:
                logger.debug("progress_cb failed: %s", e)
    return out


def scrape_pending(library_ids=None, ids=None, force: bool = False,
                   download_art: bool = True, progress_cb=None,
                   should_stop=None) -> list[dict]:
    """批量刮削（jobkit/手动）：未匹配或未刮过的剧；force=True 全量重刮。

    存量已刮剧的人名串回填不在此做（`backfill_person_names`，任务层另行调用，
    进度独立一段）。"""
    shows = store.list_shows_for_scrape(library_ids, ids=ids, force=force)
    out: list[dict] = []
    total = len(shows)
    for i, show in enumerate(shows):
        if should_stop and should_stop():
            logger.info("tv scrape cancelled: %s/%s", i, total)
            break
        try:
            r = scrape_show(show, force=force, download_art=download_art)
        except Exception as e:
            logger.warning("tv scrape failed show=%s title=%s: %s",
                           show.get("id"), show.get("title"), e)
            r = {"show_id": show.get("id"), "status": f"error: {e}",
                 "title": show.get("title")}
        r["library_id"] = show.get("library_id")
        out.append(r)
        if progress_cb:
            try:
                progress_cb(i + 1, total)
            except Exception as e:
                logger.debug("progress_cb failed: %s", e)
    return out
