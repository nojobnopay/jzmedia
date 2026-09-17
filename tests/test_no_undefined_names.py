"""拆分回归网（评审 B9 后续）：pyflakes 未定义名守护。

起因：B9 模块拆分后，`routes.py` 内部引用漏 import（`_move_one`/`_rename_or_move`）、
`prewarm.py` 漏 `_write_session_meta`，运行时 500/线程静默失败；既有测试走门面
（`files_router._move_one`）绕过内部引用，未覆盖。此用例把该类问题钉死在 L1。
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_no_undefined_names_in_app():
    r = subprocess.run([sys.executable, "-m", "pyflakes", str(ROOT / "app")],
                       capture_output=True, text=True)
    bad = [ln for ln in (r.stdout or "").splitlines() if ": undefined name" in ln]
    assert not bad, "pyflakes undefined names:\n" + "\n".join(bad)
