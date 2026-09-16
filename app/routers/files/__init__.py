"""文件整理路由门面（评审 B9/R09-Q1：原 800 行单文件拆 planner/executor/paths/routes）。"""
import os  # noqa: F401（tests monkeypatch files_router.os）
import shutil  # noqa: F401（tests monkeypatch files_router.shutil）
from ... import store  # noqa: F401（tests monkeypatch files_router.store）
from . import executor, paths, planner, routes  # noqa: F401
from .paths import *
from .planner import *
from .executor import *
from .routes import *

__all__ = []
__all__ += list(paths.__all__)
__all__ += list(planner.__all__)
__all__ += list(executor.__all__)
__all__ += list(routes.__all__)
