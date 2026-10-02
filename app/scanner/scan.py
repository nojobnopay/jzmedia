"""scanner.scan（自 app/scanner.py 拆分，评审 B9/R03-Q1；对外经 app.scanner 门面使用）。

多库 v12：所有入库/状态写入携带 `library_id`；`scan_all` 默认遍历全部启用库，
也可只扫单库（后台任务的 library_id 参数）。TV 库本期只读清单（F 阶段接 tv 表），
当前沿用剧集跳过语义。
"""
import os
from .. import library_paths
from .. import storage
from .. import store
from .. import tmdb
from ..db import ensure_dirs
from ..log import get_logger
from ..library_mutex import library_mutation_lock
logger = get_logger("scanner.scan")
from .classify import (is_sample, is_sidecar, is_extras_dir, extra_kind,
                       strip_kind_affix, scan_skip_dirs, VIDEO_EXTS)
from .parse import parse_filename, normalize_title
from . import tv_parse
from .match import search_with_fallback
from .persist import apply_cached_to_movie, apply_tmdb_detail
from ..metadata import local as meta_local
from ..metadata import nfo_import as meta_nfo
from ..metadata import external as meta_external
_EXTERNAL_MATCH_SOURCES = meta_external.EXTERNAL_SOURCES
__all__ = ['ST_SKIPPED_SAMPLE', 'ST_EXTRA_ATTACHED', 'ST_EXTRA_ORPHAN', 'ST_NO_MATCH', 'ST_EPISODE', 'ST_SCAN_FAILED', 'ST_LIBRARY_OFFLINE', 'ST_EXTERNAL', 'attribute_extra', 'attribute_extra_file', 'scan_one', 'scan_file', 'scan_tv_one', 'scan_tv_file', 'scan_tv_show', 'scan_all', 'resolve_tv_numbers', 'tv_plan']

DEFAULT_LIBRARY_ID = library_paths.DEFAULT_LIBRARY_ID

ST_SKIPPED_SAMPLE = "skipped_sample"


ST_EXTRA_ATTACHED = "extra_attached"


ST_EXTRA_ORPHAN = "extra_orphan"


ST_NO_MATCH = "no_match"

# 刮削出错（TMDB 网络/接口异常）：行已入库、可重试（评审 B9/R03-Q1 后续）
ST_SCAN_FAILED = "scan_failed"


ST_EPISODE = "skipped_episode_v1"

# 远程库不可达：本次跳过该库，绝不删行/GC（指导 §19 Offline ≠ Deleted）
ST_LIBRARY_OFFLINE = "library_offline"

# 离线/无 token 外源落库（NFO/Wikidata/TVmaze/Bangumi）：行可播可显示，
# 联网后 force 重扫/手动匹配可升级为 TMDB 元数据（tmdb_id 保持 NULL 不阻塞升级）
ST_EXTERNAL = "ok_external"


def _lib_id(library_id) -> int:
    try:
        return int(library_id) if library_id is not None else DEFAULT_LIBRARY_ID
    except (TypeError, ValueError):
        return DEFAULT_LIBRARY_ID


def attribute_extra(abs_path: str, library_id=None) -> dict:
    """本地路径入口（上传/复制等本地写路径）；内部转 backend+rel。"""
    lib_id = _lib_id(library_id)
    rel = os.path.relpath(abs_path, library_paths.library_root(lib_id))
    return attribute_extra_file(storage.backend_for(lib_id), rel)


def attribute_extra_file(backend, rel: str) -> dict:
    """花絮归属：解析标题/年份 → 库内标题/原标题匹配（年份±1，种类词前后缀剥掉再试一轮）
    → extras 表幂等记录。历史 orphan 在正片后入库/匹配修好后重扫自动补归属
    （已有归属的不碰，手工认领优先）。
    样片永不归属（仅返回 skipped_sample）。返回 {file, status, movie_id?, kind}。"""
    lib_id = backend.library_id or DEFAULT_LIBRARY_ID
    rel = backend.norm(rel)
    base = os.path.basename(rel)
    if is_sample(base):
        return {"file": rel, "status": ST_SKIPPED_SAMPLE}
    kind = extra_kind(rel)
    if kind == "sample":
        # 样片片段：只认不收（永不归属、不入库、不搬迁）
        return {"file": rel, "status": ST_SKIPPED_SAMPLE}
    parsed = parse_filename(base)
    title = normalize_title(parsed.get("title") or "")
    # 花絮文件名常带原标题（如 Making of おもひでぽろぽろ）：归一后直比；
    # 文件名无意义时（clip/etc）逐级退回祖先目录名（如 Plex 树的 Movie/Other/etc.mkv）
    cands = [(title, parsed.get("year"))]
    parts = rel.replace("\\", "/").split("/")[:-1]
    for anc in reversed(parts):
        if not anc or anc in (".",):
            continue
        try:
            pp = parse_filename(anc)
            t = normalize_title(pp.get("title") or "")
            if t:
                cands.append((t, pp.get("year")))
        except Exception:
            continue
    hit, mid = None, None
    expanded = list(cands)
    for t, y in cands:
        st = strip_kind_affix(t) if t else ""
        if st and normalize_title(st) != normalize_title(t or ""):
            expanded.append((normalize_title(st), y))
    for t, y in expanded:
        if t and (hit := store.find_movie_for_extra(t, y, library_id=lib_id)):
            mid = hit["id"]
            break
    # 已入库（搬迁改路径）先按 basename 认领，避免删建抖动
    claimed = store.repath_extra_by_basename(base, rel, mid, kind, library_id=lib_id)
    if claimed is None:
        store.upsert_extra(rel, mid, kind, library_id=lib_id)
    elif mid and not claimed.get("movie_id"):
        # 历史 orphan：正片后入库/匹配修好后重扫自动补归属（已有归属的不碰，手工认领优先）
        try:
            store.upsert_extra(rel, mid, kind, library_id=lib_id)
        except Exception as e:
            logger.debug("re-attribute extra failed file=%s: %s", rel, e)
    if mid:
        return {"file": rel, "status": ST_EXTRA_ATTACHED, "movie_id": mid,
                "kind": kind, "title": hit.get("title", "")}
    return {"file": rel, "status": ST_EXTRA_ORPHAN, "kind": kind}


def _persist_local_extras(mid: int, parsed: dict, cur: dict) -> None:
    """版本/规格后缀持久化：重扫不覆盖手工改过的值（非空保留）。"""
    local: dict = {}
    if not cur.get("edition") and parsed.get("edition"):
        local["edition"] = parsed["edition"]
    if not cur.get("spec") and parsed.get("spec"):
        local["spec"] = parsed["spec"]
    if local:
        store.update_movie_local(mid, **local)


def scan_one(abs_path: str, force: bool = False, tmdb_hint: int | None = None,
             library_id=None) -> dict:
    """本地路径入口（上传/复制/重扫等本地写路径）；内部转 backend+rel。"""
    lib_id = _lib_id(library_id)
    rel = os.path.relpath(abs_path, library_paths.library_root(lib_id))
    return scan_file(storage.backend_for(lib_id), rel, force=force, tmdb_hint=tmdb_hint)


def scan_file(backend, rel: str, force: bool = False,
              tmdb_hint: int | None = None, local_index: dict | None = None,
              entry=None) -> dict:
    """单文件入库（backend+库内相对路径；本地/远程直读统一）。
    force=True（手动重试）跳过增量跳过与缓存短路，重新刮削。
    tmdb_hint（复制文件时来自源行）：缓存命中则直绑该 tmdb_id，避免重搜与误配。
    local_index（扫描批次共享）：跨库已匹配索引——本地优先绑定，免网络且防错配。
    entry（iter_tree 的 WalkEntry）：带 size/mtime，扫描时免每片一次 stat 往返。
    NFO 导入与媒体目录落盘：远程直读无 POSIX 路径时经 backend 适配器执行。"""
    lib_id = backend.library_id or DEFAULT_LIBRARY_ID
    rel = backend.norm(rel)
    abs_path = backend.abs_path(rel) or ""
    if is_sidecar(rel, backend=backend):
        if is_sample(os.path.basename(rel)):
            return {"file": rel, "status": "skipped_sample"}
        return attribute_extra_file(backend, rel)
    cached = store.get_by_path(rel, library_id=lib_id)
    if cached and not force:
        if cached.get("tmdb_id"):
            return {"file": rel, "status": "skipped_cached", "title": cached.get("title")}
        if str(cached.get("match_source") or "") in _EXTERNAL_MATCH_SOURCES:
            # 外源已落库（NFO/无 key API）：增量短路，force 或手动匹配可升级 TMDB
            return {"file": rel, "status": "skipped_external", "title": cached.get("title")}
    # 增量跳过（评审 B9/R03-Q3）：未匹配/剧集行且文件 mtime+size 未变 → 不再重打 TMDB。
    # entry 由 iter_tree 顺带返回（每目录一次 list），远程库不再逐片 stat。
    if entry is not None:
        mtime, size = int(entry.mtime), int(entry.size)
    else:
        try:
            st = backend.stat(rel)
            mtime, size = int(st.mtime), int(st.size)
        except storage.StorageError:
            mtime, size = 0, 0
    prev = store.get_scan_state(rel, library_id=lib_id)
    if (not force and prev is not None and mtime and size
            and int(prev.get("mtime") or 0) == mtime
            and int(prev.get("size") or 0) == size
            and str(prev.get("status") or "") in (ST_NO_MATCH, ST_EPISODE,
                                                  ST_SCAN_FAILED, ST_EXTERNAL)):
        return {"file": rel, "status": "skipped_unchanged"}
    parsed = parse_filename(os.path.basename(rel))
    parsed["title"] = normalize_title(parsed["title"])
    if parsed["type"] == "episode":
        # V1 仅电影：剧集不入库（评审 P1-03：旧实现建行会让剧集出现在海报墙/统计里，
        # 与 README“剧集跳过”不符）。旧版本产生的剧集脏行可手工删行后重扫。
        store.set_scan_state(rel, mtime, size, ST_EPISODE, library_id=lib_id)
        return {"file": rel, "status": ST_EPISODE}
    # 先建行（评审 B9 后续）：刮削失败也要让影片在海报墙/匹配确认可见，绝不“消失在文件浏览里”
    mid = store.upsert_movie_by_path(rel, library_id=lib_id)
    patch: dict = {"year": parsed["year"]}
    if not (cached or {}).get("title"):
        # 文件名解析标题先用于失败/未匹配时可见；title_auto=1 允许后续 TMDB 标题覆盖
        patch["title"] = parsed["title"]
        patch["title_auto"] = 1
    try:
        store.update_movie_meta(mid, **patch)
    except Exception as e:
        logger.warning("persist parsed meta failed mid=%s: %s", mid, e)
    try:
        cur = store.get_movie(mid) or {}
    except Exception:
        cur = {}
    _persist_local_extras(mid, parsed, cur)
    m, used_q, year_mismatch = None, parsed["title"], False
    offline_review = 0
    match_source = "tmdb"
    online_error = ""
    if tmdb_hint and store.get_tmdb_cached(int(tmdb_hint)):
        # 复制场景：源片已匹配 → 直绑，避免重搜错配（评审 B9 后续）
        m = {"id": int(tmdb_hint)}
    if m is None:
        # 本地优先（2026-09）：其他库/其他版本已有同名片（归一标题+年份）→ 直接复用，
        # 零网络且避免 TMDB 模糊搜索错配；无本地数据再走远端搜索。
        # 查询键含文件名/父目录里的中文段（告白.Confessions.2010 → 告白），
        # 弥补 guessit 只认英文；年份优先取父目录（'...(2010)'）。
        # force 重扫同样先走本地：这才是"用本地库数据纠正远程库错配"的路径
        # （要换到别的 TMDB id 请用手动匹配）。
        try:
            index = (local_index if local_index is not None
                     else meta_local.library_index())
            loc_year = meta_local.parse_path_year(rel, parsed["year"])
            for cand_title in meta_local.parse_path_variants(rel):
                hit = meta_local.library_hit(cand_title, loc_year, index=index)
                if hit is not None and hit.tmdb_id:
                    break
            else:
                hit = None
        except Exception as e:
            hit = None
            logger.debug("local-first lookup failed file=%s: %s", rel, e)
        if hit is not None and hit.tmdb_id:
            m = {"id": int(hit.tmdb_id)}
            match_source = hit.source or "library"
            online_error = ""
            logger.info("本地优先匹配 file=%s -> tmdb=%s source=%s", rel,
                        hit.tmdb_id, match_source)
    # 换绑纠正判定：目标 tmdb 与当前不同，或当前标题正是旧 tmdb 的缓存标题
    #（= 之前错配自动写入的标题）→ 用新缓存标题覆盖，防错配标题残留。
    force_title = False
    if m:
        try:
            force_title = int((cached or {}).get("tmdb_id") or 0) != int(m.get("id") or 0)
        except (TypeError, ValueError):
            force_title = True
        if not force_title and (cached or {}).get("tmdb_id"):
            old_cache = store.get_tmdb_cached(int(cached["tmdb_id"])) or {}
            force_title = bool(old_cache.get("title")
                               and old_cache["title"] == (cached.get("title") or ""))
    if m is None:
        try:
            m, used_q, year_mismatch = search_with_fallback(parsed["title"], parsed["year"])
        except Exception as e:
            online_error = str(e)[:200]
            logger.warning("tmdb search failed file=%s: %s", rel, e)
    if m is None:
        # 离线/降级兜底（E/Phase 1）：NFO 完整回放 → match_index 本地匹配 → 外源链（P2.4）
        parsed_nfo = None
        try:
            parsed_nfo = (meta_nfo.read_any_for(abs_path) if abs_path
                          else meta_nfo.read_any_for_backend(backend, rel))
        except Exception as e:
            logger.debug("nfo parse failed file=%s: %s", rel, e)
        if parsed_nfo and parsed_nfo.get("_kind") == "movie":
            nfo_c = meta_nfo.candidate_from_movie(parsed_nfo, source_id=rel)
            if nfo_c.tmdb_id and store.get_tmdb_cached(int(nfo_c.tmdb_id)):
                m = {"id": int(nfo_c.tmdb_id)}
                match_source = "nfo"
                used_q = parsed["title"]
                year_mismatch = False
                logger.info("NFO 缓存命中 file=%s -> tmdb=%s", rel, nfo_c.tmdb_id)
            else:
                # NFO 带完整元数据：离线首见片直接落库（tmdb_id 留空，联网后可升级）
                try:
                    out = meta_external.apply_nfo_movie(
                        mid, parsed_nfo, backend=backend, rel=rel,
                        abs_path=abs_path)
                    store.set_scan_state(rel, mtime, size, ST_EXTERNAL,
                                         library_id=lib_id)
                    logger.info("NFO 离线落库 file=%s title=%s", rel,
                                out.get("title"))
                    return {"file": rel, "status": ST_EXTERNAL, "movie_id": mid,
                            "match_source": "nfo", **out}
                except Exception as e:
                    logger.warning("NFO 离线落库失败 file=%s: %s", rel, e)
        if m is None:
            offline = None
            try:
                offline = meta_local.best(parsed["title"], parsed["year"])
            except Exception as e:
                logger.debug("offline match failed file=%s: %s", rel, e)
            if offline is not None and offline.tmdb_id:
                m = {"id": int(offline.tmdb_id)}
                match_source = offline.source or "local"
                used_q = parsed["title"]
                year_mismatch = False
                logger.info("离线匹配 file=%s -> tmdb=%s source=%s", rel,
                            offline.tmdb_id, match_source)
        if m is None:
            # 无 key 外部源（P2.4：wikidata/tvmaze/bgm）：过门后直接落外部元数据；
            # 带 tmdb_id 且已有缓存时优先复用 TMDB 缓存（质量更高）
            cand, review, why = None, 0, ""
            try:
                from ..metadata import chain as meta_chain
                from ..metadata import auto as meta_auto
                hits = meta_chain.search(parsed["title"], parsed["year"], "movie",
                                         library_id=lib_id, limit=5,
                                         exclude=("local", "tmdb"))
                cand, review, why = meta_auto.pick_auto(hits, parsed["title"],
                                                        parsed["year"], "movie")
            except Exception as e:
                logger.debug("external chain failed file=%s: %s", rel, e)
            if cand is not None:
                offline_review = int(review or 0)
                if cand.tmdb_id and store.get_tmdb_cached(int(cand.tmdb_id)):
                    m = {"id": int(cand.tmdb_id)}
                    match_source = cand.source or "external"
                    used_q = parsed["title"]
                    year_mismatch = False
                    logger.info("外源桥接缓存 file=%s src=%s -> tmdb=%s",
                                rel, cand.source, cand.tmdb_id)
                else:
                    detail = meta_chain.detail_for(cand)
                    if detail:
                        try:
                            out = meta_external.apply_external_movie(
                                mid, detail, source=cand.source,
                                source_id=cand.source_id, backend=backend,
                                rel=rel, abs_path=abs_path,
                                needs_review=int(review or 0), set_tmdb_id=False)
                            store.set_scan_state(rel, mtime, size, ST_EXTERNAL,
                                                 library_id=lib_id)
                            logger.info("外源离线落库 file=%s src=%s why=%s title=%s",
                                        rel, cand.source, why, out.get("title"))
                            return {"file": rel, "status": ST_EXTERNAL,
                                    "movie_id": mid,
                                    "match_source": cand.source or "external",
                                    **out}
                        except Exception as e:
                            logger.warning("外部元数据落库失败 file=%s src=%s: %s",
                                           rel, cand.source, e)
    if not m:
        if online_error:
            return {"file": rel, "status": ST_SCAN_FAILED, "movie_id": mid,
                    "error": online_error}
        store.set_scan_state(rel, mtime, size, ST_NO_MATCH, library_id=lib_id)
        return {"file": rel, "status": ST_NO_MATCH, "movie_id": mid, "parsed": parsed}
    tmdb_id = int(m["id"])
    try:
        if store.get_tmdb_cached(tmdb_id):
            out = apply_cached_to_movie(mid, tmdb_id, abs_path,
                                        backend=backend, rel=rel,
                                        force_title=force_title)
        else:
            detail = tmdb.movie_detail(tmdb_id)
            out = apply_tmdb_detail(mid, detail, abs_path,
                                    backend=backend, rel=rel)
    except Exception as e:
        logger.warning("tmdb detail/apply failed mid=%s tmdb_id=%s: %s", mid, tmdb_id, e)
        return {"file": rel, "status": ST_SCAN_FAILED, "movie_id": mid,
                "error": str(e)[:200]}
    needs_review = 1 if (used_q != parsed["title"] or year_mismatch) else offline_review
    store.update_movie_local(mid, needs_review=needs_review)
    try:
        store.update_movie_meta(mid, match_source=match_source)
    except Exception as e:
        logger.debug("persist match_source failed mid=%s: %s", mid, e)
    status = "ok" if not needs_review else "ok_needs_review"
    return {"file": rel, "status": status, "query": used_q,
            "match_source": match_source, **out}


def _video_entries(entries) -> list:
    """树遍历结果 → 视频文件项（跳过隐藏文件/非视频）。"""
    return [e for e in entries
            if not e.is_dir and os.path.splitext(e.name)[1].lower() in VIDEO_EXTS]


def _tv_show_root(rel: str, subshows=None) -> str:
    """剧根目录（库内相对）：子剧目录优先（`七龙珠/七龙珠.Z` → 七龙珠.Z），否则顶层目录；
    库根散文件返回 ''（不建剧，避免 `random.mkv` 之类噪声）。"""
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if len(parts) <= 1:
        return ""
    if subshows and len(parts) >= 3:
        d2 = "/".join(parts[:2])
        if d2 in subshows:
            return d2
    return parts[0]


def _tv_subshow_dirs(entries) -> set[str]:
    """子剧目录：剧根下「直接含视频文件、无季子目录、非季/非花絮/非特典/非电影」的目录。
    NAS 实测唯一命中 `七龙珠/{A,Z,GT}`（同一目录放了 3 部独立剧）。"""
    info: dict[str, dict] = {}

    def _node(rel: str) -> dict:
        return info.setdefault(rel, {"has_video": False, "season_child": False})

    for e in entries:
        parent = os.path.dirname(e.rel)
        if parent:
            node = _node(parent)
            if e.is_dir:
                if tv_parse.season_from_dir(e.name) is not None:
                    node["season_child"] = True
            elif os.path.splitext(e.name)[1].lower() in VIDEO_EXTS:
                node["has_video"] = True
        if e.is_dir:
            _node(e.rel)
    out = set()
    for d, n in info.items():
        parts = d.split("/")
        if len(parts) != 2 or not n["has_video"] or n["season_child"]:
            continue
        name = parts[1]
        if tv_parse.season_from_dir(name) is not None:
            continue
        if (tv_parse.is_tv_special_dir(name) or tv_parse.is_tv_movie_dir(name)
                or is_extras_dir(name)):
            continue
        out.add(d)
    return out


def _tv_special_hints(vids) -> dict[str, int]:
    """无编号特典（`Legal.High.SP`、单文件 OVA）按目录内路径序编号。"""
    groups: dict[str, list[str]] = {}
    for e in vids:
        p = tv_parse.parse_episode(os.path.basename(e.rel))
        if p.get("special") and p.get("episode") is None:
            groups.setdefault(os.path.dirname(e.rel), []).append(e.rel)
    hints: dict[str, int] = {}
    for rels in groups.values():
        for i, r in enumerate(sorted(rels), 1):
            hints[r] = i
    return hints


def resolve_tv_numbers(base: str, parent: str, show_root: str,
                       episode_hint=None) -> dict:
    """纯解析（不触 DB）：tv_parse 规则阶梯 + 季目录回退 + guessit 兜底 + 季号守卫。
    返回 {ok, season, episode, episode_end, absolute, special, source, guess, parsed}。
    `scripts/tv_parse_report.py` 与 scan_tv_file 共用，保证报告与入库口径一致。"""
    parsed = tv_parse.parse_episode(base)
    season, episode = parsed.get("season"), parsed.get("episode")
    from_dir = tv_parse.season_from_dir(parent)
    if season is None:
        season = from_dir
    if season is None and parsed.get("special"):
        season = 0
    guess = None
    if season is None or episode is None:
        guess = parse_filename(base)
        try:
            # 裸数字（绝对集号）时 guessit 的季号不可信（`0001` → S0E1、`0100` → S1E0），
            # 此时季号由目录/默认 S1 决定，绝不让 guessit 覆盖。
            if (season is None and guess.get("season") is not None
                    and parsed.get("source") != "bare"):
                season = int(guess["season"])
            if episode is None and guess.get("episode") is not None:
                episode = int(guess["episode"])
        except (TypeError, ValueError):
            pass
        if season is not None and (season > 99 or 1900 <= season <= 2099):
            season = None   # guessit 把年份当季号（`武林外传…2006.EP01`）
    if season is None and episode is not None and show_root:
        season = 1          # 剧根/子剧目录下的裸集号 → 第 1 季
    if episode is None and parsed.get("special") and episode_hint:
        episode = int(episode_hint)
    absolute = (episode if parsed.get("absolute") and parsed.get("season") is None
                and from_dir is None and not parsed.get("special") else None)
    return {"ok": season is not None and episode is not None,
            "season": season, "episode": episode,
            "episode_end": int(parsed.get("episode_end") or 0),
            "absolute": absolute, "special": bool(parsed.get("special")),
            "source": parsed.get("source") or "", "guess": guess, "parsed": parsed,
            "explicit_season": parsed.get("season") if parsed.get("season") is not None else from_dir}


def tv_plan(entries, vids) -> dict:
    """扫描计划（入库与诊断报告共用）：{rel: {show_root, episode_hint}}。"""
    subshows = _tv_subshow_dirs(entries)
    hints = _tv_special_hints(vids)
    return {e.rel: {"show_root": _tv_show_root(e.rel, subshows),
                    "episode_hint": hints.get(e.rel)} for e in vids}


def _tv_in_movie_dir(rel: str) -> bool:
    """路径是否位于剧库内的电影目录（Movies/剧场版/真人版…）。"""
    parts = rel.replace("\\", "/").split("/")[:-1]
    return any(tv_parse.is_tv_movie_dir(p) for p in parts)


def scan_tv_one(abs_path: str, library_id=None) -> dict:
    """本地路径入口（TV）；内部转 backend+rel。"""
    lib_id = _lib_id(library_id)
    rel = os.path.relpath(abs_path, library_paths.library_root(lib_id))
    return scan_tv_file(storage.backend_for(lib_id), rel)


def scan_tv_file(*args, **kwargs):
    # A confirmation cannot land between reading a rule and updating its episode.
    # Never use the global store lock here: classification can touch a slow
    # SMB/NFS directory and would stall unrelated database-only pages.
    from ..library_mutex import library_mutation_lock
    backend = args[0] if args else kwargs.get("backend")
    lib_id = int(getattr(backend, "library_id", 0) or DEFAULT_LIBRARY_ID)
    with library_mutation_lock(lib_id):
        return _scan_tv_file(*args, **kwargs)


def _scan_tv_file(backend, rel: str, entry=None, show_root=None,
                 episode_hint=None, force: bool = False,
                 target_show_id: int | None = None) -> dict:
    """TV 入库：规则阶梯解析（tv_parse）→ tv_shows/tv_seasons/tv_episodes。
    - 重扫增量：mtime/size 未变且上次 tv_ok → skipped_unchanged（force=True 强制重解析）；
    - 特典（SP/OVA/OAD）入 Season 0；一文件多集记 episode_end；绝对集号记 absolute_number；
    - 不刮削/不改名/不写 NFO（T2 起由 tv_persist 回填元数据）。
    entry（iter_tree 的 WalkEntry）带 size/mtime 时免一次 stat。"""
    lib_id = backend.library_id or DEFAULT_LIBRARY_ID
    rel = backend.norm(rel)
    base = os.path.basename(rel)
    from ..tv_binding_rules import bound_numbers
    rule = store.tv_binding_for(lib_id, rel)
    if rule:
        target_show_id = int(rule['show_id'])
        show_root = rule['path']
    old_episode = store.get_episode_by_path(rel, lib_id)
    if old_episode and target_show_id is None:
        target_show_id = int(old_episode['show_id'])
    if entry is not None:
        mtime, size = int(entry.mtime), int(entry.size)
    else:
        try:
            st = backend.stat(rel)
            mtime, size = int(st.mtime), int(st.size)
        except storage.StorageError:
            mtime, size = 0, 0
    if is_sample(base):
        store.set_scan_state(rel, mtime, size, "skipped_sample", library_id=lib_id)
        return {"file": rel, "status": "skipped_sample"}
    if is_sidecar(rel, backend=backend, tv=True):
        # 花絮/剧场版登记（T4）：样片只跳过；花絮与剧库内电影挂到剧上可见可播。
        if show_root is None:
            show_root = _tv_show_root(rel)
        if not show_root:
            store.set_scan_state(rel, mtime, size, "skipped_sidecar", library_id=lib_id)
            return {"file": rel, "status": "skipped_sidecar"}
        kind = "movie" if _tv_in_movie_dir(rel) else extra_kind(rel)
        # 归属优先按「剧根已有正片」解析：TMDB 改名后（Breaking Bad → 绝命毒师）
        # 不能按目录名新建重复行；库里还没有集行时才建档，之后正片扫描/刮削复用该行。
        show_id = int(target_show_id) if target_show_id is not None else \
            store.find_show_by_dir_prefix(show_root, lib_id)
        if not show_id:
            sd = tv_parse.parse_show_dir(os.path.basename(show_root))
            title = sd["title"] or os.path.splitext(base)[0]
            show_id = store.upsert_show(lib_id, title, sd.get("year"),
                                        sort_title=normalize_title(title),
                                        hints=sd.get("hints"))
        store.upsert_tv_extra(rel, show_id, kind=kind, library_id=lib_id)
        store.set_scan_state(rel, mtime, size, "extra_tv", library_id=lib_id)
        return {"file": rel, "status": ST_EXTRA_ATTACHED, "show_id": show_id,
                "extra_kind": kind}
    if not force:
        st = store.get_scan_state(rel, lib_id)
        if (st and st.get("status") == "tv_ok"
                and int(st.get("mtime") or 0) == mtime
                and int(st.get("size") or 0) == size):
            return {"file": rel, "status": "skipped_unchanged"}
    if show_root is None:
        show_root = _tv_show_root(rel)
    num = resolve_tv_numbers(base, os.path.basename(os.path.dirname(rel)),
                             show_root, episode_hint)
    num = bound_numbers(num, rule)
    if not num["ok"]:
        store.set_scan_state(rel, mtime, size, "tv_unknown", library_id=lib_id)
        return {"file": rel, "status": "skipped_tv_unknown", "parsed": num["parsed"]}
    sd = (tv_parse.parse_show_dir(os.path.basename(show_root)) if show_root
          else {"title": "", "year": None, "hints": {}})
    title = sd["title"] or (num["parsed"].get("title")
                            or os.path.splitext(base)[0]).strip()
    if not title:
        store.set_scan_state(rel, mtime, size, "tv_unknown", library_id=lib_id)
        return {"file": rel, "status": "skipped_tv_unknown"}
    show_id = (int(target_show_id) if target_show_id is not None else
               store.upsert_show(lib_id, title, sd.get("year"),
                                 sort_title=normalize_title(title), hints=sd.get("hints")))
    # A keep-numbering rule also protects existing absolute/manual mappings.
    if rule and rule.get('season') is None and old_episode:
        num.update(season=old_episode['season'], episode=old_episode['episode'],
                   episode_end=old_episode['episode_end'], absolute=old_episode['absolute_number'])
    ep_title = str((num["guess"] or {}).get("episode_title") or "")
    ep_id = store.upsert_episode(show_id, lib_id, rel, num["season"], num["episode"],
                                 ep_title, episode_end=num["episode_end"],
                                 absolute_number=num["absolute"])
    if rule:
        conflict = bool(num.get('binding_conflict'))
        store.update_episode_meta(ep_id, absolute_number=num['absolute'],
                                  binding_conflict=int(conflict))
        if conflict:
            store.update_episode_meta(ep_id, needs_review=1)
        elif not old_episode:
            store.update_episode_meta(ep_id, needs_review=1)
    store.set_scan_state(rel, mtime, size, "tv_ok", library_id=lib_id)
    return {"file": rel, "status": "tv_ok", "show_id": show_id, "episode_id": ep_id,
            "show": title, "season": num["season"], "episode": num["episode"],
            "episode_end": num["episode_end"]}


def scan_tv_show(show_id: int, force: bool = False) -> dict:
    """只发现一个已入库剧根下的新视频，不扫描整座视频库，也不做失效 GC。

    详情页在生成整理计划前调用本函数。已存在的集走 scan_state 增量短路；新文件
    显式绑定到当前 show_id，避免剧名被手工修改或 TMDB 本地化后另建重复剧目。
    遍历仍走 StorageBackend，因此本地、挂载和 SMB 直读使用同一语义。
    """
    sid = int(show_id)
    show = store.get_show_meta(sid)
    if not show:
        raise ValueError("show not found")
    lib_id = int(show.get("library_id") or DEFAULT_LIBRARY_ID)
    episodes = store.list_episodes(sid)
    rules = store.list_tv_bindings(lib_id, sid)
    if not episodes and not rules:
        return {"show_id": sid, "library_id": lib_id, "show_dir": "",
                "scanned": 0, "added": 0, "unknown": 0, "results": []}
    from .tv_organize import _resolve_show_dir
    from .tv_nfo_link import show_dirs_for
    show_dir = _resolve_show_dir(episodes, show_id=sid, library_id=lib_id) if episodes else ''
    raw_roots = (set(show_dirs_for(sid, episodes)) | {r['path'] for r in rules}) if rules else {show_dir}
    roots = [p for p in sorted(raw_roots) if not any(p.startswith(other + '/') for other in raw_roots if other != p)]
    if not roots:
        return {"show_id": sid, "library_id": lib_id, "show_dir": "",
                "scanned": 0, "added": 0, "unknown": 0, "results": []}
    backend = storage.backend_for(lib_id)
    storage.clear_meta_cache(lib_id)
    entries = []
    for root in roots:
        entries.extend(backend.iter_tree(root, skip_dirs=scan_skip_dirs()))
    vids = _video_entries(entries)
    hints = _tv_special_hints(vids)
    known = {str(e.get("file_path") or "") for e in episodes}
    results: list[dict] = []
    for entry in vids:
        result = scan_tv_file(
            backend, entry.rel, entry=entry, show_root=show_dir,
            episode_hint=hints.get(entry.rel), force=force,
            target_show_id=sid,
        )
        result["library_id"] = lib_id
        results.append(result)
    added = sum(1 for r in results
                if r.get("status") == "tv_ok" and r.get("file") not in known)
    unknown = sum(1 for r in results if r.get("status") == "skipped_tv_unknown")
    from .tv_persist import backfill_cached_episodes
    filled = backfill_cached_episodes(sid) if rules else 0
    return {"show_id": sid, "library_id": lib_id, "show_dir": show_dir, "directories": roots, "metadata_filled": filled,
            "scanned": len(vids), "added": added, "unknown": unknown,
            "results": results[:100]}


def _scan_libraries(library_id, media_library_id=None) -> list[dict]:
    try:
        if media_library_id is not None:
            mid = int(media_library_id)
            return [l for l in library_paths.list_libraries(only_enabled=True)
                    if int(l.get("media_library_id") or 0) == mid]
        if library_id is None:
            libs = library_paths.list_libraries(only_enabled=True)
            return libs or [library_paths.default_library()]
        lib = library_paths.get_library(library_id)
        return [lib] if lib else []
    except Exception as e:
        logger.warning("resolve scan libraries failed: %s", e)
        return [library_paths.default_library()]


def scan_all(progress_cb=None, should_stop=None, library_id=None,
             force: bool = False, media_library_id=None) -> list[dict]:
    """扫描。`library_id=None` 遍历全部启用视频库；`media_library_id` 只扫该媒体库下的
    全部启用视频库；否则只扫指定视频库。
    遍历/stat 走 StorageBackend：直读远程库不依赖 POSIX 挂载（指导 Phase1）。
    库不可达（StorageOffline）→ `library_offline` 跳过，且**不 GC、不删行**（§19）。
    完整遍历成功的库自动同步删除：电影 `delete_movies_not_in` / 剧集
    `delete_episodes_not_in+prune_empty_shows`（Plex 式删除识别，不改盘上文件名）。
    可选 progress_cb(done, total) 报告全局进度、should_stop() 协作式取消
    （评审 B9/R04-D6：供后台 job 展示进度/取消；取消的库不 GC）。
    返回结果条目带 `library_id`（跨库任务可区分）；GC 以聚合条目
   （`removed_movie/removed_episode/removed_show` + `count`）回传。"""
    ensure_dirs()
    libs = _scan_libraries(library_id, media_library_id)
    if not libs:
        return []
    skip_dirs = scan_skip_dirs()
    out: list[dict] = []
    plans: list[tuple[dict, object, list]] = []
    grand = 0
    for lib in libs:
        lib_id = _lib_id(lib.get("id"))
        try:
            backend = storage.backend_for(lib_id)
        except storage.StorageError as e:
            out.append({"file": "", "library_id": lib_id,
                        "status": f"error: library unavailable: {e}"})
            continue
        change_revision = store.fs_change_summary(lib_id)["revision"]
        storage.clear_meta_cache(lib_id)   # 扫描必须看到磁盘当前状态（不吃 TTL 缓存）
        try:
            backend.stat("")
            entries = list(backend.iter_tree("", skip_dirs=skip_dirs))
        except storage.StorageOffline as e:
            logger.warning("扫描跳过离线库 lib=%s: %s", lib_id, e)
            out.append({"file": "", "library_id": lib_id,
                        "status": ST_LIBRARY_OFFLINE, "error": str(e)[:200]})
            continue
        except storage.StorageError as e:
            out.append({"file": "", "library_id": lib_id,
                        "status": f"error: library root {e}"})
            continue
        vids = _video_entries(entries)
        # 树内全部文件（含花絮/字幕）：花絮行 GC 直接成员判定，免逐行 stat
        tree_files = {e.rel for e in entries if not e.is_dir}
        # TV：子剧目录（七龙珠.Z）+ 无编号特典顺序号，一次遍历内预计算
        ctx = (tv_plan(entries, vids)
               if str(lib.get("kind") or "movie") == "tv" else None)
        plans.append((lib, backend, vids, tree_files, ctx, change_revision))
        grand += len(vids)
    if progress_cb:
        try:
            progress_cb(0, grand)
        except Exception as e:
            logger.debug("progress_cb failed: %s", e)
    # 本地优先索引：整批共享一次构建（跨库已匹配片 → tmdb 绑定）
    try:
        local_index = meta_local.library_index()
    except Exception as e:
        logger.debug("build local index failed: %s", e)
        local_index = {}
    try:
        all_extras = store.list_all_extras()
    except Exception as e:
        logger.debug("list extras failed: %s", e)
        all_extras = []
    done = 0
    for lib, backend, vids, tree_files, ctx, change_revision in plans:
        lib_id = _lib_id(lib.get("id"))
        is_tv = str(lib.get("kind") or "movie") == "tv"
        seen_extras: set[str] = set()
        for e in vids:
            if should_stop and should_stop():
                logger.info("scan cancelled: processed=%s/%s", done, grand)
                return out
            try:
                if is_tv:
                    plan = (ctx or {}).get(e.rel, {})
                    r = scan_tv_file(backend, e.rel, entry=e,
                                     show_root=plan.get("show_root"),
                                     episode_hint=plan.get("episode_hint"),
                                     force=force)
                else:
                    r = scan_file(backend, e.rel, force=force,
                                  local_index=local_index, entry=e)
                r["library_id"] = lib_id
                out.append(r)
                if r.get("status") in (ST_EXTRA_ATTACHED, ST_EXTRA_ORPHAN):
                    seen_extras.add(r["file"])
            except Exception as ex:
                logger.debug("scan failed file=%s: %s", e.rel, ex, exc_info=True)
                out.append({"file": e.rel, "library_id": lib_id,
                            "status": f"error: {ex}"})
            done += 1
            if progress_cb:
                try:
                    progress_cb(done, grand)
                except Exception as ex:
                    logger.debug("progress_cb failed: %s", ex)
        if should_stop and should_stop():
            logger.info("scan cancelled before GC: lib=%s", lib_id)
            return out
        with library_mutation_lock(lib_id):
            if store.fs_change_summary(lib_id)["revision"] != change_revision:
                out.append({"file": "", "library_id": lib_id,
                            "status": "error: files changed during scan; scan again"})
                continue
            if is_tv:
                # TV 失效 GC（仅在一次完整遍历后执行）：删掉磁盘上已不存在的集行/空剧，
                # 级联清理播放断点与探测缓存（库离线在 iter_tree 已整库跳过，绝不误删）。
                try:
                    removed = store.delete_episodes_not_in(tree_files, lib_id)
                    extra_removed = 0   # 剧集花絮/剧场版行 GC
                    for row in all_extras:
                        if int(row.get("library_id") or DEFAULT_LIBRARY_ID) != lib_id:
                            continue
                        if not row.get("show_id") or row["file_path"] in tree_files:
                            continue
                        if store.delete_extra_by_path(row["file_path"], library_id=lib_id):
                            extra_removed += 1
                    repaired = store.reattach_tv_extras(lib_id)
                    pruned = store.prune_empty_shows(lib_id)
                    if removed or pruned or extra_removed or repaired:
                        logger.info("TV GC lib=%s: 失效集 %s / 花絮 %s / 重挂 %s / 空剧 %s",
                                    lib_id, removed, extra_removed, repaired, pruned)
                    if removed:
                        out.append({"file": "", "library_id": lib_id,
                                    "status": "removed_episode", "count": int(removed)})
                    if pruned:
                        out.append({"file": "", "library_id": lib_id,
                                    "status": "removed_show", "count": int(pruned)})
                except Exception as ex:
                    logger.warning("TV GC failed lib=%s: %s", lib_id, ex)
                    out.append({'file': '', 'library_id': lib_id, 'status': f'error: TV reconciliation failed: {ex}'})
                # Confirmed shows may already have cached metadata; fill only newly
                # discovered episodes, without a whole-show refresh or NAS writes.
                from .tv_persist import backfill_cached_episodes
                for sid in {r['show_id'] for r in store.list_tv_bindings(lib_id)}:
                    backfill_cached_episodes(sid)
                continue
            # 电影正片 GC（Plex 式删除识别，仅在一次完整遍历后执行）：磁盘已不存在的行
            # 立刻 purge（含断点/探测/人物关联；海报与 tmdb_cache 保留供重扫复用）。
            # 库离线在上面的 iter_tree 已整库跳过，绝不误删；取消扫描直接 return，不到这里。
            try:
                removed_movies = store.delete_movies_not_in(tree_files, lib_id)
                if removed_movies:
                    logger.info("movie GC lib=%s: 失效影片 %s", lib_id, removed_movies)
                    out.append({"file": "", "library_id": lib_id,
                                "status": "removed_movie", "count": int(removed_movies)})
            except Exception as ex:
                logger.warning("movie GC failed lib=%s: %s", lib_id, ex)
                out.append({'file': '', 'library_id': lib_id, 'status': f'error: movie reconciliation failed: {ex}'})
            # 花絮行 GC（按库分区，仅在一次完整遍历后执行）：文件已不存在的归属记录清掉；
            # 用本次树遍历快照成员判定，免逐行 stat 往返
            # （遍历成功即代表磁盘当前状态；库离线在上面的 iter_tree 已跳过整库）。
            try:
                for row in all_extras:
                    if int(row.get("library_id") or DEFAULT_LIBRARY_ID) != lib_id:
                        continue
                    if row["file_path"] in seen_extras or row["file_path"] in tree_files:
                        continue
                    store.delete_extra_by_path(row["file_path"], library_id=lib_id)
            except Exception as ex:
                logger.debug("extras gc failed lib=%s: %s", lib_id, ex)
                out.append({'file': '', 'library_id': lib_id, 'status': f'error: extras reconciliation failed: {ex}'})
    return out
