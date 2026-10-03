"""HLS master describes all encoded audio renditions, including mixed codecs."""
import re

from app.routers.stream.common import _write_master


def test_master_declares_every_audio_codec_once_in_rendition_order(tmp_path):
    tracks = [{"codec": codec} for codec in ("aac", "mp3", "ac3", "eac3", "dts", "aac")]
    plan = {"vcopy": True, "audios": [
        {"i": i, "copy": i != 4, "default": i == 2, "channels": 2}
        for i in range(len(tracks))
    ]}
    _write_master(str(tmp_path), {"vcodec": "h264", "width": 1280,
                                 "height": 720, "audio": tracks}, plan, 4)
    master = (tmp_path / "master.m3u8").read_text()
    codecs = re.search(r'CODECS="([^"]+)"', master).group(1).split(",")
    assert codecs[0].startswith("avc1.")
    assert codecs[1:] == ["mp4a.40.2", "mp3", "ac-3", "ec-3"]
    assert master.count("#EXT-X-MEDIA:") == 6
    assert 'NAME="audio3",DEFAULT=YES' in master
    assert 'AUDIO="aud"' in master


def test_master_without_audio_does_not_advertise_aac(tmp_path):
    _write_master(str(tmp_path), {"vcodec": "h264", "width": 1280, "height": 720},
                  {"vcopy": True, "audios": []}, 4)
    master = (tmp_path / "master.m3u8").read_text()
    assert "mp4a" not in master
    assert "#EXT-X-MEDIA:" not in master
    assert 'AUDIO="aud"' not in master
