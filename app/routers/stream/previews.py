"""时间轴预览：限并发抽帧、分页拼图与独立持久缓存。只读媒体，写 DATA_DIR。

本地优先批量抽取关键帧，远程/不兼容片源按点读取；每页提交索引，取消/重启可续做。
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

from ... import data_usage, storage, store
from ...config import settings
from ...jobkit import JobRegistry
from ...log import get_logger
from .common import _media_cached_or_probe, _version_source, _sessions, _sess_lock, router
from .preview_extract import Extractor
from .preview_log import ExtractionError, error_kind, read_frame_log

logger = get_logger("stream.previews")
__all__ = ['shutdown_previews', 'start_auto_previews']
VERSION = 1
QUALITY_REVISION = 2
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
_ready_queue_check = (None, 0.0)
_KEY_RE = re.compile(r"^[a-f0-9]{32}$")
_IMAGE_RE = re.compile(r"^page_\d{4}\.jpg$")


def _root() -> Path:
    return Path(settings.data_dir) / "previews"


def _cache_key(row, src, interval: int, *, quality_revision: int | None = None) -> str:
    # 不对远程电影做全文件 hash；路径/库/大小/mtime 变化即失效。
    identity = [VERSION, row.get("kind", "movie"), row["id"], src.backend.library_id,
                src.backend.root, src.rel, src.size, src.mtime, interval, WIDTH, HEIGHT]
    revision = QUALITY_REVISION if quality_revision is None else quality_revision
    # 第一代 key 必须保持原样；新版图片另用 URL，避免覆盖 immutable 旧图。
    if revision != 1:
        identity.append(revision)
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
        return any(not getattr(s.get("proc"), "_jz_buffer_paused", False)
                   and ((s.get("proc") is not None and s["proc"].poll() is None)
                        or (not (s.get("_flow_state") or {}).get("paused", False)
                            and (s.get("prewarm") or now - float(s.get("last_ping") or now) < 90)))
                   for s in _sessions.values())


class _Cancelled(Exception):
    pass


class _YieldToPlayback(Exception):
    pass


def _transcoding_busy():
    with _sess_lock:
        return any(s.get("proc") is not None and s["proc"].poll() is None
                   and not getattr(s["proc"], "_jz_buffer_paused", False)
                   for s in _sessions.values())


def _run(cmd, jid, timeout=60):
    """进程结束立即唤醒；每 200ms 检查取消/播放让行，终止后回收子进程。"""
    if _cancelled(jid):
        raise _Cancelled()
    prefix = [shutil.which("nice"), "-n", "10"] if shutil.which("nice") else []
    # 日志落临时文件，避免 PIPE 未读满导致子进程卡死；只向用户返回固定错误。
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(prefix + cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=log)
        started = time.monotonic()
        deadline = started + timeout
        try:
            while True:
                if _cancelled(jid):
                    raise _Cancelled()
                if (_jobs.get(jid) or {}).get("library_id") is not None and _transcoding_busy():
                    raise _YieldToPlayback()
                if time.monotonic() > deadline:
                    raise ExtractionError("抽帧超时，请稍后重试", reason="timeout", timed_out=True,
                                          elapsed=time.monotonic() - started, limit=timeout,
                                          stderr_bytes=os.fstat(log.fileno()).st_size)
                try:
                    proc.wait(timeout=min(0.2, max(0.001, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    pass
            if proc.returncode:
                raise ExtractionError("无法生成预览（编码或色彩转换不受支持）", reason="exit",
                                      returncode=proc.returncode, elapsed=time.monotonic() - started,
                                      limit=timeout, stderr_bytes=os.fstat(log.fileno()).st_size,
                                      error_kind=error_kind(log))
            return read_frame_log(log, time.monotonic() - started)
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
    return (f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            + color + f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=rgb24")


def _remove_old_quality_cache(row, src, interval, manifest, count):
    """新版完整落盘后，仅清理相同片源和采样配置的旧画质缓存。"""
    pages = [f"page_{page:04d}.jpg" for page in range(math.ceil(count / (COLUMNS * ROWS)))]
    if (manifest.get("state") != "ready" or manifest.get("quality_revision") != QUALITY_REVISION
            or manifest.get("count") != count or manifest.get("pages") != pages):
        return
    for revision in range(1, QUALITY_REVISION):
        key = _cache_key(row, src, interval, quality_revision=revision)
        try:
            shutil.rmtree(_root() / key)
        except FileNotFoundError:
            continue
        except OSError as e:
            logger.warning("remove old preview quality cache failed key=%s: %s", key, e)
        else:
            data_usage.invalidate()


def _generate(jid, row, src, info, interval):
    key = _cache_key(row, src, interval)
    directory = _root() / key
    duration = float(info.get("duration") or 0)
    if not math.isfinite(duration) or not 0 < duration <= 86400:
        raise RuntimeError("无法取得有效时长（支持 24 小时以内的视频）")
    count = max(1, math.ceil(duration / interval))
    manifest = _read_manifest(directory)
    if manifest and manifest.get("state") == "ready":
        _remove_old_quality_cache(row, src, interval, manifest, count)
        return
    directory.mkdir(parents=True, exist_ok=True)
    manifest = manifest or dict(version=VERSION, interval=interval, duration=duration,
                                width=WIDTH, height=HEIGHT, columns=COLUMNS, rows=ROWS,
                                count=count, pages=[], state="partial")
    manifest.update(item_kind=row.get("kind", "movie"), item_id=int(row["id"]),
                    quality_revision=QUALITY_REVISION)
    filter_arg = _frame_filter(info)
    cells = COLUMNS * ROWS
    lib = store.get_library(row.get("library_id") or 1) or {}
    remote = lib.get("source", "local") != "local" or src.input.startswith(("http://", "https://"))
    extractor = Extractor(src, filter_arg, interval, remote=remote, run=_run, jid=jid,
                          context={"kind": row.get("kind", "movie"), "id": row["id"]},
                          progress=lambda frames: _jobs.update(jid, frames=frames, frame_total=count))
    started = time.monotonic()
    completed_pages = len(manifest["pages"])
    _jobs.update(jid, frames=min(len(manifest["pages"]) * cells, count), frame_total=count)
    with tempfile.TemporaryDirectory(prefix=".frames-", dir=directory) as tmp:
        for page in range(len(manifest["pages"]), math.ceil(count / cells)):
            if _cancelled(jid):
                raise _Cancelled()
            size = min(cells, count - page * cells)
            name = f"page_{page:04d}.jpg"
            temp_page = Path(tmp) / name
            while True:
                if (_jobs.get(jid) or {}).get("library_id") is not None:
                    _wait_for_transcodes(jid)
                try:
                    extractor.page(page * cells * interval, size, COLUMNS, ROWS, temp_page)
                    break
                except _YieldToPlayback:
                    _wait_for_transcodes(jid)
            if _cancelled(jid):
                raise _Cancelled()
            temp_page.replace(directory / name)
            manifest["pages"].append(name)
            manifest["state"] = "ready" if (page + 1) * cells >= count else "partial"
            _write_manifest(directory, manifest)
            _jobs.update(jid, frames=min((page + 1) * cells, count), frame_total=count)
    logger.info("preview generated kind=%s id=%s pages=%s wall_seconds=%.2f last_mode=%s decoder=%s",
                row.get("kind", "movie"), row["id"], len(manifest["pages"]) - completed_pages,
                time.monotonic() - started, extractor.last_mode,
                extractor.hw[1] if extractor.hw else "software")
    _remove_old_quality_cache(row, src, interval, manifest, count)
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
        active = _transcoding_busy()
        _jobs.update(jid, waiting=active)
        if not active:
            return
        _stop.wait(1)


def _worker(jid, items, batch):
    failures = []
    try:
        while True:
            # 领取与移出共用锁，绝不继续遍历启动时的影片快照。
            with _lock:
                if _cancelled(jid):
                    raise _Cancelled()
                job = _jobs.get(jid)
                live = job.get("items", items)
                index = int(job.get("done") or 0)
                if index >= len(live):
                    break
                kind, vid = live[index]
                _jobs.update(jid, waiting=False, current_kind=kind, current_id=vid,
                             frames=0, frame_total=0)
            # 库级任务在有在线转码时等待；单片由用户显式请求，可边播边低优先级生成。
            if batch:
                _wait_for_transcodes(jid)
            completed = False
            try:
                row, src = _version_source(vid, kind)
                _jobs.update(jid, current=Path(src.rel).name)
                info = _media_cached_or_probe(row, src)
                if not info.get("playable"):
                    raise RuntimeError("片源不可播放")
                _generate(jid, row, src, info, _interval(row))
                completed = True
            except _Cancelled:
                raise
            except Exception as e:
                message = str(e.detail) if isinstance(e, HTTPException) else str(e)
                logger.warning("preview generation failed kind=%s id=%s: %s", kind, vid, message)
                failures.append({"kind": kind, "id": vid, "error": message})
            with _lock:
                if completed:
                    try:
                        _retire_auto_items_locked({(kind, vid)})
                    except OSError as e:
                        logger.warning("save completed preview queue failed: %s", e)
                _jobs.update(jid, done=index + 1, current_kind=None, current_id=None,
                             failed=failures[-100:], failure_count=len(failures))
        with _lock:
            done = int((_jobs.get(jid) or {}).get("done") or 0)
            failed = bool(done and len(failures) == done)
            _jobs.update(jid, state="failed" if failed else "done",
                         error=failures[-1]["error"] if failed else "")
    except _Cancelled:
        _jobs.update(jid, state="cancelled")
    except Exception as e:
        logger.warning("preview worker failed: %s", e)
        _jobs.update(jid, state="failed", error="预览任务失败，请重试")
    finally:
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


def _retire_auto_items_locked(keys):
    """删除成功完成的影片；保存失败时保留可恢复的持久队列。"""
    before = list(_auto_queue)
    _auto_queue[:] = [item for item in before if (item["kind"], item["id"]) not in keys]
    if len(before) == len(_auto_queue):
        return
    try:
        _save_auto_queue()
    except OSError:
        _auto_queue[:] = before
        raise


def _reconcile_ready_queue():
    """对账旧版本或手动生成遗留的完成项；只读缓存/文件属性，不探测或生成媒体。"""
    global _ready_queue_check
    root = _root()
    now = time.monotonic()
    with _lock:
        if not _auto_queue or (_ready_queue_check[0] == root and now - _ready_queue_check[1] < 30):
            return
        _ready_queue_check = (root, now)
        snapshot = list(_auto_queue)
    owned = {}
    legacy = set()
    for path in root.glob("*/manifest.json"):
        if not _KEY_RE.fullmatch(path.parent.name):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("state") != "ready" or data.get("version") != VERSION:
            continue
        item_id = data.get("item_id")
        kind = data.get("item_kind", "movie")
        if type(item_id) is int and item_id > 0 and kind in ("movie", "episode", "extra"):
            key = (kind, item_id)
            owned.setdefault(key, set()).add(path.parent.name)
        elif item_id is None:
            legacy.add(path.parent.name)
    completed = set()
    for item in snapshot:
        identity = (item["kind"], item["id"])
        candidates = owned.get(identity, set()) | legacy
        if not candidates:
            continue
        try:
            row, src = _version_source(item["id"], item["kind"])
            key = _cache_key(row, src, _interval(row))
            manifest = _read_manifest(root / key) if key in candidates else None
            if manifest and manifest.get("state") == "ready":
                completed.add(identity)
        except (HTTPException, OSError):
            continue
    if completed:
        with _lock:
            try:
                _retire_auto_items_locked(completed)
            except OSError as e:
                logger.warning("reconcile completed preview queue failed: %s", e)


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
        job = _jobs.create(items=list(items), library_id=library_id, total=len(items), auto=auto)
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
    _reconcile_ready_queue()
    with _lock:
        job = _jobs.get(_auto_job_id)
        queued = len(_pending_queue()) + int(_running_queue_item(_jobs.get(_active_job)) is not None)
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


def _job_queue_item(job, kind, vid):
    library_id = job.get("library_id")
    if not library_id:
        queued = next((item for item in _auto_queue if (item["kind"], item["id"]) == (kind, vid)), None)
        row = {} if queued else (store.get_playable(kind, vid) or {})
        library_id = (queued or row).get("library_id") or 1
    return {"kind": kind, "id": vid, "library_id": library_id}


def _running_queue_item(job):
    if not job or job["state"] != "running":
        return None
    items = job.get("items") or []
    index = int(job.get("done") or 0)
    current = (job.get("current_kind"), job.get("current_id"))
    if index >= len(items) or tuple(items[index]) != current:
        return None
    return _job_queue_item(job, *current)


def _pending_queue():
    """Call with _lock held. Merge both schedules, excluding the claimed current item."""
    job = _jobs.get(_active_job)
    running = _running_queue_item(job)
    pending = []
    if job and job["state"] == "running":
        start = int(job.get("done") or 0) + int(running is not None)
        pending.extend(_job_queue_item(job, kind, vid) for kind, vid in job.get("items", [])[start:])
    seen = {(running["kind"], running["id"])} if running else set()
    result = []
    for item in pending + list(_auto_queue):
        key = (item["kind"], item["id"])
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _remove_pending_locked(library_id, targets):
    """先保存持久队列，再提交实时任务列表；两者与 worker 领取原子互斥。"""
    job = _jobs.get(_active_job)
    next_items = None
    if job and job["state"] == "running":
        items = job.get("items") or []
        start = int(job.get("done") or 0) + int(_running_queue_item(job) is not None)
        next_items = items[:start] + [(kind, vid) for kind, vid in items[start:]
                                     if not (_job_queue_item(job, kind, vid)["library_id"] == library_id
                                             and (kind, vid) in targets)]
    before = list(_auto_queue)
    _auto_queue[:] = [item for item in before if not (item["library_id"] == library_id and
                                                     (item["kind"], item["id"]) in targets)]
    if _auto_queue != before:
        try:
            _save_auto_queue()
        except OSError:
            _auto_queue[:] = before
            raise HTTPException(503, "保存预览队列失败，请稍后重试") from None
    if next_items is not None:
        _jobs.update(job["job_id"], items=next_items, total=len(next_items))


def _queue_revision(items: list[dict]) -> str:
    keys = [(item["kind"], item["id"], item["library_id"]) for item in items]
    return hashlib.sha256(json.dumps(keys, separators=(",", ":")).encode()).hexdigest()[:16]


@router.get("/previews/queue")
def preview_queue(library_id: int | None = Query(default=None, gt=0),
                  offset: int = Query(default=0, ge=0),
                  limit: int = Query(default=50, ge=1, le=100)):
    if library_id is not None and not store.get_library(library_id):
        raise HTTPException(404, "视频库不存在")
    _reconcile_ready_queue()
    with _lock:
        pending = _pending_queue()
        job = _jobs.get(_active_job)
        running = _running_queue_item(job)
        if running:
            index = int(job.get("done") or 0)
            running.update(job_id=job["job_id"], auto=bool(job.get("auto")),
                           waiting=bool(job.get("waiting")), frames=job.get("frames") or 0,
                           frame_total=job.get("frame_total") or 0,
                           done=index, total=job.get("total") or len(job.get("items") or []))
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


def _completed_manifest(data, interval, revision):
    """Validate a whole usable sprite set without probing or changing the cache."""
    if not data or data.get("state") != "ready":
        return False
    duration, count = data.get("duration"), data.get("count")
    if (type(duration) not in (int, float) or not 0 < duration <= 86400
            or not math.isfinite(duration) or type(count) is not int
            or type(interval) is not int or not 5 <= interval <= 60
            or type(data.get("quality_revision", 1)) is not int):
        return False
    expected = {"interval": interval, "width": WIDTH, "height": HEIGHT,
                "columns": COLUMNS, "rows": ROWS, "count": math.ceil(duration / interval)}
    return (all(data.get(name) == value for name, value in expected.items())
            and data.get("quality_revision", 1) == revision
            and data.get("pages") == [f"page_{page:04d}.jpg"
                                      for page in range(math.ceil(count / (COLUMNS * ROWS)))])


def _read_completed_manifest(directory):
    try:
        if directory.is_symlink() or (directory / "manifest.json").is_symlink():
            return None
        data = json.loads((directory / "manifest.json").read_text())
        if (not isinstance(data, dict) or data.get("version") != VERSION
                or data.get("quality_revision", 1) not in (1, QUALITY_REVISION)
                or not _completed_manifest(data, data.get("interval"), data.get("quality_revision", 1))):
            return None
        if any((directory / name).is_symlink() or not (directory / name).is_file()
               or (directory / name).stat().st_size == 0
               for name in data["pages"]):
            return None
    except (OSError, ValueError, TypeError):
        return None
    return data


@router.get("/previews/completed")
def preview_completed(library_id: int | None = Query(default=None, gt=0),
                      offset: int = Query(default=0, ge=0),
                      limit: int = Query(default=50, ge=1, le=100)):
    """On-demand inventory of current, complete previews, including usable old quality."""
    library = store.get_library(library_id) if library_id is not None else None
    if library_id is not None and not library:
        raise HTTPException(404, "视频库不存在")
    manifests, candidates = {}, set()
    ownerless = False
    root = _root()
    for directory in [] if root.is_symlink() else root.glob("*"):
        if not _KEY_RE.fullmatch(directory.name) or directory.is_symlink() or not directory.is_dir():
            continue
        data = _read_completed_manifest(directory)
        if not data:
            continue
        manifests[directory.name] = data
        kind, vid = data.get("item_kind"), data.get("item_id")
        if kind in ("movie", "episode", "extra") and type(vid) is int and 0 < vid < 2 ** 63:
            candidates.add((kind, vid))
        elif kind is None and vid is None:
            # Very early manifests lack ownership. Only these require a catalogue fallback.
            ownerless = True
    if ownerless:
        for lib in [library] if library else store.list_libraries():
            candidates.update(store.preview_items(lib["id"]))
    completed = []
    for kind, vid in candidates:
        row = store.get_playable(kind, vid)
        if not row or (library_id is not None and row.get("library_id") != library_id):
            continue
        try:
            backend = storage.backend_for(row.get("library_id") or 1)
            rel = backend.norm(row["file_path"])
            stat = backend.stat(rel)
            if stat.is_dir:
                continue
            # No read URL is needed: resolving one can start the remote HTTP proxy.
            src = storage.MediaSource(backend, rel, "", None, stat.size, stat.mtime)
        except storage.StorageError:
            continue
        interval = _interval(row)
        for revision in (QUALITY_REVISION, 1):
            data = manifests.get(_cache_key(row, src, interval, quality_revision=revision))
            if not _completed_manifest(data, interval, revision):
                continue
            if (data.get("item_kind", kind), data.get("item_id", vid)) != (kind, vid):
                continue
            file_name = Path(row.get("file_path") or "").name
            completed.append({"kind": kind, "id": vid, "library_id": row.get("library_id") or 1,
                              "title": row.get("display_title") or row.get("title") or file_name,
                              "file_name": file_name, "frames": data["count"], "interval": interval,
                              "quality_revision": revision, "needs_update": revision != QUALITY_REVISION})
            break
    order = {"movie": 0, "episode": 1, "extra": 2}
    completed.sort(key=lambda item: (item["library_id"], order[item["kind"]], item["id"]))
    return {"items": completed[offset:offset + limit], "total": len(completed),
            "offset": offset, "limit": limit}


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
        pending = [item for item in _pending_queue()
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
        _remove_pending_locked(body.library_id, targets)
    _auto_wake.set()
    return {"removed": len(targets)}


@router.delete("/previews/queue/{kind}/{version_id}")
def preview_queue_remove(kind: Literal["movie", "episode", "extra"], version_id: int,
                         library_id: int = Query(gt=0)):
    with _lock:
        target = (kind, version_id)
        pending = {(item["kind"], item["id"]) for item in _pending_queue()
                   if item["library_id"] == library_id}
        if target in pending:
            _remove_pending_locked(library_id, {target})
            _auto_wake.set()
            return {"removed": True, "cancelled": False}
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
    interval = _interval(row)
    key = _cache_key(row, src, interval)
    directory = _root() / key
    data = _read_manifest(directory)
    if not data or data.get("state") != "ready":
        # 重建完成前继续显示旧完整成品，不混用新旧页，也不在 GET 启动生成。
        legacy_key = _cache_key(row, src, interval, quality_revision=1)
        legacy_directory = _root() / legacy_key
        legacy_data = _read_manifest(legacy_directory)
        if legacy_data and legacy_data.get("state") == "ready":
            key, directory, data = legacy_key, legacy_directory, legacy_data
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
