"""playback.cmd（自 app/playback.py 拆分，评审 B9/R11-Q1；经 playback 门面使用）。"""
import os
import json
import subprocess
from .. import library_paths
from ..media import ffmpeg_bin
from ..media import ffprobe_bin
from ..media import norm_codec
from ..log import get_logger
logger = get_logger("playback.cmd")
from .plan import MAX_AUDIO_RENDITIONS, seg_type
__all__ = ['_append_video_encoder', '_keyframe_start', 'actual_media_start', '_seek_args', '_sub_overlay_filter', 'build_cmd', '_is_burn', '_append_sub_input', '_build_cmd_fmp4', '_build_cmd_ts']

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
    if "://" not in abs_path:   # 远程直读输入是内网 URL，不能 abspath（指导 §10/§11）
        abs_path = os.path.abspath(abs_path)  # cwd=会话目录执行，输入须绝对化
    backend = None
    if not plan.get("vcopy"):
        from .. import transcode
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
    """外挂图片字幕（VobSub）第二输入：与主输入同样的输入侧提前量（时间轴对齐）。
    `sub_sidecar_input` 由调用方按 StorageBackend 解析（本地路径或内网 URL）。"""
    rel = str(plan.get("sub_sidecar") or "")
    if not rel:
        return
    if pre > 0:
        cmd += ["-ss", f"{pre:.3f}"]
    src = str(plan.get("sub_sidecar_input") or "")
    if not src:
        src = os.path.abspath(library_paths.abs_path(rel))
    cmd += ["-i", src]


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
        from .. import transcode as _tr
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
        from .. import transcode as _tr
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

