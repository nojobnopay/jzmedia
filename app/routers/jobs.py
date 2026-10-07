import os
import re
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import artwork, library_paths, scanner, store
from ..jobkit import JobRegistry
from ..log import get_logger

logger = get_logger("jobs.scan")

router = APIRouter(prefix="/api/jobs")

# 扫描后台任务（评审 B9/R04-D6）：立即返回 job_id，进度/摘要轮询，可取消
_SCAN_JOBS = JobRegistry(prefix="scan")
_SCAN_FINISH_LOCK = threading.Lock()


def _scan_summary(results: list) -> dict:
    counts: dict = {}
    by_library: dict = {}
    errors: list = []
    for r in results:
        st = str(r.get("status") or "")
        try:
            n = int(r.get("count") or 1)
        except (TypeError, ValueError):
            n = 1
        if n < 1:
            n = 1
        counts[st] = counts.get(st, 0) + n
        lid = r.get("library_id")
        if lid is not None:
            try:
                bucket = by_library.setdefault(int(lid), {})
            except (TypeError, ValueError):
                bucket = None
            if bucket is not None:
                bucket[st] = bucket.get(st, 0) + n
        if st.startswith("error"):
            errors.append({"file": r.get("file", ""), "status": st[:200]})
    return {"counts": counts, "by_library": by_library,
            "errors": errors[:100], "results": results[:200]}


def _lib_filter(library_id=None, media_library_id=None) -> set[int] | None:
    """作用域解析（库工具媒体库化）：media_library_id 优先 → 其全部视频库；
    library_id 兼容单库/逗号多库；都缺省=None（全库）。
    未知媒体库返回空集合（调用方应据此返回空结果，绝不退化成全库）。"""
    if media_library_id is not None and str(media_library_id).strip() != "":
        try:
            mid = int(media_library_id)
        except (TypeError, ValueError):
            raise HTTPException(422, "media_library_id must be int")
        return set(store.library_ids_for_media(mid))
    if library_id is not None and str(library_id).strip() != "":
        return set(store._split_ints(library_id))
    return None


def _in_filter(m: dict, lib_ids: set[int] | None) -> bool:
    """行是否落在作用域内（lib_ids=None 表示全库）。"""
    if lib_ids is None:
        return True
    try:
        return int(m.get("library_id") or 0) in lib_ids
    except (TypeError, ValueError):
        return False


def _scan_tv_lib_ids(library_id=None, media_library_id=None) -> list[int]:
    """本次扫描作用域内的 TV 视频库 id（链式刮削用；失败返回空，不阻塞扫描完成）。"""
    try:
        if media_library_id is not None:
            libs = [l for l in store.list_libraries(only_enabled=True)
                    if int(l.get("media_library_id") or 0) == int(media_library_id)]
        elif library_id is not None:
            lib = store.get_library(int(library_id))
            libs = [lib] if lib else []
        else:
            libs = store.list_libraries(only_enabled=True)
        return sorted({int(l["id"]) for l in libs if l and str(l.get("kind") or "") == "tv"})
    except Exception as e:
        logger.debug("resolve scan tv libs failed: %s", e)
        return []


def _scan_worker(jid: str, library_id: int | None = None,
                 force: bool = False, media_library_id: int | None = None) -> None:
    def _stop() -> bool:
        job = _SCAN_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    def _cb(done, total):
        _SCAN_JOBS.update(jid, done=int(done), total=int(total))

    try:
        from ..scanner.scan import _scan_libraries
        from .fs.copy import active_copy_jobs
        scan_libs = _scan_libraries(library_id, media_library_id)
        watermarks = {int(lib['id']): store.fs_change_summary(lib['id'])['revision']
                      for lib in scan_libs}
        copying_at_start = {int(j['library_id']) for j in active_copy_jobs()}
        kwargs = {"progress_cb": _cb, "should_stop": _stop, "force": bool(force)}
        if library_id is not None:
            kwargs["library_id"] = library_id
        if media_library_id is not None:
            kwargs["media_library_id"] = media_library_id
        res = scanner.scan_all(**kwargs)
        if _stop():
            _SCAN_JOBS.update(jid, done=len(res))
            return
        summary = _scan_summary(res)
        # TV 链式刮削（②A）：扫描后顺手补新剧简介/海报，不用用户再点一次。
        # 只刮未匹配/未刮过的剧（scrape_pending 内部过滤，幂等有界）；已有
        # tv-scrape 在跑则跳过；异常只记 summary，不把扫描置失败。
        try:
            tv_libs = _scan_tv_lib_ids(library_id, media_library_id)
        except Exception:
            tv_libs = []
        if tv_libs and not _stop():
            try:
                if _TV_JOBS.running():
                    summary["tv_scrape"] = {"skipped": "running"}
                else:
                    from ..scanner import tv_persist
                    tv_res = tv_persist.scrape_pending(
                        library_ids=tv_libs, force=False, should_stop=_stop)
                    summary["tv_scrape"] = _tv_summary(tv_res)
            except Exception as e:
                logger.warning("chained tv scrape failed: %s", e)
                summary["tv_scrape"] = {"error": str(e)[:200]}
        if _stop():
            _SCAN_JOBS.update(jid, done=len(res), summary=summary)
            return
        # Successful enumeration and reconciliation acknowledge only pre-scan changes.
        # Partial errors, offline libraries and cancelled copy/scan work remain pending.
        failed_libs = {int(r['library_id']) for r in res if r.get('library_id') is not None
                       and (str(r.get('status') or '').startswith('error')
                            or r.get('status') in ('scan_failed', 'library_offline'))}
        scrape = summary.get('tv_scrape') or {}
        if scrape.get('error') or scrape.get('errors') or scrape.get('skipped'):
            failed_libs.update(tv_libs)
        failed_libs.update(copying_at_start)
        failed_libs.update(int(j['library_id']) for j in active_copy_jobs())
        with _SCAN_FINISH_LOCK:
            if _stop():
                _SCAN_JOBS.update(jid, done=len(res), summary=summary)
                return
            cleared = {}
            for lid, revision in watermarks.items():
                if lid not in failed_libs and revision:
                    cleared[lid] = store.clear_fs_changes(lid, revision)
            summary['fs_changes'] = {'cleared': cleared,
                                    'pending': [store.fs_change_summary(lid) for lid in watermarks]}
            _SCAN_JOBS.update(jid, state="done", done=len(res), total=len(res),
                              **{"summary": summary})
    except Exception as e:
        _SCAN_JOBS.update(jid, state="failed", error=str(e)[:300])


class ScanBody(BaseModel):
    library_id: int | None = None           # 单个视频库
    media_library_id: int | None = None     # 整个媒体库（其全部启用视频库）
    force: bool = False   # 强制重扫（跳过缓存短路，重走匹配+落盘；修复错配用）


@router.post("/scan")
def scan_start(body: ScanBody | None = None):
    """启动后台扫描：{library_id?|media_library_id?, force?} 缺省=全部启用库；
    立即返回 {job_id}；已在跑则复用（resumed）。force=true 时已有匹配的行也会重走。"""
    library_id = body.library_id if body else None
    media_library_id = body.media_library_id if body else None
    force = bool(body.force) if body else False
    from ..scanner.scan import _scan_libraries
    from .fs.copy import active_copy_jobs
    target_ids = {int(lib['id']) for lib in _scan_libraries(library_id, media_library_id)}
    if any(int(j['library_id']) in target_ids for j in active_copy_jobs()):
        raise HTTPException(409, '该视频库仍在复制文件，请等待复制结束后扫描')
    running = _SCAN_JOBS.running()
    if running:
        if (running.get("library_id"), running.get("media_library_id"), bool(running.get("force"))) != (library_id, media_library_id, force):
            raise HTTPException(409, "另一个范围的扫描正在进行，请等待完成")
        return {"job_id": running["job_id"], "resumed": True,
                "library_id": running.get("library_id"),
                "media_library_id": running.get("media_library_id")}
    job = _SCAN_JOBS.create(library_id=library_id, media_library_id=media_library_id,
                            force=force)
    jid = job["job_id"]
    threading.Thread(target=_scan_worker,
                     args=(jid, library_id, force, media_library_id),
                     daemon=True).start()
    return {"job_id": jid, "resumed": False, "library_id": library_id,
            "media_library_id": media_library_id, "force": force}


@router.get("/scan")
def scan_current():
    """Shared status across entry points, including after a page reload."""
    return _SCAN_JOBS.running() or _SCAN_JOBS.latest() or {"state": "idle"}


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
    with _SCAN_FINISH_LOCK:
        return {"job_id": job_id, "state": _SCAN_JOBS.cancel(job_id)}


# ---- TV 刮削（T2）：未匹配/未刮过的剧 → TMDB 元数据 + 海报 ----
_TV_JOBS = JobRegistry(prefix="tv")


def _tv_summary(results: list) -> dict:
    counts: dict = {}
    errors: list = []
    for r in results:
        st = str(r.get("status") or "")
        counts[st] = counts.get(st, 0) + 1
        if st.startswith("error"):
            errors.append({"show_id": r.get("show_id"),
                           "title": r.get("title", ""), "status": st[:200]})
    return {"counts": counts, "errors": errors[:100], "results": results[:200]}


def _tv_worker(jid: str, library_id=None, media_library_id=None,
               ids=None, force: bool = False) -> None:
    def _stop() -> bool:
        job = _TV_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    def _cb(done, total):
        _TV_JOBS.update(jid, done=int(done), total=int(total))

    try:
        lib_ids = _lib_filter(library_id, media_library_id)
        if lib_ids is not None and not lib_ids:
            _TV_JOBS.update(jid, state="done", done=0, total=0,
                            summary={"counts": {}, "errors": [], "results": []})
            return
        from ..scanner import tv_persist
        lib_arg = (sorted(lib_ids) if lib_ids is not None else None)
        res = tv_persist.scrape_pending(
            library_ids=lib_arg,
            ids=ids, force=bool(force), progress_cb=_cb, should_stop=_stop)
        # 离线回填人名串：存量已刮剧普通刮削跳过（person_names 为空导致搜不到演员），
        # force 全量重刮又太重；本段纯读缓存、不触网。
        if not _stop():
            n0 = len(res)

            def _cb2(done, _total):
                _cb(n0 + int(done), n0 + int(_total))

            res = list(res) + tv_persist.backfill_person_names(
                library_ids=lib_arg, ids=ids, progress_cb=_cb2,
                should_stop=_stop)
        if _stop():
            _TV_JOBS.update(jid, done=len(res))
            return
        _TV_JOBS.update(jid, state="done", done=len(res), total=len(res),
                        **{"summary": _tv_summary(res)})
    except Exception as e:
        _TV_JOBS.update(jid, state="failed", error=str(e)[:300])


class TvScrapeBody(BaseModel):
    library_id: int | None = None
    media_library_id: int | None = None
    ids: list[int] | None = None      # 显式剧 id（优先于库筛选）
    force: bool = False               # 已刮过的也重刮


@router.post("/tv-scrape")
def tv_scrape_start(body: TvScrapeBody | None = None):
    """启动剧集刮削后台任务：{library_id?|media_library_id?|ids?, force?}。
    只刮「未匹配或未刮过」的剧；force=true 全量重刮。立即返回 {job_id}。"""
    body = body or TvScrapeBody()
    running = _TV_JOBS.running()
    if running:
        if (running.get("library_id"), running.get("media_library_id"), sorted(running.get("ids") or []), bool(running.get("force"))) != (body.library_id, body.media_library_id, sorted(body.ids or []), body.force):
            raise HTTPException(409, "另一个范围的剧集资料任务正在进行，请稍后重试")
        return {"job_id": running["job_id"], "resumed": True}
    job = _TV_JOBS.create(library_id=body.library_id,
                          media_library_id=body.media_library_id,
                          ids=body.ids, force=body.force)
    jid = job["job_id"]
    threading.Thread(target=_tv_worker,
                     args=(jid, body.library_id, body.media_library_id,
                           body.ids, body.force),
                     daemon=True).start()
    return {"job_id": jid, "resumed": False, "force": body.force,
            "ids": body.ids or []}


@router.get("/tv-scrape/{job_id}")
def tv_scrape_status(job_id: str = ""):
    job = _TV_JOBS.get(job_id) if job_id else _TV_JOBS.latest()
    if not job:
        return {"job_id": job_id, "state": "idle", "done": 0, "total": 0}
    return job


@router.post("/tv-scrape/{job_id}/cancel")
def tv_scrape_cancel(job_id: str = ""):
    if not job_id:
        running = _TV_JOBS.running()
        job_id = running["job_id"] if running else ""
    return {"job_id": job_id, "state": _TV_JOBS.cancel(job_id)}


# ---- 剧集 NFO/海报落盘（T3）：按现有匹配从 DB 重写 tvshow/季/集 NFO + 海报 ----
_TV_NFO_JOBS = JobRegistry(prefix="tvnfo")


def _tv_nfo_summary(results: list) -> dict:
    counts: dict = {}
    totals = {"nfo_wrote": 0, "nfo_skipped": 0, "nfo_failed": 0, "artwork_wrote": 0}
    for r in results:
        st = str(r.get("status") or "")
        counts[st] = counts.get(st, 0) + 1
        for k in totals:
            totals[k] += int(r.get(k) or 0)
    return {"counts": counts, "totals": totals, "results": results[:200]}


def _tv_nfo_worker(jid: str, library_id=None, media_library_id=None, ids=None,
                   dry_run: bool = False, thumbs: bool = False,
                   episodes: bool | None = None) -> None:
    def _stop() -> bool:
        job = _TV_NFO_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    def _cb(done, total):
        _TV_NFO_JOBS.update(jid, done=int(done), total=int(total))

    try:
        lib_ids = _lib_filter(library_id, media_library_id)
        if lib_ids is not None and not lib_ids:
            _TV_NFO_JOBS.update(jid, state="done", done=0, total=0,
                                summary={"counts": {}, "totals": {}, "results": []})
            return
        from .. import storage
        from ..scanner import tv_nfo_link, tv_persist
        shows = store.list_shows_for_scrape(
            library_ids=(sorted(lib_ids) if lib_ids is not None else None),
            ids=ids, force=True)
        out: list[dict] = []
        for i, s in enumerate(shows):
            if _stop():
                return
            st: dict = {"show_id": s.get("id"), "title": s.get("title")}
            try:
                if dry_run:
                    be = storage.backend_for(int(s["library_id"]))
                    r = tv_nfo_link.sync_tv_nfos_for(int(s["id"]), backend=be,
                                                     dry_run=True,
                                                     episode_nfo=episodes)
                    st.update({"status": "dry_run",
                               "nfo_wrote": len(r.get("wrote") or [])})
                else:
                    m = tv_persist.write_media_files(int(s["id"]), s.get("library_id"),
                                                     thumbs=bool(thumbs),
                                                     episode_nfo=episodes)
                    art = m.get("artwork") or {}
                    st.update({"status": "ok" if m.get("nfo") else "partial",
                               "nfo_wrote": m.get("nfo_wrote") or 0,
                               "nfo_skipped": m.get("nfo_skipped") or 0,
                               "nfo_failed": m.get("nfo_failed") or 0,
                               "artwork_wrote": len(art.get("wrote") or []),
                               "artwork_reason": art.get("reason") or ""})
            except Exception as e:
                st["status"] = f"error: {str(e)[:160]}"
            out.append(st)
            _cb(i + 1, len(shows))
        _TV_NFO_JOBS.update(jid, state="done", done=len(out), total=len(out),
                            summary=_tv_nfo_summary(out))
    except Exception as e:
        _TV_NFO_JOBS.update(jid, state="failed", error=str(e)[:300])


class TvNfoBody(BaseModel):
    library_id: int | None = None
    media_library_id: int | None = None
    ids: list[int] | None = None
    dry_run: bool = False          # 只预览会写哪些 NFO（不落盘）
    thumbs: bool = False           # 另写每集 <stem>-thumb.jpg（千集级，默认关）
    episodes: bool | None = None   # 逐集 <stem>.nfo（None=本地写/远程不写；远程开启约 1.7h）


@router.post("/rebuild-tv-nfo")
def tv_nfo_start(body: TvNfoBody | None = None):
    """重写剧集 NFO/海报（离线，不触网）：`tvshow.nfo` + 季 `season.nfo` + 每集 `<stem>.nfo`；
    `artwork_mode=nfo_art` 的库另写 poster/fanart/季海报。立即返回 {job_id}。"""
    body = body or TvNfoBody()
    running = _TV_NFO_JOBS.running()
    if running:
        return {"job_id": running["job_id"], "resumed": True}
    job = _TV_NFO_JOBS.create(library_id=body.library_id,
                              media_library_id=body.media_library_id,
                              ids=body.ids, dry_run=body.dry_run,
                              thumbs=body.thumbs, episodes=body.episodes)
    jid = job["job_id"]
    threading.Thread(target=_tv_nfo_worker,
                     args=(jid, body.library_id, body.media_library_id, body.ids,
                           body.dry_run, body.thumbs, body.episodes),
                     daemon=True).start()
    return {"job_id": jid, "resumed": False, "dry_run": body.dry_run}


@router.get("/rebuild-tv-nfo/{job_id}")
def tv_nfo_status(job_id: str = ""):
    job = _TV_NFO_JOBS.get(job_id) if job_id else _TV_NFO_JOBS.latest()
    if not job:
        return {"job_id": job_id, "state": "idle", "done": 0, "total": 0}
    return job


@router.post("/rebuild-tv-nfo/{job_id}/cancel")
def tv_nfo_cancel(job_id: str = ""):
    if not job_id:
        running = _TV_NFO_JOBS.running()
        job_id = running["job_id"] if running else ""
    return {"job_id": job_id, "state": _TV_NFO_JOBS.cancel(job_id)}


# ---- TV 目录规范化（T4.2）：包装层拍平/补 Season/花絮拍平（只移动不改名）----
_TV_ORG_JOBS = JobRegistry(prefix="tvorg")


def _tv_org_worker(jid: str, library_id=None, media_library_id=None, ids=None,
                   actions=None, dry_run: bool = True,
                   allow_torrent: bool = False,
                   allow_absolute_shows=None) -> None:
    def _stop() -> bool:
        job = _TV_ORG_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    try:
        from ..scanner import tv_organize
        lib_ids = _lib_filter(library_id, media_library_id)
        if lib_ids is not None and not lib_ids:
            _TV_ORG_JOBS.update(jid, state="done", done=0, total=0,
                                summary={"counts": {}, "total": 0, "plans": []})
            return
        plan = tv_organize.plan_tv_organize(
            library_ids=(sorted(lib_ids) if lib_ids is not None else None),
            ids=ids, actions=actions or tv_organize.TV_ORGANIZE_ACTIONS,
            allow_torrent=allow_torrent,
            allow_absolute_shows=allow_absolute_shows)
        if dry_run:
            _TV_ORG_JOBS.update(
                jid, state="done", done=len(plan["plans"]), total=len(plan["plans"]),
                summary={"dry_run": True, "counts": plan["counts"],
                         "total": plan["total"], "conflicts": plan["conflicts"],
                         "untouched": plan.get("untouched", 0),
                         "manual": plan.get("manual", 0),
                         "kept": plan.get("kept", 0),
                         "absolute": plan.get("absolute", 0),
                         "blocked": plan["blocked"],
                         "plans": [tv_organize.summarize_plan(p)
                                   for p in plan["plans"][:200]]})
            return

        def _cb(done, total):
            _TV_ORG_JOBS.update(jid, done=int(done), total=int(total))

        res = tv_organize.execute_tv_organize(
            plan["plans"], should_stop=_stop, progress_cb=_cb,
            allow_torrent=allow_torrent)
        if _stop():
            return
        _TV_ORG_JOBS.update(jid, state="done", done=res["moved"] + res["renamed"],
                            total=plan["total"],
                            summary={"dry_run": False, "plan_total": plan["total"],
                                     **res})
    except Exception as e:
        _TV_ORG_JOBS.update(jid, state="failed", error=str(e)[:300])


class TvOrganizeBody(BaseModel):
    library_id: int | None = None
    media_library_id: int | None = None
    ids: list[int] | None = None       # 只整理这些剧（缺省全部）
    actions: list[str] | None = None   # 见 tv_organize.TV_ORGANIZE_ACTIONS，缺省全部
    dry_run: bool = True               # 默认只预览
    allow_torrent: bool = False        # 含 .torrent 的剧默认跳过
    allow_absolute_shows: list[int] | None = None  # 显式同意的绝对集号风险剧（可改名）


@router.post("/tv-organize")
def tv_organize_start(body: TvOrganizeBody | None = None):
    """剧集目录规范化（只移动/补目录，不改文件名）：默认 dry_run 返回预览计划；
    dry_run=false 执行（两段确认由前端负责）。立即返回 {job_id}。"""
    body = body or TvOrganizeBody()
    running = _TV_ORG_JOBS.running()
    if running:
        return {"job_id": running["job_id"], "resumed": True}
    job = _TV_ORG_JOBS.create(library_id=body.library_id,
                              media_library_id=body.media_library_id,
                              ids=body.ids, actions=body.actions,
                              dry_run=body.dry_run)
    jid = job["job_id"]
    threading.Thread(target=_tv_org_worker,
                     args=(jid, body.library_id, body.media_library_id, body.ids,
                           body.actions, body.dry_run, body.allow_torrent,
                           body.allow_absolute_shows),
                     daemon=True).start()
    return {"job_id": jid, "resumed": False, "dry_run": body.dry_run}


@router.get("/tv-organize/history")
def tv_organize_history(limit: int = 30):
    """整理批次历史（v24 审计）：时间、条数、已撤销数；用于「撤销整理」选择。"""
    batches = store.list_organize_batches(limit=max(1, min(int(limit or 30), 200)))
    return {"batches": batches}


@router.get("/tv-organize/{job_id}")
def tv_organize_status(job_id: str = ""):
    job = _TV_ORG_JOBS.get(job_id) if job_id else _TV_ORG_JOBS.latest()
    if not job:
        return {"job_id": job_id, "state": "idle", "done": 0, "total": 0}
    return job


@router.post("/tv-organize/{job_id}/cancel")
def tv_organize_cancel(job_id: str = ""):
    if not job_id:
        running = _TV_ORG_JOBS.running()
        job_id = running["job_id"] if running else ""
    return {"job_id": job_id, "state": _TV_ORG_JOBS.cancel(job_id)}


# ---- 撤销整理（v24 审计）：按批次把移动反向搬回，默认 dry_run 预览 ----
_TV_RESTORE_JOBS = JobRegistry(prefix="tvundo")


def _tv_restore_worker(jid: str, batch_id=None, library_id=None,
                       media_library_id=None, shows=None, kinds=None,
                       dry_run: bool = True) -> None:
    def _stop() -> bool:
        job = _TV_RESTORE_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    try:
        from ..scanner import tv_organize
        lib_ids = _lib_filter(library_id, media_library_id)
        if lib_ids is not None and not lib_ids:
            _TV_RESTORE_JOBS.update(jid, state="done", done=0, total=0,
                                    summary={"dry_run": dry_run, "total": 0,
                                             "plans": []})
            return
        plan = tv_organize.plan_restore(
            batch_id=batch_id,
            library_ids=(sorted(lib_ids) if lib_ids is not None else None),
            shows=shows, kinds=kinds)
        if dry_run:
            _TV_RESTORE_JOBS.update(
                jid, state="done", done=len(plan["plans"]), total=len(plan["plans"]),
                summary={"dry_run": True, "batch_id": plan["batch_id"],
                         "counts": plan["counts"], "total": plan["total"],
                         "conflicts": plan["conflicts"],
                         "plans": plan["plans"][:200]})
            return

        def _cb(done, total):
            _TV_RESTORE_JOBS.update(jid, done=int(done), total=int(total))

        res = tv_organize.execute_restore(plan["plans"], should_stop=_stop,
                                          progress_cb=_cb)
        if _stop():
            return
        _TV_RESTORE_JOBS.update(jid, state="done", done=res["restored"],
                                total=plan["total"],
                                summary={"dry_run": False, "plan_total": plan["total"],
                                         **res})
    except Exception as e:
        _TV_RESTORE_JOBS.update(jid, state="failed", error=str(e)[:300])


class TvRestoreBody(BaseModel):
    batch_id: str | None = None        # 缺省=最近一次整理批次
    library_id: int | None = None
    media_library_id: int | None = None
    shows: list[int] | None = None     # 只还原这些剧（show_id）
    kinds: list[str] | None = None     # episode|extra|file|dir|rmdir 过滤
    dry_run: bool = True               # 默认只预览


@router.post("/tv-organize-restore")
def tv_organize_restore_start(body: TvRestoreBody | None = None):
    """撤销整理（按审计反向搬回）：默认 dry_run 返回预览；执行需显式 dry_run=false
    （前端两段确认）。立即返回 {job_id}。"""
    body = body or TvRestoreBody()
    running = _TV_RESTORE_JOBS.running()
    if running:
        return {"job_id": running["job_id"], "resumed": True}
    job = _TV_RESTORE_JOBS.create(batch_id=body.batch_id,
                                  library_id=body.library_id,
                                  media_library_id=body.media_library_id,
                                  shows=body.shows, kinds=body.kinds,
                                  dry_run=body.dry_run)
    jid = job["job_id"]
    threading.Thread(target=_tv_restore_worker,
                     args=(jid, body.batch_id, body.library_id,
                           body.media_library_id, body.shows, body.kinds,
                           body.dry_run),
                     daemon=True).start()
    return {"job_id": jid, "resumed": False, "dry_run": body.dry_run}


@router.get("/tv-organize-restore/{job_id}")
def tv_organize_restore_status(job_id: str = ""):
    job = _TV_RESTORE_JOBS.get(job_id) if job_id else _TV_RESTORE_JOBS.latest()
    if not job:
        return {"job_id": job_id, "state": "idle", "done": 0, "total": 0}
    return job


@router.post("/tv-organize-restore/{job_id}/cancel")
def tv_organize_restore_cancel(job_id: str = ""):
    if not job_id:
        running = _TV_RESTORE_JOBS.running()
        job_id = running["job_id"] if running else ""
    return {"job_id": job_id, "state": _TV_RESTORE_JOBS.cancel(job_id)}


class BackfillBody(BaseModel):
    limit: int = 500
    force: bool = False
    library_id: int | None = None
    media_library_id: int | None = None


class RefreshBody(BaseModel):
    ids: list[int] | None = None
    tmdb_ids: list[int] | None = None
    limit: int = 500
    library_id: int | None = None
    media_library_id: int | None = None


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
    lib_ids = _lib_filter(body.library_id if body else None,
                          body.media_library_id if body else None)
    all_tmdb = [m for m in store.list_movies(grouped=False, limit=100000)
                if m.get("tmdb_id") and _in_filter(m, lib_ids)]
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
        # body.library_id / media_library_id 指定时只刷新该范围（设置页高级维护）
        lib_ids = _lib_filter(body.library_id, body.media_library_id)
        for m in store.list_movies(grouped=False, limit=100000):
            if not _in_filter(m, lib_ids):
                continue
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
    library_id: int | None = None
    media_library_id: int | None = None


@router.post("/rebuild-nfo")
def rebuild_nfo(body: NfoBody | None = None):
    """重建 NFO（一键收敛）：为文件仍存在的影片按收敛规则重写 NFO
    （独占单版本只留 movie.nfo 并删历史同名残留；同片多版本补各版本同名；
    共享目录只写当前同名、不碰 movie.nfo）。换机器/丢 NFO 后修复用。
    dry_run=true 只预览（报告会写/会删），默认 false 直接执行。
    body.library_id 可限定库（缺省=全库）。"""
    limit = max(1, min((body.limit if body else 2000) or 2000, 10000))
    dry_run = bool(body.dry_run) if body else False
    lib_ids = _lib_filter(body.library_id if body else None,
                          body.media_library_id if body else None)
    movies = sorted([m for m in store.list_movies(grouped=False, limit=100000)
                     if _in_filter(m, lib_ids)][:limit],
                    key=lambda m: (m.get("file_path", ""), m.get("id", 0)))
    from .. import storage
    from .files import _is_file, _require_writable
    for lid in sorted({int(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
                       for m in movies}):
        _require_writable(lid)
    done, skipped, failed = 0, 0, []
    wrote_total, deleted_total = 0, 0
    by_mode: dict[str, int] = {}
    for m in movies:
        lib_id = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
        rel = m["file_path"]
        if not _is_file(lib_id, rel):
            skipped += 1
            continue
        try:
            abs_path = storage.media_write_path(lib_id, rel)
            if abs_path:
                r = scanner.sync_nfos_for(m["id"], abs_path, dry_run=dry_run,
                                          force=True)
            else:
                backend = storage.backend_for(lib_id)
                r = scanner.sync_nfos_for(m["id"], backend=backend, rel=rel,
                                          dry_run=dry_run, force=True)
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
    """库状态一览（设置页展示用，纯本地聚合）；library 缺省=全库合计。
    显式“重新看盘”：先清元数据短 TTL 缓存。"""
    from .files import _exists_map
    from .. import storage
    storage.clear_meta_cache()
    libs = store._split_ints(library)
    rows = [m for m in store.list_movies(grouped=False, limit=100000)
            if not libs or int(m.get("library_id") or 0) in libs]
    groups: dict[int, set] = {}
    for m in rows:
        lid = int(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        groups.setdefault(lid, set()).add(m["file_path"])
    maps = {lid: _exists_map(lid, rels) for lid, rels in groups.items()}
    missing = sum(
        1 for m in rows
        if not maps[int(m.get("library_id")
                        or library_paths.DEFAULT_LIBRARY_ID)].get(
                            m["file_path"], True))
    base = store.library_stats(libs[0] if len(libs) == 1 else None)
    by_library = []
    for lid in groups:
        subset = [m for m in rows if int(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID) == lid]
        by_library.append({"library_id": lid,
                           "pending": sum(bool(m.get("needs_review") or not m.get("tmdb_id")) for m in subset),
                           "missing_files": sum(not maps[lid].get(m["file_path"], True) for m in subset)})
    return {**base, "missing_files": missing, "by_library": by_library}


# ===== IMDb 离线数据集导入（E 阶段）：手动触发，离线匹配用 =====
_IMDB_JOBS = JobRegistry(prefix="imdb")


class ImdbImportBody(BaseModel):
    path: str | None = None
    limit: int | None = None


def _imdb_worker(jid: str, path: str, limit) -> None:
    def _stop() -> bool:
        job = _IMDB_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    def _cb(n: int) -> None:
        _IMDB_JOBS.update(jid, done=int(n))

    try:
        r = store.import_imdb_tsv(path, limit, _cb, _stop)
        if _stop():
            _IMDB_JOBS.update(jid, done=int(r.get("imported") or 0))
            return
        _IMDB_JOBS.update(jid, state="done", done=r["imported"],
                          total=r["imported"])
    except Exception as e:
        _IMDB_JOBS.update(jid, state="failed", error=str(e)[:300])


@router.post("/import-imdb")
def import_imdb(body: ImdbImportBody | None = None):
    """导入 IMDb title.basics 数据集（离线匹配用；GB 级文件请显式传 limit 试跑）。
    path 缺省读 env `IMDB_DATASET_PATH`；单任务复用（resumed）。"""
    path = (body.path if body else None) or os.getenv("IMDB_DATASET_PATH", "")
    path = str(path or "").strip()
    if not path:
        raise HTTPException(422, "path required (or set IMDB_DATASET_PATH)")
    if not os.path.isfile(path):
        raise HTTPException(404, f"file not found: {path}")
    running = _IMDB_JOBS.running()
    if running:
        return {"job_id": running["job_id"], "resumed": True}
    job = _IMDB_JOBS.create(path=path, total=0)
    jid = job["job_id"]
    threading.Thread(target=_imdb_worker,
                     args=(jid, path, (body.limit if body else None)),
                     daemon=True).start()
    return {"job_id": jid, "resumed": False}


@router.get("/import-imdb/{job_id}")
def import_imdb_status(job_id: str = ""):
    job = _IMDB_JOBS.get(job_id) if job_id else _IMDB_JOBS.latest()
    return job or {"job_id": job_id, "state": "idle", "done": 0, "total": 0}


@router.post("/import-imdb/{job_id}/cancel")
def import_imdb_cancel(job_id: str = ""):
    if not job_id:
        running = _IMDB_JOBS.running()
        job_id = running["job_id"] if running else ""
    return {"job_id": job_id, "state": _IMDB_JOBS.cancel(job_id)}


# ===== 元数据落盘重建 / 存量清理（2026-09 用户反馈修复） =====
_META_JOBS = JobRegistry(prefix="meta")


def _target_counts(rows: list) -> dict:
    """按写目标类型统计（预览/结果用）：local / remote / unavailable。"""
    out = {"local": 0, "remote": 0, "unavailable": 0}
    for m in rows:
        t = scanner.write_target(m)
        if t.get("abs_path"):
            out["local"] += 1
        elif t.get("backend") is not None:
            out["remote"] += 1
        else:
            out["unavailable"] += 1
    return out


def _meta_rows(ids: list[int] | None, lib_ids: set[int] | None) -> list[dict]:
    """rebuild-meta 作用域：给了 ids 只取这些影片（单部/少量修复，忽略库筛选），
    否则按库筛选。只取有 tmdb_id 的行。"""
    want = {int(x) for x in (ids or [])}
    rows = []
    for m in store.list_movies(grouped=False, limit=100000):
        if not m.get("tmdb_id"):
            continue
        if want:
            if int(m.get("id") or 0) in want:
                rows.append(m)
        elif _in_filter(m, lib_ids):
            rows.append(m)
    return rows


def _meta_worker(jid: str, lib_ids: set[int] | None, dry_run: bool,
                 write_art: bool = True, backdrops: bool = True,
                 ids: list[int] | None = None, write_nfo: bool = True) -> None:
    from .files import _is_file

    def _stop() -> bool:
        job = _META_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    try:
        rows = _meta_rows(ids, lib_ids)
        _META_JOBS.update(jid, total=len(rows))
        done, failed = 0, []
        for m in rows:
            if _stop():
                _META_JOBS.update(jid, done=done)
                return
            try:
                lib_id = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
                if not _is_file(lib_id, m["file_path"]):
                    raise RuntimeError("file missing")
                target = scanner.write_target(m)
                abs_p = target.get("abs_path") or ""
                backend = target.get("backend")
                rel = target.get("rel") or ""
                if not abs_p and backend is None:
                    raise RuntimeError("no writable target")
                if write_nfo:
                    scanner.sync_nfos_for(m["id"], abs_p, force=True,
                                          backend=backend, rel=rel)
                if write_art:
                    artwork.write_for_movie(m["id"], abs_p, backend=backend,
                                            rel=rel, backdrops=backdrops)
                done += 1
            except Exception as e:
                failed.append({"id": m.get("id"), "file_path": m.get("file_path"),
                               "error": str(e)[:200]})
            _META_JOBS.update(jid, done=done, failed=failed[-20:],
                              current=str(m.get("file_path") or "")[:120])
        _META_JOBS.update(jid, state="done", done=done, total=len(rows))
    except Exception as e:
        _META_JOBS.update(jid, state="failed", error=str(e)[:300])


class RebuildMetaBody(BaseModel):
    library_id: int | None = None
    media_library_id: int | None = None
    ids: list[int] | None = None   # 单部/少量影片修复（优先于库筛选；上限 500）
    dry_run: bool = True
    nfo: bool = True
    artwork: bool = True       # 同步 poster（artwork_mode=nfo_art 时）
    backdrops: bool = True     # 缺 fanart 时下载 backdrop（费流量，可关）


@router.post("/rebuild-meta")
def rebuild_meta(body: RebuildMetaBody | None = None):
    """重建媒体目录落盘（离线，不触网）：按现有匹配从 tmdb_cache 重写 NFO
    + poster/fanart（远程直读库经 backend 写 NAS）。用于手动修正匹配后同步、
    或历史误写（挂载点）后补写。dry_run=true 默认只预览。
    body.ids（单部修复）优先；否则 library_id（单库）或 media_library_id（整个媒体库）。"""
    body = body or RebuildMetaBody()
    if not body.nfo and not body.artwork:
        raise HTTPException(422, "请选择 NFO 或海报")
    try:
        ids = sorted({int(x) for x in (body.ids or [])})
    except (TypeError, ValueError):
        raise HTTPException(422, "ids must be int list")
    if len(ids) > 500:
        raise HTTPException(422, "too many ids (max 500)")
    lib_ids = None if ids else _lib_filter(body.library_id, body.media_library_id)
    rows = _meta_rows(ids, lib_ids)
    counts = _target_counts(rows)
    if body.dry_run:
        return {"dry_run": True, "total": len(rows), "ids": ids, "targets": counts,
                "sample": [m.get("file_path") for m in rows[:20]]}
    running = _META_JOBS.running()
    if running:
        if (ids or running.get("library_id") != body.library_id
                or running.get("media_library_id") != body.media_library_id
                or running.get("nfo", True) != body.nfo
                or running.get("artwork", True) != body.artwork
                or running.get("backdrops", True) != body.backdrops):
            raise HTTPException(409, "已有不同范围或选项的资料修复任务，请等待完成")
        return {"job_id": running["job_id"], "resumed": True,
                "library_id": running.get("library_id"),
                "media_library_id": running.get("media_library_id")}
    job = _META_JOBS.create(library_id=body.library_id,
                            media_library_id=body.media_library_id,
                            ids=ids, total=len(rows), targets=counts, nfo=body.nfo,
                            artwork=body.artwork, backdrops=body.backdrops)
    jid = job["job_id"]
    threading.Thread(target=_meta_worker,
                     args=(jid, lib_ids, False, body.artwork,
                           body.backdrops, ids, body.nfo), daemon=True).start()
    return {"job_id": jid, "resumed": False, "library_id": body.library_id,
            "media_library_id": body.media_library_id, "ids": ids,
            "total": len(rows), "targets": counts}


@router.get("/rebuild-meta/{job_id}")
def rebuild_meta_status(job_id: str = ""):
    job = _META_JOBS.get(job_id) if job_id else _META_JOBS.latest()
    return job or {"job_id": job_id, "state": "idle", "done": 0, "total": 0}


@router.post("/rebuild-meta/{job_id}/cancel")
def rebuild_meta_cancel(job_id: str = ""):
    if not job_id:
        running = _META_JOBS.running()
        job_id = running["job_id"] if running else ""
    return {"job_id": job_id, "state": _META_JOBS.cancel(job_id)}


_BDMV_SEG_RE = re.compile(r"(^|/)(BDMV|VIDEO_TS|CERTIFICATE|AUDIO_TS)(/|$)",
                          re.IGNORECASE)


class CleanStraysBody(BaseModel):
    library_id: int | None = None
    media_library_id: int | None = None
    dry_run: bool = True


@router.post("/clean-bdmv")
def clean_bdmv(body: CleanStraysBody | None = None):
    """清理 BDMV/VIDEO_TS 等蓝光结构里的碎片行（00000.m2ts 等，只删 DB 记录，
    不动物理文件）。dry_run=true 默认只预览。
    body.library_id（单库）或 media_library_id（整个媒体库）限定范围。"""
    body = body or CleanStraysBody()
    lib_ids = _lib_filter(body.library_id, body.media_library_id)
    rows = [m for m in store.list_movies(grouped=False, limit=100000)
            if _BDMV_SEG_RE.search(str(m.get("file_path") or ""))
            and _in_filter(m, lib_ids)]
    if body.dry_run:
        return {"dry_run": True, "total": len(rows),
                "sample": [{"id": m["id"], "file_path": m["file_path"],
                            "title": m.get("title")} for m in rows[:20]]}
    deleted, failed = 0, []
    for m in rows:
        try:
            if store.delete_movie(int(m["id"])):
                deleted += 1
        except Exception as e:
            failed.append({"id": m["id"], "error": str(e)[:200]})
    return {"dry_run": False, "total": len(rows), "deleted": deleted,
            "failed": failed}


@router.post("/clean-samples")
def clean_samples(body: CleanStraysBody | None = None):
    """清理误入库的样片/花絮行：路径按现行规则属于 Sample/Screens/Behind The Scenes
    等样片/花絮目录（含 Sample,Screens 复合名）或样片文件名的 movies 行
    （历史扫描未识别时误建）。只删 DB 行，不动物理文件；dry_run=true 默认只预览。
    body.library_id（单库）或 media_library_id（整个媒体库）限定范围。"""
    body = body or CleanStraysBody()
    lib_ids = _lib_filter(body.library_id, body.media_library_id)
    rows = []
    for m in store.list_movies(grouped=False, limit=100000):
        if not _in_filter(m, lib_ids):
            continue
        if scanner.is_sidecar(m.get("file_path") or "",
                              library_id=m.get("library_id")):
            rows.append(m)
    if body.dry_run:
        return {"dry_run": True, "total": len(rows),
                "sample": [{"id": m["id"], "file_path": m["file_path"],
                            "title": m.get("title")} for m in rows[:50]]}
    deleted, failed = 0, []
    for m in rows:
        try:
            if store.delete_movie(int(m["id"])):
                deleted += 1
        except Exception as e:
            failed.append({"id": m["id"], "error": str(e)[:200]})
    return {"dry_run": False, "total": len(rows), "deleted": deleted,
            "failed": failed}


@router.post("/clean-collections")
def clean_collections(body: CleanStraysBody | None = None):
    """清理指向已删影片的合集成员（删片前未同步的老数据）。
    只删成员行、不自动删合集；删光成员的合集 id 在 empty_ids 中返回，
    由用户手动删除。dry_run=true 默认只预览。
    body.media_library_id 限定所属媒体库（合集本就按媒体库归属）。"""
    body = body or CleanStraysBody()
    mid = body.media_library_id
    return store.prune_dangling_members(dry_run=body.dry_run,
                                        media_library_id=mid)


@router.post("/clean-mount-artifacts")
def clean_mount_artifacts(body: CleanStraysBody | None = None):
    """清理挂载点目录里被误写的媒体产物（NFO/图片，历史 bug：远程库写到了挂载点）。
    仅当该库挂载点**未真正挂载**时清理（挂载中时目录即 NAS，绝不碰）。
    dry_run=true 默认只预览。挂载点目录为 `lib_<媒体库id>`：media_library_id 直接
    指定；兼容 library_id（视频库）自动映射到所属媒体库。"""
    from ..db import mounts_dir
    body = body or CleanStraysBody()
    target_media: int | None = None
    if body.media_library_id is not None and str(body.media_library_id).strip() != "":
        try:
            target_media = int(body.media_library_id)
        except (TypeError, ValueError):
            raise HTTPException(422, "media_library_id must be int")
    elif body.library_id is not None and str(body.library_id).strip() != "":
        try:
            target_media = store.media_id_for_library(int(body.library_id)) or -1
        except (TypeError, ValueError):
            raise HTTPException(422, "library_id must be int")
    base = mounts_dir()
    items: list[str] = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if not name.startswith("lib_"):
                continue
            if target_media is not None:
                try:
                    if int(name.split("_", 1)[1]) != int(target_media):
                        continue
                except (ValueError, IndexError):
                    continue
            root = os.path.join(base, name)
            try:
                if os.path.ismount(root):
                    continue          # 真挂载：指向 NAS，禁止清理
            except OSError:
                continue
            for dirpath, _dirs, files in os.walk(root):
                for f in files:
                    if f.lower().endswith((".nfo", ".jpg", ".jpeg", ".png")):
                        items.append(os.path.relpath(os.path.join(dirpath, f), base))
    if body.dry_run:
        return {"dry_run": True, "total": len(items), "sample": items[:50]}
    removed, failed = 0, []
    for rel in items:
        p = os.path.join(base, rel)
        try:
            os.remove(p)
            removed += 1
        except OSError as e:
            failed.append({"file": rel, "error": str(e)})
    # 清空目录（自底向上）
    if os.path.isdir(base):
        for dirpath, dirs, files in os.walk(base, topdown=False):
            if dirpath == base:
                continue
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except OSError:
                pass
    return {"dry_run": False, "total": len(items), "removed": removed,
            "failed": failed}
