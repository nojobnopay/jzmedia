import os
from .config import settings
from .log import get_logger

logger = get_logger("db")

DB_PATH = os.path.join(settings.data_dir, "jzmedia.db")
POSTER_DIR = os.path.join(settings.data_dir, "posters")
TRANSCODE_DIR = os.path.join(settings.data_dir, "transcode")


def mounts_dir() -> str:
    """远程库挂载根目录（data_dir/mounts）。按调用时读取，测试可 monkeypatch。"""
    return os.path.join(settings.data_dir, "mounts")


def mount_point(library_id) -> str:
    """远程库固定挂载点：data_dir/mounts/lib_<id>（store 派生与 mounts 挂载唯一公式）。"""
    return os.path.join(mounts_dir(), f"lib_{int(library_id)}")


def ensure_dirs() -> None:
    """Create application-owned directories; a registered media root may be offline."""
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(POSTER_DIR, exist_ok=True)
    # posters 按功能拆子目录（app/posters.py 单一来源；lazy-import 防导入期循环）
    try:
        from .posters import SUBDIRS as _POSTER_SUBDIRS
        for _sub in _POSTER_SUBDIRS:
            os.makedirs(os.path.join(POSTER_DIR, _sub), exist_ok=True)
    except Exception as e:
        logger.warning("ensure poster subdirs failed: %s", e)
    os.makedirs(TRANSCODE_DIR, exist_ok=True)
    # ASS 渲染的兜底字体投放目录（用户自行放入 woff2/ttf；不进仓库）
    os.makedirs(os.path.join(settings.data_dir, "fonts"), exist_ok=True)
    os.makedirs(mounts_dir(), exist_ok=True)
