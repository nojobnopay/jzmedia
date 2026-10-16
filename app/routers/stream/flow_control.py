"""Bound ordinary HLS production using media time, while sharing client leases."""
import math
import os
from pathlib import Path
import re
import signal
import threading
import time

from ...log import get_logger
from . import common

logger = get_logger("stream.flow")
_VIDEO_NAME = re.compile(r"^(?:video_seg\d+\.m4s|seg\d+\.ts)$")
_POLL_SECONDS = 0.1
_EXTRAPOLATE_SECONDS = 15.0


def lookahead_seconds() -> float:
    try:
        value = float(os.getenv("TRANSCODE_LOOKAHEAD_SECONDS", "120"))
    except (TypeError, ValueError):
        value = 120.0
    if not math.isfinite(value) or value < 0:
        value = 120.0
    return max(10.0, min(600.0, value)) if value else 0.0


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class _Playlist:
    """Cache a consistent playlist snapshot; never estimate duration from indices."""
    def __init__(self, path):
        self.path = Path(path)
        self.signature = None
        self.value = None
        self.lock = threading.Lock()

    @staticmethod
    def _signature(info):
        return info.st_ino, info.st_size, info.st_mtime_ns

    def read(self):
        with self.lock:
            try:
                signature = self._signature(self.path.stat())
                if signature == self.signature:
                    return self.value
                text = self.path.read_text(encoding="utf-8")
                if self._signature(self.path.stat()) != signature:
                    return self.value  # FFmpeg is still publishing this snapshot.
                duration, pending, segments = 0.0, None, {}
                for raw in text.splitlines():
                    line = raw.strip()
                    if line.startswith("#EXTINF:"):
                        pending = _number(line.partition(":")[2].partition(",")[0])
                        if pending is None or pending <= 0:
                            return self.value
                    elif line and not line.startswith("#"):
                        if pending is None or not _VIDEO_NAME.fullmatch(line) or line in segments:
                            return self.value
                        if not math.isfinite(duration + pending):
                            return self.value
                        segments[line] = (duration, duration + pending)
                        duration += pending
                        pending = None
                if pending is not None or not segments:
                    return self.value
                self.signature, self.value = signature, (duration, segments)
            except (OSError, UnicodeError):
                pass
            return self.value


def _valid_producer(task, proc):
    """Caller holds _sess_lock, including the eventual STOP/CONT operation."""
    return (task.get("proc") is proc and not task.get("cancelled")
            and not task.get("stopping") and not getattr(proc, "_jz_terminating", False)
            and proc.poll() is None)


def _owners(task):
    now = time.time()
    return [sess for sid in task.get("owners", ())
            if (sess := common._sessions.get(sid)) is not None
            and sess.get("task") is task and not common._session_expired(sess, now)]


def _state(sess, task, now):
    value = sess.get("_flow_state")
    if value is None:
        start = _number(sess.get("requested_start"))
        if start is None:
            start = float(task.get("media_start") or 0)
        value = dict(position=max(0.0, start), observed=now, paused=False,
                     rate=1.0, explicit=False, requested=max(0.0, start))
        sess["_flow_state"] = value
    return value


def _position(state, now):
    if not state["explicit"]:
        return state["requested"]
    elapsed = max(0.0, now - state["observed"])
    if state["paused"]:
        return state["position"]
    position = state["position"] + min(_EXTRAPOLATE_SECONDS, elapsed) * state["rate"]
    return max(position, state["requested"]) if elapsed > _EXTRAPOLATE_SECONDS else position


def _resume(task, proc):
    if not getattr(proc, "_jz_buffer_paused", False):
        return True
    if common._resume_proc(proc):
        return True
    flow = task["_flow"]
    if not flow.get("resume_error"):
        logger.warning("HLS producer resume failed")
        flow["resume_error"] = True
    return False


def _control_once(task, proc, *, now=None):
    flow = task.get("_flow")
    if not flow or not flow["window_supported"]:
        return False
    snapshot = flow["playlist"].read()  # Disk reads never hold the shared lease lock.
    now = time.monotonic() if now is None else now
    with common._sess_lock:
        if task.get("_flow") is not flow or not _valid_producer(task, proc):
            return False
        ready = task.get("ready")
        if ready is not None and not ready.is_set():
            flow.update(reason="initializing", ahead_seconds=None)
            _resume(task, proc)
            return True  # Startup must first produce its required initial segments.
        owners = _owners(task)
        if any(sess.get("prewarm") for sess in owners):
            flow["reason"] = "prewarm"
            flow["ahead_seconds"] = None
            _resume(task, proc)
            return True
        if snapshot is None:
            flow["reason"] = "waiting-playlist"
            return True
        duration, _segments = snapshot
        produced = float(task.get("media_start") or 0) + duration
        demand = max((_position(_state(sess, task, now), now) for sess in owners),
                     default=float(task.get("media_start") or 0))
        total = flow["duration"]
        if total > 0:
            demand = min(demand, total)
        ahead = max(0.0, produced - demand)
        flow.update(ahead_seconds=ahead, produced_until=produced, demand_until=demand)
        if getattr(proc, "_jz_buffer_paused", False):
            if ahead <= flow["lookahead_seconds"] / 2:
                if _resume(task, proc):
                    flow["reason"] = "producing"
            else:
                flow["reason"] = "buffered"
        elif ahead >= flow["lookahead_seconds"]:
            try:
                proc.send_signal(signal.SIGSTOP)
            except OSError as e:
                flow.update(window_supported=False, reason="signal-failed")
                logger.warning("HLS lookahead control unavailable reason=signal-failed error=%s", type(e).__name__)
                return False
            proc._jz_buffer_paused = True
            flow["reason"] = "buffered"
        else:
            flow["reason"] = "producing"
    return True


def _control(task, proc):
    flow = task["_flow"]
    while True:
        flow["wake"].clear()
        try:
            if not _control_once(task, proc):
                return
        except Exception:
            logger.exception("HLS lookahead control failed")
            with common._sess_lock:
                if task.get("_flow") is flow and _valid_producer(task, proc):
                    _resume(task, proc)
                    flow.update(window_supported=False, reason="control-failed")
            return
        flow["wake"].wait(_POLL_SECONDS)


def start_producer_control(task, proc, plan, duration):
    window = lookahead_seconds()
    supported = (hasattr(signal, "SIGSTOP") and hasattr(signal, "SIGCONT")
                 and callable(getattr(proc, "send_signal", None))
                 and task.get("flow_input_supported", True))
    reason = "producing"
    if plan.get("seg") == "mp4":
        supported, reason = False, "finite-mp4"
    elif not window:
        supported, reason = False, "disabled"
    elif not supported:
        reason = "unsupported-input" if not task.get("flow_input_supported", True) else "unsupported-signals"
        logger.info("HLS lookahead control unavailable reason=%s", reason)
    with common._sess_lock:
        previous = task.get("_flow")
        if previous and previous["proc"] is proc:
            return
        if not _valid_producer(task, proc):
            return
        flow = dict(proc=proc, window_supported=bool(supported), lookahead_seconds=window,
                    ahead_seconds=None, reason=reason, duration=_number(duration) or 0.0,
                    wake=threading.Event(), playlist=_Playlist(
                        Path(task["sdir"]) / ("master.m3u8" if plan.get("seg") == "ts" else "out_video.m3u8")))
        task["_flow"] = flow
        if not supported:
            return
        thread = threading.Thread(target=_control, args=(task, proc),
                                  name="hls-lookahead", daemon=True)
        flow["thread"] = thread
        thread.start()


def wake_producer_control(task):
    if task is None:
        return
    with common._sess_lock:
        flow = task.get("_flow")
        if flow is None:
            return
        proc = flow["proc"]
        if (flow["window_supported"] and _valid_producer(task, proc)
                and any(sess.get("prewarm") for sess in _owners(task))):
            _resume(task, proc)
        flow["wake"].set()


def record_playback_state(sid, sess, position=None, paused=None, playback_rate=None):
    with common._sess_lock:
        task = sess.get("task")
        if common._sessions.get(sid) is not sess or task is None:
            return
        state = _state(sess, task, time.monotonic())
        observed = time.monotonic()
        current = _number(position)
        rate = _number(playback_rate)
        if current is None or current < 0:
            if not isinstance(paused, bool) and not (rate is not None and 0 < rate <= 16):
                return  # A legacy empty heartbeat cannot advance production demand.
            current = _position(state, observed)
        else:
            state["explicit"] = True
        duration = (task.get("_flow") or {}).get("duration", 0)
        state.update(position=min(current, duration) if duration > 0 else current,
                     observed=observed)
        if isinstance(paused, bool):
            state["paused"] = paused
        if rate is not None and 0 < rate <= 16:
            state["rate"] = rate
        wake_producer_control(task)


def record_video_request(sid, sess, name):
    if not _VIDEO_NAME.fullmatch(name):
        return
    with common._sess_lock:
        task = sess.get("task")
        flow = task.get("_flow") if task else None
        if common._sessions.get(sid) is not sess or flow is None:
            return
    snapshot = flow["playlist"].read()
    if snapshot is None or name not in snapshot[1]:
        return
    with common._sess_lock:
        if common._sessions.get(sid) is not sess or task.get("_flow") is not flow:
            return
        state = _state(sess, task, time.monotonic())
        end = float(task.get("media_start") or 0) + snapshot[1][name][1]
        state["requested"] = max(state["requested"], end)
        wake_producer_control(task)


def flow_status(sess):
    with common._sess_lock:
        task = sess.get("task")
        flow = task.get("_flow") if task else None
        proc = sess.get("proc")
        return {"buffer_paused": bool(proc and getattr(proc, "_jz_buffer_paused", False)),
                "ahead_seconds": flow["ahead_seconds"] if flow else None,
                "window_supported": bool(flow and flow["window_supported"]),
                "lookahead_seconds": flow["lookahead_seconds"] if flow else lookahead_seconds(),
                "flow_reason": flow["reason"] if flow else "static"}
