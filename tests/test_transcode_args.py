"""转码编码参数回归：High profile + 强制 IDR 关键帧（2026-09 沙丘 720p 卡画面修复）。

- NVENC 不加 `-forced-idr 1` 时 `-force_key_frames` 不生效 → HLS 视频分片按源码
  GOP（10s+），音频 rendition 4s 先行 → 画面卡住声音继续；
- NVENC 默认 Main profile 与 master 声明的 avc1.6400（High）不符。
"""
from app.transcode import Backend


def _has_pair(args, a, b):
    return any(args[i] == a and args[i + 1] == b for i in range(len(args) - 1))


def test_nvenc_forces_high_profile_and_idr_keyframes():
    args = Backend("nvenc").video_args(720)
    assert _has_pair(args, "-profile:v", "high")
    assert _has_pair(args, "-forced-idr", "1")
    assert "h264_nvenc" in args


def test_qsv_forces_high_profile_and_idr_keyframes():
    args = Backend("qsv").video_args(720)
    assert _has_pair(args, "-profile:v", "high")
    assert _has_pair(args, "-forced_idr", "1")


def test_vaapi_high_profile():
    args = Backend("vaapi").video_args(720)
    assert _has_pair(args, "-profile:v", "high")


def test_software_encoders_also_force_idr():
    args = Backend("").video_args(720)
    assert _has_pair(args, "-profile:v", "high")
    assert _has_pair(args, "-forced-idr", "1")
