"""routers.fs 门面（评审 R01-Q4：浏览/定性/操作/复制四段拆分）。"""
from . import classify, common, copy, ops, paths, routes
from .classify import *
from .common import *
from .copy import *
from .ops import *
from .paths import *
from .routes import *

__all__ = []
for _m in (classify, common, copy, ops, paths, routes):
    __all__ += list(_m.__all__)
__all__.append('router')
