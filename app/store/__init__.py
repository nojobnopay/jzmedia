"""SQLite 存储层门面（评审 B9/R02-Q3：原 1900 行单文件按域拆分，调用方零改动）。"""
from . import (_base, collections, extras, media_info, movies, persons,
               progress, search, settings, tmdb_cache)  # noqa: F401
from ._base import *
from .settings import *
from .tmdb_cache import *
from .movies import *
from .persons import *
from .extras import *
from .collections import *
from .search import *
from .media_info import *
from .progress import *
from ..db import DB_PATH  # 兼容历史导入（tests 等使用 store.DB_PATH）

__all__ = ['DB_PATH']
__all__ += list(_base.__all__)
__all__ += list(settings.__all__)
__all__ += list(tmdb_cache.__all__)
__all__ += list(movies.__all__)
__all__ += list(persons.__all__)
__all__ += list(extras.__all__)
__all__ += list(collections.__all__)
__all__ += list(search.__all__)
__all__ += list(media_info.__all__)
__all__ += list(progress.__all__)
