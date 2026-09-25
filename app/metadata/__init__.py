"""元数据 Provider 包（E 阶段 / Phase 2）：本地离线索引 + 无 key API + 可选爬虫 + NFO 导入。

门面：`chain.search()`（按库链调度）；`local`/`wikidata`/`tvmaze`/`bangumi`/`douban`
为具体提供方；`external` 负责把外部 detail 落库到 movies/tv_shows。
"""
from . import chain, douban, local, nfo_import, wikidata  # noqa: F401
from .base import Candidate  # noqa: F401

__all__ = ['chain', 'local', 'wikidata', 'douban', 'nfo_import', 'Candidate']
