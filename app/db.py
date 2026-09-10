import os
from .config import settings

DB_PATH = os.path.join(settings.data_dir, "jzmedia.db")
POSTER_DIR = os.path.join(settings.data_dir, "posters")


def ensure_dirs() -> None:
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(POSTER_DIR, exist_ok=True)
    os.makedirs(settings.media_root, exist_ok=True)
