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
    """ffprobe 单文件。0 字节/缺失/失败 → playable=False + probe_error（调用方禁用播放）。
    dv_profile：视频流 side_data_list 里的 DOVI 配置记录（0=无/未知；5/7/8 为常见 DV）。"""
    base: dict = {"container": "", "duration": 0.0, "width": 0, "height": 0,
                  "vcodec": "", "acodec": "", "vbitrate": 0, "abitrate": 0,
                  "audio": [], "subs": [], "dv_profile": 0,
                  "playable": False, "probe_error": ""}
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
        cn = _norm_codec(v.get("codec_name") or "")
        if cn in ("png", "mjpeg", "bmp") and len(videos) > 1:
            continue
        base["vcodec"] = cn
        # 杜比视界：side_data_list 里的 DOVI 配置记录（ffprobe 7.x 形态；缺字段即 0）
        try:
            for sd in (v.get("side_data_list") or []):
                st = str((sd or {}).get("side_data_type") or "").lower()
                if "dovi" in st or "dolby vision" in st:
                    base["dv_profile"] = max(0, int((sd or {}).get("dv_profile") or 0))
                    break
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
    # Direct/Remux 零 CPU：只看编码，不管分辨率（4K H264 原样直发，浏览器硬解）；
    # 高度只在显式降档（quality=720p/1080p）时强制转码。
    return vcodec == TARGET_VCODEC


def _audio_compatible(acodec: str) -> bool:
    return acodec == TARGET_ACODEC


def decide(media: dict, quality: str = "original",
           audio_idx: int = 0, sub_idx: int | None = None,
           client: str = "web") -> dict:
    """三档决策。media 为 probe()/media_info 行。返回 {method, reasons, plan}。
    method: direct | remux | transcode | blocked
    reasons: container/video/audio/dovi/pgs_needs_burn 等（Jellyfin TranscodeReason 思想）。
    plan: 给 stream.m3u8 用的转码计划（copy 还是重编、目标高度、字幕方式）。
    client: web=浏览器固定 profile（H264+AAC+MP4，无 DV 解码）；kodi=外部播放器（原盘直通）。"""
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
    # 杜比视界：浏览器无 DV 解码器（Chrome MSE 明确拒绝 DV），Plex 同样不转 DV；
    # web 客户端一律强制视频重编（经 HDR 层转 SDR/HDR），kodi 等外部播放器直通。
    try:
        dv_profile = max(0, int(media.get("dv_profile") or 0))
    except (TypeError, ValueError):
        dv_profile = 0
    if dv_profile > 0 and (client or "web").strip().lower() != "kodi":
        v_ok = False
        reasons.append("dovi_not_supported")
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
    单 variant 播放列表即 out_m3u8（默认名 master.m3u8），分片 seg%05d.ts 落同目录。
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
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error"]
    try:
        st = max(0.0, float(start or 0))
    except (TypeError, ValueError):
        st = 0.0
    # seek 对齐（B4）：
    # - 转码路径：输入 -ss 到目标前 15s + 输出侧 -ss 精确裁 15s → 解码器有完整 GOP，
    #   音视频都在目标点精确起（否则视频顺延到下一关键帧，首段出现 5~8s 单轨空洞）。
    # - 拷贝路径：无法重编码对齐，用 -noaccurate_seek 从目标前一个关键帧整段起，
    #   音视频天然同点（代价：起播点最多早一个 GOP）。
    trim_after = 0.0
    burn_sub = plan.get("sub") == "burn" and plan.get("sub_ff_index") is not None
    if burn_sub:
        vcopy = False  # 烧录必须重编码
    if st > 0:
        if vcopy:
            cmd += ["-ss", f"{st:.3f}", "-noaccurate_seek"]
        else:
            pre = max(0.0, st - 15.0)
            if pre > 0:
                cmd += ["-ss", f"{pre:.3f}"]
            trim_after = min(st, 15.0)
    # 输入侧 genpts：-ss 跳转后缺/乱 PTS 由 demuxer 补齐（放 -i 之前才生效）
    cmd += ["-fflags", "+genpts"]
    cmd += ["-i", abs_path]
    if trim_after > 0:
        cmd += ["-ss", f"{trim_after:.3f}"]
    if burn_sub:
        # 图片字幕烧录：源分辨率下 overlay 再缩放（overlay 不缩放覆盖层），
        # eof_action=pass：字幕流结束后原样放行视频（默认 repeat 会把最后一句字幕钉到片尾）。
        vf = "[0:v][0:s:%d]overlay=eof_action=pass" % int(plan["sub_ff_index"])
        if height:
            vf += ",scale=-2:%d" % height
        cmd += ["-filter_complex", vf + "[vout]", "-map", "[vout]", "-map", f"a:{ai}?"]
        height = 0  # 缩放已在滤镜图内
        hw = "sw"  # 烧录走软件编码（滤镜图与 -vf/vaapi 互斥）
    else:
        cmd += ["-map", "v:0", "-map", f"a:{ai}?"]
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
    seg_pat = _os.path.join(_os.path.dirname(out_m3u8) or ".", "seg%05d.ts")
    cmd += ["-f", "hls", "-hls_time", str(seg_time),
            "-hls_list_size", "0", "-hls_segment_type", "mpegts",
            # 注意：不要加 -hls_playlist_type event！实测 hls.js 会把 EVENT 当 VOD，
            # 只播首屏快照里的分片就停（约 18s 必死）；Plex 也是纯 live 式增长列表，
            # 无 ENDLIST 即刷新、从头起播（开局分片少时 live edge≈0）。
            # 时间戳归一（输出侧 make_zero；输入侧 genpts 已在 -i 之前）：
            # 输入 -ss 后音视频起点常错位数秒，MSE 遇到大跨度错位会黑屏/卡死。
            "-avoid_negative_ts", "make_zero",
            "-hls_segment_filename", seg_pat, out_m3u8]
    return cmd


def score_for_client(media: dict, quality: str = "original",
                     client: str = "web") -> tuple:
    """浏览器选版打分（越小越优，抄 Plex 按客户端选版本）：
    direct(0) < remux(1) < 转码(2+代价) < blocked(9)。
    同档内 direct/remux 取分辨率最高；转码档 vcopy 优先、目标高度越小越省 CPU。"""
    d = decide(media, quality=quality, client=client)
    m = d["method"]
    try:
        h = int(media.get("height") or 0)
    except (TypeError, ValueError):
        h = 0
    if m == "direct":
        return (0, -h)
    if m == "remux":
        return (1, -h)
    if m == "transcode":
        plan = d.get("plan") or {}
        return (2, 0 if plan.get("vcopy") else 1, int(plan.get("height") or 0))
    return (9, 0)


def fmt_duration(sec: float) -> str:
    """秒 → H:MM:SS（徽章/续播提示用）。"""
    try:
        s = max(0, int(float(sec or 0)))
    except (TypeError, ValueError):
        s = 0
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
