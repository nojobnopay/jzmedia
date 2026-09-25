"""时间轴预览：限并发抽帧、分页拼图与独立持久缓存。只读媒体，写 DATA_DIR。

每帧输入侧 seek，避免为低分辨率预览全片解码；每页提交一次索引，取消/重启可续做。
所有生成通过 POST 显式发起，悬停 GET 不启动 ffmpeg。
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

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ... import media, store
from ...config import settings
from ...jobkit import JobRegistry
from ...log import get_logger
from .common import _media_cached_or_probe, _version_source, _sessions, _sess_lock, router

logger = get_logger("stream.previews")
__all__ = ['shutdown_previews']
VERSION = 1
WIDTH, HEIGHT, COLUMNS, ROWS = 160, 90, 5, 5
_jobs = JobRegistry(max_finished=12, prefix="preview")
_lock = threading.RLock()
_worker_thread = None
_active_job = None
_stop = threading.Event()
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
    return _stop.is_set() or not job or job["state"] != "running"


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


def _prune_cache(protect=""):
    try:
        cap = max(0.1, float(os.getenv("PREVIEW_CACHE_GB", "2"))) * 1024 ** 3
    except ValueError:
        cap = 2 * 1024 ** 3
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
        except OSError as e:
            logger.warning("preview cache eviction failed key=%s: %s", directory.name, e)


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


def _start(items, library_id=None):
    global _worker_thread, _active_job
    with _lock:
        if _worker_thread and _worker_thread.is_alive():
            job = _jobs.get(_active_job)
            if job and job["state"] == "running" and job.get("items") == items:
                return _public_job(job)
            raise HTTPException(409, "已有预览任务在运行，请完成或取消后重试")
        _stop.clear()
        job = _jobs.create(items=items, library_id=library_id, total=len(items))
        _active_job = job["job_id"]
        _worker_thread = threading.Thread(target=_worker, args=(_active_job, items, library_id is not None), daemon=True)
        _worker_thread.start()
        return _public_job(job)


def _public_job(job):
    return {k: v for k, v in job.items() if k != "items"}


def shutdown_previews():
    _stop.set()
    if _worker_thread:
        _worker_thread.join(timeout=5)


@router.get("/{version_id}/previews")
def preview_manifest(version_id: int, kind: Literal["movie", "episode", "extra"] = "movie"):
    row, src = _version_source(version_id, kind)
    key = _cache_key(row, src, _interval(row))
    directory = _root() / key
    data = _read_manifest(directory)
    result = _public_manifest(key, data)
    result["state"] = "ready" if data and data.get("state") == "ready" else "missing"
    job = _jobs.get(_active_job)
    if job and (kind, version_id) in job.get("items", []) and result["state"] != "ready":
        result.update(state=job["state"] if job["state"] != "done" else "missing",
                      job_id=job["job_id"], error=job.get("error", ""))
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


@router.get("/previews/cache/{key}/{name}")
def preview_image(key: str, name: str):
    if not _KEY_RE.fullmatch(key) or not _IMAGE_RE.fullmatch(name):
        raise HTTPException(404, "预览图片不存在")
    dest = _root() / key / name
    if not dest.is_file():
        raise HTTPException(404, "预览图片不存在")
    try:
        os.utime(dest.parent, None)
    except OSError as e:
        logger.debug("preview image touch failed key=%s: %s", key, e)
    return FileResponse(dest, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400, immutable"})
