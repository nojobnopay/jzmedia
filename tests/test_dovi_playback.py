"""P5 计划、硬件命令和分阶段能力的隔离回归；不启动 FFmpeg 或接触片源。"""
import importlib
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.playback import backend, dovi
from app import transcode

planner = importlib.import_module("app.playback.plan")
commands = importlib.import_module("app.playback.cmd")


@pytest.fixture(autouse=True)
def isolated_hardware(monkeypatch):
    monkeypatch.setattr(backend, "hw_backend", lambda: "vaapi")
    monkeypatch.setattr(backend, "hw_can_tonemap", lambda: True)
    monkeypatch.setattr(backend, "dovi_capabilities", lambda: {"available": True})
    monkeypatch.setattr(commands, "ffmpeg_bin", lambda: "/test/ffmpeg")
    monkeypatch.setenv("DOVI_P5_WEB_ENABLED", "true")
    monkeypatch.delenv("DOVI_AUTO_HEIGHT", raising=False)
    monkeypatch.delenv("HLS_SEGMENT_TYPE", raising=False)
    dovi.reset_capabilities_cache()
    yield
    dovi.reset_capabilities_cache()


def media(**updates):
    value = {"playable": True, "container": "matroska", "width": 3840, "height": 1604,
             "vcodec": "hevc", "bit_depth": 10, "dv_profile": 5, "dv_bl_compat": 0,
             "audio": [{"codec": "aac", "channels": 2, "lang": "eng", "default": 1}],
             "subs": []}
    return {**value, **updates}


def caps(**updates):
    return {"mse": True, "video": {"h264": True, "hevc10": True},
            "audio": {"aac": True}, "hdr_decode": True, **updates}


def decision(**kwargs):
    return planner.plan(media(), caps=caps(), **kwargs)


def test_p5_uses_own_pipeline_even_when_browser_decodes_hevc_and_hdr():
    result = decision()
    assert result["method"] == "video_transcode"
    assert result["plan"]["height"] == 720
    assert result["plan"]["color_pipeline"] == "dovi_p5_sdr"
    assert result["plan"]["color_revision"] == 1
    assert result["plan"]["encoder_backend"] == "vaapi"
    assert result["plan"]["audios"][0]["copy"] is False
    assert "dovi_no_base_tonemap" not in result["reasons"]


@pytest.mark.parametrize("configured", [None, "", "false", "0", "off", "unexpected"])
def test_web_p5_requires_explicit_enablement_without_probing(monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("DOVI_P5_WEB_ENABLED", raising=False)
    else:
        monkeypatch.setenv("DOVI_P5_WEB_ENABLED", configured)
    def forbidden():
        raise AssertionError("disabled P5 playback must not probe hardware")
    monkeypatch.setattr(backend, "dovi_capabilities", forbidden)
    result = decision()
    assert result["method"] == "blocked"
    assert "dovi_p5_web_disabled" in result["reasons"]
    assert result["plan"]["tonemap"] is False
    assert result["plan"]["color_pipeline"] == dovi.COLOR_PIPELINE


@pytest.mark.parametrize("configured", ["1", "true", "TRUE", " yes ", "on"])
def test_explicit_enablement_keeps_the_guarded_pipeline(monkeypatch, configured):
    monkeypatch.setenv("DOVI_P5_WEB_ENABLED", configured)
    result = decision()
    assert result["method"] == "video_transcode"
    assert result["plan"]["color_pipeline"] == dovi.COLOR_PIPELINE


def test_disabled_web_p5_does_not_change_other_video_hardware_plans(monkeypatch):
    monkeypatch.setenv("DOVI_P5_WEB_ENABLED", "false")
    result = planner.plan(media(dv_profile=0, hdr="hdr10"),
                          caps=caps(video={"h264": True}, hdr_decode=False))
    assert result["method"] == "video_transcode"
    assert result["plan"]["tonemap"] is True
    assert "color_pipeline" not in result["plan"]
    assert planner.plan(media(), caps=caps(), client="kodi")["method"] == "direct"


def test_synthetic_capabilities_cannot_raise_auto_ceiling(monkeypatch):
    assert decision()["plan"]["height"] == 720
    monkeypatch.setenv("DOVI_AUTO_HEIGHT", "1080")
    assert decision()["plan"]["height"] == 1080
    monkeypatch.setenv("DOVI_AUTO_HEIGHT", "2160")
    assert decision()["plan"]["height"] == 720


@pytest.mark.parametrize(("quality", "height"), [("source", 0), ("1080p", 1080), ("720p", 720)])
def test_explicit_quality_remains_explicit(quality, height):
    assert decision(quality=quality)["plan"]["height"] == height


def test_auto_does_not_upscale_small_p5():
    assert planner.plan(media(height=480), caps=caps())["plan"]["height"] == 480


def test_missing_p5_hardware_blocks_instead_of_ordinary_tonemap(monkeypatch):
    monkeypatch.setattr(backend, "dovi_capabilities", lambda: {"available": False})
    result = decision()
    assert result["method"] == "blocked"
    assert "dovi_p5_unavailable" in result["reasons"]
    assert result["plan"]["tonemap"] is False


@pytest.mark.parametrize("compat", [{"hls_compat": True}, {"mp4_compat": True}])
def test_packaging_fallbacks_are_explicitly_blocked(compat):
    result = planner.plan(media(), caps=caps(**compat))
    assert result["method"] == "blocked"
    assert "dovi_p5_packaging_unsupported" in result["reasons"]


def test_ts_environment_cannot_bypass_p5_color_guard(monkeypatch):
    monkeypatch.setenv("HLS_SEGMENT_TYPE", "ts")
    result = decision()
    assert result["method"] == "blocked"
    assert "dovi_p5_packaging_unsupported" in result["reasons"]


def test_p5_picture_burn_blocks_but_client_pgs_does_not():
    source = media(subs=[{"codec": "pgs", "image": 1}])
    result = planner.plan(source, caps=caps(), sub_idx=0, force_burn=True)
    assert result["method"] == "blocked"
    assert "dovi_p5_burn_unsupported" in result["reasons"]
    assert planner.plan(source, caps=caps(), sub_idx=0)["method"] == "video_transcode"


def test_invalid_p5_source_is_not_treated_as_hdr10():
    result = planner.plan(media(bit_depth=8), caps=caps())
    assert result["method"] == "blocked"
    assert "dovi_p5_source_unsupported" in result["reasons"]


@pytest.mark.parametrize("compat", [1, 2, "malformed"])
def test_contradictory_p5_compatibility_cannot_enable_direct_play(compat):
    result = planner.plan(media(container="mp4", dv_bl_compat=compat), caps=caps())
    assert result["method"] == "blocked"
    assert "dovi_p5_source_unsupported" in result["reasons"]


def test_other_formats_keep_existing_plan_shape():
    result = planner.plan(media(dv_profile=8, dv_bl_compat=1), caps=caps())
    assert result["method"] == "remux"
    assert not any(key in result["plan"] for key in
                   ("color_pipeline", "color_revision", "encoder_backend", "decoder", "tone_mapper"))
    assert planner.plan(media(), caps=caps(), client="kodi")["method"] == "direct"


def test_p5_command_preserves_native_surfaces_until_dolby_mapping(monkeypatch):
    def forbidden_detect(*args, **kwargs):
        raise AssertionError("P5 must not select generic/software transcoder")
    monkeypatch.setattr(transcode, "detect", forbidden_detect)
    cmd = commands.build_cmd("/isolated/source.mkv", decision()["plan"], start=100)
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("format=vaapi,trim=")
    assert "trim=start=15.000000,showinfo@dovi_input=checksum=0,libplacebo=" in vf
    assert "setpts=" not in vf
    assert cmd[cmd.index("-bsf:v") + 1] == (
        "setts=pts=PTS-floor(15.000000/TB+0.5):dts=DTS-floor(15.000000/TB+0.5):"
        "duration='if(eq(NEXT_PTS,NOPTS),DURATION,NEXT_PTS-PTS)'")
    assert "hwdownload" not in vf and "scale=" not in vf and "yuv420p" not in vf
    assert vf.index("libplacebo=") < vf.index("format=nv12,hwupload")
    assert "apply_dolbyvision=1" in vf and "range=tv" in vf
    assert cmd[cmd.index("-ss") + 1] == "85.000000"
    assert cmd.index("-ss") < cmd.index("-i")
    assert cmd[cmd.index("-c:v") + 1] == "hevc"
    assert cmd[cmd.index("-bf") + 1] == "0"
    assert cmd[cmd.index("-fps_mode") + 1] == "passthrough"
    assert cmd[cmd.index("-enc_time_base:v") + 1] == "filter"
    assert cmd[cmd.index("-avoid_negative_ts") + 1] == "disabled"
    assert "h264_vaapi" in cmd and "libx264" not in cmd
    assert cmd[cmd.index("-loglevel") + 1] == "repeat+info"
    assert cmd[cmd.index("-progress") + 1] == "pipe:1"
    assert cmd[cmd.index("-filter:a:0") + 1] == "atrim=start=15.000000,asetpts=PTS-15.000000/TB"


def test_seek_retry_only_changes_internal_preroll():
    p = decision()["plan"]
    cmd = commands.build_cmd("/isolated/source.mkv", {**p, "dovi_preroll": 30}, start=100)
    assert cmd[cmd.index("-ss") + 1] == "70.000000"
    assert "trim=start=30.000000" in cmd[cmd.index("-vf") + 1]
    assert "dovi_preroll" not in p


def test_real_shared_trim_keeps_late_audio_and_video_frame_offset(tmp_path):
    """实跑共同裁剪表达式：迟入500ms的音轨不能被各自STARTPTS提前。

    仅验证时间轴，使用64×64合成画面、PCM音频及软件无损编码；不调用
    P5硬件探测或GPU，也不把此结果当作杜比颜色验收。
    """
    from app import media as media_tools
    installed = media_tools._static_bins_if_present()
    ffmpeg = shutil.which("ffmpeg") or installed.get("ffmpeg")
    ffprobe = shutil.which("ffprobe") or installed.get("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("installed FFmpeg/FFprobe required for synthetic timestamp regression")
    origin = 0.15
    p5_command = commands.build_cmd("/isolated/unused.mkv", decision()["plan"], start=origin)
    video_trim = p5_command[p5_command.index("-vf") + 1].removeprefix("format=vaapi,").split(
        ",showinfo@dovi_input", 1)[0]
    audio_trim = p5_command[p5_command.index("-filter:a:0") + 1]
    filters = (f"[0:v]settb=1/1000,setpts=PTS+20,{video_trim}[v];"
               f"[1:a]asetpts=PTS+0.5/TB,{audio_trim}[a]")
    output = tmp_path / "late-audio.mkv"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-filter_complex_threads", "1",
                    "-f", "lavfi", "-i", "color=gray:s=64x64:r=24:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.5",
                    "-filter_complex", filters, "-map", "[v]", "-map", "[a]",
                    "-c:v", "ffv1", "-threads", "1", "-fps_mode", "passthrough",
                    "-enc_time_base:v", "filter", "-bsf:v", p5_command[p5_command.index("-bsf:v") + 1],
                    "-c:a", "pcm_s16le",
                    "-avoid_negative_ts", "disabled", str(output)],
                   capture_output=True, check=True, timeout=15)
    probe = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                            "stream=codec_type,start_time", "-of", "json", str(output)],
                           capture_output=True, check=True, timeout=10)
    streams = json.loads(probe.stdout)["streams"]
    starts = {stream["codec_type"]: float(stream["start_time"]) for stream in streams}
    assert 0 < starts["video"] <= 1 / 24
    assert starts["audio"] == pytest.approx(0.5 - origin, abs=0.002)
    assert starts["audio"] - starts["video"] > 0.30


@pytest.mark.parametrize("origin", ["0.15", "0.150001", "0.1505", "4.001", "4.009"])
def test_encoded_packet_shift_preserves_fractional_source_rate_and_every_pts(tmp_path, origin):
    """Real tiny muxing: compare every encoded PTS to independent source packets."""
    from fractions import Fraction
    import math
    from app import media as media_tools
    installed = media_tools._static_bins_if_present()
    ffmpeg = installed.get("ffmpeg") or shutil.which("ffmpeg")
    ffprobe = installed.get("ffprobe") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("installed FFmpeg/FFprobe required for timestamp regression")
    source, output = tmp_path / "source.mkv", tmp_path / "shifted.m3u8"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=gray:s=64x64:r=24000/1001:d={math.floor(float(origin)) + 1}",
                    "-c:v", "ffv1", "-threads", "1",
                    str(source)], capture_output=True, check=True, timeout=15)

    def packets(path):
        result = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,time_base:packet=pts,duration", "-of", "json", str(path)],
            capture_output=True, check=True, timeout=10)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return stream, [(Fraction(p["pts"]) * Fraction(stream["time_base"]),
                         Fraction(p["duration"]) * Fraction(stream["time_base"])) for p in data["packets"]]

    source_stream, source_packets = packets(source)
    p5 = commands.build_cmd("/isolated/unused.mkv", decision()["plan"], start=float(origin))
    video_trim = p5[p5.index("-vf") + 1].removeprefix("format=vaapi,").split(",showinfo@dovi_input", 1)[0]
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source),
                    "-vf", video_trim + ",format=yuv420p", "-c:v", "libx264", "-threads", "1",
                    "-bf", "0", "-fps_mode", "passthrough", "-enc_time_base:v", "filter",
                    "-bsf:v", p5[p5.index("-bsf:v") + 1],
                    "-force_key_frames", "expr:gte(t,n_forced*0.25)",
                    "-f", "hls", "-hls_time", "0.25", "-hls_list_size", "0",
                    "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "shifted_init.mp4",
                    "-hls_segment_filename", str(tmp_path / "shifted%05d.m4s"),
                    "-hls_flags", "temp_file+independent_segments", "-avoid_negative_ts", "disabled", str(output)],
                   capture_output=True, check=True, timeout=15)
    stream, encoded = packets(output)
    assert Fraction(stream["r_frame_rate"]) == Fraction(source_stream["r_frame_rate"]) == Fraction(24000, 1001)
    quantized_offset = math.floor(float(origin) / float(Fraction(source_stream["time_base"])) + 0.5) * Fraction(source_stream["time_base"])
    expected = [(pts - quantized_offset, duration) for pts, duration in source_packets if pts >= Fraction(origin)]
    assert [pts for pts, _duration in encoded] == [pts for pts, _duration in expected]
    assert len(list(tmp_path.glob("shifted*.m4s"))) >= 2
    assert all(duration == encoded[index + 1][0] - pts
               for index, (pts, duration) in enumerate(encoded[:-1]))
    assert all(abs(actual[1] - original[1]) <= Fraction(source_stream["time_base"])
               for actual, original in zip(encoded, expected))


@pytest.mark.parametrize("override", [{"seg": "ts"}, {"seg": "mp4"}, {"sub": "burn"}, {"vcopy": True}])
def test_command_defends_invalid_internal_p5_plan(override):
    with pytest.raises(ValueError):
        commands.build_cmd("/isolated/source.mkv", {**decision()["plan"], **override})
    with pytest.raises(ValueError):
        commands.build_cmd("/isolated/source.mkv", decision()["plan"], force_sw=True)


def test_vulkan_environment_is_scoped_to_child_process(monkeypatch):
    monkeypatch.setenv("VK_DRIVER_FILES", "/software.json")
    monkeypatch.setattr(dovi, "_vulkan_driver", lambda: "/intel.json")
    env = dovi.playback_env(decision()["plan"])
    assert env["VK_DRIVER_FILES"] == "/intel.json"
    assert env["LIBVA_DRIVER_NAME"] == "iHD"
    assert env["AV_LOG_FORCE_NOCOLOR"] == "1"
    assert os.environ["VK_DRIVER_FILES"] == "/software.json"
    assert dovi.playback_env({"vcopy": False}) is None


def test_capability_cache_is_independent_and_returns_copies(monkeypatch):
    calls = []
    def detect():
        calls.append(1)
        return {"available": True, "combined": {"ok": True}}
    monkeypatch.setattr(dovi, "_detect_capabilities", detect)
    first = dovi.capabilities()
    first["combined"]["ok"] = False
    assert dovi.capabilities()["combined"]["ok"] is True
    assert len(calls) == 1
    dovi.capabilities(refresh=True)
    assert len(calls) == 2


def test_capability_snapshot_separates_configuration_from_cached_hardware(monkeypatch):
    calls = []
    def detect():
        calls.append(1)
        return {"available": True, "combined": {"ok": True}, "validated_real_p5": False}
    monkeypatch.setattr(dovi, "_detect_capabilities", detect)
    monkeypatch.delenv("DOVI_P5_WEB_ENABLED", raising=False)
    initial = dovi.capabilities()
    assert initial["available"] is True
    assert initial["enabled"] is False
    assert initial["experimental"] is True
    assert initial["validated_real_p5"] is False
    monkeypatch.setenv("DOVI_P5_WEB_ENABLED", "true")
    enabled = dovi.capabilities(probe=False)
    assert enabled["enabled"] is True
    assert enabled["available"] is True
    assert len(calls) == 1


def test_capability_detection_exercises_all_stages_without_claiming_p5_acceptance(monkeypatch):
    monkeypatch.setattr(dovi.os.path, "exists", lambda path: True)
    monkeypatch.setattr(dovi.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(dovi, "ffmpeg_bin", lambda: "/test/ffmpeg")
    commands_run = []
    def run(cmd, env):
        commands_run.append(cmd)
        return True
    monkeypatch.setattr(dovi, "_run", run)
    result = dovi.capabilities()
    assert result["available"] is True
    assert result["validated_real_p5"] is False
    assert all(result[stage]["ok"] for stage in
               ("hevc_main10_decode", "h264_encode", "intel_vulkan", "combined"))
    assert len(commands_run) == 5
    combined = commands_run[-1]
    assert "showinfo@dovi_input=checksum=0,libplacebo=" in combined[combined.index("-vf") + 1]
    assert "h264_vaapi" in combined and "hevc" in combined


def test_failed_combination_is_not_hidden_by_working_encoder(monkeypatch):
    monkeypatch.setattr(dovi.os.path, "exists", lambda path: True)
    monkeypatch.setattr(dovi.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(dovi, "ffmpeg_bin", lambda: "/test/ffmpeg")
    calls = []
    def run(cmd, env):
        calls.append(cmd)
        return len(calls) != 5
    monkeypatch.setattr(dovi, "_run", run)
    result = dovi.capabilities()
    assert result["h264_encode"]["ok"] is True
    assert result["combined"]["ok"] is False
    assert result["available"] is False
    assert result["reason"].startswith("combined:")


@pytest.mark.parametrize(("stderr", "reason"), [
    ("No usable encoding profile found.", "codec_profile_unavailable"),
    ("No support for codec hevc profile 2.", "codec_profile_unavailable"),
    ("Device creation failed: -5", "hardware_init_failed"),
    ("Cannot open render device: Permission denied", "device_permission_denied"),
    ("No such filter: 'libplacebo'", "filter_unavailable"),
    ("Unknown encoder 'h264_vaapi'", "encoder_unavailable"),
    ("Impossible to convert between the formats supported by filters", "hardware_frame_import_failed"),
    ("Unknown error at http://example.invalid/private?token=synthetic", "pipeline_failed"),
])
def test_probe_failure_classifies_and_discards_raw_log(monkeypatch, stderr, reason):
    monkeypatch.setattr(dovi.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stderr=stderr.encode()))
    assert dovi._run(["/test/ffmpeg"], {}) == {"ok": False, "reason": reason}


@pytest.mark.parametrize(("error", "reason"), [
    (subprocess.TimeoutExpired("/test/ffmpeg", 8), "timeout"),
    (FileNotFoundError("/unavailable/ffmpeg"), "ffmpeg_unavailable"),
    (OSError("private process launch information"), "process_start_failed"),
])
def test_probe_process_errors_have_safe_reason(monkeypatch, error, reason):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(dovi.subprocess, "run", fail)
    assert dovi._run(["/test/ffmpeg"], {}) == {"ok": False, "reason": reason}


def test_kernel_engine_diagnostic_only_accepts_engine_names(monkeypatch):
    monkeypatch.setattr(dovi.glob, "glob", lambda pattern: [
        "/sys/class/drm/renderD128/device/drm/card0",
        "/sys/class/drm/renderD128/device/drm/card0-DP-1"])
    monkeypatch.setattr(dovi.os, "listdir", lambda path:
                        ["rcs0", "bcs0", "vecs0", "vcs0", "secret-label", "../private"])
    assert dovi._kernel_video_engines("/dev/dri/renderD128") == ["bcs0", "rcs0", "vcs0", "vecs0"]
    assert dovi._kernel_video_engines("/non-render-device") == []


def test_kernel_engine_permission_failure_is_optional_diagnostic(monkeypatch):
    monkeypatch.setattr(dovi.glob, "glob", lambda pattern: ["/sys/class/drm/card0"])
    def unreadable(path):
        raise PermissionError("sysfs unavailable")
    monkeypatch.setattr(dovi.os, "listdir", unreadable)
    assert dovi._kernel_video_engines("/dev/dri/renderD128") == []


def test_stages_preserve_safe_classification_without_using_sysfs_as_gate(monkeypatch):
    monkeypatch.setattr(dovi.os.path, "exists", lambda path: True)
    monkeypatch.setattr(dovi.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(dovi, "ffmpeg_bin", lambda: "/test/ffmpeg")
    monkeypatch.setattr(dovi, "_kernel_video_engines", lambda device: [])
    monkeypatch.setattr(dovi, "_run", lambda cmd, env: {"ok": True, "reason": "synthetic_smoke_passed"})
    passed = dovi.capabilities()
    assert passed["kernel_video_engines"] == []
    assert passed["available"] is True
    def run(cmd, env):
        if "h264_vaapi" in cmd:
            return {"ok": False, "reason": "codec_profile_unavailable"}
        return {"ok": True, "reason": "synthetic_smoke_passed"}
    monkeypatch.setattr(dovi, "_run", run)
    failed = dovi.capabilities(refresh=True)
    assert failed["h264_encode"] == {"ok": False, "reason": "codec_profile_unavailable"}
    assert failed["combined"]["reason"] == "codec_profile_unavailable"
    assert failed["reason"] == "h264_encode: codec_profile_unavailable"


def test_unknown_runner_reason_cannot_expose_log_or_url():
    assert dovi._outcome({"ok": False, "reason": "http://example.invalid/private"}) == {
        "ok": False, "reason": "pipeline_failed"}


def test_health_backend_snapshot_starts_no_dovi_process(monkeypatch):
    monkeypatch.setattr(transcode, "detect", lambda refresh=False: transcode.Backend("", "test"))
    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary backend/health information must not start P5 probes")
    monkeypatch.setattr(dovi.subprocess, "run", forbidden)
    result = transcode.backend_info()["dovi_p5"]
    assert result["available"] is False
    assert result["validated_real_p5"] is False
    assert result["reason"] == "not_tested"
    assert all(result[stage] == {"ok": False, "reason": "not_tested"} for stage in
               ("hevc_main10_decode", "h264_encode", "intel_vulkan", "combined"))


def test_snapshot_does_not_wait_for_probe_lock_or_populate_cache(monkeypatch):
    def forbidden():
        raise AssertionError("snapshot must not probe")
    monkeypatch.setattr(dovi, "_detect_capabilities", forbidden)
    with dovi._lock:
        snapshot = dovi.capabilities(probe=False)
    assert snapshot["reason"] == "not_tested"
    assert dovi._cache is None


def test_explicit_backend_refresh_runs_probe_then_health_uses_cached_copy(monkeypatch):
    monkeypatch.setattr(transcode, "detect", lambda refresh=False: transcode.Backend("vaapi", "test"))
    calls = []
    def detect():
        calls.append(1)
        return {"available": True, "combined": {"ok": True}, "validated_real_p5": False}
    monkeypatch.setattr(dovi, "_detect_capabilities", detect)
    initial = transcode.backend_info(refresh=True)["dovi_p5"]
    assert initial["available"] is True
    assert len(calls) == 1
    initial["combined"]["ok"] = False
    with dovi._lock:
        snapshot = transcode.backend_info()["dovi_p5"]
    assert snapshot["combined"]["ok"] is True
    assert len(calls) == 1
