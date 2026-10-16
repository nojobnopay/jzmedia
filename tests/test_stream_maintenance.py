"""Run the real maintenance loop against isolated leases and cache files."""
import asyncio
from pathlib import Path
import os
import signal
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.stream import common


class Process:
    def __init__(self, *, complete=False, paused=False):
        self.returncode = 0 if complete else None
        self._jz_buffer_paused = paused
        self.events = []

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.events.append(sig)

    def terminate(self):
        self.events.append("terminate")
        self.returncode = -15

    def wait(self, timeout=None):
        assert self.returncode is not None
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
    monkeypatch.setattr(common, "TRANSCODE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "_cap_last", 0.0)
    monkeypatch.setattr(common, "_SWEEP_INTERVAL", 3600.0)
    yield
    common.stop_session_maintenance()
    common.shutdown_sessions()
    with common._sess_lock:
        common._sessions.clear()
        common._sessions.update(saved_sessions)
        common._tasks.clear()
        common._tasks.update(saved_tasks)


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.01)
    assert predicate()


def lease(sid, directory, *, age=0, proc=None, unclaimed=False, prewarm=False, task=None):
    now = time.time()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    value = dict(sdir=str(directory), vid=1, kind="movie", plan={}, proc=proc,
                 created=now - age, last_ping=now - age, unclaimed=unclaimed,
                 prewarm=prewarm, task=task)
    with common._sess_lock:
        common._sessions[sid] = value
    return value


def cache(root, name, *, age, size=100):
    directory = root / "m1" / name
    directory.mkdir(parents=True)
    (directory / "video_seg00000.m4s").write_bytes(b"v" * size)
    timestamp = time.time() - age
    os.utime(directory, (timestamp, timestamp))
    return directory


def test_loop_start_stop_is_idempotent_interruptible_and_restartable(monkeypatch):
    calls = []
    monkeypatch.setattr(common, "_maintain_sessions", lambda: calls.append(True))
    common.start_session_maintenance()
    first = common._sweeper_thread
    wait_for(lambda: calls)
    common.start_session_maintenance()
    assert common._sweeper_thread is first
    common.stop_session_maintenance()
    assert not first.is_alive()
    assert common._sweeper_thread is None
    common.stop_session_maintenance()
    common.start_session_maintenance()
    second = common._sweeper_thread
    wait_for(lambda: len(calls) >= 2)
    assert second is not first
    common.stop_session_maintenance()
    assert not second.is_alive()


def test_loop_expires_unclaimed_and_idle_leases_without_new_playback(tmp_path):
    unclaimed, idle = Process(), Process()
    lease("unclaimed", tmp_path / "m1" / "a", age=91, proc=unclaimed, unclaimed=True)
    lease("idle", tmp_path / "m1" / "b", age=601, proc=idle)
    lease("active", tmp_path / "m1" / "c", proc=Process())
    common.start_session_maintenance()
    wait_for(lambda: "idle" not in common._sessions)
    assert unclaimed.events == ["terminate"]
    assert idle.events == ["terminate"]
    assert "active" in common._sessions


@pytest.mark.parametrize("unclaimed", [False, True])
def test_renewed_lease_is_rechecked_before_expiry(tmp_path, unclaimed):
    proc = Process()
    value = lease("renewed", tmp_path / "m1" / "a", age=601,
                  proc=proc, unclaimed=unclaimed)
    scanned_at = time.time()
    assert common._dead_sessions(scanned_at) == ["renewed"]
    assert common._get_session("renewed") is value
    common._drop_session("renewed", expired_at=scanned_at)
    assert "renewed" in common._sessions
    assert not proc.events


def test_get_session_cannot_renew_a_lease_removed_during_authorization(monkeypatch, tmp_path):
    from app import viewing
    proc = Process()
    value = lease("closed", tmp_path / "m1" / "a", proc=proc)
    claimed_at = value["last_ping"]
    token = viewing.current.set(SimpleNamespace(session_hash="test"))
    monkeypatch.setattr(viewing, "visible_item", lambda *_args: common._drop_session("closed"))
    try:
        with pytest.raises(HTTPException) as error:
            common._get_session("closed")
    finally:
        viewing.current.reset(token)
    assert error.value.status_code == 404
    assert value["last_ping"] == claimed_at
    assert "closed" not in common._sessions


def test_expiry_preserves_shared_viewer_prewarm_and_initialization(tmp_path):
    proc = Process()
    ready = threading.Event()
    ready.set()
    task = dict(key=("movie", 1, "shared"), sdir=str(tmp_path / "m1" / "shared"),
                proc=proc, owners={"old", "viewer", "prewarm"}, ready=ready,
                done=threading.Event())
    common._tasks[task["key"]] = task
    lease("old", task["sdir"], proc=proc, age=601, task=task)
    lease("viewer", task["sdir"], proc=proc, task=task)
    lease("prewarm", task["sdir"], proc=proc, age=10000, prewarm=True, task=task)
    initializing = {**task, "ready": threading.Event(), "owners": {"starting"}}
    lease("starting", tmp_path / "m1" / "starting", proc=Process(), age=10000,
          unclaimed=True, task=initializing)
    common._maintain_sessions()
    assert set(common._sessions) == {"viewer", "prewarm", "starting"}
    assert task["owners"] == {"viewer", "prewarm"}
    assert not proc.events


def test_completed_idle_task_releases_cache_protection_and_is_evicted(monkeypatch, tmp_path):
    proc = Process(complete=True)
    directory = cache(tmp_path, "complete", age=400)
    ready = threading.Event()
    ready.set()
    task = dict(key=("movie", 1, "done"), sdir=str(directory), proc=proc,
                owners={"finished"}, ready=ready, done=threading.Event(), complete=True)
    common._tasks[task["key"]] = task
    lease("finished", directory, proc=proc, age=301, task=task)
    monkeypatch.setenv("TRANSCODE_CACHE_GB", str(50 / (1 << 30)))
    common.start_session_maintenance()
    wait_for(lambda: not directory.exists())
    assert "finished" not in common._sessions
    assert not common._tasks


@pytest.mark.parametrize("mode", ["ttl", "cap"])
def test_loop_reclaims_unowned_cache_without_any_new_session(monkeypatch, tmp_path, mode):
    age = common._TTL + 1 if mode == "ttl" else common._CACHE_MIN_AGE + 1
    directory = cache(tmp_path, "unused", age=age)
    monkeypatch.setenv("TRANSCODE_CACHE_GB", "0" if mode == "ttl" else str(50 / (1 << 30)))
    common.start_session_maintenance()
    wait_for(lambda: not directory.exists())


def test_failed_cleanup_does_not_report_freed_bytes(monkeypatch, tmp_path, caplog):
    directory = cache(tmp_path, "denied", age=1000)
    def denied(_path):
        raise PermissionError("test cache denied")
    monkeypatch.setattr(common.shutil, "rmtree", denied)
    result = common._evict_transcode_cache(cap=50)
    assert result["removed"] == result["freed"] == 0
    assert directory.exists()
    assert "remove transcode cache failed" in caplog.text


def test_exception_in_one_lease_does_not_prevent_other_expiry_or_cache_pass(monkeypatch, tmp_path):
    lease("bad", tmp_path / "m1" / "a", age=601, proc=Process())
    proc = Process()
    lease("good", tmp_path / "m1" / "b", age=601, proc=proc)
    drop = common._drop_session
    cache_pass = []
    def dropping(sid, **kwargs):
        if sid == "bad":
            raise RuntimeError("test lease failure")
        drop(sid, **kwargs)
    monkeypatch.setattr(common, "_drop_session", dropping)
    monkeypatch.setattr(common, "_purge_old", lambda: cache_pass.append(True))
    common._maintain_sessions()
    assert proc.events == ["terminate"]
    assert "good" not in common._sessions
    assert cache_pass
    # The deliberately failing lease must not interfere with fixture teardown.
    monkeypatch.setattr(common, "_drop_session", drop)


def test_loop_survives_a_failed_pass_and_wakes_again(monkeypatch):
    calls = []
    def maintain():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("test maintenance failure")
    monkeypatch.setattr(common, "_maintain_sessions", maintain)
    common.start_session_maintenance()
    wait_for(lambda: calls)
    common._wake_session_maintenance()
    wait_for(lambda: len(calls) >= 2)
    assert common._sweeper_thread.is_alive()


@pytest.mark.skipif(not hasattr(signal, "SIGCONT"), reason="POSIX process control")
def test_paused_producer_is_resumed_before_termination():
    proc = Process(paused=True)
    common._kill_proc(proc)
    assert proc.events == [signal.SIGCONT, "terminate"]
    assert not proc._jz_buffer_paused


def test_lifespan_starts_maintenance_after_orphans_and_stops_on_exception(monkeypatch):
    from app import main, mounts, posters, transcode, tv_airing
    order = []
    monkeypatch.setattr(main, "ensure_dirs", lambda: None)
    monkeypatch.setattr(main.store, "init_db", lambda: None)
    monkeypatch.setattr(posters, "migrate_posters", lambda: None)
    monkeypatch.setattr(tv_airing, "start", lambda: None)
    monkeypatch.setattr(tv_airing, "shutdown", lambda: None)
    monkeypatch.setattr(main.cast.manager, "shutdown", lambda: None)
    monkeypatch.setattr(mounts, "start_watchdog", lambda: None)
    monkeypatch.setattr(mounts, "shutdown_mounts", lambda: None)
    monkeypatch.setattr(transcode, "detect", lambda: None)
    monkeypatch.setattr(main.stream, "start_auto_previews", lambda: None)
    monkeypatch.setattr(main.stream, "shutdown_previews", lambda: None)
    monkeypatch.setattr(main.stream, "shutdown_sessions", lambda: order.append("sessions-stop"))
    monkeypatch.setattr(main.stream, "_reap_orphans", lambda: order.append("orphans") or 0)
    monkeypatch.setattr(main.stream, "start_session_maintenance", lambda: order.append("start"))
    monkeypatch.setattr(main.stream, "stop_session_maintenance", lambda: order.append("stop"))
    async def run():
        async with main._lifespan(main.app):
            order.append("running")
            raise RuntimeError("test app failure")
    with pytest.raises(RuntimeError, match="test app failure"):
        asyncio.run(run())
    assert order == ["orphans", "start", "running", "stop", "sessions-stop"]
