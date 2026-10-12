"""ClientCapabilities：前端实测能力的归一化与摘要（目标文档 §4）。

前端 caps.js 用 MediaSource.isTypeSupported / navigator.mediaCapabilities.decodingInfo
实测后随 decide/versions/sessions 上报；这里白名单化（拒绝超大 payload）并给会话
plan_key 提供摘要，防止不同浏览器/能力复用错档。probes 为逐片候选码串的实测结果。
"""
import hashlib
import json

VIDEO_KEYS = ("h264", "h264_hi10p", "hevc", "hevc10", "av1", "vp9", "mpeg2", "vc1")
AUDIO_KEYS = ("aac", "mp3", "ac3", "eac3", "dts", "truehd", "flac", "opus", "vorbis", "pcm")
CONTAINER_KEYS = ("mp4", "mov", "m4v", "mkv", "webm", "mpegts")
HDR_FORMATS = ("hdr10", "hlg", "dolby_vision")
_MAX_PROBES = 32
_MAX_PROBE_LEN = 160


def default_caps() -> dict:
    """服务端保守默认（无 caps 的旧客户端/GET 调试/预转码用）：
    等价改造前的硬编码 web profile（H264 + AAC/MP3 + MP4）。"""
    return {"video": {"h264": True}, "audio": {"aac": True, "mp3": True},
            "hdr": False, "mse": True, "native_hls": False, "probes": {}}


def normalize_caps(raw) -> dict:
    """原始 caps → 白名单结构（未知键丢弃；布尔只认真正的 True，防 "false" 这类
    字符串被 bool() 收成真值；probes 限量限长）。"""
    raw = raw if isinstance(raw, dict) else {}

    def _bools(keys, src):
        src = src if isinstance(src, dict) else {}
        return {k: src.get(k) is True for k in keys}

    def _probes(src):
        out: dict[str, bool] = {}
        if isinstance(src, dict):
            for k, v in list(src.items())[:_MAX_PROBES]:
                k2 = str(k or "").strip()
                if k2 and len(k2) <= _MAX_PROBE_LEN:
                    out[k2] = v is True
        return out

    file = raw.get("file")
    file = ({"video": _bools(VIDEO_KEYS, file.get("video")),
             "audio": _bools(AUDIO_KEYS, file.get("audio")),
             "hdr": file.get("hdr") is True,
             "hdr_decode": file.get("hdr_decode") is True,
             "probes": _probes(file.get("probes"))}
            if isinstance(file, dict) else None)
    hdr_formats = raw.get("hdr_formats")
    hdr_formats = hdr_formats if isinstance(hdr_formats, list) else []
    return {"video": _bools(VIDEO_KEYS, raw.get("video")),
            "audio": _bools(AUDIO_KEYS, raw.get("audio")),
            "hdr": raw.get("hdr") is True,
            "hdr_decode": raw.get("hdr_decode") is True,
            "mse": raw.get("mse") is True if "mse" in raw else True,
            "native_hls": raw.get("native_hls") is True,
            "native_audio_tracks": (raw.get("native_audio_tracks") is True
                                    if "native_audio_tracks" in raw
                                    else raw.get("native_hls") is True),
            "hls_compat": raw.get("hls_compat") is True,
            "mp4_compat": raw.get("mp4_compat") is True,
            "force_hls": raw.get("force_hls") is True,
            "file": file,
            # Native capabilities are explicit and never inferred from browser flags.
            "containers": _bools(CONTAINER_KEYS, raw.get("containers")),
            "hls": raw.get("hls") is True,
            "hls_audio": _bools(AUDIO_KEYS, raw.get("hls_audio")),
            "audio_track_selection": raw.get("audio_track_selection") is True,
            "hdr_formats": [k for k in HDR_FORMATS if k in hdr_formats[:16]],
            "subtitles": _bools(("webvtt", "ass"), raw.get("subtitles")),
            "probes": _probes(raw.get("probes"))}


def caps_hash(caps: dict) -> str:
    """会话复用键摘要：只含影响 plan 的归一化字段（sort_keys 稳定）。
    用 BLAKE2b 而非 SHA-1（评审 B6/R11-B6）：非密码学用途也不留弱哈希告警。"""
    try:
        blob = json.dumps(normalize_caps(caps), sort_keys=True, ensure_ascii=True)
    except Exception:
        blob = "{}"
    return hashlib.blake2b(blob.encode("utf-8"), digest_size=6).hexdigest()


def probe_state(caps: dict, strings: list) -> int:
    """候选码串实测三态：1=支持（任一 true）/ 0=明确不支持（全 false）/ -1=未测。"""
    probes = (caps or {}).get("probes") or {}
    hits = tested = 0
    for s in (strings or []):
        if s in probes:
            tested += 1
            if probes.get(s):
                hits += 1
    if hits:
        return 1
    return 0 if tested else -1
