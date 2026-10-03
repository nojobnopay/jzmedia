"""Concurrent clients own leases, while identical outputs share one process."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import os
import shutil
import subprocess
import threading
import time

import pytest
from fastapi import HTTPException

from app.routers.stream import common, prewarm, session


class Producer:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.exited = threading.Event()
        self.returncode = None
        self.pid = 999999999
        self.terminated = False
        (self.directory / "video_seg00000.m4s").write_bytes(b"video")
        (self.directory / "out_video.m3u8").write_text(
            "#EXTM3U\n#EXTINF:4.0,\nvideo_seg00000.m4s\n")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self.exited.wait(timeout):
            raise subprocess.TimeoutExpired("test producer", timeout)
        return self.returncode

    def finish(self, returncode=0):
        if returncode == 0:
            with (self.directory / "out_video.m3u8").open("a") as fh:
                fh.write("#EXT-X-ENDLIST\n")
        self.returncode = returncode
        self.exited.set()

    def terminate(self):
        self.terminated = True
        self.finish(-15)

    kill = terminate


@pytest.fixture
def producers(monkeypatch, tmp_path):
    assert not common._tasks
    created = []
    saved_sessions = dict(common._sessions)
    common._sessions.clear()
    monkeypatch.setattr(common, "TRANSCODE_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_hls_sem", threading.BoundedSemaphore(2))
    monkeypatch.setattr(session, "_version_source", lambda vid, kind: (
        {"id": vid, "kind": kind, "library_id": 1, "file_path": "movie.mkv"},
        SimpleNamespace(input="unused", size=100, mtime=1)))
    monkeypatch.setattr(session, "_media_cached_or_probe", lambda *a: {
        "playable": True, "duration": 120.0})
    monkeypatch.setattr(session, "_media_payload", lambda *a: {})
    monkeypatch.setattr(session, "_media_start_for", lambda vid, src, start, plan, kind: max(0, start - 1))
    monkeypatch.setattr(session, "_ffmpeg_ok", lambda: True)
    monkeypatch.setattr(session._playback, "hw_backend", lambda: "")
    monkeypatch.setattr(session._playback, "build_cmd", lambda *a, **kw: ["fake-ffmpeg"])
    monkeypatch.setattr(session._playback, "plan", lambda *a, **kw: {
        "method": "remux", "reasons": [], "plan": {
            "vcopy": True, "seg": "fmp4", "sub": "none",
            "height": 720 if kw["quality"] == "720p" else 0,
            "audios": [{"copy": not (kw.get("caps") or {}).get("native_hls")}],
        }})

    def popen(cmd, **kwargs):
        proc = Producer(kwargs["cwd"])
        created.append(proc)
        return proc

    monkeypatch.setattr(session.subprocess, "Popen", popen)
    yield created
    tasks = list(common._tasks.values())
    common.shutdown_sessions()
    for task in tasks:
        task["done"].wait(2)
    assert not common._tasks
    common._sessions.update(saved_sessions)
    prewarm._prewarm_jobs.clear()


def create(**kwargs):
    return session.hls_session_create(1, session.SessionBody(**kwargs))


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_concurrent_same_output_gets_unique_leases_and_one_process(producers, monkeypatch):
    barrier = threading.Barrier(2)
    resolve = session._version_source

    def source(*args):
        barrier.wait(timeout=3)
        return resolve(*args)

    monkeypatch.setattr(session, "_version_source", source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: create(), range(2)))
    assert a["session_id"] != b["session_id"]
    assert len(producers) == 1
    sa, sb = [common._sessions[r["session_id"]] for r in (a, b)]
    assert sa["task"] is sb["task"]
    assert session.hls_session_close(a["session_id"])["closed"]
    assert not producers[0].terminated
    assert session.hls_session_ping(b["session_id"])["running"]
    session.hls_session_close(b["session_id"])
    assert producers[0].terminated
    assert not common._tasks


def test_different_outputs_coexist_until_process_limit(producers):
    a, b = create(), create(quality="720p", start=12)
    assert common._sessions[a["session_id"]]["sdir"] != common._sessions[b["session_id"]]["sdir"]
    assert len(producers) == 2
    with pytest.raises(HTTPException) as exc:
        create(start=24)
    assert exc.value.status_code == 429
    assert all(p.poll() is None for p in producers)
    # Existing identical output can attach even at the cap.
    shared = create()
    session.hls_session_close(a["session_id"])
    assert producers[0].poll() is None
    session.hls_session_close(shared["session_id"])
    create(start=24)
    assert len(producers) == 3
    assert producers[1].poll() is None


def test_complete_hash_separates_audio_policy_fractional_seek_and_source(producers, monkeypatch):
    first = create(start=10.1)
    second = create(start=10.2)
    first_dir = common._sessions[first["session_id"]]["sdir"]
    second_dir = common._sessions[second["session_id"]]["sdir"]
    assert first_dir != second_dir
    session.hls_session_close(second["session_id"])
    audio = create(start=10.1, caps={"native_hls": True})
    assert common._sessions[audio["session_id"]]["sdir"] != first_dir
    session.hls_session_close(audio["session_id"])
    resolve = session._version_source

    def replaced(*args):
        row, src = resolve(*args)
        src.mtime = 2
        return row, src

    monkeypatch.setattr(session, "_version_source", replaced)
    replacement = create(start=10.1)
    assert common._sessions[replacement["session_id"]]["sdir"] != first_dir
    assert not producers[0].terminated


def test_natural_completion_releases_slot_and_full_cache_resumes(producers):
    a = create()
    b = create(quality="720p")
    task = common._sessions[a["session_id"]]["task"]
    producers[0].finish()
    assert task["done"].wait(3)
    assert task["complete"]
    create(quality="720p", start=30)  # A newly available slot can start another producer.
    resumed = create(start=50)
    assert resumed["complete"] and resumed["media_start"] == 0
    assert resumed["initial_time"] == 50
    assert common._sessions[resumed["session_id"]]["task"] is task
    assert len(producers) == 3  # Resume reuses the completed full artifact.
    assert session.hls_session_ping(b["session_id"])["running"]


def test_prewarm_holds_producer_after_viewer_closes(producers):
    viewer = create()
    prewarm._prewarm_jobs["job"] = {"status": "queued"}
    worker = threading.Thread(target=prewarm._prewarm_worker, args=("job", 1, "auto", 0))
    worker.start()
    wait_for(lambda: prewarm._prewarm_jobs["job"].get("sid"))
    job = prewarm._prewarm_jobs["job"]
    assert job["attached"]
    assert job["sid"] != viewer["session_id"]
    session.hls_session_close(viewer["session_id"])
    assert not producers[0].terminated
    producers[0].finish()
    worker.join(3)
    assert not worker.is_alive()
    assert job["status"] == "done"
    assert job["sid"] not in common._sessions
    assert create()["complete"]
    assert len(producers) == 1


def test_prewarm_other_output_coexists_and_does_not_clear_viewer(producers):
    viewer = create()
    before = producers[0].directory / "keep.m4s"
    before.write_bytes(b"keep")
    prewarm._prewarm_jobs["job"] = {"status": "queued"}
    worker = threading.Thread(target=prewarm._prewarm_worker, args=("job", 1, "720p", 0))
    worker.start()
    wait_for(lambda: prewarm._prewarm_jobs["job"].get("sid"))
    assert len(producers) == 2
    producers[1].finish()
    worker.join(3)
    assert prewarm._prewarm_jobs["job"]["status"] == "done"
    assert before.read_bytes() == b"keep"
    assert session.hls_session_ping(viewer["session_id"])["running"]


def test_expiring_one_unclaimed_lease_does_not_stop_claimed_viewer(producers):
    abandoned, active = create(), create()
    now = time.time()
    common._sessions[abandoned["session_id"]]["created"] = now - common._UNCLAIMED_TTL - 1
    common._get_session(active["session_id"])
    dead = common._dead_sessions(now)
    assert dead == [abandoned["session_id"]]
    for sid in dead:
        common._drop_session(sid)
    assert not producers[0].terminated


def test_slow_termination_does_not_block_other_clients_or_clear_successor(producers, monkeypatch):
    closing, active = create(), create(start=20)
    old = producers[0]
    task = common._sessions[closing["session_id"]]["task"]
    waiting, proceed = threading.Event(), threading.Event()
    original_wait = old.wait

    def delayed_wait(timeout=None):
        if timeout is not None:
            waiting.set()
            assert proceed.wait(5)
        return original_wait(timeout)

    # Simulate FFmpeg ignoring TERM. The completion watcher still awaits actual exit.
    monkeypatch.setattr(old, "terminate", lambda: None)
    monkeypatch.setattr(old, "wait", delayed_wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        closer = pool.submit(session.hls_session_close, closing["session_id"])
        assert waiting.wait(2)
        try:
            getter = pool.submit(common._get_session, active["session_id"])
            assert getter.result(timeout=1)["proc"] is producers[1]
            assert common._tasks[task["key"]] is task and task["cancelled"]
            with pytest.raises(HTTPException) as exc:
                create()
            assert exc.value.status_code == 503
            with pytest.raises(HTTPException) as exc:
                create(start=40)
            assert exc.value.status_code == 429  # The stopping process still holds its slot.

            # Its watcher can observe exit and remove the old task before the DELETE
            # thread returns from wait. That late cleanup must not clear the replacement.
            old.finish(-15)
            assert task["done"].wait(2)
            replacement = create()
            new_task = common._sessions[replacement["session_id"]]["task"]
            assert new_task is not task
            meta = Path(new_task["sdir"]) / "session.json"
            replacement_meta = meta.read_bytes()
        finally:
            old.finish(-15)
            proceed.set()
        assert closer.result(timeout=2)["closed"]
        assert meta.read_bytes() == replacement_meta
        assert common._tasks[new_task["key"]] is new_task
        assert not producers[1].terminated


def test_cache_ttl_and_manual_cleanup_protect_active_tasks(producers):
    viewer = create()
    directory = producers[0].directory
    old = time.time() - common._TTL - 1
    os.utime(directory, (old, old))
    common._purge_old()
    assert directory.exists()
    assert common.clean_stream_cache(common.CacheCleanBody(dry_run=False))["removed"] == 0
    session.hls_session_close(viewer["session_id"])
    os.utime(directory, (old, old))
    common._purge_old()
    assert not directory.exists()


def test_kind_namespace_and_version_deletion_are_isolated(producers):
    movie, episode = create(), create(kind="episode")
    assert Path(common._sessions[movie["session_id"]]["sdir"]).parent.name == "m1"
    assert Path(common._sessions[episode["session_id"]]["sdir"]).parent.name == "e1"
    assert common.drop_sessions_for_version(1) == 1
    assert producers[0].terminated and not producers[1].terminated
    extra = create(kind="extra")
    assert Path(common._sessions[extra["session_id"]]["sdir"]).parent.name == "x1"


def test_spawn_failure_returns_slot_and_removes_reservation(producers, monkeypatch):
    popen = session.subprocess.Popen

    def failure(*args, **kwargs):
        raise OSError("test spawn failure")

    monkeypatch.setattr(session.subprocess, "Popen", failure)
    with pytest.raises(HTTPException) as exc:
        create()
    assert exc.value.status_code == 500
    assert not common._tasks and not common._sessions
    monkeypatch.setattr(session.subprocess, "Popen", popen)
    create()
    create(start=15)
    assert len(producers) == 2


def test_initializing_task_is_shared_and_not_swept(producers, monkeypatch):
    entered, proceed = threading.Event(), threading.Event()
    start_task = session._start_task

    def slow_start(*args):
        entered.set()
        assert proceed.wait(3)
        return start_task(*args)

    monkeypatch.setattr(session, "_start_task", slow_start)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create)
        assert entered.wait(3)
        second = pool.submit(create)
        wait_for(lambda: len(common._sessions) == 2)
        assert len(common._tasks) == 1
        assert common._dead_sessions(time.time() + 1000) == []
        proceed.set()
        a, b = first.result(timeout=3), second.result(timeout=3)
    assert a["session_id"] != b["session_id"]
    assert len(producers) == 1


def test_initialization_failure_reaches_all_waiters_without_leaking_slot(producers, monkeypatch):
    entered, proceed = threading.Event(), threading.Event()

    def failed_start(*args):
        entered.set()
        assert proceed.wait(3)
        raise HTTPException(500, "test startup failure")

    monkeypatch.setattr(session, "_start_task", failed_start)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create)
        assert entered.wait(3)
        second = pool.submit(create)
        wait_for(lambda: len(common._sessions) == 2)
        proceed.set()
        for future in (first, second):
            with pytest.raises(HTTPException) as exc:
                future.result(timeout=3)
            assert exc.value.detail == "test startup failure"
    assert not common._tasks and not common._sessions
    assert session._hls_sem.acquire(blocking=False)
    assert session._hls_sem.acquire(blocking=False)
    assert not session._hls_sem.acquire(blocking=False)
    session._hls_sem.release()
    session._hls_sem.release()


def test_shutdown_during_initialization_cannot_spawn_late_process(producers, monkeypatch):
    entered, proceed = threading.Event(), threading.Event()
    start_task = session._start_task

    def slow_start(*args):
        entered.set()
        assert proceed.wait(3)
        return start_task(*args)

    monkeypatch.setattr(session, "_start_task", slow_start)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(create)
        assert entered.wait(3)
        common.shutdown_sessions()
        proceed.set()
        with pytest.raises(HTTPException) as exc:
            result.result(timeout=3)
        assert exc.value.status_code == 503
    assert not producers and not common._tasks and not common._sessions


def test_shutdown_stops_all_producers_and_invalidates_all_leases(producers):
    create()
    create()
    create(start=60)
    common.shutdown_sessions()
    assert all(p.terminated for p in producers)
    assert not common._tasks and not common._sessions
    assert session._hls_sem.acquire(blocking=False)
    assert session._hls_sem.acquire(blocking=False)
    assert not session._hls_sem.acquire(blocking=False)
    session._hls_sem.release()
    session._hls_sem.release()


def test_media_start_cache_distinguishes_copy_and_transcode(monkeypatch):
    common._MEDIA_START_CACHE.clear()
    monkeypatch.setattr(common._playback, "actual_media_start", lambda src, start, copy: 8 if copy else start)
    assert common._media_start_for(1, "test", 10, {"vcopy": True}) == 8
    assert common._media_start_for(1, "test", 10, {"vcopy": False}) == 10
    common._MEDIA_START_CACHE.clear()


def test_real_ffmpeg_shared_output_and_client_playlists(monkeypatch, tmp_path):
    """Exercise actual HLS files against synthetic media only, with no app DB rows."""
    from app import media

    ffmpeg = shutil.which("ffmpeg") or media._static_bins_if_present().get("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "synthetic.mkv"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "4", "-c:v", "libx264", "-threads", "1", "-c:a", "aac",
                    str(source)], check=True, timeout=30)
    info = media.probe(str(source))
    assert info["playable"]
    monkeypatch.setattr(common, "TRANSCODE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(session, "_version_source", lambda vid, kind: (
        {"id": vid, "kind": kind, "library_id": 1, "file_path": "synthetic.mkv"},
        SimpleNamespace(input=str(source), size=source.stat().st_size, mtime=source.stat().st_mtime)))
    monkeypatch.setattr(session, "_media_cached_or_probe", lambda *a: info)
    monkeypatch.setattr(session, "_media_payload", lambda *a: info)
    tasks = []
    try:
        a, b = create(), create()
        assert a["session_id"] != b["session_id"]
        sa, sb = [common._sessions[r["session_id"]] for r in (a, b)]
        tasks = [sa["task"]]
        assert sa["task"] is sb["task"]
        assert sa["task"]["done"].wait(10)
        assert sa["complete"] and sb["complete"]
        assert b"out_video.m3u8" in session.hls_session_playlist(b["session_id"]).body
        session.hls_session_close(a["session_id"])
        variant = session.hls_session_file(b["session_id"], "out_video.m3u8")
        assert b"#EXT-X-ENDLIST" in variant.body
        resumed = create(start=2)
        assert resumed["media_start"] == 0 and resumed["initial_time"] == 2
        assert resumed["complete"]
    finally:
        common.shutdown_sessions()
        for task in tasks:
            task["done"].wait(3)
