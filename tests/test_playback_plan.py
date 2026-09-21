"""playback 决策矩阵 / 会话复用键 回归网（纯函数，宿主无 ffmpeg 也可跑）。

约束：必须 monkeypatch hw_backend/hw_can_tonemap，避免 transcode.detect() 触发
static-ffmpeg 下载（网络）。
"""
import pytest

from app import playback as pb
import app.playback.backend as pb_backend
from app.routers.stream import _plan_marker, _quality_key, _session_key


@pytest.fixture(autouse=True)
def _no_hw(monkeypatch):
    monkeypatch.setattr(pb_backend, "hw_backend", lambda: "")
    monkeypatch.setattr(pb_backend, "hw_can_tonemap", lambda: False)


@pytest.fixture()
def hw_on(monkeypatch):
    monkeypatch.setattr(pb_backend, "hw_backend", lambda: "vaapi")
    monkeypatch.setattr(pb_backend, "hw_can_tonemap", lambda: True)


def _media(**kw):
    base = {
        "playable": True, "container": "mp4", "duration": 7200.0,
        "width": 1920, "height": 1080, "vcodec": "h264", "bit_depth": 8,
        "video_profile": "High", "video_level": 40, "hdr": "",
        "dv_profile": 0, "dv_bl_compat": 0, "vcaps": [],
        "audio": [{"index": 0, "ff_index": 1, "codec": "aac", "channels": 2,
                   "bitrate": 128000, "lang": "eng", "title": "", "default": 1,
                   "forced": 0, "caps": []}],
        "subs": [],
    }
    base.update(kw)
    return base


def _caps(**kw):
    base = {"video": {"h264": True}, "audio": {"aac": True, "mp3": True},
            "hdr": False, "mse": True, "native_hls": False, "probes": {}}
    base.update(kw)
    return base


def test_unplayable_is_blocked():
    d = pb.plan(_media(playable=False), caps=_caps())
    assert d["method"] == "blocked"
    assert "unplayable" in d["reasons"]


def test_direct_play():
    d = pb.plan(_media(), caps=_caps())
    assert d["method"] == "direct"
    assert d["subtitle_mode"] == "none"


def test_kodi_passthrough():
    d = pb.plan(_media(container="matroska", vcodec="hevc"), caps=_caps(), client="kodi")
    assert d["method"] == "direct"
    assert "kodi_passthrough" in d["reasons"]


def test_remux_for_mkv_container():
    d = pb.plan(_media(container="matroska"), caps=_caps())
    assert d["method"] == "remux"
    assert "container_not_supported" in d["reasons"]


def test_audio_transcode_for_dts():
    d = pb.plan(_media(audio=[{"index": 0, "ff_index": 1, "codec": "dts",
                               "channels": 6, "bitrate": 768000, "lang": "eng",
                               "title": "", "default": 1, "forced": 0, "caps": []}]),
                caps=_caps())
    assert d["method"] == "audio_transcode"
    assert d["plan"]["vcopy"] is True and d["plan"]["acopy"] is False


def test_video_transcode_hevc_offline_auto_caps_720p():
    d = pb.plan(_media(vcodec="hevc", height=2160, width=3840), caps=_caps())
    assert d["method"] == "video_transcode"
    assert d["plan"]["height"] == 720
    assert "auto_downscale_720p" in d["reasons"]
    assert "video_codec_not_supported" in d["reasons"]


def test_video_transcode_hevc_with_hw_caps_1080p(hw_on):
    d = pb.plan(_media(vcodec="hevc", height=2160, width=3840), caps=_caps())
    assert d["plan"]["height"] == 1080
    assert "auto_downscale_1080p" in d["reasons"]


def test_probe_override_can_allow_hevc_remux():
    vcap = 'video/mp4; codecs="hvc1.1.6.L120.B0"'
    media = _media(container="matroska", vcodec="hevc", vcaps=[vcap])
    d = pb.plan(media, caps=_caps(probes={vcap: True}))
    assert d["method"] == "remux"


def test_hdr_blocks_direct_and_flags_no_tonemap():
    d = pb.plan(_media(hdr="hdr10"), caps=_caps(hdr=False))
    assert d["method"] == "video_transcode"
    assert "hdr_not_supported" in d["reasons"]
    assert "hdr_no_tonemap" in d["reasons"]
    assert d["plan"]["tonemap"] is False


def test_hdr_tonemap_with_hw(hw_on):
    d = pb.plan(_media(hdr="hdr10"), caps=_caps(hdr=False))
    assert d["plan"]["tonemap"] is True
    assert "hdr_no_tonemap" not in d["reasons"]


def test_hdr_passthrough_when_caps_hdr():
    d = pb.plan(_media(hdr="hdr10"), caps=_caps(hdr=True))
    assert d["method"] == "direct"


def _hevc10_media(**kw):
    return _media(vcodec="hevc", bit_depth=10, video_profile="Main 10",
                  video_level=150, **kw)


def _caps_hevc10(**kw):
    return _caps(video={"h264": True, "hevc10": True}, **kw)


def test_hdr_passthrough_when_client_decodes_pq():
    """caps.hdr_decode=能解 PQ（与显示器是否 HDR 无关）→ Plex 式原画直通。"""
    d = pb.plan(_hevc10_media(hdr="hdr10"), caps=_caps_hevc10(hdr_decode=True))
    assert d["method"] == "direct"
    assert "hdr_not_supported" not in d["reasons"]


def test_dv_compat1_passthrough_when_client_decodes_pq():
    d = pb.plan(_hevc10_media(dv_profile=8, dv_bl_compat=1),
                caps=_caps_hevc10(hdr_decode=True))
    assert d["method"] == "direct"
    assert "hdr_not_supported" not in d["reasons"]


def test_hdr_without_decode_capability_still_transcodes():
    d = pb.plan(_hevc10_media(hdr="hdr10"), caps=_caps_hevc10())
    assert d["method"] == "video_transcode"
    assert "hdr_not_supported" in d["reasons"]


def test_native_hls_passthrough_hdr():
    d = pb.plan(_hevc10_media(hdr="hdr10"), caps=_caps_hevc10(native_hls=True))
    assert d["method"] == "direct"


def test_dv_profile5_blocked_even_with_pq_decode():
    d = pb.plan(_hevc10_media(dv_profile=5, dv_bl_compat=0),
                caps=_caps_hevc10(hdr_decode=True))
    assert d["method"] == "video_transcode"
    assert "dovi_not_supported" in d["reasons"]


def test_caps_normalize_keeps_hdr_decode_bool():
    from app import caps as caps_mod
    c = caps_mod.normalize_caps({"hdr_decode": True, "hdr": "yes"})
    assert c["hdr_decode"] is True
    assert c["hdr"] is False


def test_dv_compat2_treated_as_sdr():
    d = pb.plan(_media(dv_profile=8, dv_bl_compat=2), caps=_caps())
    assert d["method"] == "direct"


def test_dv_profile5_blocks_direct():
    d = pb.plan(_media(dv_profile=5, dv_bl_compat=0), caps=_caps())
    assert d["method"] == "video_transcode"
    assert "dovi_not_supported" in d["reasons"]


def test_vobsub_forces_burn():
    d = pb.plan(_media(subs=[{"index": 0, "ff_index": 5, "codec": "vobsub",
                              "image": 1, "lang": "", "title": "", "default": 0,
                              "forced": 0}]),
                caps=_caps(), sub_idx=0)
    assert d["subtitle_mode"] == "burn"
    assert d["method"] == "video_transcode"
    assert "vobsub_needs_burn" in d["reasons"]


def test_burn_hdr_flags_no_tonemap_even_with_hw(hw_on):
    """烧录走软件滤镜图，硬件 tonemap 不可用 → 必须如实提示 hdr_no_tonemap（P1-08）。"""
    d = pb.plan(_media(hdr="hdr10", subs=[{"index": 0, "ff_index": 5,
                                           "codec": "vobsub", "image": 1, "lang": "",
                                           "title": "", "default": 0, "forced": 0}]),
                caps=_caps(hdr=False), sub_idx=0)
    assert d["subtitle_mode"] == "burn"
    assert d["plan"]["tonemap"] is False
    assert "hdr_no_tonemap" in d["reasons"]


def test_text_subtitle_keeps_direct():
    d = pb.plan(_media(subs=[{"index": 0, "ff_index": 3, "codec": "subrip",
                              "image": 0, "lang": "chi", "title": "", "default": 1,
                              "forced": 0}]),
                caps=_caps(), sub_idx=0)
    assert d["method"] == "direct"
    assert d["subtitle_mode"] == "webvtt"


def test_non_default_audio_track_needs_remux():
    media = _media(audio=[
        {"index": 0, "ff_index": 1, "codec": "aac", "channels": 2, "bitrate": 128000,
         "lang": "eng", "title": "", "default": 1, "forced": 0, "caps": []},
        {"index": 1, "ff_index": 2, "codec": "aac", "channels": 2, "bitrate": 128000,
         "lang": "chi", "title": "", "default": 0, "forced": 0, "caps": []},
    ])
    d = pb.plan(media, caps=_caps(), audio_idx=1)
    assert d["method"] == "remux"
    assert "audio_track_selection" in d["reasons"]


def test_score_ordering():
    direct = pb.score(_media(), caps=_caps())
    remux = pb.score(_media(container="matroska"), caps=_caps())
    audio_tc = pb.score(_media(audio=[{"index": 0, "ff_index": 1, "codec": "dts",
                                       "channels": 6, "bitrate": 768000, "lang": "",
                                       "title": "", "default": 1, "forced": 0,
                                       "caps": []}]), caps=_caps())
    assert direct < remux < audio_tc


def test_plan_marker_stability_and_ignores():
    p = {"vcopy": True, "acopy": True, "height": 0, "sub": "none", "sub_idx": None,
         "tonemap": False, "audio_idx": 0, "audios": [], "seg": "fmp4"}
    m0 = _plan_marker(p, 0, 0)
    # fMP4 下所选音轨不进键
    assert _plan_marker(p, 3, 0) == m0
    # 非烧录字幕归一化
    p_vtt = {**p, "sub": "webvtt", "sub_idx": 7}
    assert _plan_marker(p_vtt, 0, 0) == m0
    # start 进键
    assert _plan_marker(p, 0, 30) != m0
    # 烧录保留
    p_burn = {**p, "sub": "burn", "sub_idx": 2, "sub_ff_index": 5}
    assert _plan_marker(p_burn, 0, 0) != m0
    assert '"v": 2' in m0.replace("'", '"')


def test_quality_and_session_keys():
    copy = {"vcopy": True, "height": 0, "seg": "fmp4"}
    h720 = {"vcopy": False, "height": 720, "seg": "fmp4"}
    src = {"vcopy": False, "height": 0, "seg": "fmp4"}
    assert _quality_key(copy) == "copy"
    assert _quality_key(h720) == "h720"
    assert _quality_key(src) == "src"
    assert _session_key(copy, 0) == "fcopy"
    ts = {"vcopy": True, "height": 0, "seg": "ts"}
    assert _session_key(ts, 1) == "copy_a1"


# ---------- B7-PLAYBACK 补充矩阵 ----------

def test_explicit_quality_does_not_upscale():
    d = pb.plan(_media(vcodec="mpeg4", height=480, width=640), caps=_caps(),
                quality="720p")
    assert d["method"] == "video_transcode"
    assert d["plan"]["height"] == 480          # 不放大（评审 B7/R11-D1）


def test_quality_source_uncapped_with_reason():
    d = pb.plan(_media(vcodec="hevc", height=2160, width=3840), caps=_caps(),
                quality="source")
    assert d["method"] == "video_transcode"
    assert d["plan"]["height"] == 0
    assert "source_transcode" in d["reasons"]


def test_dovi_no_base_reason_when_tonemapping_with_hw(hw_on):
    d = pb.plan(_media(dv_profile=5, dv_bl_compat=0), caps=_caps())
    assert d["plan"]["tonemap"] is True
    assert "dovi_no_base_tonemap" in d["reasons"]
    assert "hdr_no_tonemap" not in d["reasons"]


def test_native_hls_keeps_direct_for_non_default_audio():
    media = _media(audio=[
        {"index": 0, "ff_index": 1, "codec": "aac", "channels": 2, "bitrate": 128000,
         "lang": "eng", "title": "", "default": 1, "forced": 0, "caps": []},
        {"index": 1, "ff_index": 2, "codec": "aac", "channels": 2, "bitrate": 128000,
         "lang": "chi", "title": "", "default": 0, "forced": 0, "caps": []},
    ])
    d = pb.plan(media, caps=_caps(native_hls=True), audio_idx=1)
    assert d["method"] == "direct"             # Safari 原生可切轨，无需 remux
    assert d["plan"]["audio_idx"] == 1


def test_audio_copy_safe_env_allows_eac3(monkeypatch):
    monkeypatch.setenv("AUDIO_COPY_SAFE", "aac,mp3,eac3,ac3")
    media = _media(audio=[{"index": 0, "ff_index": 1, "codec": "eac3",
                           "channels": 6, "bitrate": 640000, "lang": "eng",
                           "title": "", "default": 1, "forced": 0, "caps": []}])
    d = pb.plan(media, caps=_caps(audio={"aac": True, "eac3": True}))
    assert d["method"] == "direct"


# ---------- B11 审计补齐：无 MSE 客户端不能走 HLS 档（R11-D2） ----------

def test_no_mse_blocks_hls_tier():
    caps = _caps(mse=False, native_hls=False)
    # mkv 容器需要 remux → 无 MSE 时阻断并给 no_mse
    m = _media(container="mkv")
    d = pb.plan(m, caps, quality="auto", client="web")
    assert d["method"] == "blocked"
    assert "no_mse" in d["reasons"]


def test_no_mse_direct_ok():
    caps = _caps(mse=False, native_hls=False)
    m = _media()   # mp4/h264/aac → direct
    d = pb.plan(m, caps, quality="auto", client="web")
    assert d["method"] == "direct"
    assert "no_mse" not in d["reasons"]


def test_no_mse_native_hls_allows_remux():
    caps = _caps(mse=False, native_hls=True)
    m = _media(container="mkv")
    d = pb.plan(m, caps, quality="auto", client="web")
    assert d["method"] == "remux"


def test_quality_key_burn_splits_dir():
    copy = {"vcopy": True, "acopy": True, "height": 0, "sub": "none"}
    burn = {"vcopy": True, "acopy": True, "height": 0, "sub": "burn"}
    assert _quality_key(copy) == "copy"
    assert _quality_key(burn) == "copy_burn"
    assert _quality_key({**copy, "height": 720, "vcopy": False}) == "h720"
    assert _quality_key({**burn, "height": 720, "vcopy": False}) == "h720_burn"


def test_transcoded_video_codec_level_by_output_size():
    """CODECS 声明必须与编码器实际 level 匹配（沙丘 720p 实测：1282x720 实际 3.2，
    旧实现按高度硬编码 3.1 → 声明低于实际，严格客户端可能拒绝视频轨）。"""
    tc = pb.transcoded_video_codec
    assert tc(1280, 720).endswith("1F")     # 3600 MBs → 3.1
    assert tc(1282, 720).endswith("20")     # 3645 MBs → 3.2（宽超 1280 就要 3.2）
    assert tc(1920, 1080).endswith("28")    # 8160 MBs → 4.0
    assert tc(3840, 2160).endswith("33")    # 32400 MBs → 5.1
    assert tc(0, 0).endswith("28")          # 未知尺寸兜底 4.0
    assert tc(1282, 720).startswith("avc1.6400")   # High profile
