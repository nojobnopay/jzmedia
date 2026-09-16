"""scanner.scan（自 app/scanner.py 拆分，评审 B9/R03-Q1；对外经 app.scanner 门面使用）。"""
import os
from ..config import settings
from .. import store
from .. import tmdb
from ..db import ensure_dirs
from ..log import get_logger
logger = get_logger("scanner.scan")
from .classify import is_sample, is_sidecar, extra_kind, strip_kind_affix, scan_skip_dirs, VIDEO_EXTS
from .parse import parse_filename, normalize_title
from .match import search_with_fallback
from .persist import apply_cached_to_movie, apply_tmdb_detail
__all__ = ['ST_SKIPPED_SAMPLE', 'ST_EXTRA_ATTACHED', 'ST_EXTRA_ORPHAN', 'ST_NO_MATCH', 'ST_EPISODE', 'attribute_extra', 'scan_one', 'scan_all']

ST_SKIPPED_SAMPLE = "skipped_sample"


ST_EXTRA_ATTACHED = "extra_attached"


ST_EXTRA_ORPHAN = "extra_orphan"


ST_NO_MATCH = "no_match"


ST_EPISODE = "skipped_episode_v1"


def attribute_extra(abs_path: str) -> dict:
    """花絮归属：解析标题/年份 → 库内标题/原标题匹配（年份±1，种类词前后缀剥掉再试一轮）
    → extras 表幂等记录。历史 orphan 在正片后入库/匹配修好后重扫自动补归属
    （已有归属的不碰，手工认领优先）。
    样片永不归属（仅返回 skipped_sample）。返回 {file, status, movie_id?, kind}。"""
    rel = os.path.relpath(abs_path, settings.media_root)
    base = os.path.basename(abs_path)
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
        if t and (hit := store.find_movie_for_extra(t, y)):
            mid = hit["id"]
            break
    # 已入库（搬迁改路径）先按 basename 认领，避免删建抖动
    claimed = store.repath_extra_by_basename(base, rel, mid, kind)
    if claimed is None:
        store.upsert_extra(rel, mid, kind)
    elif mid and not claimed.get("movie_id"):
        # 历史 orphan：正片后入库/匹配修好后重扫自动补归属（已有归属的不碰，手工认领优先）
        try:
            store.upsert_extra(rel, mid, kind)
        except Exception as e:
            logger.debug("re-attribute extra failed file=%s: %s", rel, e)
    if mid:
        return {"file": rel, "status": ST_EXTRA_ATTACHED, "movie_id": mid,
                "kind": kind, "title": hit.get("title", "")}
    return {"file": rel, "status": ST_EXTRA_ORPHAN, "kind": kind}


def scan_one(abs_path: str) -> dict:
    rel = os.path.relpath(abs_path, settings.media_root)
    if is_sidecar(rel):
        if is_sample(os.path.basename(abs_path)):
            return {"file": rel, "status": "skipped_sample"}
        return attribute_extra(abs_path)
    cached = store.get_by_path(rel)
    if cached and cached.get("tmdb_id"):
        return {"file": rel, "status": "skipped_cached", "title": cached.get("title")}
    # 增量跳过（评审 B9/R03-Q3）：未匹配/剧集行且文件 mtime+size 未变 → 不再重打 TMDB
    try:
        st = os.stat(abs_path)
        mtime, size = int(st.st_mtime), int(st.st_size)
    except OSError:
        mtime, size = 0, 0
    prev = store.get_scan_state(rel)
    if (prev is not None and mtime and size
            and int(prev.get("mtime") or 0) == mtime
            and int(prev.get("size") or 0) == size
            and str(prev.get("status") or "") in (ST_NO_MATCH, ST_EPISODE)):
        return {"file": rel, "status": "skipped_unchanged"}
    parsed = parse_filename(os.path.basename(abs_path))
    parsed["title"] = normalize_title(parsed["title"])
    if parsed["type"] == "episode":
        # V1 仅电影：剧集不入库（评审 P1-03：旧实现建行会让剧集出现在海报墙/统计里，
        # 与 README“剧集跳过”不符）。历史脏行由 POST /api/files/clean-episodes 清理。
        store.set_scan_state(rel, mtime, size, ST_EPISODE)
        return {"file": rel, "status": ST_EPISODE}
    m, used_q, year_mismatch = search_with_fallback(parsed["title"], parsed["year"])
    if not m:
        mid = store.upsert_movie_by_path(rel)
        # 重扫不覆盖既有标题（评审 B5a-1/R03-B1）：只补空值，人工修正/上次解析
        # 结果都保留；年份不可手工改，允许按文件名更新
        patch: dict = {"year": parsed["year"]}
        if not (cached or {}).get("title"):
            patch["title"] = parsed["title"]
        store.update_movie_meta(mid, **patch)
        try:
            cur = store.get_movie(mid) or {}
        except Exception:
            cur = {}
        local = {}
        if parsed.get("edition") and not cur.get("edition"):
            local["edition"] = parsed["edition"]
        if parsed.get("spec") and not cur.get("spec"):
            local["spec"] = parsed["spec"]
        if local:
            try:
                store.update_movie_local(mid, **local)
            except Exception as e:
                logger.debug("persist edition/spec failed mid=%s: %s", mid, e)
        store.set_scan_state(rel, mtime, size, ST_NO_MATCH)
        return {"file": rel, "status": ST_NO_MATCH, "parsed": parsed}
    tmdb_id = int(m["id"])
    mid = store.upsert_movie_by_path(rel)
    # 版本/规格后缀持久化：重扫不覆盖手工改过的值（非空保留）
    try:
        cur = store.get_movie(mid) or {}
    except Exception:
        cur = {}
    local: dict = {}
    if not cur.get("edition") and parsed.get("edition"):
        local["edition"] = parsed["edition"]
    if not cur.get("spec") and parsed.get("spec"):
        local["spec"] = parsed["spec"]
    if local:
        store.update_movie_local(mid, **local)
    if store.get_tmdb_cached(tmdb_id):
        out = apply_cached_to_movie(mid, tmdb_id, abs_path)
    else:
        detail = tmdb.movie_detail(tmdb_id)
        out = apply_tmdb_detail(mid, detail, abs_path)
    needs_review = 1 if (used_q != parsed["title"] or year_mismatch) else 0
    store.update_movie_local(mid, needs_review=needs_review)
    status = "ok" if not needs_review else "ok_needs_review"
    return {"file": rel, "status": status, "query": used_q, **out}


def _count_videos(skip_dirs: set) -> int:
    total = 0
    for root, dirs, files in os.walk(settings.media_root):
        dirs[:] = sorted(d for d in dirs
                         if not d.startswith(".") and d not in skip_dirs)
        for f in files:
            if f.startswith("."):
                continue
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                total += 1
    return total


def scan_all(progress_cb=None, should_stop=None) -> list[dict]:
    """全量扫描。可选 progress_cb(done, total) 报告进度、should_stop() 协作式取消
    （评审 B9/R04-D6：供后台 job 展示进度/取消）。"""
    ensure_dirs()
    out = []
    seen_extras: set[str] = set()
    skip_dirs = scan_skip_dirs()
    total = _count_videos(skip_dirs)
    if progress_cb:
        try:
            progress_cb(0, total)
        except Exception as e:
            logger.debug("progress_cb failed: %s", e)
    for root, dirs, files in os.walk(settings.media_root):
        # 剪枝（评审 P1-04）：隐藏目录 + NAS 回收站/缩略图等系统目录不进库
        dirs[:] = sorted(d for d in dirs
                         if not d.startswith(".") and d not in skip_dirs)
        for f in sorted(files):
            if should_stop and should_stop():
                logger.info("scan cancelled: processed=%s/%s", len(out), total)
                return out
            if f.startswith("."):
                continue
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                try:
                    r = scan_one(os.path.join(root, f))
                    out.append(r)
                    if r.get("status") in (ST_EXTRA_ATTACHED, ST_EXTRA_ORPHAN):
                        seen_extras.add(r["file"])
                except Exception as e:
                    rel = os.path.relpath(os.path.join(root, f), settings.media_root)
                    logger.debug("scan_one failed file=%s: %s", rel, e, exc_info=True)
                    out.append({"file": rel, "status": f"error: {e}"})
                if progress_cb:
                    try:
                        progress_cb(len(out), total)
                    except Exception as e:
                        logger.debug("progress_cb failed: %s", e)
    # 花絮行 GC：文件已不存在的归属记录清掉（正片走 missing/clean 流程）
    try:
        for row in store.list_all_extras():
            if row["file_path"] not in seen_extras and not os.path.exists(
                    os.path.join(settings.media_root, row["file_path"])):
                store.delete_extra_by_path(row["file_path"])
    except Exception as e:
        logger.debug("extras gc failed: %s", e)
    return out

