"""电影路由门面（评审 B9/R05-Q1：原 1030 行单文件拆 common/scope/routes）。"""
import os  # noqa: F401（tests monkeypatch movies_router.os）
from . import common, routes, scope  # noqa: F401
from .common import *
from .scope import *
from .routes import *

__all__ = ['router']
__all__ += list(common.__all__)
__all__ += list(scope.__all__)
__all__ += list(routes.__all__)
