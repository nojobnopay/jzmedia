"""SQLite 存储层门面（评审 B9/R02-Q3：原 1900 行单文件按域拆分，调用方零改动）。"""
from . import (_base, collections, external, extras, libraries, media_libraries,
               match_index, media_info, items, movies, persons, progress, search,
               settings, similar, tmdb_cache, tv, tv_bindings)  # noqa: F401
from ._base import *
from .libraries import *
from .media_libraries import *
from .match_index import *
from .external import *
from .settings import *
from .tmdb_cache import *
from .tv import *
from .tv_bindings import (
    list_tv_bindings as list_tv_bindings,
    tv_binding_for as tv_binding_for,
    tv_binding_snapshot as tv_binding_snapshot,
    save_tv_binding_preview as save_tv_binding_preview,
    get_tv_binding_plan as get_tv_binding_plan,
    apply_tv_binding_plan as apply_tv_binding_plan,
    undo_tv_binding_plan as undo_tv_binding_plan,
    list_tv_binding_history as list_tv_binding_history,
    repath_tv_bindings as repath_tv_bindings,
    tv_binding_digest as tv_binding_digest,
)
from .movies import *
from .persons import *
from .extras import *
from .collections import *
from .search import *
from .similar import *
from .media_info import *
from .progress import *
from .items import *
from .fs_changes import (record_fs_change as record_fs_change,
                         fs_change_summary as fs_change_summary,
                         clear_fs_changes as clear_fs_changes)
from ..db import DB_PATH  # 兼容历史导入（tests 等使用 store.DB_PATH）

__all__ = ['DB_PATH', 'tv_bindings', 'list_tv_bindings', 'tv_binding_for',
           'tv_binding_snapshot', 'save_tv_binding_preview', 'get_tv_binding_plan',
           'apply_tv_binding_plan', 'undo_tv_binding_plan', 'list_tv_binding_history',
           'repath_tv_bindings', 'tv_binding_digest',
           'record_fs_change', 'fs_change_summary', 'clear_fs_changes']
__all__ += list(_base.__all__)
__all__ += list(libraries.__all__)
__all__ += list(media_libraries.__all__)
__all__ += list(match_index.__all__)
__all__ += list(external.__all__)
__all__ += list(settings.__all__)
__all__ += list(tmdb_cache.__all__)
__all__ += list(movies.__all__)
__all__ += list(persons.__all__)
__all__ += list(extras.__all__)
__all__ += list(collections.__all__)
__all__ += list(search.__all__)
__all__ += list(similar.__all__)
__all__ += list(media_info.__all__)
__all__ += list(progress.__all__)
__all__ += list(tv.__all__)
__all__ += list(items.__all__)
