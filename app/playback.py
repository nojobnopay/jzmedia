"""PlaybackPlanner：MediaInfo + ClientCapabilities + 用户选择 → 四档 PlaybackPlan + FFmpeg 命令。

四档（目标文档 §5，优先级从低到高开销）：
- direct：容器/视频/音频都兼容 → 复用 /api/movies/{id}/blob（Range 直发），零 CPU。
- remux：编码兼容、仅容器不对（MKV 等）→ ffmpeg -c copy 换浏览器容器。
- audio_transcode：视频兼容、所选音轨不兼容（DTS/TrueHD…）→ video copy + 音频转 AAC。
- video_transcode：视频编码/位深/HDR/图片字幕烧录/降档必须重编（P4 接 HW 后端）。
- blocked：文件不可播（探测失败/伪造）。

reasons 抄 Jellyfin TranscodeReason 思路，供前端人话提示；plan 供 build_cmd 组命令。
P2 起默认输出 HLS + fMP4（`-var_stream_map` 单进程：video + 全部音轨 rendition，
切音轨只换 rendition 不重开视频转码）；`HLS_SEGMENT_TYPE=ts` 可回滚旧 MPEG-TS 单音轨。
"""
import json
import os
import subprocess

from . import caps as _caps
from .config import settings
from .media import (ASS_SUBS, TEXT_SUBS, ffmpeg_bin, ffprobe_bin, norm_codec)

MP4_CONTAINERS = ("mp4", "mov", "m4v")
MAX_AUDIO_RENDITIONS = 8     # 单次转码最多产出的音轨 rendition 数（防极端多音轨）

# 音频 copy 安全集：浏览器声明支持 ≠ 当前输出管线能出音。
# - hls.js 管线实测 EAC3/AC3 copy 无声（古董局中局 EAC3 6ch 复现），默认只信 AAC/MP3；
#   fMP4 下若实测 Windows Chrome Dolby 有声，可用 env AUDIO_COPY_SAFE=eac3,ac3 放开。
# - Safari 原生 HLS 走 Apple 管线，Dolby（AC3/EAC3）可直通。
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


def hw_backend() -> str:
    """当前转码后端名（''=软件；qsv/vaapi/nvenc）。
    P4 起由 `app/transcode.detect()` 冒烟探测（env TRANSCODER/HW_ACCEL 可强制）。"""
    try:
        from . import transcode
        return transcode.detect().name
    except Exception:
        return ""


def hw_can_tonemap() -> bool:
    """当前后端能否做 HDR→SDR 实时 tonemap：仅 VAAPI（tonemap_vaapi）/ QSV（vpp_qsv）。
    NVENC 未实现（tonemap_cuda 需单独滤镜链）→ 视作不可，走 hdr_no_tonemap 提示外放。"""
    return hw_backend() in ("vaapi", "qsv")


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


def _target_height(quality: str, media: dict, need_encode: bool) -> tuple[int, str]:
    """目标高度 + auto 降档原因。档位语义：
    - auto（新前端默认）与 original（旧前端兼容）→ 需视频重编且片源>1080p 时封顶
      （无硬件转码 720p / 有硬件 1080p；copy 路径不封顶，保原分辨率）；
    - source（原画）→ 0 不封顶（显式选择，重编耗 CPU 由 reasons 提示）；
    - 720p/1080p 显式降档。
    P4 起封顶值由转码后端能力决定（HW 1080p / 软件 720p）。"""
    q = (quality or "auto").strip().lower()
    if q in ("720p", "720"):
        return 720, ""
    if q in ("1080p", "1080"):
        return 1080, ""
    if q in ("source",):
        return 0, ""
    try:
        h = int(media.get("height") or 0)
    except (TypeError, ValueError):
        h = 0
    if need_encode and h > 1080:
        return (1080, "auto_downscale_1080p") if hw_backend() else (720, "auto_downscale_720p")
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
        # 作为弱 NAS 的主要能力）；无 HW/后端不支持时明确提示色彩偏灰并建议外部播放器/直链
        tonemap = False
        if hdr_tonemap:
            if hw_can_tonemap():
                tonemap = True
            else:
                reasons.append("hdr_no_tonemap")
        return emit("video_transcode", sub_mode,
                    {"vcopy": False, "acopy": bool(a_ok), "height": target_height,
                     "sub": sub_mode, "audio_idx": ai, "sub_idx": sub_idx,
                     "tonemap": tonemap}, variants)
    if not a_ok:
        reasons.append("audio_codec_not_supported")
        return emit("audio_transcode", sub_mode,
                    {"vcopy": True, "acopy": False, "height": 0, "sub": sub_mode,
                     "audio_idx": ai, "sub_idx": sub_idx}, variants)
    if container not in MP4_CONTAINERS:
        reasons.append("container_not_supported")
        return emit("remux", sub_mode,
                    {"vcopy": True, "acopy": True, "height": 0, "sub": sub_mode,
                     "audio_idx": ai, "sub_idx": sub_idx}, variants)
    # 原文件直发只能播默认音轨（Chrome/Firefox 无 audioTracks 切换 API）：
    # 用户选了非默认轨 → 走 HLS remux（全部音轨 rendition），由前端切 hls.audioTrack。
    default_ai = next((i for i, t in enumerate(audios)
                       if int((t or {}).get("default") or 0)), 0)
    if ai != default_ai and not caps.get("native_hls"):
        reasons.append("audio_track_selection")
        return emit("remux", sub_mode,
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


def _append_video_encoder(cmd: list, backend, height: int, burn: bool,
                          tonemap: bool = False) -> None:
    """追加视频重编码参数（委托 transcode.Backend.video_args）。
    burn（filter_complex 烧录）与硬件滤镜互斥 → Backend 内部回落软件。"""
    cmd += backend.video_args(height, burn=burn, tonemap=tonemap)


def _keyframe_start(abs_path: str, st: float) -> float | None:
    """源中 <= st 的最后一个视频关键帧 PTS（对齐 copy 会话 -noaccurate_seek 实际起点）。
    ffprobe 只解码关键帧（-skip_frame nokey），失败/超时返回 None 由调用方回落。"""
    for span in (10.0, 300.0):
        lo = max(0.0, st - span)
        cmd = [ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
               "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
               "-of", "json",
               "-read_intervals", f"{lo:.3f}%{st + 0.05:.3f}",
               abs_path]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        try:
            frames = json.loads(out.stdout.decode("utf-8", errors="replace")).get("frames") or []
        except ValueError:
            frames = []
        pts: list[float] = []
        for f in frames:
            try:
                v = float((f or {}).get("pts_time"))
            except (TypeError, ValueError):
                continue
            if v <= st + 1e-3:
                pts.append(v)
        if pts:
            return max(pts)
    return None


def actual_media_start(abs_path: str, start: float, vcopy: bool) -> float:
    """会话片内 0 对应的源时间：copy 会话=目标前关键帧（-noaccurate_seek），
    转码/烧录=准确 seek 到 start。客户端字幕等绝对时间轴产物按此平移。"""
    try:
        st = max(0.0, float(start or 0))
    except (TypeError, ValueError):
        st = 0.0
    if st <= 0:
        return 0.0
    if not vcopy:
        return st
    kf = _keyframe_start(abs_path, st)
    return kf if (kf is not None and 0 <= kf <= st + 1e-3) else st


def _seek_args(cmd: list, vcopy: bool, start: float) -> tuple[float, float]:
    """seek 对齐（B4）追加输入侧参数，返回 (输出侧精确裁剪秒数, 输入侧提前秒数)。
    - 转码：输入 -ss 到目标前 15s + 输出侧裁 15s → 解码器有完整 GOP，音视频同点；
    - 拷贝：-noaccurate_seek 从目标前一关键帧整段起（代价：起播最多早一个 GOP）。
    外挂图片字幕烧录时，第二输入要用同样的输入侧提前量（见 build_cmd）。"""
    try:
        st = max(0.0, float(start or 0))
    except (TypeError, ValueError):
        st = 0.0
    trim_after = 0.0
    pre = 0.0
    if st > 0:
        if vcopy:
            cmd += ["-ss", f"{st:.3f}", "-noaccurate_seek"]
        else:
            pre = max(0.0, st - 15.0)
            if pre > 0:
                cmd += ["-ss", f"{pre:.3f}"]
            trim_after = min(st, 15.0)
    return trim_after, pre


def _sub_overlay_filter(plan: dict) -> tuple[str, bool]:
    """烧录 overlay 滤镜：返回 (filter_complex 串, 是否外挂第二输入)。
    - 内嵌图片字幕：同一输入 [0:v][0:s:N]；
    - 外挂图片字幕（VobSub）：第二输入 [0:v][1:s:0]。"""
    side = str(plan.get("sub_sidecar") or "")
    if side:
        return "[0:v][1:s:0]overlay=eof_action=pass", True
    return ("[0:v][0:s:%d]overlay=eof_action=pass" % int(plan["sub_ff_index"]), False)


def build_cmd(abs_path: str, plan: dict,
              start: float = 0, seg_time: int | None = None,
              force_sw: bool = False) -> list[str]:
    """按 plan() 构造 ffmpeg HLS 命令。
    ⚠️ 必须 `cwd=会话目录` 执行：ffmpeg 的 `-hls_segment_filename` 相对路径按进程
    CWD 解析（写文件），而播放列表里的 URI 按列表位置解析；只有 cwd=目录 + 裸文件名
    才能两者一致（否则分片会落到服务进程 CWD，会话目录里永远没分片）。
    - fmp4（默认）：`-var_stream_map` 单进程，video + 全部音轨 rendition 扁平落盘
      （out_<name>.m3u8 + <name>_init.mp4/<name>_segNNNNN.m4s）；master.m3u8 由
      stream._write_master 生成（ffmpeg 对 HEVC copy 不产 CODECS，Safari 需要）。
    - ts（HLS_SEGMENT_TYPE=ts 回滚）：旧单 variant MPEG-TS，master.m3u8 + segNNNNN.ts。
    HW 加速：transcode.detect() 冒烟探测（env TRANSCODER/HW_ACCEL 可强制）；
    force_sw=True 用于硬件路径失败后的软件重试。"""
    plan = plan or {}
    abs_path = os.path.abspath(abs_path)  # cwd=会话目录执行，输入须绝对化
    backend = None
    if not plan.get("vcopy"):
        from . import transcode
        backend = transcode.software() if force_sw else transcode.detect()
    if (plan.get("seg") or seg_type()) == "ts":
        return _build_cmd_ts(abs_path, plan, start, seg_time or 6, backend=backend)
    return _build_cmd_fmp4(abs_path, plan, start, seg_time or 4, backend=backend)


def _is_burn(plan: dict) -> bool:
    """图片字幕烧录（内嵌 sub_ff_index 或外挂 sub_sidecar 二选一）。"""
    return (plan.get("sub") == "burn"
            and (plan.get("sub_ff_index") is not None
                 or bool(plan.get("sub_sidecar"))))


def _append_sub_input(cmd: list, plan: dict, pre: float) -> None:
    """外挂图片字幕（VobSub）第二输入：与主输入同样的输入侧提前量（时间轴对齐）。"""
    rel = str(plan.get("sub_sidecar") or "")
    if not rel:
        return
    if pre > 0:
        cmd += ["-ss", f"{pre:.3f}"]
    cmd += ["-i", os.path.abspath(os.path.join(settings.media_root, rel))]


def _build_cmd_fmp4(abs_path: str, plan: dict,
                    start: float, seg_time: int, backend=None) -> list[str]:
    vcopy = bool(plan.get("vcopy"))
    height = int(plan.get("height") or 0)
    burn_sub = _is_burn(plan)
    variants = list(plan.get("audios") or [])[:MAX_AUDIO_RENDITIONS]
    if burn_sub:
        vcopy = False  # 烧录必须重编码
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error"]
    trim_after, pre = _seek_args(cmd, vcopy, start)
    # 输入侧硬件参数（-hwaccel/-vaapi_device 必须在 -i 之前；烧录走软件滤镜图）
    if not vcopy and backend is not None:
        cmd += backend.input_args(burn=burn_sub)
    # 输入侧 genpts：-ss 跳转后缺/乱 PTS 由 demuxer 补齐（放 -i 之前才生效）
    cmd += ["-fflags", "+genpts", "-i", abs_path]
    if burn_sub:
        _append_sub_input(cmd, plan, pre)
    if trim_after > 0:
        cmd += ["-ss", f"{trim_after:.3f}"]
    # 映射：视频（烧录走 overlay 滤镜输出）+ 全部音轨
    if burn_sub:
        # overlay 不缩放覆盖层：源分辨率叠加后再缩放；eof_action=pass 防最后一句字幕钉片尾
        vf = _sub_overlay_filter(plan)[0]
        if height:
            vf += ",scale=-2:%d" % height
        vf += ",format=yuv420p"   # 烧录输出同样降 8bit（10bit 源否则变 High10，浏览器不能解）
        cmd += ["-filter_complex", vf + "[vout]", "-map", "[vout]"]
        height = 0  # 缩放已在滤镜图内
    else:
        cmd += ["-map", "v:0"]
    for a in variants:
        cmd += ["-map", f"a:{int(a.get('i') or 0)}"]
    # 视频编码
    if vcopy:
        cmd += ["-c:v", "copy"]
        if norm_codec(str(plan.get("vcodec") or "")) == "hevc":
            cmd += ["-tag:v", "hvc1"]  # Apple/MSE 认 hvc1（hev1 部分客户端不认）
    else:
        from . import transcode as _tr
        _append_video_encoder(cmd, backend or _tr.software(), height,
                              burn=burn_sub, tonemap=bool(plan.get("tonemap")))
        # 4s 强制关键帧：分段对齐，避免拷贝帧率/时长漂移（音频 rendition 同步也依赖）
        cmd += ["-force_key_frames", f"expr:gte(t,n_forced*{int(seg_time)})"]
    # 音频编码：按 rendition 序号（a:N 指输出音频流序号）
    for n, a in enumerate(variants):
        if a.get("copy"):
            cmd += [f"-c:a:{n}", "copy"]
        else:
            cmd += [f"-c:a:{n}", "aac", f"-b:a:{n}", "192k", f"-ac:a:{n}", "2"]
    # var_stream_map：video 变体带 agroup:aud，音轨各自独立 rendition
    parts = ["v:0,agroup:aud,name:video"] if variants else ["v:0,name:video"]
    for n, a in enumerate(variants):
        opts = [f"a:{n}", "agroup:aud", f"name:audio{n}"]
        if a.get("lang"):
            opts.append(f"language:{a['lang']}")
        parts.append(",".join(opts))
    cmd += ["-var_stream_map", " ".join(parts)]
    cmd += ["-f", "hls", "-hls_time", str(int(seg_time)),
            "-hls_list_size", "0",
            "-hls_segment_type", "fmp4",
            # %v = var_stream_map 的 name；扁平命名（<name>_segNNNNN.m4s）便于统一 HTTP 路由
            "-hls_fmp4_init_filename", "%v_init.mp4",
            "-hls_segment_filename", "%v_seg%05d.m4s",
            # temp_file：分片先写 .tmp 再原子改名，防半写分片被 hls.js 拉走
            "-hls_flags", "temp_file+independent_segments",
            # 时间戳归一：输入 -ss 后音视频起点常错位数秒，MSE 遇大跨度错位会黑屏/卡死
            "-avoid_negative_ts", "make_zero",
            "out_%v.m3u8"]
    return cmd


def _build_cmd_ts(abs_path: str, plan: dict,
                  start: float, seg_time: int, backend=None) -> list[str]:
    """旧 MPEG-TS 单 variant（回滚路径，P1 行为不变）：master.m3u8 + seg%05d.ts。
    路径为裸名，调用方须以会话目录为 cwd 执行（见 build_cmd 注释）。"""
    out_m3u8 = "master.m3u8"
    vcopy = bool(plan.get("vcopy"))
    acopy = bool(plan.get("acopy"))
    height = int(plan.get("height") or 0)
    try:
        ai = max(0, int(plan.get("audio_idx") or 0))
    except (TypeError, ValueError):
        ai = 0
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error"]
    burn_sub = _is_burn(plan)
    if burn_sub:
        vcopy = False  # 烧录必须重编码
    trim_after, pre = _seek_args(cmd, vcopy, start)
    if not vcopy and backend is not None:
        cmd += backend.input_args(burn=burn_sub)
    # 输入侧 genpts：-ss 跳转后缺/乱 PTS 由 demuxer 补齐（放 -i 之前才生效）
    cmd += ["-fflags", "+genpts"]
    cmd += ["-i", abs_path]
    if burn_sub:
        _append_sub_input(cmd, plan, pre)
    if trim_after > 0:
        cmd += ["-ss", f"{trim_after:.3f}"]
    if burn_sub:
        # 图片字幕烧录：源分辨率下 overlay 再缩放（overlay 不缩放覆盖层），
        # eof_action=pass：字幕流结束后原样放行视频（默认 repeat 会把最后一句字幕钉到片尾）。
        vf = _sub_overlay_filter(plan)[0]
        if height:
            vf += ",scale=-2:%d" % height
        vf += ",format=yuv420p"   # 烧录输出同样降 8bit
        cmd += ["-filter_complex", vf + "[vout]", "-map", "[vout]", "-map", f"a:{ai}?"]
        height = 0  # 缩放已在滤镜图内
    else:
        cmd += ["-map", "v:0", "-map", f"a:{ai}?"]
    if vcopy:
        cmd += ["-c:v", "copy"]
    else:
        from . import transcode as _tr
        _append_video_encoder(cmd, backend or _tr.software(), height,
                              burn=burn_sub, tonemap=bool(plan.get("tonemap")))
    if acopy:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    seg_pat = "seg%05d.ts"
    cmd += ["-f", "hls", "-hls_time", str(seg_time),
            "-hls_list_size", "0", "-hls_segment_type", "mpegts",
            # 不要加 -hls_playlist_type event！hls.js 会把 EVENT 当 VOD 只播首屏分片
            # （约 18s 必死）；Plex 也是纯 live 式增长列表，无 ENDLIST 即刷新。
            "-avoid_negative_ts", "make_zero",
            "-hls_segment_filename", seg_pat, out_m3u8]
    return cmd
