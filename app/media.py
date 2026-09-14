"""在线播放：ffprobe 探测 + Plex 式三档决策（Direct Play > remux > transcode）。

浏览器固定 profile（Plex DeviceProfile 的极简版）：H264 + AAC + MP4 + 外挂文字字幕。
- direct：mp4/h264/aac/<=1080p 且无烧录字幕 → 复用 movies.blob FileResponse 零 CPU。
- remux：音视频兼容、仅容器不对（MKV/H264/AAC）→ ffmpeg -c copy 换容器。
- transcode：只转不兼容流（能 copy 则 copy），画质档 原画/1080p/720p。
- 图片字幕（pgs/vobsub/dvdsub）只能烧录 → reasons 含 pgs_needs_burn，首版 UI 置灰不硬转。
"""
import json
import os
import shutil
import subprocess

FFPROBE = shutil.which("ffprobe") or "ffprobe"
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

IMAGE_SUBS = {"hdmv_pgs_subtitle", "pgs", "vobsub", "dvd_subtitle", "dvdsub", "pgssub"}
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "vtt"}

# 万能目标（Plex TranscodingProfile 同理）：H264 + AAC
TARGET_VCODEC = "h264"
TARGET_ACODEC = "aac"


def _norm_codec(name: str) -> str:
    s = (name or "").strip().lower()
    mapping = {
        "avc": "h264", "avc1": "h264", "h264": "h264",
        "hevc": "hevc", "h265": "hevc", "hev1": "hevc", "hvc1": "hevc",
        "av1": "av1", "vp9": "vp9", "mpeg4": "mpeg4",
        "mpeg2video": "mpeg2", "vc1": "vc1",
        "aac": "aac", "mp3": "mp3", "ac3": "ac3", "eac3": "eac3",
        "dts": "dts", "truehd": "truehd", "flac": "flac",
        "opus": "opus", "vorbis": "vorbis", "pcm": "pcm",
    }
    for key, val in mapping.items():
        if s == key or s.startswith(key):
            return val
    return s or ""


def probe(abs_path: str, timeout: int = 30) -> dict:
    """ffprobe 单文件。0 字节/缺失/失败 → playable=False + probe_error（调用方禁用播放）。"""
    base: dict = {"container": "", "duration": 0.0, "width": 0, "height": 0,
                  "vcodec": "", "acodec": "", "vbitrate": 0, "abitrate": 0,
                  "audio": [], "subs": [], "playable": False, "probe_error": ""}
    try:
        size = os.path.getsize(abs_path)
    except OSError as e:
        base["probe_error"] = f"stat failed: {e}"[:300]
        return base
    if size <= 0:
        base["probe_error"] = "empty file (0 bytes)"
        return base
    cmd = [FFPROBE, "-v", "quiet", "-print_format", "json",
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
        cn = _norm_codec(v.get("codec_name") or "")
        if cn in ("png", "mjpeg", "bmp") and len(videos) > 1:
            continue
        base["vcodec"] = cn
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
        cn = _norm_codec(a.get("codec_name") or "")
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
                              "title": str(tags.get("title") or "")[:60]})
        if i == 0:
            base["acodec"] = cn
            base["abitrate"] = br
    for i, s in enumerate(subs):
        cn = _norm_codec(s.get("codec_name") or "")
        tags = s.get("tags") or {}
        base["subs"].append({"index": i, "ff_index": int(s.get("index", i)),
                             "codec": cn, "image": 1 if cn in IMAGE_SUBS else 0,
                             "lang": str(tags.get("language") or "").lower()[:8],
                             "title": str(tags.get("title") or "")[:60]})
    if base["duration"] > 0 and base["vcodec"]:
        base["playable"] = True
    else:
        base["probe_error"] = base["probe_error"] or "no video stream"
    return base


def _video_compatible(vcodec: str, height: int) -> bool:
    return vcodec == TARGET_VCODEC and (height or 0) <= 1080


def _audio_compatible(acodec: str) -> bool:
    return acodec == TARGET_ACODEC


def decide(media: dict, quality: str = "original",
           audio_idx: int = 0, sub_idx: int | None = None) -> dict:
    """三档决策。media 为 probe()/media_info 行。返回 {method, reasons, plan}。
    method: direct | remux | transcode | blocked
    reasons: container/video/audio/pgs_needs_burn/uns playable 等（Jellyfin TranscodeReason 思想）。
    plan: 给 stream.m3u8 用的转码计划（copy 还是重编、目标高度、字幕方式）。"""
    media = media or {}
    if not media.get("playable"):
        return {"method": "blocked", "reasons": ["unplayable"],
                "plan": {"vcopy": False, "acopy": False, "height": 0, "sub": "none"}}
    container = str(media.get("container") or "").lower()
    vcodec = _norm_codec(str(media.get("vcodec") or ""))
    audios = media.get("audio") or []
    subs = media.get("subs") or []
    try:
        ai = max(0, int(audio_idx or 0))
    except (TypeError, ValueError):
        ai = 0
    want_audio = audios[ai] if 0 <= ai < len(audios) else (audios[0] if audios else {})
    acodec = _norm_codec(str((want_audio or {}).get("codec") or media.get("acodec") or ""))
    height = int(media.get("height") or 0)
    # 字幕方式判定
    sub_mode = "none"
    reasons: list[str] = []
    if sub_idx is not None:
        try:
            si = int(sub_idx)
        except (TypeError, ValueError):
            si = -1
        track = subs[si] if 0 <= si < len(subs) else None
        if track is None:
            reasons.append("subtitle_not_found")
        elif int(track.get("image") or 0) or str(track.get("codec") or "") not in TEXT_SUBS \
                and str(track.get("codec") or "") not in ("srt", "ass", "ssa", "mov_text"):
            # 图片字幕只能烧录 → 强制视频转码；首版调用方应置灰提示下载原盘
            reasons.append("pgs_needs_burn")
            sub_mode = "burn"
        else:
            sub_mode = "sidecar"
    v_ok = _video_compatible(vcodec, height)
    a_ok = _audio_compatible(acodec)
    c_ok = container in ("mp4", "mov", "m4v")
    if sub_mode == "burn":
        v_ok = False  # 烧录强制视频重编
    target_height = 0
    q = (quality or "original").strip().lower()
    if q in ("720p", "720"):
        target_height = 720
    elif q in ("1080p", "1080"):
        target_height = 1080
    if height and target_height and height > target_height:
        v_ok = False  # 降档需重编
        reasons.append("resolution_downscale")
    if c_ok and v_ok and a_ok and sub_mode != "burn":
        return {"method": "direct", "reasons": reasons,
                "plan": {"vcopy": True, "acopy": True, "height": 0, "sub": sub_mode,
                         "audio_idx": ai, "sub_idx": sub_idx}}
    if not c_ok and v_ok and a_ok and sub_mode != "burn":
        reasons.append("container_not_supported")
        return {"method": "remux", "reasons": reasons,
                "plan": {"vcopy": True, "acopy": True, "height": 0, "sub": sub_mode,
                         "audio_idx": ai, "sub_idx": sub_idx}}
    if not v_ok and vcodec != TARGET_VCODEC:
        reasons.append("video_codec_not_supported")
    if not a_ok:
        reasons.append("audio_codec_not_supported")
    plan_height = 0
    if target_height:
        plan_height = target_height
    elif height and height > 1080 and vcodec != TARGET_VCODEC:
        plan_height = 1080  # 转码默认封顶 1080p（Plex 万能目标同理），原画档传 quality=source 可解
        if q == "source":
            plan_height = 0
    return {"method": "transcode", "reasons": reasons,
            "plan": {"vcopy": v_ok, "acopy": a_ok, "height": plan_height,
                     "sub": sub_mode, "audio_idx": ai, "sub_idx": sub_idx}}


def build_cmd(abs_path: str, plan: dict, out_m3u8: str,
              start: float = 0, seg_time: int = 6) -> list[str]:
    """按 decide().plan 构造 ffmpeg HLS 命令（stock ffmpeg，无 fork 依赖）。
    HW 加速预留：环境 HW_ACCEL=vaapi/qsv/nvenc 时切换编解码器（V2 实测开启）。"""
    import os as _os
    plan = plan or {}
    vcopy = bool(plan.get("vcopy"))
    acopy = bool(plan.get("acopy"))
    height = int(plan.get("height") or 0)
    try:
        ai = max(0, int(plan.get("audio_idx") or 0))
    except (TypeError, ValueError):
        ai = 0
    hw = (_os.getenv("HW_ACCEL") or "").strip().lower()
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]
    try:
        st = max(0.0, float(start or 0))
    except (TypeError, ValueError):
        st = 0.0
    if st > 0:
        cmd += ["-ss", f"{st:.3f}"]
    cmd += ["-i", abs_path, "-map", "v:0", "-map", f"a:{ai}?"]
    if vcopy:
        cmd += ["-c:v", "copy"]
    else:
        if hw in ("qsv",):
            cmd += ["-c:v", "h264_qsv", "-preset", "veryfast"]
        elif hw in ("nvenc", "nvidia"):
            cmd += ["-c:v", "h264_nvenc", "-preset", "p4"]
        elif hw in ("vaapi",):
            cmd += ["-vaapi_device", "/dev/dri/renderD128",
                    "-vf", f"format=nv12,hwupload{',scale_vaapi=-2:' + str(height) if height else ''}",
                    "-c:v", "h264_vaapi", "-qp", "23"]
            height = 0  # 已在 vaapi 滤镜内缩放
        else:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
        if height:
            cmd += ["-vf", f"scale=-2:{height}"]
    if acopy:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    cmd += ["-f", "hls", "-hls_time", str(seg_time),
            "-hls_list_size", "0", "-hls_segment_type", "mpegts",
            "-master_pl_name", "master.m3u8", out_m3u8]
    return cmd


def fmt_duration(sec: float) -> str:
    """秒 → H:MM:SS（徽章/续播提示用）。"""
    try:
        s = max(0, int(float(sec or 0)))
    except (TypeError, ValueError):
        s = 0
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
