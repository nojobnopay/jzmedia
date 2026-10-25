"""Isolated audio preparation, immutable publication and independent cast leases."""
import io
import json
from pathlib import Path
import array
import shutil
import subprocess
from types import SimpleNamespace
import threading
import time

import pytest
from fastapi import HTTPException

from app.casting import audio
from app.routers.stream import common


INFO = {"vcodec": "h264", "duration": 10, "audio": [{"index": 0}, {"index": 1}]}
ROW = {"id": 913, "kind": "movie", "library_id": 1, "file_path": "synthetic.mkv"}


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "Isolated audio worker did not reach expected state"


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    manager = audio.AudioOutputs()
    monkeypatch.setattr(audio, "outputs", manager)
    monkeypatch.setattr(common, "TRANSCODE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "_hls_sem", threading.BoundedSemaphore(1))
    monkeypatch.setattr(common, "_cache_cap_bytes", lambda: 10_000_000)
    monkeypatch.setattr(common, "_purge_old", lambda: None)
    monkeypatch.setattr(audio.media, "ffmpeg_bin", lambda: "fixture-ffmpeg")
    monkeypatch.setattr(audio, "validate_output", lambda path, _info: {"duration": 10, "bytes": path.stat().st_size})
    created = []

    class Process:
        pid = 999999999
        def __init__(self, cmd, **kwargs):
            self.finish = threading.Event()
            self.returncode = None
            self.stdout = io.StringIO("out_time_us=1000000\n")
            self.killed = False
            self.path = Path(cmd[-1])
            self.path.write_bytes(b"completed synthetic file" * 4)
            created.append(self)
        def poll(self):
            if self.finish.is_set() and self.returncode is None:
                self.returncode = 0
            return self.returncode
        def terminate(self):
            self.killed = True
            self.returncode = -15
            self.finish.set()
        def kill(self):
            self.terminate()
        def wait(self, timeout=None):
            self.finish.wait(timeout)
            return self.poll()

    monkeypatch.setattr(audio.subprocess, "Popen", Process)
    source = SimpleNamespace(input="not-a-real-source", size=100, mtime=1)
    yield SimpleNamespace(manager=manager, source=source, processes=created, directory=tmp_path)
    manager.shutdown()


def test_command_keeps_video_and_selects_exact_audio_without_subtitles(tmp_path, monkeypatch):
    monkeypatch.setattr(audio.media, "ffmpeg_bin", lambda: "ffmpeg")
    command = audio.build_command("source.mkv", 1, tmp_path / "out.mp4", "hevc")
    assert command[command.index("-c:v") + 1] == "copy"
    assert [command[i + 1] for i, value in enumerate(command) if value == "-map"] == ["0:V:0", "0:a:1"]
    assert command[command.index("-c:a") + 1] == "aac" and command[command.index("-ac") + 1] == "2"
    assert "-sn" in command and "-dn" in command and "+faststart" in command
    assert command[command.index("-map_chapters") + 1] == "-1"
    assert command[command.index("-tag:v") + 1] == "hvc1"
    assert not any(value in command for value in ("-vf", "-filter_complex", "libx264"))


@pytest.mark.parametrize("index", [-1, 2, True, "1"])
def test_invalid_track_is_rejected_before_any_work(index):
    with pytest.raises(HTTPException, match="所选音轨"):
        audio.validate_selection(INFO, index)


@pytest.mark.parametrize("changes", [{"vcodec": "vp9"}, {"dv_profile": 5}, {"hdr": "HDR10"}, {"hdr10plus": 1}])
def test_unsupported_video_routes_to_tv_app_without_video_transcoding(changes):
    with pytest.raises(HTTPException, match="jzmedia-tv"):
        audio.validate_selection({**INFO, **changes}, 1)


def test_no_partial_file_is_exposed_and_two_casts_share_one_output(outputs):
    manager = outputs.manager
    first = manager.prepare(ROW, outputs.source, INFO, 1, lambda: True)
    wait_for(lambda: len(outputs.processes) == 1)
    second = manager.prepare(ROW, outputs.source, INFO, 1, lambda: True)
    assert len(outputs.processes) == 1
    before = manager.snapshot(first)
    assert before["state"] == "preparing" and before["path"] is None
    assert not list(outputs.directory.rglob("media.mp4"))
    protected = common._protected_dirs(time.time())
    assert outputs.processes[0].path.parent.as_posix() in protected
    manager.release(first)
    assert not outputs.processes[0].killed
    outputs.processes[0].finish.set()
    wait_for(lambda: manager.snapshot(second)["state"] == "ready")
    ready = manager.snapshot(second)
    assert ready["progress"] == 1 and Path(ready["path"]).read_bytes()
    assert not outputs.processes[0].path.exists()
    assert Path(ready["path"]).stat().st_mode & 0o777 == 0o600
    manager.release(second)
    wait_for(lambda: not manager.jobs)
    third = manager.prepare(ROW, outputs.source, INFO, 1, lambda: True)
    assert manager.snapshot(third)["state"] == "ready" and len(outputs.processes) == 1
    manager.release(third)


def test_changed_source_and_audio_do_not_reuse_completed_output(outputs):
    manager = outputs.manager
    first = manager.prepare(ROW, outputs.source, INFO, 0, lambda: True)
    wait_for(lambda: len(outputs.processes) == 1)
    outputs.processes[0].finish.set()
    wait_for(lambda: manager.snapshot(first)["state"] == "ready")
    manager.release(first)
    other_track = manager.prepare(ROW, outputs.source, INFO, 1, lambda: True)
    wait_for(lambda: len(outputs.processes) == 2)
    assert manager.snapshot(other_track)["path"] is None
    manager.release(other_track)
    wait_for(lambda: outputs.processes[1].killed)
    changed = SimpleNamespace(input="not-a-real-source", size=101, mtime=2)
    newer = manager.prepare(ROW, changed, INFO, 0, lambda: True)
    wait_for(lambda: len(outputs.processes) == 3)
    assert manager.snapshot(newer)["path"] is None
    manager.release(newer)


@pytest.mark.parametrize("corruption", [[], {"duration": 0}])
def test_invalid_completion_marker_is_rebuilt_instead_of_published(outputs, corruption):
    manager = outputs.manager
    first = manager.prepare(ROW, outputs.source, INFO, 1, lambda: True)
    wait_for(lambda: len(outputs.processes) == 1)
    outputs.processes[0].finish.set()
    wait_for(lambda: manager.snapshot(first)["state"] == "ready")
    path = Path(manager.snapshot(first)["path"])
    manager.release(first)
    wait_for(lambda: not manager.jobs)
    marker = path.parent / "cast-audio.json"
    saved = json.loads(marker.read_text())
    marker.write_text(json.dumps({**saved, **corruption} if isinstance(corruption, dict) else corruption))
    second = manager.prepare(ROW, outputs.source, INFO, 1, lambda: True)
    wait_for(lambda: len(outputs.processes) == 2)
    assert manager.snapshot(second)["path"] is None
    manager.release(second)


def test_expired_authorization_cancels_job_and_releases_process_slot(outputs):
    alive = [True]
    lease = outputs.manager.prepare(ROW, outputs.source, INFO, 1, lambda: alive[0])
    wait_for(lambda: len(outputs.processes) == 1)
    alive[0] = False
    wait_for(lambda: outputs.processes[0].killed)
    wait_for(lambda: outputs.manager.snapshot(lease)["state"] == "cancelled")
    wait_for(lambda: common._hls_sem.acquire(blocking=False))
    common._hls_sem.release()
    assert not list(outputs.directory.rglob("media.mp4"))


def test_cancelled_waiter_never_starts_ffmpeg(outputs):
    common._hls_sem.acquire()
    lease = outputs.manager.prepare(ROW, outputs.source, INFO, 1, lambda: True)
    assert outputs.manager.snapshot(lease)["state"] == "queued"
    outputs.manager.release(lease)
    common._hls_sem.release()
    wait_for(lambda: not outputs.manager.jobs)
    assert not outputs.processes


def test_budget_overrun_does_not_publish_a_partial_output(outputs, monkeypatch):
    monkeypatch.setattr(common, "_cache_cap_bytes", lambda: 10)
    lease = outputs.manager.prepare(ROW, outputs.source, INFO, 1, lambda: True)
    wait_for(lambda: outputs.manager.snapshot(lease)["state"] == "error")
    assert outputs.processes[0].killed
    assert not list(outputs.directory.rglob("media.mp4"))
    assert "not-a-real-source" not in outputs.manager.snapshot(lease)["error"]


def test_complete_validation_checks_codec_tracks_and_whole_duration(tmp_path, monkeypatch):
    path = tmp_path / "output.mp4"
    path.write_bytes(b"synthetic")
    monkeypatch.setattr(audio.media, "ffprobe_bin", lambda: "ffprobe")
    data = {"format": {"duration": "10.05"}, "streams": [
        {"codec_type": "video", "codec_name": "h264"},
        {"codec_type": "audio", "codec_name": "aac", "channels": 2}]}
    monkeypatch.setattr(audio.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(data)))
    assert audio.validate_output(path, INFO)["duration"] == 10.05
    data["streams"].append({"codec_type": "subtitle", "codec_name": "mov_text"})
    with pytest.raises(RuntimeError):
        audio.validate_output(path, INFO)
    data["streams"].pop()
    data["format"]["duration"] = "1"
    with pytest.raises(RuntimeError):
        audio.validate_output(path, INFO)


def test_real_complete_file_contains_selected_tone_and_copied_video(tmp_path, monkeypatch):
    present = audio.media._static_bins_if_present()
    ffmpeg = shutil.which("ffmpeg") or present.get("ffmpeg")
    ffprobe = shutil.which("ffprobe") or present.get("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("Existing FFmpeg binaries are required; this test never downloads them")
    monkeypatch.setattr(audio.media, "ffmpeg_bin", lambda: ffmpeg)
    monkeypatch.setattr(audio.media, "ffprobe_bin", lambda: ffprobe)
    source = tmp_path / "two-tones.mkv"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264",
        "-preset", "ultrafast", "-threads", "1", "-c:a", "aac", str(source)],
        capture_output=True, check=True, timeout=30)
    info = {**INFO, "duration": 2}
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(common, "TRANSCODE_DIR", str(cache))
    monkeypatch.setattr(common, "_hls_sem", threading.BoundedSemaphore(1))
    monkeypatch.setattr(common, "_cache_cap_bytes", lambda: 10_000_000)
    monkeypatch.setattr(common, "_purge_old", lambda: None)
    manager = audio.AudioOutputs()
    monkeypatch.setattr(audio, "outputs", manager)
    stat = source.stat()
    try:
        lease = manager.prepare(ROW, SimpleNamespace(input=str(source), size=stat.st_size,
            mtime=stat.st_mtime), info, 1, lambda: True)
        wait_for(lambda: manager.snapshot(lease)["state"] in ("ready", "error"), timeout=15)
        result = manager.snapshot(lease)
        assert result["state"] == "ready", result
        destination = Path(result["path"])
        assert audio.validate_output(destination, info)["duration"] >= 2
        assert destination.read_bytes().find(b"moov") < destination.read_bytes().find(b"mdat")
        # Stream-copy preserves decoded video exactly, independently of container.
        def video_digest(path):
            return subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:v:0",
                "-f", "hash", "-hash", "sha256", "-"], capture_output=True, check=True,
                timeout=15).stdout
        assert video_digest(source) == video_digest(destination)
        decoded = subprocess.run([ffmpeg, "-v", "error", "-i", str(destination),
            "-map", "0:a:0", "-ar", "8000", "-ac", "1", "-f", "f32le", "-"],
            capture_output=True, check=True, timeout=15).stdout
        samples = array.array("f")
        samples.frombytes(decoded)
        stable = samples[4000:12000]
        crossings = sum(a <= 0 < b for a, b in zip(stable, stable[1:]))
        assert 860 <= crossings <= 900, "The output must carry the selected 880 Hz audio, not 440 Hz"
    finally:
        manager.shutdown()
