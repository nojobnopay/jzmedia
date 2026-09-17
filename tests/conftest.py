"""pytest 全局夹具：隔离数据/媒体目录，避免测试触碰仓库 data/ 与真实媒体库。

必须在导入任何 app 模块前设置环境变量（app.config 在导入期读取 env）。
"""
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="jzmedia-tests-"))
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["MEDIA_ROOT"] = str(_TMP / "media")
os.environ["TMDB_READ_TOKEN"] = ""
os.environ["TMDB_API_KEY"] = ""
os.environ["JZMEDIA_TOKEN"] = ""   # 测试默认不鉴权（鉴权用例显式开启）
os.environ.pop("TRANSCODER", None)
os.environ.pop("HW_ACCEL", None)

import pytest  # noqa: E402

from app import store  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import ensure_dirs  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    # 与生产 lifespan 相同的前置初始化（R01-Q1 后不再有导入期副作用）
    ensure_dirs()
    store.init_db()
    yield


@pytest.fixture()
def media_root() -> pathlib.Path:
    d = pathlib.Path(settings.media_root)
    d.mkdir(parents=True, exist_ok=True)
    return d
