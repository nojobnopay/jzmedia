"""Bounded, fail-closed audit of FFmpeg 7.1 showinfo Dolby Vision frames.

The named showinfo filter is placed after accurate trim, before any colour
conversion. Its original timestamps are translated by the same offset as the
output packet filter. ``checksum=0`` reads metadata, never image planes. A frame
only becomes eligible after its final colour line. Progress has a separate pipe
so its writer cannot interrupt showinfo's multi-call metadata lines.
"""
from collections import deque
from dataclasses import dataclass
from fractions import Fraction
import math
import re
import threading
import time


class AuditError(RuntimeError):
    """A stable reason code, without a source URL or FFmpeg diagnostic text."""


@dataclass(frozen=True)
class Frame:
    number: int
    pts: Fraction
    duration: Fraction


_PREFIX = re.compile(r"^\[showinfo@dovi_input\s+@\s+[^\]]+\]\s*(.*)$")
_HEADER = re.compile(
    r"^n:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\S+\s+"
    r"duration:\s*(\d+)\s+duration_time:\S+\s+fmt:\S+\s+cl:\S+\s+"
    r"sar:\d+/\d+\s+s:\d+x\d+\s+i:[PTB]\s+iskey:[01]\s+type:[?IPBS]\s*$")
_TIMEBASE = re.compile(r"^config in time_base:\s*(\d+)/(\d+),\s*frame_rate:\s*(\d+)/(\d+)\s*$")
_COLOR_END = re.compile(r"^color_range:\S+\s+color_space:\S+\s+color_primaries:\S+\s+color_trc:\S+\s*$")
_INTEGER_FIELDS = re.compile(r"\b([a-z][a-z0-9_]*)=(-?\d+)(?=;|\s|$)")
_FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_ERROR = re.compile(
    r"error|failed|failure|invalid (?:data|rpu|dolby|dovi|nal)|unknown previous rpu|"
    r"crc mismatch|corrupt|dropping|discarding|non.monoton|miss(?:ing|ed) (?:rpu|frame)|"
    r"(?:dup|drop)=\s*[1-9]", re.I)
_PROGRESS = re.compile(r"^(frame|fps|speed|out_time_us|out_time_ms|out_time|total_size|"
                       r"dup_frames|drop_frames|progress|bitrate|stream_\d+_\d+_q)=(.*)$")
# print_report(is_last_report=1) writes this human line even with -nostats.
# It is one complete INFO message, unlike stdout's one-key-per-line protocol.
_FINAL_REPORT = re.compile(
    r"^frame= *(?P<frames>\d+) fps= *(?P<fps>\d+(?:\.\d)?) q= *(?P<q>-?\d+\.\d) "
    r"Lsize=(?:N/A| *\d+KiB) time=(?:N/A|-?\d{2,}:[0-5]\d:[0-5]\d\.\d{2}) "
    r"bitrate=(?:N/A| *\d+\.\dkbits/s)(?: dup=(?P<dup>\d+) drop=(?P<drop>\d+))? "
    r"speed= *(?:N/A|(?P<speed>" + _FLOAT + r")x)"
    r"(?: elapsed=\d+:[0-5]\d:[0-5]\d\.\d{2})? {4}$")
_EXACT_PREFIX = re.compile(r"^(\[showinfo@dovi_input @ 0x[0-9a-fA-F]+\] )(.*)$")
_MUX_FILE = (r"(?:(?:video|audio[0-7])_(?:init\.mp4|seg\d{5,}\.m4s)(?:\.tmp)?"
             r"|out_(?:video|audio[0-7])\.m3u8\.tmp)")
_MUX_OPENING = re.compile(r"(?:\[hls @ 0x[0-9a-fA-F]+\] )?Opening '" + _MUX_FILE + r"' for writing$")
_MUX_STANDALONE = re.compile(r"\[hls @ 0x[0-9a-fA-F]+\] Opening '" + _MUX_FILE + r"' for writing$")
_CHANNEL_ARRAYS = ("pivots", "mapping_idc", "poly_order", "poly_coef", "mmr_order", "mmr_constant", "mmr_coef")
_CHANNEL_SCHEMA = re.compile(r"^\s*" + "".join(
    name + r"=\{[0-9+\-\s,{}]*\};\s*" for name in _CHANNEL_ARRAYS)
    + r"nlq_offset=\d+;\s*vdr_in_max=\d+;\s*"
    + r"(?:linear_deadzone_slope=\d+;\s*linear_deadzone_threshold=\d+;?\s*)?$")


def _integers(text, required, *, allow_extra=False):
    pairs = _INTEGER_FIELDS.findall(text)
    values = dict(pairs)
    if (len(values) != len(pairs) or not set(required).issubset(values)
            or not allow_extra and set(values) != set(required)):
        raise AuditError("dovi_audit_metadata_incomplete")
    return {key: int(value) for key, value in values.items()}


def _only_integer_fields(text, fields):
    for name in fields:
        text = re.sub(r"\b" + name + r"=-?\d+;?", "", text, count=1)
    if text.strip("; \t"):
        raise AuditError("dovi_audit_metadata_incomplete")


class Audit:
    """Streaming frame audit; the publisher drains matched frames continuously.

    feed/close and match_samples can run in different threads. A full pending
    queue is an error, never permission to skip a frame. finish(rc) is called
    after process.wait() and reader.join(); repeating the same rc is harmless.
    """

    def __init__(self, *, max_pending=8192, max_line=128 * 1024,
                 max_record=256 * 1024, tail_bytes=64 * 1024,
                 timestamp_offset=Fraction(0)):
        if min(max_pending, max_line, max_record, tail_bytes) <= 0:
            raise ValueError("audit limits must be positive")
        try:
            offset = Fraction(str(timestamp_offset)) if isinstance(timestamp_offset, float) else Fraction(timestamp_offset)
        except (ValueError, TypeError, ZeroDivisionError, OverflowError):
            raise ValueError("timestamp offset must be a nonnegative microsecond fraction") from None
        # The command uses exactly six decimal places. Reject a more precise
        # offset rather than audit a different origin from FFmpeg's expression.
        if offset < 0 or 1000000 % offset.denominator:
            raise ValueError("timestamp offset must be a nonnegative microsecond fraction")
        self._timestamp_offset = offset
        self._timestamp_shift_ticks = None
        self._lock = threading.RLock()
        self._frames = deque()
        self._buffer = bytearray()
        self._tail = bytearray()
        self._limits = max_pending, max_line, max_record, tail_bytes
        self._record = None
        self._timebase = None
        self._nominal_frame_ticks = 0
        self._next_number = 0
        self._last_pts = None
        self.error = None
        self.eof = False
        self.returncode = None
        self.stderr_bytes = 0
        self.matched_frames = 0
        self.progress = {}
        self._reader = None
        self._progress_reader = None
        self._has_progress_stream = False
        self.progress_eof = True
        self.progress_bytes = 0
        self._progress_group = {}
        self._progress_ended = False
        self._filter_prefix = None
        self._logical_line = None
        self._logical_prefix = None
        self._demux_record_events = 0
        self._demux_events = 0
        self._demux_recovered_lines = 0
        self._final_report_frames = None

    @property
    def tail(self):
        with self._lock:
            return self._tail.decode("utf-8", errors="replace")

    @property
    def pending(self):
        with self._lock:
            return len(self._frames)

    def _fail(self, reason):
        self.error = self.error or reason
        raise AuditError(self.error)

    def check(self):
        with self._lock:
            if self.error:
                raise AuditError(self.error)

    def feed(self, block):
        """Consume raw bytes, retaining only a bounded partial line and tail."""
        if not isinstance(block, bytes):
            raise TypeError("feed requires bytes")
        with self._lock:
            if self.eof:
                self._fail("dovi_audit_after_eof")
            self.stderr_bytes += len(block)
            self._tail.extend(block[-self._limits[3]:])
            del self._tail[:-self._limits[3]]
            # Process one line at a time, so a large input block cannot create
            # an unbounded buffer even before a terminating newline arrives.
            for part in block.splitlines(keepends=True):
                self._buffer.extend(part)
                if len(self._buffer) > self._limits[1]:
                    self._fail("dovi_audit_line_overflow")
                if self._buffer.endswith((b"\n", b"\r")):
                    try:
                        line = self._buffer.decode("utf-8", errors="strict").rstrip("\r\n")
                    except UnicodeDecodeError:
                        self._fail("dovi_audit_log_encoding")
                    self._buffer.clear()
                    try:
                        recovering = self._logical_line is not None
                        logical = self._demux_line(line)
                        if logical is not None:
                            self._parse_line(logical)
                            if recovering:
                                self._demux_recovered_lines += 1
                    except AuditError as exc:
                        # Helpers also reject malformed metadata. Persist that
                        # failure before the reader starts draining/discarding.
                        self.error = self.error or str(exc)
                        raise

    def feed_line(self, line):
        """Convenience for a caller that already reads complete stderr lines."""
        if isinstance(line, str):
            line = line.encode("utf-8")
        self.feed(line if line.endswith(b"\n") else line + b"\n")

    def _mux_event(self):
        self._demux_record_events += 1
        self._demux_events += 1
        if self._demux_record_events > 32:
            self._fail("dovi_audit_mux_log_overflow")

    def _opening_suffix(self, body):
        if "Opening '" not in body:
            return None
        # The exact fixed message is the only material that can be removed.
        # Every filename is generated by this HLS producer, never a source URL.
        match = _MUX_OPENING.search(body)
        if match is None or body.count("Opening '") != 1:
            self._fail("dovi_audit_mux_log_invalid")
        return body[:match.start()]

    def _demux_line(self, line):
        """Undo a complete known mux event inserted between av_log calls.

        showinfo emits an RPU logical line using many locked av_log calls. An
        HLS Opening call may insert its complete message/newline between them;
        the next showinfo callback supplies its exact prefix again. No RPU byte
        is synthesized: retain the prefix bytes, remove only that full control
        message and its newline, and concatenate the same-pointer continuation.
        The reconstructed line still has to pass the original full schema.
        """
        if _ERROR.search(line):
            self._fail("dovi_audit_ffmpeg_error")
        named = _EXACT_PREFIX.fullmatch(line)
        if "showinfo@dovi_input" in line and named is None:
            self._fail("dovi_audit_log_interleaved")
        if named:
            prefix, body = named.groups()
            if self._filter_prefix is None:
                self._filter_prefix = prefix
            elif prefix != self._filter_prefix:
                self._fail("dovi_audit_log_interleaved")
        if self._logical_line is not None:
            if _MUX_STANDALONE.fullmatch(line):
                self._mux_event()
                return None
            if not named or named[1] != self._logical_prefix:
                self._fail("dovi_audit_log_interleaved")
            body = named[2]
            if body.lstrip().startswith(("n:", "config ", "side data -", "data mapping:", "color_range:")):
                self._fail("dovi_audit_log_interleaved")
            before_opening = self._opening_suffix(body)
            continuation = body if before_opening is None else before_opening
            self._logical_line += continuation
            if len(self._logical_line.encode("utf-8")) > self._limits[1]:
                self._fail("dovi_audit_line_overflow")
            if before_opening is not None:
                self._mux_event()
                return None
            logical = self._logical_line
            self._logical_line = self._logical_prefix = None
            return logical
        if named:
            before_opening = self._opening_suffix(named[2])
            if before_opening is not None:
                if self._record is None and not named[2].lstrip().startswith("n:"):
                    self._fail("dovi_audit_log_interleaved")
                self._mux_event()
                self._logical_prefix = named[1]
                self._logical_line = named[1] + before_opening
                return None
        return line

    def _parse_line(self, line):
        if self.error:
            raise AuditError(self.error)
        if _ERROR.search(line):
            self._fail("dovi_audit_ffmpeg_error")
        final_report = _FINAL_REPORT.fullmatch(line)
        if final_report:
            if self._record or self._final_report_frames is not None:
                self._fail("dovi_audit_final_report_order")
            if any(not math.isfinite(float(final_report[name])) for name in ("fps", "q", "speed")
                   if final_report[name] is not None):
                self._fail("dovi_audit_final_report_invalid")
            frames = int(final_report["frames"])
            if (frames != self._next_number
                    or any(int(final_report[name] or 0) for name in ("dup", "drop"))):
                self._fail("dovi_audit_frame_count")
            self._final_report_frames = frames
            return
        progress = _PROGRESS.fullmatch(line)
        if progress:
            if self._has_progress_stream:
                self._fail("dovi_audit_progress_wrong_stream")
            key, value = progress.groups()
            if len(value) > 128 or (key not in self.progress and len(self.progress) >= 64):
                self._fail("dovi_audit_progress_overflow")
            if key in ("dup_frames", "drop_frames") and value != "0":
                self._fail("dovi_audit_frame_count")
            self.progress[key] = value
            return
        named = _PREFIX.fullmatch(line)
        if not named:
            # A damaged/interleaved named record is not ordinary stderr.
            if "showinfo@dovi_input" in line:
                self._fail("dovi_audit_log_interleaved")
            return
        body = named[1]
        if self._final_report_frames is not None:
            self._fail("dovi_audit_final_report_order")
        if re.search(r"\[[^\]\n]{1,128}\s+@\s+0x[0-9a-fA-F]+\]", body):
            self._fail("dovi_audit_log_interleaved")
        tb = _TIMEBASE.fullmatch(body)
        if tb:
            numerator, denominator, fps_numerator, fps_denominator = map(int, tb.groups())
            if not numerator or not denominator or self._timebase is not None or self._record:
                self._fail("dovi_audit_timebase")
            self._timebase = Fraction(numerator, denominator)
            if fps_numerator > 0 and fps_denominator > 0:
                nominal_ticks = Fraction(fps_denominator, fps_numerator) / self._timebase
                self._nominal_frame_ticks = -(-nominal_ticks.numerator // nominal_ticks.denominator)
            # Match the packet expression's explicit floor(offset/TB+0.5),
            # including AVExpr's double arithmetic. Quantize the offset once:
            # per-PTS llrint at half ticks could change frame intervals, while
            # ceil could over-shift a nominal integer at a floating boundary.
            self._timestamp_shift_ticks = math.floor(
                float(self._timestamp_offset) / float(self._timebase) + 0.5)
            return
        if body.startswith("config out time_base:"):
            return
        header = _HEADER.fullmatch(body)
        if header:
            if self._record or self._timebase is None:
                self._fail("dovi_audit_record_incomplete")
            number, pts, duration = map(int, header.groups())
            if not re.search(r"\bfmt:vaapi(?=\s)", body):
                self._fail("dovi_audit_software_decode")
            if number != self._next_number or duration <= 0:
                self._fail("dovi_audit_frame_sequence")
            timestamp = (pts - self._timestamp_shift_ticks) * self._timebase
            # A common audio/video trim origin preserves their relative
            # offset. The first retained video picture may arrive up to one
            # nominal frame rounded into its input timebase after that origin.
            # A coarse one-tick/frame clock must not allow two frames' delay.
            first_bound = max(duration, self._nominal_frame_ticks) * self._timebase
            if (self._last_pts is None and not 0 <= timestamp <= first_bound) or (
                    self._last_pts is not None and timestamp <= self._last_pts):
                self._fail("dovi_audit_pts")
            self._record = {"frame": Frame(number, timestamp, duration * self._timebase),
                            "bytes": len(body), "header": False, "mapping": False,
                            "channels": [], "color": False}
            return
        if body.startswith("n:"):
            self._fail("dovi_audit_frame_header")
        record = self._record
        if record is None:
            if body:
                self._fail("dovi_audit_orphan_metadata")
            return
        record["bytes"] += len(body)
        if record["bytes"] > self._limits[2]:
            self._fail("dovi_audit_record_overflow")
        if "side data - Dolby Vision Metadata:" in body:
            if record["header"]:
                self._fail("dovi_audit_duplicate_metadata")
            fields = _integers(body, (
                "rpu_type", "rpu_format", "vdr_rpu_profile", "vdr_rpu_level",
                "chroma_resampling_explicit_filter_flag", "coef_data_type", "coef_log2_denom",
                "vdr_rpu_normalized_idc", "bl_video_full_range_flag", "bl_bit_depth",
                "el_bit_depth", "vdr_bit_depth", "spatial_resampling_filter_flag",
                "el_spatial_resampling_filter_flag", "disable_residual_flag"))
            _only_integer_fields(body.split("Dolby Vision Metadata:", 1)[1], fields)
            if (fields["rpu_type"] != 2 or fields["disable_residual_flag"] != 1
                    or fields["bl_bit_depth"] != 10):
                self._fail("dovi_audit_metadata_invalid")
            record["header"] = True
        elif body.startswith("data mapping:"):
            if not record["header"] or record["mapping"]:
                self._fail("dovi_audit_metadata_order")
            fields = _integers(body, ("vdr_rpu_id", "mapping_color_space", "mapping_chroma_format_idc",
                                      "nlq_method_idc", "num_x_partitions", "num_y_partitions"))
            _only_integer_fields(body.split("data mapping:", 1)[1], fields)
            record["mapping"] = True
        elif "channel " in body or "color metadata:" in body:
            if not record["mapping"] or record["color"]:
                self._fail("dovi_audit_metadata_order")
            channels = [int(number) for number in re.findall(r"\bchannel (\d+):", body)]
            for channel in channels:
                if channel != len(record["channels"]):
                    self._fail("dovi_audit_metadata_channels")
                record["channels"].append(channel)
            # A channel can occupy its own line or all three can precede the
            # colour record on one line (stock showinfo's NLQ-none case).
            if channels:
                for channel_body in re.split(r"\bchannel \d+:", body)[1:]:
                    channel_data = channel_body.split("color metadata:")[0]
                    if not _CHANNEL_SCHEMA.fullmatch(channel_data):
                        self._fail("dovi_audit_metadata_channel_incomplete")
                    for name in _CHANNEL_ARRAYS:
                        if not re.search(r"\b" + name + r"=\{[^;]*\}", channel_body):
                            self._fail("dovi_audit_metadata_channel_incomplete")
                    depth = 0
                    for char in channel_data:
                        depth += (char == "{") - (char == "}")
                        if depth < 0:
                            self._fail("dovi_audit_metadata_channel_incomplete")
                    if depth:
                        self._fail("dovi_audit_metadata_channel_incomplete")
                    _integers(channel_data, ("nlq_offset", "vdr_in_max"), allow_extra=True)
            if "color metadata:" in body:
                if record["channels"] != [0, 1, 2]:
                    self._fail("dovi_audit_metadata_channels")
                color = body.split("color metadata:", 1)[1]
                fields = _integers(color, (
                    "dm_metadata_id", "scene_refresh_flag", "signal_eotf", "signal_eotf_param0",
                    "signal_eotf_param1", "signal_eotf_param2", "signal_bit_depth",
                    "signal_color_space", "signal_chroma_format", "signal_full_range_flag",
                    "source_min_pq", "source_max_pq", "source_diagonal"))
                if fields["signal_color_space"] != 2 or not 8 <= fields["signal_bit_depth"] <= 16:
                    self._fail("dovi_audit_metadata_invalid")
                remainder = color
                for name, count in (("ycc_to_rgb_matrix", 9), ("ycc_to_rgb_offset", 3),
                                    ("rgb_to_lms_matrix", 9)):
                    matrix = re.search(r"\b" + name + r"=\{\s*([^}]+)\}", color)
                    values = matrix[1].split() if matrix else []
                    if len(values) != count or any(not re.fullmatch(_FLOAT, value)
                            or not math.isfinite(float(value)) for value in values):
                        self._fail("dovi_audit_metadata_matrix")
                    remainder = remainder.replace(matrix[0], "", 1)
                _only_integer_fields(remainder, fields)
                if not re.search(r"source_diagonal=\d+;\s*$", color):
                    self._fail("dovi_audit_metadata_incomplete")
                record["color"] = True
        elif _COLOR_END.fullmatch(body):
            if not (record["header"] and record["mapping"] and record["color"]):
                self._fail("dovi_audit_metadata_missing")
            if len(self._frames) >= self._limits[0]:
                self._fail("dovi_audit_backlog")
            frame = record["frame"]
            self._frames.append(frame)
            self._last_pts = frame.pts
            self._next_number += 1
            self._record = None
            self._demux_record_events = 0
        elif body.startswith(("side data -", "UUID=", "User Data=")) or not body:
            # Other side data may be printed before/after the Dolby record.
            return
        else:
            self._fail("dovi_audit_unparsed_record")

    def match_samples(self, samples, *, tolerance=Fraction(0)):
        """Atomically match a whole fragment, then consume its audit frames.

        samples are (PTS, duration) fractions in display order. A half mux tick
        may be supplied for unavoidable timestamp rescaling, never a frame.
        False means a still-running reader has not delivered enough records.
        """
        with self._lock:
            self.check()
            if not samples:
                self._fail("dovi_audit_empty_fragment")
            if len(self._frames) < len(samples):
                if self.eof:
                    self._fail("dovi_audit_missing_frames")
                return False
            for frame, (pts, duration) in zip(self._frames, samples):
                if (duration <= 0 or tolerance < 0 or tolerance >= duration / 4
                        or abs(frame.pts - pts) > tolerance
                        or abs(frame.duration - duration) > self._timebase + 2 * tolerance):
                    self._fail("dovi_audit_sample_pts")
            for _ in samples:
                self._frames.popleft()
            self.matched_frames += len(samples)
            return True

    def close(self):
        with self._lock:
            if self.eof:
                return
            self.eof = True
            if self._buffer or self._record or self._logical_line is not None:
                self._fail("dovi_audit_truncated_log")
            if not self._next_number:
                self._fail("dovi_audit_no_frames")
            self.check()

    def finish(self, returncode):
        with self._lock:
            if self.returncode is not None and self.returncode != returncode:
                self._fail("dovi_audit_returncode_changed")
            self.returncode = returncode
            if returncode != 0:
                self._fail("dovi_audit_process_failed")
            if self._reader is not None and (self.readers_alive() or not self.eof or not self.progress_eof):
                self._fail("dovi_audit_reader_incomplete")
            self.close()
            self.check()

    def assert_complete(self):
        with self._lock:
            self.check()
            if not self.eof or not self.progress_eof or self.returncode != 0 or self._frames or self._record:
                self._fail("dovi_audit_unmatched_eof")

    def _parse_progress_line(self, line):
        """Parse one controlled stdout line without touching the RPU record."""
        with self._lock:
            self.check()
            if self._progress_ended:
                self._fail("dovi_audit_progress_after_end")
            match = _PROGRESS.fullmatch(line)
            if not match:
                self._fail("dovi_audit_progress_format")
            key, value = match.groups()
            value = value.strip()
            if len(key) > 64 or len(value) > 128 or (
                    key not in self.progress and len(self.progress) >= 64):
                self._fail("dovi_audit_progress_overflow")
            if key in self._progress_group:
                self._fail("dovi_audit_progress_duplicate_key")
            if key in ("frame", "dup_frames", "drop_frames"):
                valid = bool(re.fullmatch(r"\d+", value))
            elif key in ("out_time_us", "out_time_ms", "total_size"):
                valid = value == "N/A" or bool(re.fullmatch(r"-?\d+", value))
            elif key == "out_time":
                valid = value == "N/A" or bool(re.fullmatch(r"-?\d+:\d{2}:\d{2}\.\d{6}", value))
            elif key == "progress":
                valid = value in ("continue", "end")
            else:
                numeric = value
                if key == "speed":
                    numeric = value[:-1] if value.endswith("x") else ""
                elif key == "bitrate":
                    numeric = value[:-7].strip() if value.endswith("kbits/s") else ""
                valid = value == "N/A" or bool(re.fullmatch(_FLOAT, numeric)) and math.isfinite(float(numeric))
            if not valid:
                self._fail("dovi_audit_progress_format")
            if key in ("dup_frames", "drop_frames") and int(value) != 0:
                self._fail("dovi_audit_frame_count")
            self._progress_group[key] = value
            self.progress[key] = value
            if key != "progress":
                return None
            if not {"frame", "out_time_us", "dup_frames", "drop_frames"}.issubset(self._progress_group):
                self._fail("dovi_audit_progress_incomplete")
            if value == "end":
                self._progress_ended = True
            snapshot = dict(self.progress)
            self._progress_group.clear()
            return snapshot

    def readers_alive(self):
        """Whether either configured pipe reader is still running."""
        return any(reader is not None and reader.is_alive()
                   for reader in (self._reader, self._progress_reader))

    def join_readers(self, timeout=None):
        """Join both pipe readers using one overall timeout budget."""
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        for reader in (self._reader, self._progress_reader):
            if reader is None or reader is threading.current_thread():
                continue
            remaining = None if deadline is None else max(0, deadline - time.monotonic())
            reader.join(timeout=remaining)

    def start_reader(self, stream, *, progress_stream=None, on_error=None, on_progress=None):
        """Drain separate metadata/progress pipes, even after an audit error.

        The returned stderr Thread is retained for older callers. New callers
        join_readers()/readers_alive() and finish() only after both streams EOF.
        """
        if self._reader is not None:
            raise RuntimeError("audit reader already started")
        self._has_progress_stream = progress_stream is not None
        self.progress_eof = not self._has_progress_stream
        notified = False

        def notify_error(exc):
            nonlocal notified
            with self._lock:
                report = not notified
                notified = True
            if report and on_error:
                on_error(exc)

        def run():
            previous = None
            try:
                read = getattr(stream, "read1", stream.read)
                while True:
                    block = read(16 * 1024)
                    if not block:
                        break
                    try:
                        self.feed(block)
                        if not self._has_progress_stream:
                            current = dict(self.progress)
                            if on_progress and current != previous:
                                on_progress(current)
                            previous = current
                    except AuditError as exc:
                        notify_error(exc)
                self.close()
            except Exception as exc:  # Reader/callback failure must fail closed.
                if not isinstance(exc, AuditError):
                    with self._lock:
                        self.error = self.error or "dovi_audit_reader_failed"
                notify_error(AuditError(self.error or "dovi_audit_reader_failed"))
            finally:
                with self._lock:
                    self.eof = True

        def run_progress():
            buffer = bytearray()
            try:
                read = getattr(progress_stream, "read1", progress_stream.read)
                while True:
                    block = read(4096)
                    if not block:
                        break
                    with self._lock:
                        self.progress_bytes += len(block)
                    if self.error:
                        continue  # Keep FFmpeg's stdout pipe drained after failure.
                    try:
                        for part in block.splitlines(keepends=True):
                            buffer.extend(part)
                            if len(buffer) > 512:
                                self._fail("dovi_audit_progress_overflow")
                            if buffer.endswith((b"\n", b"\r")):
                                try:
                                    line = buffer.decode("ascii", errors="strict").rstrip("\r\n")
                                except UnicodeDecodeError:
                                    self._fail("dovi_audit_progress_format")
                                buffer.clear()
                                snapshot = self._parse_progress_line(line)
                                if snapshot is not None and on_progress:
                                    on_progress(snapshot)
                    except AuditError as exc:
                        notify_error(exc)
                with self._lock:
                    if not self.error and (buffer or self._progress_group or not self._progress_ended):
                        self._fail("dovi_audit_progress_incomplete")
            except Exception as exc:
                with self._lock:
                    self.error = self.error or (str(exc) if isinstance(exc, AuditError)
                                               else "dovi_audit_progress_reader_failed")
                notify_error(AuditError(self.error))
            finally:
                with self._lock:
                    self.progress_eof = True

        self._reader = threading.Thread(target=run, name="dovi-stderr-audit", daemon=True)
        if self._has_progress_stream:
            self._progress_reader = threading.Thread(target=run_progress, name="dovi-stdout-progress", daemon=True)
        self._reader.start()
        if self._progress_reader is not None:
            self._progress_reader.start()
        return self._reader

    def diagnostics(self):
        with self._lock:
            return {"error": self.error, "eof": self.eof, "returncode": self.returncode,
                    "audited_frames": self._next_number, "matched_frames": self.matched_frames,
                    "pending_frames": len(self._frames), "stderr_bytes": self.stderr_bytes,
                    "timestamp_offset": str(self._timestamp_offset),
                    "timestamp_shift_ticks": self._timestamp_shift_ticks,
                    "progress_eof": self.progress_eof, "progress_bytes": self.progress_bytes,
                    "demux_events": self._demux_events,
                    "demux_recovered_lines": self._demux_recovered_lines,
                    "human_final_frames": self._final_report_frames,
                    "progress": dict(self.progress)}
