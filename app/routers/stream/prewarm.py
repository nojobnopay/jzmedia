"""routers.stream.prewarm（自 app/routers/stream.py 拆分，评审 B9/R12-Q1；经 stream 门面使用）。"""
import time
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
from .common import (_sess_lock, _sessions, _session_complete, _seg_count,
                     _video_seg_prefix, _drop_session, router)
__all__ = ['_prewarm_jobs', '_prewarm_plan', '_prewarm_worker', 'PrewarmBody', 'prewarm_start', 'prewarm_status']

# 预转码任务（评审 R12-B2）：加锁保护并发读写，完成后只留最近 N 条防内存无界
_prewarm_jobs: dict[str, dict] = {}
_prewarm_lock = threading.RLock()
_MAX_FINISHED_PREWARM = 5


def _trim_prewarm_jobs() -> None:
    """调用方需持有 _prewarm_lock：按开始时间保留运行中 + 最近 N 条完成态。"""
    finished = [(k, j) for k, j in _prewarm_jobs.items()
                if j.get("status") not in ("queued", "running")]
    if len(finished) <= _MAX_FINISHED_PREWARM:
        return
    finished.sort(key=lambda kv: kv[1].get("started_at") or 0)
    for k, _ in finished[:len(finished) - _MAX_FINISHED_PREWARM]:
        _prewarm_jobs.pop(k, None)


def _prewarm_plan(info: dict, quality: str, audio: int, caps: dict | None, client: str = "web") -> dict:
    """预转码 plan（评审 B5a-7/R12-D2）：caps 缺省走服务端保守默认；
    前端带上与在线播相同的 caps 时，产物键（plan marker）与在线会话一致，
    预转码成品才能真正被点播命中，不再白转。"""
    caps_n = _caps.default_caps() if caps is None else _caps.normalize_caps(caps)
    return _playback.plan(info, caps=caps_n, quality=quality, audio_idx=audio, client=client)


def _prewarm_worker(job_id: str, vid: int, quality: str, audio: int,
                    caps: dict | None = None, kind: str = "movie", client: str = "web") -> None:
    """A prewarm job owns a lease on the same producer as ordinary playback.

    Closing a viewer cannot cancel this lease; completing or failing this job releases
    only its own lease. Different outputs coexist within the shared process limit.
    """
    from .session import _spawn_session
    job = _prewarm_jobs.get(job_id)
    if not job:
        return
    sid = None
    try:
        job["status"] = "running"
        sid, sdir, d = _spawn_session(vid, quality, audio, None, 0, caps=caps,
                                     kind=kind, client=client, prewarm=True)
        dur = max(0.0, float(d.get("duration") or 0))
        seg = d["plan"].get("seg") or "fmp4"
        stime = _playback.seg_time(seg)
        vprefix = _video_seg_prefix(seg)
        with _sess_lock:
            sess = _sessions[sid]
            task = sess.get("task")
        job.update({"sid": sid, "sdir": sdir, "vprefix": vprefix,
                    "backend": sess.get("backend"),
                    "attached": bool(task and len(task["owners"]) > 1),
                    "expected": max(1, int(dur / stime) + 1) if dur > 0 else 0,
                    "total_text": _media.fmt_duration(dur)})
        timeout = max(1800.0, dur * 4 + 600) if dur > 0 else 7200.0
        if task is not None:
            if not task["done"].wait(timeout):
                raise RuntimeError("prewarm timeout")
            if task.get("error"):
                raise task["error"]
        if not _session_complete(sdir, sess["plan_key"]):
            raise RuntimeError("transcode interrupted before completion")
        job.update({"status": "done", "segments": _seg_count(sdir, vprefix)})
    except Exception as e:
        logger.warning("prewarm failed job=%s: %s", job_id, e)
        job.update({"status": "failed", "error": str(e)[:500]})
    finally:
        if sid:
            _drop_session(sid)
        if job.get("sdir"):
            job["segments"] = _seg_count(job["sdir"], job.get("vprefix") or "video_")


class PrewarmBody(BaseModel):
    version_id: int = 0
    kind: str = "movie"       # movie|episode（T2：剧集也可预缓存）
    quality: str = "auto"     # auto=按后端能力封顶（无 HW 720p / 有 HW 1080p）
    audio: int = 0
    # 前端实测 caps（可选；评审 B5a-7）：与在线播同 caps 时产物键一致，成品可被点播命中
    caps: dict | None = None
    client: str = "web"


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
    kind = body.kind or "movie"
    if kind not in ("movie", "episode", "extra"):
        raise HTTPException(422, "bad kind")
    if not store.get_playable(kind, vid):
        raise HTTPException(404, "version not found")
    quality = body.quality or "auto"
    job_id = uuid.uuid4().hex[:12]
    with _prewarm_lock:
        _trim_prewarm_jobs()
        _prewarm_jobs[job_id] = {"job_id": job_id, "version_id": vid, "kind": kind,
                                 "quality": quality,
                                 "audio": int(body.audio or 0),
                                 "status": "queued", "segments": 0, "expected": 0,
                                 "started_at": int(time.time())}
    th = threading.Thread(target=_prewarm_worker,
                          args=(job_id, vid, quality,
                                int(body.audio or 0), body.caps, kind, body.client),
                          daemon=True)
    th.start()
    return {"job_id": job_id, "status": "queued"}


@router.get("/prewarm/{job_id}")
def prewarm_status(job_id: str):
    """预转码进度：{status queued/running/done/failed, segments/expected}。"""
    with _prewarm_lock:
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

