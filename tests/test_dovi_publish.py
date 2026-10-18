"""Tiny invented MP4 boxes exercise the gate without invoking a transcoder."""
from fractions import Fraction
import json
import os
import shutil
import struct
import subprocess

import pytest

from app.playback.dovi_audit import Audit
from app.routers.stream.dovi_publish import (
    Publisher, PublishError, Track, parse_fragment, parse_init, publication_complete,
)
from test_dovi_audit import CONFIG, record


def box(kind, payload):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def init_bytes(*, timescale=24, duration=1, size=1, handler=b"vide", version=0, edit=None):
    fields = struct.pack(">IQQI", 0x01000000, 0, 0, 1) if version else struct.pack(">IIII", 0, 0, 0, 1)
    tkhd = box(b"tkhd", fields)
    mdhd_fields = (struct.pack(">IQQI", 0x01000000, 0, 0, timescale) if version else
                   struct.pack(">IIII", 0, 0, 0, timescale))
    mdia = box(b"mdia", box(b"mdhd", mdhd_fields)
               + box(b"hdlr", struct.pack(">II4s", 0, 0, handler)))
    edts = b""
    if edit is not None:
        elst = struct.pack(">IIIiI", 0, 1, 0, edit, 0x00010000)
        edts = box(b"edts", box(b"elst", elst))
    mvhd = box(b"mvhd", struct.pack(">IIII", 0, 0, 0, 1000))
    trex = box(b"trex", struct.pack(">IIIIII", 0, 1, 1, duration, size, 0))
    return box(b"ftyp", b"iso6\0\0\0\0iso6") + box(
        b"moov", mvhd + box(b"trak", tkhd + edts + mdia) + box(b"mvex", trex))


def fragment_bytes(*, start=0, count=1, duration=1, size=1, defaults=True,
                   ctos=None, version=1, tfdt_version=1, use_trex=False, sequence=1):
    flags = 0x020018 if defaults and not use_trex else 0x020000
    tfhd = box(b"tfhd", struct.pack(">II", flags, 1)
               + (struct.pack(">II", duration, size) if defaults and not use_trex else b""))
    tfdt = box(b"tfdt", struct.pack(">IQ", 0x01000000, start) if tfdt_version else
               struct.pack(">II", 0, start))
    run_flags = 1 | (0 if defaults else 0x300) | (0x800 if ctos is not None else 0)
    entries = b"".join((b"" if defaults else struct.pack(">II", duration, size))
                        + (struct.pack(">i" if version else ">I", ctos[index])
                           if ctos is not None else b"") for index in range(count))

    def make_moof(offset):
        trun = box(b"trun", struct.pack(">IIi", (version << 24) | run_flags, count, offset) + entries)
        return box(b"moof", box(b"mfhd", struct.pack(">II", 0, sequence))
                   + box(b"traf", tfhd + tfdt + trun))

    moof = make_moof(0)
    return make_moof(len(moof) + 8) + box(b"mdat", bytes(range(count)) * size)


def playlist(directory, rendition, count, *, final=False):
    value = ("#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n"
             f'#EXT-X-MAP:URI="{rendition}_init.mp4"\n')
    value += "".join(f"#EXTINF:1,\n{rendition}_seg{index:05d}.m4s\n" for index in range(count))
    if final:
        value += "#EXT-X-ENDLIST\n"
    (directory / f"out_{rendition}.m3u8").write_text(value)


def producer(tmp_path, frames=2):
    private, public = tmp_path / "private", tmp_path / "public"
    private.mkdir()
    public.mkdir()
    audit = Audit()
    audit.feed((CONFIG + "".join(record(index) for index in range(frames))).encode())
    (private / "video_init.mp4").write_bytes(init_bytes())
    for index in range(frames):
        (private / f"video_seg{index:05d}.m4s").write_bytes(fragment_bytes(start=index))
    playlist(private, "video", frames)
    return private, public, audit, Publisher(private, public, audit)


@pytest.mark.parametrize("version", [0, 1])
def test_init_parses_track_timescales_defaults_and_64_bit_versions(tmp_path, version):
    path = tmp_path / "init.mp4"
    path.write_bytes(init_bytes(timescale=48000, duration=1024, size=7,
                                handler=b"soun", version=version))
    assert parse_init(path) == {1: Track(1, 48000, b"soun", 1024, 7, 0)}


def test_edit_list_preserves_presentation_origin_and_audio_priming(tmp_path):
    path = tmp_path / "init.mp4"
    path.write_bytes(init_bytes(timescale=48000, handler=b"soun", edit=1024))
    tracks = parse_init(path)
    assert tracks[1].time_offset == -Fraction(1024, 48000)
    fragment = tmp_path / "audio.m4s"
    fragment.write_bytes(fragment_bytes(duration=1024))
    assert parse_fragment(fragment, tracks)[1][0].pts == -Fraction(1024, 48000)


@pytest.mark.parametrize("defaults", [True, False])
@pytest.mark.parametrize("tfdt_version", [0, 1])
def test_fragment_samples_use_integer_timebase_and_defaults(tmp_path, defaults, tfdt_version):
    path = tmp_path / "segment.m4s"
    path.write_bytes(fragment_bytes(start=24000, count=2, duration=1001, size=4,
                                    defaults=defaults, tfdt_version=tfdt_version))
    samples = parse_fragment(path, {1: Track(1, 24000, b"vide")})[1]
    assert [sample.pts for sample in samples] == [Fraction(1), Fraction(25001, 24000)]
    assert [sample.duration for sample in samples] == [Fraction(1001, 24000)] * 2
    assert [sample.size for sample in samples] == [4, 4]


@pytest.mark.parametrize(("version", "ctos"), [(1, [-1, -1]), (0, [1, 1])])
def test_composition_offsets_include_signed_version_one(tmp_path, version, ctos):
    path = tmp_path / "segment.m4s"
    path.write_bytes(fragment_bytes(start=10, count=2, ctos=ctos, version=version))
    samples = parse_fragment(path, {1: Track(1, 24, b"vide")})[1]
    assert samples[0].pts == Fraction(10 + ctos[0], 24)
    assert samples[1].pts == Fraction(11 + ctos[1], 24)


def test_fragment_can_inherit_all_sample_defaults_from_trex(tmp_path):
    path = tmp_path / "segment.m4s"
    path.write_bytes(fragment_bytes(count=2, duration=1024, size=2, use_trex=True))
    samples = parse_fragment(path, {1: Track(1, 48000, b"soun", 1024, 2, 0)})[1]
    assert len(samples) == 2
    assert samples[1].pts == Fraction(1024, 48000)


@pytest.mark.parametrize("damage", ["truncated", "open_mdat", "bad_offset", "extra_payload", "unknown_track"])
def test_incomplete_or_ambiguous_fragment_is_rejected(tmp_path, damage):
    path = tmp_path / "segment.m4s"
    data = fragment_bytes()
    if damage == "truncated":
        data = data[:-1]
    elif damage == "open_mdat":
        offset = data.index(b"mdat") - 4
        data = data[:offset] + b"\0\0\0\0" + data[offset + 4:]
    elif damage == "bad_offset":
        offset = data.index(b"trun") + 12
        data = data[:offset] + struct.pack(">i", -100000) + data[offset + 4:]
    elif damage == "extra_payload":
        data += box(b"mdat", b"unreferenced")
    else:
        offset = data.index(b"tfhd") + 8
        data = data[:offset] + struct.pack(">I", 2) + data[offset + 4:]
    path.write_bytes(data)
    with pytest.raises(PublishError):
        parse_fragment(path, {1: Track(1, 24, b"vide")})


def test_publisher_exposes_only_verified_files_and_finishes_at_audited_eof(tmp_path):
    private, public, audit, gate = producer(tmp_path)
    assert not (public / "video_seg00000.m4s").exists()
    assert gate.poll()
    assert (private / "video_init.mp4").exists()
    assert not list(private.glob("video_seg*.m4s"))
    assert gate.video_end == Fraction(2, 24)
    value = (public / "out_video.m3u8").read_text()
    assert "#EXT-X-ENDLIST" not in value
    assert "private" not in value
    assert not publication_complete(public)
    playlist(private, "video", 2, final=True)
    audit.finish(0)
    assert gate.poll(final=True)
    assert gate.poll(final=True)
    assert "#EXT-X-ENDLIST" in (public / "out_video.m3u8").read_text()
    assert publication_complete(public)
    assert not list(public.glob("*.tmp"))


def test_nonzero_first_frame_preserves_exact_common_audio_video_origin(tmp_path):
    private, public, _audit, _gate = producer(tmp_path, frames=1)
    audit = Audit()
    audit.feed((CONFIG + record(0, pts=1)).encode())
    gate = Publisher(private, public, audit)
    (private / "video_seg00000.m4s").write_bytes(fragment_bytes(start=1))
    playlist(private, "video", 1, final=True)
    audit.finish(0)
    assert gate.poll(final=True)
    samples = parse_fragment(public / "video_seg00000.m4s", parse_init(public / "video_init.mp4"))[1]
    assert samples[0].pts == Fraction(1, 24)
    assert gate.video_end == Fraction(2, 24)
    assert publication_complete(public)
    assert not (private / "video_seg00000.m4s").exists()


def test_waits_for_complete_audit_record_before_copying_even_init(tmp_path):
    private, public, _audit, _gate = producer(tmp_path, frames=1)
    audit = Audit()
    lines = record().splitlines(keepends=True)
    audit.feed((CONFIG + "".join(lines[:-1])).encode())
    gate = Publisher(private, public, audit)
    assert not gate.poll()
    assert not list(public.glob("*.m4s")) and not list(public.glob("*_init.mp4"))
    audit.feed(lines[-1].encode())
    assert gate.poll()


def test_bad_sample_pts_never_publishes_segment_or_playlist(tmp_path):
    private, public, _audit, gate = producer(tmp_path, frames=1)
    (private / "video_seg00000.m4s").write_bytes(fragment_bytes(start=1))
    with pytest.raises(PublishError, match="sample_pts"):
        gate.poll()
    assert not list(public.glob("*.m4s")) and not list(public.glob("*.m3u8"))


def test_missing_input_frame_or_unmatched_output_prevents_endlist(tmp_path):
    private, public, audit, gate = producer(tmp_path, frames=2)
    playlist(private, "video", 1, final=True)
    audit.finish(0)
    with pytest.raises(PublishError, match="unmatched_eof"):
        gate.poll(final=True)
    assert not gate.complete
    assert not (public / "dovi-publication.json").exists()
    assert not (public / "out_video.m3u8").exists()


def test_complete_marker_cannot_precede_reader_eof_or_ffmpeg_exit(tmp_path):
    private, public, _audit, gate = producer(tmp_path, frames=1)
    playlist(private, "video", 1, final=True)
    with pytest.raises(PublishError, match="unmatched_eof"):
        gate.poll(final=True)
    assert not publication_complete(public)


def test_audio_waits_for_video_boundary_and_final_packet_is_trimmed(tmp_path):
    private, public, audit, gate = producer(tmp_path, frames=2)
    (private / "audio0_init.mp4").write_bytes(init_bytes(timescale=1000, duration=30, handler=b"soun"))
    (private / "audio0_seg00000.m4s").write_bytes(fragment_bytes(count=3, duration=30))
    playlist(private, "audio0", 1)
    assert not gate.poll()
    assert gate.video_end == Fraction(1, 12)
    assert not list(private.glob("video_seg*.m4s"))
    assert not (public / "audio0_seg00000.m4s").exists()
    assert (private / "audio0_seg00000.m4s").exists()
    playlist(private, "video", 2, final=True)
    playlist(private, "audio0", 1, final=True)
    audit.finish(0)
    assert gate.poll(final=True)
    tracks = parse_init(public / "audio0_init.mp4")
    audio = parse_fragment(public / "audio0_seg00000.m4s", tracks)[1]
    assert len(audio) == 2
    assert not (private / "audio0_seg00000.m4s").exists()
    assert (private / "audio0_init.mp4").exists()
    assert audio[-1].pts + audio[-1].duration <= gate.video_end
    assert "#EXTINF:0.060000000," in (public / "out_audio0.m3u8").read_text()
    assert publication_complete(public)


def test_trimmed_final_audio_keeps_original_non_one_fragment_sequence(tmp_path):
    private, public, audit, gate = producer(tmp_path, frames=2)
    (private / "audio0_init.mp4").write_bytes(init_bytes(timescale=1000, handler=b"soun"))
    (private / "audio0_seg00000.m4s").write_bytes(fragment_bytes(count=3, duration=30, sequence=2371))
    playlist(private, "audio0", 1, final=True)
    playlist(private, "video", 2, final=True)
    audit.finish(0)
    assert gate.poll(final=True)
    value = (public / "audio0_seg00000.m4s").read_bytes()
    assert struct.unpack_from(">I", value, value.index(b"mfhd") + 8)[0] == 2371
    samples = parse_fragment(public / "audio0_seg00000.m4s", parse_init(public / "audio0_init.mp4"))[1]
    assert len(samples) == 2 and samples[-1].pts + samples[-1].duration <= gate.video_end


@pytest.mark.parametrize("damage", ["two_extra_packets", "seconds_long_tail", "extra_segment"])
def test_multi_packet_audio_tail_cannot_hide_truncated_video(tmp_path, damage):
    private, public, audit, gate = producer(tmp_path, frames=2)
    timescale = 48000 if damage == "two_extra_packets" else 1000
    (private / "audio0_init.mp4").write_bytes(init_bytes(timescale=timescale, handler=b"soun"))
    count, duration = (5, 1024) if damage == "two_extra_packets" else (3, 1000)
    if damage == "extra_segment":
        count, duration = 3, 30
        (private / "audio0_seg00001.m4s").write_bytes(fragment_bytes(start=90, count=3, duration=30))
    (private / "audio0_seg00000.m4s").write_bytes(fragment_bytes(count=count, duration=duration))
    playlist(private, "audio0", 2 if damage == "extra_segment" else 1, final=True)
    playlist(private, "video", 2, final=True)
    audit.finish(0)
    with pytest.raises(PublishError, match="audio_tail"):
        gate.poll(final=True)
    assert not gate.complete and not publication_complete(public)
    assert not (public / "out_video.m3u8").exists()


def test_fractional_video_endpoint_can_trim_two_aac_packets_across_two_files(tmp_path):
    private, public, audit, gate = producer(tmp_path, frames=2)
    # Verified video ends at tick 4000/48000. The first crossing AAC packet
    # ends at 4096; a 144-tick final packet in another HLS file leaves +5ms.
    (private / "audio0_init.mp4").write_bytes(init_bytes(timescale=48000, handler=b"soun"))
    (private / "audio0_seg00000.m4s").write_bytes(fragment_bytes(count=4, duration=1024, sequence=41))
    (private / "audio0_seg00001.m4s").write_bytes(fragment_bytes(start=4096, duration=144, sequence=42))
    playlist(private, "audio0", 2, final=True)
    playlist(private, "video", 2, final=True)
    audit.finish(0)
    assert gate.poll(final=True)
    samples = parse_fragment(public / "audio0_seg00000.m4s", parse_init(public / "audio0_init.mp4"))[1]
    assert len(samples) == 3
    assert samples[-1].pts + samples[-1].duration == Fraction(3072, 48000)
    assert gate.video_end == Fraction(4000, 48000)
    assert not (public / "audio0_seg00001.m4s").exists()
    assert "audio0_seg00001" not in (public / "out_audio0.m3u8").read_text()
    assert publication_complete(public)


@pytest.mark.parametrize("damage", ["two_full_packets", "three_short_packets", "gap", "truncated", "missing"])
def test_bounded_aac_suffix_rejects_long_incomplete_or_noncontiguous_tail(tmp_path, damage):
    private, public, audit, gate = producer(tmp_path, frames=2)
    (private / "audio0_init.mp4").write_bytes(init_bytes(timescale=48000, handler=b"soun"))
    if damage == "three_short_packets":
        # Three tiny packets total less than one AAC frame at the endpoint:
        # the count bound is independent of duration and must still reject.
        first = fragment_bytes(count=41, duration=100)
        last = fragment_bytes(start=4100, count=2, duration=80)
    else:
        first = fragment_bytes(count=4, duration=1024)
        last = fragment_bytes(start=4097 if damage == "gap" else 4096,
                              duration=1024 if damage == "two_full_packets" else 144)
    (private / "audio0_seg00000.m4s").write_bytes(first)
    if damage != "missing":
        (private / "audio0_seg00001.m4s").write_bytes(last[:-1] if damage == "truncated" else last)
    playlist(private, "audio0", 2, final=True)
    playlist(private, "video", 2, final=True)
    audit.finish(0)
    with pytest.raises(PublishError):
        gate.poll(final=True)
    assert not gate.complete and not publication_complete(public)
    assert not (public / "out_video.m3u8").exists()
    assert not list(public.glob("audio0_seg*.m4s"))


def test_single_last_audio_packet_can_be_omitted_after_published_prefix(tmp_path):
    private, public, audit, gate = producer(tmp_path, frames=2)
    (private / "audio0_init.mp4").write_bytes(init_bytes(timescale=1000, handler=b"soun"))
    (private / "audio0_seg00000.m4s").write_bytes(fragment_bytes(count=4, duration=20))
    (private / "audio0_seg00001.m4s").write_bytes(fragment_bytes(start=80, duration=20))
    playlist(private, "audio0", 2, final=True)
    playlist(private, "video", 2, final=True)
    audit.finish(0)
    assert gate.poll(final=True)
    assert (public / "audio0_seg00000.m4s").exists()
    assert not (public / "audio0_seg00001.m4s").exists()
    assert "audio0_seg00001" not in (public / "out_audio0.m3u8").read_text()
    assert publication_complete(public)


def test_expected_audio_rendition_is_required_at_eof(tmp_path):
    private, public, audit, _gate = producer(tmp_path, frames=1)
    gate = Publisher(private, public, audit, expected_renditions=["video", "audio0"])
    playlist(private, "video", 1, final=True)
    audit.finish(0)
    with pytest.raises(PublishError, match="missing_rendition"):
        gate.poll(final=True)
    assert not gate.complete and not publication_complete(public)


@pytest.mark.parametrize("uri", ["../private/video_seg00000.m4s", "/tmp/video_seg00000.m4s", "https://host/secret"])
def test_playlist_cannot_escape_private_directory(tmp_path, uri):
    private, public, _audit, gate = producer(tmp_path, frames=1)
    path = private / "out_video.m3u8"
    path.write_text(path.read_text().replace("video_seg00000.m4s", uri))
    with pytest.raises(PublishError, match="unsafe_uri"):
        gate.poll()
    assert not list(public.glob("*.m4s"))


def test_missing_rpu_error_blocks_any_new_publication(tmp_path):
    _private, public, audit, gate = producer(tmp_path, frames=1)
    with pytest.raises(Exception):
        audit.feed_line("[hevc @ 0xabc] Error parsing DOVI NAL unit.")
    with pytest.raises(PublishError, match="ffmpeg_error"):
        gate.poll()
    assert not list(public.glob("*.m4s"))


def test_cached_completion_rejects_file_or_manifest_tampering(tmp_path):
    private, public, audit, gate = producer(tmp_path, frames=1)
    playlist(private, "video", 1, final=True)
    audit.finish(0)
    gate.poll(final=True)
    assert publication_complete(public)
    segment = public / "video_seg00000.m4s"
    original = segment.read_bytes()
    segment.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    assert not publication_complete(public)
    segment.write_bytes(original)
    assert publication_complete(public)
    manifest = public / "dovi-publication.json"
    state = json.loads(manifest.read_text())
    state["audit"]["matched_frames"] = 0
    manifest.write_text(json.dumps(state))
    assert not publication_complete(public)


def test_completion_memo_skips_rehash_but_invalidates_on_ctime_or_playlist_change(tmp_path, monkeypatch):
    from app.routers.stream import dovi_publish as module

    private, public, audit, gate = producer(tmp_path, frames=1)
    playlist(private, "video", 1, final=True)
    audit.finish(0)
    gate.poll(final=True)
    calls = []
    real_digest = module._digest

    def digest(path):
        calls.append(path)
        return real_digest(path)

    monkeypatch.setattr(module, "_digest", digest)
    assert publication_complete(public)
    first = len(calls)
    assert first > 0
    assert publication_complete(public)
    assert len(calls) == first
    path = public / "video_seg00000.m4s"
    info = path.stat()
    value = path.read_bytes()
    path.write_bytes(value[:-1] + bytes([value[-1] ^ 1]))
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert not publication_complete(public)
    assert len(calls) > first
    path.write_bytes(value)
    assert publication_complete(public)
    manifest_playlist = public / "out_video.m3u8"
    manifest_playlist.write_text(manifest_playlist.read_text().replace("0.041666667", "4.000000000"))
    assert not publication_complete(public)


def test_symlink_cannot_become_public_and_playlist_history_cannot_change(tmp_path):
    private, public, _audit, gate = producer(tmp_path, frames=1)
    segment = private / "video_seg00000.m4s"
    real = private / "real.m4s"
    segment.rename(real)
    segment.symlink_to(real)
    with pytest.raises(PublishError, match="file_missing"):
        gate.poll()
    assert not (public / segment.name).exists()


@pytest.mark.parametrize("fps", [Fraction(24), Fraction(24000, 1001)], ids=["24", "24000_1001"])
def test_actual_ffmpeg_fmp4_hls_timeline_and_aac_tail_publication(tmp_path, fps):
    """Real muxing only: tiny SDR, no GPU, source media or Dolby/RPU claim.

    The independent audit double knows the 48 input frame timestamps before
    parsing any output. It cannot learn or repair expectations from MP4 boxes.
    AAC is intentionally five milliseconds longer, exercising the real final
    packet prefix rewrite as well as the muxer's encoder-delay edit list.
    """
    from app import media

    ffmpeg = media._static_bins_if_present().get("ffmpeg") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("packaging regression requires an already installed FFmpeg")
    private, public = tmp_path / "private", tmp_path / "public"
    private.mkdir()
    frame_count = 48
    video_duration = Fraction(frame_count) / fps
    audio_duration = video_duration + Fraction(5, 1000)

    class PackagingAudit:
        error = None
        eof = True
        returncode = 0

        def __init__(self):
            self.expected = [(Fraction(index) / fps, Fraction(1) / fps)
                             for index in range(frame_count)]
            self.matched = 0

        def check(self):
            assert self.error is None

        def match_samples(self, samples, *, tolerance):
            assert samples
            # These exact fractions come only from the known lavfi input,
            # independent of trun/tfdt, encoder progress or ffprobe output.
            assert samples == self.expected[self.matched:self.matched + len(samples)]
            self.matched += len(samples)
            return True

        def assert_complete(self):
            assert self.matched == frame_count

        def diagnostics(self):
            return {"error": None, "eof": True, "returncode": 0,
                    "audited_frames": frame_count, "matched_frames": self.matched,
                    "pending_frames": frame_count - self.matched}

    command = [ffmpeg, "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-nostats",
        "-filter_threads", "1", "-filter_complex_threads", "1",
        "-f", "lavfi", "-i", f"testsrc2=size=64x64:rate={fps.numerator}/{fps.denominator}:"
                              f"duration={float(video_duration):.6f}",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={float(audio_duration):.6f}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", "trim=start=0,setpts=PTS-0/TB,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-threads:v", "1",
        "-bf", "0", "-fps_mode", "passthrough", "-enc_time_base:v", "filter",
        "-force_key_frames", "expr:gte(t,n_forced*1)",
        "-filter:a:0", "atrim=start=0,asetpts=PTS-0/TB",
        "-c:a:0", "aac", "-threads:a:0", "1", "-b:a:0", "64k", "-ac:a:0", "2",
        "-var_stream_map", "v:0,agroup:aud,name:video a:0,agroup:aud,name:audio0",
        "-f", "hls", "-hls_time", "1", "-hls_list_size", "0",
        "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "%v_init.mp4",
        "-hls_segment_filename", "%v_seg%05d.m4s",
        "-hls_flags", "temp_file+independent_segments",
        "-avoid_negative_ts", "disabled", "out_%v.m3u8"]
    result = subprocess.run(command, cwd=private, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False, timeout=20,
        env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-2000:]

    video_tracks = parse_init(private / "video_init.mp4")
    audio_tracks = parse_init(private / "audio0_init.mp4")
    assert len(video_tracks) == len(audio_tracks) == 1
    video_track, audio_track = next(iter(video_tracks.values())), next(iter(audio_tracks.values()))
    assert video_track.handler == b"vide" and audio_track.handler == b"soun"
    assert audio_track.timescale == 48000
    source_audio = [sample for path in sorted(private.glob("audio0_seg*.m4s"))
                    for sample in parse_fragment(path, audio_tracks)[audio_track.id]]
    assert source_audio
    assert -Fraction(1024, 48000) <= source_audio[0].pts <= 0
    source_audio_end = source_audio[-1].pts + source_audio[-1].duration
    assert video_duration < source_audio_end <= video_duration + Fraction(1024, 48000)

    audit = PackagingAudit()
    gate = Publisher(private, public, audit, expected_renditions=["video", "audio0"])
    assert gate.poll(final=True)
    assert audit.matched == frame_count and gate.video_end == video_duration
    public_audio = [sample for path in sorted(public.glob("audio0_seg*.m4s"))
                    for sample in parse_fragment(path, audio_tracks)[audio_track.id]]
    assert len(public_audio) == len(source_audio) - (1 if fps == 24 else 2)
    assert (video_duration - Fraction(1024, 48000)
            <= public_audio[-1].pts + public_audio[-1].duration <= video_duration)
    assert all("#EXT-X-ENDLIST" in (public / name).read_text()
               for name in ("out_video.m3u8", "out_audio0.m3u8"))
    assert not any((private / path.name).exists() for path in public.glob("*.m4s"))
    assert publication_complete(public)
