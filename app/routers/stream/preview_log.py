"""抽帧日志：完整采集时间戳，限制保留的诊断文本。"""
import os
import re


_PTS = re.compile(rb"\bn:\s*(\d+)\s+pts:[^\r\n]*?\bpts_time:(\S+)(?=\s)")
_TAIL_BYTES = 256 * 1024


class FrameLog(str):
    def __new__(cls, text, *, frame_times=(), frame_numbers=(), stderr_bytes=0, elapsed=0.0):
        result = super().__new__(cls, text)
        result.frame_times = tuple(frame_times)
        result.frame_numbers = tuple(frame_numbers)
        result.stderr_bytes = stderr_bytes
        result.elapsed = elapsed
        return result


def read_frame_log(log, elapsed):
    """流式读取全部 showinfo；长 metadata/警告只占固定大小的读取缓冲区。"""
    log.seek(0)
    times, numbers = [], []
    carry, offset, last_end = b"", 0, 0
    while True:
        block = log.read(64 * 1024)
        data = carry + (block if block else b"\n")
        base = offset - len(carry)
        for match in _PTS.finditer(data):
            end = base + match.end()
            if end > last_end:
                try:
                    times.append(float(match[2]))
                except ValueError:
                    # 不丢弃坏记录，否则可能把后一张画面的 PTS 误当成实际首帧。
                    times.append(float("nan"))
                numbers.append(int(match[1]))
                last_end = end
        if not block:
            break
        offset += len(block)
        # 一条 showinfo 的时间戳字段在行首；保留跨读取边界的字段。
        carry = data[-4096:]
    size = log.seek(0, os.SEEK_END)
    log.seek(max(0, size - _TAIL_BYTES))
    return FrameLog(log.read().decode("utf-8", errors="replace"), frame_times=times,
                    frame_numbers=numbers,
                    stderr_bytes=size, elapsed=elapsed)


def error_kind(log):
    """只记录有限错误类别，避免把片源 URL、路径或凭据写入任务日志。"""
    size = log.seek(0, os.SEEK_END)
    log.seek(max(0, size - _TAIL_BYTES))
    tail = log.read().lower()
    for kind, markers in (
        ("memory", (b"cannot allocate memory", b"out of memory")),
        ("permission", (b"permission denied", b"operation not permitted")),
        ("option", (b"unrecognized option", b"option not found", b"error parsing options")),
        ("hardware", (b"device creation failed", b"no device available", b"failed to initialise vaapi")),
        ("filter", (b"error reinitializing filters", b"error applying option", b"no such filter",
                    b"error while filtering", b"image dimensions must be divisible")),
        ("input", (b"invalid data found", b"error opening input", b"no such file or directory")),
        ("decoder", (b"error while opening decoder", b"error while decoding", b"decoding failed")),
    ):
        if any(marker in tail for marker in markers):
            return kind
    return "unknown"


class ExtractionError(RuntimeError):
    def __init__(self, message, *, reason, returncode=None, timed_out=False,
                 stderr_bytes=0, elapsed=0.0, limit=0, error_kind="unknown"):
        super().__init__(message)
        self.reason = reason
        self.returncode = returncode
        self.timed_out = timed_out
        self.stderr_bytes = stderr_bytes
        self.elapsed = elapsed
        self.error_kind = error_kind
        self.diagnostic = (f"reason={reason} returncode={returncode} timed_out={timed_out} "
                           f"wall_s={elapsed:.3f} limit_s={limit} stderr_bytes={stderr_bytes} "
                           f"error_kind={error_kind}")
