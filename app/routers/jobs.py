import os
import re
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import artwork, library_paths, scanner, store
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


def _scan_worker(jid: str, library_id: int | None = None,
                 force: bool = False) -> None:
    def _stop() -> bool:
        job = _SCAN_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    def _cb(done, total):
        _SCAN_JOBS.update(jid, done=int(done), total=int(total))

    try:
        kwargs = {"progress_cb": _cb, "should_stop": _stop, "force": bool(force)}
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
    force: bool = False   # 强制重扫（跳过缓存短路，重走匹配+落盘；修复错配用）


@router.post("/scan")
def scan_start(body: ScanBody | None = None):
    """启动后台扫描：{library_id?, force?} 缺省=全部启用库；立即返回 {job_id}；
    已在跑则复用（resumed）。force=true 时已有匹配的行也会重走（本地优先/TMDB）。"""
    library_id = body.library_id if body else None
    force = bool(body.force) if body else False
    running = _SCAN_JOBS.running()
    if running:
        return {"job_id": running["job_id"], "resumed": True,
                "library_id": running.get("library_id")}
    job = _SCAN_JOBS.create(library_id=library_id, force=force)
    jid = job["job_id"]
    threading.Thread(target=_scan_worker, args=(jid, library_id, force),
                     daemon=True).start()
    return {"job_id": jid, "resumed": False, "library_id": library_id,
            "force": force}


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
    library_id: int | None = None


class RefreshBody(BaseModel):
    ids: list[int] | None = None
    tmdb_ids: list[int] | None = None
    limit: int = 500
    library_id: int | None = None


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
    lib_id = body.library_id if body else None
    all_tmdb = [m for m in store.list_movies(grouped=False, limit=100000)
                if m.get("tmdb_id")
                and (lib_id is None
                     or int(m.get("library_id") or 0) == int(lib_id))]
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
        # body.library_id 指定时只刷新该库（设置页按库高级维护）
        for m in store.list_movies(grouped=False, limit=100000):
            if body.library_id is not None \
                    and int(m.get("library_id") or 0) != int(body.library_id):
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


@router.post("/rebuild-nfo")
def rebuild_nfo(body: NfoBody | None = None):
    """重建 NFO（一键收敛）：为文件仍存在的影片按收敛规则重写 NFO
    （独占单版本只留 movie.nfo 并删历史同名残留；同片多版本补各版本同名；
    共享目录只写当前同名、不碰 movie.nfo）。换机器/丢 NFO 后修复用。
    dry_run=true 只预览（报告会写/会删），默认 false 直接执行。
    body.library_id 可限定库（缺省=全库）。"""
    limit = max(1, min((body.limit if body else 2000) or 2000, 10000))
    dry_run = bool(body.dry_run) if body else False
    lib_id = body.library_id if body else None
    movies = sorted([m for m in store.list_movies(grouped=False, limit=100000)
                     if lib_id is None
                     or int(m.get("library_id") or 0) == int(lib_id)][:limit],
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
    """库状态一览（设置页展示用，纯本地聚合）；library 缺省=全库合计。"""
    from .files import _exists
    libs = store._split_ints(library)
    missing = 0
    for m in store.list_movies(grouped=False, limit=100000):
        if libs and int(m.get("library_id") or 0) not in libs:
            continue
        if not _exists(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID,
                       m["file_path"]):
            missing += 1
    base = store.library_stats(libs[0] if len(libs) == 1 else None)
    return {**base, "missing_files": missing}


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


def _meta_worker(jid: str, library_id: int | None, dry_run: bool,
                 write_art: bool = True, backdrops: bool = True) -> None:
    from .files import _is_file

    def _stop() -> bool:
        job = _META_JOBS.get(jid)
        return job is None or job.get("state") != "running"

    try:
        rows = [m for m in store.list_movies(grouped=False, limit=100000)
                if m.get("tmdb_id")
                and (library_id is None
                     or int(m.get("library_id") or 0) == int(library_id))]
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
    dry_run: bool = True
    artwork: bool = True       # 同步 poster（artwork_mode=nfo_art 时）
    backdrops: bool = True     # 缺 fanart 时下载 backdrop（费流量，可关）


@router.post("/rebuild-meta")
def rebuild_meta(body: RebuildMetaBody | None = None):
    """重建媒体目录落盘（离线，不触网）：按现有匹配从 tmdb_cache 重写 NFO
    + poster/fanart（远程直读库经 backend 写 NAS）。用于手动修正匹配后同步、
    或历史误写（挂载点）后补写。dry_run=true 默认只预览。"""
    body = body or RebuildMetaBody()
    library_id = body.library_id
    rows = [m for m in store.list_movies(grouped=False, limit=100000)
            if m.get("tmdb_id")
            and (library_id is None
                 or int(m.get("library_id") or 0) == int(library_id))]
    counts = _target_counts(rows)
    if body.dry_run:
        return {"dry_run": True, "total": len(rows), "targets": counts,
                "sample": [m.get("file_path") for m in rows[:20]]}
    running = _META_JOBS.running()
    if running:
        return {"job_id": running["job_id"], "resumed": True,
                "library_id": running.get("library_id")}
    job = _META_JOBS.create(library_id=library_id, total=len(rows), targets=counts)
    jid = job["job_id"]
    threading.Thread(target=_meta_worker,
                     args=(jid, library_id, False, body.artwork,
                           body.backdrops), daemon=True).start()
    return {"job_id": jid, "resumed": False, "library_id": library_id,
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
    dry_run: bool = True


@router.post("/clean-bdmv")
def clean_bdmv(body: CleanStraysBody | None = None):
    """清理 BDMV/VIDEO_TS 等蓝光结构里的碎片行（00000.m2ts 等，只删 DB 记录，
    不动物理文件）。dry_run=true 默认只预览。"""
    body = body or CleanStraysBody()
    rows = [m for m in store.list_movies(grouped=False, limit=100000)
            if _BDMV_SEG_RE.search(str(m.get("file_path") or ""))
            and (body.library_id is None
                 or int(m.get("library_id") or 0) == int(body.library_id))]
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


@router.post("/clean-mount-artifacts")
def clean_mount_artifacts(body: CleanStraysBody | None = None):
    """清理挂载点目录里被误写的媒体产物（NFO/图片，历史 bug：远程库写到了挂载点）。
    仅当该库挂载点**未真正挂载**时清理（挂载中时目录即 NAS，绝不碰）。
    dry_run=true 默认只预览。"""
    from ..db import mounts_dir
    body = body or CleanStraysBody()
    base = mounts_dir()
    items: list[str] = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if not name.startswith("lib_"):
                continue
            if body.library_id is not None:
                try:
                    if int(name.split("_", 1)[1]) != int(body.library_id):
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
