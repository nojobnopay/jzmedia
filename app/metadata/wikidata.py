"""Wikidata 桥接/元数据（无 key）：标题 → 实体 → 完整 detail（P2 深化）。

- `search()`：wbsearchentities → 候选（带 IMDb/TMDB ID，可继续 TMDB 缓存链路）；
- `detail()`：wbgetentities（实体 + 引用实体一次批量）→ 标准化 detail
  （标题/年份/简介/类型/产地 ISO/导演/演员/Commons 海报），用作无 token 降级源。
- 走 TMDB_PROXY（如配置），独立超时；异常自吞返回空/None，不阻塞扫描链。
"""
import threading
import time
import urllib.parse

import httpx

from .. import config
from ..log import get_logger
from .base import Candidate

logger = get_logger("metadata.wikidata")

API = "https://www.wikidata.org/w/api.php"
_MAX_REFS = 40

# 公开 API 友好限速（批量扫描时不至于打爆第三方）
_MIN_INTERVAL = 0.3
_throttle_lock = threading.Lock()
_last_at = 0.0


def _throttle() -> None:
    global _last_at
    with _throttle_lock:
        now = time.time()
        wait = _MIN_INTERVAL - (now - _last_at)
        if wait > 0:
            time.sleep(min(wait, 2.0))
        _last_at = time.time()


def _get_json(params: dict, timeout: float = 10.0) -> dict:
    _throttle()
    proxy = config.effective_tmdb_proxy() or None
    headers = {"accept": "application/json",
               "user-agent": "jzmedia/0.9 (self-hosted media manager)"}
    with httpx.Client(timeout=timeout, proxy=proxy, headers=headers) as c:
        r = c.get(API, params=params)
        r.raise_for_status()
        return r.json()


def _claims(claims: dict, prop: str) -> list:
    out = []
    for c in claims.get(prop) or []:
        try:
            out.append(c["mainsnak"]["datavalue"]["value"])
        except (KeyError, TypeError):
            continue
    return out


def _claim_value(claims: dict, prop: str) -> str:
    vals = _claims(claims, prop)
    return str(vals[0]) if vals else ""


def _int_or_none(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _label(entity: dict, lang_pref=("zh", "zh-cn", "zh-hant", "en")) -> str:
    labels = (entity or {}).get("labels") or {}
    for lang in lang_pref:
        v = ((labels.get(lang) or {}).get("value") or "").strip()
        if v:
            return v
    for v in labels.values():
        s = str((v or {}).get("value") or "").strip()
        if s:
            return s
    return ""


def _desc(entity: dict) -> str:
    descs = (entity or {}).get("descriptions") or {}
    for lang in ("zh", "zh-cn", "zh-hant", "en"):
        v = ((descs.get(lang) or {}).get("value") or "").strip()
        if v:
            return v
    return ""


def _year_of(value) -> int | None:
    s = str(value or "").lstrip("+")
    return int(s[:4]) if s[:4].isdigit() else None


def _qid_of(value) -> str:
    if isinstance(value, dict):
        v = value.get("id")
        if v:
            return str(v)
        v = ((value.get("numeric-id") is not None)
             and f"Q{value.get('numeric-id')}")
        return str(v or "")
    return ""


def _fetch_entities(ids: list[str]) -> dict:
    if not ids:
        return {}
    try:
        data = _get_json({"action": "wbgetentities", "ids": "|".join(ids[:50]),
                          "props": "claims|labels|descriptions",
                          "languages": "zh|zh-cn|zh-hant|en", "format": "json"})
    except Exception as e:
        logger.debug("wikidata entities failed ids=%s: %s", ids[:5], e)
        return {}
    return (data.get("entities") or {})


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
        label = _label(entity) or hit.get("label") or ""
        out.append(Candidate(title=label, year=year, tmdb_id=tmdb_id,
                             imdb_id=imdb, source="wikidata", source_id=qid,
                             score=20.0 + (10.0 if tmdb_id else 0.0)))
    return out


def detail(source_id) -> dict | None:
    """QID → 标准化 detail（一次主实体 + 一次引用实体批量取标签/ISO）。"""
    qid = str(source_id or "").strip()
    if not qid:
        return None
    entities = _fetch_entities([qid])
    entity = entities.get(qid) or {}
    claims = entity.get("claims") or {}
    if not claims:
        return None
    title = _label(entity)
    if not title:
        return None
    # 引用实体：导演/演员/类型/产地（一次批量取标签与 claims）
    ref_ids: list[str] = []
    for prop in ("P57", "P161", "P136", "P495"):
        for v in _claims(claims, prop):
            q = _qid_of(v)
            if q and q not in ref_ids:
                ref_ids.append(q)
    refs = _fetch_entities(ref_ids[:_MAX_REFS])
    from ..regions import name_to_country

    def _names(prop: str, cap: int | None = None) -> list[str]:
        out: list[str] = []
        for v in _claims(claims, prop):
            q = _qid_of(v)
            name = _label(refs.get(q) or {}) if q else ""
            if name and name not in out:
                out.append(name)
            if cap and len(out) >= cap:
                break
        return out

    countries: list[str] = []
    for v in _claims(claims, "P495"):
        q = _qid_of(v)
        ref = refs.get(q) or {}
        code = ""
        for cv in _claims(ref.get("claims") or {}, "P297"):
            code = str(cv or "").strip().upper()
            if code:
                break
        if not code:
            code = name_to_country(_label(ref))
        if code and code not in countries:
            countries.append(code)
    directors = _names("P57")
    cast = _names("P161", 10)
    genres = _names("P136")
    year = _year_of(_claim_value(claims, "P577")) or _year_of(_claim_value(claims, "P571"))
    image = _claim_value(claims, "P18")
    poster_url = ""
    if image:
        poster_url = ("https://commons.wikimedia.org/wiki/Special:FilePath/"
                      + urllib.parse.quote(str(image).replace(" ", "_"))
                      + "?width=500")
    imdb = _claim_value(claims, "P345")
    tmdb_id = _int_or_none(_claim_value(claims, "P4947"))
    other_labels = []
    for lang in ("en", "zh-cn", "zh-hant"):
        lv = ((entity.get("labels") or {}).get(lang) or {}).get("value")
        if lv and lv != title and lv not in other_labels:
            other_labels.append(str(lv))
    return {
        "title": title,
        "original_title": (other_labels[0] if other_labels else title),
        "year": year,
        "overview": _desc(entity),
        "rating": None,
        "genres": genres,
        "countries": countries,
        "studios": [],
        "aliases": [x for x in other_labels if x != title],
        "people": {"directors": [{"name": n} for n in directors],
                   "cast": [{"name": n, "character": "", "order": i}
                            for i, n in enumerate(cast)]},
        "poster_url": poster_url,
        "backdrop_url": "",
        "imdb_id": imdb,
        "tmdb_id": tmdb_id,
        "episodes": [],
        "url": f"https://www.wikidata.org/wiki/{qid}",
        "_source": "wikidata",
    }


__all__ = ['search', 'detail']
