"""Invented metadata and a tiny reporting fixture; no real media or GPU."""
from fractions import Fraction
import io
import os
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest

from app.playback.dovi_audit import Audit, AuditError


PREFIX = "[showinfo@dovi_input @ 0xabc] "
CONFIG = PREFIX + "config in time_base: 1/24, frame_rate: 24/1\n"


def record(number=0, *, pts=None, duration=1, fmt="vaapi", timebase=Fraction(1, 24)):
    """Complete stock 7.1 showinfo record using invented P5 metadata."""
    pts = number if pts is None else pts
    header = (f"n: {number} pts: {pts} pts_time:{float(pts * timebase):.6f} "
              f"duration: {duration} duration_time:{float(duration * timebase):.6f} "
              f"fmt:{fmt} cl:left sar:1/1 s:64x64 i:P iskey:1 type:I ")
    metadata = "  side data - Dolby Vision Metadata: " + "; ".join(
        f"{key}={value}" for key, value in {
            "rpu_type": 2, "rpu_format": 18, "vdr_rpu_profile": 0, "vdr_rpu_level": 0,
            "chroma_resampling_explicit_filter_flag": 0, "coef_data_type": 0,
            "coef_log2_denom": 23, "vdr_rpu_normalized_idc": 1, "bl_video_full_range_flag": 1,
            "bl_bit_depth": 10, "el_bit_depth": 10, "vdr_bit_depth": 12,
            "spatial_resampling_filter_flag": 0, "el_spatial_resampling_filter_flag": 0,
            "disable_residual_flag": 1}.items())
    mapping = ("    data mapping: vdr_rpu_id=0; mapping_color_space=0; "
               "mapping_chroma_format_idc=0; nlq_method_idc=-1; num_x_partitions=1; num_y_partitions=1")
    # NLQ-none channels are concatenated onto the colour metadata line by
    # stock FFmpeg, rather than one prefixed log line per channel.
    channels = "".join(
        f"      channel {channel}: pivots={{ 0 1023 }}; mapping_idc={{ 0 }}; "
        "poly_order={ 1 }; poly_coef={ {0, 1, 0} }; mmr_order={ 0 }; "
        "mmr_constant={ 0 }; mmr_coef={ {} }; nlq_offset=0; vdr_in_max=0; "
        for channel in range(3))
    color = ("    color metadata: dm_metadata_id=0; scene_refresh_flag=0; "
             "ycc_to_rgb_matrix={ 1 0 0 0 1 0 0 0 1 }; ycc_to_rgb_offset={ 0 .5 .5 }; "
             "rgb_to_lms_matrix={ 1 0 0 0 1 0 0 0 1 }; signal_eotf=65535; "
             "signal_eotf_param0=0; signal_eotf_param1=0; signal_eotf_param2=0; "
             "signal_bit_depth=12; signal_color_space=2; signal_chroma_format=0; "
             "signal_full_range_flag=1; source_min_pq=0; source_max_pq=3000; source_diagonal=42; ")
    ending = "color_range:pc color_space:unknown color_primaries:unknown color_trc:unknown"
    return "".join(PREFIX + line + "\n" for line in (header, metadata, mapping, channels + color, ending))


def test_complete_records_are_committed_only_at_color_ending():
    audit = Audit()
    audit.feed(CONFIG.encode())
    lines = record().splitlines(keepends=True)
    audit.feed("".join(lines[:-1]).encode())
    assert audit.pending == 0
    assert audit.match_samples([(Fraction(0), Fraction(1, 24))]) is False
    audit.feed(lines[-1].encode())
    assert audit.match_samples([(Fraction(0), Fraction(1, 24))])
    audit.finish(0)
    audit.finish(0)
    audit.assert_complete()
    assert audit.diagnostics()["audited_frames"] == 1


def test_chunk_boundaries_and_bounded_tail():
    audit = Audit(tail_bytes=31)
    data = (CONFIG + record(0) + record(1)).encode()
    for index in range(0, len(data), 7):
        audit.feed(data[index:index + 7])
    assert len(audit.tail.encode()) <= 31
    assert audit.stderr_bytes == len(data)
    assert audit.match_samples([(Fraction(0), Fraction(1, 24)), (Fraction(1, 24), Fraction(1, 24))])
    audit.finish(0)
    audit.assert_complete()


@pytest.mark.parametrize(("old", "new", "reason"), [
    ("disable_residual_flag=1", "disable_residual_flag=0", "metadata_invalid"),
    ("signal_color_space=2", "signal_color_space=0", "metadata_invalid"),
    ("fmt:vaapi", "fmt:yuv420p10le", "software_decode"),
    ("source_diagonal=42;", "", "metadata_incomplete"),
    ("channel 1:", "channel 2:", "metadata_channels"),
    ("rgb_to_lms_matrix={ 1 0 0 0 1 0 0 0 1 }", "rgb_to_lms_matrix={ nan }", "metadata_matrix"),
    ("n: 0 pts: 0", "n: 1 pts: 0", "frame_sequence"),
    ("pts: 0", "pts: NOPTS", "frame_header"),
    ("pts: 0", "pts: 2", "audit_pts"),
])
def test_invalid_records_fail_closed(old, new, reason):
    audit = Audit()
    audit.feed(CONFIG.encode())
    with pytest.raises(AuditError, match=reason):
        audit.feed(record().replace(old, new).encode())
    assert audit.pending == 0
    with pytest.raises(AuditError):
        audit.match_samples([(Fraction(0), Fraction(1, 24))])


@pytest.mark.parametrize("damage", ["missing", "truncated", "interleaved", "error", "crc", "invalid_nal"])
def test_partial_or_missing_metadata_never_commits(damage):
    audit = Audit()
    value = CONFIG + record()
    if damage == "missing":
        value = "\n".join(line for line in value.splitlines() if "Dolby Vision Metadata" not in line) + "\n"
    elif damage == "truncated":
        value = value[:-10]
    elif damage == "interleaved":
        value = value.replace(PREFIX + "n:", "[hevc @ 0xdef] " + PREFIX + "n:")
    elif damage == "error":
        value = "[hevc @ 0xdef] Error parsing DOVI NAL unit.\n" + value
    elif damage == "crc":
        value = "[hevc @ 0xdef] RPU CRC mismatch\n" + value
    else:
        value = "[hevc @ 0xdef] Invalid NAL unit 18, skipping.\n" + value
    with pytest.raises(AuditError):
        audit.feed(value.encode())
        audit.finish(0)
    assert audit.error


def test_non_monotonic_pts_and_duplicate_frame_rejected():
    for number, pts in ((1, 0), (0, 1), (2, 1)):
        audit = Audit()
        audit.feed((CONFIG + record()).encode())
        with pytest.raises(AuditError):
            audit.feed(record(number, pts=pts).encode())


@pytest.mark.parametrize("first_pts", [0, 1])
def test_first_frame_keeps_common_trim_origin_within_one_frame(first_pts):
    audit = Audit()
    audit.feed((CONFIG + record(0, pts=first_pts) + record(1, pts=first_pts + 1)).encode())
    assert audit.match_samples([(Fraction(first_pts, 24), Fraction(1, 24)),
                                (Fraction(first_pts + 1, 24), Fraction(1, 24))])
    audit.finish(0)
    audit.assert_complete()


@pytest.mark.parametrize("first_pts", [-1, 2])
def test_first_frame_cannot_precede_trim_or_arrive_more_than_one_frame_late(first_pts):
    audit = Audit()
    with pytest.raises(AuditError, match="audit_pts"):
        audit.feed((CONFIG + record(0, pts=first_pts)).encode())


def test_original_frame_timestamps_are_translated_by_exact_trim_offset():
    audit = Audit(timestamp_offset=Fraction(15))
    config = PREFIX + "config in time_base: 1/1000, frame_rate: 24000/1001\n"
    source_pts = (15000, 15042, 15083)
    audit.feed((config + "".join(record(index, pts=pts, duration=41, timebase=Fraction(1, 1000))
                               for index, pts in enumerate(source_pts))).encode())
    expected = [(Fraction(pts - 15000, 1000), Fraction(41, 1000)) for pts in source_pts]
    assert audit.match_samples(expected)
    audit.finish(0)
    audit.assert_complete()
    assert audit.diagnostics()["timestamp_offset"] == "15"
    assert audit.diagnostics()["timestamp_shift_ticks"] == 15000


def test_fractional_trim_matches_explicit_setts_nearest_offset_quantization():
    audit = Audit(timestamp_offset=Fraction("1.234567"))
    config = PREFIX + "config in time_base: 1/1000, frame_rate: 24000/1001\n"
    # The command subtracts floor(1234.567+0.5), not a per-PTS rounded difference.
    # Subtracting Fraction seconds directly would expect a different clock.
    audit.feed((config + record(0, pts=1235, duration=41, timebase=Fraction(1, 1000))
                + record(1, pts=1276, duration=41, timebase=Fraction(1, 1000))).encode())
    assert audit.match_samples([(Fraction(0), Fraction(41, 1000)),
                                (Fraction(41, 1000), Fraction(41, 1000))])
    audit.finish(0)
    audit.assert_complete()
    assert audit.diagnostics()["timestamp_shift_ticks"] == 1235


@pytest.mark.parametrize(("offset", "shift"), [
    ("0.150000", 150), ("0.150001", 150), ("0.150500", 151), ("0.150999", 151),
])
def test_offset_quantization_is_one_constant_shift_even_at_half_ticks(offset, shift):
    audit = Audit(timestamp_offset=offset)
    config = PREFIX + "config in time_base: 1/1000, frame_rate: 24000/1001\n"
    source_pts = (151, 192, 234, 275)
    audit.feed((config + "".join(record(index, pts=pts, duration=41, timebase=Fraction(1, 1000))
                               for index, pts in enumerate(source_pts))).encode())
    # For a half-tick offset, per-frame llrint would alternately round ties up
    # or down. The explicit integer shift preserves every original interval.
    expected = [(Fraction(pts - shift, 1000), Fraction(41, 1000)) for pts in source_pts]
    assert audit.match_samples(expected)
    audit.finish(0)
    audit.assert_complete()
    assert audit.diagnostics()["timestamp_shift_ticks"] == shift


@pytest.mark.parametrize(("offset", "shift"), [
    ("4.001000", 4001), ("4.009000", 4009),
])
def test_nearest_offset_does_not_over_shift_integer_tick_at_double_boundary(offset, shift):
    audit = Audit(timestamp_offset=offset)
    config = PREFIX + "config in time_base: 1/1000, frame_rate: 24000/1001\n"
    # 4.001/.001 is slightly above 4001 in AVExpr double arithmetic. Applying
    # ceil would wrongly make a source picture exactly at the trim negative.
    audit.feed((config + record(0, pts=shift, duration=41, timebase=Fraction(1, 1000))
                + record(1, pts=shift + 42, duration=41, timebase=Fraction(1, 1000))).encode())
    assert audit.match_samples([(Fraction(0), Fraction(41, 1000)),
                                (Fraction(42, 1000), Fraction(41, 1000))])
    audit.finish(0)
    audit.assert_complete()
    assert audit.diagnostics()["timestamp_shift_ticks"] == shift


@pytest.mark.parametrize(("raw_pts", "accepted"), [(150, False), (151, True)])
def test_half_tick_offset_still_rejects_a_negative_normalized_first_picture(raw_pts, accepted):
    audit = Audit(timestamp_offset="0.150500")
    value = (PREFIX + "config in time_base: 1/1000, frame_rate: 24000/1001\n"
             + record(0, pts=raw_pts, duration=41, timebase=Fraction(1, 1000)))
    if accepted:
        audit.feed(value.encode())
        assert audit.match_samples([(Fraction(0), Fraction(41, 1000))])
        audit.finish(0)
        audit.assert_complete()
    else:
        with pytest.raises(AuditError, match="audit_pts"):
            audit.feed(value.encode())


@pytest.mark.parametrize(("normalized", "accepted"), [(-1, False), (0, True), (42, True), (43, False)])
def test_trimmed_first_picture_is_bounded_to_nominal_frame_rounded_into_input_ticks(normalized, accepted):
    audit = Audit(timestamp_offset=Fraction("1.234567"))
    value = (PREFIX + "config in time_base: 1/1000, frame_rate: 24000/1001\n"
             + record(0, pts=1235 + normalized, duration=41, timebase=Fraction(1, 1000)))
    if accepted:
        audit.feed(value.encode())
        assert audit.match_samples([(Fraction(normalized, 1000), Fraction(41, 1000))])
        audit.finish(0)
        audit.assert_complete()
    else:
        with pytest.raises(AuditError, match="audit_pts"):
            audit.feed(value.encode())


def test_unknown_frame_rate_does_not_add_a_rounding_frame_to_first_picture_bound():
    audit = Audit()
    config = PREFIX + "config in time_base: 1/1000, frame_rate: 1/0\n"
    with pytest.raises(AuditError, match="audit_pts"):
        audit.feed((config + record(0, pts=42, duration=41, timebase=Fraction(1, 1000))).encode())


@pytest.mark.parametrize("offset", [Fraction(-1), Fraction(1, 3), "nan", float("inf"), Fraction(1, 10000000)])
def test_timestamp_offset_must_match_finite_microsecond_command_precision(offset):
    with pytest.raises(ValueError, match="microsecond fraction"):
        Audit(timestamp_offset=offset)


def test_trim_offset_cannot_hide_damaged_metadata_or_reset_input_frame_numbers():
    audit = Audit(timestamp_offset=Fraction(15))
    config = PREFIX + "config in time_base: 1/1000, frame_rate: 24000/1001\n"
    audit.feed((config + record(0, pts=15000, duration=41, timebase=Fraction(1, 1000))).encode())
    damaged = record(1, pts=15042, duration=41, timebase=Fraction(1, 1000)).replace(
        "disable_residual_flag=1", "disable_residual_flag=0")
    with pytest.raises(AuditError, match="metadata_invalid"):
        audit.feed(damaged.encode())
    with pytest.raises(AuditError):
        audit.match_samples([(Fraction(0), Fraction(41, 1000))])
    audit = Audit(timestamp_offset=Fraction(15))
    with pytest.raises(AuditError, match="frame_sequence"):
        audit.feed((config + record(1, pts=15000, duration=41, timebase=Fraction(1, 1000))).encode())


def test_trim_normalization_preserves_pts_instead_of_generating_a_cfr_grid():
    audit = Audit(timestamp_offset=Fraction(15))
    config = PREFIX + "config in time_base: 1/1000, frame_rate: 24000/1001\n"
    audit.feed((config + record(0, pts=15000, duration=41, timebase=Fraction(1, 1000))
                + record(1, pts=15042, duration=41, timebase=Fraction(1, 1000))).encode())
    with pytest.raises(AuditError, match="sample_pts"):
        audit.match_samples([(Fraction(0), Fraction(41, 1000)),
                             (Fraction(1001, 24000), Fraction(41, 1000))])


def test_sample_matching_is_atomic_and_detects_dropped_frame():
    audit = Audit()
    audit.feed((CONFIG + record(0) + record(1) + record(2)).encode())
    with pytest.raises(AuditError, match="sample_pts"):
        audit.match_samples([(Fraction(0), Fraction(1, 24)), (Fraction(2, 24), Fraction(1, 24))])
    assert audit.pending == 3
    assert audit.matched_frames == 0


def test_tolerance_must_be_smaller_than_a_frame_and_duration_cannot_expand():
    for duration, tolerance in ((Fraction(1, 24), Fraction(1, 24)),
                                (Fraction(3), Fraction(0))):
        audit = Audit()
        audit.feed((CONFIG + record()).encode())
        with pytest.raises(AuditError, match="sample_pts"):
            audit.match_samples([(Fraction(0), duration)], tolerance=tolerance)


def test_unmatched_eof_and_nonzero_exit_are_not_complete():
    audit = Audit()
    audit.feed((CONFIG + record()).encode())
    audit.finish(0)
    with pytest.raises(AuditError, match="unmatched_eof"):
        audit.assert_complete()
    audit = Audit()
    audit.feed((CONFIG + record()).encode())
    with pytest.raises(AuditError, match="process_failed"):
        audit.finish(1)


def test_finish_cannot_fabricate_eof_while_reader_still_running():
    audit = Audit()
    audit.feed((CONFIG + record()).encode())
    assert audit.match_samples([(Fraction(0), Fraction(1, 24))])
    audit._reader = SimpleNamespace(is_alive=lambda: True)
    with pytest.raises(AuditError, match="reader_incomplete"):
        audit.finish(0)
    assert not audit.eof


def test_log_and_pending_limits_are_fail_closed():
    audit = Audit(max_line=40)
    with pytest.raises(AuditError, match="line_overflow"):
        audit.feed(b"x" * 41)
    audit = Audit(max_pending=1)
    with pytest.raises(AuditError, match="backlog"):
        audit.feed((CONFIG + record(0) + record(1)).encode())
    assert audit.pending == 1
    audit = Audit(max_record=100)
    with pytest.raises(AuditError, match="record_overflow"):
        audit.feed((CONFIG + record()).encode())


def test_stderr_reader_reports_progress_and_eof_without_storing_whole_log():
    audit = Audit()
    observations, errors = [], []
    stderr = io.BytesIO((CONFIG + record() + "frame=1\nout_time_us=41666\nspeed=1.5x\n"
                        "dup_frames=0\ndrop_frames=0\nprogress=end\n").encode())
    reader = audit.start_reader(stderr, on_error=errors.append, on_progress=observations.append)
    reader.join(timeout=2)
    assert not reader.is_alive()
    assert audit.eof and not errors
    assert observations[-1]["speed"] == "1.5x"
    assert audit.match_samples([(Fraction(0), Fraction(1, 24))])
    audit.finish(0)
    audit.assert_complete()


def test_stderr_reader_failure_notifies_once_and_keeps_draining():
    audit = Audit(max_line=60)
    errors = []
    stream = io.BytesIO(b"x" * 70 + b"\n" + b"ordinary log\n" * 20000)
    reader = audit.start_reader(stream, on_error=errors.append)
    reader.join(timeout=2)
    assert not reader.is_alive()
    assert len(errors) == 1 and audit.error and audit.eof
    assert stream.tell() == len(stream.getvalue())
    assert len(audit.tail.encode()) <= 64 * 1024


def progress_record(*, state="end"):
    return ("frame=2\nfps=24.00\nstream_0_0_q=-1.0\nbitrate=0.1kbits/s\ntotal_size=N/A\n"
            "out_time_us=83333\nout_time_ms=83333\nout_time=00:00:00.083333\n"
            f"dup_frames=0\ndrop_frames=0\nspeed=1.5x\nprogress={state}\n").encode()


def test_independent_progress_pipe_finishes_without_touching_metadata_stream():
    audit = Audit()
    stderr_data = (CONFIG + record(0) + record(1)).encode()
    stdout_data = progress_record(state="continue") + progress_record()
    observations, errors = [], []
    reader = audit.start_reader(io.BytesIO(stderr_data), progress_stream=io.BytesIO(stdout_data),
                                on_progress=observations.append, on_error=errors.append)
    assert reader is audit._reader
    audit.join_readers(timeout=2)
    assert not audit.readers_alive() and audit.eof and audit.progress_eof
    assert not errors
    assert [snapshot["progress"] for snapshot in observations] == ["continue", "end"]
    assert audit.stderr_bytes == len(stderr_data)
    assert audit.progress_bytes == len(stdout_data)
    assert "speed=" not in audit.tail
    assert audit.match_samples([(Fraction(0), Fraction(1, 24)),
                                (Fraction(1, 24), Fraction(1, 24))])
    audit.finish(0)
    audit.assert_complete()


def test_progress_cannot_complete_a_partial_rpu_record():
    entered, release = threading.Event(), threading.Event()
    lines = record().splitlines(keepends=True)
    prefix = (CONFIG + "".join(lines[:-1])).encode()
    ending = lines[-1].encode()

    class MetadataStream:
        def __init__(self):
            self.calls = 0

        def read(self, _size):
            self.calls += 1
            if self.calls == 1:
                return prefix
            if self.calls == 2:
                entered.set()
                assert release.wait(timeout=2)
                return ending
            return b""

    audit = Audit()
    audit.start_reader(MetadataStream(), progress_stream=io.BytesIO(progress_record()))
    try:
        assert entered.wait(timeout=1)
        audit._progress_reader.join(timeout=1)
        assert audit.progress_eof and audit.progress["progress"] == "end"
        assert audit.pending == 0
        assert audit.match_samples([(Fraction(0), Fraction(1, 24))]) is False
    finally:
        release.set()
        audit.join_readers(timeout=2)
    assert not audit.readers_alive()
    assert audit.match_samples([(Fraction(0), Fraction(1, 24))])
    audit.finish(0)
    audit.assert_complete()


@pytest.mark.parametrize(("damage", "reason"), [
    (b"unexpected=text\n", "progress_format"),
    (b"[hls @ 0xabc] Opening output\n", "progress_format"),
    (b"frame=two\n", "progress_format"),
    (b"speed=nanx\n", "progress_format"),
    (b"speed=\xffx\n", "progress_format"),
    (b"frame=1\nframe=2\n", "progress_duplicate_key"),
    (b"dup_frames=1\n", "frame_count"),
    (b"drop_frames=1\n", "frame_count"),
    (b"progress=end\n", "progress_incomplete"),
    (progress_record(state="continue"), "progress_incomplete"),
    (progress_record()[:-1], "progress_incomplete"),
    (progress_record() + b"frame=2\n", "progress_after_end"),
    (b"speed=" + b"x" * 600, "progress_overflow"),
    (b"", "progress_incomplete"),
])
def test_progress_format_or_incomplete_eof_is_fail_closed(damage, reason):
    audit = Audit()
    errors = []
    stdout = io.BytesIO(damage + (b"ordinary extra output\n" * 20000 if len(damage) > 512 else b""))
    audit.start_reader(io.BytesIO((CONFIG + record()).encode()), progress_stream=stdout,
                       on_error=errors.append)
    audit.join_readers(timeout=2)
    assert not audit.readers_alive() and audit.progress_eof
    assert audit.error and reason in audit.error
    assert len(errors) == 1
    assert stdout.tell() == len(stdout.getvalue())
    with pytest.raises(AuditError, match=reason):
        audit.finish(0)
    with pytest.raises(AuditError):
        audit.match_samples([(Fraction(0), Fraction(1, 24))])


def test_progress_in_stderr_is_rejected_when_a_dedicated_pipe_is_configured():
    audit = Audit()
    stderr = io.BytesIO((CONFIG + record() + "frame=1\n").encode())
    audit.start_reader(stderr, progress_stream=io.BytesIO(progress_record()))
    audit.join_readers(timeout=2)
    assert audit.error == "dovi_audit_progress_wrong_stream"
    with pytest.raises(AuditError, match="wrong_stream"):
        audit.finish(0)


FINAL_REPORT = ("frame=    2 fps=0.0 q=-0.0 Lsize=N/A time=00:00:00.08 "
                "bitrate=N/A speed= 100x    \n")


@pytest.mark.parametrize("report", [
    FINAL_REPORT,
    FINAL_REPORT.replace("100x    ", "100x elapsed=0:00:00.00    "),
    "frame=    2 fps= 10 q=23.0 Lsize=       1KiB time=00:00:00.08 bitrate= 100.0kbits/s speed=1.01e+03x    \n",
    "frame=    2 fps=0.0 q=-1.0 Lsize=N/A time=N/A bitrate=N/A dup=0 drop=0 speed=N/A    \n",
])
def test_complete_human_final_report_is_separate_from_machine_progress(report):
    audit = Audit()
    errors = []
    stderr = (CONFIG + record(0) + record(1) + report).encode()
    audit.start_reader(io.BytesIO(stderr), progress_stream=io.BytesIO(progress_record()),
                       on_error=errors.append)
    audit.join_readers(timeout=2)
    assert not errors
    assert audit.match_samples([(Fraction(0), Fraction(1, 24)), (Fraction(1, 24), Fraction(1, 24))])
    audit.finish(0)
    audit.assert_complete()
    assert audit.diagnostics()["human_final_frames"] == 2
    assert audit.progress["frame"] == "2"


@pytest.mark.parametrize("report", [
    "frame=2\n",
    FINAL_REPORT.replace("Lsize", "size"),
    FINAL_REPORT.replace("00:00:00.08", "00:00:99.08"),
    FINAL_REPORT.replace("q=-0.0", "q=nan"),
    FINAL_REPORT.replace("100x", "nanx"),
    FINAL_REPORT.replace("100x", "100x unexpected=value"),
    FINAL_REPORT.replace("100x", "100x elapsed=-0:00:00.00"),
    FINAL_REPORT.replace("100x", "100x elapsed=0:00:99.00"),
    FINAL_REPORT.replace("100x", "100x elapsed=0:00:00.0"),
    FINAL_REPORT.replace("100x", "100x elapsed=0:00:00.00 extra=value"),
    FINAL_REPORT[:-2] + "\n",
])
def test_machine_or_malformed_human_report_in_stderr_remains_wrong_stream(report):
    audit = Audit()
    stderr = (CONFIG + record(0) + record(1) + report).encode()
    audit.start_reader(io.BytesIO(stderr), progress_stream=io.BytesIO(progress_record()))
    audit.join_readers(timeout=2)
    assert audit.error == "dovi_audit_progress_wrong_stream"
    with pytest.raises(AuditError, match="wrong_stream"):
        audit.finish(0)


@pytest.mark.parametrize("damage", ["open_record", "duplicate", "later_frame", "wrong_count", "drop"])
def test_human_final_report_cannot_hide_missing_frames_or_record_interleaving(damage):
    audit = Audit()
    value = CONFIG + record(0) + record(1)
    if damage == "open_record":
        value = "\n".join(value.splitlines()[:-1]) + "\n" + FINAL_REPORT
    elif damage == "duplicate":
        value += FINAL_REPORT * 2
    elif damage == "later_frame":
        value += FINAL_REPORT + record(2)
    elif damage == "wrong_count":
        value += FINAL_REPORT.replace("frame=    2", "frame=    3")
    else:
        value += FINAL_REPORT.replace(" speed=", " dup=0 drop=1 speed=")
    with pytest.raises(AuditError):
        audit.feed(value.encode())
    assert audit.error


def test_actual_ffmpeg_nostats_final_report_and_stdout_progress_are_separate():
    """Only exercise stock reporting, with two generated 64x64 SDR frames.

    The invented RPU records are independent of the subprocess. This does not
    test real Dolby metadata, colour conversion or hardware performance.
    """
    from app import media

    ffmpeg = media._static_bins_if_present().get("ffmpeg") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("reporting regression requires an already installed FFmpeg")
    result = subprocess.run([
        ffmpeg, "-y", "-nostdin", "-hide_banner", "-loglevel", "info", "-nostats",
        "-progress", "pipe:1", "-filter_threads", "1", "-filter_complex_threads", "1",
        "-f", "lavfi", "-i", "color=size=64x64:rate=24:duration=0.083334",
        "-frames:v", "2", "-an", "-c:v", "wrapped_avframe", "-threads", "1",
        "-f", "null", os.devnull,
    ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=10,
        env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-2000:]
    audit = Audit()
    errors = []
    audit.start_reader(io.BytesIO((CONFIG + record(0) + record(1)).encode() + result.stderr),
                       progress_stream=io.BytesIO(result.stdout), on_error=errors.append)
    audit.join_readers(timeout=2)
    assert not errors
    assert audit.diagnostics()["human_final_frames"] == 2
    assert audit.progress["progress"] == "end" and audit.progress["frame"] == "2"
    assert audit.match_samples([(Fraction(0), Fraction(1, 24)), (Fraction(1, 24), Fraction(1, 24))])
    audit.finish(0)
    audit.assert_complete()


def test_finish_cannot_ignore_a_still_running_progress_reader():
    audit = Audit()
    audit.feed((CONFIG + record()).encode())
    assert audit.match_samples([(Fraction(0), Fraction(1, 24))])
    audit.close()
    audit._reader = SimpleNamespace(is_alive=lambda: False)
    audit._progress_reader = SimpleNamespace(is_alive=lambda: True)
    audit.progress_eof = False
    with pytest.raises(AuditError, match="reader_incomplete"):
        audit.finish(0)
    assert audit.eof and not audit.progress_eof


def mux_interleave(value, positions, *, messages=None, standalone=None, continuation_prefix=PREFIX):
    """Insert complete mux calls between the callbacks of one RPU line."""
    lines = value.splitlines(keepends=True)
    body = lines[3][len(PREFIX):-1]
    messages = messages or ["Opening 'audio0_seg00000.m4s.tmp' for writing"] * len(positions)
    pieces, previous = [], 0
    for index, position in enumerate(positions):
        prefix = PREFIX if index == 0 else continuation_prefix
        pieces.append(prefix + body[previous:position] + messages[index] + "\n")
        if standalone and index == 0:
            pieces.append(standalone + "\n")
        previous = position
    pieces.append(continuation_prefix + body[previous:] + "\n")
    lines[3] = "".join(pieces)
    return "".join(lines)


@pytest.mark.parametrize("boundary", range(8))
def test_known_mux_call_can_split_a_coefficient_at_each_byte_boundary(boundary):
    value = record().replace("{0, 1, 0}", "{0, 1234567, 0}")
    body = value.splitlines()[3][len(PREFIX):]
    audit = Audit()
    audit.feed((CONFIG + mux_interleave(value, [body.index("1234567") + boundary])).encode())
    assert audit.match_samples([(Fraction(0), Fraction(1, 24))])
    audit.finish(0)
    audit.assert_complete()
    assert audit.diagnostics()["demux_events"] == 1
    assert audit.diagnostics()["demux_recovered_lines"] == 1


def test_multiple_mux_calls_and_a_standalone_opening_preserve_the_complete_rpu_line():
    value = record().replace("{0, 1, 0}", "{0, 1234567, 0}")
    body = value.splitlines()[3][len(PREFIX):]
    point = body.index("1234567")
    mixed = mux_interleave(value, [point + 2, point + 5], messages=[
        "Opening 'audio0_seg00000.m4s.tmp' for writing",
        "Opening 'out_audio0.m3u8.tmp' for writing"],
        standalone="[hls @ 0x123] Opening 'video_seg00000.m4s.tmp' for writing")
    audit = Audit()
    # Byte-sized feeds also cover splits inside a mux filename/prefix.
    for byte in (CONFIG + mixed).encode():
        audit.feed(bytes([byte]))
    assert audit.match_samples([(Fraction(0), Fraction(1, 24))])
    audit.finish(0)
    audit.assert_complete()
    assert audit.diagnostics()["demux_events"] == 3
    assert audit.diagnostics()["demux_recovered_lines"] == 1


@pytest.mark.parametrize("filename", ["../video_seg00000.m4s.tmp", "/tmp/video_seg00000.m4s.tmp",
    "https://host/video_seg00000.m4s.tmp", "audio8_seg00000.m4s.tmp", "evil_seg00000.m4s.tmp",
    "out_video.m3u8", "video_seg00000.m4s.tmp.extra"])
def test_mux_demux_never_accepts_unknown_paths_or_output_names(filename):
    value = record()
    body = value.splitlines()[3][len(PREFIX):]
    mixed = mux_interleave(value, [body.index("poly_coef") + 4],
                           messages=[f"Opening '{filename}' for writing"])
    audit = Audit()
    with pytest.raises(AuditError, match="mux_log_invalid"):
        audit.feed((CONFIG + mixed).encode())
    assert audit.pending == 0


@pytest.mark.parametrize("bad_prefix", ["[showinfo@dovi_input @ 0xdef] ",
                                       "[showinfo@dovi_input  @ 0xabc] "])
def test_mux_continuation_requires_the_exact_same_filter_pointer_and_prefix(bad_prefix):
    value = record()
    body = value.splitlines()[3][len(PREFIX):]
    audit = Audit()
    with pytest.raises(AuditError, match="log_interleaved"):
        audit.feed((CONFIG + mux_interleave(value, [body.index("poly_coef") + 4],
                                           continuation_prefix=bad_prefix)).encode())
    assert audit.pending == 0


def test_mux_interruption_with_physical_newline_but_no_continuation_is_truncated():
    lines = record().splitlines(keepends=True)
    audit = Audit()
    audit.feed((CONFIG + "".join(lines[:3]) + PREFIX
                + "      channel 0: pivots={ 0 Opening 'video_init.mp4' for writing\n").encode())
    assert audit.pending == 0
    with pytest.raises(AuditError, match="truncated_log"):
        audit.finish(0)


@pytest.mark.parametrize("continuation", [
    PREFIX + "n: 1 pts: 1 pts_time:0.04 duration:1 duration_time:0.04 fmt:vaapi\n",
    PREFIX + "color_range:pc color_space:unknown color_primaries:unknown color_trc:unknown\n",
    "[hls @ 0x123] unrelated output\n",
    "[hevc @ 0x123] Error parsing DOVI NAL unit.\n",
])
def test_mux_demux_never_joins_new_frames_unknown_messages_or_errors(continuation):
    lines = record().splitlines(keepends=True)
    value = (CONFIG + "".join(lines[:3]) + PREFIX
             + "      channel 0: pivots={ 0 Opening 'video_init.mp4' for writing\n"
             + continuation)
    audit = Audit()
    with pytest.raises(AuditError):
        audit.feed(value.encode())
    assert audit.pending == 0


def test_mux_reassembly_does_not_fill_missing_metadata_or_accept_unknown_coefficient_text():
    for value in (record().replace("source_diagonal=42;", ""),
                  record().replace("poly_coef={ {0, 1, 0}", "poly_coef={ {0, evil, 0}")):
        body = value.splitlines()[3][len(PREFIX):]
        audit = Audit()
        with pytest.raises(AuditError, match="metadata"):
            audit.feed((CONFIG + mux_interleave(value, [body.index("poly_coef") + 4])).encode())
        assert audit.pending == 0
        with pytest.raises(AuditError):
            audit.match_samples([(Fraction(0), Fraction(1, 24))])


def test_mux_event_count_and_reconstructed_logical_line_are_bounded():
    value = record()
    body = value.splitlines()[3][len(PREFIX):]
    audit = Audit()
    with pytest.raises(AuditError, match="mux_log_overflow"):
        audit.feed((CONFIG + mux_interleave(value, [body.index("poly_coef")] * 33)).encode())
    audit = Audit(max_line=len(body) // 2 + len(PREFIX) + 100)
    with pytest.raises(AuditError, match="line_overflow"):
        audit.feed((CONFIG + mux_interleave(value, [len(body) // 2])).encode())
