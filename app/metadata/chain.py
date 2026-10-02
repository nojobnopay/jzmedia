"""Provider 链调度（E 阶段）：按库 `metadata_providers` 顺序尝试，首个有结果即返回。

默认链：local（离线索引）→ tmdb（有凭据时）→ wikidata（无 key 桥接）。
`douban` 需 env `DOUBAN_ENABLED=1` 且显式加入库链才会被调用（默认关）。
失败冷却（§9.1）：连续失败 3 次进入 10 分钟冷却，冷却期内跳过该 provider。
"""
import json

from .. import config, library_paths, store
from ..log import get_logger
from . import bangumi, douban, local, state, tvmaze, wikidata
from .base import Candidate

logger = get_logger("metadata.chain")

DEFAULT_CHAIN = ("local", "tmdb", "wikidata")
KNOWN_PROVIDERS = {"local", "tmdb", "wikidata", "douban", "nfo",
                   "tvmaze", "bgm"}


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


def _tmdb(title: str, year, limit: int, kind: str = "movie") -> list[Candidate]:
    if not (config.effective_tmdb_read_token() or config.effective_tmdb_api_key()):
        return []
    from .. import tmdb as tmdb_client
    # 失败不吞：由 search 统一记失败/冷却（返回空列表=正常无结果，不计失败）
    is_tv = kind == "tv"
    rows = (tmdb_client.search_tv if is_tv else tmdb_client.search_movie)(title, year)
    out: list[Candidate] = []
    for r in (rows or [])[:limit]:
        rd = (r.get("first_air_date" if is_tv else "release_date") or "")[:4]
        out.append(Candidate(
            title=r.get("name" if is_tv else "title") or "",
            original_title=r.get("original_name" if is_tv else "original_title") or "",
            year=int(rd) if rd.isdigit() else None,
            tmdb_id=r.get("id"), source="tmdb", source_id=str(r.get("id") or ""),
            score=50.0))
    return out


# provider 名 → 调用（唯一分发点；链外名字直接跳过）
_SEARCHERS = {
    "local": lambda term, year, kind, limit: local.search(term, year, kind, limit),
    "tmdb": lambda term, year, kind, limit: _tmdb(term, year, min(int(limit), 20), kind),
    "wikidata": lambda term, year, kind, limit: wikidata.search(term, year, kind, limit),
    "douban": lambda term, year, kind, limit: douban.search(term, year, kind, limit),
    "tvmaze": lambda term, year, kind, limit: tvmaze.search(term, year, kind, limit),
    "bgm": lambda term, year, kind, limit: bangumi.search(term, year, kind, limit),
}

# 重详情源：search 只给候选，落库前按需拉完整 detail（无 key 公开 API）
_DETAILERS = {
    "wikidata": wikidata.detail,
    "tvmaze": tvmaze.detail,
    "bgm": bangumi.detail,
}


def candidate_for(source: str, source_id: str, kind: str = "movie") -> Candidate | None:
    """Restore a bindable candidate from trusted detail storage, without a network call.

    A search-index title alone does not prove that a provider can supply metadata.
    Remote-only candidates deliberately have no title: failed detail retrieval must
    not silently confirm the current item's previous title as a new match.
    """
    source, source_id = str(source or "").strip(), str(source_id or "").strip()
    if not source or not source_id or kind not in ("movie", "tv"):
        return None
    if source == "tvmaze" and kind != "tv":
        return None
    cached = store.get_external(source, source_id)
    if cached:
        if cached.get("kind") != kind:
            return None
        payload = dict(cached.get("payload") or {})
        for name in ("title", "original_title", "year", "tmdb_id", "imdb_id", "tvdb_id",
                     "poster_url", "backdrop_url"):
            if not payload.get(name) and cached.get(name):
                payload[name] = cached[name]
        if payload.get("title"):
            return Candidate(source=source, source_id=source_id, title=payload["title"],
                             original_title=payload.get("original_title") or "",
                             year=payload.get("year"), tmdb_id=payload.get("tmdb_id"),
                             imdb_id=payload.get("imdb_id") or "", payload={"detail": payload})
    if source in _DETAILERS:
        return Candidate(source=source, source_id=source_id)
    return None


def search(title: str, year: int | None = None, kind: str = "movie",
           library_id=None, limit: int = 10,
           exclude=()) -> list[Candidate]:
    term = (title or "").strip()
    if not term:
        return []
    skip = {str(x) for x in (exclude or ())}
    for name in chain_for(library_id):
        if name in skip:
            continue
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


def detail_for(cand: Candidate, fetch: bool = True) -> dict:
    """候选 → 标准化 detail：优先重详情源（tvmaze/bgm/wikidata），失败回退 payload。

    `fetch=False`（搜索预览/低置信候选）只读 payload，不触发额外网络请求。"""
    from . import external as _external
    base = _external.detail_from_candidate(cand)
    if not fetch or not cand.source_id:
        return base
    fn = _DETAILERS.get(str(cand.source or ""))
    if fn is None:
        return base
    try:
        d = fn(cand.source_id)
    except Exception as e:
        logger.debug("provider %s detail failed id=%s: %s",
                     cand.source, cand.source_id, e)
        return base
    if not isinstance(d, dict) or not d.get("title"):
        return base
    # 基础候选字段补位（detail 缺失时）
    for k, v in base.items():
        if not d.get(k) and v:
            d[k] = v
    return d


__all__ = ['search', 'detail_for', 'candidate_for', 'chain_for', 'DEFAULT_CHAIN', 'KNOWN_PROVIDERS']
