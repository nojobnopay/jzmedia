"""scanner.scan（自 app/scanner.py 拆分，评审 B9/R03-Q1；对外经 app.scanner 门面使用）。

多库 v12：所有入库/状态写入携带 `library_id`；`scan_all` 默认遍历全部启用库，
也可只扫单库（后台任务的 library_id 参数）。TV 库本期只读清单（F 阶段接 tv 表），
当前沿用剧集跳过语义。
"""
import os
import re
from .. import library_paths
from .. import storage
from .. import store
from .. import tmdb
from ..db import ensure_dirs
from ..log import get_logger
logger = get_logger("scanner.scan")
from .classify import is_sample, is_sidecar, extra_kind, strip_kind_affix, scan_skip_dirs, VIDEO_EXTS
from .parse import parse_filename, normalize_title
from .match import search_with_fallback
from .persist import apply_cached_to_movie, apply_tmdb_detail
from ..metadata import local as meta_local
from ..metadata import nfo_import as meta_nfo
__all__ = ['ST_SKIPPED_SAMPLE', 'ST_EXTRA_ATTACHED', 'ST_EXTRA_ORPHAN', 'ST_NO_MATCH', 'ST_EPISODE', 'ST_SCAN_FAILED', 'ST_LIBRARY_OFFLINE', 'attribute_extra', 'attribute_extra_file', 'scan_one', 'scan_file', 'scan_tv_one', 'scan_tv_file', 'scan_all']

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
    if cached and cached.get("tmdb_id") and not force:
        return {"file": rel, "status": "skipped_cached", "title": cached.get("title")}
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
            and str(prev.get("status") or "") in (ST_NO_MATCH, ST_EPISODE, ST_SCAN_FAILED)):
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
        # 离线/降级兜底（E 阶段）：NFO 导入 → match_index 本地匹配
        offline = None
        try:
            nfo_c = (meta_nfo.candidates_for(abs_path) if abs_path
                     else meta_nfo.candidates_for_backend(backend, rel))
            if nfo_c is not None and nfo_c.tmdb_id:
                offline = nfo_c
                try:
                    store.upsert_match_entry(
                        "nfo", nfo_c.source_id, "movie", nfo_c.title,
                        nfo_c.original_title, nfo_c.year, nfo_c.tmdb_id,
                        nfo_c.imdb_id, {"file": rel})
                except Exception as e:
                    logger.debug("index nfo candidate failed file=%s: %s", rel, e)
            if offline is None:
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
    needs_review = 1 if (used_q != parsed["title"] or year_mismatch) else 0
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


_SEASON_DIR_RE = re.compile(r"(?i)^(?:season|s)\s*0*(\d{1,3})$")
_SHOW_DIR_RE = re.compile(r"^(.*?)\s*\((\d{4})\)\s*$")


def _tv_show_title(rel: str, fallback: str) -> tuple[str, int | None]:
    """剧名（Plex 树优先最近一级 `剧名 (年份)` 祖先目录，其次顶层目录，最后文件名解析）。"""
    parts = [p for p in rel.replace("\\", "/").split("/")[:-1] if p]
    for p in reversed(parts):
        m = _SHOW_DIR_RE.match(p.strip())
        if m:
            return m.group(1).strip(), int(m.group(2))
    if parts:
        return parts[0].strip(), None
    return (fallback or "").strip(), None


def scan_tv_one(abs_path: str, library_id=None) -> dict:
    """本地路径入口（TV）；内部转 backend+rel。"""
    lib_id = _lib_id(library_id)
    rel = os.path.relpath(abs_path, library_paths.library_root(lib_id))
    return scan_tv_file(storage.backend_for(lib_id), rel)


def scan_tv_file(backend, rel: str, entry=None) -> dict:
    """TV 只读清单入库：解析 SxxEyy → tv_shows/tv_episodes；不刮削/不改名/不写 NFO。
    entry（iter_tree 的 WalkEntry）带 size/mtime 时免一次 stat。"""
    lib_id = backend.library_id or DEFAULT_LIBRARY_ID
    rel = backend.norm(rel)
    base = os.path.basename(rel)
    if entry is not None:
        mtime, size = int(entry.mtime), int(entry.size)
    else:
        try:
            st = backend.stat(rel)
            mtime, size = int(st.mtime), int(st.size)
        except storage.StorageError:
            mtime, size = 0, 0
    if is_sidecar(rel, backend=backend) or is_sample(base):
        store.set_scan_state(rel, mtime, size, "skipped_sidecar", library_id=lib_id)
        return {"file": rel, "status": "skipped_sidecar"}
    parsed = parse_filename(base)
    season, episode = parsed.get("season"), parsed.get("episode")
    if season is None:
        parent = os.path.basename(os.path.dirname(rel))
        m = _SEASON_DIR_RE.match(parent or "")
        if m:
            season = int(m.group(1))
    if season is None or episode is None:
        store.set_scan_state(rel, mtime, size, "tv_unknown", library_id=lib_id)
        return {"file": rel, "status": "skipped_tv_unknown", "parsed": parsed}
    title, year = _tv_show_title(rel, parsed.get("title") or os.path.splitext(base)[0])
    if not title:
        store.set_scan_state(rel, mtime, size, "tv_unknown", library_id=lib_id)
        return {"file": rel, "status": "skipped_tv_unknown"}
    show_id = store.upsert_show(lib_id, title, year, sort_title=normalize_title(title))
    ep_title = parsed.get("episode_title") or ""
    ep_id = store.upsert_episode(show_id, lib_id, rel, season, episode, ep_title)
    store.set_scan_state(rel, mtime, size, "tv_ok", library_id=lib_id)
    return {"file": rel, "status": "tv_ok", "show_id": show_id, "episode_id": ep_id,
            "show": title, "season": season, "episode": episode}


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
    可选 progress_cb(done, total) 报告全局进度、should_stop() 协作式取消
    （评审 B9/R04-D6：供后台 job 展示进度/取消）。
    返回结果条目带 `library_id`（跨库任务可区分）。"""
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
        plans.append((lib, backend, vids, tree_files))
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
    for lib, backend, vids, tree_files in plans:
        lib_id = _lib_id(lib.get("id"))
        is_tv = str(lib.get("kind") or "movie") == "tv"
        seen_extras: set[str] = set()
        for e in vids:
            if should_stop and should_stop():
                logger.info("scan cancelled: processed=%s/%s", done, grand)
                return out
            try:
                r = (scan_tv_file(backend, e.rel, entry=e) if is_tv
                     else scan_file(backend, e.rel, force=force,
                                    local_index=local_index, entry=e))
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
        if is_tv:
            continue
        # 花絮行 GC（按库分区，仅在一次完整遍历后执行）：文件已不存在的归属记录清掉
        # （正片走 missing/clean 流程）；用本次树遍历快照成员判定，免逐行 stat 往返
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
    return out
