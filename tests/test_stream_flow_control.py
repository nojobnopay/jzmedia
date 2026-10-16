"""HLS production uses actual video media time and all live client leases."""
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from app.routers.stream import common, flow_control as flow

_REAL_CONTROL = flow._control


class Process:
    def __init__(self):
        self.returncode = None
        self.events = []

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.events.append(sig)

    def terminate(self):
        self.events.append("terminate")
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    kill = terminate


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    common.stop_session_maintenance()
    with common._sess_lock:
        saved_sessions = dict(common._sessions)
        saved_tasks = dict(common._tasks)
        common._sessions.clear()
        common._tasks.clear()
    monkeypatch.setenv("TRANSCODE_LOOKAHEAD_SECONDS", "120")
    monkeypatch.setattr(common, "TRANSCODE_DIR", str(tmp_path))
    # Unit tests step the real control pass themselves; the process test restores it.
    monkeypatch.setattr(flow, "_control", lambda *_args: None)
    yield
    common.shutdown_sessions()
    for task in list(common._tasks.values()):
        thread = (task.get("_flow") or {}).get("thread")
        if thread:
            thread.join(timeout=2)
    with common._sess_lock:
        common._sessions.clear()
        common._sessions.update(saved_sessions)
        common._tasks.clear()
        common._tasks.update(saved_tasks)


def task_and_viewer(tmp_path, *, proc=None, start=100.0, seg="fmp4", supported=True):
    proc = proc or Process()
    ready = threading.Event()
    ready.set()
    task = dict(key=("movie", 1, "flow"), sdir=str(tmp_path), proc=proc,
                media_start=start, owners={"viewer"}, ready=ready, done=threading.Event(),
                flow_input_supported=supported)
    viewer = dict(task=task, proc=proc, requested_start=start, last_ping=time.time(),
                  sdir=str(tmp_path), vid=1, plan={"seg": seg})
    common._tasks[task["key"]] = task
    common._sessions["viewer"] = viewer
    flow.start_producer_control(task, proc, {"seg": seg}, 1000)
    return task, proc, viewer


def playlist(directory, durations, *, seg="fmp4"):
    lines = ["#EXTM3U", "#EXT-X-MEDIA-SEQUENCE:0"]
    for index, duration in enumerate(durations):
        name = f"video_seg{index:05d}.m4s" if seg == "fmp4" else f"seg{index:05d}.ts"
        lines.extend([f"#EXTINF:{duration},", name])
    name = "out_video.m3u8" if seg == "fmp4" else "master.m3u8"
    (directory / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize("value,expected", [("0", 0), ("1", 10), ("120", 120),
    ("1000", 600), ("-1", 120), ("nan", 120), ("inf", 120), ("invalid", 120)])
def test_lookahead_configuration_is_bounded_and_zero_disables(monkeypatch, value, expected):
    monkeypatch.setenv("TRANSCODE_LOOKAHEAD_SECONDS", value)
    assert flow.lookahead_seconds() == expected


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="POSIX signals")
@pytest.mark.parametrize("seg", ["fmp4", "ts"])
def test_true_extinf_durations_pause_and_video_request_resumes(tmp_path, seg):
    playlist(tmp_path, [30, 30, 30, 40], seg=seg)
    task, proc, viewer = task_and_viewer(tmp_path, seg=seg)
    assert flow._control_once(task, proc)
    assert proc.events == [signal.SIGSTOP]
    assert flow.flow_status(viewer)["ahead_seconds"] == 130
    name = "video_seg00002.m4s" if seg == "fmp4" else "seg00002.ts"
    flow.record_video_request("viewer", viewer, name)
    flow._control_once(task, proc)
    assert proc.events == [signal.SIGSTOP, signal.SIGCONT]
    assert task["_flow"]["demand_until"] == 190
    assert flow.flow_status(viewer)["ahead_seconds"] == 40


def test_audio_playlist_and_empty_heartbeat_cannot_advance_legacy_demand(tmp_path):
    playlist(tmp_path, [30, 30, 30, 40])
    task, proc, viewer = task_and_viewer(tmp_path)
    for name in ("audio0_seg00003.m4s", "out_video.m3u8", "video_init.mp4"):
        flow.record_video_request("viewer", viewer, name)
    flow.record_playback_state("viewer", viewer)
    flow._control_once(task, proc)
    assert task["_flow"]["demand_until"] == 100
    assert task["_flow"]["ahead_seconds"] == 130


def test_explicit_position_overrides_prefetched_video_segments(tmp_path):
    playlist(tmp_path, [30, 30, 30, 40])
    task, proc, viewer = task_and_viewer(tmp_path)
    flow.record_video_request("viewer", viewer, "video_seg00003.m4s")
    flow.record_playback_state("viewer", viewer, position=110, paused=True, playback_rate=2)
    flow._control_once(task, proc)
    assert task["_flow"]["demand_until"] == 110
    assert flow.flow_status(viewer)["ahead_seconds"] == 120


def test_playing_extrapolation_is_bounded_but_pause_never_extrapolates(tmp_path):
    playlist(tmp_path, [130])
    task, proc, viewer = task_and_viewer(tmp_path)
    flow.record_playback_state("viewer", viewer, position=110, paused=False, playback_rate=2)
    observed = viewer["_flow_state"]["observed"]
    flow._control_once(task, proc, now=observed + 1000)
    assert task["_flow"]["demand_until"] == 140
    flow.record_playback_state("viewer", viewer, position=110, paused=True)
    flow._control_once(task, proc, now=observed + 1000)
    assert task["_flow"]["demand_until"] == 110


@pytest.mark.parametrize("paused", [False, True])
def test_timer_throttling_uses_video_requests_only_while_playing(tmp_path, paused):
    playlist(tmp_path, [30, 30, 30, 40])
    task, proc, viewer = task_and_viewer(tmp_path)
    flow.record_playback_state("viewer", viewer, position=110, paused=paused)
    observed = viewer["_flow_state"]["observed"]
    flow.record_video_request("viewer", viewer, "video_seg00003.m4s")
    flow._control_once(task, proc, now=observed + 30)
    assert task["_flow"]["demand_until"] == (110 if paused else 230)


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="POSIX signals")
def test_startup_can_fill_initial_segments_before_a_small_window_pauses(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSCODE_LOOKAHEAD_SECONDS", "10")
    playlist(tmp_path, [130])
    task, proc, viewer = task_and_viewer(tmp_path)
    task["ready"].clear()
    assert flow._control_once(task, proc)
    assert not proc.events
    assert flow.flow_status(viewer)["flow_reason"] == "initializing"
    task["ready"].set()
    flow._control_once(task, proc)
    assert proc.events == [signal.SIGSTOP]


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="POSIX signals")
def test_high_and_low_watermarks_have_hysteresis(tmp_path):
    playlist(tmp_path, [130])
    task, proc, viewer = task_and_viewer(tmp_path)
    flow.record_playback_state("viewer", viewer, position=100, paused=True)
    flow._control_once(task, proc)
    assert getattr(proc, "_jz_buffer_paused", False)
    flow.record_playback_state("viewer", viewer, position=160, paused=True)
    flow._control_once(task, proc)
    assert getattr(proc, "_jz_buffer_paused", False)  # 70 seconds is above the 60-second low mark.
    flow.record_playback_state("viewer", viewer, position=170, paused=True)
    flow._control_once(task, proc)
    assert not proc._jz_buffer_paused
    assert proc.events == [signal.SIGSTOP, signal.SIGCONT]


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="POSIX signals")
def test_all_viewers_contribute_and_departure_recomputes_demand(tmp_path):
    playlist(tmp_path, [150])
    task, proc, viewer = task_and_viewer(tmp_path)
    other = {**viewer, "requested_start": 200}
    common._sessions["other"] = other
    task["owners"].add("other")
    flow._control_once(task, proc)
    assert not getattr(proc, "_jz_buffer_paused", False)  # Shared demand leaves only 50 seconds ahead.
    common._drop_session("other")
    flow.wake_producer_control(task)
    flow._control_once(task, proc)
    assert getattr(proc, "_jz_buffer_paused", False)
    assert "viewer" in common._sessions


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="POSIX signals")
def test_dynamic_prewarm_owner_immediately_resumes_paused_producer(tmp_path):
    playlist(tmp_path, [130])
    task, proc, viewer = task_and_viewer(tmp_path)
    flow._control_once(task, proc)
    common._sessions["prewarm"] = {**viewer, "prewarm": True, "last_ping": 1}
    task["owners"].add("prewarm")
    flow.wake_producer_control(task)
    assert not proc._jz_buffer_paused
    flow._control_once(task, proc)
    assert flow.flow_status(viewer)["flow_reason"] == "prewarm"
    common._drop_session("prewarm")
    flow._control_once(task, proc)
    assert proc._jz_buffer_paused
    assert proc.events == [signal.SIGSTOP, signal.SIGCONT, signal.SIGSTOP]


@pytest.mark.parametrize("mode", ["mp4", "input", "disabled", "signals"])
def test_unsupported_or_disabled_paths_skip_control_with_status(monkeypatch, tmp_path, mode):
    if mode == "disabled":
        monkeypatch.setenv("TRANSCODE_LOOKAHEAD_SECONDS", "0")
    if mode == "signals":
        monkeypatch.delattr(flow.signal, "SIGSTOP", raising=False)
    task, proc, viewer = task_and_viewer(tmp_path, seg="mp4" if mode == "mp4" else "fmp4",
                                       supported=mode != "input")
    assert not flow.flow_status(viewer)["window_supported"]
    assert not flow._control_once(task, proc)
    assert not proc.events


@pytest.mark.parametrize("mode", ["cancelled", "stopping", "terminating", "replaced"])
def test_old_or_terminating_producer_cannot_be_stopped(tmp_path, mode):
    playlist(tmp_path, [130])
    task, proc, _viewer = task_and_viewer(tmp_path)
    if mode == "terminating":
        proc._jz_terminating = True
    elif mode == "replaced":
        task["proc"] = Process()
    else:
        task[mode] = True
    assert not flow._control_once(task, proc)
    assert not proc.events


def test_playlist_cache_retains_last_consistent_snapshot(monkeypatch, tmp_path):
    playlist(tmp_path, [3.5, 9.25])
    reader = flow._Playlist(tmp_path / "out_video.m3u8")
    first = reader.read()
    assert first[0] == 12.75
    def unreadable(*_args, **_kwargs):
        pytest.fail("unchanged playlist must use cached snapshot")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", unreadable)
        assert reader.read() is first
    playlist(tmp_path, [3.5, math.nan])
    assert reader.read() is first
    playlist(tmp_path, [3.5, 9.25, 5])
    assert reader.read()[0] == 17.75


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="POSIX signals")
def test_signal_failure_is_diagnosed_and_does_not_claim_pause(monkeypatch, tmp_path, caplog):
    playlist(tmp_path, [130])
    task, proc, viewer = task_and_viewer(tmp_path)
    def denied(_sig):
        raise PermissionError("test signal denied")
    monkeypatch.setattr(proc, "send_signal", denied)
    assert not flow._control_once(task, proc)
    status = flow.flow_status(viewer)
    assert not status["window_supported"] and not status["buffer_paused"]
    assert "reason=signal-failed" in caplog.text


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.01)
    assert predicate()


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="POSIX signals")
def test_real_process_pauses_resumes_and_is_killed_while_paused(monkeypatch, tmp_path):
    monkeypatch.setattr(flow, "_control", _REAL_CONTROL)
    counter = tmp_path / "counter"
    program = ("import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\ni=0\n"
               "while True:\n i+=1\n p.write_text(str(i))\n time.sleep(.01)\n")
    proc = subprocess.Popen([sys.executable, "-c", program, str(counter)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(counter.exists)
        playlist(tmp_path, [130])
        task, _proc, viewer = task_and_viewer(tmp_path, proc=proc)
        wait_for(lambda: getattr(proc, "_jz_buffer_paused", False))
        wait_for(lambda: os.WIFSTOPPED(os.waitpid(proc.pid, os.WNOHANG | os.WUNTRACED)[1]))
        before = counter.read_text()
        threading.Event().wait(0.08)
        assert counter.read_text() == before
        flow.record_playback_state("viewer", viewer, position=200, paused=False)
        wait_for(lambda: not proc._jz_buffer_paused and counter.read_text() != before)
        flow.record_playback_state("viewer", viewer, position=100, paused=True)
        wait_for(lambda: proc._jz_buffer_paused)
        common._drop_session("viewer")
        assert proc.poll() is not None
        assert not proc._jz_buffer_paused
        task["_flow"]["thread"].join(timeout=2)
        assert not task["_flow"]["thread"].is_alive()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=3)
