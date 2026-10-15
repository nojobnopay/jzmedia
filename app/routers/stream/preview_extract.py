"""预览抽帧策略：本地关键帧分页，远程/不兼容片源按时间点精确读取。"""
import math
import os
from pathlib import Path
import re

from ... import media
from ...log import get_logger

logger = get_logger("stream.previews")
_PTS_RE = re.compile(r"\bn:\s*\d+\s+pts:.*?\bpts_time:([\d.eE+\-]+)")


class _SparseKeyframes(Exception):
    pass


def _threads():
    try:
        return str(max(1, min(4, int(os.getenv("PREVIEW_THREADS", "2")))))
    except ValueError:
        return "2"


def _hardware_args():
    # 实际抽帧验证解码能力，不依赖播放后端的 H.264 编码冒烟测试。
    pref = (os.getenv("TRANSCODER") or os.getenv("HW_ACCEL") or "auto").lower().strip()
    device = os.getenv("VAAPI_DEVICE", "/dev/dri/renderD128")
    if pref in ("sw", "software", "cpu", "none"):
        return []
    if pref == "vaapi" or (pref == "auto" and Path(device).exists()):
        return ["-hwaccel", "vaapi", "-hwaccel_device", device]
    if pref == "qsv":
        return ["-hwaccel", "qsv"]
    if pref in ("nvenc", "nvidia") or (pref == "auto" and Path("/dev/nvidia0").exists()):
        return ["-hwaccel", "cuda"]
    return []


def _check_timeline(log, size, interval):
    """按 fps 的向下取整规则检查每个采样格，允许最多一个采样间隔的误差。"""
    times = [float(value) for value in _PTS_RE.findall(log)]
    if not times or any(not math.isfinite(t) for t in times) or times != sorted(times):
        raise _SparseKeyframes("无法核对关键帧时间")
    index = 0
    for cell in range(size):
        while index + 1 < len(times) and math.floor(times[index + 1] / interval) <= cell:
            index += 1
        # fps 会复制首帧/尾帧补齐；不能把很远的关键帧填满整页。
        if abs(times[index] - cell * interval) > interval + 0.001:
            raise _SparseKeyframes("关键帧过稀，改用精确抽帧")


class Extractor:
    def __init__(self, src, filter_arg, interval, *, remote, run, jid, progress):
        self.src = src
        self.filter_arg = filter_arg
        self.interval = interval
        self.run = run
        self.jid = jid
        self.progress = progress
        self.hw = _hardware_args()
        self.fast = not remote and os.getenv("PREVIEW_MODE", "auto").strip().lower() != "accurate"
        self.ffmpeg = media.ffmpeg_bin()
        self.threads = _threads()

    def _input(self, start, *, keyframes=False, duration=None):
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "info" if keyframes else "error",
                "-nostdin", "-y", "-threads", self.threads, "-filter_threads", "1"]
        args += self.hw
        if keyframes:
            args += ["-skip_frame", "nokey"]
        args += ["-ss", str(start)]
        if duration is not None:
            args += ["-t", str(duration)]
        if self.src.input.startswith(("http://", "https://")):
            args += media.probe_limit_args(self.src.input)
        else:
            args += ["-rw_timeout", "30000000"]
        return args + ["-i", self.src.input, "-map", "0:v:0", "-an", "-sn", "-dn"]

    def _keyframe_page(self, start, size, columns, rows, output):
        # 输入侧 seek 保持页起点为时间零；首个关键帧过晚时由时间校验触发回退。
        window = (size + 1) * self.interval
        # select 只减少日志量；真正节省解码的是输入侧 skip_frame。
        # fps/trim 在缩放和 HDR 色调映射之前，每个采样格只处理一张小图。
        vf = (f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{self.interval / 2})',"
              f"showinfo=checksum=0,fps=1/{self.interval}:start_time=0:round=down:eof_action=pass,"
              f"trim=end_frame={size},tpad=stop_mode=clone:stop={size},trim=end_frame={size},"
              f"{self.filter_arg},tpad=stop_mode=add:stop={columns * rows - size}:color=black,"
              f"tile={columns}x{rows}")
        output.unlink(missing_ok=True)
        cmd = self._input(start, keyframes=True, duration=window)
        cmd += ["-vf", vf, "-frames:v", "1", "-fps_mode", "passthrough",
                "-threads", "1", "-q:v", "5", str(output)]
        log = self.run(cmd, self.jid, timeout=120)
        if not output.is_file() or not output.stat().st_size:
            raise _SparseKeyframes("关键帧抽取未生成图片")
        _check_timeline(log, size, self.interval)

    def _accurate_page(self, start, size, columns, rows, output):
        for cell in range(size):
            while True:
                frame = output.parent / f"frame_{cell:02d}.jpg"
                frame.unlink(missing_ok=True)
                cmd = self._input(start + cell * self.interval)
                cmd += ["-frames:v", "1", "-vf", self.filter_arg, "-threads", "1",
                        "-q:v", "5", str(frame)]
                try:
                    self.run(cmd, self.jid)
                    if not frame.is_file() or not frame.stat().st_size:
                        raise RuntimeError("无法从采样位置取得预览画面")
                    break
                except RuntimeError:
                    if not self.hw:
                        raise
                    logger.info("preview hardware decode unavailable; retry with software")
                    self.hw = []
            self.progress(start // self.interval + cell + 1)
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-threads", "1", "-filter_threads", "1", "-framerate", "1", "-i",
               str(output.parent / "frame_%02d.jpg"), "-vf",
               f"tpad=stop_mode=add:stop={columns * rows - size}:color=black,tile={columns}x{rows}",
               "-frames:v", "1", "-threads", "1", "-q:v", "5", str(output)]
        self.run(cmd, self.jid)
        for img in output.parent.glob("frame_*.jpg"):
            img.unlink()

    def page(self, start, size, columns, rows, output):
        while self.fast:
            try:
                self._keyframe_page(start, size, columns, rows, output)
                return
            except _SparseKeyframes as e:
                logger.info("preview keyframe fallback: %s", e)
                self.fast = False
            except RuntimeError:
                if self.hw:
                    logger.info("preview hardware decode unavailable; retry with software")
                    self.hw = []
                else:
                    logger.info("preview keyframe extraction unavailable; retry accurate extraction")
                    self.fast = False
        self._accurate_page(start, size, columns, rows, output)
