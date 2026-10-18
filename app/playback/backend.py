"""playback.backend（评审 B9/R11-Q1）：当前转码后端能力查询（独立模块便于 monkeypatch，
名称不与 plan 包属性冲突）。"""

from ..log import get_logger

logger = get_logger("playback.backend")

__all__ = ['hw_backend', 'hw_can_tonemap', 'dovi_capabilities']


def dovi_capabilities(refresh: bool = False) -> dict:
    """P5 所需各硬件阶段；与通用 H.264 编码探测分开缓存。"""
    from . import dovi
    return dovi.capabilities(refresh=refresh)

def hw_backend() -> str:
    """当前转码后端名（''=软件；qsv/vaapi/nvenc）。
    P4 起由 `app/transcode.detect()` 冒烟探测（env TRANSCODER/HW_ACCEL 可强制）。"""
    try:
        from .. import transcode
        return transcode.detect().name
    except Exception:
        return ""


def hw_can_tonemap() -> bool:
    """当前后端能否做 HDR→SDR 实时 tonemap：仅 VAAPI（tonemap_vaapi）/ QSV（vpp_qsv）。
    NVENC 未实现（tonemap_cuda 需单独滤镜链）→ 视作不可，走 hdr_no_tonemap 提示外放。"""
    return hw_backend() in ("vaapi", "qsv")
