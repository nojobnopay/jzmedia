"""有界 P5 连续解码：图片与时间严格配对，失败时保留逐点恢复入口。"""
from pathlib import Path
import subprocess
from threading import Event, RLock
from types import SimpleNamespace

import pytest

from app import media
from app.jobkit import JobRegistry
from app.routers.stream import preview_extract as ex
from app.routers.stream import previews as pv
from app.routers.stream.preview_color import color_plan
from app.routers.stream.preview_log import ExtractionError, FrameLog
import test_preview_color as color_fixtures


software_vulkan = color_fixtures.software_vulkan
synthetic_p5_references_24fps = color_fixtures.synthetic_p5_references_24fps


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setenv("TRANSCODER", "sw")
    monkeypatch.setenv("PREVIEW_THREADS", "2")
    monkeypatch.delenv("PREVIEW_MODE", raising=False)
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: "/isolated/ffmpeg")
    monkeypatch.setattr(pv, "_jobs", JobRegistry(prefix="dv-window-test"))
    monkeypatch.setattr(pv, "_sessions", {})
    monkeypatch.setattr(pv, "_sess_lock", RLock())
    monkeypatch.setattr(pv, "_stop", Event())


def worker(run, *, interval=10, video="/isolated/generated-p5.mp4"):
    conversion = color_plan({"dv_profile": 5, "dv_bl_compat": 0}, 64, 64)
    return ex.Extractor(
        SimpleNamespace(input=video), conversion["filter"], interval,
        remote=True, run=run, jid=pv._jobs.create(library_id=1)["job_id"],
        progress=lambda _n: None, input_args=conversion["input_args"],
        software=True, frame_guard=conversion["frame_guard"])


def write_window(cmd, *, times=(10.0, 20.0), numbers=None, images=None):
    """每张候选图写不同字节，防止只排序时间戳而把图像配错。"""
    pattern = cmd[-1]
    assert Path(pattern).name == "sample_%02d.png"
    if images is None:
        images = {index: f"image-{index}".encode() for index in range(len(times))}
    for index, content in images.items():
        Path(pattern % index).write_bytes(content)
    if numbers is None:
        numbers = tuple(range(len(times)))
    return FrameLog("", frame_times=times, frame_numbers=numbers)


def assert_clean(tmp_path, frames):
    assert not any(frame.exists() for frame in frames)
    assert not list(tmp_path.glob(".dv-window-*"))
    assert not list(tmp_path.rglob("sample_*.png"))


@pytest.mark.parametrize("targets,interval,preroll,times,start,duration", [
    ([250, 260], 10, 10, (10.0, 20.0), 240, 22),
    ([250, 260], 10, 30, (30.0, 40.0), 220, 42),
    ([250], 10, 30, (30.0,), 220, 32),
    ([1, 1.5], 0.5, 10, (1.0, 1.5), 0, 2),
])
def test_guarded_window_decodes_once_and_only_publishes_verified_pairs(
        tmp_path, targets, interval, preroll, times, start, duration):
    calls = []

    def run(cmd, _jid, timeout=60):
        calls.append((cmd, timeout))
        return write_window(cmd, times=times)

    frames = [tmp_path / f"frame_{index:02d}.png" for index in range(len(targets))]
    for frame in frames:
        frame.write_bytes(b"stale image")
    extractor = worker(run, interval=interval)
    assert extractor._guarded_window(targets, frames, preroll=preroll) is True
    assert [frame.read_bytes() for frame in frames] == [
        f"image-{index}".encode() for index in range(len(targets))]
    assert not list(tmp_path.glob(".dv-window-*"))
    assert len(calls) == 1
    cmd, timeout = calls[0]
    assert float(cmd[cmd.index("-ss") + 1]) == start
    assert float(cmd[cmd.index("-t") + 1]) == duration <= 42
    assert timeout == (90 if len(targets) == 2 and duration > 32 else 60)
    assert "-skip_frame" not in cmd and "-noaccurate_seek" not in cmd
    assert int(cmd[cmd.index("-frames:v") + 1]) == len(targets)
    assert cmd[cmd.index("-fps_mode") + 1] == "passthrough"
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.count("sidedata=mode=select:type=DOVI_METADATA") == 1
    assert (vf.index("DOVI_METADATA") < vf.index("select=")
            < vf.index("libplacebo=") < vf.index("showinfo=")
            < vf.index("settb=") < vf.index("setpts="))


@pytest.mark.parametrize("times,numbers,images", [
    ((), (), {}),
    ((10.0,), (0,), {0: b"first"}),
    ((10.0, 20.0, 21.0), (0, 1, 2), None),
    ((9.99, 20.0), None, None),
    ((12.01, 20.0), None, None),
    ((10.0, 19.99), None, None),
    ((10.0, 22.01), None, None),
    ((20.0, 10.0), None, None),
    ((10.0, 10.0), None, None),
    ((float("nan"), 20.0), None, None),
    ((10.0, float("inf")), None, None),
    ((10.0, 20.0), (), None),
    ((10.0, 20.0), (1, 2), None),
    ((10.0, 20.0), (0, 2), None),
    ((10.0, 20.0), (0, 0), None),
    ((10.0, 20.0), (0,), None),
    ((10.0, 20.0), None, {0: b"first"}),
    ((10.0, 20.0), None, {0: b"first", 1: b""}),
    ((10.0, 20.0), None, {0: b"first", 2: b"second"}),
    ((10.0, 20.0), None, {0: b"first", 1: b"second", 2: b"extra"}),
])
def test_unreliable_window_never_publishes_partial_or_mismatched_images(
        tmp_path, times, numbers, images):
    calls = []

    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        return write_window(cmd, times=times, numbers=numbers, images=images)

    frames = [tmp_path / "frame_00.png", tmp_path / "frame_01.png"]
    for frame in frames:
        frame.write_bytes(b"stale image")
    assert worker(run)._guarded_window([250, 260], frames, preroll=10) is False
    assert len(calls) == 1
    assert_clean(tmp_path, frames)


@pytest.mark.parametrize("targets,preroll", [
    ([250, 270], 30),  # Duration would be 52 seconds, exceeding the 42-second cap.
    ([250, 260, 270], 10),
    ([], 10),
    ([250, 250], 10),
    ([260, 250], 10),
    ([-1], 10),
    ([float("nan")], 10),
    ([float("inf")], 10),
    ([250], 5),
    ([250], 60),  # The expensive single-point recovery is never a shared window.
])
def test_out_of_bounds_window_does_not_start_a_decoder(tmp_path, targets, preroll):
    def run(*_args, **_kwargs):
        pytest.fail("invalid window must not start FFmpeg")

    frames = [tmp_path / f"frame_{index:02d}.png" for index in range(len(targets))]
    for frame in frames:
        frame.write_bytes(b"stale image")
    assert worker(run)._guarded_window(targets, frames, preroll=preroll) is False
    assert_clean(tmp_path, frames)


@pytest.mark.parametrize("failure", ["runtime", "decoder"])
def test_failed_window_returns_control_for_point_fallback_and_removes_output(tmp_path, failure):
    calls = []

    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        write_window(cmd, times=(10.0,))
        if failure == "decoder":
            raise ExtractionError("无法解码", reason="exit", error_kind="decoder")
        raise RuntimeError("private source URL must not become a thumbnail")

    frames = [tmp_path / "frame_00.png", tmp_path / "frame_01.png"]
    assert worker(run)._guarded_window([250, 260], frames, preroll=10) is False
    assert len(calls) == 1
    assert_clean(tmp_path, frames)


@pytest.mark.parametrize("interruption", ["cancel", "playback", "timeout"])
def test_window_interruption_propagates_without_retry_or_partial_publication(tmp_path, interruption):
    error = {"cancel": pv._Cancelled(), "playback": pv._YieldToPlayback(),
             "timeout": ExtractionError("超时", reason="timeout", timed_out=True)}[interruption]
    calls = []

    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        write_window(cmd, times=(10.0,))
        raise error

    frames = [tmp_path / "frame_00.png", tmp_path / "frame_01.png"]
    for frame in frames:
        frame.write_bytes(b"stale image")
    with pytest.raises(type(error)) as caught:
        worker(run)._guarded_window([250, 260], frames, preroll=10)
    assert caught.value is error
    assert len(calls) == 1
    assert_clean(tmp_path, frames)


@pytest.mark.parametrize("error_kind", ["memory", "permission", "option", "filter", "hardware", "input"])
def test_resource_and_configuration_errors_propagate_without_extra_decoding(tmp_path, error_kind):
    error = ExtractionError("运行条件不满足", reason="exit", error_kind=error_kind)
    calls = []

    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        write_window(cmd, times=(10.0,))
        raise error

    frames = [tmp_path / "frame_00.png", tmp_path / "frame_01.png"]
    with pytest.raises(ExtractionError) as caught:
        worker(run)._guarded_window([250, 260], frames, preroll=10)
    assert caught.value is error
    assert len(calls) == 1
    assert_clean(tmp_path, frames)


def test_publish_io_error_removes_both_partial_destinations(tmp_path, monkeypatch):
    real_copy = ex.shutil.copyfile
    copied = []

    def copy_file(source, destination):
        copied.append(destination)
        if len(copied) == 2:
            destination.write_bytes(b"partial second image")
            raise OSError("isolated output is full")
        return real_copy(source, destination)

    def run(cmd, _jid, timeout=60):
        return write_window(cmd)

    monkeypatch.setattr(ex.shutil, "copyfile", copy_file)
    frames = [tmp_path / "frame_00.png", tmp_path / "frame_01.png"]
    with pytest.raises(OSError, match="isolated output is full"):
        worker(run)._guarded_window([250, 260], frames, preroll=10)
    assert copied == frames
    assert_clean(tmp_path, frames)


def stub_tile(extractor, monkeypatch, assembled):
    def tile(size, _columns, _rows, output):
        assembled.append([(output.parent / f"frame_{index:02d}.png").read_bytes()
                          for index in range(size)])
        output.write_bytes(b"complete page")

    monkeypatch.setattr(extractor, "_tile_page", tile)


def test_normal_p5_points_do_not_pay_for_preroll_or_windows(tmp_path, monkeypatch):
    calls, assembled = [], []

    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(f"point-{cmd[cmd.index('-ss') + 1]}".encode())
        return FrameLog("", frame_times=(0,), frame_numbers=(0,))

    extractor = worker(run)
    monkeypatch.setattr(extractor, "_guarded_window",
                        lambda *_args, **_kwargs: pytest.fail("normal points must not start windows"))
    stub_tile(extractor, monkeypatch, assembled)
    extractor.page(250, 3, 3, 1, tmp_path / "page.jpg")
    assert [float(cmd[cmd.index("-ss") + 1]) for cmd in calls] == [250, 260, 270]
    assert assembled == [[b"point-250", b"point-260", b"point-270"]]
    assert extractor.last_stats == {"windows": 0, "individual_points": 3}


@pytest.mark.parametrize("needed_preroll", [10, 30])
def test_real_point_recovery_reuses_exact_entry_after_next_direct_seek_fails(
        tmp_path, monkeypatch, needed_preroll):
    calls, windows, assembled = [], [], []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        calls.append(seek)
        if seek == 250 - needed_preroll:
            Path(cmd[-1]).write_bytes(b"recovered-250")
            return FrameLog("", frame_times=(float(needed_preroll),), frame_numbers=(0,))
        return FrameLog("")

    extractor = worker(run)

    def segment(_extractor, targets, frames, *, start):
        windows.append((list(targets), start))
        for target, frame in zip(targets, frames):
            frame.write_bytes(f"window-{target}".encode())
        return tuple(targets)

    monkeypatch.setattr("app.routers.stream.preview_segment.extract_segment", segment)
    stub_tile(extractor, monkeypatch, assembled)
    extractor.page(250, 3, 3, 1, tmp_path / "page.jpg")
    assert calls == ([250, 249.75, 248, 240, 260] if needed_preroll == 10
                     else [250, 249.75, 248, 240, 220, 260])
    assert windows == [([260, 270], 250 - needed_preroll)]
    assert assembled == [[b"recovered-250", b"window-260", b"window-270"]]
    assert extractor.last_stats == {"windows": 0, "individual_points": 1, "segments": 1}


def test_short_empty_point_recovery_does_not_activate_preroll_windows(tmp_path, monkeypatch):
    calls, assembled = [], []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        calls.append(seek)
        if seek == 250:
            return FrameLog("")
        Path(cmd[-1]).write_bytes(b"short-recovery" if seek == 249.75 else b"normal-point")
        return FrameLog("", frame_times=(0.25 if seek == 249.75 else 0,), frame_numbers=(0,))

    extractor = worker(run)
    monkeypatch.setattr(extractor, "_guarded_window",
                        lambda *_args, **_kwargs: pytest.fail("short recovery must not activate windows"))
    stub_tile(extractor, monkeypatch, assembled)
    extractor.page(250, 2, 2, 1, tmp_path / "page.jpg")
    assert calls == [250, 249.75, 260]
    assert assembled == [[b"short-recovery", b"normal-point"]]
    assert extractor.last_stats == {"windows": 0, "individual_points": 2}


@pytest.mark.parametrize("preroll,limit", [(60, 120), (120, 240)])
def test_long_point_recovery_stays_bounded_without_activating_shared_windows(
        tmp_path, monkeypatch, preroll, limit):
    calls, durations, assembled = [], [], []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        calls.append((seek, timeout))
        durations.append(float(cmd[cmd.index("-t") + 1]))
        if seek == 250 - preroll:
            Path(cmd[-1]).write_bytes(b"long-recovery")
            return FrameLog("", frame_times=(float(preroll),), frame_numbers=(0,))
        if seek == 260:
            Path(cmd[-1]).write_bytes(b"normal-next-point")
            return FrameLog("", frame_times=(0.0,), frame_numbers=(0,))
        return FrameLog("")

    extractor = worker(run)
    monkeypatch.setattr(extractor, "_guarded_window",
                        lambda *_args, **_kwargs: pytest.fail("long recovery must not activate windows"))
    stub_tile(extractor, monkeypatch, assembled)
    extractor.page(250, 2, 2, 1, tmp_path / "page.jpg")
    expected_calls = [(250, 60), (249.75, 30), (248, 30), (240, 60), (220, 60), (190, 120)]
    expected_durations = [12, 0.5, 0.5, 12, 32, 62]
    if preroll == 120:
        expected_calls.append((130, 240))
        expected_durations.append(122)
    assert calls == [*expected_calls, (260, 60)]
    assert durations == [*expected_durations, 12]
    assert calls[-2] == (250 - preroll, limit)
    assert assembled == [[b"long-recovery", b"normal-next-point"]]
    assert extractor._dv_hint_uses == 0
    assert extractor.last_stats == {"windows": 0, "individual_points": 2}


@pytest.mark.parametrize("failure", ["cancel", "yield", "timeout"])
@pytest.mark.parametrize("preroll,limit,calls_before_failure", [(60, 120, 6), (120, 240, 7)])
def test_long_point_interruption_cleans_partial_page(
        tmp_path, failure, preroll, limit, calls_before_failure):
    error = {"cancel": pv._Cancelled(), "yield": pv._YieldToPlayback(),
             "timeout": ExtractionError("预览抽帧超时", reason="timeout", timed_out=True,
                                        limit=limit)}[failure]
    calls, progress = [], []

    def run(cmd, _jid, timeout=60):
        seek = float(cmd[cmd.index("-ss") + 1])
        calls.append((seek, timeout))
        if seek == 250 - preroll:
            Path(cmd[-1]).write_bytes(b"partial before interruption")
            raise error
        return FrameLog("")

    extractor = worker(run)
    extractor.progress = progress.append
    output = tmp_path / "page.jpg"
    output.write_bytes(b"stale page")
    with pytest.raises(type(error)) as caught:
        extractor.page(250, 1, 1, 1, output)
    assert caught.value is error
    assert calls[-1] == (250 - preroll, limit)
    assert len(calls) == calls_before_failure
    assert progress == []
    assert not output.exists()
    assert not list(tmp_path.glob("frame_*.png"))


@pytest.mark.parametrize("interval,lengths,size", [(10, [2, 2, 1], 5), (20, [2, 2, 1], 5), (60, [1] * 3, 3)])
def test_page_shares_bounded_decoder_windows_without_skipping_sample_cells(
        tmp_path, monkeypatch, interval, lengths, size):
    extractor = worker(lambda *_args, **_kwargs: pytest.fail("unexpected FFmpeg"), interval=interval)
    extractor._remember_dv_preroll(10, 250 - interval)
    windows, assembled, progress = [], [], []

    def window(targets, frames, *, preroll):
        windows.append((list(targets), preroll))
        assert targets[-1] + min(interval, 2) - max(0, targets[0] - preroll) <= 42
        for target, frame in zip(targets, frames):
            frame.write_bytes(f"target-{target}".encode())
        return True

    monkeypatch.setattr(extractor, "_guarded_window", window)
    monkeypatch.setattr(extractor, "_sample_point",
                        lambda *_args, **_kwargs: pytest.fail("valid windows must not decode points"))
    extractor.progress = progress.append
    stub_tile(extractor, monkeypatch, assembled)
    output = tmp_path / "page.jpg"
    extractor.page(250, size, 5, 1, output)
    targets = [250 + index * interval for index in range(size)]
    assert [len(points) for points, _before in windows] == lengths
    assert [target for points, _before in windows for target in points] == targets
    assert all(before == 10 for _points, before in windows)
    assert assembled == [[f"target-{target}".encode() for target in targets]]
    assert progress == [250 // interval + index + 1 for index in range(size)]
    assert extractor.last_stats == {"windows": len(lengths), "individual_points": 0}
    assert extractor.last_mode == "dv_windows"
    assert output.read_bytes() == b"complete page"
    assert not list(tmp_path.glob("frame_*.png"))


def test_failed_page_windows_fall_back_to_each_original_target(tmp_path, monkeypatch):
    extractor = worker(lambda *_args, **_kwargs: pytest.fail("unexpected FFmpeg"))
    extractor._remember_dv_preroll(10, 240)
    windows, points, assembled, progress = [], [], [], []

    def window(targets, _frames, *, preroll):
        windows.append((list(targets), preroll))
        return False

    def point(target, frame, *, nearby):
        assert nearby is False
        points.append(target)
        frame.write_bytes(f"point-{target}".encode())
        return "accurate"

    monkeypatch.setattr(extractor, "_guarded_window", window)
    monkeypatch.setattr(extractor, "_sample_point", point)
    extractor.progress = progress.append
    stub_tile(extractor, monkeypatch, assembled)
    extractor.page(250, 3, 3, 1, tmp_path / "page.jpg")
    assert windows == [([250, 260], 10), ([250, 260], 30)]
    assert points == [250, 260, 270]
    assert assembled == [[b"point-250", b"point-260", b"point-270"]]
    assert progress == [26, 27, 28]
    assert extractor.last_stats == {"windows": 0, "individual_points": 3}
    assert [sample["mode"] for sample in extractor.last_samples] == ["accurate"] * 3
    assert not list(tmp_path.glob("frame_*.png"))


def test_page_does_not_repeat_identical_clipped_preroll_at_the_beginning(tmp_path, monkeypatch):
    extractor = worker(lambda *_args, **_kwargs: pytest.fail("unexpected FFmpeg"), interval=1)
    extractor._remember_dv_preroll(10, 0)
    windows, points, assembled = [], [], []

    def window(targets, _frames, *, preroll):
        windows.append((list(targets), preroll))
        return False

    def point(target, frame, *, nearby):
        assert nearby is False
        points.append(target)
        frame.write_bytes(f"point-{target}".encode())
        return "accurate"

    monkeypatch.setattr(extractor, "_guarded_window", window)
    monkeypatch.setattr(extractor, "_sample_point", point)
    stub_tile(extractor, monkeypatch, assembled)
    extractor.page(1, 2, 2, 1, tmp_path / "page.jpg")
    assert windows == [([1, 2], 10)]  # Both 10s/30s prerolls would seek to zero.
    assert points == [1, 2]
    assert assembled == [[b"point-1", b"point-2"]]


def test_long_preroll_splits_twenty_second_points_without_losing_cells(tmp_path, monkeypatch):
    extractor = worker(lambda *_args, **_kwargs: pytest.fail("unexpected FFmpeg"), interval=20)
    extractor._remember_dv_preroll(10, 230)
    windows, points, assembled = [], [], []

    def window(targets, frames, *, preroll):
        windows.append((list(targets), preroll))
        if preroll == 10:
            return False
        assert len(targets) == 1
        frames[0].write_bytes(f"target-{targets[0]}".encode())
        return True

    def point(target, frame, *, nearby):
        assert nearby is False
        points.append(target)
        frame.write_bytes(f"point-{target}".encode())
        return "accurate"

    monkeypatch.setattr(extractor, "_guarded_window", window)
    monkeypatch.setattr(extractor, "_sample_point", point)
    stub_tile(extractor, monkeypatch, assembled)
    extractor.page(250, 4, 4, 1, tmp_path / "page.jpg")
    assert windows == [([250, 270], 10), ([250], 30), ([270], 30), ([290], 30)]
    assert points == [310]
    assert assembled == [[b"target-250", b"target-270", b"target-290", b"point-310"]]
    assert extractor.last_stats == {"windows": 3, "individual_points": 1}
    assert not list(tmp_path.glob("frame_*.png"))


def test_alternating_successful_prerolls_cannot_extend_the_three_window_budget(tmp_path, monkeypatch):
    extractor = worker(lambda *_args, **_kwargs: pytest.fail("unexpected FFmpeg"))
    extractor._remember_dv_preroll(10, 240)
    windows, points, assembled = [], [], []
    required = {250: 30, 270: 10, 290: 30}

    def window(targets, frames, *, preroll):
        windows.append((list(targets), preroll))
        if preroll != required[targets[0]]:
            return False
        for target, frame in zip(targets, frames):
            frame.write_bytes(f"target-{target}".encode())
        return True

    def point(target, frame, *, nearby):
        assert nearby is False
        points.append(target)
        frame.write_bytes(f"point-{target}".encode())
        return "accurate"

    monkeypatch.setattr(extractor, "_guarded_window", window)
    monkeypatch.setattr(extractor, "_sample_point", point)
    stub_tile(extractor, monkeypatch, assembled)
    extractor.page(250, 8, 4, 2, tmp_path / "page.jpg")
    assert windows == [([250, 260], 10), ([250, 260], 30),
                       ([270, 280], 30), ([270, 280], 10),
                       ([290, 300], 10), ([290, 300], 30)]
    assert points == [310, 320]
    assert assembled == [[b"target-250", b"target-260", b"target-270", b"target-280",
                          b"target-290", b"target-300", b"point-310", b"point-320"]]
    assert extractor.last_stats == {"windows": 3, "individual_points": 2}
    assert not list(tmp_path.glob("frame_*.png"))


def test_successful_long_preroll_is_reused_across_three_adjacent_windows_then_point_probes(
        tmp_path, monkeypatch):
    extractor = worker(lambda *_args, **_kwargs: pytest.fail("unexpected FFmpeg"))
    extractor._remember_dv_preroll(30, 240)
    windows, points, assembled = [], [], []

    def window(targets, frames, *, preroll):
        windows.append((list(targets), preroll))
        assert preroll == 30
        for target, frame in zip(targets, frames):
            frame.write_bytes(f"target-{target}".encode())
        return True

    def point(target, frame, *, nearby):
        assert nearby is False
        points.append(target)
        frame.write_bytes(f"point-{target}".encode())
        return "accurate"

    monkeypatch.setattr(extractor, "_guarded_window", window)
    monkeypatch.setattr(extractor, "_sample_point", point)
    stub_tile(extractor, monkeypatch, assembled)
    for index, start in enumerate((250, 270, 290, 310)):
        extractor.page(start, 2, 2, 1, tmp_path / f"page-{index}.jpg")
    assert windows == [([250, 260], 30), ([270, 280], 30), ([290, 300], 30)]
    assert points == [310, 320]
    assert len(assembled) == 4
    assert not list(tmp_path.glob("frame_*.png"))


@pytest.mark.parametrize("new_start", [280, 1000, 240])
def test_preroll_preference_is_not_inherited_by_a_nonadjacent_page(tmp_path, monkeypatch, new_start):
    extractor = worker(lambda *_args, **_kwargs: pytest.fail("unexpected FFmpeg"))
    extractor._remember_dv_preroll(30, 240)
    windows, points, assembled = [], [], []

    def window(targets, frames, *, preroll):
        windows.append((list(targets), preroll))
        assert preroll == 30
        for frame in frames:
            frame.write_bytes(b"verified")
        return True

    def point(target, frame, *, nearby):
        assert nearby is False
        points.append(target)
        frame.write_bytes(f"point-{target}".encode())
        return "accurate"

    monkeypatch.setattr(extractor, "_guarded_window", window)
    monkeypatch.setattr(extractor, "_sample_point", point)
    stub_tile(extractor, monkeypatch, assembled)
    extractor.page(250, 2, 2, 1, tmp_path / "page-a.jpg")
    extractor.page(new_start, 2, 2, 1, tmp_path / "page-b.jpg")
    assert windows == [([250, 260], 30)]
    assert points == [new_start, new_start + 10]
    assert len(assembled) == 2


@pytest.mark.parametrize("interruption", ["cancel", "playback", "timeout"])
def test_interrupted_page_window_does_not_fall_back_or_commit_output(
        tmp_path, monkeypatch, interruption):
    error = {"cancel": pv._Cancelled(), "playback": pv._YieldToPlayback(),
             "timeout": ExtractionError("超时", reason="timeout", timed_out=True)}[interruption]
    extractor = worker(lambda *_args, **_kwargs: pytest.fail("unexpected FFmpeg"))
    extractor._remember_dv_preroll(10, 240)
    calls, progress = [], []

    def window(targets, frames, *, preroll):
        calls.append((list(targets), preroll))
        frames[0].write_bytes(b"partial")
        raise error

    monkeypatch.setattr(extractor, "_guarded_window", window)
    monkeypatch.setattr(extractor, "_sample_point",
                        lambda *_args, **_kwargs: pytest.fail("interruption must not enter point fallback"))
    monkeypatch.setattr(extractor, "_tile_page",
                        lambda *_args, **_kwargs: pytest.fail("interruption must not commit a page"))
    extractor.progress = progress.append
    output = tmp_path / "page.jpg"
    output.write_bytes(b"stale page")
    with pytest.raises(type(error)) as caught:
        extractor.page(250, 2, 2, 1, output)
    assert caught.value is error
    assert calls == [([250, 260], 10)]
    assert progress == []
    assert not output.exists()
    assert not list(tmp_path.glob("frame_*.png"))


@pytest.mark.parametrize("remote,explicit,guarded,expected", [
    (False, "auto", False, "window_keyframes"),
    (True, "auto", False, "accurate"),
    (False, "auto", True, "dv_windows"),
    (True, "auto", True, "dv_windows"),
    (False, "accurate", True, "accurate"),
    (True, "accurate", True, "accurate"),
])
def test_only_guarded_automatic_mode_selects_dv_windows(
        monkeypatch, remote, explicit, guarded, expected):
    monkeypatch.setenv("PREVIEW_MODE", explicit)
    extractor = ex.Extractor(
        SimpleNamespace(input="/isolated/synthetic.mp4"), "format=rgb24", 10,
        remote=remote, run=lambda *_args, **_kwargs: None, jid="isolated",
        progress=lambda _n: None, software=True,
        frame_guard="sidedata=mode=select:type=DOVI_METADATA" if guarded else "")
    assert extractor.mode == expected


def rgb_pixels(ffmpeg, env, image):
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1",
         "-filter_threads", "1", "-t", "1", "-i", str(image), "-frames:v", "1",
         "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        env=env, capture_output=True, check=True, timeout=15)
    assert len(result.stdout) == 64 * 64 * 3
    return result.stdout


@pytest.mark.parametrize("target,next_full,seeks,limit", [
    (45, 70, [45, 44.75, 43, 35, 15, 0], 120),
    (100, 140, [100, 99.75, 98, 90, 70, 40, 0], 240),
])
def test_real_long_referenced_p5_point_recovers_original_target_pixels(
        software_vulkan, tmp_path, monkeypatch, require_dovi_metadata,
        target, next_full, seeks, limit):
    ffmpeg, env = software_vulkan
    require_dovi_metadata(ffmpeg, env)
    # Every picture is independently decodable, but references between the full
    # RPUs at zero and next_full need the decoder state from zero. For target
    # 100s even a 60s preroll cannot restore that state. The next full RPU lies
    # outside the accepted target window and has a deliberately different color.
    colors = [(100, 0, 0)] * (next_full + 2)
    colors[target:target + 3] = [(0, 100, 0), (0, 0, 100), (50, 50, 50)]
    colors[next_full] = (0, 0, 100)
    reference_rpu = color_fixtures._p5_reference_rpu()
    rpus = [color_fixtures._P5_RPU[4:] if frame in (0, next_full) else reference_rpu
            for frame in range(len(colors))]
    video = color_fixtures._write_synthetic_p5(ffmpeg, env, tmp_path, colors, rpus)
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: ffmpeg)
    calls = []

    def run(cmd, jid, timeout=60):
        log = pv._run(cmd, jid, timeout)
        calls.append((cmd, timeout, log))
        return log

    extractor = worker(run, video=str(video))
    recovered = tmp_path / "recovered.png"
    assert extractor._sample_point(target, recovered, nearby=False) == "accurate"
    assert len(calls) == len(seeks)
    assert [float(cmd[cmd.index("-ss") + 1]) for cmd, _, _ in calls] == seeks
    assert all(log.frame_times == () for _, _, log in calls[:-1])
    command, timeout, log = calls[-1]
    assert float(command[command.index("-t") + 1]) == target + 2
    assert timeout == limit
    # FFmpeg may feed the next picture to the filter before the PNG encoder's
    # one-frame limit stops it; the first showinfo record identifies the PNG.
    assert log.frame_times[0] == float(target)
    assert extractor._dv_hint_uses == 0

    conversion = color_plan({"dv_profile": 5, "dv_bl_compat": 0}, 64, 64)
    reference = tmp_path / "independent.png"
    # An independent decoder starting at zero is the pixel reference. Selection
    # after the metadata guard also differs from production's single-point path.
    vf = (conversion["frame_guard"] + f",select='between(t,{target},{target + 0.5})',"
          + conversion["filter"] + ",showinfo=checksum=0")
    reference_log = pv._run(
        [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin", "-y",
         *conversion["input_args"], "-threads", "2", "-filter_threads", "1",
         "-ss", "0", "-t", str(target + 0.5), "-i", str(video), "-map", "0:v:0", "-an", "-sn", "-dn",
         "-vf", vf, "-frames:v", "1", "-fps_mode", "passthrough", "-threads", "1", "-c:v", "png",
         "-pix_fmt", "rgb24", "-compression_level", "1", str(reference)], extractor.jid, timeout=30)
    assert reference_log.frame_times[0] == float(target)
    pixels = rgb_pixels(ffmpeg, env, recovered)
    assert pixels == rgb_pixels(ffmpeg, env, reference)
    center = pixels[(32 * 64 + 32) * 3:][:3]
    assert center[1] > max(center[0], center[2]) + 80


def test_real_referenced_p5_window_matches_independent_point_pixels(
        synthetic_p5_references_24fps, tmp_path, monkeypatch):
    ffmpeg, env, video = synthetic_p5_references_24fps
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: ffmpeg)
    calls = []

    def run(cmd, jid, timeout=60):
        log = pv._run(cmd, jid, timeout)
        calls.append((cmd, log))
        return log

    extractor = worker(run, interval=0.5, video=str(video))
    targets = [1.0, 1.5]
    frames = [tmp_path / "window_0.png", tmp_path / "window_1.png"]
    assert extractor._guarded_window(targets, frames, preroll=10) is True
    assert len(calls) == 1
    window_log = calls[0][1]
    assert window_log.frame_numbers == (0, 1)
    assert window_log.frame_times == pytest.approx(targets, abs=0.001)
    conversion = color_plan({"dv_profile": 5, "dv_bl_compat": 0}, 64, 64)
    for index, target in enumerate(targets):
        reference = tmp_path / f"independent_{index}.png"
        # Each reference decoder starts at zero to restore the complete RPU;
        # it chooses the target independently of the new selected_n window.
        vf = (conversion["frame_guard"] + f",select='between(t,{target},{target + 0.5})',"
              + conversion["filter"] + ",showinfo=checksum=0")
        log = pv._run(
            [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin", "-y",
             *conversion["input_args"], "-threads", "2", "-filter_threads", "1",
             "-ss", "0", "-t", str(target + 0.5), "-i", str(video), "-map", "0:v:0",
             "-an", "-sn", "-dn", "-vf", vf, "-frames:v", "1", "-fps_mode", "passthrough",
             "-threads", "1", "-c:v", "png", "-pix_fmt", "rgb24", "-compression_level", "1",
             str(reference)], extractor.jid, timeout=30)
        assert log.frame_times[0] == pytest.approx(target, abs=0.001)
        assert rgb_pixels(ffmpeg, env, frames[index]) == rgb_pixels(ffmpeg, env, reference)
    assert not list(tmp_path.glob(".dv-window-*"))


def test_real_p5_without_frame_rpu_cannot_publish_color_images(
        synthetic_p5_references_24fps, tmp_path, monkeypatch):
    ffmpeg, env, video = synthetic_p5_references_24fps
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: ffmpeg)
    missing_rpu = tmp_path / "no-rpu.mp4"
    # Remove only the self-generated type-62 NALs, preserving real HEVC frames.
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(video),
         "-map", "0:v:0", "-c:v", "copy", "-bsf:v", "filter_units=remove_types=62",
         "-strict", "unofficial", str(missing_rpu)],
        env=env, capture_output=True, check=True, timeout=15)
    frames = [tmp_path / "frame_00.png", tmp_path / "frame_01.png"]
    extractor = worker(pv._run, interval=0.5, video=str(missing_rpu))
    assert extractor._guarded_window([1.0, 1.5], frames, preroll=10) is False
    assert_clean(tmp_path, frames)
