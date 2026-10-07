"""Android native decisions are explicit; browser defaults remain unchanged."""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import caps, playback, store
from app.main import app
from app.playback import backend
from app.routers.stream import media as stream_media
from app.routers.movies.routes import get_movie


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    monkeypatch.setattr(backend, "hw_backend", lambda: "")
    monkeypatch.setattr(backend, "hw_can_tonemap", lambda: False)


def native_caps(**changes):
    value = {
        "video": {"h264": True, "hevc": True, "hevc10": True},
        "audio": {"aac": True, "mp3": True, "ac3": True, "dts": True},
        "containers": {"mp4": True, "mov": True, "mkv": True, "webm": True},
        "hls": True, "hls_audio": {"aac": True, "mp3": True},
        "audio_track_selection": True,
        "hdr_formats": ["hdr10"], "subtitles": {"webvtt": True},
        "mse": False, "native_hls": False,
    }
    value.update(changes)
    return value


def media(**changes):
    value = {
        "playable": True, "container": "matroska", "vcodec": "h264",
        "height": 1080, "width": 1920, "bit_depth": 8, "duration": 600,
        "audio": [{"index": 0, "ff_index": 1, "codec": "aac", "channels": 2,
                   "default": 1}], "subs": [],
    }
    value.update(changes)
    return value


def decide(info=None, client_caps=None, **options):
    return playback.plan(info or media(), caps=client_caps or native_caps(),
                         client="android_tv", **options)


def test_native_caps_are_strict_bounded_and_hash_stable():
    value = caps.normalize_caps({
        "containers": {"mkv": True, "mp4": "true", "avi": True},
        "hls": "true", "audio_track_selection": 1,
        "hls_audio": {"aac": True, "dts": "false"},
        "subtitles": {"webvtt": True, "ass": True},
        "hdr_formats": ["hlg", "hdr10", "hdr10", "untrusted"],
    })
    assert value["containers"]["mkv"] is True
    assert value["containers"]["mp4"] is False
    assert "avi" not in value["containers"]
    assert value["hls"] is False
    assert value["audio_track_selection"] is False
    assert value["hdr_formats"] == ["hdr10", "hlg"]
    assert value["subtitles"] == {"webvtt": True}
    assert value["hls_audio"]["dts"] is False
    assert caps.caps_hash(value) == caps.caps_hash(deepcopy(value))
    assert caps.caps_hash(value) != caps.caps_hash({**value, "hls": True})
    assert caps.caps_hash(native_caps(hdr_formats=["hlg", "hdr10"])) == caps.caps_hash(
        native_caps(hdr_formats=["hdr10", "hlg"]))


def test_native_mkv_source_audio_and_track_selection_stay_direct():
    tracks = [media()["audio"][0], {"index": 1, "ff_index": 3, "codec": "dts", "channels": 6}]
    result = decide(media(audio=tracks), audio_idx=1)
    assert result["method"] == "direct"
    assert result["plan"]["audio_idx"] == 1
    # The DTS source can play directly but cannot be copied into an HLS rendition.
    assert result["plan"]["audios"][1]["copy"] is False


def test_native_does_not_inherit_kodi_or_browser_capabilities():
    result = playback.plan(media(), caps=caps.default_caps(), client="android_tv")
    assert result["method"] == "blocked"
    assert "hls_not_supported" in result["reasons"]
    assert playback.plan(media(), caps=native_caps(), client="web")["method"] == "blocked"


def test_native_hls_does_not_need_browser_mse():
    result = decide(media(container="avi"))
    assert result["method"] == "remux"
    assert result["plan"]["audios"][0]["copy"] is True


def test_native_hls_audio_requires_both_decoder_and_mux_support():
    result = decide(media(container="avi", audio=[{"codec": "ac3", "channels": 6}]))
    assert result["method"] == "audio_transcode"
    assert result["plan"]["audios"][0]["channels"] == 2
    supported = native_caps(hls_audio={"aac": True, "ac3": True})
    result = decide(media(container="avi", audio=[{"codec": "ac3", "channels": 6}]), supported)
    assert result["method"] == "remux"
    assert result["plan"]["audios"][0]["channels"] == 6
    result = decide(media(container="avi", audio=[{"codec": "dts", "channels": 6}]),
                    native_caps(hls_audio={"aac": True, "dts": True}))
    assert result["method"] == "audio_transcode"


def test_native_missing_output_support_blocks_instead_of_creating_unplayable_hls():
    result = decide(media(container="avi", audio=[{"codec": "dts"}]),
                    native_caps(hls_audio={}))
    assert result["method"] == "blocked"
    assert "hls_output_not_supported" in result["reasons"]


def test_native_direct_does_not_require_hls_or_audio_tracks():
    assert decide(client_caps=native_caps(hls=False))["method"] == "direct"
    assert decide(media(audio=[]))["method"] == "direct"
    assert decide(media(container="avi"), native_caps(hls=False))["method"] == "blocked"


def test_native_missing_direct_audio_selection_uses_hls():
    info = media(audio=[{"codec": "aac", "default": 1}, {"codec": "aac"}])
    result = decide(info, native_caps(audio_track_selection=False), audio_idx=1)
    assert result["method"] == "remux"
    assert "audio_track_selection" in result["reasons"]


def test_native_per_movie_probe_rejects_generic_hevc_support():
    codec = 'video/mp4; codecs="hvc1.2.4.L153.B0"'
    result = decide(media(vcodec="hevc", bit_depth=10, vcaps=[codec]),
                    native_caps(probes={codec: False}))
    assert result["method"] == "video_transcode"


@pytest.mark.parametrize(("hdr", "formats", "expected"), [
    ("hdr10", ["hdr10"], "direct"), ("hlg", ["hdr10"], "video_transcode"),
    ("hlg", ["hlg"], "direct"), ("hdr10", [], "video_transcode"),
])
def test_native_hdr_formats_are_independent(hdr, formats, expected):
    result = decide(media(hdr=hdr, vcodec="hevc", bit_depth=10),
                    native_caps(hdr_formats=formats, native_hls=True, hdr=True, hdr_decode=True))
    assert result["method"] == expected


def test_native_dolby_vision_requires_explicit_format_or_compatible_base():
    assert decide(media(dv_profile=5, dv_bl_compat=0))["method"] == "video_transcode"
    assert decide(media(dv_profile=8, dv_bl_compat=1))["method"] == "direct"
    assert decide(media(dv_profile=8, dv_bl_compat=2), native_caps(hdr_formats=[]))["method"] == "direct"
    assert decide(media(dv_profile=5, dv_bl_compat=0),
                  native_caps(hdr_formats=["dolby_vision"]))["method"] == "direct"


@pytest.mark.parametrize("codec", ["ass", "ssa", "subrip", "webvtt"])
def test_native_text_subtitles_use_webvtt(codec):
    result = decide(media(subs=[{"codec": codec, "image": 0}]), sub_idx=0)
    assert result["method"] == "direct"
    assert result["subtitle_mode"] == "webvtt"


@pytest.mark.parametrize("codec", ["pgs", "vobsub"])
def test_native_picture_subtitles_burn(codec):
    result = decide(media(subs=[{"codec": codec, "image": 1}]), sub_idx=0)
    assert result["method"] == "video_transcode"
    assert result["subtitle_mode"] == "burn"


def test_legacy_web_subtitle_and_container_decisions_unchanged():
    result = playback.plan(media(subs=[{"codec": "ass", "image": 0}]),
                           caps=caps.default_caps(), sub_idx=0)
    assert result["method"] == "remux"
    assert result["subtitle_mode"] == "ass_client"


def test_decide_payload_preserves_track_ids_and_escapes_source_name(monkeypatch):
    monkeypatch.setattr(stream_media, "_sub_list", lambda row, info: info["subs"])
    path = "电影/A #1 & 2.mkv"
    result = stream_media._decide_payload(
        {"id": 5, "kind": "movie", "file_path": path}, media(),
        stream_media.PlaybackQuery(client="android_tv", caps=native_caps()))
    assert result["method"] == "direct"
    assert result["media"]["audio"][0]["ff_index"] == 1
    assert parse_qs(urlsplit(result["direct_url"]).query) == {"name": [path]}


def test_client_handshake_is_read_only_and_validates_existing_token(monkeypatch):
    monkeypatch.setattr(stream_media.config, "effective_jzmedia_token", lambda: "test-token")
    client = TestClient(app)
    result = client.get("/api/stream/client-info")
    assert result.status_code == 200
    body = result.json()
    assert body["protocol_version"] == 1
    assert body["features"] == ["android_tv", "independent_sessions", "tv_search", "tv_actor_search"]
    assert body["auth_required"] is True
    # 局域网发现字段（additive，老客户端忽略）
    assert body["server_id"] == store.get_server_id() and body["server_id"]
    assert isinstance(body["name"], str) and body["name"]
    assert isinstance(body["version"], str) and body["version"]
    assert "test-token" not in result.text
    assert client.post("/api/stream/client-check").status_code == 401
    assert client.post("/api/stream/client-check", headers={"X-Api-Token": "bad"}).status_code == 401
    assert client.post("/api/stream/client-check", headers={"X-Api-Token": "test-token"}).json() == result.json()
    assert client.post("/api/stream/client-check", headers={"Authorization": "Bearer test-token"}).status_code == 200


def test_client_handshake_without_auth(monkeypatch):
    monkeypatch.setattr(stream_media.config, "effective_jzmedia_token", lambda: "")
    result = TestClient(app).post("/api/stream/client-check")
    assert result.status_code == 200
    assert result.json()["auth_required"] is False


def test_movie_detail_exposes_stored_extras_across_versions_without_scanning(monkeypatch):
    first = store.upsert_movie_by_path("native-tv-extras/a.mkv")
    second = store.upsert_movie_by_path("native-tv-extras/b.mkv")
    unrelated = store.upsert_movie_by_path("native-tv-extras/other.mkv")
    for mid in (first, second):
        store.update_movie_meta(mid, tmdb_id=973197, title="TV extra contract")
    a = store.upsert_extra("native-tv-extras/interview.mkv", first, "interview")
    b = store.upsert_extra("native-tv-extras/trailer.mp4", second, "trailer")
    store.upsert_extra("native-tv-extras/unrelated.mp4", unrelated)

    def no_scan(*args, **kwargs):
        raise AssertionError("movie detail must not enumerate the media backend")

    monkeypatch.setattr(stream_media.storage, "backend_for", no_scan)
    result = get_movie(first)
    assert {e["id"] for e in result["extras"]} == {a, b}
    assert {e["id"] for e in get_movie(second)["extras"]} == {a, b}
    assert store.get_playable("extra", b)["kind"] == "extra"
    assert next(e for e in result["extras"] if e["id"] == b)["kind"] == "trailer"
