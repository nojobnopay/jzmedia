import os
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import library_paths, scanner, store
from ..jobkit import JobRegistry

router = APIRouter(prefix="/api/jobs")

# 扫描后台任务（评审 B9/R04-D6）：立即返回 job_id，进度/摘要轮询，可取消
_SCAN_JOBS = JobRegistry(prefix="scan")


def _scan_summary(results: list) -> dict:
    counts: dict = {}
    errors: list = []
    for r in results:
        st = str(r.get("status") or "")
        counts[st] = counts.get(st, 0) + 1
        if st.startswith("error"):
            errors.append({"file": r.get("file", ""), "status": st[:200]})
    return {"counts": counts, "errors": errors[:100], "results": results[:200]}


def _scan_worker(jid: str, library_id: int | None = None) -> None:
    def _stop() -> bool:
        job = _SCAN_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    def _cb(done, total):
        _SCAN_JOBS.update(jid, done=int(done), total=int(total))

    try:
        kwargs = {"progress_cb": _cb, "should_stop": _stop}
        if library_id is not None:
            kwargs["library_id"] = library_id
        res = scanner.scan_all(**kwargs)
        if _stop():
            _SCAN_JOBS.update(jid, done=len(res))
            return
        _SCAN_JOBS.update(jid, state="done", done=len(res), total=len(res),
                          **{"summary": _scan_summary(res)})
    except Exception as e:
        _SCAN_JOBS.update(jid, state="failed", error=str(e)[:300])


class ScanBody(BaseModel):
    library_id: int | None = None


@router.post("/scan")
def scan_start(body: ScanBody | None = None):
    """启动后台扫描：{library_id?} 缺省=全部启用库；立即返回 {job_id}；
    已在跑则复用（resumed）。"""
    library_id = body.library_id if body else None
    running = _SCAN_JOBS.running()
    if running:
        return {"job_id": running["job_id"], "resumed": True,
                "library_id": running.get("library_id")}
    job = _SCAN_JOBS.create(library_id=library_id)
    jid = job["job_id"]
    threading.Thread(target=_scan_worker, args=(jid, library_id), daemon=True).start()
    return {"job_id": jid, "resumed": False, "library_id": library_id}


@router.get("/scan/{job_id}")
def scan_status(job_id: str):
    """扫描进度：{state, done, total, summary?, error?}。无 job_id 看最近一个。"""
    job = _SCAN_JOBS.get(job_id) if job_id else _SCAN_JOBS.latest()
    if not job:
        return {"job_id": job_id, "state": "idle", "done": 0, "total": 0}
    return job


@router.post("/scan/{job_id}/cancel")
def scan_cancel(job_id: str = ""):
    """取消扫描（协作式：worker 处理完当前文件后退出）。"""
    if not job_id:
        running = _SCAN_JOBS.running()
        job_id = running["job_id"] if running else ""
    return {"job_id": job_id, "state": _SCAN_JOBS.cancel(job_id)}


class BackfillBody(BaseModel):
    limit: int = 500
    force: bool = False


class RefreshBody(BaseModel):
    ids: list[int] | None = None
    tmdb_ids: list[int] | None = None
    limit: int = 500


@router.post("/douban-fetch")
def douban_fetch():
    # 按确认结论：默认不爬豆瓣，V1手动填，口子先留好
    raise HTTPException(status_code=501, detail="douban fetch disabled by default (phase0 placeholder)")


@router.post("/backfill-meta")
def backfill_meta(body: BackfillBody | None = None):
    """离线修复：从 tmdb_cache 向 movies 补元数据 + 从缓存/兄弟行补人物关联。

    不调任何 TMDB 网络（默认永不自动刷新）；不重下海报、不写 NFO；
    手工标题永不覆盖；已存在的头像文件不重下。
    远端更新请走 POST /api/movies/{id}/refresh 或 POST /api/jobs/tmdb-refresh。
    """
    limit = (body.limit if body else 500) or 500
    force = bool(body.force) if body else False
    all_tmdb = [m for m in store.list_movies(grouped=False, limit=100000)
                if m.get("tmdb_id")]
    if force:
        cands = all_tmdb
    else:
        cands = [m for m in all_tmdb
                 if not (m.get("region") or "") or store.persons_missing_avatar(m["id"])]
    cands = cands[:max(1, min(limit, 5000))]
    done, failed = [], []
    avatars = 0
    skipped_no_cache = 0
    for m in cands:
        try:
            cached = store.get_tmdb_cached(int(m["tmdb_id"]))
            if not cached:
                skipped_no_cache += 1
                failed.append({"id": m["id"], "tmdb_id": m.get("tmdb_id"),
                               "error": "no tmdb_cache, use refresh first"})
                continue
            if force or not (m.get("region") or ""):
                # old_title=None：空标题才写入，非空（手工或已同步）一律保留
                store.copy_tmdb_to_movie(m["id"], old_title=None)
            avatars += scanner.sync_persons_from_cache(m["id"], int(m["tmdb_id"]))
            store.resync_fts(m["id"])
            done.append({"id": m["id"], "tmdb_id": m["tmdb_id"],
                         "region": (store.get_movie(m["id"]) or {}).get("region", "")})
        except Exception as e:
            failed.append({"id": m["id"], "tmdb_id": m.get("tmdb_id"), "error": str(e)})
    return {"total": len(cands), "ok": len(done), "failed": failed,
            "avatars_downloaded": avatars,
            "skipped_no_cache": skipped_no_cache,
            "results": done, "facets": store.get_facets()}


@router.post("/tmdb-refresh")
def tmdb_refresh(body: RefreshBody | None = None):
    """手动批量刷新：显式给出的 ids/tmdb_ids 才抓远端，无变化的行不碰。
    默认永不自动触发，供前端多选/设置页调用。"""
    body = body or RefreshBody()
    limit = max(1, min(body.limit or 500, 5000))
    tmdb_ids: list[int] = []
    seen: set[int] = set()
    for mid in body.ids or []:
        try:
            m = store.get_movie(int(mid))
        except Exception:
            m = None
        if m and m.get("tmdb_id") and int(m["tmdb_id"]) not in seen:
            seen.add(int(m["tmdb_id"]))
            tmdb_ids.append(int(m["tmdb_id"]))
    for tid in body.tmdb_ids or []:
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        if tid not in seen:
            seen.add(tid)
            tmdb_ids.append(tid)
    if not tmdb_ids:
        # 空 body = 刷新全部（设置页“一键刷新”用，需二次确认；cap by limit）
        for m in store.list_movies(grouped=False, limit=100000):
            if m.get("tmdb_id") and int(m["tmdb_id"]) not in seen:
                seen.add(int(m["tmdb_id"]))
                tmdb_ids.append(int(m["tmdb_id"]))
    if not tmdb_ids:
        raise HTTPException(422, "library has no tmdb_id yet, scan first")
    tmdb_ids = tmdb_ids[:limit]
    done, failed = [], []
    for tid in tmdb_ids:
        try:
            out = scanner.refresh_tmdb_id(tid)
            done.append({"tmdb_id": tid, **out})
        except Exception as e:
            failed.append({"tmdb_id": tid, "error": str(e)})
    return {"total": len(tmdb_ids),
            "ok": len(done), "failed": failed,
            "results": done, "facets": store.get_facets()}


class NfoBody(BaseModel):
    limit: int = 2000
    dry_run: bool = False


@router.post("/rebuild-nfo")
def rebuild_nfo(body: NfoBody | None = None):
    """重建 NFO（一键收敛）：为文件仍存在的影片按收敛规则重写 NFO
    （独占单版本只留 movie.nfo 并删历史同名残留；同片多版本补各版本同名；
    共享目录只写当前同名、不碰 movie.nfo）。换机器/丢 NFO 后修复用。
    dry_run=true 只预览（报告会写/会删），默认 false 直接执行。"""
    limit = max(1, min((body.limit if body else 2000) or 2000, 10000))
    dry_run = bool(body.dry_run) if body else False
    movies = sorted(store.list_movies(grouped=False, limit=100000)[:limit],
                    key=lambda m: (m.get("file_path", ""), m.get("id", 0)))
    from .files import _require_writable
    for lid in sorted({int(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
                       for m in movies}):
        _require_writable(lid)
    done, skipped, failed = 0, 0, []
    wrote_total, deleted_total = 0, 0
    by_mode: dict[str, int] = {}
    for m in movies:
        abs_path = library_paths.resolve(
            m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID, m["file_path"])
        if not os.path.isfile(abs_path):
            skipped += 1
            continue
        try:
            r = scanner.sync_nfos_for(m["id"], abs_path, dry_run=dry_run)
            if r.get("ok"):
                done += 1
                mode = str(r.get("mode") or "unknown")
                by_mode[mode] = by_mode.get(mode, 0) + 1
                wrote_total += len(r.get("wrote") or [])
                deleted_total += len(r.get("deleted") or [])
            else:
                failed.append({"id": m["id"], "file_path": m["file_path"],
                               "error": str(r.get("mode") or "sync failed")})
        except Exception as e:
            failed.append({"id": m["id"], "file_path": m["file_path"], "error": str(e)})
    return {"total": len(movies), "ok": done, "skipped_missing": skipped,
            "failed": failed, "dry_run": dry_run,
            "wrote": wrote_total, "deleted": deleted_total, "by_mode": by_mode}


@router.post("/rebuild-fts")
def rebuild_fts():
    """重建全文索引（搜索异常时的修复口）。"""
    n = store.rebuild_fts()
    return {"ok": True, "rows": n}


@router.get("/stats")
def stats(library: str | None = None):
    """库状态一览（设置页展示用，纯本地聚合）；library 缺省=全库合计。"""
    libs = store._split_ints(library)
    missing = 0
    for m in store.list_movies(grouped=False, limit=100000):
        if libs and int(m.get("library_id") or 0) not in libs:
            continue
        if not os.path.exists(library_paths.resolve(
                m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID,
                m["file_path"])):
            missing += 1
    base = store.library_stats(libs[0] if len(libs) == 1 else None)
    return {**base, "missing_files": missing}
