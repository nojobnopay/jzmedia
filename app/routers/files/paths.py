"""routers.files.paths（自 app/routers/files.py 拆分，评审 B9/R09-Q1；经 files 门面使用）。"""
import os
import errno
import shutil
from ...config import settings
from ...editions import _ILLEGAL
from fastapi import HTTPException
from ...log import get_logger
logger = get_logger("files.paths")
__all__ = ['_MAX_ONLY_IDS', '_safe_component', '_check_inside_root', '_only_ids', '_rename_or_move']


_MAX_ONLY_IDS = 5000


def _safe_component(s: str) -> str:
    """文件名安全清洗。非法字符表与 editions 单源（评审 B11/R09-B3）；与
    editions.sanitize_tag 的差异：本函数不限长、不剥首尾点划线（用于路径段）；
    sanitize_tag 面向标签/后缀（限 20、剥边界）。"""
    s = str(s or "").strip()
    s = _ILLEGAL.sub("", s)
    return " ".join(s.split())


def _check_inside_root(rel: str) -> str:
    """归一并约束在 MEDIA_ROOT 内，返回归一相对路径；非法抛 422。
    除字符串边界外还做 realpath 校验（评审 B6/R09-B1）：根内符号链接指向外部时，
    仅 normpath 检查会放行，实际写入/读取会落到根外。"""
    norm = os.path.normpath((rel or "").strip().strip("/"))
    if not norm or norm == "." or norm.startswith("..") or os.path.isabs(rel or ""):
        raise HTTPException(422, f"illegal path: {rel!r}")
    try:
        root_real = os.path.realpath(settings.media_root)
        abs_real = os.path.realpath(os.path.join(settings.media_root, norm))
    except (OSError, ValueError):
        raise HTTPException(422, f"illegal path: {rel!r}")
    if abs_real != root_real and not abs_real.startswith(root_real + os.sep):
        raise HTTPException(422, f"path escapes media root: {rel!r}")
    return norm


def _only_ids(body: dict | None) -> set[int] | None:
    """body.ids → 选择集合（评审 B6/R09-B2）：强转 int；空=全量。
    此前 set(str) 会让字符串 id 静默不匹配，把“只操作选中项”放大成全量操作。"""
    raw = (body or {}).get("ids") or []
    if not raw:
        return None
    if len(raw) > _MAX_ONLY_IDS:
        raise HTTPException(422, f"too many ids (max {_MAX_ONLY_IDS})")
    try:
        return {int(x) for x in raw}
    except (TypeError, ValueError):
        raise HTTPException(422, "ids must be int list")


def _rename_or_move(src: str, dst: str) -> None:
    """同盘 rename；跨盘（EXDEV）退化为 shutil.move（评审 B8/R09-D3）。"""
    try:
        os.rename(src, dst)
    except OSError as e:
        if e.errno == errno.EXDEV:
            shutil.move(src, dst)
        else:
            raise

