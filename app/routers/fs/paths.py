"""routers.fs.paths（自 app/routers/fs.py 拆分，评审 R01-Q4；经 fs 门面使用）。"""
import os

from fastapi import HTTPException

from ...config import settings
from ..files import _check_inside_root
__all__ = ['_resolve_dir', '_check_inside_root']


def _resolve_dir(rel: str | None) -> str:
    """目录相对路径归一：空串=根；其余必须在 ROOT 内且对应目录。"""
    raw = (rel or "").strip().strip("/")
    if not raw:
        return ""
    norm = _check_inside_root(raw)
    abs_p = os.path.join(settings.media_root, norm)
    if not os.path.isdir(abs_p):
        raise HTTPException(404, f"not a directory: {rel!r}")
    return norm


