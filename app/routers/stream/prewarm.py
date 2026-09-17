"""routers.stream.prewarm（自 app/routers/stream.py 拆分，评审 B9/R12-Q1；经 stream 门面使用）。"""
import os
import time
import json
import subprocess
import threading
from fastapi import HTTPException
from pydantic import BaseModel
from ... import caps as _caps
from ... import store
from ... import media as _media
from ... import playback as _playback
import uuid
from ...log import get_logger
logger = get_logger("stream.prewarm")
from .common import _write_session_meta, _sess_lock, _sessions, _hls_sem, _media_cached_or_probe, _version_abs, _session_dir, _session_key, _plan_marker, _write_master, _playlist_endlist, _write_complete_marker, _session_complete, _seg_count, _video_seg_prefix, _live_sessions_for, _find_live_session, _register_prewarm_session, _drop_session, _kill_proc, router
__all__ = ['_prewarm_jobs', '_prewarm_plan', '_prewarm_worker', 'PrewarmBody', 'prewarm_start', 'prewarm_status']

_prewarm_jobs: dict[str, dict] = {}


def _prewarm_plan(info: dict, quality: str, audio: int, caps: dict | None) -> dict:
    """预转码 plan（评审 B5a-7/R12-D2）：caps 缺省走服务端保守默认；
    前端带上与在线播相同的 caps 时，产物键（plan marker）与在线会话一致，
    预转码成品才能真正被点播命中，不再白转。"""
    caps_n = _caps.default_caps() if caps is None else _caps.normalize_caps(caps)
    return _playback.plan(info, caps=caps_n, quality=quality, audio_idx=audio)


def _prewarm_worker(job_id: str, vid: int, quality: str, audio: int,
                    caps: dict | None = None) -> None:
    """后台整片转完（夜间用）：与在线播同一 build_cmd/目录 scheme，完工即静态 VOD。
    进度=已产分片/预估总数；失败记 error 尾。
    与在线播共用会话目录：同 plan 的在线会话直接附着复用，存在不同 plan 的在线会话时
    拒绝启动（绝不清理/双写同一目录，评审 P1-07）。"""
    job = _prewarm_jobs.get(job_id)
    if not job:
        return
    if not _hls_sem.acquire(blocking=False):
        job.update({"status": "failed", "error": "transcode slots full, retry later"})
        return
    try:
        m, abs_p = _version_abs(vid)
        info = _media_cached_or_probe(m, abs_p)
        if not info.get("playable"):
            raise RuntimeError(f"unplayable: {info.get('probe_error') or 'probe failed'}")
        d = _prewarm_plan(info, quality, audio, caps)
        if d["method"] == "direct":
            raise RuntimeError("already direct-playable, no prewarm needed")
        try:
            dur = max(0.0, float(info.get("duration") or 0))
        except (TypeError, ValueError):
            dur = 0.0
        seg = d["plan"].get("seg") or "fmp4"
        stime = _playback.seg_time(seg)
        expected = max(1, int(dur / stime) + 1) if dur > 0 else 0
        job.update({"expected": expected,
                    "total_text": _media.fmt_duration(dur)})
        marker = _plan_marker(d["plan"], audio, 0)
        sdir = _session_dir(int(m["id"]), _session_key(d["plan"], audio), 0)
        job["sdir"] = sdir
        job["vprefix"] = _video_seg_prefix(seg)
        log_path = os.path.join(sdir, "ffmpeg.log")
        timeout = max(1800.0, dur * 4 + 600) if dur > 0 else 7200.0
        vprefix = str(job.get("vprefix") or "video_")
        # 静态成品已在（之前预转码/播完）：直接完成
        if _session_complete(sdir, marker):
            job.update({"status": "done", "segments": _seg_count(sdir, vprefix)})
            return
        # 在线会话避让/复用：同 plan 附着等待；不同 plan 拒绝启动
        live_same = _find_live_session(int(m["id"]), marker)
        if live_same is not None:
            job.update({"status": "running", "sid": live_same["sid"],
                        "sdir": live_same["sdir"], "attached": True,
                        "backend": "shared"})
            try:
                live_same["proc"].wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                raise RuntimeError("prewarm timeout (attached)")
            if _session_complete(sdir, marker):
                job.update({"status": "done", "segments": _seg_count(sdir, vprefix)})
                return
            raise RuntimeError("在线会话中断，未产出完整成品；请播放结束后重试预转码")
        if _live_sessions_for(int(m["id"])):
            raise RuntimeError("该版本正在播放（不同转码档），请播放结束后再预转码")
        # 硬件后端不出片 → 软件重试一次（与在线播同一兜底逻辑）
        use_hw = bool(_playback.hw_backend()) and not d["plan"].get("vcopy")
        force_sw = False
        sid_cur = ""
        ok = False
        try:
            for attempt in (0, 1):
                for n in os.listdir(sdir):
                    try:
                        os.remove(os.path.join(sdir, n))
                    except OSError:
                        pass
                if seg != "ts":
                    _write_master(sdir, info, d["plan"], stime)
                cmd = _playback.build_cmd(abs_p, d["plan"], start=0, seg_time=stime,
                                          force_sw=force_sw)
                try:
                    log_fh = open(log_path, "wb")
                except OSError:
                    log_fh = subprocess.DEVNULL  # type: ignore[assignment]
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log_fh,
                                        cwd=sdir)
                if log_fh is not subprocess.DEVNULL:
                    try:
                        log_fh.close()
                    except Exception:
                        pass
                backend = ("software" if force_sw or not use_hw
                           else (_playback.hw_backend() or "software"))
                sid_cur = _register_prewarm_session(
                    int(m["id"]), sdir, d["plan"], proc, marker, backend, attempt + 1)
                _write_session_meta(sdir, sid_cur, int(m["id"]), proc, backend, attempt + 1)
                job.update({"status": "running", "pid": proc.pid, "sid": sid_cur,
                            "backend": backend})
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _kill_proc(proc)
                    raise RuntimeError("prewarm timeout")
                if proc.returncode == 0 and _playlist_endlist(sdir):
                    break  # 成功
                _drop_session(sid_cur, kill=True)
                sid_cur = ""
                tail = ""
                try:
                    with open(log_path, "rb") as fh:
                        fh.seek(max(0, os.path.getsize(log_path) - 2000))
                        tail = fh.read().decode("utf-8", errors="replace").strip()[-500:]
                except OSError:
                    pass
                if attempt == 0 and use_hw:
                    force_sw = True
                    continue
                raise RuntimeError("transcode failed" + (f": {tail}" if tail else ""))
            mk = json.loads(marker)
            mk["sid"] = "prewarm:" + job_id
            marker_with_sid = json.dumps(mk, sort_keys=True)
            with open(os.path.join(sdir, "plan.json"), "w", encoding="utf-8") as fh:
                fh.write(marker_with_sid)
            # 自然转完（returncode 0 且 ENDLIST）：写完工标记，点播当静态 VOD 秒开
            _write_complete_marker(sdir, marker)
            with _sess_lock:
                s = _sessions.get(sid_cur)
                if s is not None:
                    s["complete"] = True
            ok = True
            job.update({"status": "done", "segments": _seg_count(sdir, vprefix)})
        finally:
            if not ok and sid_cur:
                _drop_session(sid_cur, kill=True)
    except Exception as e:
        logger.warning("prewarm failed job=%s: %s", job_id, e)
        job.update({"status": "failed", "error": str(e)[:500]})
    finally:
        try:
            sdir = str(job.get("sdir") or "")
            if sdir:
                job["segments"] = _seg_count(sdir, str(job.get("vprefix") or "video_"))
        except Exception:
            pass
        _hls_sem.release()


class PrewarmBody(BaseModel):
    version_id: int = 0
    quality: str = "auto"     # auto=按后端能力封顶（无 HW 720p / 有 HW 1080p）
    audio: int = 0
    # 前端实测 caps（可选；评审 B5a-7）：与在线播同 caps 时产物键一致，成品可被点播命中
    caps: dict | None = None


@router.post("/prewarm")
def prewarm_start(body: PrewarmBody | None = None):
    """夜间预转码：后台把整片转完（与在线播同一管线），完工后点播即静态秒播。
    只对 remux/transcode 生效；direct 直接 400（本来就零 CPU）。
    quality 支持 auto/1080p/720p/source；产物键与在线播一致（同 plan 即命中复用）。"""
    body = body or PrewarmBody()
    try:
        vid = int(body.version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    if not store.get_movie(vid):
        raise HTTPException(404, "version not found")
    quality = body.quality or "auto"
    job_id = uuid.uuid4().hex[:12]
    _prewarm_jobs[job_id] = {"job_id": job_id, "version_id": vid,
                             "quality": quality,
                             "audio": int(body.audio or 0),
                             "status": "queued", "segments": 0, "expected": 0,
                             "started_at": int(time.time())}
    th = threading.Thread(target=_prewarm_worker,
                          args=(job_id, vid, quality,
                                int(body.audio or 0), body.caps),
                          daemon=True)
    th.start()
    return {"job_id": job_id, "status": "queued"}


@router.get("/prewarm/{job_id}")
def prewarm_status(job_id: str):
    """预转码进度：{status queued/running/done/failed, segments/expected}。"""
    job = _prewarm_jobs.get(job_id or "")
    if not job:
        raise HTTPException(404, "no such prewarm job")
    if job.get("status") == "running" and job.get("sdir"):
        try:
            job["segments"] = _seg_count(str(job.get("sdir")),
                                         str(job.get("vprefix") or "video_"))
        except Exception:
            pass
    return dict(job)

