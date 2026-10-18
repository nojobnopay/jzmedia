"""pytest 全局夹具：隔离数据/媒体目录，避免测试触碰仓库 data/ 与真实媒体库。

必须在导入任何 app 模块前设置环境变量（app.config 在导入期读取 env）。
"""
import os
import pathlib
import re
import subprocess
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


@pytest.fixture(scope="session")
def require_dovi_metadata():
    """Gate only real frame-RPU tests on the selected FFmpeg's named enum."""
    def require(ffmpeg, env=None):
        result = subprocess.run([ffmpeg, "-hide_banner", "-h", "filter=sidedata"],
                                env=env, capture_output=True, text=True, check=True, timeout=10)
        help_text = result.stdout + result.stderr
        # A failed, unrelated or truncated help probe is a failure, not proof
        # that the enum is unavailable. Keep real conversion assertions strict.
        assert re.search(r"(?m)^Filter sidedata\s*$", help_text), help_text
        assert "sidedata AVOptions:" in help_text, help_text
        assert re.search(r"(?m)^\s+type\s+<int>\s", help_text), help_text
        assert ("This filter has support for timeline through the 'enable' option."
                in help_text.splitlines()), help_text
        if not re.search(r"(?m)^\s+DOVI_METADATA\s+\d+\s", help_text):
            pytest.skip("selected FFmpeg sidedata filter lacks DOVI_METADATA")
    return require
