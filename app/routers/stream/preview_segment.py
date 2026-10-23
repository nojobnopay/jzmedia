"""有界连续解码，只发布元数据、时间戳和图片均可核对的采样前缀。"""
import math
from pathlib import Path
import shutil
import tempfile

from .preview_log import ExtractionError


MAX_SEGMENT_POINTS = 25
# Keep the full 2x input-time budget even on slower CPUs; do not clip a longer
# window's timeout to 600s and accidentally give it a smaller decoding budget.
MAX_SEGMENT_SECONDS = 300.0
MAX_SEGMENT_PREROLL = 130.0


def extract_segment(extractor, targets, frames, *, start):
    """Return absolute PTS for the verified prefix, leaving later targets absent.

    A missing sample never advances the selector to a later target. Callers
    must recover the first missing target before assembling a complete page.
    Resource/configuration failures and interruptions retain their semantics;
    only a decoder failure or unverifiable output permits point fallback.
    """
    # Deferred imports keep the primitive available to Extractor without an
    # import cycle while retaining the existing bounded-log parsing contract.
    from .preview_extract import _frame_numbers, _frame_times, _PointOutputError

    extractor._segment_elapsed = None
    frames = tuple(Path(frame) for frame in frames)
    published = False
    try:
        for frame in frames:
            frame.unlink(missing_ok=True)
        try:
            targets = tuple(float(target) for target in targets)
            start, interval = float(start), float(extractor.interval)
        except (TypeError, ValueError, OverflowError):
            return ()
        if (not extractor.frame_guard or not 1 <= len(targets) <= MAX_SEGMENT_POINTS
                or len(frames) != len(targets) or len(set(frames)) != len(frames)
                or not math.isfinite(interval) or interval <= 0
                or any(not math.isfinite(target) or target < 0 for target in targets)
                or any(right <= left for left, right in zip(targets, targets[1:]))
                or not math.isfinite(start) or not 0 <= start <= targets[0]
                or targets[0] - start > min(MAX_SEGMENT_PREROLL, 120.0 + interval)):
            return ()
        tolerance = min(interval, 2.0)
        duration = targets[-1] + tolerance - start
        if not 0 < duration <= MAX_SEGMENT_SECONDS:
            return ()
        select = "+".join(
            f"eq(selected_n,{index})*between(t,{target - start},{target - start + tolerance})"
            for index, target in enumerate(targets))
        with tempfile.TemporaryDirectory(prefix=".dv-segment-", dir=frames[0].parent) as tmp:
            directory = Path(tmp)
            cmd = extractor._input(start, duration=duration, frame_log=True)
            # Decode normally to preserve decoder state. Reject frames without
            # metadata before advancing selected_n; convert only chosen frames.
            cmd += ["-vf", f"{extractor.frame_guard}select='{select}',{extractor.filter_arg},"
                    "showinfo=checksum=0,settb=expr=1/1,setpts=N",
                    "-frames:v", str(len(targets)), "-enc_time_base", "1:1",
                    "-fps_mode", "passthrough", "-threads", "1", "-c:v", "png",
                    "-pix_fmt", "rgb24", "-compression_level", "1", "-start_number", "0",
                    str(directory / "sample_%02d.png")]
            log = extractor.run(cmd, extractor.jid, timeout=min(600, max(60, 2 * duration)))
            times = tuple(_frame_times(log))
            files = sorted(directory.iterdir())
            valid = (
                0 < len(times) == len(files) <= len(targets)
                and tuple(_frame_numbers(log)) == tuple(range(len(times)))
                and all(file.name == f"sample_{index:02d}.png"
                        and file.is_file() and file.stat().st_size
                        for index, file in enumerate(files))
                and all(math.isfinite(time)
                        and target - 0.001 <= start + time <= target + tolerance + 0.001
                        for target, time in zip(targets, times))
                and all(right > left for left, right in zip(times, times[1:])))
            if not valid:
                extractor._fallback_log("dv_segment", targets[0], _PointOutputError(
                    reason="unverified_segment", images=len(files), samples=len(times), start=start))
                return ()
            for source, destination in zip(files, frames):
                shutil.copyfile(source, destination)
            observed = tuple(start + time for time in times)
        published = True
        extractor._segment_elapsed = getattr(log, "elapsed", None)
        return observed
    except RuntimeError as exc:
        if isinstance(exc, ExtractionError) and (exc.timed_out or exc.error_kind in {
                "memory", "permission", "option", "filter", "hardware", "input"}):
            raise
        extractor._fallback_log("dv_segment", targets[0], exc)
        return ()
    finally:
        if not published:
            for frame in frames:
                frame.unlink(missing_ok=True)
