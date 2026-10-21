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
import uuid

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StrictBool

from ... import data_usage, storage, store
from ...config import settings
from ...jobkit import JobRegistry
from ...log import get_logger
from .common import _media_cached_or_probe, _version_source, _sessions, _sess_lock, router
from .preview_extract import Extractor, _threads
from .preview_failures import FailureStore, failure_reason
from .preview_log import ExtractionError, error_kind, read_frame_log
from .preview_timeline import resolve_timeline
from .preview_color import color_plan, smoke_command

logger = get_logger("stream.previews")
__all__ = ['shutdown_previews', 'start_auto_previews']
VERSION = 1
QUALITY_REVISION = 3
EXTRACTION_REVISION = 2
# Web 160 CSS px at up to 3x; TV 240 dp at 2x. One shared sprite set.
WIDTH, HEIGHT, COLUMNS, ROWS = 480, 270, 5, 5
_jobs = JobRegistry(max_finished=12, prefix="preview")
_lock = threading.RLock()
_worker_thread = None
_active_job = None
_stop = threading.Event()
_auto_wake = threading.Event()
_auto_thread = None
_auto_queue = []
_auto_job_id = None
_queued_finished = None
_queue_retry_at = 0.0
_failure_store = None
_ready_queue_check = (None, 0.0)
_dv_runtime_ready = set()
_tail_reconcile_check = (None, 0.0)
_tail_reconcile_pending = set()
_TAIL_COMPLETION = "known_tail_missing"
_TAIL_WARNING = {"code": "confirmed_tail_damage",
                 "message": "已确认片源尾部损坏，末尾部分没有进度预览"}
_TAIL_FIELDS = ("completion", "available_count", "coverage_end", "warning")
_KEY_RE = re.compile(r"^[a-f0-9]{32}$")
_IMAGE_RE = re.compile(r"^page_\d{4}\.jpg$")
_BATCH_RE = re.compile(r"^[a-f0-9]{32}$")


def _root() -> Path:
    return Path(settings.data_dir) / "previews"


def _quality_dimensions(revision):
    # Deployed revisions must keep their original dimensions and immutable URLs.
    return (160, 90) if revision in (1, 2) else (WIDTH, HEIGHT)


def _cache_key(row, src, interval: int, *, quality_revision: int | None = None,
               extraction_revision: int | None = None) -> str:
    # 不对远程电影做全文件 hash；路径/库/大小/mtime 变化即失效。
    revision = QUALITY_REVISION if quality_revision is None else quality_revision
    width, height = _quality_dimensions(revision)
    identity = [VERSION, row.get("kind", "movie"), row["id"], src.backend.library_id,
                src.backend.root, src.rel, src.size, src.mtime, interval, width, height]
    # 第一代 key 必须保持原样；新版图片另用 URL，避免覆盖 immutable 旧图。
    if revision != 1:
        identity.append(revision)
    extraction = EXTRACTION_REVISION if extraction_revision is None else extraction_revision
    if revision >= 3 and extraction != 1:
        identity.extend(["extraction", extraction])
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:32]


def _quality_keys(row, src, interval, revision):
    keys = [_cache_key(row, src, interval, quality_revision=revision)]
    if revision >= 3:
        legacy = _cache_key(row, src, interval, quality_revision=revision, extraction_revision=1)
        if legacy not in keys:
            keys.append(legacy)
    return keys


def _current_ready(row, src, interval):
    for key in _quality_keys(row, src, interval, QUALITY_REVISION):
        data = _read_completed_manifest(_root() / key)
        if (data and _completed_manifest(data, interval, QUALITY_REVISION)
                and (data.get("completion") != _TAIL_COMPLETION or
                     (data.get("item_kind"), data.get("item_id"), data["tail_decision"]["library_id"])
                     == (row.get("kind", "movie"), row["id"], row.get("library_id") or 1))):
            return key, data
    return None, None


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
        if (any(name in data for name in _TAIL_FIELDS) or
                data.get("state") == "ready" and "tail_decision" in data) and not _completed_manifest(
                data, data.get("interval"), data.get("quality_revision", 1)):
            return None
        if data.get("completion") == _TAIL_COMPLETION:
            if (data["tail_decision"]["key"] != directory.name or directory.is_symlink()
                    or (directory / "manifest.json").is_symlink() or _root().is_symlink()
                    or any((directory / name).is_symlink() or (directory / name).stat().st_size == 0
                           for name in pages)):
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


def _run(cmd, jid, timeout=60, *, capture_output=False):
    """进程结束立即唤醒；每 200ms 检查取消/播放让行，终止后回收子进程。"""
    if _cancelled(jid):
        raise _Cancelled()
    prefix = [shutil.which("nice"), "-n", "10"] if shutil.which("nice") else []
    child_env = dict(os.environ, LP_NUM_THREADS=_threads())
    # 日志落临时文件，避免 PIPE 未读满导致子进程卡死；只向用户返回固定错误。
    with tempfile.TemporaryFile() as log, tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(prefix + cmd, stdin=subprocess.DEVNULL,
                                stdout=output if capture_output else subprocess.DEVNULL, stderr=log,
                                env=child_env)
        started = time.monotonic()
        deadline = started + timeout
        try:
            while True:
                if _cancelled(jid):
                    raise _Cancelled()
                if (_jobs.get(jid) or {}).get("library_id") is not None and _transcoding_busy():
                    raise _YieldToPlayback()
                if capture_output and os.fstat(output.fileno()).st_size > 8 * 1024 * 1024:
                    raise ExtractionError("无法核对视频预览时长", reason="probe_output_limit")
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
            if capture_output:
                output.seek(0)
                data = output.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024:
                    raise ExtractionError("无法核对视频预览时长", reason="probe_output_limit")
                return data
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
    return color_plan(info, WIDTH, HEIGHT)["filter"]


def _ensure_dv_runtime(jid):
    from ... import media
    ffmpeg = media.ffmpeg_bin()
    try:
        identity = (ffmpeg, Path(ffmpeg).stat().st_mtime_ns, _threads())
    except OSError:
        identity = (ffmpeg, None, _threads())
    if identity in _dv_runtime_ready:
        return
    try:
        _run(smoke_command(ffmpeg), jid, timeout=30)
    except (ExtractionError, FileNotFoundError):
        raise RuntimeError("Dolby Vision 预览转换环境不可用，请更新含软件 Vulkan 支持的服务镜像") from None
    _dv_runtime_ready.add(identity)


def _remove_old_quality_cache(row, src, interval, manifest, count):
    """新版完整落盘后，仅清理相同片源和采样配置的旧画质缓存。"""
    pages = [f"page_{page:04d}.jpg" for page in range(math.ceil(count / (COLUMNS * ROWS)))]
    if (manifest.get("state") != "ready" or manifest.get("quality_revision") != QUALITY_REVISION
            or manifest.get("count") != count or manifest.get("pages") != pages):
        return
    old_keys = [_cache_key(row, src, interval, quality_revision=revision)
                for revision in range(1, QUALITY_REVISION)]
    if manifest.get("extraction_revision") == EXTRACTION_REVISION:
        old_keys.append(_cache_key(row, src, interval, extraction_revision=1))
    for key in old_keys:
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
    _ready_key, ready = _current_ready(row, src, interval)
    if ready:
        # An explicit retry may succeed by reusing a whole older extraction.
        # Retire the current prefix's revoked decision before the worker clears
        # its failure, so restart reconciliation cannot resurrect that failure.
        current = _read_manifest(directory)
        decision = _tail_decision(current or {})
        if (decision and decision["state"] == "revoked" and decision["key"] == key
                and (current.get("item_kind"), current.get("item_id"))
                == (row.get("kind", "movie"), row["id"])):
            current.pop("tail_decision")
            _write_manifest(directory, current)
        _remove_old_quality_cache(row, src, interval, ready, ready["count"])
        return
    manifest = _read_manifest(directory)
    # A ready label may survive missing/empty pages or damaged metadata. Rebuild
    # that set rather than silently retiring a retry; keep older usable quality
    # until the replacement has completed.
    if manifest and manifest.get("state") == "ready":
        manifest = None
    plan = color_plan(info, WIDTH, HEIGHT)
    while True:
        if (_jobs.get(jid) or {}).get("library_id") is not None:
            _wait_for_transcodes(jid)
        try:
            if plan["software"]:
                _ensure_dv_runtime(jid)
            timeline = resolve_timeline(src, info,
                                        run_probe=lambda cmd, job, timeout: _run(
                                            cmd, job, timeout, capture_output=True), jid=jid)
            break
        except _YieldToPlayback:
            _wait_for_transcodes(jid)
    duration = timeline["duration"]
    count = max(1, math.ceil(duration / interval))
    directory.mkdir(parents=True, exist_ok=True)
    expected = dict(version=VERSION, interval=interval, duration=duration,
                                width=WIDTH, height=HEIGHT, columns=COLUMNS, rows=ROWS,
                                count=count, pages=[], state="partial")
    if manifest:
        if (any(manifest.get(name) != expected[name]
                for name in ("version", "interval", "width", "height", "columns", "rows"))
                or manifest.get("pages") != [f"page_{page:04d}.jpg"
                                             for page in range(len(manifest["pages"]))]):
            raise RuntimeError("预览采样信息已变化，无法继续旧任务")
        # A retry can confirm a shorter video tail after an earlier probe failed.
        # Keep unchanged sprite pixels; count hides extra cells in the last page.
        manifest.update(duration=duration, count=count)
        manifest["pages"] = manifest["pages"][:math.ceil(count / (COLUMNS * ROWS))]
        manifest["state"] = ("ready" if len(manifest["pages"]) * COLUMNS * ROWS >= count
                             else "partial")
        if manifest["state"] == "ready":
            manifest.pop("tail_decision", None)
    manifest = manifest or expected
    manifest.update(item_kind=row.get("kind", "movie"), item_id=int(row["id"]),
                    quality_revision=QUALITY_REVISION, extraction_revision=EXTRACTION_REVISION,
                    video_duration_source=timeline["source"],
                    video_stream_index=timeline["video_stream_index"])
    _write_manifest(directory, manifest)
    if manifest["state"] == "ready":
        _remove_old_quality_cache(row, src, interval, manifest, count)
        data_usage.invalidate()
        return
    cells = COLUMNS * ROWS
    lib = store.get_library(row.get("library_id") or 1) or {}
    remote = lib.get("source", "local") != "local" or src.input.startswith(("http://", "https://"))
    extractor = Extractor(src, plan["filter"], interval, remote=remote, run=_run, jid=jid,
                          video_stream_index=timeline["video_stream_index"],
                          input_args=plan["input_args"], software=plan["software"],
                          frame_guard=plan["frame_guard"],
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
            if manifest["state"] == "ready":
                manifest.pop("tail_decision", None)
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
    from ...cache_settings import preview_bytes
    return preview_bytes()


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
    global _queued_finished, _queue_retry_at
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
                code, message = failure_reason(e)
                logger.warning("preview generation failed kind=%s id=%s code=%s", kind, vid, code)
                identity = _job_queue_item(_jobs.get(jid) or {}, kind, vid)
                failure = {"kind": kind, "id": vid, "library_id": identity["library_id"],
                           "failed_at": int(time.time()), "error_code": code, "error": message}
                failures.append(failure)
            with _lock:
                job = _jobs.get(jid)
                if _stop.is_set() or not job or job["state"] != "running":
                    raise _Cancelled()
                ledger = _failure_ledger()
                ledger.update(kind, vid, None if completed else failure)
                saved = _flush_failures_locked()
                if completed:
                    try:
                        if saved:
                            _retire_auto_items_locked({(kind, vid)})
                    except OSError:
                        _queue_retry_at = time.monotonic() + 30
                        logger.warning("save completed preview queue failed")
                _jobs.update(jid, done=index + 1, current_kind=None, current_id=None,
                             failed=failures[-100:], failure_count=len(failures))
        with _lock:
            if (_jobs.get(jid) or {}).get("state") != "running":
                raise _Cancelled()
            done = int((_jobs.get(jid) or {}).get("done") or 0)
            failed = bool(done and len(failures) == done)
            _jobs.update(jid, state="failed" if failed else "done",
                         error=failures[-1]["error"] if failed else "")
    except _Cancelled:
        with _lock:
            _jobs.update(jid, state="cancelled")
    except Exception as e:
        code, message = failure_reason(e)
        logger.warning("preview worker failed code=%s", code)
        with _lock:
            job = _jobs.get(jid) or {}
            if _stop.is_set() or job.get("state") != "running":
                _jobs.update(jid, state="cancelled")
            else:
                item = job.get("queue_item")
                if item:
                    failure = {name: item[name] for name in ("kind", "id", "library_id")}
                    failure.update(failed_at=int(time.time()), error_code=code, error=message)
                    _failure_ledger().update(item["kind"], item["id"], failure)
                    _flush_failures_locked()
                _jobs.update(jid, state="failed", error="预览任务失败，请重试")
    finally:
        with _lock:
            job = _jobs.get(jid)
            if job and job.get("queue_item"):
                # Keep the terminal result even if the bounded job registry evicts it.
                _queued_finished = job
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


def _failure_ledger():
    global _failure_store
    root = _root()
    if _failure_store is None or _failure_store.root != root:
        _failure_store = FailureStore(root)
    return _failure_store


def _flush_failures_locked():
    try:
        return _failure_ledger().flush()
    except (OSError, ValueError, UnicodeError):
        logger.warning("save preview failures failed; retrying in 30 seconds")
        return False


def _failure_snapshot_locked():
    try:
        return _failure_ledger().snapshot()
    except (OSError, ValueError, UnicodeError):
        raise HTTPException(503, "读取缩略图失败记录失败，请稍后重试") from None


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
            for key in _quality_keys(row, src, _interval(row), QUALITY_REVISION):
                manifest = _read_completed_manifest(root / key) if key in candidates else None
                if manifest and _completed_manifest(manifest, _interval(row), QUALITY_REVISION):
                    completed.add(identity)
                    break
        except (HTTPException, OSError):
            continue
    if completed:
        with _lock:
            for kind, vid in completed:
                _failure_ledger().update(kind, vid, None)
            if not _flush_failures_locked():
                return
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


def _queue_immediate(item):
    return bool(item.get("manual_retry") or item.get("immediate"))


def _valid_saved_queue_item(item):
    """Keep legacy idle/retry entries; reject partial or inconsistent batch metadata."""
    if (not isinstance(item, dict) or item.get("kind") not in ("movie", "episode", "extra")
            or type(item.get("id")) is not int or item["id"] <= 0
            or type(item.get("library_id")) is not int or item["library_id"] <= 0
            or type(item.get("manual_retry", False)) is not bool
            or type(item.get("immediate", False)) is not bool):
        return False
    if item.get("source") == "batch" or "batch_id" in item or item.get("immediate"):
        return (item.get("source") == "batch" and item.get("immediate") is True
                and not item.get("manual_retry") and isinstance(item.get("batch_id"), str)
                and bool(_BATCH_RE.fullmatch(item["batch_id"])))
    return item.get("source") in (None, "auto", "retry") and (
        item.get("source") != "retry" or item.get("manual_retry") is True)


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


def _queued_job_item(job):
    if not job:
        return None
    if job.get("queue_item"):
        return job["queue_item"]
    # Compatibility for existing in-memory jobs and test fixtures.
    items = job.get("items") or []
    if not job.get("auto") or not items:
        return None
    return next((item for item in _auto_queue
                 if (item["kind"], item["id"]) == tuple(items[0])), None)


def _queued_item_matches(left, right):
    return bool(left and right and all(left.get(name) == right.get(name)
                                      for name in ("kind", "id", "library_id")))


def _auto_head_matches(job) -> bool:
    """Compatibility helper: dispatcher jobs can now own any queue position."""
    item = _queued_job_item(job)
    policy = ("manual_retry", "immediate", "source", "batch_id")
    return any(_queued_item_matches(item, queued)
               and all(item.get(name) == queued.get(name) for name in policy)
               for queued in _auto_queue)


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


def _dispatch_queue_once():
    """Finalize the previous item, then claim the first currently eligible item."""
    global _auto_job_id, _queued_finished, _queue_retry_at
    with _lock:
        _reconcile_tail_decisions_locked()
        if time.monotonic() < _queue_retry_at or not _flush_failures_locked():
            return
        if _auto_job_id:
            job = _jobs.get(_auto_job_id) or _queued_finished
            if job and job["state"] == "running":
                return
            if job and job["state"] in ("done", "failed"):
                item = _queued_job_item(job)
                if item and _auto_head_matches(job):
                    try:
                        _retire_auto_items_locked({(item["kind"], item["id"])})
                    except OSError:
                        _queue_retry_at = time.monotonic() + 30
                        logger.warning("save finished preview queue failed; retrying in 30 seconds")
                        return
            _auto_job_id = None
            _queued_finished = None
        # _start(auto=False) would preempt an automatic job. A queued retry must wait.
        if _worker_thread and _worker_thread.is_alive():
            return
        if not _auto_queue:
            return
        automatic_paused = None
        for candidate in list(_auto_queue):
            if _queue_immediate(candidate):
                item = dict(candidate)
                break
            if automatic_paused is None:
                automatic_paused = bool(_auto_pause_reason())
            if automatic_paused:
                continue
            if not _auto_library(store.get_library(candidate["library_id"])):
                try:
                    _retire_auto_items_locked({(candidate["kind"], candidate["id"])})
                except OSError:
                    _queue_retry_at = time.monotonic() + 30
                    logger.warning("save skipped preview queue failed; retrying in 30 seconds")
                    return
                continue
            item = dict(candidate)
            break
        else:
            return
        try:
            manual = _queue_immediate(item)
            job = _start([(item["kind"], item["id"])],
                         library_id=item["library_id"] if manual else None,
                         auto=not manual, queue_item=item)
            _auto_job_id = job["job_id"]
        except HTTPException as e:
            if e.status_code != 409:
                logger.warning("queued preview start failed status=%s", e.status_code)


def _auto_loop() -> None:
    while not _stop.is_set():
        _auto_wake.wait(30)
        _auto_wake.clear()
        if _stop.is_set():
            break
        _dispatch_queue_once()


def start_auto_previews() -> None:
    global _auto_thread, _auto_queue, _auto_job_id, _queued_finished
    with _lock:
        if _auto_thread and _auto_thread.is_alive():
            return
        _stop.clear()
        _auto_job_id = None
        _queued_finished = None
        try:
            saved = json.loads(_queue_path().read_text(encoding="utf-8"))
            if not isinstance(saved, list):
                raise ValueError("automatic preview queue must be a list")
            _auto_queue = [x for x in saved if _valid_saved_queue_item(x)]
        except FileNotFoundError:
            _auto_queue = []
        except (OSError, ValueError, TypeError) as e:
            logger.warning("read automatic preview queue failed: %s", e)
            _auto_queue = []
        _auto_thread = threading.Thread(target=_auto_loop, name="preview-auto", daemon=True)
        _auto_thread.start()
    _auto_wake.set()


def _start(items, library_id=None, auto=False, *, queue_item=None):
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
        source = (queue_item.get("source") if queue_item else None) or (
            "retry" if queue_item and queue_item.get("manual_retry") else "auto" if auto else "batch")
        job = _jobs.create(items=list(items), library_id=library_id, total=len(items), auto=auto,
                           source=source, queue_item=dict(queue_item) if queue_item else None)
        _active_job = job["job_id"]
        _worker_thread = threading.Thread(target=_worker, args=(_active_job, items, library_id is not None or auto), daemon=True)
        _worker_thread.start()
        return _public_job(job)


def _public_job(job):
    return {k: v for k, v in job.items() if k not in ("items", "queue_item")}


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
        running = bool(job and job.get("auto") and job["state"] == "running")
    start, end = _auto_hours()
    return {"mode": _auto_mode(), "queued": queued, "running": running,
            "start_hour": start, "end_hour": end, "pause_reason": _auto_pause_reason()}


def _queue_item_public(item: dict) -> dict:
    row = store.get_playable(item["kind"], item["id"]) or {}
    file_name = Path(row.get("file_path") or "").name
    result = {"kind": item["kind"], "id": item["id"],
            "library_id": item.get("library_id") or row.get("library_id"),
            "title": row.get("display_title") or row.get("title") or file_name or "已移除的影片",
            "file_name": file_name,
            "source": item.get("source") or ("retry" if item.get("manual_retry") else "auto")}
    if item.get("batch_id"):
        result["batch_id"] = item["batch_id"]
    return result


def _job_queue_item(job, kind, vid):
    library_id = job.get("library_id")
    if not library_id:
        queued = next((item for item in _auto_queue if (item["kind"], item["id"]) == (kind, vid)), None)
        row = {} if queued else (store.get_playable(kind, vid) or {})
        library_id = (queued or row).get("library_id") or 1
    result = {"kind": kind, "id": vid, "library_id": library_id,
              "source": job.get("source") or ("auto" if job.get("auto") else "batch")}
    batch_id = (job.get("queue_item") or {}).get("batch_id")
    if batch_id:
        result["batch_id"] = batch_id
    return result


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


def _queue_batches(pending, running):
    batches = {}
    for item, active in ([(running, True)] if running else []) + [(item, False) for item in pending]:
        batch_id = item.get("batch_id")
        if not batch_id:
            continue
        batch = batches.setdefault(batch_id, {"batch_id": batch_id, "pending": 0, "running": 0, "remaining": 0})
        batch["running" if active else "pending"] += 1
        batch["remaining"] += 1
    return list(batches.values())


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
            "running": running, "batches": _queue_batches(pending, running)}


def _tail_prefix_count(data, interval):
    """Only complete saved pages form the contiguous accepted prefix."""
    if not isinstance(data, dict) or type(interval) is not int or not 5 <= interval <= 60:
        return None
    duration, count, pages = data.get("duration"), data.get("count"), data.get("pages")
    if (type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 86400
            or type(count) is not int or count != math.ceil(duration / interval)
            or not isinstance(pages, list) or not pages):
        return None
    expected = dict(version=VERSION, interval=interval, width=WIDTH, height=HEIGHT,
                    columns=COLUMNS, rows=ROWS, quality_revision=QUALITY_REVISION,
                    extraction_revision=EXTRACTION_REVISION)
    if any(type(data.get(name)) is not int or data[name] != value for name, value in expected.items()):
        return None
    available = len(pages) * COLUMNS * ROWS
    coverage = available * interval
    if (pages != [f"page_{page:04d}.jpg" for page in range(len(pages))]
            or not 0 < available < count or coverage / duration < 0.95
            or not 0 < duration - coverage <= 300):
        return None
    return available


def _tail_decision(data):
    decision = data.get("tail_decision")
    if (not isinstance(decision, dict) or decision.get("state") not in ("accepted", "revoked")
            or any(type(decision.get(name)) is not int or decision[name] <= 0
                   for name in ("library_id", "failed_at", "confirmed_at", "available_count"))
            or not isinstance(decision.get("key"), str) or not _KEY_RE.fullmatch(decision["key"])):
        return None
    return decision


def _valid_tail_ready(data, interval):
    available = _tail_prefix_count(data, interval)
    decision = _tail_decision(data)
    return bool(available and decision and decision["state"] == "accepted"
                and decision["available_count"] == available
                and data.get("completion") == _TAIL_COMPLETION
                and type(data.get("available_count")) is int and data["available_count"] == available
                and type(data.get("coverage_end")) in (int, float)
                and data["coverage_end"] == available * interval
                and data.get("warning") == _TAIL_WARNING
                and data.get("item_kind") in ("movie", "episode", "extra")
                and type(data.get("item_id")) is int and data["item_id"] > 0)


def _tail_source(row):
    """File attributes only; never create a read URL or run a media probe."""
    backend = storage.backend_for(row.get("library_id") or 1)
    rel = backend.norm(row["file_path"])
    stat = backend.stat(rel)
    if stat.is_dir:
        raise storage.StorageNotFound("source is a directory")
    return storage.MediaSource(backend, rel, "", None, stat.size, stat.mtime)


def _tail_work_pending(kind, vid):
    target = (kind, vid)
    jobs = _jobs.active()
    if _worker_thread and _worker_thread.is_alive():
        worker = _jobs.get(_active_job)
        if not worker:
            return True  # An untracked writer cannot safely coexist with a decision.
        jobs.append(worker)  # Cancellation changes job state before its writer exits.
    return (any((item["kind"], item["id"]) == target for item in _auto_queue)
            or any(target in [tuple(item) for item in job.get("items", [])[int(job.get("done") or 0):]]
                   for job in jobs))


def _read_tail_prefix(directory, row, interval):
    if (directory.is_symlink() or (directory / "manifest.json").is_symlink()
            or _root().is_symlink()):
        return None
    data = _read_manifest(directory)
    if (not data or data.get("state") not in ("partial", "ready")
            or type(data.get("item_id")) is not int
            or (data.get("item_kind"), data.get("item_id")) != (row.get("kind", "movie"), row["id"])
            or _tail_prefix_count(data, interval) is None):
        return None
    if any((directory / name).is_symlink() or (directory / name).stat().st_size == 0
           for name in data["pages"]):
        return None
    return data


def _tail_candidate(row, record, key, data):
    interval = _interval(row)
    available = _tail_prefix_count(data, interval)
    if (record.get("error_code") != "decoder" or record.get("library_id") != (row.get("library_id") or 1)
            or not data or data.get("state") != "partial" or not available
            or _tail_work_pending(row.get("kind", "movie"), row["id"])):
        return None
    coverage = available * interval
    return dict(key=key, failed_at=record["failed_at"], available_count=available,
                coverage_end=coverage, duration=data["duration"],
                missing_seconds=round(data["duration"] - coverage, 6))


def _reconcile_tail_decisions_locked():
    """A committed manifest remains authoritative after a ledger write failure."""
    global _tail_reconcile_check
    root, now = _root(), time.monotonic()
    if _tail_reconcile_check[0] != root:
        _tail_reconcile_pending.clear()
        # Discover decisions once per cache root. Ordinary manifests never
        # cause page stats, source stats, or repeated background rescans.
        if not root.is_symlink():
            for path in root.glob("*/manifest.json"):
                if (not _KEY_RE.fullmatch(path.parent.name) or path.parent.is_symlink()
                        or path.is_symlink()):
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict) and _tail_decision(data):
                    _tail_reconcile_pending.add(path)
    elif not _tail_reconcile_pending or now - _tail_reconcile_check[1] < 30:
        return
    _tail_reconcile_check = (root, now)
    if root.is_symlink():
        return
    ledger = _failure_ledger()
    try:
        records = ledger.snapshot()
    except (OSError, ValueError, UnicodeError):
        return
    for path in tuple(_tail_reconcile_pending):
        try:
            data = _read_manifest(path.parent)
            decision = _tail_decision(data or {})
            if not decision:
                _tail_reconcile_pending.discard(path)
                continue
            kind, vid = data.get("item_kind"), data.get("item_id")
            if kind not in ("movie", "episode", "extra") or type(vid) is not int:
                _tail_reconcile_pending.discard(path)
                continue
            row = store.get_playable(kind, vid)
            if (not row or (row.get("library_id") or 1) != decision["library_id"]
                    or _cache_key(row, _tail_source(row), _interval(row)) != path.parent.name
                    or decision["key"] != path.parent.name):
                _tail_reconcile_pending.discard(path)
                continue
            checked = _read_tail_prefix(path.parent, row, _interval(row))
            if not checked:
                _tail_reconcile_pending.discard(path)
                continue
            previous = records.get((kind, vid))
            if data.get("state") == "ready" and _valid_tail_ready(data, _interval(row)):
                if previous:
                    ledger.update(kind, vid, None)
            elif data.get("state") == "partial" and decision["state"] == "revoked":
                if not previous or previous["failed_at"] < decision["failed_at"]:
                    ledger.update(kind, vid, dict(kind=kind, id=vid, library_id=decision["library_id"],
                        failed_at=decision["failed_at"], error_code="decoder"))
            _tail_reconcile_pending.discard(path)  # FailureStore now owns durable-write retries.
        except (OSError, storage.StorageError, ValueError, TypeError):
            continue


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
    width, height = _quality_dimensions(revision)
    expected = {"interval": interval, "width": width, "height": height,
                "columns": COLUMNS, "rows": ROWS, "count": math.ceil(duration / interval)}
    if any(name in data for name in _TAIL_FIELDS) or "tail_decision" in data:
        return (revision == QUALITY_REVISION and _valid_tail_ready(data, interval)
                and all(data.get(name) == value for name, value in expected.items()))
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
                or data.get("quality_revision", 1) not in range(1, QUALITY_REVISION + 1)
                or not _completed_manifest(data, data.get("interval"), data.get("quality_revision", 1))):
            return None
        if data.get("completion") == _TAIL_COMPLETION and data["tail_decision"]["key"] != directory.name:
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
        for revision in range(QUALITY_REVISION, 0, -1):
            matched = next(((key, candidate) for key in _quality_keys(row, src, interval, revision)
                            if _completed_manifest(candidate := manifests.get(key), interval, revision)), None)
            if not matched:
                continue
            key, data = matched
            if (data.get("item_kind", kind), data.get("item_id", vid)) != (kind, vid):
                continue
            if (data.get("completion") == _TAIL_COMPLETION
                    and data["tail_decision"]["library_id"] != (row.get("library_id") or 1)):
                continue
            file_name = Path(row.get("file_path") or "").name
            completed.append({"kind": kind, "id": vid, "library_id": row.get("library_id") or 1,
                              "title": row.get("display_title") or row.get("title") or file_name,
                              "file_name": file_name, "frames": data.get("available_count", data["count"]), "interval": interval,
                              "quality_revision": revision, "needs_update": revision != QUALITY_REVISION})
            if data.get("completion") == _TAIL_COMPLETION:
                completed[-1].update(key=key, completion=_TAIL_COMPLETION,
                    available_count=data["available_count"], coverage_end=data["coverage_end"],
                    duration=data["duration"], warning=dict(_TAIL_WARNING),
                    confirmed_at=data["tail_decision"]["confirmed_at"])
            break
    order = {"movie": 0, "episode": 1, "extra": 2}
    completed.sort(key=lambda item: (item["library_id"], order[item["kind"]], item["id"]))
    return {"items": completed[offset:offset + limit], "total": len(completed),
            "offset": offset, "limit": limit}


@router.get("/previews/failed")
def preview_failed(library_id: int | None = Query(default=None, gt=0),
                   offset: int = Query(default=0, ge=0),
                   limit: int = Query(default=50, ge=1, le=100)):
    if library_id is not None and not store.get_library(library_id):
        raise HTTPException(404, "视频库不存在")
    with _lock:
        records = list(_failure_snapshot_locked().values())
    failed = []
    for record in records:
        row = store.get_playable(record["kind"], record["id"])
        if not row:
            continue
        current_library = row.get("library_id") or 1
        if library_id is not None and current_library != library_id:
            continue
        tail_acceptance = None
        if record["error_code"] == "decoder":
            try:
                key = _cache_key(row, _tail_source(row), _interval(row))
                data = _read_tail_prefix(_root() / key, row, _interval(row))
                if data and data.get("state") == "ready" and _valid_tail_ready(data, _interval(row)):
                    continue  # A committed acceptance wins over a stale failure ledger.
                with _lock:
                    tail_acceptance = _tail_candidate(row, record, key, data)
            except (storage.StorageError, OSError):
                pass
        file_name = Path(row.get("file_path") or "").name
        failed.append(record | {"library_id": current_library,
                                "title": row.get("display_title") or row.get("title") or file_name,
                                "file_name": file_name})
        if tail_acceptance:
            failed[-1]["tail_acceptance"] = tail_acceptance
    order = {"movie": 0, "episode": 1, "extra": 2}
    failed.sort(key=lambda item: (-item["failed_at"], item["library_id"], order[item["kind"]], item["id"]))
    return {"items": failed[offset:offset + limit], "total": len(failed),
            "offset": offset, "limit": limit}


class PreviewTailAcceptBody(BaseModel):
    model_config = {"extra": "forbid"}
    library_id: int = Field(gt=0, strict=True)
    key: str = Field(pattern=r"^[a-f0-9]{32}$")
    failed_at: int = Field(gt=0, strict=True)
    available_count: int = Field(gt=0, strict=True)
    confirmed: StrictBool


class PreviewTailRevokeBody(BaseModel):
    model_config = {"extra": "forbid"}
    library_id: int = Field(gt=0, strict=True)
    key: str = Field(pattern=r"^[a-f0-9]{32}$")
    confirmed_at: int | None = Field(default=None, gt=0, strict=True)


def _tail_request_source(kind, version_id, library_id, key):
    row = store.get_playable(kind, version_id)
    if not store.get_library(library_id) or not row or (row.get("library_id") or 1) != library_id:
        raise HTTPException(404, "该影片不属于当前视频库")
    if _tail_work_pending(kind, version_id):
        raise HTTPException(409, "该影片正在生成或已排队，请先等待或移出队列")
    try:
        storage.clear_meta_cache(row.get("library_id") or 1)
        current_key = _cache_key(row, _tail_source(row), _interval(row))
    except storage.StorageNotFound:
        raise HTTPException(410, "片源文件不存在") from None
    except storage.StorageOffline:
        raise HTTPException(503, "片源存储离线，请恢复连接后重试") from None
    except (OSError, storage.StorageError):
        raise HTTPException(503, "无法核对片源，请稍后重试") from None
    if key != current_key:
        raise HTTPException(409, "片源或采样配置已变化，请刷新列表")
    directory = _root() / key
    try:
        data = _read_tail_prefix(directory, row, _interval(row))
    except OSError:
        raise HTTPException(409, "已保存的预览页已变化，请刷新列表") from None
    if not data:
        raise HTTPException(409, "已保存的完整预览页不满足片尾确认条件，请刷新列表")
    decision = _tail_decision(data)
    if data.get("completion") == _TAIL_COMPLETION and (
            not decision or decision["library_id"] != library_id or decision["key"] != key):
        raise HTTPException(409, "片尾确认所属片源已变化，请刷新列表")
    return row, directory, data


@router.post("/previews/failed/{kind}/{version_id}/accept-tail")
def preview_accept_tail(kind: Literal["movie", "episode", "extra"], version_id: int,
                        body: PreviewTailAcceptBody):
    if body.confirmed is not True:
        raise HTTPException(422, "请明确确认片源尾部损坏")
    with _lock:
        row, directory, data = _tail_request_source(kind, version_id, body.library_id, body.key)
        decision = _tail_decision(data)
        if data.get("state") == "ready":
            if (not _valid_tail_ready(data, _interval(row)) or not decision
                    or (decision["failed_at"], data["available_count"]) != (body.failed_at, body.available_count)):
                raise HTTPException(409, "确认记录已变化，请刷新列表")
        else:
            record = _failure_snapshot_locked().get((kind, version_id))
            candidate = _tail_candidate(row, record or {}, body.key, data)
            if (not candidate or candidate["failed_at"] != body.failed_at
                    or candidate["available_count"] != body.available_count):
                raise HTTPException(409, "失败记录或已保存页数已变化，请刷新列表")
            data = dict(data, state="ready", completion=_TAIL_COMPLETION,
                        available_count=body.available_count, coverage_end=candidate["coverage_end"],
                        warning=dict(_TAIL_WARNING), tail_decision=dict(state="accepted", key=body.key,
                            library_id=body.library_id, failed_at=body.failed_at,
                            confirmed_at=int(time.time()), available_count=body.available_count))
            try:
                _write_manifest(directory, data)
            except OSError:
                raise HTTPException(503, "保存片尾确认失败，请稍后重试") from None
        # The durable manifest is authoritative. A failed ledger write remains
        # pending and is recovered after restart; never report an untrue failure.
        _failure_ledger().update(kind, version_id, None)
        _flush_failures_locked()
    _auto_wake.set()
    data_usage.invalidate()
    return _public_manifest(body.key, data)


@router.delete("/previews/completed/{kind}/{version_id}/tail-acceptance")
def preview_revoke_tail(kind: Literal["movie", "episode", "extra"], version_id: int,
                        body: PreviewTailRevokeBody):
    with _lock:
        row, directory, data = _tail_request_source(kind, version_id, body.library_id, body.key)
        decision = _tail_decision(data)
        if (not decision or not _valid_tail_ready(data, _interval(row)) or data.get("state") != "ready"
                or decision["library_id"] != body.library_id or decision["key"] != body.key
                or body.confirmed_at is not None and body.confirmed_at != decision["confirmed_at"]):
            raise HTTPException(409, "片尾确认已变化，请刷新列表")
        data = {name: value for name, value in data.items() if name not in _TAIL_FIELDS}
        data.update(state="partial", tail_decision=dict(decision, state="revoked"))
        try:
            _write_manifest(directory, data)
        except OSError:
            raise HTTPException(503, "保存撤销失败，请稍后重试") from None
        _failure_ledger().update(kind, version_id, dict(kind=kind, id=version_id,
            library_id=body.library_id, failed_at=decision["failed_at"], error_code="decoder"))
        _flush_failures_locked()
    _auto_wake.set()
    data_usage.invalidate()
    return {"state": "partial"}


class PreviewRetryBody(BaseModel):
    library_id: int = Field(gt=0)


@router.post("/previews/failed/{kind}/{version_id}/retry")
def preview_retry(kind: Literal["movie", "episode", "extra"], version_id: int,
                  body: PreviewRetryBody):
    if not store.get_library(body.library_id):
        raise HTTPException(404, "视频库不存在")
    row = store.get_playable(kind, version_id)
    if not row or (row.get("library_id") or 1) != body.library_id:
        raise HTTPException(404, "该影片不属于当前视频库")
    target = (kind, version_id)
    with _lock:
        job = _jobs.get(_active_job)
        running = _running_queue_item(job)
        if running and (running["kind"], running["id"]) == target:
            return {"state": "running"}
        if job and job["state"] == "running":
            start = int(job.get("done") or 0) + int(running is not None)
            if target in [tuple(item) for item in job.get("items", [])[start:]]:
                return {"state": "already_queued"}
        queued = next((item for item in _auto_queue
                       if (item["kind"], item["id"]) == target), None)
        if queued and _queue_immediate(queued):
            return {"state": "already_queued"}
        if target not in _failure_snapshot_locked():
            raise HTTPException(409, "该影片已没有待处理的生成失败，请刷新列表")
        dispatched = _jobs.get(_auto_job_id) or _queued_finished
        if (queued and dispatched and dispatched["state"] != "running"
                and _queued_item_matches(_queued_job_item(dispatched), queued)):
            raise HTTPException(503, "正在保存上次生成结果，请稍后重试")
        before = list(_auto_queue)
        _auto_queue[:] = [item for item in before if (item["kind"], item["id"]) != target]
        _auto_queue.append({"kind": kind, "id": version_id, "library_id": body.library_id,
                            "manual_retry": True})
        try:
            _save_auto_queue()
        except OSError:
            _auto_queue[:] = before
            raise HTTPException(503, "保存预览队列失败，请稍后重试") from None
    _auto_wake.set()
    return {"state": "queued"}


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
        if _current_ready(row, src, _interval(row))[1]:
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
            # Player-triggered single-item jobs remain in memory for compatibility.
            job = _jobs.get(_active_job)
            current = _running_queue_item(job)
            if (current and (current["kind"], current["id"], current["library_id"]) ==
                    (kind, version_id, library_id) and len(job.get("items") or []) == 1):
                _jobs.cancel(job["job_id"])
                return {"removed": True, "cancelled": True}
            raise HTTPException(404, "该影片已不在待处理队列中")
        job = _jobs.get(_auto_job_id)
        running = bool(job and job["state"] == "running"
                       and _queued_item_matches(_queued_job_item(job), _auto_queue[index]))
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
    if data and data.get("completion") == _TAIL_COMPLETION and (
            (data.get("item_kind"), data.get("item_id")) != (kind, version_id)
            or data["tail_decision"]["library_id"] != (row.get("library_id") or 1)):
        data = None
    if not data or data.get("state") != "ready":
        # 重建完成前继续显示旧完整成品，不混用新旧页，也不在 GET 启动生成。
        legacy_candidates = [(revision, candidate)
                             for revision in range(QUALITY_REVISION, 0, -1)
                             for candidate in _quality_keys(row, src, interval, revision)
                             if candidate != key]
        for revision, legacy_key in legacy_candidates:
            legacy_directory = _root() / legacy_key
            legacy_data = _read_manifest(legacy_directory)
            if _completed_manifest(legacy_data, interval, revision):
                key, directory, data = legacy_key, legacy_directory, legacy_data
                break
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
    try:
        _version_source(version_id, kind)  # 后续 worker 再检查一次。
    except HTTPException as e:
        row = store.get_playable(kind, version_id)
        if row:
            code, message = failure_reason(e)
            failure = {"kind": kind, "id": version_id, "library_id": row.get("library_id") or 1,
                       "failed_at": int(time.time()), "error_code": code, "error": message}
            with _lock:
                _failure_ledger().update(kind, version_id, failure)
                _flush_failures_locked()
            raise HTTPException(e.status_code, message) from None
        raise
    return _start([(kind, version_id)])


class PreviewBatchBody(BaseModel):
    library_id: int = Field(gt=0)


@router.post("/previews/jobs")
def preview_batch(body: PreviewBatchBody):
    if not store.get_library(body.library_id):
        raise HTTPException(404, "视频库不存在")
    items = store.preview_items(body.library_id)
    candidates = []
    ready = unavailable = 0
    for kind, vid in items:
        try:
            row, src = _version_source(vid, kind)
        except HTTPException:
            unavailable += 1
            continue
        interval = _interval(row)
        if _current_ready(row, src, interval)[1]:
            ready += 1
        else:
            candidates.append((kind, vid, row, src, interval))
    batch_id = uuid.uuid4().hex
    added = promoted = already_queued = 0
    with _lock:
        job = _jobs.get(_active_job)
        active = set()
        if job and job["state"] == "running":
            active.update(tuple(item) for item in job.get("items", [])[int(job.get("done") or 0):])
        before = list(_auto_queue)
        for kind, vid, row, src, interval in candidates:
            identity = (kind, vid)
            entry_index = next((i for i, entry in enumerate(_auto_queue)
                                if (entry["kind"], entry["id"]) == identity), None)
            if identity in active or (entry_index is not None and _queue_immediate(_auto_queue[entry_index])):
                already_queued += 1
                continue
            # Generation can complete while this request gathers the library list.
            if _current_ready(row, src, interval)[1]:
                ready += 1
                continue
            entry = {"kind": kind, "id": vid, "library_id": body.library_id,
                     "immediate": True, "source": "batch", "batch_id": batch_id}
            if entry_index is None:
                _auto_queue.append(entry)
                added += 1
            else:
                _auto_queue[entry_index] = entry
                promoted += 1
        try:
            if _auto_queue != before:
                _save_auto_queue()
        except OSError:
            _auto_queue[:] = before
            raise HTTPException(503, "保存预览队列失败，请稍后重试") from None
    _auto_wake.set()
    return {"state": "queued", "batch_id": batch_id if added + promoted else None,
            "total": len(items), "added": added, "promoted": promoted, "ready": ready,
            "already_queued": already_queued, "unavailable": unavailable}


@router.post("/previews/batches/{batch_id}/cancel")
def preview_batch_cancel(batch_id: str, body: PreviewBatchBody):
    if not _BATCH_RE.fullmatch(batch_id):
        raise HTTPException(422, "批次标识无效")
    if not store.get_library(body.library_id):
        raise HTTPException(404, "视频库不存在")
    with _lock:
        before = list(_auto_queue)
        removed = [item for item in before if item.get("batch_id") == batch_id
                   and item["library_id"] == body.library_id]
        if not removed:
            return {"removed": 0, "cancelled": False}
        _auto_queue[:] = [item for item in before if item not in removed]
        try:
            _save_auto_queue()
        except OSError:
            _auto_queue[:] = before
            raise HTTPException(503, "保存预览队列失败，请稍后重试") from None
        job = _jobs.get(_auto_job_id)
        current = _queued_job_item(job)
        cancelled = bool(job and job["state"] == "running" and current
                         and current.get("batch_id") == batch_id and current["library_id"] == body.library_id)
        if cancelled:
            _jobs.cancel(job["job_id"])
    _auto_wake.set()
    return {"removed": len(removed), "cancelled": cancelled}


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
    with _lock:
        job = _jobs.get(job_id)
        item = _queued_job_item(job)
        if (job and job["state"] == "running" and job_id == _auto_job_id
                and item):
            try:
                _retire_auto_items_locked({(item["kind"], item["id"])})
            except OSError:
                raise HTTPException(503, "保存预览队列失败，请稍后重试") from None
        _jobs.cancel(job_id)
    _auto_wake.set()
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
