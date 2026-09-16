"""在线播放：ffprobe 探测（MediaInfo）+ 二进制解析。

决策/命令构造已迁至 app/playback.py（ClientCapabilities + 四档 PlaybackPlan）。
本模块只负责：
- probe()：ffprobe 单文件 → media_info 行（含 HDR/DV/位深/轨道 disposition/附件），
  并给客户端能力实测生成候选码串（decorate()：vcaps + 每音轨 caps）。
- ffmpeg/ffprobe 二进制解析与健康检查。
"""
import json
import os
import re
import shutil
import subprocess

# 探测结构版本：老行 probe_ver < 本值 → 视为过期自动重探（新字段上线时 +1 即可）
# v3：图片字幕 codec 归一为 pgs/vobsub（原 hdmv_pgs_subtitle 等）
PROBE_VERSION = 3

# 二进制解析见下方 ffprobe_bin()/ffmpeg_bin()（系统版优先，静态版兜底）

# 可重试的环境错误前缀：这类失败不代表文件本身无效，绝不落库为最终结论
# （否则换个有 ffmpeg 的环境打开仍显示无效，MP4 误杀即此类）。
RETRYABLE_ERRORS = ("ffprobe not installed", "ffprobe timeout", "stat failed")


def _static_bins_if_present() -> dict:
    """已下载好的 static-ffmpeg 双二进制（只看文件在不在，绝不触发下载）。"""
    try:
        import static_ffmpeg  # noqa: F401
        import os as _os
        import glob as _glob
        pkgdir = _os.path.join(_os.path.dirname(__file__), "..", ".venv",
                               "lib", "python3.12", "site-packages",
                               "static_ffmpeg", "bin")
        # .venv 位置随启动方式变：优先按已安装包的实际路径找
        try:
            import static_ffmpeg as _pkg
            pkgdir = _os.path.join(_os.path.dirname(_pkg.__file__), "bin")
        except Exception:
            pass
        out = {}
        for name in ("ffmpeg", "ffprobe"):
            cands = _glob.glob(_os.path.join(pkgdir, "*", name)) + \
                _glob.glob(_os.path.join(pkgdir, "*", name + ".exe"))
            hit = next((c for c in cands if _os.path.isfile(c)), "")
            if hit:
                out[name] = hit
        return out
    except Exception:
        return {}


_BIN_CACHE: dict = {}


def _resolve_bin(name: str) -> str:
    """ffmpeg/ffprobe 解析：系统版优先 → 已下载的静态版 → 懒下载静态版 → 裸名（下游报错）。
    下载只在首次真实调用时发生一次；离线失败则缓存裸名，后续快速失败（仍为可重试错误）。"""
    if _BIN_CACHE.get(name):
        return _BIN_CACHE[name]
    p = shutil.which(name)
    if p:
        _BIN_CACHE[name] = p
        return p
    present = _static_bins_if_present()
    if present.get(name):
        _BIN_CACHE[name] = present[name]
        return present[name]
    try:
        from static_ffmpeg import run as _sfrun
        ff, fp = _sfrun.get_or_fetch_platform_executables_else_raise()
        if ff:
            _BIN_CACHE["ffmpeg"] = ff
        if fp:
            _BIN_CACHE["ffprobe"] = fp
        if _BIN_CACHE.get(name):
            return _BIN_CACHE[name]
    except Exception:
        pass
    _BIN_CACHE[name] = name
    return name


def ffprobe_bin() -> str:
    return _resolve_bin("ffprobe")


def ffmpeg_bin() -> str:
    return _resolve_bin("ffmpeg")


def bin_status() -> dict:
    """给 /api/health 与 start.sh 预检用：只读检查，绝不触发下载。"""
    sys_ff = shutil.which("ffmpeg") or ""
    sys_fp = shutil.which("ffprobe") or ""
    present = _static_bins_if_present()
    try:
        import static_ffmpeg  # noqa: F401
        static_pkg = True
    except Exception:
        static_pkg = False
    return {"ffmpeg": bool(sys_ff or present.get("ffmpeg")),
            "ffprobe": bool(sys_fp or present.get("ffprobe")),
            "system_ffmpeg": sys_ff, "system_ffprobe": sys_fp,
            "static_ffmpeg_present": bool(present.get("ffmpeg")),
            "static_ffprobe_present": bool(present.get("ffprobe")),
            "static_pkg_installed": static_pkg}


def is_retryable_error(msg: str) -> bool:
    s = (msg or "").strip()
    return any(s.startswith(p) for p in RETRYABLE_ERRORS)

# 图片字幕归一后的 codec：pgs（可客户端渲染）/ vobsub（仅烧录）
IMAGE_SUBS = {"pgs", "vobsub"}
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "vtt"}
ASS_SUBS = {"ass", "ssa"}


def norm_codec(name: str) -> str:
    s = (name or "").strip().lower()
    mapping = {
        "avc": "h264", "avc1": "h264", "h264": "h264",
        "hevc": "hevc", "h265": "hevc", "hev1": "hevc", "hvc1": "hevc",
        "av1": "av1", "vp9": "vp9", "mpeg4": "mpeg4",
        "mpeg2video": "mpeg2", "vc1": "vc1",
        "aac": "aac", "mp3": "mp3", "ac3": "ac3", "eac3": "eac3",
        "dts": "dts", "truehd": "truehd", "flac": "flac",
        "opus": "opus", "vorbis": "vorbis", "pcm": "pcm",
        # 图片字幕家族归一（PGS 可客户端渲染；VobSub 只能烧录）
        "hdmv_pgs_subtitle": "pgs", "pgssub": "pgs", "pgs": "pgs",
        "vobsub": "vobsub", "dvd_subtitle": "vobsub", "dvdsub": "vobsub",
    }
    for key, val in mapping.items():
        if s == key or s.startswith(key):
            return val
    return s or ""


def _bit_depth(stream: dict) -> int:
    """视频位深：bits_per_raw_sample 优先，缺则从 pix_fmt 后缀推（yuv420p10le → 10）。"""
    try:
        b = int(stream.get("bits_per_raw_sample") or 0)
        if b > 0:
            return b
    except (TypeError, ValueError):
        pass
    pf = str(stream.get("pix_fmt") or "")
    m = re.search(r"p(\d{1,2})(?:le|be)?$", pf)
    if m:
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            pass
    return 8 if pf else 0


def _disposition(stream: dict) -> dict:
    d = stream.get("disposition") or {}
    try:
        default = 1 if int(d.get("default") or 0) else 0
    except (TypeError, ValueError):
        default = 0
    try:
        forced = 1 if int(d.get("forced") or 0) else 0
    except (TypeError, ValueError):
        forced = 0
    return {"default": default, "forced": forced}


def probe(abs_path: str, timeout: int = 30) -> dict:
    """ffprobe 单文件 → MediaInfo（播放决策输入）。0 字节/缺失/失败 → playable=False +
    probe_error（调用方禁用播放）。
    - dv_profile/dv_bl_compat：side_data 的 DOVI 配置记录（0=无/未知；bl_compat=1 为
      HDR10 基底，可当 HDR10 直通）。
    - hdr：color_transfer=smpte2084 → hdr10；arib-std-b67 → hlg；空= SDR。
    - attachments：字体等附件清单（ASS 客户端渲染取内嵌字体用）。
    """
    base: dict = {"container": "", "duration": 0.0, "width": 0, "height": 0,
                  "vcodec": "", "acodec": "", "vbitrate": 0, "abitrate": 0,
                  "video_profile": "", "video_level": 0, "bit_depth": 0, "pix_fmt": "",
                  "color_transfer": "", "color_primaries": "", "hdr": "",
                  "dv_profile": 0, "dv_bl_compat": 0, "hdr10plus": 0,
                  "audio": [], "subs": [], "attachments": [],
                  "playable": False, "probe_error": "", "probe_ver": PROBE_VERSION}
    try:
        size = os.path.getsize(abs_path)
    except OSError as e:
        base["probe_error"] = f"stat failed: {e}"[:300]
        return base
    if size <= 0:
        base["probe_error"] = "empty file (0 bytes)"
        return base
    cmd = [ffprobe_bin(), "-v", "quiet", "-print_format", "json",
           "-show_format", "-show_streams", abs_path]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        base["probe_error"] = "ffprobe not installed"
        return base
    except subprocess.TimeoutExpired:
        base["probe_error"] = "ffprobe timeout"
        return base
    if out.returncode != 0:
        err = (out.stderr or b"").decode("utf-8", errors="replace").strip()
        base["probe_error"] = (err or f"ffprobe exit {out.returncode}")[:300]
        return base
    try:
        data = json.loads((out.stdout or b"").decode("utf-8", errors="replace") or "{}")
    except Exception as e:
        base["probe_error"] = f"ffprobe parse failed: {e}"[:300]
        return base
    fmt = data.get("format") or {}
    base["container"] = str(fmt.get("format_name") or "").split(",")[0].strip().lower()
    try:
        base["duration"] = max(0.0, float(fmt.get("duration") or 0))
    except (TypeError, ValueError):
        base["duration"] = 0.0
    try:
        base["vbitrate"] = int(float(fmt.get("bit_rate") or 0))
    except (TypeError, ValueError):
        base["vbitrate"] = 0
    videos = [s for s in (data.get("streams") or []) if (s.get("codec_type") or "") == "video"]
    audios = [s for s in (data.get("streams") or []) if (s.get("codec_type") or "") == "audio"]
    subs = [s for s in (data.get("streams") or []) if (s.get("codec_type") or "") == "subtitle"]
    # 首视频流定分辨率/编码（封面流 png/mjpeg 跳过）
    for v in videos:
        cn = norm_codec(v.get("codec_name") or "")
        if cn in ("png", "mjpeg", "bmp") and len(videos) > 1:
            continue
        base["vcodec"] = cn
        base["video_profile"] = str(v.get("profile") or "")[:40]
        try:
            base["video_level"] = max(0, int(v.get("level") or 0))
        except (TypeError, ValueError):
            pass
        base["bit_depth"] = _bit_depth(v)
        base["pix_fmt"] = str(v.get("pix_fmt") or "")[:24]
        base["color_transfer"] = str(v.get("color_transfer") or "").lower()[:24]
        base["color_primaries"] = str(v.get("color_primaries") or "").lower()[:24]
        if base["color_transfer"] in ("smpte2084",):
            base["hdr"] = "hdr10"
        elif base["color_transfer"] in ("arib-std-b67",):
            base["hdr"] = "hlg"
        # DV/HDR10+ 元数据在 side_data_list（ffprobe 7.x/8.x 形态；缺字段即 0/无）
        try:
            for sd in (v.get("side_data_list") or []):
                sd = sd or {}
                st = str(sd.get("side_data_type") or "").lower()
                if "dovi" in st or "dolby vision" in st:
                    base["dv_profile"] = max(0, int(sd.get("dv_profile") or 0))
                    base["dv_bl_compat"] = max(
                        0, int(sd.get("dv_bl_signal_compatibility_id") or 0))
                elif "2094-40" in st or "hdr10+" in st:
                    base["hdr10plus"] = 1
        except (TypeError, ValueError):
            pass
        try:
            base["width"] = int(v.get("width") or 0)
            base["height"] = int(v.get("height") or 0)
        except (TypeError, ValueError):
            pass
        try:
            base["vbitrate"] = int(v.get("bit_rate") or base["vbitrate"] or 0)
        except (TypeError, ValueError):
            pass
        break
    for i, a in enumerate(audios):
        cn = norm_codec(a.get("codec_name") or "")
        tags = a.get("tags") or {}
        try:
            br = int(a.get("bit_rate") or 0)
        except (TypeError, ValueError):
            br = 0
        try:
            ch = int(a.get("channels") or 0)
        except (TypeError, ValueError):
            ch = 0
        base["audio"].append({"index": i, "ff_index": int(a.get("index", i)),
                              "codec": cn, "channels": ch, "bitrate": br,
                              "lang": str(tags.get("language") or "").lower()[:8],
                              "title": str(tags.get("title") or "")[:60],
                              **_disposition(a)})
        if i == 0:
            base["acodec"] = cn
            base["abitrate"] = br
    for i, s in enumerate(subs):
        cn = norm_codec(s.get("codec_name") or "")
        tags = s.get("tags") or {}
        base["subs"].append({"index": i, "ff_index": int(s.get("index", i)),
                             "codec": cn, "image": 1 if cn in IMAGE_SUBS else 0,
                             "lang": str(tags.get("language") or "").lower()[:8],
                             "title": str(tags.get("title") or "")[:60],
                             **_disposition(s)})
    for s in (data.get("streams") or []):
        if (s.get("codec_type") or "") != "attachment":
            continue
        tags = s.get("tags") or {}
        base["attachments"].append({"index": int(s.get("index", 0)),
                                    "name": str(tags.get("filename") or "")[:120],
                                    "mime": str(tags.get("mimetype") or "")[:60]})
    if base["duration"] > 0 and base["vcodec"]:
        base["playable"] = True
    else:
        base["probe_error"] = base["probe_error"] or "no video stream"
    return base


# ===== 客户端能力实测：候选码串（前端 caps.js 用 isTypeSupported/decodingInfo 逐条实测）=====

def _avc_codec_string(profile: str, level: int) -> str:
    """H264 → avc1.PPCCLL（PP=profile_idc，CC=约束位取 0，LL=level_idc）。"""
    pid = {"baseline": 0x42, "constrained baseline": 0x42, "main": 0x4D,
           "high": 0x64, "high 10": 0x6E, "high 4:2:2": 0x7A,
           "high 4:4:4 predictive": 0xF4}.get((profile or "").strip().lower(), 0x64)
    lv = level if 0 < level <= 0x3F else 0x28  # 缺省按 level 4.0
    return f"avc1.{pid:02X}00{lv:02X}"


def _hevc_codec_strings(bit_depth: int, level: int) -> list[str]:
    """HEVC → hvc1/hev1 候选（profile 1=Main 8bit，2=Main10）。"""
    pid, comp = (2, 4) if bit_depth > 8 else (1, 6)
    lv = level if 0 < level <= 255 else 120  # ffprobe 的 level 即 general_level_idc
    return [f"hvc1.{pid}.{comp}.L{lv}.B0", f"hev1.{pid}.{comp}.L{lv}.B0"]


def video_codec_strings(vcodec: str, profile: str, level: int, bit_depth: int) -> list[str]:
    """视频 → MSE/HLS CODECS 原始码串候选（不含 MIME）。"""
    if vcodec == "h264":
        return [_avc_codec_string(profile, level)]
    if vcodec == "hevc":
        return _hevc_codec_strings(bit_depth, level)
    if vcodec == "av1":
        lv = level if 0 < level <= 31 else 8
        return [f"av01.0.{lv:02d}M.{'10' if bit_depth > 8 else '08'}"]
    if vcodec == "vp9":
        return [f"vp09.00.10.{'10' if bit_depth > 8 else '08'}"]
    return []


_AUDIO_CAPS = {
    "aac": ["mp4a.40.2", "mp4a.40.5"],
    "mp3": ["mp3", "mp4a.6b"],
    "ac3": ["ac-3"],
    "eac3": ["ec-3"],
    "dts": ["dtsc", "dtsh", "dtsl", "dtse"],
    "truehd": ["mlpa"],
    "flac": ["flac"],
    "opus": ["opus"],
    "vorbis": ["vorbis"],
    "pcm": ["lpcm", "pcm-s16"],
}


def audio_codec_string(acodec: str) -> str:
    """音频 → 首选 MSE CODECS 原始码串（如 mp4a.40.2 / ec-3）；未知返回空。"""
    caps = _AUDIO_CAPS.get(norm_codec(acodec) or "", [])
    return caps[0] if caps else ""


def audio_caps(acodec: str) -> list[str]:
    """音频 → 全量 MIME 候选（前端逐条实测用）。"""
    return [f'audio/mp4; codecs="{c}"' for c in _AUDIO_CAPS.get(acodec or "", [])]


def decorate(info: dict) -> dict:
    """给 media_info 行补客户端实测候选码串（vcaps + 每音轨 caps）。纯派生，不落库。
    前端实测结果以 probes 形式回传，playback.plan 优先用精确结果、回落通用矩阵。"""
    out = dict(info or {})
    if not out.get("playable"):
        out["vcaps"] = []
        return out
    vcodec = norm_codec(str(out.get("vcodec") or ""))
    try:
        bit_depth = int(out.get("bit_depth") or 0)
        level = int(out.get("video_level") or 0)
    except (TypeError, ValueError):
        bit_depth, level = 0, 0
    out["vcaps"] = [f'video/mp4; codecs="{c}"' for c in video_codec_strings(
        vcodec, str(out.get("video_profile") or ""), level, bit_depth)]
    audio = []
    for a in (out.get("audio") or []):
        a2 = dict(a or {})
        a2["caps"] = audio_caps(norm_codec(str(a2.get("codec") or "")))
        audio.append(a2)
    out["audio"] = audio
    return out


def fmt_duration(sec: float) -> str:
    """秒 → H:MM:SS（徽章/续播提示用）。"""
    try:
        s = max(0, int(float(sec or 0)))
    except (TypeError, ValueError):
        s = 0
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
