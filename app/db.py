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


_ensuring = False


def ensure_dirs() -> None:
    global _ensuring
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(POSTER_DIR, exist_ok=True)
    os.makedirs(TRANSCODE_DIR, exist_ok=True)
    # ASS 渲染的兜底字体投放目录（用户自行放入 woff2/ttf；不进仓库）
    os.makedirs(os.path.join(settings.data_dir, "fonts"), exist_ok=True)
    os.makedirs(mounts_dir(), exist_ok=True)
    # 多库 v12：本地库根由库表驱动创建（无库时保留旧 MEDIA_ROOT 行为）。
    # 防重入：ensure_roots → 库表查询 → _conn → ensure_dirs 会递归，用标志截断。
    os.makedirs(settings.media_root, exist_ok=True)
    if _ensuring:
        return
    _ensuring = True
    try:
        from . import library_paths
        library_paths.ensure_roots()
    except Exception as e:
        logger.warning("ensure library roots failed: %s", e)
    finally:
        _ensuring = False
