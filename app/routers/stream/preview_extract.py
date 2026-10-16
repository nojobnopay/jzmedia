"""预览抽帧策略：本地关键帧分页/索引附近采样，远程或不兼容采样点精确读取。"""
import math
import os
from pathlib import Path
import re
import shutil
import tempfile

from ... import media
from ...log import get_logger

logger = get_logger("stream.previews")
_PTS_RE = re.compile(r"\bn:\s*\d+\s+pts:.*?\bpts_time:([\d.eE+\-]+)")
_FRAME_RE = re.compile(r"\bn:\s*(\d+)\s+pts:[^\r\n]*?\bpts_time:(\S+)")
_MAX_KEYFRAMES = 4096


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


def _frame_times(log):
    # _run 独立保留完整时间戳，不能用可能已被截断的 stderr 尾部校验整页。
    if hasattr(log, "frame_times"):
        return log.frame_times
    return tuple(float(value) for value in _PTS_RE.findall(log))


def _frame_numbers(log):
    if hasattr(log, "frame_numbers"):
        return log.frame_numbers
    return tuple(int(number) for number, _time in _FRAME_RE.findall(log))


def _match_keyframes(times, size, interval):
    """按真实 PTS 选择最近画面；无法在一个采样间隔内覆盖的格子留空。"""
    matches = []
    for cell in range(size):
        target = cell * interval
        # 时间相同或等距时固定选择较早画面和较早输出序号。
        index = min(range(len(times)), key=lambda i: (abs(times[i] - target), times[i], i))
        matches.append(index if abs(times[index] - target) <= interval + 0.001 else None)
    return matches


class Extractor:
    def __init__(self, src, filter_arg, interval, *, remote, run, jid, progress, context=None):
        self.src = src
        self.filter_arg = filter_arg
        self.interval = interval
        self.run = run
        self.jid = jid
        self.progress = progress
        self.context = context or {}
        self.hw = _hardware_args()
        self.fast = not remote and os.getenv("PREVIEW_MODE", "auto").strip().lower() != "accurate"
        self.mode = "window_keyframes" if self.fast else "accurate"
        self.last_mode = self.mode
        self.last_stats = {}
        self.last_samples = []
        self.page_number = 0
        self.ffmpeg = media.ffmpeg_bin()
        self.threads = _threads()

    def _input(self, start, *, keyframes=False, duration=None, accurate_seek=True, frame_log=False):
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "info" if keyframes or frame_log else "error",
                "-nostdin", "-y", "-threads", self.threads, "-filter_threads", "1"]
        args += self.hw
        if not accurate_seek:
            args += ["-noaccurate_seek"]
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

    def _fallback_log(self, mode, start, reason):
        logger.info("preview extraction fallback job=%s item=%s:%s page=%s start=%s mode=%s decoder=%s reason=%s",
                    self.jid, self.context.get("kind", "unknown"), self.context.get("id", "unknown"),
                    self.page_number, start, mode, self.hw[1] if self.hw else "software",
                    getattr(reason, "diagnostic", str(reason)))

    def _set_mode(self, mode):
        self.mode = mode
        self.fast = mode != "accurate"

    def _keyframe_page(self, start, size, columns, rows, output):
        # 每张缩小后的画面与 showinfo 一一对应；不经 fps/select，保留 HEVC
        # 解码器可能输出的乱序、重复和负 PTS，随后再按真实时间选择采样格。
        window = (size + 1) * self.interval
        with tempfile.TemporaryDirectory(prefix=".keyframes-", dir=output.parent) as tmp:
            directory = Path(tmp)
            pattern = directory / "key_%06d.png"
            cmd = self._input(start, keyframes=True, duration=window, accurate_seek=False)
            # 全关键帧片源也只保留有界数量的小图；达到上限仍须严格核对
            # 输出数量，不一致时安全降级，不允许写图随整片时长无限增长。
            # 先记录原始时间供采样匹配，再仅为输出图片重建递增时间；
            # PNG 无损保留候选画面，passthrough 保持一帧对应一张图。
            cmd += ["-vf", f"{self.filter_arg},showinfo=checksum=0,settb=expr=1/1,setpts=N",
                    "-frames:v", str(_MAX_KEYFRAMES), "-enc_time_base", "1:1", "-fps_mode", "passthrough",
                    "-threads", "1", "-c:v", "png", "-pix_fmt", "rgb24",
                    "-compression_level", "1", "-start_number", "0", str(pattern)]
            log = self.run(cmd, self.jid, timeout=120)
            times = _frame_times(log)
            numbers = _frame_numbers(log)
            files = sorted(directory.glob("key_*.png"))
            if (not times or len(times) > _MAX_KEYFRAMES or
                    tuple(numbers) != tuple(range(len(times))) or len(files) != len(times) or
                    any(file.name != f"key_{index:06d}.png" or not file.stat().st_size
                        for index, file in enumerate(files)) or
                    any(not math.isfinite(time) for time in times)):
                raise _SparseKeyframes(
                    f"无法核对关键帧图片与时间 samples={len(times)} "
                    f"numbers={len(numbers)} images={len(files)}")
            matches = _match_keyframes(times, size, self.interval)
            reused = sum(index is not None for index in matches)
            if not reused:
                raise _SparseKeyframes(f"关键帧未覆盖本页 samples={len(times)}")
            nearby_points = accurate_points = 0
            try:
                for cell, index in enumerate(matches):
                    frame = output.parent / f"frame_{cell:02d}.png"
                    if index is None:
                        mode = self._sample_point(start + cell * self.interval, frame,
                                                  nearby=True, remember_failure=False)
                        nearby_points += mode == "point_seek"
                        accurate_points += mode == "accurate"
                        self.last_samples.append({"cell": cell, "pts": None, "mode": mode})
                    else:
                        shutil.copyfile(files[index], frame)
                        self.last_samples.append({"cell": cell, "pts": times[index], "mode": "keyframe"})
                    self.progress(start // self.interval + cell + 1)
                self._tile_page(size, columns, rows, output)
            finally:
                self._clear_frames(output)
            self.last_mode = "mixed_keyframes" if reused < size else "window_keyframes"
            self.last_stats = {"candidates": len(times), "used_cells": reused,
                               "missing": size - reused, "point_seek": nearby_points,
                               "accurate": accurate_points,
                               "max_keyframe_error": max(abs(times[index] - cell * self.interval)
                                                         for cell, index in enumerate(matches)
                                                         if index is not None)}
            logger.info("preview keyframe page job=%s item=%s:%s page=%s candidates=%s "
                        "used_cells=%s missing=%s point_seek=%s accurate=%s decoder=%s",
                        self.jid, self.context.get("kind", "unknown"), self.context.get("id", "unknown"),
                        self.page_number, len(times), reused, size - reused, nearby_points,
                        accurate_points, self.hw[1] if self.hw else "software")

    def _nearby_point(self, target, frame):
        # 从索引位置取附近画面，不必解码到精确目标；部分 HEVC 的 nokey 会漏帧，
        # 此处保留正常解码，并在缩放前限制时间范围，核对实际输出的第一张画面。
        vf = (f"select='between(t,{-self.interval},{self.interval})',"
              f"showinfo=checksum=0,{self.filter_arg}")
        cmd = self._input(target, duration=2 * self.interval, accurate_seek=False, frame_log=True)
        cmd += ["-vf", vf, "-frames:v", "1", "-fps_mode", "passthrough",
                "-threads", "1", "-c:v", "png", "-pix_fmt", "rgb24",
                "-compression_level", "1", str(frame)]
        log = self.run(cmd, self.jid)
        times = _frame_times(log)
        if not frame.is_file() or not frame.stat().st_size:
            raise _SparseKeyframes("采样位置附近没有画面")
        # showinfo 紧接 select；第一项就是实际写出的第一张画面，而非乱序候选的末项。
        if not times or not math.isfinite(times[0]) or abs(times[0]) > self.interval + 0.001:
            raise _SparseKeyframes(
                f"无法核对采样位置的画面时间 observed={times[0] if times else 'missing'} "
                f"samples={len(times)}")

    def _sample_point(self, target, frame, *, nearby, remember_failure=True):
        while True:
            frame.unlink(missing_ok=True)
            try:
                if nearby:
                    self._nearby_point(target, frame)
                else:
                    cmd = self._input(target)
                    cmd += ["-frames:v", "1", "-vf", self.filter_arg, "-threads", "1",
                            "-c:v", "png", "-pix_fmt", "rgb24", "-compression_level", "1", str(frame)]
                    self.run(cmd, self.jid)
                if not frame.is_file() or not frame.stat().st_size:
                    raise RuntimeError("无法从采样位置取得预览画面")
                return "point_seek" if nearby else "accurate"
            except (RuntimeError, _SparseKeyframes) as exc:
                mode = "point_seek" if nearby else "accurate"
                if self.hw and isinstance(exc, RuntimeError):
                    self._fallback_log(mode, target, exc)
                    self.hw = []
                    continue
                if not nearby:
                    raise
                self._fallback_log(mode, target, exc)
                if remember_failure:
                    self._set_mode("accurate")
                nearby = False

    @staticmethod
    def _clear_frames(output):
        for image in output.parent.glob("frame_*.png"):
            image.unlink(missing_ok=True)

    def _tile_page(self, size, columns, rows, output):
        output.unlink(missing_ok=True)
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-threads", "1", "-filter_threads", "1", "-framerate", "1", "-i",
               str(output.parent / "frame_%02d.png"), "-vf",
               f"tpad=stop_mode=add:stop={columns * rows - size}:color=black,tile={columns}x{rows}",
               "-frames:v", "1", "-threads", "1", "-c:v", "mjpeg", "-pix_fmt", "yuvj444p",
               "-q:v", "2", str(output)]
        self.run(cmd, self.jid)
        if not output.is_file() or not output.stat().st_size:
            raise RuntimeError("无法拼接预览画面")

    def _sample_page(self, start, size, columns, rows, output, *, nearby):
        nearby_points = accurate_points = 0
        try:
            for cell in range(size):
                mode = self._sample_point(start + cell * self.interval,
                                          output.parent / f"frame_{cell:02d}.png", nearby=nearby)
                nearby_points += mode == "point_seek"
                accurate_points += mode == "accurate"
                self.last_samples.append({"cell": cell, "pts": None, "mode": mode})
                nearby = nearby and self.mode != "accurate"
                self.progress(start // self.interval + cell + 1)
            self._tile_page(size, columns, rows, output)
        finally:
            self._clear_frames(output)
        self.last_stats = {"candidates": 0, "used_cells": 0, "missing": size,
                           "point_seek": nearby_points, "accurate": accurate_points,
                           "max_keyframe_error": None}

    def _accurate_page(self, start, size, columns, rows, output):
        self._sample_page(start, size, columns, rows, output, nearby=False)

    def _nearby_page(self, start, size, columns, rows, output):
        self._sample_page(start, size, columns, rows, output, nearby=True)

    def page(self, start, size, columns, rows, output):
        self.page_number = int(start // (self.interval * columns * rows))
        self.last_stats = {}
        self.last_samples = []
        output.unlink(missing_ok=True)
        while self.mode == "window_keyframes":
            try:
                self._keyframe_page(start, size, columns, rows, output)
                return
            except _SparseKeyframes as e:
                self.last_samples = []
                self._fallback_log(self.mode, start, e)
                self._set_mode("point_seek")
            except RuntimeError as exc:
                self.last_samples = []
                self._fallback_log(self.mode, start, exc)
                if self.hw:
                    self.hw = []
                else:
                    self._set_mode("point_seek")
        if self.mode == "point_seek":
            self._nearby_page(start, size, columns, rows, output)
        else:
            self._accurate_page(start, size, columns, rows, output)
        self.last_mode = self.mode
