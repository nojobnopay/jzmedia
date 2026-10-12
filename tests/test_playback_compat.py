"""真实 FFmpeg 验证兼容 TS 的编码、单音轨与分段；仅使用临时合成媒体。"""
import json
import struct
import subprocess

import pytest

from app.playback import cmd as playback_cmd
from app import media


def test_compat_ts_output_is_h264_aac_stereo_and_selected_track(tmp_path, monkeypatch):
    available = media.bin_status()
    if not available["ffmpeg"] or not available["ffprobe"]:
        pytest.skip("installed ffmpeg/ffprobe required for synthetic TS playback test")
    ffmpeg, ffprobe = media.ffmpeg_bin(), media.ffprobe_bin()
    monkeypatch.setattr(playback_cmd, "ffmpeg_bin", lambda: ffmpeg)
    source = tmp_path / "synthetic.mkv"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=24:duration=14", "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=14", "-f", "lavfi", "-i",
                    "sine=frequency=880:duration=14", "-map", "0:v", "-map", "1:a",
                    "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "2400", "-c:a", "ac3", "-ac", "6", str(source)],
                   check=True, capture_output=True, timeout=30)
    out = tmp_path / "session"
    out.mkdir()
    plan = {"seg": "ts", "vcopy": False, "acopy": False, "height": 0,
            "audio_idx": 1, "vcodec": "h264"}
    cmd = playback_cmd.build_cmd(str(source), plan, force_sw=True)
    subprocess.run(cmd, cwd=out, check=True, capture_output=True, timeout=30)
    playlist = (out / "master.m3u8").read_text()
    assert "#EXT-X-ENDLIST" in playlist
    assert "#EXT-X-MEDIA:" not in playlist
    durations = [float(line.split(":")[1].rstrip(","))
                 for line in playlist.splitlines() if line.startswith("#EXTINF:")]
    assert len(durations) >= 3, "TS 重编应按强制关键帧分段，而非照源的超长 GOP"
    assert max(durations) <= 6.1
    segment = next(out.glob("seg*.ts"))
    probe = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_streams",
                                      "-of", "json", str(segment)],
                                     check=True, capture_output=True, timeout=10).stdout)
    video, audio = probe["streams"]
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert audio["codec_name"] == "aac" and audio["channels"] == 2
    pcm = subprocess.run([ffmpeg, "-v", "error", "-i", str(segment), "-t", "1",
                          "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         check=True, capture_output=True, timeout=10).stdout
    samples = [x[0] for x in struct.iter_unpack("<h", pcm)][1000:7000]
    crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
    frequency = crossings / (len(samples) / 8000)
    assert 850 < frequency < 910, "输出必须使用所选的第二音轨（880Hz），不是第一轨（220Hz）"


@pytest.mark.parametrize("copy,start", [(False, 2), (True, 0), (True, 5)])
def test_mp4_clip_is_finite_seekable_and_has_faststart_index(tmp_path, monkeypatch, copy, start):
    available = media.bin_status()
    if not available["ffmpeg"] or not available["ffprobe"]:
        pytest.skip("installed ffmpeg/ffprobe required")
    ff, fp = media.ffmpeg_bin(), media.ffprobe_bin()
    monkeypatch.setattr(playback_cmd, "ffmpeg_bin", lambda: ff)
    source = tmp_path / "source.mkv"
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=24:duration=14", "-f", "lavfi", "-i",
                    "sine=frequency=880:duration=14", "-c:v", "libx264", "-g", "2400",
                    "-c:a", "ac3", "-ac", "6", str(source)],
                   check=True, capture_output=True, timeout=30)
    out = tmp_path / "clip"
    out.mkdir()
    cmd = playback_cmd.build_cmd(str(source), {"seg": "mp4", "clip_seconds": 6,
        "vcopy": copy, "acopy": False, "height": 0, "audio_idx": 0}, start=start, force_sw=True)
    assert "-hls_time" not in cmd
    subprocess.run(cmd, cwd=out, check=True, capture_output=True, timeout=30)
    data = (out / "media.tmp.mp4").read_bytes()
    assert data.find(b"moov") < data.find(b"mdat")
    assert b"moof" not in data
    probe = json.loads(subprocess.run([fp, "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(out / "media.tmp.mp4")], check=True, capture_output=True, timeout=10).stdout)
    duration = float(probe["format"]["duration"])
    assert 5.5 < duration < 12
    assert probe["streams"][0]["codec_name"] == "h264"
    assert probe["streams"][1]["codec_name"] == "aac" and probe["streams"][1]["channels"] == 2
    if copy:
        def frame(path, at):
            return subprocess.run([ff, "-v", "error", "-i", str(path), "-ss", str(at),
                "-an", "-frames:v", "1", "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"],
                check=True, capture_output=True, timeout=10).stdout
        origin = playback_cmd.actual_media_start(str(source), start, True)
        expected = frame(source, start + 0.5)
        actual = frame(out / "media.tmp.mp4", start - origin + 0.5)
        assert len(actual) == len(expected) > 0
        assert sum(abs(a - b) for a, b in zip(actual, expected)) / len(actual) < 8
