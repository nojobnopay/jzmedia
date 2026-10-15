"""时间轴预览：限并发抽帧、分页拼图与独立持久缓存。只读媒体，写 DATA_DIR。

每帧输入侧 seek，避免为低分辨率预览全片解码；每页提交一次索引，取消/重启可续做。
新入库项由空闲时段的持久队列处理，手动任务通过 POST 发起；悬停 GET 不启动 ffmpeg。
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Literal

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ... import data_usage, media, store
from ...config import settings
from ...jobkit import JobRegistry
from ...log import get_logger
from .common import _media_cached_or_probe, _version_source, _sessions, _sess_lock, router

logger = get_logger("stream.previews")
__all__ = ['shutdown_previews', 'start_auto_previews']
VERSION = 1
WIDTH, HEIGHT, COLUMNS, ROWS = 160, 90, 5, 5
_jobs = JobRegistry(max_finished=12, prefix="preview")
_lock = threading.RLock()
_worker_thread = None
_active_job = None
_stop = threading.Event()
_auto_wake = threading.Event()
_auto_thread = None
_auto_queue = []
_auto_job_id = None
_KEY_RE = re.compile(r"^[a-f0-9]{32}$")
_IMAGE_RE = re.compile(r"^page_\d{4}\.jpg$")


def _root() -> Path:
    return Path(settings.data_dir) / "previews"


def _cache_key(row, src, interval: int) -> str:
    # 不对远程电影做全文件 hash；路径/库/大小/mtime 变化即失效。
    identity = [VERSION, row.get("kind", "movie"), row["id"], src.backend.library_id,
                src.backend.root, src.rel, src.size, src.mtime, interval, WIDTH, HEIGHT]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:32]


def _interval(row) -> int:
    lib = store.get_library(row.get("library_id") or 1) or {}
    default = 10 if lib.get("source", "local") == "local" else 20
    try:
        return max(5, min(60, int(os.getenv("PREVIEW_INTERVAL", default))))
    except (TypeError, ValueError):
        return default


def _read_manifest(directory: Path):
    try:
        data = json.loads((directory / "manifest.json").read_text())
        if not isinstance(data, dict) or data.get("version") != VERSION:
            return None
        pages = data.get("pages", [])
        if not isinstance(pages, list) or not all(isinstance(n, str) and _IMAGE_RE.fullmatch(n) and (directory / n).is_file() for n in pages):
            return None
        return data
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as e:
        logger.warning("read preview manifest failed key=%s: %s", directory.name, e)
        return None


def _write_manifest(directory: Path, data: dict):
    dest = directory / "manifest.json"
    tmp = directory / "manifest.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(dest)


def _public_manifest(key, data):
    result = dict(data or {})
    result["pages"] = [f"/api/stream/previews/cache/{key}/{name}" for name in result.get("pages", [])]
    result["key"] = key
    return result


def _cancelled(jid):
    job = _jobs.get(jid)
    return (_stop.is_set() or not job or job["state"] != "running"
            or (job.get("auto") and (_playback_busy() or not _auto_allowed_now())))


def _playback_busy() -> bool:
    now = time.time()
    with _sess_lock:
        return any(s.get("prewarm") or now - float(s.get("last_ping") or now) < 90
                   for s in _sessions.values())


class _Cancelled(Exception):
    pass


def _run(cmd, jid, timeout=60):
    """短进程分批运行，取消/停服在 200ms 轮询处终止并 wait，无孤儿 ffmpeg。"""
    if _cancelled(jid):
        raise _Cancelled()
    prefix = [shutil.which("nice"), "-n", "10"] if shutil.which("nice") else []
    # 日志落临时文件，避免 PIPE 未读满导致子进程卡死；只向用户返回固定错误。
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(prefix + cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=log)
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if _cancelled(jid):
                    raise _Cancelled()
                if time.monotonic() > deadline:
                    raise RuntimeError("抽帧超时，请稍后重试")
                _stop.wait(0.2)
            if proc.returncode:
                raise RuntimeError("无法生成预览（编码或色彩转换不受支持）")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)


def _frame_filter(info):
    dv = int(info.get("dv_profile") or 0)
    compat = int(info.get("dv_bl_compat") or 0)
    if dv and compat not in (1, 2, 4):
        raise RuntimeError("该 Dolby Vision 片源无可用兼容基底，暂不支持预览")
    hdr = info.get("hdr") or ("hdr10" if dv and compat == 1 else "hlg" if dv and compat == 4 else "")
    color = ""
    if hdr and not (dv and compat == 2):
        # 先缩小再软件 tone-map；无 zscale 的构建明确报失败，避免静默生成灰图。
        color = "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
    return (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
            + color + f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuvj420p")


def _generate(jid, row, src, info, interval):
    key = _cache_key(row, src, interval)
    directory = _root() / key
    duration = float(info.get("duration") or 0)
    if not math.isfinite(duration) or not 0 < duration <= 86400:
        raise RuntimeError("无法取得有效时长（支持 24 小时以内的视频）")
    count = max(1, math.ceil(duration / interval))
    manifest = _read_manifest(directory)
    if manifest and manifest.get("state") == "ready":
        return
    directory.mkdir(parents=True, exist_ok=True)
    manifest = manifest or dict(version=VERSION, interval=interval, duration=duration,
                                width=WIDTH, height=HEIGHT, columns=COLUMNS, rows=ROWS,
                                count=count, pages=[], state="partial")
    manifest.update(item_kind=row.get("kind", "movie"), item_id=int(row["id"]))
    filter_arg = _frame_filter(info)
    ffmpeg = media.ffmpeg_bin()
    cells = COLUMNS * ROWS
    with tempfile.TemporaryDirectory(prefix=".frames-", dir=directory) as tmp:
        for page in range(len(manifest["pages"]), math.ceil(count / cells)):
            if _cancelled(jid):
                raise _Cancelled()
            size = min(cells, count - page * cells)
            for cell in range(size):
                if (_jobs.get(jid) or {}).get("library_id") is not None:
                    _wait_for_transcodes(jid)
                frame = page * cells + cell
                # 每次只读目标附近的 GOP，音轨/字幕不参与，线程数限制到 1。
                cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                       "-threads", "1", "-filter_threads", "1", "-ss", str(frame * interval),
                       "-rw_timeout", "30000000", "-i", src.input,
                       "-map", "0:v:0", "-an", "-sn", "-frames:v", "1", "-vf", filter_arg,
                       "-threads", "1", "-q:v", "5", str(Path(tmp) / f"frame_{cell:02d}.jpg")]
                _run(cmd, jid)
                _jobs.update(jid, frames=frame + 1, frame_total=count)
            name = f"page_{page:04d}.jpg"
            temp_page = Path(tmp) / name
            _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                  "-threads", "1", "-filter_threads", "1", "-framerate", "1", "-i",
                  str(Path(tmp) / "frame_%02d.jpg"), "-vf", f"tile={COLUMNS}x{ROWS}:nb_frames={size}",
                  "-frames:v", "1", "-threads", "1", "-q:v", "5", str(temp_page)], jid)
            temp_page.replace(directory / name)
            manifest["pages"].append(name)
            manifest["state"] = "ready" if (page + 1) * cells >= count else "partial"
            _write_manifest(directory, manifest)
            for img in Path(tmp).glob("frame_*.jpg"):
                img.unlink()
    _prune_cache(protect=key)
    data_usage.invalidate()


def _prune_cache(protect=""):
    cap = _cache_cap_bytes()
    entries = []
    for directory in _root().glob("*"):
        if not directory.is_dir() or not _KEY_RE.fullmatch(directory.name):
            continue
        try:
            size = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
            entries.append((directory.stat().st_mtime, size, directory))
        except OSError as e:
            logger.debug("preview cache stat failed key=%s: %s", directory.name, e)
    total = sum(e[1] for e in entries)
    for accessed, size, directory in sorted(entries):
        if total <= cap:
            break
        if directory.name == protect or time.time() - accessed < 600:
            continue
        try:
            shutil.rmtree(directory)
            total -= size
            data_usage.invalidate()
        except OSError as e:
            logger.warning("preview cache eviction failed key=%s: %s", directory.name, e)


def _cache_cap_bytes() -> int:
    try:
        return int(max(0.1, float(os.getenv("PREVIEW_CACHE_GB", "10"))) * 1024 ** 3)
    except (TypeError, ValueError, OverflowError):
        return 10 * 1024 ** 3


def _wait_for_transcodes(jid):
    while True:
        if _cancelled(jid):
            raise _Cancelled()
        with _sess_lock:
            active = any(s.get("proc") is not None and s["proc"].poll() is None for s in _sessions.values())
        _jobs.update(jid, waiting=active)
        if not active:
            return
        _stop.wait(1)


def _worker(jid, items, batch):
    failures = []
    try:
        for index, (kind, vid) in enumerate(items):
            if _cancelled(jid):
                raise _Cancelled()
            # 库级任务在有在线转码时等待；单片由用户显式请求，可边播边低优先级生成。
            if batch:
                _wait_for_transcodes(jid)
            _jobs.update(jid, waiting=False, current_kind=kind, current_id=vid, frames=0, frame_total=0)
            try:
                row, src = _version_source(vid, kind)
                _jobs.update(jid, current=Path(src.rel).name)
                info = _media_cached_or_probe(row, src)
                if not info.get("playable"):
                    raise RuntimeError("片源不可播放")
                _generate(jid, row, src, info, _interval(row))
            except _Cancelled:
                raise
            except Exception as e:
                message = str(e.detail) if isinstance(e, HTTPException) else str(e)
                logger.warning("preview generation failed kind=%s id=%s: %s", kind, vid, message)
                failures.append({"kind": kind, "id": vid, "error": message})
            _jobs.update(jid, done=index + 1, failed=failures[-100:], failure_count=len(failures))
        _jobs.update(jid, state="failed" if len(failures) == len(items) and items else "done",
                     error=failures[-1]["error"] if len(failures) == len(items) and items else "")
    except _Cancelled:
        _jobs.update(jid, state="cancelled")
    except Exception as e:
        logger.warning("preview worker failed: %s", e)
        _jobs.update(jid, state="failed", error="预览任务失败，请重试")
    finally:
        if (_jobs.get(jid) or {}).get("auto"):
            _auto_wake.set()


def _auto_mode() -> str:
    mode = os.getenv("PREVIEW_AUTO_MODE", "all").strip().lower()
    return mode if mode in ("off", "local", "all") else "all"


def _auto_library(lib) -> bool:
    return bool(lib and lib.get("effective_enabled") and _auto_mode() != "off"
                and (_auto_mode() == "all" or lib.get("source") == "local"))


def auto_scan_snapshot(libraries) -> dict:
    """Capture existing item IDs before a scan; upgrades do not enqueue the old library."""
    return {int(lib["id"]): set(store.preview_items(lib["id"]))
            for lib in libraries if _auto_library(lib)}


def _queue_path() -> Path:
    return _root() / "auto-queue.json"


def _save_auto_queue() -> None:
    path = _queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(_auto_queue), encoding="utf-8")
    temp.replace(path)


def _enqueue_auto_items(items: list[dict]) -> int:
    with _lock:
        seen = {(x["kind"], x["id"]) for x in _auto_queue}
        unique = []
        for item in items:
            key = (item["kind"], item["id"])
            if key not in seen:
                unique.append(item)
                seen.add(key)
        if not unique:
            return 0
        _auto_queue.extend(unique)
        try:
            _save_auto_queue()
        except OSError:
            del _auto_queue[-len(unique):]
            raise
    _auto_wake.set()
    return len(unique)


def enqueue_scan_previews(before: dict, failed_libraries=()) -> int:
    """Persist newly discovered items; preview generation never delays scan completion."""
    failed = set(failed_libraries)
    added = []
    for lid, old in before.items():
        if lid in failed or not _auto_library(store.get_library(lid)):
            continue
        for kind, vid in sorted(set(store.preview_items(lid)) - old):
            added.append({"kind": kind, "id": vid, "library_id": lid})
    if not added:
        return 0
    return _enqueue_auto_items(added)


def _auto_head_matches(job) -> bool:
    items = (job or {}).get("items") or []
    return bool(_auto_queue and items and tuple(items[0]) ==
                (_auto_queue[0]["kind"], _auto_queue[0]["id"]))


def _auto_hours() -> tuple[int, int]:
    try:
        start = int(os.getenv("PREVIEW_AUTO_START_HOUR", "3"))
        end = int(os.getenv("PREVIEW_AUTO_END_HOUR", "6"))
        if not 0 <= start <= 23 or not 0 <= end <= 23:
            raise ValueError()
    except ValueError:
        start, end = 3, 6
    return start, end


def _auto_allowed_now() -> bool:
    start, end = _auto_hours()
    hour = time.localtime().tm_hour
    return start <= hour < end if start < end else (hour >= start or hour < end)


def _auto_pause_reason(usage: dict | None = None) -> str:
    if _auto_mode() == "off":
        return "disabled"
    usage = usage or data_usage.usage()
    if not usage["ok"]:
        return "usage_unavailable"
    if usage["over_limit"]:
        return "data_limit"
    if usage["categories"]["previews"] >= int(_cache_cap_bytes() * 0.9):
        return "preview_limit"
    if _playback_busy():
        return "playback"
    if not _auto_allowed_now():
        return "scheduled"
    return ""


def _auto_loop() -> None:
    global _auto_job_id
    while not _stop.is_set():
        _auto_wake.wait(30)
        _auto_wake.clear()
        if _stop.is_set():
            break
        with _lock:
            if _auto_job_id:
                job = _jobs.get(_auto_job_id)
                if job and job["state"] == "running":
                    continue
                if job and job["state"] in ("done", "failed") and _auto_head_matches(job):
                    finished = _auto_queue.pop(0)
                    try:
                        _save_auto_queue()
                    except OSError as e:
                        _auto_queue.insert(0, finished)
                        logger.warning("save automatic preview queue failed: %s", e)
                _auto_job_id = None
            if not _auto_queue:
                continue
            item = dict(_auto_queue[0])
        if _auto_pause_reason():
            continue
        if not _auto_library(store.get_library(item["library_id"])):
            with _lock:
                if _auto_queue and _auto_queue[0] == item:
                    skipped = _auto_queue.pop(0)
                    try:
                        _save_auto_queue()
                    except OSError as e:
                        _auto_queue.insert(0, skipped)
                        logger.warning("save automatic preview queue failed: %s", e)
            continue
        try:
            with _lock:
                if not _auto_queue or _auto_queue[0] != item:
                    continue
                job = _start([(item["kind"], item["id"])], auto=True)
                _auto_job_id = job["job_id"]
        except HTTPException as e:
            if e.status_code != 409:
                logger.warning("automatic preview start failed: %s", e.detail)
            continue
        _auto_wake.set()


def start_auto_previews() -> None:
    global _auto_thread, _auto_queue, _auto_job_id
    with _lock:
        if _auto_thread and _auto_thread.is_alive():
            return
        _stop.clear()
        _auto_job_id = None
        try:
            saved = json.loads(_queue_path().read_text(encoding="utf-8"))
            if not isinstance(saved, list):
                raise ValueError("automatic preview queue must be a list")
            _auto_queue = [x for x in saved if isinstance(x, dict)
                           and x.get("kind") in ("movie", "episode", "extra")
                           and isinstance(x.get("id"), int) and x["id"] > 0
                           and isinstance(x.get("library_id"), int) and x["library_id"] > 0]
        except FileNotFoundError:
            _auto_queue = []
        except (OSError, ValueError, TypeError) as e:
            logger.warning("read automatic preview queue failed: %s", e)
            _auto_queue = []
        _auto_thread = threading.Thread(target=_auto_loop, name="preview-auto", daemon=True)
        _auto_thread.start()
    _auto_wake.set()


def _start(items, library_id=None, auto=False):
    global _worker_thread, _active_job
    previous = None
    with _lock:
        if _worker_thread and _worker_thread.is_alive():
            job = _jobs.get(_active_job)
            if job and job["state"] == "running" and job.get("items") == items:
                return _public_job(job)
            if job and job.get("auto") and not auto:
                _jobs.cancel(_active_job)
                previous = _worker_thread
            else:
                raise HTTPException(409, "已有预览任务在运行，请完成或取消后重试")
    if previous:
        previous.join(timeout=3)
    with _lock:
        if _worker_thread and _worker_thread.is_alive():
            raise HTTPException(409, "已有预览任务在运行，请稍后重试")
        _stop.clear()
        job = _jobs.create(items=items, library_id=library_id, total=len(items), auto=auto)
        _active_job = job["job_id"]
        _worker_thread = threading.Thread(target=_worker, args=(_active_job, items, library_id is not None or auto), daemon=True)
        _worker_thread.start()
        return _public_job(job)


def _public_job(job):
    return {k: v for k, v in job.items() if k != "items"}


def shutdown_previews():
    _stop.set()
    _auto_wake.set()
    if _worker_thread:
        _worker_thread.join(timeout=5)
    if _auto_thread:
        _auto_thread.join(timeout=5)


@router.get("/previews/auto")
def preview_auto_status():
    with _lock:
        job = _jobs.get(_auto_job_id)
        queued = len(_auto_queue)
        running = bool(job and job["state"] == "running")
    start, end = _auto_hours()
    return {"mode": _auto_mode(), "queued": queued, "running": running,
            "start_hour": start, "end_hour": end, "pause_reason": _auto_pause_reason()}


def _queue_item_public(item: dict) -> dict:
    row = store.get_playable(item["kind"], item["id"]) or {}
    file_name = Path(row.get("file_path") or "").name
    return {"kind": item["kind"], "id": item["id"],
            "library_id": item.get("library_id") or row.get("library_id"),
            "title": row.get("display_title") or row.get("title") or file_name or "已移除的影片",
            "file_name": file_name}


def _pending_auto_queue() -> list[dict]:
    """Call with _lock held. The active automatic item is managed separately."""
    pending = list(_auto_queue)
    job = _jobs.get(_auto_job_id)
    if job and job["state"] == "running" and _auto_head_matches(job):
        pending = pending[1:]
    return pending


def _queue_revision(items: list[dict]) -> str:
    keys = [(item["kind"], item["id"], item["library_id"]) for item in items]
    return hashlib.sha256(json.dumps(keys, separators=(",", ":")).encode()).hexdigest()[:16]


@router.get("/previews/queue")
def preview_queue(library_id: int | None = Query(default=None, gt=0),
                  offset: int = Query(default=0, ge=0),
                  limit: int = Query(default=50, ge=1, le=100)):
    if library_id is not None and not store.get_library(library_id):
        raise HTTPException(404, "视频库不存在")
    with _lock:
        pending = _pending_auto_queue()
        job = _jobs.get(_active_job)
        running = None
        if job and job["state"] == "running":
            items = job.get("items") or []
            index = int(job.get("done") or 0)
            if index < len(items):
                kind, vid = items[index]
                running = {"kind": kind, "id": vid, "library_id": job.get("library_id")}
                if job.get("auto") and _auto_head_matches(job):
                    running["library_id"] = _auto_queue[0]["library_id"]
                running.update(job_id=job["job_id"], auto=bool(job.get("auto")),
                               waiting=bool(job.get("waiting")),
                               frames=(job.get("frames") or 0) if
                               (job.get("current_kind"), job.get("current_id")) == (kind, vid) else 0,
                               frame_total=(job.get("frame_total") or 0) if
                               (job.get("current_kind"), job.get("current_id")) == (kind, vid) else 0,
                               done=index, total=job.get("total") or len(items))
    if library_id is not None:
        pending = [item for item in pending if item["library_id"] == library_id]
    total = len(pending)
    revision = _queue_revision(pending)
    page = [_queue_item_public(item) for item in pending[offset:offset + limit]]
    if running:
        running = _queue_item_public(running) | {k: v for k, v in running.items()
                                                 if k not in ("kind", "id", "library_id")}
        if library_id is not None and running["library_id"] != library_id:
            running = None
    return {"items": page, "total": total, "offset": offset, "limit": limit,
            "revision": revision,
            "running": running}


class PreviewQueueBody(BaseModel):
    library_id: int = Field(gt=0)


@router.post("/previews/queue")
def preview_queue_existing(body: PreviewQueueBody):
    """Add unfinished videos from an existing local library to the idle queue."""
    lib = store.get_library(body.library_id)
    if not lib:
        raise HTTPException(404, "视频库不存在")
    if lib.get("source") != "local":
        raise HTTPException(400, "当前仅支持将已有的本地视频加入闲时队列")
    if not _auto_library(lib):
        raise HTTPException(409, "该视频库未启用自动预览生成")
    items = store.preview_items(body.library_id)
    with _lock:
        queued = {(entry["kind"], entry["id"]) for entry in _auto_queue}
    candidates = []
    ready = unavailable = already_queued = 0
    for kind, vid in items:
        if (kind, vid) in queued:
            already_queued += 1
            continue
        try:
            row, src = _version_source(vid, kind)
        except HTTPException:
            unavailable += 1
            continue
        manifest = _read_manifest(_root() / _cache_key(row, src, _interval(row)))
        if manifest and manifest.get("state") == "ready":
            ready += 1
            continue
        candidates.append({"kind": kind, "id": vid, "library_id": body.library_id})
    try:
        added = _enqueue_auto_items(candidates)
    except OSError:
        raise HTTPException(503, "保存预览队列失败，请稍后重试") from None
    return {"total": len(items), "added": added, "ready": ready,
            "unavailable": unavailable, "already_queued": already_queued + len(candidates) - added}


class PreviewQueueItemRef(BaseModel):
    kind: Literal["movie", "episode", "extra"]
    id: int = Field(gt=0)


class PreviewQueueRemoveBody(BaseModel):
    library_id: int = Field(gt=0)
    revision: str = Field(min_length=16, max_length=16)
    all_pending: bool = False
    items: list[PreviewQueueItemRef] = Field(default_factory=list)
    exclude: list[PreviewQueueItemRef] = Field(default_factory=list)


@router.post("/previews/queue/remove")
def preview_queue_remove_many(body: PreviewQueueRemoveBody):
    """Atomically remove selected pending entries; never cancel the active item."""
    if not store.get_library(body.library_id):
        raise HTTPException(404, "视频库不存在")
    if not body.all_pending and not body.items:
        raise HTTPException(400, "请先选择待处理影片")
    with _lock:
        pending = [item for item in _pending_auto_queue()
                   if item["library_id"] == body.library_id]
        if _queue_revision(pending) != body.revision:
            raise HTTPException(409, "队列已变化，请重新选择后重试")
        pending_keys = {(item["kind"], item["id"]) for item in pending}
        requested = ({(item.kind, item.id) for item in body.items}
                     if not body.all_pending else
                     pending_keys - {(item.kind, item.id) for item in body.exclude})
        targets = pending_keys & requested
        if not targets:
            return {"removed": 0}
        before = list(_auto_queue)
        _auto_queue[:] = [item for item in _auto_queue
                          if not (item["library_id"] == body.library_id and
                                  (item["kind"], item["id"]) in targets)]
        removed = len(before) - len(_auto_queue)
        try:
            _save_auto_queue()
        except OSError:
            _auto_queue[:] = before
            raise HTTPException(503, "保存预览队列失败，请稍后重试") from None
    _auto_wake.set()
    return {"removed": removed}


@router.delete("/previews/queue/{kind}/{version_id}")
def preview_queue_remove(kind: Literal["movie", "episode", "extra"], version_id: int,
                         library_id: int = Query(gt=0)):
    with _lock:
        index = next((i for i, item in enumerate(_auto_queue)
                      if (item["kind"], item["id"], item["library_id"]) ==
                      (kind, version_id, library_id)), None)
        if index is None:
            raise HTTPException(404, "该影片已不在待处理队列中")
        job = _jobs.get(_auto_job_id)
        running = bool(index == 0 and job and job["state"] == "running"
                       and _auto_head_matches(job))
        item = _auto_queue.pop(index)
        try:
            _save_auto_queue()
        except OSError:
            _auto_queue.insert(index, item)
            raise HTTPException(503, "保存预览队列失败，请稍后重试") from None
        if running:
            _jobs.cancel(_auto_job_id)
    _auto_wake.set()
    return {"removed": True, "cancelled": running}


@router.get("/{version_id}/previews")
def preview_manifest(version_id: int, kind: Literal["movie", "episode", "extra"] = "movie"):
    row, src = _version_source(version_id, kind)
    key = _cache_key(row, src, _interval(row))
    directory = _root() / key
    data = _read_manifest(directory)
    if data and (data.get("item_kind") != kind or data.get("item_id") != version_id):
        # Upgrade old cache ownership only after resolving the authorized source.
        data.update(item_kind=kind, item_id=version_id)
        _write_manifest(directory, data)
    result = _public_manifest(key, data)
    result["state"] = "ready" if data and data.get("state") == "ready" else "missing"
    job = _jobs.get(_active_job)
    if job and (kind, version_id) in job.get("items", []) and result["state"] != "ready":
        result.update(state=job["state"] if job["state"] != "done" else "missing",
                      job_id=job["job_id"], error=job.get("error", ""))
        if result["state"] == "running":
            result["waiting"] = bool(job.get("waiting"))
            if (job.get("current_kind"), job.get("current_id")) == (kind, version_id):
                result["frames"] = job.get("frames", 0)
                result["frame_total"] = job.get("frame_total", 0)
            else:
                result["queued"] = True
    if directory.is_dir():
        try:
            os.utime(directory, None)
        except OSError as e:
            logger.debug("preview touch failed: %s", e)
    return result


@router.post("/{version_id}/previews")
def preview_start(version_id: int, kind: Literal["movie", "episode", "extra"] = "movie"):
    _version_source(version_id, kind)  # 拒绝不存在/离线文件；后续 worker 再检查一次
    return _start([(kind, version_id)])


class PreviewBatchBody(BaseModel):
    library_id: int = Field(gt=0)


@router.post("/previews/jobs")
def preview_batch(body: PreviewBatchBody):
    if not store.get_library(body.library_id):
        raise HTTPException(404, "视频库不存在")
    return _start(store.preview_items(body.library_id), body.library_id)


@router.get("/previews/jobs/latest")
def preview_latest(library_id: int):
    job = _jobs.get(_active_job)
    return _public_job(job) if job and job.get("library_id") == library_id else {"state": "idle"}


@router.get("/previews/jobs/{job_id}")
def preview_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "预览任务不存在")
    return _public_job(job)


@router.post("/previews/jobs/{job_id}/cancel")
def preview_cancel(job_id: str):
    _jobs.cancel(job_id)
    return preview_job(job_id)


class PreviewCacheCleanBody(BaseModel):
    dry_run: bool = True


@router.post("/previews/cache/clean")
def preview_cache_clean(body: PreviewCacheCleanBody):
    """Two-step user cleanup, excluding queue metadata and any active generator."""
    with _lock:
        if _worker_thread and _worker_thread.is_alive():
            raise HTTPException(409, "进度缩略图正在生成，请完成后再清理")
        directories = [d for d in _root().glob("*") if d.is_dir() and _KEY_RE.fullmatch(d.name)]
        sizes = []
        for directory in directories:
            try:
                sizes.append((directory, sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())))
            except OSError:
                continue
        result = {"candidates": len(sizes), "candidate_bytes": sum(size for _, size in sizes),
                  "dry_run": body.dry_run, "removed": 0, "freed": 0}
        if not body.dry_run:
            for directory, size in sizes:
                try:
                    shutil.rmtree(directory)
                    result["removed"] += 1
                    result["freed"] += size
                except OSError as e:
                    logger.warning("preview cache cleanup failed key=%s: %s", directory.name, e)
            data_usage.invalidate()
        return result


@router.get("/previews/cache/{key}/{name}")
def preview_image(key: str, name: str):
    if not _KEY_RE.fullmatch(key) or not _IMAGE_RE.fullmatch(name):
        raise HTTPException(404, "预览图片不存在")
    dest = _root() / key / name
    if not dest.is_file():
        raise HTTPException(404, "预览图片不存在")
    from ...viewing import current, visible_item
    if current.get() is not None:
        manifest = _read_manifest(dest.parent) or {}
        if not manifest.get("item_id"):
            raise HTTPException(404, "请先重新打开影片预览")
        visible_item(manifest.get("item_kind", "movie"), int(manifest["item_id"]))
    try:
        os.utime(dest.parent, None)
    except OSError as e:
        logger.debug("preview image touch failed key=%s: %s", key, e)
    return FileResponse(dest, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400, immutable"})
