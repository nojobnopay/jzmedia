import os
from .config import settings

DB_PATH = os.path.join(settings.data_dir, "jzmedia.db")
POSTER_DIR = os.path.join(settings.data_dir, "posters")
TRANSCODE_DIR = os.path.join(settings.data_dir, "transcode")


def ensure_dirs() -> None:
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(POSTER_DIR, exist_ok=True)
    os.makedirs(TRANSCODE_DIR, exist_ok=True)
    # ASS 渲染的兜底字体投放目录（用户自行放入 woff2/ttf；不进仓库）
    os.makedirs(os.path.join(settings.data_dir, "fonts"), exist_ok=True)
    os.makedirs(settings.media_root, exist_ok=True)
