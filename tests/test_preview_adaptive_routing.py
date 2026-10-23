"""Cost observations may reorder recovery, never bypass sample verification."""
from pathlib import Path

import pytest

from app import media
from app.routers.stream import preview_extract as ex
from app.routers.stream import previews as pv
from app.routers.stream.preview_log import ExtractionError, FrameLog
import test_preview_extraction as extraction_fixtures
import test_preview_segment_routing as routing_fixtures
import test_preview_segments as segment_fixtures


isolate = segment_fixtures.isolate
ffmpeg = extraction_fixtures.ffmpeg


def output(cmd, pts=0, *, elapsed=1):
    Path(cmd[-1]).write_bytes(b"verified sample")
    return FrameLog("", frame_times=(pts,), frame_numbers=(0,), elapsed=elapsed)


@pytest.mark.parametrize("pts", [0, 2.5, 9.875, 10])
def test_shorter_direct_window_preserves_all_accepted_offsets(tmp_path, pts):
    commands = []

    def run(cmd, _jid, timeout=60):
        commands.append(cmd)
        return output(cmd, pts)

    worker = segment_fixtures.worker(run)
    frame = tmp_path / "frame.png"
    assert worker._sample_point(250, frame, nearby=False) == "accurate"
    assert len(commands) == 1
    assert commands[0][commands[0].index("-t") + 1] == "12.0"
    assert worker._point_pts == 250 + pts


def test_explicit_accurate_mode_keeps_its_existing_direct_window(tmp_path, monkeypatch):
    monkeypatch.setenv("PREVIEW_MODE", "accurate")
    commands = []

    def run(cmd, _jid, timeout=60):
        commands.append(cmd)
        return output(cmd, 9.875)

    worker = segment_fixtures.worker(run)
    worker._sample_point(250, tmp_path / "frame.png", nearby=False)
    assert commands[0][commands[0].index("-t") + 1] == "20.0"


def test_one_direct_success_discards_an_expensive_recovery_hint(tmp_path, monkeypatch):
    calls = []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        cell = int(Path(cmd[-1]).stem.rsplit("_", 1)[1])
        target = 250 + cell * 10
        calls.append((target, seek))
        if target == 250 and seek == 190:
            return output(cmd, 60, elapsed=26)
        if target == 270 and seek == 260:
            return output(cmd, 10, elapsed=5)
        if target not in (250, 270):
            return output(cmd, 0)
        return FrameLog("", elapsed=2)

    worker = segment_fixtures.worker(run)
    routing_fixtures.capture_tiles(worker, monkeypatch)
    worker.page(250, 4, 4, 1, tmp_path / "page.jpg")
    attempts = [seek for target, seek in calls if target == 270]
    assert attempts == [270, 269.75, 268, 260]
    assert [sample["pts"] for sample in worker.last_samples] == [250, 260, 270, 280]
    assert worker._dv_policy is None
    assert worker._dv_recovery_entry is None


@pytest.mark.parametrize("successful_before,invalid_preferred,expected", [
    (10, False, [250]),
    (60, False, [250, 200]),
    (30, False, [250, 200, 230]),
    (30, True, [250, 200, 230]),
    (120, False, [250, 200, 230, 140]),
])
def test_long_hint_keeps_the_cheap_probe_and_verified_fallbacks(
        tmp_path, successful_before, invalid_preferred, expected):
    from app.routers.stream.preview_adaptive import RecoveryPolicy
    calls = []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        target = 250 if Path(cmd[-1]).name == "first.png" else 260
        calls.append((target, seek))
        if target == 250 and seek == 190:
            return output(cmd, 60, elapsed=26)
        if target == 260 and seek == 260 - successful_before:
            return output(cmd, successful_before, elapsed=5)
        if target == 260 and seek == 200 and invalid_preferred:
            return output(cmd, 64, elapsed=26)
        return FrameLog("", elapsed=2)

    worker = segment_fixtures.worker(run)
    worker._dv_policy = RecoveryPolicy(10)
    worker._sample_point(250, tmp_path / "first.png", nearby=False)
    assert worker._point_pts == 250
    worker._sample_point(260, tmp_path / "next.png", nearby=False)
    assert [seek for target, seek in calls if target == 260] == [260, 259.75, 258, *expected]
    assert worker._point_pts == 260


def test_repeated_costly_recovery_can_expand_the_next_short_batch(tmp_path, monkeypatch):
    batches = []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        if Path(cmd[-1]).name == "sample_%02d.png":
            count = int(cmd[cmd.index("-frames:v") + 1])
            batches.append((seek, count))
            times = tuple(20 + index * 10 for index in range(count))
            log = segment_fixtures.write_output(cmd, times=times)
            return FrameLog(log, frame_times=log.frame_times, frame_numbers=log.frame_numbers,
                            elapsed=2 * count)
        cell = int(Path(cmd[-1]).stem.rsplit("_", 1)[1])
        target = 250 + cell * 10
        if target in (250, 320) and seek == target - 10:
            return output(cmd, 10, elapsed=2)
        if target in (250, 260, 320, 330):
            return FrameLog("", elapsed=2 if seek == target else 1)
        return output(cmd)

    worker = segment_fixtures.worker(run)
    routing_fixtures.capture_tiles(worker, monkeypatch)
    worker.page(250, 25, 5, 5, tmp_path / "page.jpg")
    assert batches == [(240, 6), (310, 12)]
    assert [sample["pts"] for sample in worker.last_samples] == list(range(250, 500, 10))
    assert worker._dv_policy is None


def test_decoder_fallback_cannot_reuse_an_entry_verified_by_the_old_decoder(tmp_path, monkeypatch):
    calls = []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        hardware = "-hwaccel" in cmd
        cell = int(Path(cmd[-1]).stem.rsplit("_", 1)[1])
        calls.append((cell, seek, hardware))
        if cell == 0 and seek == 220:
            return output(cmd, 30, elapsed=8)
        if cell == 1 and hardware:
            raise ExtractionError("device unavailable", reason="exit", error_kind="hardware")
        if cell == 1 and seek == 250:
            return output(cmd, 10, elapsed=3)
        return FrameLog("", elapsed=1)

    from app.routers.stream import preview_segment
    monkeypatch.setattr(preview_segment, "extract_segment",
                        lambda *_args, **_kwargs: pytest.fail("old decoder entry must be discarded"))
    worker = segment_fixtures.worker(run)
    worker.hw = ["-hwaccel", "test"]
    sample_point = worker._sample_point

    def first_guarded_point(target, frame, **kwargs):
        if target == 250:
            assert worker._recover_guarded_point(target, frame)
            return "accurate"
        return sample_point(target, frame, **kwargs)

    monkeypatch.setattr(worker, "_sample_point", first_guarded_point)
    routing_fixtures.capture_tiles(worker, monkeypatch)
    worker.page(250, 2, 2, 1, tmp_path / "page.jpg")
    assert [(seek, hardware) for cell, seek, hardware in calls if cell == 1] == [
        (260, True), (260, False), (259.75, False), (258, False), (250, False)]
    assert worker.hw == []
    assert worker._dv_policy is None
    assert worker._dv_recovery_entry is None


@pytest.mark.parametrize("threshold", [9.5, 10])
def test_real_decode_retains_late_valid_frames_with_shorter_input(
        ffmpeg, tmp_path, monkeypatch, threshold):
    video = tmp_path / "colors.mkv"
    extraction_fixtures.colors(ffmpeg, video, gop=100)
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: ffmpeg)
    jid = pv._jobs.create()["job_id"]
    worker = ex.Extractor(extraction_fixtures.source(video), pv._frame_filter({}), 10,
                          remote=True, run=pv._run, jid=jid, progress=lambda _n: None,
                          frame_guard=f"select='gte(t,{threshold})'", software=True)
    frame = tmp_path / "frame.png"
    assert worker._sample_point(0, frame, nearby=False) == "accurate"
    assert worker._point_pts == pytest.approx(threshold)
    assert frame.is_file() and frame.stat().st_size > 0
