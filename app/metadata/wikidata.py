"""Wikidata 桥接（无 key）：标题 → 实体 → IMDb/TMDB ID（P345/P4947）。

- 结果只作候选/ID 桥接，不提供简介/海报；命中后有 tmdb_id 的候选可继续走 TMDB/cache 链路。
- 走 TMDB_PROXY（如配置），独立超时与异常自吞（不阻塞扫描链）。
"""
import httpx

from .. import config
from ..log import get_logger
from .base import Candidate

logger = get_logger("metadata.wikidata")

API = "https://www.wikidata.org/w/api.php"


def _get_json(params: dict, timeout: float = 10.0) -> dict:
    proxy = config.effective_tmdb_proxy() or None
    headers = {"accept": "application/json",
               "user-agent": "jzmedia/0.9 (self-hosted media manager)"}
    with httpx.Client(timeout=timeout, proxy=proxy, headers=headers) as c:
        r = c.get(API, params=params)
        r.raise_for_status()
        return r.json()


def _claim_value(claims: dict, prop: str) -> str:
    for c in claims.get(prop) or []:
        try:
            v = c["mainsnak"]["datavalue"]["value"]
        except (KeyError, TypeError):
            continue
        if v is not None:
            return str(v)
    return ""


def _int_or_none(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def search(title: str, year: int | None = None, kind: str = "movie",
           limit: int = 5) -> list[Candidate]:
    term = (title or "").strip()
    if not term:
        return []
    try:
        limit = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        limit = 5
    try:
        data = _get_json({"action": "wbsearchentities", "search": term,
                          "language": "zh", "uselang": "zh", "format": "json",
                          "limit": limit})
    except Exception as e:
        logger.debug("wikidata search failed term=%s: %s", term, e)
        return []
    out: list[Candidate] = []
    for hit in (data.get("search") or [])[:limit]:
        qid = hit.get("id")
        if not qid:
            continue
        try:
            ent = _get_json({"action": "wbgetentities", "ids": qid,
                             "props": "claims|labels", "languages": "zh|en",
                             "format": "json"})
        except Exception as e:
            logger.debug("wikidata entity failed qid=%s: %s", qid, e)
            continue
        entity = ((ent.get("entities") or {}).get(qid)) or {}
        claims = entity.get("claims") or {}
        imdb = _claim_value(claims, "P345")
        tmdb_id = _int_or_none(_claim_value(claims, "P4947"))
        if not imdb and not tmdb_id:
            continue
        labels = entity.get("labels") or {}
        label = ((labels.get("zh") or labels.get("en") or {}).get("value")
                 or hit.get("label") or "")
        out.append(Candidate(title=label, year=year, tmdb_id=tmdb_id,
                             imdb_id=imdb, source="wikidata", source_id=qid,
                             score=20.0 + (10.0 if tmdb_id else 0.0)))
    return out
