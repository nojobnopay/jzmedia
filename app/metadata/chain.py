"""Provider 链调度（E 阶段）：按库 `metadata_providers` 顺序尝试，首个有结果即返回。

默认链：local（离线索引）→ tmdb（有凭据时）→ wikidata（无 key 桥接）。
`douban` 需 env `DOUBAN_ENABLED=1` 且显式加入库链才会被调用（默认关）。
失败冷却（§9.1）：连续失败 3 次进入 10 分钟冷却，冷却期内跳过该 provider。
"""
import json

from .. import config, library_paths
from ..log import get_logger
from . import douban, local, state, wikidata
from .base import Candidate

logger = get_logger("metadata.chain")

DEFAULT_CHAIN = ("local", "tmdb", "wikidata")
KNOWN_PROVIDERS = {"local", "tmdb", "wikidata", "douban", "nfo"}


def chain_for(library_id=None) -> list[str]:
    if library_id is not None:
        try:
            lib = library_paths.get_library(library_id)
            raw = str((lib or {}).get("metadata_providers") or "").strip()
            if raw:
                names = json.loads(raw)
                if isinstance(names, list):
                    names = [str(x).strip().lower() for x in names
                             if str(x).strip().lower() in KNOWN_PROVIDERS]
                    if names:
                        return names
        except Exception as e:
            logger.debug("parse metadata_providers failed lib=%s: %s", library_id, e)
    return list(DEFAULT_CHAIN)


def _tmdb(title: str, year, limit: int) -> list[Candidate]:
    if not (config.effective_tmdb_read_token() or config.effective_tmdb_api_key()):
        return []
    from .. import tmdb as tmdb_client
    # 失败不吞：由 search 统一记失败/冷却（返回空列表=正常无结果，不计失败）
    rows = tmdb_client.search_movie(title, year)
    out: list[Candidate] = []
    for r in (rows or [])[:limit]:
        rd = (r.get("release_date") or "")[:4]
        out.append(Candidate(
            title=r.get("title") or "", original_title=r.get("original_title") or "",
            year=int(rd) if rd.isdigit() else None,
            tmdb_id=r.get("id"), source="tmdb", source_id=str(r.get("id") or ""),
            score=50.0))
    return out


# provider 名 → 调用（唯一分发点；链外名字直接跳过）
_SEARCHERS = {
    "local": lambda term, year, kind, limit: local.search(term, year, kind, limit),
    "tmdb": lambda term, year, kind, limit: _tmdb(term, year, min(int(limit), 20)),
    "wikidata": lambda term, year, kind, limit: wikidata.search(term, year, kind, limit),
    "douban": lambda term, year, kind, limit: douban.search(term, year, kind, limit),
}


def search(title: str, year: int | None = None, kind: str = "movie",
           library_id=None, limit: int = 10) -> list[Candidate]:
    term = (title or "").strip()
    if not term:
        return []
    for name in chain_for(library_id):
        fn = _SEARCHERS.get(name)
        if fn is None:
            continue
        if not state.available(name):
            logger.debug("provider %s cooling, skipped term=%s", name, term)
            continue
        try:
            hits = fn(term, year, kind, limit)
        except Exception as e:
            tripped = state.note_fail(name, str(e))
            if tripped:
                logger.warning("provider %s 连续失败进入冷却 %ss term=%s: %s",
                               name, state.COOLDOWN_SEC, term, e)
            else:
                logger.debug("provider %s failed term=%s: %s", name, term, e)
            hits = []
        else:
            # 无异常即视为健康（空结果不算失败，避免误冷却）
            state.note_ok(name)
        if hits:
            return hits
    return []


__all__ = ['search', 'chain_for', 'DEFAULT_CHAIN', 'KNOWN_PROVIDERS']
