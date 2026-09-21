"""转码后端（TranscoderBackend）：软件 / VAAPI / QSV / NVENC 的探测与参数。

目标文档 §11：NAS 上优先 QSV/VAAPI，不要默认 CPU libx264。

- 探测（`detect()`）：env `TRANSCODER=auto|sw|vaapi|qsv|nvenc`（兼容旧 `HW_ACCEL`）；
  auto 时按 VAAPI → QSV → NVENC 顺序跑 **0.2s lavfi 冒烟编码**（验证设备/驱动/编码器
  真的可用，而不是只看设备节点），全失败回落软件；结果进程内缓存（`refresh` 可重探）。
- 参数：`input_args()` 放 `-i` 之前；`video_args(height, burn, tonemap)` 输出侧编码参数
  （tonemap=True 时用 tonemap_vaapi / vpp_qsv；NVENC 未实现 → 调用方走 hdr_no_tonemap
  提示外放）。软件 zscale 链按目标文档不作默认能力。烧录（软件滤镜图）与硬件滤镜互斥
  → `burn=True` 强制软件。
"""
import os
import subprocess
import threading

from .log import get_logger
from .media import ffmpeg_bin

logger = get_logger("transcode")

VAAPI_DEVICE = os.getenv("VAAPI_DEVICE", "/dev/dri/renderD128")
_SMOKE_TIMEOUT = 8
_lock = threading.Lock()
_cache = None


class Backend:
    def __init__(self, name: str = "", reason: str = "", device: str = ""):
        self.name = name            # ''（软件）| vaapi | qsv | nvenc
        self.reason = reason
        self.device = device

    @property
    def hw(self) -> bool:
        return bool(self.name)

    def input_args(self, burn: bool = False) -> list[str]:
        """输入侧硬件参数（必须在对应 `-i` 之前）。烧录走软件滤镜图 → 不开硬件解码。"""
        if burn or not self.hw:
            return []
        if self.name == "vaapi":
            return ["-vaapi_device", self.device or VAAPI_DEVICE,
                    "-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi"]
        if self.name == "qsv":
            return ["-init_hw_device", "qsv=hw", "-filter_hw_device", "hw",
                    "-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]
        if self.name == "nvenc":
            return ["-hwaccel", "cuda"]
        return []

    def video_args(self, height: int, burn: bool = False, tonemap: bool = False) -> list[str]:
        """输出侧视频编码参数。height=0 不缩放；burn 强制软件（滤镜图与硬件滤镜互斥）。
        目标统一 8bit（yuv420p）：H.264 10bit（High10）浏览器/MSE 基本不能解，
        源是 10bit 时必须显式降到 8bit（libx264/nvenc 都要；VAAPI/QSV 由滤镜 format 保证）。
        统一 High profile：master.m3u8 的 CODECS 按 `avc1.6400xx` 声明，编码器实际
        也是 High（NVENC 默认 Main 会与声明不符，Safari 等严格客户端可能不认视频轨）。
        关键帧：playback 侧 `-force_key_frames 4s`；NVENC/QSV 需显式 `-forced-idr 1`
        才会把强制关键帧当 IDR（否则 HLS 分片仍按源码 GOP，10s+ 分片会让音轨跑在
        视频前面 → 画面卡住声音继续），见 2026-09 沙丘 720p 实测。"""
        h = int(height or 0)
        if burn or not self.hw:
            # 烧录：filter_complex（overlay+缩放）与硬件滤镜互斥，统一软件编码；
            # 软件路径不实现 tonemap → 调用方（playback.plan）需在 HDR+burn 时
            # 追加 hdr_no_tonemap 提示（评审 P1-08），勿在此静默忽略 tonemap 参数。
            args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-profile:v", "high", "-forced-idr", "1"]
            if h:
                args += ["-vf", f"scale=-2:{h}"]
            return args
        if self.name == "vaapi":
            if tonemap:
                vf = "tonemap_vaapi=format=nv12:t=bt709:m=bt709:r=tv"
                vf += f",scale_vaapi=w=-2:h={h}" if h else ""
            else:
                vf = "scale_vaapi=format=nv12" + (f":w=-2:h={h}" if h else "")
            # 注意：本构建 h264_vaapi 无 forced-idr 选项，4s 关键帧能否生效待 NAS 实测
            return ["-vf", vf, "-c:v", "h264_vaapi", "-qp", "23", "-profile:v", "high"]
        if self.name == "qsv":
            if tonemap:
                vf = "vpp_qsv=tonemap=1:format=nv12"
                vf += f",vpp_qsv=w=-2:h={h}" if h else ""
            else:
                vf = "vpp_qsv=format=nv12" + (f":w=-2:h={h}" if h else "")
            # ICQ 质量档（评审 R11-D6）：不指定时 QSV 默认质量偏低
            return ["-vf", vf, "-c:v", "h264_qsv", "-preset", "veryfast",
                    "-global_quality", "23", "-profile:v", "high", "-forced_idr", "1"]
        if self.name == "nvenc":
            # VBR + CQ（评审 R11-D6）：默认码率策略不透明，显式质量优先
            args = ["-c:v", "h264_nvenc", "-preset", "p4", "-pix_fmt", "yuv420p",
                    "-rc", "vbr", "-cq", "23", "-b:v", "0",
                    "-profile:v", "high", "-forced-idr", "1"]
            if h:
                args += ["-vf", f"scale=-2:{h}"]
            return args
        return []


def software() -> Backend:
    return Backend("", "software (forced)")


def _run(cmd: list[str]) -> bool:
    try:
        p = subprocess.run(cmd, timeout=_SMOKE_TIMEOUT, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return p.returncode == 0
    except Exception as e:
        logger.debug("smoke run failed: %s", e)
        return False


def _smoke(name: str) -> tuple[bool, str]:
    """0.2s lavfi 冒烟编码：真实验证 设备/驱动/编码器 组合可用。"""
    src = ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=0.2"]
    if name == "vaapi":
        if not os.path.exists(VAAPI_DEVICE):
            return False, f"{VAAPI_DEVICE} 不存在"
        cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
               "-vaapi_device", VAAPI_DEVICE] + src + \
              ["-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-f", "null", "-"]
    elif name == "qsv":
        cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
               "-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"] + src + \
              ["-vf", "format=nv12,hwupload=extra_hw_frames=64",
               "-c:v", "h264_qsv", "-f", "null", "-"]
    elif name == "nvenc":
        cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error"] + src + \
              ["-c:v", "h264_nvenc", "-f", "null", "-"]
    else:
        return False, "unknown backend"
    ok = _run(cmd)
    return ok, ("smoke ok" if ok else "smoke failed")


def _detect_uncached() -> Backend:
    pref = (os.getenv("TRANSCODER") or os.getenv("HW_ACCEL") or "").strip().lower()
    if pref in ("sw", "software", "cpu", "none"):
        return Backend("", f"env={pref}")
    order = [pref] if pref in ("vaapi", "qsv", "nvenc", "nvidia") else ["vaapi", "qsv", "nvenc"]
    order = ["nvenc" if x in ("nvenc", "nvidia") else x for x in order]
    last = ""
    for name in order:
        ok, why = _smoke(name)
        if ok:
            dev = VAAPI_DEVICE if name == "vaapi" else ""
            forced = " (env forced)" if pref else ""
            return Backend(name, f"smoke ok{forced}", dev)
        last = f"{name}: {why}"
    return Backend("", f"software fallback ({last})" if last else "software")


def detect(refresh: bool = False) -> Backend:
    """探测/取缓存。首次播放或 /api/stream/backends 时触发；失败自动软件兜底。"""
    global _cache
    with _lock:
        if _cache is None or refresh:
            _cache = _detect_uncached()
        return _cache


def backend_info(refresh: bool = False) -> dict:
    b = detect(refresh=refresh)
    return {"name": b.name or "software", "hw": b.hw, "device": b.device,
            "reason": b.reason,
            "env": os.getenv("TRANSCODER") or os.getenv("HW_ACCEL") or ""}
