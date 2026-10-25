"""Independent, cancellable single-audio MP4 outputs for DLNA receivers.

Only completed, validated files are published. Video is always stream-copied;
subtitles are never burned. A cast owns its own lease, independently of a web
player, and the normal playback cache accounts for these directories.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from fastapi import HTTPException

from .. import data_usage, media

RULE_VERSION = 1
MAX_JOBS = 8
MAX_PREPARE_SECONDS = 6 * 3600


def _common():
    from ..routers.stream import common
    return common


def _private_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fingerprint(row: dict, source, info: dict, index: int) -> str:
    values = {"rule": RULE_VERSION, "kind": row.get("kind") or "movie", "id": int(row["id"]),
              "library_id": int(row.get("library_id") or 1), "path": row["file_path"],
              "size": int(source.size), "mtime": float(source.mtime), "audio": index,
              "vcodec": info.get("vcodec"), "duration": float(info.get("duration") or 0)}
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_selection(info: dict, index: int) -> None:
    tracks = info.get("audio") or []
    if type(index) is not int or not 0 <= index < len(tracks):
        raise HTTPException(422, "所选音轨不存在，请重新选择音轨")
    if (info.get("vcodec") not in ("h264", "hevc") or int(info.get("dv_profile") or 0)
            or info.get("hdr") or int(info.get("hdr10plus") or 0)):
        raise HTTPException(422, "此片源不适合仅处理音轨，请打开电视上的 jzmedia-tv 播放")


def build_command(source: str, index: int, destination: Path, vcodec: str) -> list[str]:
    try:
        threads = max(1, min(4, int(os.getenv("FFMPEG_THREADS", "2"))))
    except ValueError:
        threads = 2
    # Upper-case V excludes cover artwork. Audio indices are relative to the
    # audio streams, matching media_info.audio and the mobile player's choice.
    command = [media.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
               "-threads", str(threads), "-i", source, "-map", "0:V:0", "-map", f"0:a:{index}",
               "-map_metadata", "-1", "-map_chapters", "-1", "-c:v", "copy",
               "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-threads", str(threads),
               "-sn", "-dn", "-disposition:a:0", "default"]
    if vcodec == "hevc":
        command += ["-tag:v", "hvc1"]
    return command + ["-movflags", "+faststart", "-progress", "pipe:1", "-stats_period", "1",
                      "-f", "mp4", str(destination)]


def validate_output(path: Path, info: dict) -> dict:
    result = subprocess.run([media.ffprobe_bin(), "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,channels", "-of", "json", str(path)],
        capture_output=True, check=False, timeout=30)
    if result.returncode:
        raise RuntimeError("音轨文件校验失败，请使用电视 App 播放")
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    duration = float(data.get("format", {}).get("duration") or 0)
    expected = float(info.get("duration") or 0)
    if (len(streams) != 2 or len(videos) != 1 or len(audios) != 1
            or videos[0].get("codec_name") != info.get("vcodec")
            or audios[0].get("codec_name") != "aac" or not 0 < int(audios[0].get("channels") or 0) <= 2
            or not math.isfinite(duration) or duration <= 0
            or expected > 0 and abs(duration - expected) > max(3, expected * 0.001)):
        raise RuntimeError("音轨文件不完整，请使用电视 App 播放")
    return {"duration": duration, "bytes": path.stat().st_size}


@dataclass
class Job:
    key: str
    directory: Path
    source: str
    info: dict
    index: int
    owners: dict[str, Callable[[], bool]] = field(default_factory=dict)
    state: str = "queued"
    progress: float | None = None
    error: str = ""
    path: Path | None = None
    duration: float = 0
    size: int = 0
    cancelled: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    proc: object | None = None


class AudioOutputs:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs: dict[str, Job] = {}
        self.leases: dict[str, Job] = {}

    def protected_directories(self) -> set[str]:
        with self.lock:
            return {str(job.directory) for job in self.jobs.values() if job.owners}

    def _complete(self, directory: Path, key: str) -> dict | None:
        path = directory / "media.mp4"
        marker = directory / "cast-audio.json"
        try:
            if path.is_symlink() or marker.is_symlink():
                return None
            saved = json.loads(marker.read_text())
            stat = path.stat()
            if (isinstance(saved, dict) and type(saved.get("duration")) in (float, int)
                    and math.isfinite(saved["duration"]) and saved["duration"] > 0
                    and saved.get("key") == key and saved.get("rule") == RULE_VERSION
                    and saved.get("bytes") == stat.st_size > 0
                    and saved.get("mtime_ns") == stat.st_mtime_ns):
                return saved
        except (OSError, ValueError, TypeError):
            pass
        return None

    def prepare(self, row: dict, source, info: dict, index: int, valid: Callable[[], bool]) -> str:
        validate_selection(info, index)
        common = _common()
        key = fingerprint(row, source, info, index)
        common._purge_old()
        prefix = "e" if row.get("kind") == "episode" else "m"
        root = Path(common.TRANSCODE_DIR).resolve()
        parent = root / f"{prefix}{int(row['id'])}"
        directory = parent / ("cast-audio-" + key)
        if parent.is_symlink() or directory.is_symlink():
            raise HTTPException(500, "播放缓存目录无效")
        # The cache cleaner holds _sess_lock while it rechecks and removes a
        # directory. Reserve the cast lease under the same lock so attaching to
        # a completed file cannot race with that final eviction check.
        with common._sess_lock, self.lock:
            job = self.jobs.get(key)
            if job and job.cancelled.is_set():
                raise HTTPException(409, "上一音轨准备正在停止，请稍后重试")
            if job is None:
                if len(self.jobs) >= MAX_JOBS:
                    raise HTTPException(429, "音轨准备任务过多，请停止其他投屏后重试")
                directory.mkdir(parents=True, exist_ok=True)
                directory.chmod(0o700)
                job = Job(key, directory, source.input, dict(info), index)
                completed = self._complete(directory, key)
                if completed:
                    job.state, job.progress = "ready", 1.0
                    job.path, job.duration, job.size = directory / "media.mp4", completed["duration"], completed["bytes"]
                self.jobs[key] = job
            lease = secrets.token_urlsafe(24)
            job.owners[lease] = valid
            self.leases[lease] = job
            if job.state == "queued" and job.thread is None:
                job.thread = threading.Thread(target=self._run, args=(job,), name="cast-audio", daemon=True)
                job.thread.start()
        return lease

    def snapshot(self, lease: str) -> dict:
        with self.lock:
            job = self.leases.get(lease)
            if job is None:
                return {"state": "cancelled", "progress": None, "error": "音轨准备已取消"}
            return {"state": job.state, "progress": job.progress, "error": job.error,
                    "path": str(job.path) if job.path else None, "duration": job.duration, "size": job.size}

    def release(self, lease: str) -> None:
        with self.lock:
            job = self.leases.pop(lease, None)
            if job is None:
                return
            job.owners.pop(lease, None)
            if not job.owners:
                job.cancelled.set()
                if job.thread is None or not job.thread.is_alive():
                    self.jobs.pop(job.key, None)

    def _valid_owners(self, job: Job) -> bool:
        with self.lock:
            owners = list(job.owners.items())
        for lease, valid in owners:
            try:
                allowed = valid()
            except Exception:
                allowed = False
            if not allowed:
                self.release(lease)
        return not job.cancelled.is_set()

    def _read_progress(self, job: Job, proc) -> None:
        try:
            for line in proc.stdout:
                if line.startswith("out_time_us="):
                    try:
                        value = int(line.partition("=")[2]) / 1_000_000
                        duration = float(job.info.get("duration") or 0)
                        if duration > 0:
                            with self.lock:
                                job.progress = min(0.99, max(0, value / duration))
                    except ValueError:
                        pass
        except (OSError, ValueError):
            pass

    def _run(self, job: Job) -> None:
        common = _common()
        slot = common._hls_sem
        acquired = False
        proc = None
        partial = job.directory / "media.tmp.mp4"
        try:
            while not acquired:
                if not self._valid_owners(job):
                    return
                acquired = slot.acquire(timeout=1)
            if not self._valid_owners(job):
                return
            cap = common._cache_cap_bytes()
            free = shutil.disk_usage(job.directory).free
            if free < 64 * 1024 * 1024:
                raise RuntimeError("播放缓存空间不足，请清理缓存或使用电视 App")
            partial.unlink(missing_ok=True)
            with self.lock:
                job.state = "preparing"
            error_log = job.directory / "prepare.log"
            with error_log.open("wb") as errors:
                error_log.chmod(0o600)
                proc = subprocess.Popen(build_command(job.source, job.index, partial, str(job.info.get("vcodec"))),
                    cwd=job.directory, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
                    text=True)
                proc._jz_work_dir = str(job.directory)
                common._write_session_meta(str(job.directory), "cast-audio", 0, proc, "copy-video-aac", 1)
                with self.lock:
                    job.proc = proc
                reader = threading.Thread(target=self._read_progress, args=(job, proc), daemon=True)
                reader.start()
                deadline = time.monotonic() + MAX_PREPARE_SECONDS
                next_check = 0.0
                while proc.poll() is None:
                    now = time.monotonic()
                    if now >= deadline:
                        raise RuntimeError("音轨准备超时，请使用电视 App 播放")
                    if now >= next_check:
                        next_check = now + 1
                        if not self._valid_owners(job):
                            return
                        cap = common._cache_cap_bytes()
                        if partial.exists() and cap > 0 and partial.stat().st_size > cap:
                            raise RuntimeError("所选音轨文件超过播放缓存额度，请使用电视 App")
                        if shutil.disk_usage(job.directory).free < 32 * 1024 * 1024:
                            raise RuntimeError("播放缓存空间不足，请使用电视 App")
                    time.sleep(0.2)
                reader.join(timeout=2)
                if proc.returncode != 0:
                    raise RuntimeError("音轨准备失败，请使用电视 App 播放")
            if not self._valid_owners(job):
                return
            verified = validate_output(partial, job.info)
            if not self._valid_owners(job):
                return
            if cap > 0 and verified["bytes"] > cap:
                raise RuntimeError("所选音轨文件超过播放缓存额度，请使用电视 App")
            final = job.directory / "media.mp4"
            with self.lock:
                if job.cancelled.is_set():
                    return
                os.replace(partial, final)
                final.chmod(0o600)
                _private_json(job.directory / "cast-audio.json", {"key": job.key, "rule": RULE_VERSION,
                    **verified, "mtime_ns": final.stat().st_mtime_ns})
                job.path, job.duration, job.size = final, verified["duration"], verified["bytes"]
                job.state, job.progress = "ready", 1.0
                data_usage.invalidate()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            with self.lock:
                job.state = "error"
                # Never expose FFmpeg stderr, source URLs or credentials.
                job.error = "所选音轨准备失败、源文件变化或空间不足，请重新尝试或使用电视 App"
        finally:
            if proc is not None and proc.poll() is None:
                common._kill_proc(proc)
            if proc is not None and proc.stdout is not None:
                proc.stdout.close()
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass  # Cleanup must still release the shared processing slot.
            common._clear_session_meta(str(job.directory))
            if acquired:
                slot.release()
            with self.lock:
                job.proc = None
                if job.cancelled.is_set():
                    job.state = "cancelled"
                if not job.owners:
                    self.jobs.pop(job.key, None)

    def shutdown(self) -> None:
        with self.lock:
            jobs = list(self.jobs.values())
            for job in jobs:
                job.cancelled.set()
        common = _common()
        for job in jobs:
            if job.proc is not None:
                common._kill_proc(job.proc)
            if job.thread is not None:
                job.thread.join(timeout=2)
        with self.lock:
            self.jobs.clear()
            self.leases.clear()


outputs = AudioOutputs()
