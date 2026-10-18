"""预览抽帧策略：本地关键帧分页/索引附近采样，远程或不兼容采样点精确读取。"""
import math
import os
from pathlib import Path
import re
import shutil
import tempfile

from ... import media
from ...log import get_logger
from .preview_log import ExtractionError

logger = get_logger("stream.previews")
_PTS_RE = re.compile(r"\bn:\s*\d+\s+pts:.*?\bpts_time:([\d.eE+\-]+)")
_FRAME_RE = re.compile(r"\bn:\s*(\d+)\s+pts:[^\r\n]*?\bpts_time:(\S+)")
_MAX_KEYFRAMES = 4096
_EMPTY_POINT_MAX_BACKOFF = 2.0
_EMPTY_POINT_WINDOW = 0.5
_DV_PREROLL_SECONDS = (10.0, 30.0)
_DV_WINDOW_POINTS = 2
_DV_WINDOW_SECONDS = 42.0
_DV_HINT_USES = 3


class _SparseKeyframes(Exception):
    pass


class _PointOutputError(RuntimeError):
    def __init__(self, *, reason, images, samples, start, observed=None):
        super().__init__("无法从采样位置取得预览画面")
        self.diagnostic = (f"reason={reason} returncode=0 images={images} samples={samples} "
                           f"seek={start} observed={observed}")


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
    def __init__(self, src, filter_arg, interval, *, remote, run, jid, progress, context=None,
                 video_stream_index=None, input_args=(), software=False, frame_guard=""):
        self.src = src
        self.filter_arg = filter_arg
        self.frame_guard = f"{frame_guard.rstrip(',')}," if frame_guard else ""
        self.interval = interval
        self.run = run
        self.jid = jid
        self.progress = progress
        self.context = context or {}
        self.hw = [] if software else _hardware_args()
        self.input_args = tuple(input_args)
        self.video_map = "0:v:0" if video_stream_index is None else f"0:{int(video_stream_index)}"
        self.fast = not remote and os.getenv("PREVIEW_MODE", "auto").strip().lower() != "accurate"
        self.mode = "window_keyframes" if self.fast else "accurate"
        # Skipping pictures also skips referenced RPU state. Decode short P5
        # windows normally and share that state between adjacent samples.
        if self.frame_guard and os.getenv("PREVIEW_MODE", "auto").strip().lower() != "accurate":
            self.mode = "dv_windows"
            self.fast = False
        self._dv_hint_uses = 0
        self._dv_hint_target = None
        self._dv_hint_before = 10.0
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
        args += self.input_args
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
        return args + ["-i", self.src.input, "-map", self.video_map, "-an", "-sn", "-dn"]

    def _fallback_log(self, mode, start, reason):
        # 只有本模块和进程包装器构造的诊断可直接写日志；普通异常文本可能带源 URL。
        detail = (reason.diagnostic if isinstance(reason, (ExtractionError, _PointOutputError))
                  else str(reason) if isinstance(reason, _SparseKeyframes)
                  else type(reason).__name__)
        logger.info("preview extraction fallback job=%s item=%s:%s page=%s start=%s mode=%s decoder=%s reason=%s",
                    self.jid, self.context.get("kind", "unknown"), self.context.get("id", "unknown"),
                    self.page_number, start, mode, self.hw[1] if self.hw else "software",
                    detail)

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
            cmd += ["-vf", f"{self.frame_guard}{self.filter_arg},showinfo=checksum=0,settb=expr=1/1,setpts=N",
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
              f"{self.frame_guard}showinfo=checksum=0,{self.filter_arg}")
        cmd = self._input(target, duration=2 * self.interval, accurate_seek=False, frame_log=True)
        cmd += ["-vf", vf, "-frames:v", "1", "-fps_mode", "passthrough",
                "-threads", "1", "-c:v", "png", "-pix_fmt", "rgb24",
                "-compression_level", "1", str(frame)]
        log = self.run(cmd, self.jid)
        times = _frame_times(log)
        if not frame.is_file() or not frame.stat().st_size:
            raise _SparseKeyframes("采样位置附近没有画面")
        # showinfo 位于时间和元数据过滤之后；第一项就是实际写出的第一张画面。
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
                    # 元数据过滤可能连续丢帧；不能为了找下一张有 RPU 的画面读完整片。
                    window = min(2 * float(self.interval), 30.0) if self.frame_guard else None
                    cmd = self._input(target, duration=window, frame_log=True)
                    cmd += ["-frames:v", "1", "-vf",
                            f"{self.frame_guard}showinfo=checksum=0,{self.filter_arg}", "-threads", "1",
                            "-c:v", "png", "-pix_fmt", "rgb24", "-compression_level", "1", str(frame)]
                    log = self.run(cmd, self.jid)
                if not frame.is_file() or not frame.stat().st_size:
                    raise _PointOutputError(reason="empty_output", images=0,
                                            samples=0 if nearby else len(_frame_times(log)), start=target)
                if not nearby and self.frame_guard:
                    times = _frame_times(log)
                    if (not times or not math.isfinite(times[0]) or
                            not -0.001 <= times[0] <= self.interval + 0.001):
                        raise _PointOutputError(reason="unverified_output", images=1,
                                                samples=len(times), start=target,
                                                observed=target + times[0] if times else None)
                return "point_seek" if nearby else "accurate"
            except (RuntimeError, _SparseKeyframes) as exc:
                mode = "point_seek" if nearby else "accurate"
                if self.hw and isinstance(exc, RuntimeError):
                    self._fallback_log(mode, target, exc)
                    self.hw = []
                    continue
                if not nearby:
                    self._fallback_log(mode, target, exc)
                    if isinstance(exc, _PointOutputError):
                        self._recover_empty_point(target, frame)
                        return "accurate"
                    raise
                self._fallback_log(mode, target, exc)
                if remember_failure:
                    self._set_mode("accurate")
                nearby = False

    def _recover_empty_point(self, target, frame):
        """只修复微小尾部误差或索引偏差；不把远处画面填入已结束的视频。"""
        tolerance = min(float(self.interval), _EMPTY_POINT_MAX_BACKOFF)
        frame.unlink(missing_ok=True)
        attempted = {target}
        for backoff in (min(0.25, tolerance), tolerance):
            start = max(0.0, target - backoff)
            if start in attempted:
                continue
            attempted.add(start)
            frame.unlink(missing_ok=True)
            # 最多两次半秒窗口；保留输入侧 seek，禁止为了找尾图从头扫描整片。
            # select 在转换前限窗，showinfo 的首项必须对应实际写出的首张 PNG。
            cmd = self._input(start, duration=_EMPTY_POINT_WINDOW, frame_log=True)
            cmd += ["-vf", f"select='between(t,0,{_EMPTY_POINT_WINDOW})',"
                    f"{self.frame_guard}showinfo=checksum=0,{self.filter_arg}",
                    "-frames:v", "1", "-fps_mode", "passthrough", "-threads", "1",
                    "-c:v", "png", "-pix_fmt", "rgb24", "-compression_level", "1", str(frame)]
            log = self.run(cmd, self.jid, timeout=30)
            times = _frame_times(log)
            images = int(frame.is_file() and bool(frame.stat().st_size))
            observed = start + times[0] if times else None
            if (images and times and math.isfinite(times[0]) and
                    -0.001 <= times[0] <= _EMPTY_POINT_WINDOW + 0.001 and
                    abs(observed - target) <= tolerance + 0.001):
                logger.info("preview extraction recovered job=%s item=%s:%s page=%s target=%s "
                            "mode=accurate_recovery seek=%s observed=%s images=%s samples=%s",
                            self.jid, self.context.get("kind", "unknown"), self.context.get("id", "unknown"),
                            self.page_number, target, start, observed, images, len(times))
                return
            error = _PointOutputError(reason="empty_output" if not images else "unverified_output",
                                      images=images, samples=len(times), start=start, observed=observed)
            self._fallback_log("accurate_recovery", target, error)
            frame.unlink(missing_ok=True)
        if self.frame_guard and self._recover_guarded_point(target, frame):
            return
        raise _PointOutputError(reason="empty_output_exhausted", images=0, samples=0, start=target)

    def _recover_guarded_point(self, target, frame):
        """Give referenced DV metadata bounded decoder preroll, then sample near target."""
        window = min(float(self.interval), _EMPTY_POINT_MAX_BACKOFF)
        attempted = set()
        invalid_data = False
        for before in _DV_PREROLL_SECONDS:
            start = max(0.0, target - before)
            if start >= target or start in attempted:
                continue
            attempted.add(start)
            offset = target - start
            frame.unlink(missing_ok=True)
            cmd = self._input(start, duration=offset + window, frame_log=True)
            # Decode preroll frames to restore RPU references, but only convert
            # verified metadata-bearing frames at the original sampling point.
            cmd += ["-vf", f"select='between(t,{offset},{offset + window})',"
                    f"{self.frame_guard}showinfo=checksum=0,{self.filter_arg}",
                    "-frames:v", "1", "-fps_mode", "passthrough", "-threads", "1",
                    "-c:v", "png", "-pix_fmt", "rgb24", "-compression_level", "1", str(frame)]
            log = self.run(cmd, self.jid, timeout=60)
            times = _frame_times(log)
            images = int(frame.is_file() and bool(frame.stat().st_size))
            invalid_data |= not images and 'invalid nal unit' in str(log).lower()
            observed = start + times[0] if times else None
            if (images and times and math.isfinite(times[0])
                    and target - 0.001 <= observed <= target + window + 0.001):
                logger.info("preview extraction recovered job=%s item=%s:%s page=%s target=%s "
                            "mode=dv_preroll seek=%s observed=%s images=%s samples=%s",
                            self.jid, self.context.get("kind", "unknown"), self.context.get("id", "unknown"),
                            self.page_number, target, start, observed, images, len(times))
                if self.mode == "dv_windows":
                    self._remember_dv_preroll(before, target)
                return True
            error = _PointOutputError(reason="empty_output" if not images else "unverified_output",
                                      images=images, samples=len(times), start=start, observed=observed)
            self._fallback_log("dv_preroll", target, error)
            frame.unlink(missing_ok=True)
        if invalid_data:
            raise ExtractionError("无法解码片源，请检查视频编码或文件是否损坏",
                                  reason="invalid_video_data", error_kind="decoder", returncode=0)
        return False

    def _remember_dv_preroll(self, before, target):
        self._dv_hint_before = before
        self._dv_hint_target = target
        self._dv_hint_uses = _DV_HINT_USES

    def _guarded_window(self, targets, frames, *, preroll):
        """Publish at most two samples only after checking the entire window."""
        for frame in frames:
            frame.unlink(missing_ok=True)
        if (not self.frame_guard or not 1 <= len(targets) <= _DV_WINDOW_POINTS
                or len(frames) != len(targets) or preroll not in _DV_PREROLL_SECONDS
                or any(not math.isfinite(t) or t < 0 for t in targets)
                or any(right <= left for left, right in zip(targets, targets[1:]))):
            return False
        tolerance = min(float(self.interval), _EMPTY_POINT_MAX_BACKOFF)
        start = max(0.0, targets[0] - preroll)
        duration = targets[-1] + tolerance - start
        if not 0 < duration <= _DV_WINDOW_SECONDS:
            return False
        # selected_n advances only after a frame is accepted. A missing first
        # target cannot consume the second target's PNG filename. The metadata
        # guard must run before this stateful selector.
        select = "+".join(
            f"eq(selected_n,{index})*between(t,{target - start},{target - start + tolerance})"
            for index, target in enumerate(targets))
        published = False
        try:
            with tempfile.TemporaryDirectory(prefix=".dv-window-", dir=frames[0].parent) as tmp:
                directory = Path(tmp)
                cmd = self._input(start, duration=duration, frame_log=True)
                cmd += ["-vf", f"{self.frame_guard}select='{select}',{self.filter_arg},"
                        "showinfo=checksum=0,settb=expr=1/1,setpts=N",
                        "-frames:v", str(len(targets)), "-enc_time_base", "1:1",
                        "-fps_mode", "passthrough", "-threads", "1", "-c:v", "png",
                        "-pix_fmt", "rgb24", "-compression_level", "1", "-start_number", "0",
                        str(directory / "sample_%02d.png")]
                # Two distant samples may decode up to forty seconds of 4K
                # pictures. Their shared budget remains below two separate
                # sixty-second probes; cancellation/yield still polls at 200ms.
                timeout = 90 if len(targets) == 2 and duration > 32 else 60
                log = self.run(cmd, self.jid, timeout=timeout)
                times = _frame_times(log)
                files = sorted(directory.glob("*.png"))
                valid = (
                    len(times) == len(files) == len(targets)
                    and tuple(_frame_numbers(log)) == tuple(range(len(targets)))
                    and all(file.name == f"sample_{index:02d}.png" and file.stat().st_size
                            for index, file in enumerate(files))
                    and all(math.isfinite(t) and target - 0.001 <= start + t <= target + tolerance + 0.001
                            for target, t in zip(targets, times))
                    and all(right > left for left, right in zip(times, times[1:])))
                if not valid:
                    self._fallback_log("dv_window", targets[0], _PointOutputError(
                        reason="unverified_window", images=len(files), samples=len(times), start=start))
                    return False
                for source, destination in zip(files, frames):
                    shutil.copyfile(source, destination)
                published = True
                logger.info("preview dv window job=%s item=%s:%s page=%s target=%s "
                            "last_target=%s preroll=%s images=%s wall_seconds=%.3f",
                            self.jid, self.context.get("kind", "unknown"), self.context.get("id", "unknown"),
                            self.page_number, targets[0], targets[-1], preroll, len(files),
                            getattr(log, "elapsed", 0.0))
                return True
        except RuntimeError as exc:
            if isinstance(exc, ExtractionError) and (exc.timed_out or exc.error_kind in {
                    "memory", "permission", "option", "filter", "hardware", "input"}):
                raise
            self._fallback_log("dv_window", targets[0], exc)
            return False
        finally:
            if not published:
                for frame in frames:
                    frame.unlink(missing_ok=True)

    def _dv_windows_page(self, start, size, columns, rows, output):
        cell, windows, individual = 0, 0, 0
        try:
            while cell < size:
                target = start + cell * self.interval
                adjacent = (self._dv_hint_target is not None
                            and abs(target - self._dv_hint_target - self.interval) <= 0.001)
                if not self._dv_hint_uses or not adjacent:
                    # Ordinary points can finish in seconds. Do not make every
                    # P5 sample pay for a decoder preroll it has not needed.
                    self._dv_hint_uses = 0
                    frame = output.parent / f"frame_{cell:02d}.png"
                    self._sample_point(target, frame, nearby=False)
                    self._dv_hint_target = target
                    self.last_samples.append({"cell": cell, "pts": None, "mode": "accurate"})
                    self.progress(start // self.interval + cell + 1)
                    individual += 1
                    cell += 1
                    continue
                preferred = self._dv_hint_before
                attempts = tuple(dict.fromkeys((preferred, *_DV_PREROLL_SECONDS)))
                recovered = False
                attempted = set()
                for before in attempts:
                    count = min(_DV_WINDOW_POINTS, size - cell)
                    tolerance = min(float(self.interval), _EMPTY_POINT_MAX_BACKOFF)
                    if target + (count - 1) * self.interval + tolerance - max(0.0, target - before) > _DV_WINDOW_SECONDS:
                        count = 1
                    targets = [target + offset * self.interval for offset in range(count)]
                    frames = [output.parent / f"frame_{cell + offset:02d}.png" for offset in range(count)]
                    identity = (tuple(targets), max(0.0, target - before))
                    if identity in attempted:
                        continue
                    attempted.add(identity)
                    if self._guarded_window(targets, frames, preroll=before):
                        recovered = True
                        windows += 1
                        self._dv_hint_uses -= 1
                        self._dv_hint_before = before
                        break
                if not recovered:
                    # A broken short window must not fill a missing slot with a
                    # later picture. Keep the proven per-point recovery chain.
                    self._dv_hint_uses = 0
                    for target, frame in zip(targets, frames):
                        self._dv_hint_uses = 0
                        self._sample_point(target, frame, nearby=False)
                        individual += 1
                self._dv_hint_target = targets[-1]
                for offset in range(count):
                    self.last_samples.append({"cell": cell + offset, "pts": None,
                                              "mode": "dv_window" if recovered else "accurate"})
                    self.progress(start // self.interval + cell + offset + 1)
                cell += count
            self._tile_page(size, columns, rows, output)
        finally:
            self._clear_frames(output)
        self.last_stats = {"windows": windows, "individual_points": individual}
        self.last_mode = "dv_windows"

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
        if self.mode == "dv_windows":
            self._dv_windows_page(start, size, columns, rows, output)
            return
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
