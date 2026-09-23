"""扫描器门面（评审 B9/R03-Q1：原 1080 行单文件按阶段拆分，调用方零改动）。

store/tmdb 以模块属性暴露（不进 __all__）：tests 与旧调用方可直接 monkeypatch
`scanner.tmdb.xxx`，子模块共享同一模块对象，补丁生效。"""
from .. import store, tmdb  # noqa: F401（monkeypatch 兼容）
from . import (classify, match, nfo_link, parse, persist, scan, tv_match, tv_nfo_link,
               tv_organize, tv_parse, tv_persist)  # noqa: F401
from .classify import *
from .parse import *
from .match import *
from .persist import *
from .nfo_link import *
from .scan import *
from .tv_parse import *
from .tv_match import *
from .tv_persist import *
from .tv_nfo_link import *
from .tv_organize import *

__all__ = []
__all__ += list(classify.__all__)
__all__ += list(parse.__all__)
__all__ += list(match.__all__)
__all__ += list(persist.__all__)
__all__ += list(nfo_link.__all__)
__all__ += list(scan.__all__)
__all__ += list(tv_parse.__all__)
__all__ += list(tv_match.__all__)
__all__ += list(tv_persist.__all__)
__all__ += list(tv_nfo_link.__all__)
__all__ += list(tv_organize.__all__)
