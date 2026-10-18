"""预览色彩选择及隔离的软件 Vulkan / 缺 RPU 防护。"""
import base64
import json
import os
import re
import shutil
import struct
import subprocess

import pytest

from app import media
from app.routers.stream.preview_color import color_plan, smoke_command


def plan(info=None):
    return color_plan(info or {}, 480, 270)


def test_sdr_keeps_existing_scale_pad_and_lossless_rgb_path():
    result = plan()
    assert result == {
        "filter": "scale=480:270:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                  "pad=480:270:(ow-iw)/2:(oh-ih)/2,setsar=1,format=rgb24",
        "input_args": (), "software": False, "frame_guard": "",
    }


@pytest.mark.parametrize("info", [
    {"hdr": "hdr10"}, {"hdr": "hlg"},
    {"dv_profile": 8, "dv_bl_compat": 1},
    {"dv_profile": 8, "dv_bl_compat": 4},
    {"dv_profile": 7, "dv_bl_compat": 6, "vcodec": "hevc",
     "color_transfer": "smpte2084", "color_primaries": "bt2020"},
])
def test_compatible_hdr_bases_keep_existing_conversion_without_vulkan(info):
    result = plan(info)
    assert result["filter"].startswith("scale=")
    assert "tonemap=tonemap=hable:desat=0" in result["filter"]
    assert result["input_args"] == () and not result["software"]
    assert result["frame_guard"] == ""


def test_sdr_compatible_dv_base_does_not_tone_map_hdr_tag():
    result = plan({"dv_profile": 8, "dv_bl_compat": 2, "hdr": "hdr10"})
    assert result == plan()


@pytest.mark.parametrize("change", [
    {"vcodec": "h264"}, {"color_transfer": "bt709"}, {"color_primaries": "bt709"},
    {"dv_profile": 8}, {"dv_bl_compat": 0},
])
def test_p7_compatibility_requires_verified_hdr10_metadata(change):
    info = {"dv_profile": 7, "dv_bl_compat": 6, "vcodec": "hevc",
            "color_transfer": "smpte2084", "color_primaries": "bt2020"}
    with pytest.raises(RuntimeError, match="该 Dolby Vision 片源无可用兼容基底"):
        plan(info | change)


def test_p5_uses_cpu_vulkan_and_requires_parsed_frame_rpu_before_any_conversion():
    result = plan({"dv_profile": 5, "dv_bl_compat": 0, "hdr": "hdr10"})
    assert result["software"] is True
    assert result["input_args"] == ("-init_hw_device", "vulkan=preview:llvmpipe",
                                     "-filter_hw_device", "preview")
    assert result["frame_guard"] == "sidedata=mode=select:type=DOVI_METADATA"
    vf = result["filter"]
    assert vf.startswith("libplacebo=w=480:h=270:")
    assert "apply_dolbyvision=1" in vf and "peak_detect=0" in vf
    assert "contrast_recovery=0" in vf and "apply_filmgrain=0" in vf
    assert "colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv" in vf
    assert "downscaler=mitchell" in vf
    assert ",scale=" not in vf and "zscale" not in vf
    assert vf.index("libplacebo=") < vf.index("format=rgb24") < vf.index("pad=")


@pytest.mark.parametrize("info", [
    {"dv_profile": 4}, {"dv_profile": 7}, {"dv_profile": 8},
    {"dv_profile": 5, "dv_bl_compat": 1},
    {"dv_profile": 5, "dv_bl_compat": 2},
    {"dv_profile": 5, "dv_bl_compat": 4},
    {"dv_profile": 5, "dv_bl_compat": 6},
    {"dv_profile": "untrusted"}, {"dv_profile": float("nan")},
    {"dv_profile": 5, "dv_bl_compat": "untrusted"},
])
def test_unsupported_or_invalid_dv_has_fixed_safe_failure(info):
    with pytest.raises(RuntimeError) as exc:
        plan(info)
    assert str(exc.value) == "该 Dolby Vision 片源无可用兼容基底，暂不支持预览"


@pytest.mark.parametrize("width,height", [(0, 270), (480, -1), (481, 270), (480, 271),
                                         (True, 270), ("480", 270)])
def test_color_dimensions_cannot_inject_filter_arguments(width, height):
    with pytest.raises(ValueError):
        color_plan({}, width, height)


def test_runtime_smoke_is_bounded_synthetic_and_has_no_media_rpu_guard():
    cmd = smoke_command("/isolated/ffmpeg")
    assert cmd[0] == "/isolated/ffmpeg"
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert "color=gray:s=64x64:r=1:d=1" in cmd[cmd.index("-i") + 1]
    assert "format=yuv420p10le" in cmd[cmd.index("-i") + 1]
    assert "DOVI_METADATA" not in cmd[cmd.index("-vf") + 1]
    assert cmd[-3:] == ["-f", "null", "-"]


@pytest.fixture(scope="module")
def software_vulkan():
    ffmpeg = shutil.which("ffmpeg") or media._static_bins_if_present().get("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    env = os.environ.copy()
    env["LP_NUM_THREADS"] = "2"
    result = subprocess.run(smoke_command(ffmpeg), env=env, capture_output=True, timeout=30)
    if result.returncode:
        stderr = result.stderr.decode(errors="replace").lower()
        unavailable = ("no such filter", "device creation failed", "failed to create vulkan",
                       "failed creating vulkan", "no device found", "no vulkan device found")
        if any(marker in stderr for marker in unavailable):
            pytest.skip("software Vulkan/libplacebo unavailable")
        pytest.fail(f"software Vulkan smoke failed: {stderr[-2000:]}")
    return ffmpeg, env


def raw_smoke(ffmpeg, *, guard=""):
    cmd = smoke_command(ffmpeg)
    if guard:
        index = cmd.index("-vf") + 1
        cmd[index] = guard + "," + cmd[index]
    return cmd[:-3] + ["-pix_fmt", "rgb24", "-f", "rawvideo", "-"]


def test_real_10bit_cpu_conversion_produces_full_size_neutral_sdr(software_vulkan):
    ffmpeg, env = software_vulkan
    result = subprocess.run(raw_smoke(ffmpeg), env=env, capture_output=True, check=True, timeout=30)
    assert len(result.stdout) == 64 * 64 * 3
    center = result.stdout[(32 * 64 + 32) * 3:][:3]
    assert 10 < min(center) < 255
    assert max(center) - min(center) <= 2


def test_real_missing_frame_rpu_produces_no_wrong_color_image(
        software_vulkan, require_dovi_metadata):
    ffmpeg, env = software_vulkan
    require_dovi_metadata(ffmpeg, env)
    result = subprocess.run(raw_smoke(ffmpeg, guard=plan({"dv_profile": 5})["frame_guard"]),
                            env=env, capture_output=True, check=True, timeout=30)
    # HDR input without parsed DV RPU must never silently become a P5 preview.
    assert result.stdout == b""


# Self-generated metadata only: dovi_tool 2.3.4 `generate` with
# {"cm_version":"V40","profile":"5","length":1,"level6":null,
#  "source_min_pq":0,"source_max_pq":3079}. No film content is used.
# Official utility archive SHA256:
# 1844258e13c26607b32224bf1fa82b595d3b35949f5467405fda560daad32b3f.
# The 150 bytes below contain one RPU, without an HEVC NAL header.
_P5_RPU = base64.b64decode(
    "AAAAARkICQBAYbZQbwA/+AH/wA//0AAACAAABoAAAEAAADQAAAMCAAADAaIAADHwaRIAD8Ww"
    "RDIAABC+pXAAAAMAAIAAAAMAgAAABCuf6j/qP+o0K5/qP+o/6jQrn//wAAADAAADAAADAAZC"
    "ABgOFSAQCgAAAwAAAwAAAwBQMBwAQAQAAgkAf8AARQsBAAADABSRnbiA")
_COLORS = [(100, 0, 0), (0, 100, 0), (0, 0, 100),
           (10, 10, 10), (50, 50, 50), (100, 100, 100)]


def _inverse3(matrix):
    augmented = [list(row) + [int(i == j) for j in range(3)]
                 for i, row in enumerate(matrix)]
    for i in range(3):
        pivot = max(range(i, 3), key=lambda j: abs(augmented[j][i]))
        augmented[i], augmented[pivot] = augmented[pivot], augmented[i]
        divisor = augmented[i][i]
        augmented[i] = [value / divisor for value in augmented[i]]
        for j in range(3):
            if j != i:
                scale = augmented[j][i]
                augmented[j] = [value - scale * pivot_value
                                for value, pivot_value in zip(augmented[j], augmented[i])]
    return [row[3:] for row in augmented]


def _matvec(matrix, vector):
    return [sum(a * b for a, b in zip(row, vector)) for row in matrix]


def _ipt_10bit(rgb_nits):
    # Invert the P5 nonlinear/linear matrices used by the generated RPU and
    # Dolby's HPE LMS -> BT.2020 matrix. Input is known scene-linear RGB nits;
    # output is IPT, not ordinary YCbCr carrying an unrelated Dolby RPU.
    # Matrices: quietvoid/dovi_tool dolby_vision/src/rpu/profiles/profile5.rs;
    # HPE matrix: haasn/libplacebo src/shaders/colorspace.c, Dolby Vision decode.
    nonlinear = [[value / 8192 for value in row] for row in
                 [[8192, 799, 1681], [8192, -933, 1091], [8192, 267, -5545]]]
    linear = [[value / 16384 for value in row] for row in
              [[17081, -349, -349], [-349, 17081, -349], [-349, -349, 17081]]]
    lms_to_rgb = [[3.06441879, -2.16597676, 0.10155818],
                  [-0.65612108, 1.78554118, -0.12943749],
                  [0.01736321, -0.04725154, 1.03004253]]
    combined = [[sum(lms_to_rgb[i][k] * linear[k][j] for k in range(3))
                 for j in range(3)] for i in range(3)]
    lms = _matvec(_inverse3(combined), [value / 10000 for value in rgb_nits])
    def pq(value):
        value = max(0, value) ** (2610 / 16384)
        return ((3424 / 4096 + 2413 / 128 * value)
                / (1 + 2392 / 128 * value)) ** (2523 / 32)
    ipt = _matvec(_inverse3(nonlinear), [pq(value) for value in lms])
    # Full range DV offsets are scaled by 1024/1023 before subtracting.
    ipt[1] += 512 / 1023
    ipt[2] += 512 / 1023
    return [round(value * 1023) for value in ipt]


def _add_dovi_configuration(data):
    # Generated MP4 keeps moov after mdat; growing moov leaves media offsets
    # intact. Add the ordinary dvcC record so production media.probe also sees
    # P5 / no-compatible-base, rather than only inspecting decoded frame RPUs.
    containers = {b"moov": 0, b"trak": 0, b"mdia": 0, b"minf": 0,
                  b"stbl": 0, b"stsd": 8}
    result, offset = bytearray(), 0
    while offset < len(data):
        size, kind = struct.unpack_from(">I4s", data, offset)
        assert 8 <= size <= len(data) - offset
        payload = data[offset + 8:offset + size]
        if kind in (b"hvc1", b"hev1"):
            # version 1.0, profile 5, level 6, RPU+BL, no EL, compat 0.
            configuration = bytes([1, 0, 10, 53]) + bytes(20)
            payload += struct.pack(">I4s", 8 + len(configuration), b"dvcC") + configuration
        elif kind in containers:
            prefix = containers[kind]
            payload = payload[:prefix] + _add_dovi_configuration(payload[prefix:])
        result += struct.pack(">I4s", 8 + len(payload), kind) + payload
        offset += size
    return bytes(result)


def _mpeg2_crc32(data):
    crc = 0xffffffff
    for value in data:
        crc ^= value << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04c11db7 if crc & 0x80000000 else 0)) & 0xffffffff
    return crc


def _p5_reference_rpu():
    # Derive a reference to mapping ID 0 from our generated RPU. Keep the full
    # sequence header and DM color payload; only replace the mapping with its
    # reference. These offsets belong to the fixed self-generated fixture above.
    # RPU layout/CRC: quietvoid/dovi_tool dolby_vision/src/rpu/{dovi_rpu,
    # rpu_data_header}.rs. CRC-32/MPEG-2 excludes the 0x19 prefix and final 0x80.
    rbsp = _P5_RPU[4:].replace(b"\x00\x00\x03", b"\x00\x00")
    assert _mpeg2_crc32(rbsp[1:-5]) == int.from_bytes(rbsp[-5:-1], "big")
    bits = "".join(f"{value:08b}" for value in rbsp)
    assert bits[66:69] == "101"  # DM present, full mapping, ue(mapping ID 0).
    # The fixed DM fields occupy 514 bits: IDs 0/0 and scene flag 1 (five
    # Exp-Golomb bits), matrices/offsets, signal parameters and source levels.
    # Its first extension-block count is followed by byte alignment. Moving
    # DM by 226 bits requires rebuilding that internal padding as well as CRC.
    assert bits[295:300] == "11010"
    count_start = 295 + 514
    count_value_start = count_start
    while bits[count_value_start] == "0":
        count_value_start += 1
    count_end = count_value_start + (count_value_start - count_start) + 1
    blocks_start = (count_end + 7) // 8 * 8
    assert not int(bits[count_end:blocks_start] or "0", 2)
    # Preserve DM, with use_prev_vdr_rpu=1 and ue(previous ID 0).
    referenced = bits[:67] + "11" + bits[295:count_end]
    referenced += "0" * (-len(referenced) % 8)
    referenced += bits[blocks_start:-40]
    referenced += "0" * (-len(referenced) % 8)
    payload = int(referenced, 2).to_bytes(len(referenced) // 8, "big")
    payload += _mpeg2_crc32(payload[1:]).to_bytes(4, "big") + b"\x80"
    escaped = bytearray()
    zeros = 0
    for value in payload:
        if zeros == 2 and value <= 3:
            escaped.append(3)
            zeros = 0
        escaped.append(value)
        zeros = zeros + 1 if value == 0 else 0
    return bytes(escaped)


def test_referenced_p5_fixture_uses_correct_mpeg2_crc_without_external_tool():
    assert _mpeg2_crc32(b"123456789") == 0x0376e6e7
    rbsp = _p5_reference_rpu().replace(b"\x00\x00\x03", b"\x00\x00")
    assert rbsp[0] == 0x19 and rbsp[-1] == 0x80
    assert _mpeg2_crc32(rbsp[1:-5]) == int.from_bytes(rbsp[-5:-1], "big")


def _write_synthetic_p5(ffmpeg, env, tmp_path, colors, rpus, *, framerate=1):
    assert len(colors) == len(rpus)
    raw = bytearray()
    for rgb in colors:
        values = _ipt_10bit(rgb)
        assert all(0 <= value <= 1023 for value in values)
        for value, count in zip(values, [64 * 64, 32 * 32, 32 * 32]):
            raw += struct.pack("<H", value) * count
    source = tmp_path / "ipt.yuv"
    source.write_bytes(raw)
    base = tmp_path / "base.hevc"
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "rawvideo", "-pixel_format", "yuv420p10le", "-video_size", "64x64",
            "-framerate", str(framerate), "-i", str(source), "-an", "-threads", "1", "-c:v", "libx265",
            "-preset", "ultrafast", "-x265-params",
            "lossless=1:pools=1:frame-threads=1:keyint=1:bframes=0:repeat-headers=1:aud=1",
            "-color_range", "pc", "-color_primaries", "bt2020", "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc", "-f", "hevc", str(base)]
    encoded = subprocess.run(args, env=env, capture_output=True, timeout=30)
    if encoded.returncode and b"Unknown encoder" in encoded.stderr:
        pytest.skip("libx265 unavailable")
    assert encoded.returncode == 0, encoded.stderr.decode(errors="replace")
    nals = [nal for nal in re.split(b"\x00\x00\x00\x01|\x00\x00\x01", base.read_bytes()) if nal]
    p5 = tmp_path / "p5.hevc"
    content, frames = bytearray(), 0
    for nal in nals:
        content += b"\x00\x00\x00\x01" + nal
        if ((nal[0] >> 1) & 63) <= 31:
            content += b"\x00\x00\x00\x01\x7c\x01" + rpus[frames]
            frames += 1
    assert frames == len(colors)
    p5.write_bytes(content)
    video = tmp_path / "p5.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(framerate),
                    "-i", str(p5), "-c", "copy", "-strict", "unofficial", str(video)],
                   env=env, capture_output=True, check=True, timeout=30)
    video.write_bytes(_add_dovi_configuration(video.read_bytes()))
    return video


@pytest.fixture
def synthetic_p5(software_vulkan, tmp_path, require_dovi_metadata):
    ffmpeg, env = software_vulkan
    require_dovi_metadata(ffmpeg, env)
    video = _write_synthetic_p5(ffmpeg, env, tmp_path, _COLORS,
                                [_P5_RPU[4:]] * len(_COLORS))
    return ffmpeg, env, video


@pytest.fixture
def synthetic_p5_references(tmp_path, require_dovi_metadata):
    # No Vulkan dependency: the regression concerns decoder RPU state before
    # conversion. All I-frames make seek(5) land on an independently decodable
    # picture while its RPU still refers to the full mapping at time 0.
    ffmpeg = shutil.which("ffmpeg") or media._static_bins_if_present().get("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    env = dict(os.environ, LP_NUM_THREADS="2")
    require_dovi_metadata(ffmpeg, env)
    reference = _p5_reference_rpu()
    rpus = [_P5_RPU[4:] if frame in (0, 10) else reference for frame in range(12)]
    video = _write_synthetic_p5(ffmpeg, env, tmp_path, _COLORS * 2, rpus)
    return ffmpeg, env, video


def test_real_p5_referenced_rpu_needs_bounded_preroll_at_the_same_target(
        synthetic_p5_references):
    ffmpeg, env, video = synthetic_p5_references

    def timestamps(seek, duration, low, high, *, guarded=True):
        vf = f"select='between(t,{low},{high})',"
        if guarded:
            vf += color_plan({"dv_profile": 5}, 64, 64)["frame_guard"] + ","
        vf += "showinfo=checksum=0"
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin", "-threads", "1",
             "-filter_threads", "1", "-ss", str(seek), "-t", str(duration), "-i", str(video),
             "-an", "-vf", vf, "-frames:v", "1", "-threads", "1", "-f", "null", "-"],
            env=env, capture_output=True, check=True, timeout=15)
        return tuple(float(value) for value in re.findall(
            rb"\bn:\s*\d+\s+pts:[^\r\n]*?\bpts_time:(\S+)", result.stderr))

    # A real video frame exists at the target, but its reference RPU cannot be
    # resolved after a fresh seek; a later full RPU must not substitute for it.
    assert timestamps(5, 0.5, 0, 0.5, guarded=False) == (0.0,)
    assert timestamps(5, 0.5, 0, 0.5) == ()
    assert timestamps(5, 6, 0, 6) == (5.0,)  # Absolute time 10, not target 5.
    # Decode only five seconds before the target. Selection still outputs the
    # same target picture, now carrying parsed metadata from the reference RPU.
    assert timestamps(0, 5.5, 5, 5.5) == (5.0,)


def test_real_p5_production_preroll_preserves_target_and_converted_pixels(
        synthetic_p5_references, software_vulkan, tmp_path, monkeypatch):
    from threading import Event, RLock
    from types import SimpleNamespace

    from app.jobkit import JobRegistry
    from app.routers.stream import preview_extract as ex
    from app.routers.stream import previews as pv

    ffmpeg, _env, video = synthetic_p5_references
    assert software_vulkan[0] == ffmpeg
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: ffmpeg)
    monkeypatch.setattr(pv, "_jobs", JobRegistry(prefix="p5-color-test"))
    monkeypatch.setattr(pv, "_sessions", {})
    monkeypatch.setattr(pv, "_sess_lock", RLock())
    # Isolate cancellation state instead of clearing the application's event.
    monkeypatch.setattr(pv, "_stop", Event())
    monkeypatch.setenv("PREVIEW_THREADS", "1")
    jid = pv._jobs.create(library_id=1)["job_id"]
    conversion = color_plan({"dv_profile": 5}, 64, 64)
    calls = []

    def run(cmd, job_id, timeout=60):
        log = pv._run(cmd, job_id, timeout)
        calls.append((cmd, timeout, log.frame_times))
        return log

    worker = ex.Extractor(
        SimpleNamespace(input=str(video)), conversion["filter"], 1,
        remote=True, run=run, jid=jid, progress=lambda _frames: None,
        input_args=conversion["input_args"], software=True,
        frame_guard=conversion["frame_guard"])
    recovered = tmp_path / "recovered.png"
    assert worker._sample_point(7, recovered, nearby=False) == "accurate"
    assert recovered.is_file() and recovered.stat().st_size
    # The fresh seek cannot resolve metadata. Recovery is bounded and must
    # decode earlier frames, then output the original target rather than time 10.
    assert 2 <= len(calls) <= 5
    assert float(calls[0][0][calls[0][0].index("-ss") + 1]) == 7
    assert calls[0][2] == ()
    for cmd, timeout, _times in calls:
        assert "-t" in cmd and 0 < float(cmd[cmd.index("-t") + 1]) <= 32
        assert 0 < timeout <= 60
        assert conversion["frame_guard"] in cmd[cmd.index("-vf") + 1]
    last_cmd, _timeout, last_times = calls[-1]
    last_seek = float(last_cmd[last_cmd.index("-ss") + 1])
    assert last_seek == 0 and last_seek + last_times[0] == pytest.approx(7)

    reference = tmp_path / "reference.png"
    reference_log = pv._run(
        [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin", "-y",
         *conversion["input_args"], "-threads", "1", "-filter_threads", "1",
         "-ss", "0", "-t", "7.5", "-i", str(video), "-map", "0:v:0", "-an", "-sn", "-dn",
         "-vf", "select='between(t,7,7.5)'," + conversion["frame_guard"]
         + ",showinfo=checksum=0," + conversion["filter"],
         "-frames:v", "1", "-fps_mode", "passthrough", "-threads", "1", "-c:v", "png",
         "-pix_fmt", "rgb24", "-compression_level", "1", str(reference)], jid, timeout=30)
    assert reference_log.frame_times[0] == pytest.approx(7)

    pixels = []
    for image in (recovered, reference):
        decoded = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1",
             "-filter_threads", "1", "-t", "1", "-i", str(image), "-frames:v", "1",
             "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
            capture_output=True, check=True, timeout=15)
        assert len(decoded.stdout) == 64 * 64 * 3
        pixels.append(decoded.stdout)
    assert pixels[0] == pixels[1]
    center = pixels[0][(32 * 64 + 32) * 3:][:3]
    assert center[1] > max(center[0], center[2]) + 80  # Time 7 is the green fixture.


@pytest.fixture
def synthetic_p5_references_24fps(software_vulkan, tmp_path, require_dovi_metadata):
    import math

    ffmpeg, env = software_vulkan
    require_dovi_metadata(ffmpeg, env)
    # Three seconds of smoothly changing known IPT colors. The target at 1.5s
    # has multiple subsequent pictures inside the 0.1s recovery window; unlike
    # the 1fps fixture this exposes adjacent-frame selection or mixing errors.
    colors = [(45 + 30 * math.sin(frame / 12),
               45 + 30 * math.sin(frame / 12 + 2),
               45 + 30 * math.sin(frame / 12 + 4)) for frame in range(72)]
    reference = _p5_reference_rpu()
    rpus = [_P5_RPU[4:] if frame in (0, 60) else reference for frame in range(72)]
    video = _write_synthetic_p5(ffmpeg, env, tmp_path, colors, rpus, framerate=24)
    return ffmpeg, env, video


def test_real_p5_24fps_preroll_outputs_the_same_first_converted_picture(
        synthetic_p5_references_24fps, tmp_path, monkeypatch):
    from threading import Event, RLock
    from types import SimpleNamespace

    from app.jobkit import JobRegistry
    from app.routers.stream import preview_extract as ex
    from app.routers.stream import previews as pv

    ffmpeg, _env, video = synthetic_p5_references_24fps
    monkeypatch.setattr(media, "ffmpeg_bin", lambda: ffmpeg)
    monkeypatch.setattr(pv, "_jobs", JobRegistry(prefix="p5-24fps-test"))
    monkeypatch.setattr(pv, "_sessions", {})
    monkeypatch.setattr(pv, "_sess_lock", RLock())
    monkeypatch.setattr(pv, "_stop", Event())
    monkeypatch.setenv("PREVIEW_THREADS", "2")
    jid = pv._jobs.create(library_id=1)["job_id"]
    conversion = color_plan({"dv_profile": 5}, 64, 64)
    observations = []

    def frames(log, name):
        result = []
        for line in str(log).splitlines():
            if f"showinfo@{name} " not in line or " pts_time:" not in line:
                continue
            pts = re.search(r"\bpts_time:(\S+)", line)
            checksum = re.search(r"\bchecksum:([0-9A-Fa-f]+)", line)
            assert pts and checksum, line
            result.append((float(pts[1]), checksum[1]))
        return result

    def instrument(vf):
        # Diagnostic showinfo filters preserve pixels and timestamps. Keeping
        # the second one after conversion identifies the actual PNG picture.
        return (vf.replace("showinfo=checksum=0", "showinfo@source=checksum=1")
                + ",showinfo@converted=checksum=1")

    def run(cmd, job_id, timeout=60):
        cmd = list(cmd)
        vf = cmd.index("-vf") + 1
        cmd[vf] = instrument(cmd[vf])
        log = pv._run(cmd, job_id, timeout)
        observations.append({"cmd": cmd, "source": frames(log, "source"),
                             "converted": frames(log, "converted")})
        return log

    worker = ex.Extractor(
        SimpleNamespace(input=str(video)), conversion["filter"], 0.1,
        remote=True, run=run, jid=jid, progress=lambda _frames: None,
        input_args=conversion["input_args"], software=True,
        frame_guard=conversion["frame_guard"])
    recovered = tmp_path / "recovered-24fps.png"
    assert worker._sample_point(1.5, recovered, nearby=False) == "accurate"
    assert 2 <= len(observations) <= 5 and observations[0]["source"] == []
    actual = observations[-1]
    assert len(actual["source"]) >= 2
    assert actual["source"][0][0] == pytest.approx(1.5, abs=0.001)
    assert actual["converted"][0][0] == pytest.approx(1.5, abs=0.001)
    for observation in observations:
        cmd = observation["cmd"]
        assert 0 < float(cmd[cmd.index("-t") + 1]) <= 32
        assert cmd[cmd.index("-threads") + 1] == "2"

    reference = tmp_path / "reference-24fps.png"
    vf = ("select='between(t,1.5,1.6)'," + conversion["frame_guard"]
          + ",showinfo=checksum=0," + conversion["filter"])
    log = pv._run(
        [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin", "-y",
         *conversion["input_args"], "-threads", "2", "-filter_threads", "1",
         "-ss", "0", "-t", "1.6", "-i", str(video), "-map", "0:v:0", "-an", "-sn", "-dn",
         "-vf", instrument(vf), "-frames:v", "1", "-fps_mode", "passthrough",
         "-threads", "1", "-c:v", "png", "-pix_fmt", "rgb24", "-compression_level", "1",
         str(reference)], jid, timeout=30)
    assert frames(log, "source")[0] == actual["source"][0]
    assert frames(log, "converted")[0] == actual["converted"][0]

    pixels = []
    for image in (recovered, reference):
        decoded = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1",
             "-filter_threads", "1", "-t", "1", "-i", str(image), "-frames:v", "1",
             "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
            capture_output=True, check=True, timeout=15)
        assert len(decoded.stdout) == 64 * 64 * 3
        pixels.append(decoded.stdout)
    assert pixels[0] == pixels[1]


def test_real_synthetic_p5_converts_known_ipt_colors_and_gray_levels(synthetic_p5):
    ffmpeg, env, video = synthetic_p5
    result_plan = color_plan({"dv_profile": 5}, 64, 64)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *result_plan["input_args"],
           "-threads", "1", "-filter_threads", "1", "-i", str(video), "-vf",
           result_plan["frame_guard"] + "," + result_plan["filter"],
           "-fps_mode", "passthrough", "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    converted = subprocess.run(cmd, env=env, capture_output=True, check=True, timeout=30).stdout
    assert len(converted) == len(_COLORS) * 64 * 64 * 3
    centers = [converted[(i * 64 * 64 + 32 * 64 + 32) * 3:][:3]
               for i in range(len(_COLORS))]
    for channel, pixel in enumerate(centers[:3]):
        assert pixel[channel] > max(v for i, v in enumerate(pixel) if i != channel) + 80
    for pixel in centers[3:]:
        assert max(pixel) - min(pixel) <= 2
    assert centers[3][0] + 30 < centers[4][0] < centers[5][0] - 20

    # The embedded container configuration and actual frame RPU must agree;
    # this proves the test exercises production P5 detection, not a mocked flag.
    ffprobe = shutil.which("ffprobe") or media._static_bins_if_present().get("ffprobe")
    if ffprobe:
        probe = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_frames",
                                "-of", "json", str(video)], capture_output=True, check=True, timeout=30)
        metadata = json.loads(probe.stdout)
        configs = metadata["streams"][0]["side_data_list"]
        assert any(sd.get("dv_profile") == 5 and sd.get("dv_bl_signal_compatibility_id") == 0
                   for sd in configs)
        assert len(metadata["frames"]) == len(_COLORS)
        for frame in metadata["frames"]:
            assert any(sd.get("signal_color_space") == 2 and sd.get("disable_residual_flag") == 1
                       for sd in frame.get("side_data_list", []))
