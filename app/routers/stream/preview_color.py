"""预览色彩计划；纯函数，不启动子进程或访问片源。

P5 的 IPT 基底必须保留原始像素及逐帧 RPU，交给 libplacebo 共同转换。
不能先用普通 scale / RGB 转换处理，再尝试补 Dolby Vision 元数据。
"""


_UNSUPPORTED_DV = "该 Dolby Vision 片源无可用兼容基底，暂不支持预览"
_CPU_VULKAN = ("-init_hw_device", "vulkan=preview:llvmpipe",
               "-filter_hw_device", "preview")
_RPU_GUARD = "sidedata=mode=select:type=DOVI_METADATA"


def _integer(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        # 无法识别的 DV 配置不能静默落入普通 SDR 路径。
        return -1


def _p5_filter(width, height):
    # libplacebo 自行完成原始帧的 RPU reshape、缩放和 SDR 输出；关闭依赖
    # 前后帧的峰值平滑，使按点采样与关键帧采样使用相同的色彩转换。
    # 软件 Vulkan 的局部对比度恢复可能跨进程产生不同像素；不为预览
    # 添加这层增强，保留原有的 Dolby Vision reshape 与 SDR 色调映射。
    # 保留 Mitchell 缩小，避免 CPU 官方最简示例的 bilinear 影响缩略图清晰度。
    return (f"libplacebo=w={width}:h={height}:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2:apply_dolbyvision=1:colorspace=bt709:"
            "color_primaries=bt709:color_trc=bt709:range=tv:peak_detect=0:contrast_recovery=0:"
            "apply_filmgrain=0:upscaler=none:downscaler=mitchell,"
            f"format=rgb24,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1")


def color_plan(info, width, height):
    """返回 filter / input_args / software / frame_guard，不做能力探测。

    frame_guard 必须置于每条抽帧路径的 showinfo 之前，准确采样也必须使用。
    libplacebo 在缺 RPU 时不会主动拒绝帧，因此仅靠 apply_dolbyvision 不够。
    input_args 是转换设备初始化，不能随硬件解码回退被移除；software 仅要求
    解码器使用软件路径，不代表滤镜可以省略 Vulkan 设备。
    """
    if (type(width) is not int or type(height) is not int
            or width <= 0 or height <= 0 or width % 2 or height % 2):
        raise ValueError("预览画面尺寸必须为正偶数")
    dv = _integer(info.get("dv_profile"))
    compat = _integer(info.get("dv_bl_compat"))
    if dv < 0 or compat < 0 or (dv == 5 and compat != 0):
        raise RuntimeError(_UNSUPPORTED_DV)
    if dv == 5 and compat == 0:
        return {"filter": _p5_filter(width, height), "input_args": _CPU_VULKAN,
                "software": True, "frame_guard": _RPU_GUARD}

    p7_hdr10 = (dv == 7 and compat == 6 and info.get("vcodec") == "hevc"
                and info.get("color_transfer") == "smpte2084"
                and info.get("color_primaries") == "bt2020")
    if dv and compat not in (1, 2, 4) and not p7_hdr10:
        raise RuntimeError(_UNSUPPORTED_DV)
    hdr = info.get("hdr") or ("hdr10" if p7_hdr10 or (dv and compat == 1)
                              else "hlg" if dv and compat == 4 else "")
    color = ""
    if hdr and not (dv and compat == 2):
        # 已有 HDR10 / HLG 基底继续使用原先的软件 tone-map 路径。
        color = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                 "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,")
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
          + color + f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=rgb24")
    return {"filter": vf, "input_args": (), "software": False, "frame_guard": ""}


def smoke_command(ffmpeg):
    """构建软件 Vulkan 的实际 10 bit 转换冒烟命令，交由可取消的 _run 执行。

    使用纯合成输入，不访问媒体库；不含 RPU 守卫，因为本检测仅验证运行时
    设备、10 bit 上传及滤镜输出能力，不能代替 P5 元数据和色彩夹具验收。
    """
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            *_CPU_VULKAN, "-threads", "1", "-filter_threads", "1",
            "-f", "lavfi", "-i", "color=gray:s=64x64:r=1:d=1,format=yuv420p10le,"
            "geq=lum=400:cb=512:cr=512,"
            "setparams=color_primaries=bt2020:color_trc=smpte2084:"
            "colorspace=bt2020nc:range=limited",
            "-vf", _p5_filter(64, 64), "-frames:v", "1", "-threads", "1",
            "-f", "null", "-"]
