"""连续采样验证真实 PTS、缺帧前缀及资源失败时的清理边界。"""
from pathlib import Path
import subprocess
from threading import Event, RLock
from types import SimpleNamespace

import pytest

from app import media
from app.jobkit import JobRegistry
from app.routers.stream import preview_segment as segment
from app.routers.stream import previews as pv
from app.routers.stream.preview_color import color_plan
from app.routers.stream.preview_extract import Extractor
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
    monkeypatch.setattr(pv, "_jobs", JobRegistry(prefix="segment-test"))
    monkeypatch.setattr(pv, "_sessions", {})
    monkeypatch.setattr(pv, "_sess_lock", RLock())
    monkeypatch.setattr(pv, "_stop", Event())


def worker(run, *, interval=10, video="/isolated/generated-p5.mp4"):
    conversion = color_plan({"dv_profile": 5, "dv_bl_compat": 0}, 64, 64)
    return Extractor(
        SimpleNamespace(input=video), conversion["filter"], interval,
        remote=True, run=run, jid=pv._jobs.create(library_id=1)["job_id"],
        progress=lambda _n: None, input_args=conversion["input_args"],
        software=True, frame_guard=conversion["frame_guard"])


def write_output(cmd, *, times=(120.0, 130.0, 140.0), numbers=None, images=None):
    pattern = cmd[-1]
    if images is None:
        images = {index: f"image-{index}".encode() for index in range(len(times))}
    for index, content in images.items():
        Path(pattern % index).write_bytes(content)
    if numbers is None:
        numbers = tuple(range(len(times)))
    return FrameLog("", frame_times=times, frame_numbers=numbers)


def frame_paths(tmp_path, count):
    frames = [tmp_path / f"frame_{index:02d}.png" for index in range(count)]
    for frame in frames:
        frame.write_bytes(b"stale image")
    return frames


def assert_clean(tmp_path, frames):
    assert not any(frame.exists() for frame in frames)
    assert not list(tmp_path.glob(".dv-segment-*"))


@pytest.mark.parametrize("targets,start,interval,times,duration,timeout", [
    ([250, 260, 270], 130, 10, (120, 130, 140), 142, 284),
    ([250, 428], 130, 10, (120, 298), 300, 600),
    (list(range(250, 500, 10)), 220, 10, tuple(range(30, 280, 10)), 272, 544),
    ([1, 1.5, 2], 0, 0.5, (1, 1.5, 2), 2.5, 60),
])
def test_segment_decodes_once_with_bounded_output_and_verified_image_order(
        tmp_path, targets, start, interval, times, duration, timeout):
    calls = []

    def run(cmd, _jid, timeout=60):
        calls.append((cmd, timeout))
        return write_output(cmd, times=times)

    frames = frame_paths(tmp_path, len(targets))
    assert segment.extract_segment(worker(run, interval=interval), targets, frames, start=start) == tuple(targets)
    assert [frame.read_bytes() for frame in frames] == [
        f"image-{index}".encode() for index in range(len(targets))]
    assert not list(tmp_path.glob(".dv-segment-*"))
    assert len(calls) == 1
    cmd, observed_timeout = calls[0]
    assert float(cmd[cmd.index("-ss") + 1]) == start
    assert float(cmd[cmd.index("-t") + 1]) == duration <= 300
    assert observed_timeout == timeout <= 600
    assert observed_timeout >= 2 * duration
    assert "-skip_frame" not in cmd and "-noaccurate_seek" not in cmd
    assert cmd[cmd.index("-fps_mode") + 1] == "passthrough"
    assert int(cmd[cmd.index("-frames:v") + 1]) == len(targets) <= 25
    vf = cmd[cmd.index("-vf") + 1]
    assert (vf.index("DOVI_METADATA") < vf.index("select=")
            < vf.index("libplacebo=") < vf.index("showinfo=")
            < vf.index("settb=") < vf.index("setpts="))
    assert "fps=" not in vf


def test_verified_prefix_is_available_without_publishing_unfilled_cells(tmp_path):
    frames = frame_paths(tmp_path, 3)
    extractor = worker(lambda cmd, _jid, timeout: write_output(cmd, times=(120.04, 130.04)))
    assert segment.extract_segment(extractor, [250, 260, 270], frames, start=130) == pytest.approx((250.04, 260.04))
    assert [frame.read_bytes() for frame in frames[:2]] == [b"image-0", b"image-1"]
    assert not frames[2].exists()
    assert not list(tmp_path.glob(".dv-segment-*"))


@pytest.mark.parametrize("times,numbers,images", [
    ((), (), {}),
    ((120, 130, 140, 150), None, None),
    ((119.99, 130), None, None),
    ((122.01, 130), None, None),
    ((120, 129.99), None, None),
    ((120, 132.01), None, None),
    ((130,), None, None),  # Later metadata must not become the first target.
    ((130, 120), None, None),
    ((120, 120), None, None),
    ((float("nan"), 130), None, None),
    ((120, float("inf")), None, None),
    ((120, 130), (), None),
    ((120, 130), (1, 2), None),
    ((120, 130), (0, 2), None),
    ((120, 130), (0, 0), None),
    ((120, 130), (0,), None),
    ((120, 130), None, {0: b"first"}),
    ((120, 130), None, {0: b"first", 1: b""}),
    ((120, 130), None, {0: b"first", 2: b"second"}),
    ((120, 130), None, {0: b"first", 1: b"second", 2: b"extra"}),
])
def test_malformed_output_rejects_the_entire_segment(tmp_path, times, numbers, images):
    frames = frame_paths(tmp_path, 3)

    def run(cmd, _jid, timeout):
        return write_output(cmd, times=times, numbers=numbers, images=images)

    assert segment.extract_segment(worker(run), [250, 260, 270], frames, start=130) == ()
    assert_clean(tmp_path, frames)


@pytest.mark.parametrize("targets,start,interval", [
    ([], 0, 10),
    (list(range(26)), 0, 10),
    ([250, 250], 130, 10),
    ([260, 250], 130, 10),
    ([-1], 0, 10),
    ([float("nan")], 0, 10),
    ([float("inf")], 0, 10),
    ([250], -1, 10),
    ([250], float("nan"), 10),
    ([250], 251, 10),
    ([250], 119.9, 10),
    ([250], 129, 0.5),
    ([250, 428.01], 130, 10),
    ([250, 500], 120, 10),
    ([250], 130, 0),
    ([250], 130, float("nan")),
])
def test_out_of_bounds_request_never_starts_a_decoder(tmp_path, targets, start, interval):
    frames = frame_paths(tmp_path, len(targets))
    extractor = worker(lambda *_args, **_kwargs: pytest.fail("invalid segment started FFmpeg"),
                       interval=interval)
    assert segment.extract_segment(extractor, targets, frames, start=start) == ()
    assert_clean(tmp_path, frames)


@pytest.mark.parametrize("failure", ["missing_guard", "missing_destination", "duplicate_destination"])
def test_invalid_output_contract_never_starts_a_decoder(tmp_path, failure):
    frames = frame_paths(tmp_path, 2)
    extractor = worker(lambda *_args, **_kwargs: pytest.fail("invalid segment started FFmpeg"))
    if failure == "missing_guard":
        extractor.frame_guard = ""
    elif failure == "missing_destination":
        frames.pop().unlink()
    else:
        frames.pop().unlink()
        frames.append(frames[0])
    assert segment.extract_segment(extractor, [250, 260], frames, start=130) == ()
    assert_clean(tmp_path, frames)


@pytest.mark.parametrize("failure", ["runtime", "decoder"])
def test_decoder_failure_discards_partial_images_for_point_fallback(tmp_path, failure):
    frames = frame_paths(tmp_path, 3)

    def run(cmd, _jid, timeout):
        write_output(cmd, times=(120,))
        if failure == "decoder":
            raise ExtractionError("无法解码", reason="exit", error_kind="decoder")
        raise RuntimeError("private input URL")

    assert segment.extract_segment(worker(run), [250, 260, 270], frames, start=130) == ()
    assert_clean(tmp_path, frames)


@pytest.mark.parametrize("kind", ["cancel", "yield", "timeout", "memory", "permission",
                                  "option", "filter", "hardware", "input", "io"])
def test_interruptions_and_resource_failures_propagate_after_cleanup(tmp_path, kind):
    error = {"cancel": pv._Cancelled(), "yield": pv._YieldToPlayback(),
             "timeout": ExtractionError("超时", reason="timeout", timed_out=True),
             "io": OSError("isolated output full")}.get(kind)
    if error is None:
        error = ExtractionError("运行条件不满足", reason="exit", error_kind=kind)
    frames = frame_paths(tmp_path, 3)
    calls = []

    def run(cmd, _jid, timeout):
        calls.append(cmd)
        write_output(cmd, times=(120,))
        raise error

    with pytest.raises(type(error)) as caught:
        segment.extract_segment(worker(run), [250, 260, 270], frames, start=130)
    assert caught.value is error
    assert len(calls) == 1
    assert_clean(tmp_path, frames)


def test_copy_error_removes_already_published_and_partial_destinations(tmp_path, monkeypatch):
    real_copy = segment.shutil.copyfile
    copied = []

    def copy_file(source, destination):
        copied.append(destination)
        if len(copied) == 2:
            destination.write_bytes(b"partial write")
            raise OSError("isolated output full")
        return real_copy(source, destination)

    monkeypatch.setattr(segment.shutil, "copyfile", copy_file)
    frames = frame_paths(tmp_path, 3)
    extractor = worker(lambda cmd, _jid, timeout: write_output(cmd))
    with pytest.raises(OSError, match="isolated output full"):
        segment.extract_segment(extractor, [250, 260, 270], frames, start=130)
    assert copied == frames[:2]
    assert_clean(tmp_path, frames)


def rgb_pixels(ffmpeg, env, image):
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1",
         "-filter_threads", "1", "-i", str(image), "-frames:v", "1", "-threads", "1",
         "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        env=env, capture_output=True, check=True, timeout=15)
    assert len(result.stdout) == 64 * 64 * 3
    return result.stdout


def test_real_continuous_p5_samples_match_independent_point_pixels(
        synthetic_p5_references_24fps, tmp_path, monkeypatch):
    ffmpeg, env, video = synthetic_p5_references_24fps
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: ffmpeg)
    targets = [1.0, 1.5, 2.0, 2.5]
    frames = frame_paths(tmp_path, len(targets))
    extractor = worker(pv._run, interval=0.5, video=str(video))
    assert segment.extract_segment(extractor, targets, frames, start=0) == pytest.approx(targets, abs=0.001)
    conversion = color_plan({"dv_profile": 5, "dv_bl_compat": 0}, 64, 64)
    for index, target in enumerate(targets):
        reference = tmp_path / f"independent_{index}.png"
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


def test_real_eof_preserves_only_the_verified_prefix(
        synthetic_p5_references_24fps, tmp_path, monkeypatch):
    ffmpeg, _env, video = synthetic_p5_references_24fps
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: ffmpeg)
    frames = frame_paths(tmp_path, 4)
    extractor = worker(pv._run, interval=0.1, video=str(video))
    assert segment.extract_segment(extractor, [2, 2.5, 3, 3.5], frames, start=0) == pytest.approx((2, 2.5), abs=0.001)
    assert all(frame.stat().st_size for frame in frames[:2])
    assert not any(frame.exists() for frame in frames[2:])


def test_real_missing_first_metadata_cannot_borrow_a_later_full_rpu_frame(
        synthetic_p5_references_24fps, tmp_path, monkeypatch):
    ffmpeg, _env, video = synthetic_p5_references_24fps
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: ffmpeg)
    # Seek past the full RPU at zero. A new full RPU at 2.5 s must not
    # consume the PNG intended for 1.5 s after that target's metadata gap.
    frames = frame_paths(tmp_path, 2)
    extractor = worker(pv._run, interval=0.1, video=str(video))
    assert segment.extract_segment(extractor, [1.5, 2.5], frames, start=1) == ()
    assert_clean(tmp_path, frames)
