"""Dolby Vision P5 的专用硬件播放链与分阶段环境探测。

合成 Main10 探测仅证明驱动和滤镜组合能工作，不代表已验证真实 RPU、
颜色或实时性能。播放的逐帧 RPU 审计和发布守卫由会话层负责。
"""
import copy
import glob
import os
import re
import subprocess
import tempfile
import threading

from ..media import ffmpeg_bin

COLOR_PIPELINE = "dovi_p5_sdr"
COLOR_REVISION = 1
_TIMEOUT = 8
_lock = threading.Lock()
_cache = None
_REASONS = {"synthetic_smoke_passed", "codec_profile_unavailable", "hardware_init_failed",
            "device_permission_denied", "filter_unavailable", "encoder_unavailable",
            "hardware_frame_import_failed", "timeout", "ffmpeg_unavailable",
            "process_start_failed", "pipeline_failed"}


def is_p5_plan(plan: dict | None) -> bool:
    return (plan or {}).get("color_pipeline") == COLOR_PIPELINE


def _vulkan_driver() -> str:
    # 限定 ANV，不能落到 HASVK 或 llvmpipe。兼容 Debian 不同架构的文件名。
    paths = glob.glob("/usr/share/vulkan/icd.d/intel_icd*.json")
    return sorted(paths)[0] if paths else "/usr/share/vulkan/icd.d/intel_icd.json"


def playback_env(plan: dict | None = None) -> dict | None:
    """仅传给 P5 FFmpeg 子进程，不修改应用及缩略图的环境。"""
    if plan is not None and not is_p5_plan(plan):
        return None
    env = os.environ.copy()
    env.update(VK_DRIVER_FILES=_vulkan_driver(), LIBVA_DRIVER_NAME="iHD",
               AV_LOG_FORCE_NOCOLOR="1")
    # 老 Vulkan loader 使用旧变量；同时限定它，防止继承软件设备覆盖配置。
    env["VK_ICD_FILENAMES"] = env["VK_DRIVER_FILES"]
    return env


def auto_height() -> int:
    """只有实际片源验收后才配置 1080；合成检测不能自动升档。"""
    return 1080 if os.getenv("DOVI_AUTO_HEIGHT", "").strip() == "1080" else 720


def web_enabled() -> bool:
    """网页 P5 转换需显式启用；不限制电视原视频或独立硬件探测。"""
    return os.getenv("DOVI_P5_WEB_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def input_args(device: str | None = None) -> list[str]:
    from ..transcode import VAAPI_DEVICE
    dev = device or VAAPI_DEVICE
    return ["-init_hw_device", f"vaapi=dovi:{dev}", "-filter_hw_device", "dovi",
            "-hwaccel", "vaapi", "-hwaccel_device", "dovi",
            "-hwaccel_output_format", "vaapi", "-c:v", "hevc", "-threads", "2"]


def color_filter(height: int = 0) -> str:
    # VAAPI 的原始 Main10 surface 由 libplacebo 导入；这里不能插入普通
    # scale/format/hwdownload。指定 NV12 输出后才上传回原 VAAPI 编码设备。
    size = f"w=trunc(oh*a/2)*2:h={int(height)}:" if height else ""
    return ("libplacebo=" + size + "format=nv12:apply_dolbyvision=1:"
            "colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:"
            "peak_detect=0:contrast_recovery=0:apply_filmgrain=0:"
            "upscaler=none:downscaler=mitchell,format=nv12,hwupload")


def video_args(height: int, trim_after: float) -> list[str]:
    # 显式硬件格式约束：原生 HEVC 硬解失败时 FFmpeg 自身可能改用软件，
    # 该约束令其无法悄悄进入 libplacebo 的软件帧上传路径。
    # FFmpeg 7.1 setpts 会清空 AVFrame.duration 和滤镜帧率；先审计原始
    # 时间戳并编码，再用 setts 仅平移编码包的 PTS/DTS，保留帧率与时长。
    vf = (f"format=vaapi,trim=start={trim_after:.6f},"
          "showinfo@dovi_input=checksum=0," + color_filter(height))
    args = ["-vf", vf, "-c:v", "h264_vaapi", "-qp", "23", "-profile:v", "high",
            "-bf", "0", "-fps_mode", "passthrough", "-enc_time_base:v", "filter",
            "-color_primaries", "bt709",
            "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    # 将共同原点一次就近量化为整数 tick，避免 setts 的逐帧 llrint 在半 tick
    # seek 时按奇偶产生不同移位。时长取真实下一 PTS 差，避免 Matroska
    # 舍入到 41ms 的 duration 在每个分片边界累积漂移；末帧保留原时长。
    args += ["-bsf:v", f"setts=pts=PTS-floor({trim_after:.6f}/TB+0.5):"
             f"dts=DTS-floor({trim_after:.6f}/TB+0.5):"
             "duration='if(eq(NEXT_PTS,NOPTS),DURATION,NEXT_PTS-PTS)'"]
    return args


def _failure_reason(stderr: bytes | str) -> str:
    """将受控合成探测日志归类；不向诊断或日志返回原文、路径或 URL。"""
    message = (stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes)
               else str(stderr or "")).lower()
    if any(marker in message for marker in (
            "no usable encoding profile", "no usable encoding entrypoint",
            "no support for codec", "profile is not supported", "not supported by driver")):
        return "codec_profile_unavailable"
    if "permission denied" in message or "operation not permitted" in message:
        return "device_permission_denied"
    if "unknown encoder" in message or "encoder not found" in message:
        return "encoder_unavailable"
    if "no such filter" in message or "option not found" in message:
        return "filter_unavailable"
    if any(marker in message for marker in (
            "failed mapping input frame", "failed to map", "impossible to convert between",
            "unsupported format for", "invalid output format")):
        return "hardware_frame_import_failed"
    if any(marker in message for marker in (
            "device creation failed", "failed to initialise", "failed to initialize",
            "failed creating vulkan", "failed importing vulkan", "no va display",
            "failed to open", "cannot open", "failed to get number of platforms")):
        return "hardware_init_failed"
    return "pipeline_failed"


def _outcome(value) -> dict:
    # 保持已有 bool 探测替身兼容，公开字段仅接受固定错误码。
    ok = bool(value.get("ok")) if isinstance(value, dict) else bool(value)
    reason = (value.get("reason") if isinstance(value, dict) else
              "synthetic_smoke_passed" if ok else "pipeline_failed")
    if reason not in _REASONS:
        reason = "synthetic_smoke_passed" if ok else "pipeline_failed"
    return {"ok": ok, "reason": reason}


def _run(cmd: list[str], env: dict) -> dict:
    try:
        result = subprocess.run(cmd, env=env, timeout=_TIMEOUT, check=False,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        ok = result.returncode == 0
        return {"ok": ok, "reason": "synthetic_smoke_passed" if ok else
                _failure_reason(getattr(result, "stderr", b""))}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "timeout"}
    except FileNotFoundError:
        return {"ok": False, "reason": "ffmpeg_unavailable"}
    except OSError:
        return {"ok": False, "reason": "process_start_failed"}


def _kernel_video_engines(device: str) -> list[str]:
    """尽力读取同一 render 设备的内核引擎名，仅诊断，不参与能力门控。"""
    node = os.path.basename(device)
    if not re.fullmatch(r"renderD\d+", node):
        return []
    engines = set()
    try:
        cards = glob.glob(f"/sys/class/drm/{node}/device/drm/card[0-9]*")
        for card in cards:
            if not re.fullmatch(r"card\d+", os.path.basename(card)):
                continue
            try:
                entries = os.listdir(os.path.join(card, "engine"))
            except OSError:
                continue
            engines.update(name for name in entries
                           if re.fullmatch(r"(?:rcs|bcs|vcs|vecs|ccs)\d+", name))
    except OSError:
        pass
    return sorted(engines)


def _untested_capabilities() -> dict:
    from ..transcode import VAAPI_DEVICE
    names = ("hevc_main10_decode", "h264_encode", "intel_vulkan", "combined")
    result = {name: {"ok": False, "reason": "not_tested"} for name in names}
    result.update(available=False, device=VAAPI_DEVICE, vulkan_driver=_vulkan_driver(),
                  validated_real_p5=False, reason="not_tested", kernel_video_engines=[])
    return result


def _detect_capabilities() -> dict:
    from ..transcode import VAAPI_DEVICE
    names = ("hevc_main10_decode", "h264_encode", "intel_vulkan", "combined")
    result = _untested_capabilities()
    result["kernel_video_engines"] = _kernel_video_engines(VAAPI_DEVICE)
    pref = (os.getenv("TRANSCODER") or os.getenv("HW_ACCEL") or "auto").lower().strip()
    if pref not in ("auto", "vaapi", ""):
        result["reason"] = "P5 requires VAAPI; disabled by transcoder configuration"
        return result
    if not os.path.exists(VAAPI_DEVICE):
        result["reason"] = "VAAPI render device unavailable"
        return result
    if not os.path.isfile(result["vulkan_driver"]):
        result["reason"] = "Intel ANV Vulkan driver unavailable"
        return result
    env = playback_env()
    base = [ffmpeg_bin(), "-y", "-nostdin", "-hide_banner", "-loglevel", "error"]
    source = ["-f", "lavfi", "-i", "color=gray:s=64x64:r=5:d=0.2,format=yuv420p10le"]
    device = ["-init_hw_device", f"vaapi=dovi:{VAAPI_DEVICE}",
              "-filter_hw_device", "dovi"]
    null = ["-frames:v", "1", "-f", "null", "-"]

    def record(name: str, command: list[str]) -> bool:
        result[name] = _outcome(_run(command, env))
        return result[name]["ok"]

    record("h264_encode", base + device + source +
           ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-bf", "0"] + null)
    record("intel_vulkan", base + source +
           ["-vf", color_filter().removesuffix(",hwupload")] + null)
    # 夹具不访问真实媒体，最多一个64×64 Main10帧；限制 x265 的线程和队列。
    with tempfile.TemporaryDirectory(prefix="jzmedia-dovi-smoke-") as temp:
        clip = os.path.join(temp, "main10.mkv")
        encoded = _outcome(_run(base + source + ["-frames:v", "1", "-c:v", "libx265",
                           "-preset", "ultrafast", "-x265-params",
                           "pools=1:frame-threads=1:log-level=error", "-pix_fmt", "yuv420p10le", clip], env))
        if encoded["ok"]:
            decode = base + input_args() + ["-i", clip]
            record("hevc_main10_decode", decode + ["-vf", "hwdownload,format=p010le"] + null)
            record("combined", decode + video_args(64, 0) + null)
        else:
            for name in ("hevc_main10_decode", "combined"):
                result[name] = {"ok": False, "reason": encoded["reason"]}
    result["available"] = all(result[name]["ok"] for name in names)
    result["reason"] = ("synthetic hardware stages passed; real P5 validation required"
                        if result["available"] else next(
                            f"{name}: {result[name]['reason']}" for name in names if not result[name]["ok"]))
    return result


def capabilities(refresh: bool = False, *, probe: bool = True) -> dict:
    """显式探测或读取快照；健康检查不能启动或等待合成探测。"""
    global _cache
    if not probe:
        # 探测在锁内构建局部结果后一次性发布缓存，快照可安全读取现有引用。
        # 此处不能等锁，否则另一请求正在探测仍会拖住健康检查。
        cached = _cache
    else:
        with _lock:
            if _cache is None or refresh:
                _cache = _detect_capabilities()
            cached = _cache
    snapshot = copy.deepcopy(cached) if cached is not None else _untested_capabilities()
    # 硬件能力缓存与启用策略分开；available 不能被误读为实片验收通过。
    snapshot.update(enabled=web_enabled(), experimental=True)
    return snapshot


def reset_capabilities_cache() -> None:
    global _cache
    with _lock:
        _cache = None
