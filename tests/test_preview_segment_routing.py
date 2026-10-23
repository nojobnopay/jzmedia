"""困难 P5 采样只复用本页已验证入口，连续段缺失时回到原始采样位置。"""
from pathlib import Path

import pytest

from app import media
from app.routers.stream import preview_segment as segment
from app.routers.stream import previews as pv
from app.routers.stream.preview_log import ExtractionError, FrameLog
import test_preview_color as color_fixtures
import test_preview_segments as segment_fixtures


isolate = segment_fixtures.isolate
software_vulkan = color_fixtures.software_vulkan
worker = segment_fixtures.worker


def capture_tiles(extractor, monkeypatch):
    assembled = []

    def tile(size, _columns, _rows, output):
        assembled.append([(output.parent / f"frame_{index:02d}.png").read_bytes()
                          for index in range(size)])
        output.write_bytes(b"complete page")

    monkeypatch.setattr(extractor, "_tile_page", tile)
    return assembled


def point_output(cmd, target, *, point=250, before=30, fail_direct=()):
    if target in fail_direct:
        return FrameLog("")
    if target == point - before:
        Path(cmd[-1]).write_bytes(f"recovered-{point}".encode())
        return FrameLog("", frame_times=(float(before),), frame_numbers=(0,))
    if target > point:
        Path(cmd[-1]).write_bytes(f"point-{int(target)}".encode())
        return FrameLog("", frame_times=(0,), frame_numbers=(0,))
    return FrameLog("")


@pytest.mark.parametrize("before,size,segment_size", [(10, 10, 6), (30, 28, 25),
                                                      (60, 28, 23), (120, 28, 17)])
def test_recovery_reuses_the_exact_verified_entry_once_then_probes_again(
        tmp_path, monkeypatch, before, size, segment_size):
    seeks, segments, progress = [], [], []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        seeks.append(seek)
        return point_output(cmd, seek, before=before, fail_direct=(260,))

    def continuous(_extractor, targets, frames, *, start):
        segments.append((list(targets), start))
        for target, frame in zip(targets, frames):
            frame.write_bytes(f"segment-{int(target)}".encode())
        return tuple(target + 0.04 for target in targets)

    monkeypatch.setattr(segment, "extract_segment", continuous)
    extractor = worker(run)
    extractor.progress = progress.append
    assembled = capture_tiles(extractor, monkeypatch)
    extractor.page(250, size, size, 1, tmp_path / "page.jpg")
    expected_targets = list(range(260, 260 + 10 * segment_size, 10))
    assert segments == [(expected_targets, 250 - before)]
    recoveries = [250, 249.75, 248, *[250 - backoff for backoff in (10, 30, 60, 120)
                                    if backoff <= before]]
    remaining = list(range(260 + 10 * segment_size, 250 + 10 * size, 10))
    assert seeks == recoveries + [260] + remaining
    assert assembled == [[b"recovered-250",
                          *[f"segment-{target}".encode() for target in expected_targets],
                          *[f"point-{target}".encode() for target in remaining]]]
    assert progress == list(range(26, 26 + size))
    assert [sample["pts"] for sample in extractor.last_samples] == pytest.approx(
        [250, *[target + 0.04 for target in expected_targets], *remaining])
    assert extractor._dv_recovery_entry is None
    assert extractor._dv_hint_uses == 0
    assert extractor.last_stats == {"windows": 0, "individual_points": len(remaining) + 1, "segments": 1}


@pytest.mark.parametrize("prefix", [0, 2])
@pytest.mark.parametrize("direct_output", ["empty", "late"])
def test_partial_segment_keeps_verified_cells_and_recovers_first_missing_target(
        tmp_path, monkeypatch, prefix, direct_output):
    seeks, segments = [], []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        if Path(cmd[-1]).name == "sample_%02d.png":
            segments.append((seek, int(cmd[cmd.index("-frames:v") + 1])))
            return segment_fixtures.write_output(cmd, times=tuple(40 + index * 10 for index in range(prefix)))
        seeks.append(seek)
        if seek == 260 and direct_output == "late":
            Path(cmd[-1]).write_bytes(b"late direct output must not survive")
            return FrameLog("", frame_times=(30,), frame_numbers=(0,))
        if seek == 259.75:
            Path(cmd[-1]).write_bytes(b"point-260")
            return FrameLog("", frame_times=(0.25,), frame_numbers=(0,))
        return point_output(cmd, seek, fail_direct=(260,))

    extractor = worker(run)
    assembled = capture_tiles(extractor, monkeypatch)
    extractor.page(250, 6, 6, 1, tmp_path / "page.jpg")
    assert segments == [(220, 5)]
    remaining = list(range(260 + prefix * 10, 310, 10))
    recovery = [259.75, *remaining[1:]] if not prefix else remaining
    assert seeks == [250, 249.75, 248, 240, 220, 260, *recovery]
    assert seeks.count(260) == 1
    assert assembled == [[b"recovered-250", *[f"image-{index}".encode() for index in range(prefix)],
                          *[f"point-{target}".encode() for target in remaining]]]
    assert [sample["pts"] for sample in extractor.last_samples] == list(range(250, 310, 10))
    assert not list(tmp_path.glob("frame_*.png"))
    assert not list(tmp_path.glob(".dv-segment-*"))


@pytest.mark.parametrize("kind", ["cancel", "yield", "timeout"])
def test_interrupted_segment_cleans_the_page_and_retry_revalidates_its_entry(
        tmp_path, monkeypatch, kind):
    error = {"cancel": pv._Cancelled(), "yield": pv._YieldToPlayback(),
             "timeout": ExtractionError("超时", reason="timeout", timed_out=True)}[kind]
    interrupted = True
    seeks, segments, progress = [], [], []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        if Path(cmd[-1]).name == "sample_%02d.png":
            segments.append(seek)
            if interrupted:
                segment_fixtures.write_output(cmd, times=(40,))
                raise error
            return segment_fixtures.write_output(cmd, times=(40, 50))
        seeks.append(seek)
        return point_output(cmd, seek, fail_direct=(260,))

    extractor = worker(run)
    extractor.progress = progress.append
    assembled = capture_tiles(extractor, monkeypatch)
    output = tmp_path / "page.jpg"
    output.write_bytes(b"stale page")
    with pytest.raises(type(error)) as caught:
        extractor.page(250, 3, 3, 1, output)
    assert caught.value is error
    assert progress == [26]
    assert assembled == []
    assert not output.exists()
    assert not list(tmp_path.glob("frame_*.png"))
    assert not list(tmp_path.glob(".dv-segment-*"))
    assert extractor._dv_recovery_entry is None
    interrupted = False
    extractor.page(250, 3, 3, 1, output)
    assert segments == [220, 220]
    assert seeks.count(250) == 2
    assert seeks.count(220) == 2
    assert assembled == [[b"recovered-250", b"image-0", b"image-1"]]
    assert progress == [26, 26, 27, 28]
    assert extractor._dv_recovery_entry is None


def test_interval_preroll_bound_falls_back_without_an_unbounded_decoder(tmp_path, monkeypatch):
    before, interval, size = 120, 20, 3
    seeks = []

    def run(cmd, _jid, timeout=60):
        assert Path(cmd[-1]).name != "sample_%02d.png"
        seek = float(cmd[cmd.index("-ss") + 1])
        seeks.append(seek)
        return point_output(cmd, seek, before=before)

    extractor = worker(run, interval=interval)
    assembled = capture_tiles(extractor, monkeypatch)
    extractor.page(250, size, size, 1, tmp_path / "page.jpg")
    expected_targets = [250 + interval * index for index in range(1, size)]
    assert seeks[-len(expected_targets):] == expected_targets
    assert assembled == [[b"recovered-250", *[f"point-{target}".encode() for target in expected_targets]]]
    assert "segments" not in extractor.last_stats


@pytest.mark.parametrize("interval,count", [(20, 13), (60, 4)])
def test_segment_capacity_is_clipped_before_starting_long_interval_decode(
        tmp_path, monkeypatch, interval, count):
    segments, seeks = [], []
    first = 250 + interval

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        if Path(cmd[-1]).name == "sample_%02d.png":
            amount = int(cmd[cmd.index("-frames:v") + 1])
            duration = float(cmd[cmd.index("-t") + 1])
            segments.append((seek, amount, duration))
            assert duration <= 300
            assert 2 * duration <= timeout <= 600
            return segment_fixtures.write_output(
                cmd, times=tuple(first - seek + index * interval for index in range(amount)))
        seeks.append(seek)
        return point_output(cmd, seek, fail_direct=(first,))

    extractor = worker(run, interval=interval)
    capture_tiles(extractor, monkeypatch)
    extractor.page(250, 25, 5, 5, tmp_path / "page.jpg")
    assert segments == [(220, count, first + (count - 1) * interval + 2 - 220)]
    assert seeks.count(first) == 1
    assert extractor.last_stats == {"windows": 0, "individual_points": 25 - count, "segments": 1}


@pytest.mark.parametrize("before", [10, 30, 60, 120])
def test_an_easy_neighbour_bypasses_the_segment_after_an_isolated_slow_point(
        tmp_path, monkeypatch, before):
    seeks = []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        seeks.append(seek)
        return point_output(cmd, seek, before=before)

    monkeypatch.setattr(segment, "extract_segment",
                        lambda *_args, **_kwargs: pytest.fail("easy neighbour must keep its direct result"))
    extractor = worker(run)
    assembled = capture_tiles(extractor, monkeypatch)
    extractor.page(250, 3, 3, 1, tmp_path / "page.jpg")
    assert seeks[-2:] == [260, 270]
    assert assembled == [[b"recovered-250", b"point-260", b"point-270"]]
    assert extractor.last_stats == {"windows": 0, "individual_points": 3}
    assert extractor._dv_recovery_entry is None
    assert extractor._dv_hint_uses == 0


def test_failed_segment_can_establish_a_new_entry_for_the_next_target(tmp_path, monkeypatch):
    seeks, segments = [], []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        name = Path(cmd[-1]).name
        if name == "sample_%02d.png":
            segments.append(seek)
            if seek == 220:
                return FrameLog("")
            assert seek == 250
            return segment_fixtures.write_output(cmd, times=(20, 30, 40))
        seeks.append((name, seek))
        if (name, seek) in {("frame_00.png", 220), ("frame_01.png", 250)}:
            before = 30 if name == "frame_00.png" else 10
            Path(cmd[-1]).write_bytes(f"recovered-{int(seek + before)}".encode())
            return FrameLog("", frame_times=(before,), frame_numbers=(0,))
        return FrameLog("")

    extractor = worker(run)
    assembled = capture_tiles(extractor, monkeypatch)
    extractor.page(250, 5, 5, 1, tmp_path / "page.jpg")
    assert segments == [220, 250]
    assert seeks.count(("frame_01.png", 260)) == 1
    assert seeks.count(("frame_02.png", 270)) == 1
    assert assembled == [[b"recovered-250", b"recovered-260", b"image-0", b"image-1", b"image-2"]]
    assert [sample["pts"] for sample in extractor.last_samples] == [250, 260, 270, 280, 290]


def test_recovery_entry_does_not_survive_a_page_commit(tmp_path, monkeypatch):
    seeks = []

    def run(cmd, _jid, timeout=60):
        assert Path(cmd[-1]).name != "sample_%02d.png"
        seek = float(cmd[cmd.index("-ss") + 1])
        seeks.append(seek)
        return point_output(cmd, seek, before=120)

    extractor = worker(run)
    assembled = capture_tiles(extractor, monkeypatch)
    extractor.page(250, 1, 1, 1, tmp_path / "page-0.jpg")
    assert extractor._dv_recovery_entry is None
    extractor.page(260, 3, 3, 1, tmp_path / "page-1.jpg")
    assert seeks[-3:] == [260, 270, 280]
    assert assembled == [[b"recovered-250"], [b"point-260", b"point-270", b"point-280"]]


def test_normal_p5_points_never_start_a_segment(tmp_path, monkeypatch):
    seeks = []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        seeks.append(seek)
        Path(cmd[-1]).write_bytes(f"point-{int(seek)}".encode())
        return FrameLog("", frame_times=(0,), frame_numbers=(0,))

    monkeypatch.setattr(segment, "extract_segment",
                        lambda *_args, **_kwargs: pytest.fail("ordinary P5 point started a segment"))
    extractor = worker(run)
    capture_tiles(extractor, monkeypatch)
    extractor.page(250, 3, 3, 1, tmp_path / "page.jpg")
    assert seeks == [250, 260, 270]
    assert extractor.last_stats == {"windows": 0, "individual_points": 3}


@pytest.mark.parametrize("guarded,remote,explicit,expected", [
    (False, False, "auto", "window_keyframes"),
    (False, True, "auto", "accurate"),
    (True, False, "accurate", "accurate"),
    (True, True, "accurate", "accurate"),
])
def test_other_extraction_modes_keep_their_existing_page_route(
        tmp_path, monkeypatch, guarded, remote, explicit, expected):
    monkeypatch.setenv("PREVIEW_MODE", explicit)
    extractor = worker(lambda *_args, **_kwargs: pytest.fail("unexpected decoder"))
    # The route is selected at construction; create the same source with the
    # selected remote/guard attributes instead of manually forcing its mode.
    extractor = type(extractor)(
        extractor.src, extractor.filter_arg, 10, remote=remote,
        run=extractor.run, jid=extractor.jid, progress=lambda _n: None, software=True,
        frame_guard=extractor.frame_guard if guarded else "")
    routes = []
    monkeypatch.setattr(segment, "extract_segment",
                        lambda *_args, **_kwargs: pytest.fail("other mode started a P5 segment"))
    monkeypatch.setattr(extractor, "_keyframe_page", lambda *_args: routes.append("window_keyframes"))
    monkeypatch.setattr(extractor, "_accurate_page", lambda *_args: routes.append("accurate"))
    assert extractor.mode == expected
    extractor.page(250, 3, 3, 1, tmp_path / "page.jpg")
    assert routes == [expected]


def test_real_long_recovery_seeds_correct_adjacent_segment_pixels(
        software_vulkan, require_dovi_metadata, tmp_path, monkeypatch):
    ffmpeg, env = software_vulkan
    require_dovi_metadata(ffmpeg, env)
    colors = [(100, 0, 0)] * 142
    colors[100], colors[110], colors[120] = (0, 100, 0), (0, 0, 100), (50, 50, 50)
    reference = color_fixtures._p5_reference_rpu()
    rpus = [color_fixtures._P5_RPU[4:] if frame in (0, 140) else reference
            for frame in range(len(colors))]
    video = color_fixtures._write_synthetic_p5(ffmpeg, env, tmp_path, colors, rpus)
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: ffmpeg)
    calls, captured = [], []

    def run(cmd, jid, timeout=60):
        log = pv._run(cmd, jid, timeout)
        calls.append((cmd, log))
        return log

    extractor = worker(run, video=str(video))

    def tile(size, _columns, _rows, output):
        captured.extend(segment_fixtures.rgb_pixels(ffmpeg, env, output.parent / f"frame_{index:02d}.png")
                        for index in range(size))
        output.write_bytes(b"complete page")

    monkeypatch.setattr(extractor, "_tile_page", tile)
    extractor.page(100, 3, 3, 1, tmp_path / "page.jpg")
    assert [float(cmd[cmd.index("-ss") + 1]) for cmd, _log in calls] == [
        100, 99.75, 98, 90, 70, 40, 0, 110, 0]
    segment_cmd, segment_log = calls[-1]
    assert Path(segment_cmd[-1]).name == "sample_%02d.png"
    assert segment_log.frame_times == pytest.approx((110, 120), abs=0.001)
    assert [sample["pts"] for sample in extractor.last_samples] == pytest.approx((100, 110, 120), abs=0.001)
    for index, target in enumerate((100, 110, 120)):
        independent = tmp_path / f"independent-{target}.png"
        independent_worker = worker(pv._run, video=str(video))
        cmd = independent_worker._input(0, duration=target + 0.5, frame_log=True)
        vf = (f"{independent_worker.frame_guard}select='between(t,{target},{target + 0.5})',"
              f"{independent_worker.filter_arg},showinfo=checksum=0")
        cmd += ["-vf", vf, "-frames:v", "1", "-fps_mode", "passthrough", "-threads", "1",
                "-c:v", "png", "-pix_fmt", "rgb24", "-compression_level", "1", str(independent)]
        log = pv._run(cmd, independent_worker.jid, timeout=30)
        assert log.frame_times[0] == pytest.approx(target)
        assert captured[index] == segment_fixtures.rgb_pixels(ffmpeg, env, independent)
    assert extractor.last_stats == {"windows": 0, "individual_points": 1, "segments": 1}
    assert not list(tmp_path.glob("frame_*.png"))
    assert not list(tmp_path.glob(".dv-segment-*"))
