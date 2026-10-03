"""ffprobe subtitle indices are absolute; exercise actual PGS overlay as well as argv."""
import shutil
import struct
import subprocess

import pytest

from app import media
from app.playback import cmd as playback_cmd


@pytest.mark.parametrize("segment_type", ["fmp4", "ts"])
def test_embedded_burn_uses_absolute_stream_index(monkeypatch, segment_type):
    monkeypatch.setattr(playback_cmd, "ffmpeg_bin", lambda: "ffmpeg")
    plan = {"seg": segment_type, "sub": "burn", "sub_ff_index": 5,
            "vcopy": False, "acopy": True, "audios": []}
    command = playback_cmd.build_cmd("/synthetic.mkv", plan, force_sw=True)
    overlay = command[command.index("-filter_complex") + 1]
    assert "[0:v:0][0:5]overlay=" in overlay
    assert "[0:s:5]" not in overlay


def _picture_subtitle():
    """Original PGS: a white 32x16 rectangle on a 160x90 canvas, from 1s to 3s.

    Segments follow FFmpeg's pgssubdec PCS/WDS/PDS/ODS/display parser. No media
    download or third-party fixture is needed to validate an actual bitmap stream.
    """
    def segment(kind, payload, pts=90000):
        return b"PG" + struct.pack(">IIBH", pts, pts, kind, len(payload)) + payload

    presentation = (struct.pack(">HHBHBBBB", 160, 90, 0x10, 0, 0x80, 0, 0, 1)
                    + struct.pack(">HBBHH", 0, 0, 0, 32, 40))
    window = struct.pack(">BBHHHH", 1, 0, 32, 40, 32, 16)
    palette = bytes([0, 0, 0, 16, 128, 128, 0, 1, 235, 128, 128, 255])
    pixels = (bytes([1]) * 32 + b"\x00\x00") * 16
    picture = (struct.pack(">HBB", 0, 0, 0xC0) + (len(pixels) + 4).to_bytes(3, "big")
               + struct.pack(">HH", 32, 16) + pixels)
    clear = struct.pack(">HHBHBBBB", 160, 90, 0x10, 1, 0, 0, 0, 0)
    return (b"".join(segment(kind, payload) for kind, payload in [
        (0x16, presentation), (0x17, window), (0x14, palette),
        (0x15, picture), (0x80, b""),
    ]) + segment(0x16, clear, 270000) + segment(0x80, b"", 270000))


@pytest.mark.parametrize("segment_type", ["fmp4", "ts"])
def test_real_pgs_after_video_and_audio_burns_into_hls(monkeypatch, tmp_path, segment_type):
    ffmpeg = shutil.which("ffmpeg") or media._static_bins_if_present().get("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable; do not download binaries")
    monkeypatch.setattr(playback_cmd, "ffmpeg_bin", lambda: ffmpeg)
    subtitle = tmp_path / "box.sup"
    subtitle.write_bytes(_picture_subtitle())
    source = tmp_path / "source.mkv"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=black:size=160x90:rate=12", "-f", "lavfi", "-i",
        "sine=sample_rate=48000", "-i", str(subtitle), "-t", "4",
        "-map", "0:v", "-map", "1:a", "-map", "2:s", "-c:v", "libx264",
        "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "copy",
        str(source),
    ], check=True, capture_output=True, timeout=30)
    # The only subtitle is s:0, but ffprobe's absolute stream index is 2.
    plan = {"seg": segment_type, "sub": "burn", "sub_ff_index": 2,
            "vcopy": False, "acopy": True, "audio_idx": 0,
            "audios": [{"i": 0, "copy": True, "channels": 1, "default": 1}]}
    subprocess.run(playback_cmd.build_cmd(str(source), plan, force_sw=True),
                   cwd=tmp_path, check=True, capture_output=True, timeout=30)
    playlist = tmp_path / ("out_video.m3u8" if segment_type == "fmp4" else "master.m3u8")
    assert "#EXT-X-ENDLIST" in playlist.read_text()
    frame = subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-allowed_extensions", "ALL",
        "-i", str(playlist), "-ss", "1.5", "-frames:v", "1", "-f", "rawvideo",
        "-pix_fmt", "gray", "-",
    ], check=True, capture_output=True, timeout=30).stdout
    assert len(frame) == 160 * 90
    assert frame[45 * 160 + 40] > 200, "Selected PGS pixels must be burned into video"
    assert frame[5 * 160 + 5] < 30, "Synthetic background remains black"
