"""routers.stream.session（自 app/routers/stream.py 拆分，评审 B9/R12-Q1；经 stream 门面使用）。"""
import os
import time
import subprocess
import threading
import json
import math
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from ... import caps as _caps
from ... import playback as _playback
import uuid
from ...log import get_logger
logger = get_logger("stream.session")
from .common import (_min_segs, _SEG_RE, _SESS_FILE_RE, _has_endlist, _drop_session, _ffmpeg_ok, _get_session, _hls_sem, _hits,
                     _log_hit, _media_cached_or_probe, _media_start_for, _plan_marker,
                     _playlist_endlist, _playlist_text, _purge_old, _seg_count,
                     _sess_lock, _write_session_meta,
                     _sessions, _session_complete, _session_dir, _variant_playlists,
                     _version_source, _video_seg_prefix, _write_master, router, _tasks,
                     _artifact_key, _source_plan, _kill_proc, _watch_task)
from .common import _write_complete_marker, _release_task_slot, _clear_session_meta
from .media import _media_payload
from .subtitles import _sub_list
from .flow_control import (flow_status, record_playback_state, record_video_request,
                           start_producer_control, wake_producer_control)
from .flow_input import prepare_flow_input
from ... import media as _media
__all__ = ['SessionBody', 'SessionPingBody', '_spawn_session', 'hls_session_create', 'hls_session_playlist', '_wait_file', 'hls_session_segment', 'hls_session_debug', 'hls_session_ping', 'hls_session_close', 'hls_session_file']

class SessionBody(BaseModel):
    quality: str = "auto"
    audio: int = 0
    sub: int | None = None
    start: float = Field(default=0, ge=0, allow_inf_nan=False)
    caps: dict | None = None
    force_burn: bool = False
    kind: str = "movie"   # movie|episode|extra
    client: str = "web"
    client_id: str | None = Field(default=None, min_length=16, max_length=64,
                                  pattern=r"^[A-Za-z0-9_-]+$")
    request_seq: int | None = Field(default=None, ge=0)


class SessionPingBody(BaseModel):
    position: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    paused: bool | None = None
    playback_rate: float | None = Field(default=None, ge=0.25, le=4, allow_inf_nan=False)


# A browser player can replace its own in-flight seek before the POST returns a
# sid. Keep the latest request number long enough for an older POST to finish.
_client_requests: dict[str, tuple[int, float]] = {}
_CLIENT_REQUEST_TTL = 3600


def _replace_client_request(client_id: str, request_seq: int) -> None:
    now = time.time()
    with _sess_lock:
        previous = _client_requests.get(client_id)
        if previous is not None and request_seq <= previous[0]:
            raise HTTPException(409, "superseded playback request")
        for key, (_, seen) in list(_client_requests.items()):
            if now - seen > _CLIENT_REQUEST_TTL:
                _client_requests.pop(key, None)
        _client_requests[client_id] = (request_seq, now)
        stale = [sid for sid, sess in _sessions.items()
                 if sess.get("client_id") == client_id
                 and int(sess.get("request_seq") or 0) < request_seq]
    for sid in stale:
        _drop_session(sid)


def _mp4_duration(sdir: str) -> float:
    """已完成短片的真实时长；不依赖厂商浏览器的 duration/loadedmetadata。"""
    metadata = os.path.join(sdir, "media-info.json")
    try:
        with open(metadata, encoding="utf-8") as fh:
            duration = float(json.load(fh)["duration"])
        if math.isfinite(duration) and duration > 0:
            return duration
    except (OSError, ValueError, TypeError, KeyError):
        pass
    result = subprocess.run([_media.ffprobe_bin(), "-v", "error", "-show_entries",
        "format=duration", "-of", "json", os.path.join(sdir, "media.mp4")],
        capture_output=True, check=True, timeout=15)
    duration = float(json.loads(result.stdout)["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise HTTPException(500, "MP4 clip duration unavailable")
    temp = metadata + "." + uuid.uuid4().hex + ".tmp"
    try:
        with open(temp, "w", encoding="utf-8") as fh:
            json.dump({"duration": duration}, fh)
        os.replace(temp, metadata)
    finally:
        if os.path.exists(temp):
            os.remove(temp)
    return duration


def _spawn_session(version_id: int, quality: str, audio: int,
                   sub: int | None, start: float,
                   caps: dict | None = None,
                   force_burn: bool = False,
                   kind: str = "movie", client: str = "web",
                   prewarm: bool = False, client_id: str | None = None,
                   request_seq: int | None = None) -> tuple[str, str, dict]:
    """Create a client lease, atomically sharing an identical output producer.

    Every call receives its own sid. Only the final lease releases a running producer;
    prewarm owns a lease for its whole job. Reservations count against the process cap.
    """
    if (client_id is None) != (request_seq is None):
        raise HTTPException(422, "client_id and request_seq must be paired")
    m, src = _version_source(version_id, kind)
    k = m.get("kind") or kind
    info = _media_cached_or_probe(m, src)
    if not info.get("playable"):
        raise HTTPException(422, f"unplayable: {info.get('probe_error') or 'probe failed'}")
    caps_n = _caps.default_caps() if caps is None else _caps.normalize_caps(caps)
    d = _playback.plan(_media_payload(m, info), caps=caps_n, quality=quality,
                       audio_idx=audio, sub_idx=sub, force_burn=force_burn, client=client)
    if d["method"] == "blocked":
        raise HTTPException(422, "unplayable")
    if d["method"] == "direct":
        raise HTTPException(400, "use direct_url (Direct Play, no HLS needed)")
    if prewarm and d["plan"].get("seg") == "mp4":
        raise HTTPException(400, "MP4 compatibility clips cannot prewarm a whole movie")
    if (d.get("plan") or {}).get("sub") == "burn":
        subs = _sub_list(m, info)
        si = int(sub if sub is not None else -1)
        if not (0 <= si < len(subs)):
            raise HTTPException(422, "subtitle not found")
        track = subs[si] or {}
        if not int(track.get("image") or 0):
            raise HTTPException(422, "not an image subtitle")
        if track.get("source") == "sidecar":
            side = str(track.get("sidecar") or "")
            d["plan"]["sub_sidecar"] = side
            d["plan"]["sub_sidecar_input"] = src.backend.get_read_url(side)
        else:
            ff = track.get("ff_index")
            if ff is None:
                raise HTTPException(422, "subtitle stream index missing; re-probe required")
            d["plan"]["sub_ff_index"] = int(ff)
    if not _ffmpeg_ok():
        raise HTTPException(501, "ffmpeg not installed in server image")
    _purge_old()
    marker_plan = _source_plan(d["plan"], m, src)
    plan_key = _plan_marker(marker_plan, audio, start)
    full_key = _plan_marker(marker_plan, audio, 0)
    sid = uuid.uuid4().hex[:16]
    creator = False
    if client_id is not None:
        _replace_client_request(client_id, request_seq)
    with _sess_lock:
        # A newer request can arrive while the old producer is being stopped.
        if client_id is not None and _client_requests.get(client_id, (None,))[0] != request_seq:
            raise HTTPException(409, "superseded playback request")
        full_dir = _session_dir(int(m["id"]), _artifact_key(d["plan"], audio, full_key), 0, k)
        full_hit = (d["plan"].get("seg") != "mp4" and start > 0
                    and _session_complete(full_dir, full_key))
        output_start = 0 if full_hit else start
        if full_hit:
            sdir, plan_key = full_dir, full_key
        else:
            sdir = _session_dir(int(m["id"]), _artifact_key(d["plan"], audio, plan_key), start, k)
        key = (k, int(m["id"]), plan_key)
        task = _tasks.get(key)
        if task is not None and task.get("cancelled"):
            raise HTTPException(503, "transcode stopping, retry later")
        complete = _session_complete(sdir, plan_key)
        if task is None and not complete:
            if not _hls_sem.acquire(blocking=False):
                raise HTTPException(429, "transcode slots full, try later")
            task = {"key": key, "sdir": sdir, "vid": int(m["id"]), "kind": k,
                    "plan_key": plan_key, "proc": None, "owners": set(),
                    "ready": threading.Event(), "done": threading.Event(),
                    "slot": _hls_sem, "cancelled": False, "complete": False}
            _tasks[key] = task
            creator = True
        now = time.time()
        sess = {"proc": task.get("proc") if task else None, "sdir": sdir,
                "vid": int(m["id"]), "kind": k, "plan": d["plan"], "plan_key": plan_key,
                "caps_hash": _caps.caps_hash(caps_n), "backend": "static",
                "complete": complete, "last_ping": now, "created": now,
                "unclaimed": not prewarm, "prewarm": prewarm, "task": task,
                "requested_start": float(start),
                "client_id": client_id, "request_seq": request_seq}
        from ...viewing import current
        access = current.get()
        if access is not None:
            sess["viewing_owner"] = access.session_hash
            sess["viewing_generation"] = access.generation
        _sessions[sid] = sess
        if task is not None:
            task["owners"].add(sid)
            wake_producer_control(task)
    try:
        # Probe source time once per producer, before its first segment. Completed full
        # artifacts require no remote keyframe read for resume.
        if task is None:
            media_start = (0.0 if output_start == 0 else
                           _media_start_for(int(m["id"]), src.input, output_start, marker_plan, k))
        elif creator:
            task["media_start"] = (0.0 if output_start == 0 else
                                   _media_start_for(int(m["id"]), src.input, output_start, marker_plan, k))
            _start_task(task, sid, m, src, info, d["plan"], output_start)
            media_start = task["media_start"]
        else:
            if not task["ready"].wait(timeout=360):
                raise HTTPException(504, "transcode startup timed out")
            if task.get("error"):
                raise task["error"]
            if task.get("cancelled"):
                raise HTTPException(503, "transcode stopped, retry later")
            media_start = task["media_start"]
        with _sess_lock:
            if sid not in _sessions:
                raise HTTPException(503, "transcode session closed during startup")
            if task is not None:
                sess.update({"proc": task.get("proc"), "backend": task.get("backend"),
                             "attempt": task.get("attempt"), "complete": task.get("complete", False)})
            # Startup can take longer than the abandoned-client grace period.
            sess["created"] = sess["last_ping"] = time.time()
        d["media_start"] = media_start
        d["initial_time"] = max(0.0, float(start) - media_start)
        d["complete"] = bool(sess["complete"])
        d["duration"] = info.get("duration") or 0
        if d["plan"].get("seg") == "mp4":
            d["clip_duration"] = _mp4_duration(sdir)
        return sid, sdir, d
    except Exception as e:
        if creator:
            with _sess_lock:
                # A closed caller lease must not poison an already shared producer.
                if not task["ready"].is_set():
                    task["error"] = e if isinstance(e, HTTPException) else HTTPException(500, "transcode startup failed")
                    task["cancelled"] = True
                    # Waiters release their own leases; the final release stops
                    # the producer outside the lock and returns its slot after exit.
                    task["ready"].set()
        _drop_session(sid)
        if isinstance(e, HTTPException):
            raise
        logger.warning("transcode startup failed vid=%s: %s", version_id, e)
        raise HTTPException(500, "transcode startup failed") from e


def _start_task(task: dict, sid: str, row: dict, src, info: dict, plan: dict, start: float) -> None:
    """Initialize a reserved producer; followers await ready and cannot touch its files."""
    sdir = task["sdir"]
    seg = plan.get("seg") or "fmp4"
    stime = _playback.seg_time(seg)
    vprefix = _video_seg_prefix(seg)
    use_hw = bool(_playback.hw_backend()) and not plan.get("vcopy")
    log_path = os.path.join(sdir, "ffmpeg.log")
    for attempt in (0, 1):
        force_sw = attempt == 1
        cmd = _playback.build_cmd(src.input, plan, start=start, seg_time=stime, force_sw=force_sw)
        cmd, flow_supported = prepare_flow_input(cmd, src.input, plan)
        # Cancellation and creation share the lock, so shutdown can never miss Popen.
        with _sess_lock:
            if task.get("cancelled"):
                raise HTTPException(503, "transcode stopped")
            for name in os.listdir(sdir):
                try:
                    os.remove(os.path.join(sdir, name))
                except OSError as e:
                    logger.debug("cannot clear output %s/%s: %s", sdir, name, e)
            if seg == "fmp4":
                _write_master(sdir, info, plan, stime)
            task["flow_input_supported"] = flow_supported
            with open(log_path, "wb") as log_fh:
                try:
                    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log_fh, cwd=sdir)
                except FileNotFoundError as e:
                    raise HTTPException(501, "ffmpeg not installed in server image") from e
            backend = ("copy" if plan.get("vcopy") else
                       ("software" if force_sw or not use_hw else _playback.hw_backend()))
            task.update({"proc": proc, "backend": backend, "attempt": attempt + 1})
            for owner in task["owners"]:
                if owner in _sessions:
                    _sessions[owner].update({"proc": proc, "backend": backend, "attempt": attempt + 1})
            _write_session_meta(sdir, sid, int(row["id"]), proc, backend, attempt + 1)
        start_producer_control(task, proc, plan, float(info.get("duration") or 0))
        deadline = time.monotonic() + (45 if use_hw and not force_sw else 300)
        while time.monotonic() < deadline:
            if task.get("cancelled"):
                raise HTTPException(503, "transcode stopped")
            if seg != "mp4" and _seg_count(sdir, vprefix) >= _min_segs(plan) and _variant_playlists(sdir):
                break
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        if seg == "mp4" and proc.poll() == 0:
            temp = os.path.join(sdir, "media.tmp.mp4")
            if os.path.isfile(temp) and os.path.getsize(temp) > 0:
                with _sess_lock:
                    if task.get("cancelled"):
                        raise HTTPException(503, "transcode stopped")
                    os.replace(temp, os.path.join(sdir, "media.mp4"))
                    _write_complete_marker(sdir, task["plan_key"])
                    task["complete"] = True
                    for owner in task["owners"]:
                        if owner in _sessions:
                            _sessions[owner]["complete"] = True
                    _clear_session_meta(sdir)
                    _release_task_slot(task)
                    task["done"].set()
                    task["ready"].set()
                return
        if _seg_count(sdir, vprefix) > 0 and _variant_playlists(sdir):
            with _sess_lock:
                if task.get("cancelled"):
                    raise HTTPException(503, "transcode stopped")
                with open(os.path.join(sdir, "plan.json"), "w", encoding="utf-8") as fh:
                    fh.write(task["plan_key"])
                task["watched"] = True
                threading.Thread(target=_watch_task, args=(task, proc), daemon=True).start()
                task["ready"].set()
                wake_producer_control(task)
            return
        tail = ""
        try:
            with open(log_path, "rb") as fh:
                fh.seek(max(0, os.path.getsize(log_path) - 2000))
                tail = fh.read().decode("utf-8", errors="replace").strip()[-500:]
        except OSError as e:
            logger.debug("cannot read failed transcode log: %s", e)
        _kill_proc(proc)
        if proc.poll() is None:
            raise HTTPException(500, "transcode process could not stop")
        if attempt == 0 and use_hw:
            continue
        logger.warning("transcode failed vid=%s backend=%s tail=%s", row["id"], backend, tail)
        raise HTTPException(500, "transcode failed (no segments)")


@router.post("/{version_id}/sessions")
def hls_session_create(version_id: int, body: SessionBody | None = None):
    """开渐进式转码会话：后台 ffmpeg，前 3 分片就绪即回（首画面不等整片）。
    seek/换清晰度 = 关旧开新；fMP4 下切音轨只切 rendition（客户端完成，不重开会话）。
    caps 参与 plan（HEVC 直通/音频单转等四档），缺省保守默认。"""
    body = body or SessionBody()
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    sid, _sdir, d = _spawn_session(vid, body.quality, body.audio, body.sub, body.start,
                                   caps=body.caps, force_burn=body.force_burn,
                                   kind=body.kind, client=body.client,
                                   client_id=body.client_id, request_seq=body.request_seq)
    caps_n = _caps.default_caps() if body.caps is None else _caps.normalize_caps(body.caps)
    mp4 = d["plan"].get("seg") == "mp4"
    return {"session_id": sid,
            "playlist_url": "" if mp4 else f"/api/stream/sessions/{sid}/master.m3u8",
            "media_url": f"/api/stream/sessions/{sid}/media.mp4" if mp4 else "",
            "method": d["method"], "reasons": d["reasons"], "plan": d["plan"],
            "media_start": float(d.get("media_start") or 0),
            "initial_time": float(d.get("initial_time") or 0),
            "clip_duration": float(d.get("clip_duration") or 0),
            "complete": bool(d.get("complete")),
            "caps_hash": _caps.caps_hash(caps_n),
            "subtitle_mode": d.get("subtitle_mode") or "none"}


@router.get("/sessions/{sid}/master.m3u8")
def hls_session_playlist(sid: str):
    """会话主播放列表（增长型；转码完成前无 ENDLIST，hls.js 照播）。每次取即心跳。
    - fMP4：自产 master（EXT-X-MEDIA 多音轨）原样吐出；
    - TS 回滚：旧 media 列表，分片路径重写为 `seg/`（旧前端兼容）。
    永不缓存：hls.js 靠反复重取发现新分片，缓存即断流。"""
    sess = _get_session(sid)
    if (sess.get("plan") or {}).get("seg") == "ts":
        body = _playlist_text(sess["sdir"])
    else:
        path = os.path.join(sess["sdir"], "master.m3u8")
        try:
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
        except OSError:
            raise HTTPException(404, "playlist not ready (session may have ended)")
    _log_hit(sid, "playlist", "master.m3u8", 200)
    return PlainTextResponse(body, media_type="application/vnd.apple.mpegurl",
                             headers={"Cache-Control": "no-store"})


def _wait_file(sess: dict, dest: str, timeout: float = 25.0) -> bool:
    """等文件就绪（追渐进式转码进度）；会话已死则提前放弃。"""
    deadline = time.time() + timeout
    while not os.path.isfile(dest) and time.time() < deadline:
        proc = sess.get("proc")
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(0.5)
    return os.path.isfile(dest)


def _read_playlist_stable(path: str, attempts: int = 5) -> bytes:
    """读取会被 ffmpeg 持续改写的 m3u8：以 stat 前后一致确认内容快照。

    不能用 FileResponse：其 Content-Length 来自 stat，文件随后增长会让 ASGI 抛
    `RuntimeError: Response content longer than Content-Length`（2026-09 实测
    out_audio0.m3u8：音频 rendition 列表每出一片就重写一次）。"""
    data = b""
    for _ in range(max(1, attempts)):
        try:
            size1 = os.path.getsize(path)
            with open(path, "rb") as fh:
                data = fh.read()
            if len(data) == size1 and os.path.getsize(path) == size1:
                return data
        except OSError:
            return data
        time.sleep(0.05)
    return data


@router.get("/sessions/{sid}/seg/{name}")
def hls_session_segment(sid: str, name: str, request: Request = None):
    """会话分片：未就绪等最多 25s（追转码进度），会话死亡则 404。"""
    if not _SEG_RE.match(name or ""):
        raise HTTPException(422, "bad segment name")
    sess = _get_session(sid)
    # 文件名已白名单限定为扁平 segNNNNN.ts（无目录成分），此处再以 dirname 双保险；
    # 注意两侧都要 normpath：DATA_DIR 可能是相对路径（./data），单边归一会恒假。
    base = os.path.normpath(sess["sdir"])
    dest = os.path.normpath(os.path.join(base, os.path.basename(name or "")))
    if os.path.dirname(dest) != base:
        raise HTTPException(422, "bad segment name")
    record_video_request(sid, sess, name)
    deadline = time.time() + 25
    while not os.path.isfile(dest) and time.time() < deadline:
        proc = sess.get("proc")
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(0.5)
    if not os.path.isfile(dest):
        _log_hit(sid, "seg", name, 404)
        raise HTTPException(404, "segment not ready (session may have ended)")
    _log_hit(sid, "seg", name, 200)
    from ...viewing import current
    if current.get() is not None:
        from ..blob import media_response
        from ...storage import LocalStorageBackend
        return media_response(request or Request({"type": "http", "headers": []}),
                              LocalStorageBackend({"path": os.path.abspath(base)}), name,
                              media_type="video/MP2T")
    return FileResponse(dest, media_type="video/MP2T", filename=name)


@router.get("/sessions/{sid}/debug")
def hls_session_debug(sid: str):
    """卡死自证口：进程活/死/退出码、分片/列表/ENDLIST（含各 rendition）、
    实际转码后端/重试次数/caps 摘要、ffmpeg 尾日志、近期命中。"""
    with _sess_lock:
        sess = _sessions.get(sid or "")
        hits = [h for h in list(_hits) if h.get("sid") == sid][-20:]
    if not sess:
        return {"session_id": sid, "exists": False, "hits": hits}
    proc = sess.get("proc")
    alive = proc is not None and proc.poll() is None
    exit_code = None if proc is None or alive else proc.poll()
    sdir = sess.get("sdir") or ""
    plan = sess.get("plan") or {}
    segs = _seg_count(sdir)
    if plan.get("seg") == "mp4":
        segs = 1 if os.path.isfile(os.path.join(sdir, "media.mp4")) else 0
    vsegs = _seg_count(sdir, _video_seg_prefix(plan.get("seg") or "fmp4"))
    if plan.get("seg") == "mp4":
        vsegs = segs
    extinf = 0
    for name in _variant_playlists(sdir):
        try:
            with open(os.path.join(sdir, name), encoding="utf-8") as fh:
                for line in fh:
                    if line.strip().startswith("#EXTINF"):
                        extinf += 1
        except OSError:
            continue
    endlist = bool(sess.get("complete")) if plan.get("seg") == "mp4" else _playlist_endlist(sdir)
    variants = []
    for name in _variant_playlists(sdir):
        variants.append({"name": name,
                         "endlist": _has_endlist(os.path.join(sdir, name))})
    flog = ""
    try:
        lp = os.path.join(sdir, "ffmpeg.log")
        with open(lp, "rb") as fh:
            fh.seek(max(0, os.path.getsize(lp) - 2000))
            flog = fh.read().decode("utf-8", errors="replace")[-800:]
    except OSError:
        pass
    return {"session_id": sid, "exists": True, "running": alive,
            "complete": bool(sess.get("complete")),
            "exit_code": exit_code, "segments": segs, "video_segments": vsegs,
            "playlist_items": extinf, "finished": endlist,
            "backend": sess.get("backend") or "", "attempt": sess.get("attempt") or 1,
            "caps_hash": sess.get("caps_hash") or "", "plan": plan,
            "variants": variants,
            "last_ping_ago": round(time.time() - float(sess.get("last_ping") or 0), 1),
            "ffmpeg_log_tail": flog, "hits": hits, **flow_status(sess)}


@router.post("/sessions/{sid}/ping")
def hls_session_ping(sid: str, body: SessionPingBody | None = None):
    """心跳保活（播放器每 ~10s 调一次；10min 无心跳会话被回收）。"""
    sess = _get_session(sid)
    if body is not None:
        record_playback_state(sid, sess, position=body.position, paused=body.paused,
                              playback_rate=body.playback_rate)
    proc = sess.get("proc")
    exit_code = proc.poll() if proc is not None else None
    running = proc is not None and exit_code is None
    with _sess_lock:
        task = sess.get("task") or {}
        complete = bool(sess.get("complete") or task.get("complete"))
        failed = bool(not complete and (task.get("error") or (
            exit_code is not None and exit_code != 0)))
    return {"session_id": sid, "running": running,
            "complete": complete, "failed": failed,
            "error": "转码已中断，请重新建立播放会话" if failed else "",
            **flow_status(sess),
            "segments": (1 if os.path.isfile(os.path.join(sess["sdir"], "media.mp4")) else 0)
            if (sess.get("plan") or {}).get("seg") == "mp4" else _seg_count(sess["sdir"])}


@router.delete("/sessions/{sid}")
def hls_session_close(sid: str):
    """释放本客户端会话，最后持有者退出才停止转码；分片留缓存回收。
    预缓存自己的持有者仅由 worker 释放，客户端无权中断其后台任务。"""
    with _sess_lock:
        sess = _sessions.get(sid)
        shared = bool(sess and sess.get("prewarm") and not sess.get("complete"))
    from ...viewing import current
    access = current.get()
    if access is not None and sess and sess.get("viewing_owner") and sess["viewing_owner"] != access.session_hash:
        raise HTTPException(404, "播放会话不存在")
    if shared:
        return {"session_id": sid, "closed": False, "detached": True}
    with _sess_lock:
        existed = sid in _sessions
    _drop_session(sid, kill=True)
    return {"session_id": sid, "closed": existed}


@router.get("/sessions/{sid}/{name}")
def hls_session_file(sid: str, name: str, request: Request = None):
    """fMP4 扁平产物：变体列表 out_<name>.m3u8 / init <name>_init.mp4 / 分片 <name>_segNNNNN.m4s。
    TS 回滚时的 segNNNNN.ts 也走这里（旧 seg/ 路由保留兼容）。"""
    if not _SESS_FILE_RE.match(name or ""):
        raise HTTPException(422, "bad file name")
    sess = _get_session(sid)
    base = os.path.normpath(sess["sdir"])
    dest = os.path.normpath(os.path.join(base, os.path.basename(name or "")))
    if os.path.dirname(dest) != base:
        raise HTTPException(422, "bad file name")
    record_video_request(sid, sess, name)
    wait = 25.0 if name.endswith((".m4s", ".ts", ".mp4")) else 10.0
    if not _wait_file(sess, dest, timeout=wait):
        _log_hit(sid, "file", name, 404)
        raise HTTPException(404, "file not ready (session may have ended)")
    _log_hit(sid, "file", name, 200)
    if name.endswith(".m3u8"):
        # 变体列表随转码增长/重写：按内容快照返回（见 _read_playlist_stable）
        return Response(content=_read_playlist_stable(dest),
                        media_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-store"})
    if name.endswith(".mp4"):
        media = "video/mp4"
    else:
        media = "video/iso.segment"
    from ...viewing import current
    if current.get() is not None:
        from ..blob import media_response
        from ...storage import LocalStorageBackend
        return media_response(request or Request({"type": "http", "headers": []}),
                              LocalStorageBackend({"path": os.path.abspath(base)}), name,
                              media_type=media, inline=name == "media.mp4")
    return FileResponse(dest, media_type=media, filename=name,
                        content_disposition_type="inline" if name == "media.mp4" else "attachment",
                        headers={"Cache-Control": "no-store"})
