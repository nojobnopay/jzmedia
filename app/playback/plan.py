"""playback.plan（自 app/playback.py 拆分，评审 B9/R11-Q1；经 playback 门面使用）。"""
import os
from .. import caps as _caps
from ..media import ASS_SUBS
from ..media import TEXT_SUBS
from ..media import norm_codec
from . import backend
from ..log import get_logger
logger = get_logger("playback.plan")
__all__ = ['MP4_CONTAINERS', 'MAX_AUDIO_RENDITIONS', 'AUDIO_COPY_SAFE', 'AUDIO_COPY_SAFE_NATIVE', '_ISO639_2', '_env_set', 'seg_type', 'seg_time', '_is_kodi', '_generic_video_key', '_video_ok', '_audio_ok', 'iso639_2', 'audio_variants', 'transcoded_video_codec', '_hdr_blocks_direct', '_subtitle_mode', '_clamp_to_source', '_target_height', 'plan', 'score']

MP4_CONTAINERS = ("mp4", "mov", "m4v")


MAX_AUDIO_RENDITIONS = 8     # 单次转码最多产出的音轨 rendition 数（防极端多音轨）


AUDIO_COPY_SAFE = {"aac", "mp3"}


AUDIO_COPY_SAFE_NATIVE = {"aac", "mp3", "ac3", "eac3"}


_ISO639_2 = {
    "zh": "chi", "cn": "chi", "en": "eng", "ja": "jpn", "jp": "jpn", "ko": "kor",
    "fr": "fra", "de": "deu", "es": "spa", "it": "ita", "ru": "rus", "pt": "por",
    "th": "tha", "vi": "vie", "ar": "ara", "hi": "hin", "nl": "nld", "sv": "swe",
    "pl": "pol", "tr": "tur", "id": "ind", "ms": "msa", "da": "dan", "no": "nor",
    "fi": "fin", "el": "ell", "he": "heb", "cs": "ces", "hu": "hun", "uk": "ukr",
}


def _env_set(name: str, default: set) -> set:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return {x.strip() for x in raw.split(",") if x.strip()} or default


def seg_type() -> str:
    """HLS 分片封装：fmp4（默认，P2）| ts（回滚开关）。"""
    v = (os.getenv("HLS_SEGMENT_TYPE") or "fmp4").strip().lower()
    return "ts" if v in ("ts", "mpegts") else "fmp4"


def seg_time(st: str | None = None) -> int:
    """分片时长：fMP4 4s（目标文档 §6）/ TS 6s（历史稳定值）。"""
    return 6 if (st or seg_type()) == "ts" else 4


def _is_kodi(client: str) -> bool:
    return (client or "web").strip().lower() == "kodi"


def _generic_video_key(vcodec: str, bit_depth: int) -> str:
    if vcodec == "h264":
        return "h264_hi10p" if bit_depth > 8 else "h264"
    if vcodec == "hevc":
        return "hevc10" if bit_depth > 8 else "hevc"
    if vcodec in ("av1", "vp9", "mpeg2", "vc1"):
        return vcodec
    return ""


def _video_ok(media: dict, caps: dict) -> bool:
    """视频可否直通：probes 精确结果优先（vcaps 任一 true/false），回落通用矩阵。"""
    vcodec = norm_codec(str(media.get("vcodec") or ""))
    try:
        bit_depth = int(media.get("bit_depth") or 0)
    except (TypeError, ValueError):
        bit_depth = 0
    st = _caps.probe_state(caps, list(media.get("vcaps") or []))
    if st != -1:
        return st == 1
    key = _generic_video_key(vcodec, bit_depth)
    return bool((caps.get("video") or {}).get(key)) if key else False


def _audio_ok(track: dict, caps: dict) -> bool:
    """音频可否直通（copy）：浏览器能力（probes 精确优先，回落通用矩阵）
    + 当前输出管线 copy 安全集（可用 env AUDIO_COPY_SAFE 放开，实测为准）。"""
    acodec = norm_codec(str((track or {}).get("codec") or ""))
    st = _caps.probe_state(caps, list((track or {}).get("caps") or []))
    if st != -1:
        ok = st == 1
    else:
        ok = bool((caps.get("audio") or {}).get(acodec))
    if not ok:
        return False
    safe = (_env_set("AUDIO_COPY_SAFE", AUDIO_COPY_SAFE) if not caps.get("native_hls")
            else _env_set("AUDIO_COPY_SAFE_NATIVE", AUDIO_COPY_SAFE_NATIVE))
    return acodec in safe


def iso639_2(lang: str) -> str:
    """HLS LANGUAGE 要求 ISO 639-2/B：两位码/别名转三位，未知返回空（不写该属性）。"""
    s = (lang or "").strip().lower()
    if not s or s in ("und", "unknown", "mul"):
        return ""
    if len(s) == 3 and s.isalpha():
        return s
    return _ISO639_2.get(s, "")


def audio_variants(media: dict, caps: dict) -> list[dict]:
    """全部音轨的 rendition 计划（fMP4 多音轨用）。顺序=输出 rendition 顺序。
    default 取源标注的首条 default 轨（无标注则第一条），与用户选择无关：
    选择由前端在 MANIFEST_PARSED 后切 hls.audioTrack，保证产物与选择解耦（静态复用稳）。"""
    audios = list(media.get("audio") or [])[:MAX_AUDIO_RENDITIONS]
    default_idx = next((i for i, t in enumerate(audios) if int((t or {}).get("default") or 0)), 0)
    out = []
    for i, t in enumerate(audios):
        t = t or {}
        copy = _audio_ok(t, caps)
        try:
            ch = int(t.get("channels") or 0)
        except (TypeError, ValueError):
            ch = 0
        out.append({"i": i, "copy": bool(copy),
                    "lang": iso639_2(str(t.get("lang") or "")),
                    "channels": ch if copy else 2,
                    "default": 1 if i == default_idx else 0})
    return out


def transcoded_video_codec(height: int) -> str:
    """重编目标（H.264 High）→ CODECS 码串：720p 用 level 3.1，其余 4.0。"""
    lv = 0x1F if int(height or 0) <= 720 else 0x28
    return f"avc1.6400{lv:02X}"


def _hdr_blocks_direct(media: dict, caps: dict, client: str) -> tuple[bool, str, bool]:
    """HDR/DV 直通判定（目标文档 §12）。返回 (是否阻止视频直通, reason, 是否需 tonemap)。
    - kodi 外部播放器直通；
    - DV：compat=2（SDR 基底）当 SDR；compat=1（HDR10 基底）当 HDR10；
      其余（profile 5 等无兼容基底）浏览器无法直通；
    - HDR10/HLG：客户端 caps.hdr=false 时不能直通 → 走转码（有 HW 后端才做 tonemap）。"""
    if _is_kodi(client):
        return False, "", False
    try:
        dv = int(media.get("dv_profile") or 0)
        compat = int(media.get("dv_bl_compat") or 0)
    except (TypeError, ValueError):
        dv, compat = 0, 0
    hdr = str(media.get("hdr") or "")
    if dv > 0:
        if compat == 2:
            return False, "", False
        if compat == 1:
            if caps.get("hdr"):
                return False, "", False
            return True, "hdr_not_supported", True
        return True, "dovi_not_supported", True
    if hdr:
        if caps.get("hdr"):
            return False, "", False
        return True, "hdr_not_supported", True
    return False, "", False


def _subtitle_mode(media: dict, sub_idx, reasons: list,
                   force_burn: bool = False) -> str:
    """subtitle_mode: none | webvtt | ass_client | pgs_client | burn。
    - 文本 → webvtt / ass_client（客户端渲染，切换不重开会话）
    - pgs（内嵌/外挂）→ pgs_client（libpgs 客户端，零转码）
    - vobsub / 未知图片 → burn（唯一需要视频重编的字幕路径）
    - force_burn：客户端解码失败等场景显式要求烧录（图片字幕才生效）"""
    if sub_idx is None:
        return "none"
    subs = media.get("subs") or []
    try:
        si = int(sub_idx)
    except (TypeError, ValueError):
        si = -1
    track = subs[si] if 0 <= si < len(subs) else None
    if track is None:
        reasons.append("subtitle_not_found")
        return "none"
    codec = norm_codec(str(track.get("codec") or ""))
    if int(track.get("image") or 0):
        if force_burn:
            reasons.append("subtitle_burn_forced")
            return "burn"
        if codec == "pgs":
            return "pgs_client"
        reasons.append("vobsub_needs_burn")
        return "burn"
    if codec in ASS_SUBS:
        return "ass_client"
    if codec in TEXT_SUBS:
        return "webvtt"
    reasons.append("subtitle_not_found")
    return "none"


def _clamp_to_source(target: int, media: dict) -> int:
    """封顶不超过源高（评审 B7/R11-D1）：480p 源选 720p 档时不再放大重编（浪费 CPU 无画质）"""
    try:
        h = int(media.get("height") or 0)
    except (TypeError, ValueError):
        h = 0
    return min(target, h) if (target and h) else target


def _target_height(quality: str, media: dict, need_encode: bool) -> tuple[int, str]:
    """目标高度 + auto 降档原因。档位语义：
    - auto（新前端默认）与 original（旧前端兼容）→ 需视频重编且片源>1080p 时封顶
      （无硬件转码 720p / 有硬件 1080p；copy 路径不封顶，保原分辨率）；
    - source（原画）→ 0 不封顶（显式选择，重编耗 CPU 由 reasons 提示）；
    - 720p/1080p 显式降档（同样不向上放大，见 _clamp_to_source）。
    P4 起封顶值由转码后端能力决定（HW 1080p / 软件 720p）。"""
    q = (quality or "auto").strip().lower()
    if q in ("720p", "720"):
        return _clamp_to_source(720, media), ""
    if q in ("1080p", "1080"):
        return _clamp_to_source(1080, media), ""
    if q in ("source",):
        return 0, ""
    try:
        h = int(media.get("height") or 0)
    except (TypeError, ValueError):
        h = 0
    if need_encode and h > 1080:
        return (1080, "auto_downscale_1080p") if backend.hw_backend() else (720, "auto_downscale_720p")
    return 0, ""


def plan(media: dict, caps: dict | None = None, quality: str = "auto",
         audio_idx: int = 0, sub_idx=None, client: str = "web",
         force_burn: bool = False) -> dict:
    """四档决策。返回 {method, reasons, plan, subtitle_mode}。
    plan: {vcopy, acopy, height, sub, audio_idx, sub_idx, audios, seg}（build_cmd 输入）。
    - audios: 全部音轨 rendition 计划（fMP4 多音轨；TS 回滚只用 audio_idx）；
    - seg: 输出封装（fmp4|ts，随 env），目录键前缀区分、不进复用键（见 stream._plan_marker）。"""
    caps = _caps.normalize_caps(caps)
    media = media or {}
    reasons: list[str] = []

    def emit(method: str, sub_mode: str, p: dict, variants: list) -> dict:
        out = dict(p)
        out["audios"] = variants
        out["seg"] = seg_type()
        out["vcodec"] = norm_codec(str(media.get("vcodec") or ""))
        return {"method": method, "reasons": reasons, "subtitle_mode": sub_mode, "plan": out}

    if not media.get("playable"):
        reasons.append("unplayable")
        return emit("blocked", "none",
                    {"vcopy": False, "acopy": False, "height": 0, "sub": "none",
                     "audio_idx": 0, "sub_idx": None}, [])
    if _is_kodi(client):
        reasons.append("kodi_passthrough")
        return emit("direct", "none",
                    {"vcopy": True, "acopy": True, "height": 0, "sub": "none",
                     "audio_idx": 0, "sub_idx": None}, [])
    # 无 MSE 且无原生 HLS 的浏览器（老 Safari/部分电视浏览器）播不了 HLS 档：
    # 只有 direct 可用，需要走 HLS 的档一律阻断并给明确原因（评审 R11-D2）
    no_mse = caps.get("mse") is False and not caps.get("native_hls")

    def _emit_or_blocked(method: str, sub_mode: str, plan_dict: dict, variants):
        if no_mse:
            reasons.append("no_mse")
            return emit("blocked", "none",
                        {"vcopy": False, "acopy": False, "height": 0, "sub": "none",
                         "audio_idx": 0, "sub_idx": None}, [])
        return emit(method, sub_mode, plan_dict, variants)
    container = str(media.get("container") or "").lower()
    vcodec = norm_codec(str(media.get("vcodec") or ""))
    try:
        bit_depth = int(media.get("bit_depth") or 0)
    except (TypeError, ValueError):
        bit_depth = 0
    try:
        height = int(media.get("height") or 0)
    except (TypeError, ValueError):
        height = 0
    audios = media.get("audio") or []
    try:
        ai = max(0, int(audio_idx or 0))
    except (TypeError, ValueError):
        ai = 0
    want_audio = audios[ai] if 0 <= ai < len(audios) else (audios[0] if audios else {})
    variants = audio_variants(media, caps)
    codec_ok = _video_ok(media, caps)
    v_ok = codec_ok
    a_ok = _audio_ok(want_audio, caps)
    sub_mode = _subtitle_mode(media, sub_idx, reasons, force_burn=force_burn)
    burn = sub_mode == "burn"
    hdr_block, hdr_reason, hdr_tonemap = _hdr_blocks_direct(media, caps, client)
    if hdr_block:
        v_ok = False
        reasons.append(hdr_reason)
    target_height, auto_reason = _target_height(quality, media,
                                                need_encode=(not v_ok) or burn)
    downgrade = bool(target_height and height and height > target_height)
    need_encode = (not v_ok) or burn or downgrade
    if need_encode:
        # reasons 顺序：字幕/HDR → 编码 → 降档 → 原画强制（前端按序拼人话提示）
        if not codec_ok:
            reasons.append("video_bit_depth_not_supported"
                           if vcodec == "h264" and bit_depth > 8
                           else "video_codec_not_supported")
        if auto_reason:
            reasons.append(auto_reason)
        elif downgrade:
            reasons.append("resolution_downscale")
        if (quality or "").strip().lower() == "source":
            reasons.append("source_transcode")  # 用户显式原画：不封顶重编，提示耗 CPU
        # HDR→SDR：仅硬件后端做实时 tonemap（目标文档 §12：不把实时 tone mapping
        # 作为弱 NAS 的主要能力）；烧录走软件滤镜图（overlay+缩放）用不了硬件 tonemap 链，
        # 软件 tonemap 亦非默认能力 → 同样如实提示偏灰（评审 P1-08），别让用户拿
        # 一版无提示的灰片。
        tonemap = False
        if hdr_tonemap:
            if backend.hw_can_tonemap() and not burn:
                tonemap = True
                try:
                    if int(media.get("dv_profile") or 0) > 0 and int(media.get("dv_bl_compat") or 0) == 0:
                        # DV 无 HDR10 基底（如 P5）：硬件 tonemap 按 HDR10 处理会偏色
                        reasons.append("dovi_no_base_tonemap")
                except (TypeError, ValueError):
                    pass
            else:
                reasons.append("hdr_no_tonemap")
        return _emit_or_blocked("video_transcode", sub_mode,
                    {"vcopy": False, "acopy": bool(a_ok), "height": target_height,
                     "sub": sub_mode, "audio_idx": ai, "sub_idx": sub_idx,
                     "tonemap": tonemap}, variants)
    if not a_ok:
        reasons.append("audio_codec_not_supported")
        return _emit_or_blocked("audio_transcode", sub_mode,
                    {"vcopy": True, "acopy": False, "height": 0, "sub": sub_mode,
                     "audio_idx": ai, "sub_idx": sub_idx}, variants)
    if container not in MP4_CONTAINERS:
        reasons.append("container_not_supported")
        return _emit_or_blocked("remux", sub_mode,
                    {"vcopy": True, "acopy": True, "height": 0, "sub": sub_mode,
                     "audio_idx": ai, "sub_idx": sub_idx}, variants)
    # 原文件直发只能播默认音轨（Chrome/Firefox 无 audioTracks 切换 API）：
    # 用户选了非默认轨 → 走 HLS remux（全部音轨 rendition），由前端切 hls.audioTrack。
    default_ai = next((i for i, t in enumerate(audios)
                       if int((t or {}).get("default") or 0)), 0)
    if ai != default_ai and not caps.get("native_hls"):
        reasons.append("audio_track_selection")
        return _emit_or_blocked("remux", sub_mode,
                    {"vcopy": True, "acopy": True, "height": 0, "sub": sub_mode,
                     "audio_idx": ai, "sub_idx": sub_idx}, variants)
    return emit("direct", sub_mode,
                {"vcopy": True, "acopy": True, "height": 0, "sub": sub_mode,
                 "audio_idx": ai, "sub_idx": sub_idx}, variants)


def score(media: dict, caps: dict | None = None, quality: str = "auto",
          client: str = "web") -> tuple:
    """选版打分（越小越优，抄 Plex 按客户端选版本）：
    direct(0) < remux(1) < audio_transcode(2) < video_transcode(3) < blocked(9)。
    direct/remux/音频转码同档取分辨率最高；视频转码档目标越小越省 CPU、
    copy 优于重编（audio_transcode 已单列，此处基本只剩 video 重编）。"""
    d = plan(media, caps=caps, quality=quality, client=client)
    m = d["method"]
    try:
        h = int(media.get("height") or 0)
    except (TypeError, ValueError):
        h = 0
    if m in ("direct", "remux", "audio_transcode"):
        return ({"direct": 0, "remux": 1, "audio_transcode": 2}[m], -h)
    if m == "video_transcode":
        p = d.get("plan") or {}
        cap = int(p.get("height") or 0) or 9999
        return (3, cap, 0 if p.get("vcopy") else 1)
    return (9, 0)

