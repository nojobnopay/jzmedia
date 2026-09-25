"""TVmaze（无 key，TV 专用）：搜索 → 剧级候选；detail() → 季集明细 + 演职。

- 公开 API（api.tvmaze.com），无需 token；走 TMDB_PROXY（如配置）。
- 只服务 `kind='tv'` 链路；结果 detail 为 `metadata.external` 标准形态，
  可离线落 tv_shows/tv_episodes（tmdb_id 通常为空，imdb/tvdb 桥接保留升级路径）。
"""
import html
import re
import threading
import time

import httpx

from .. import config
from ..log import get_logger
from .base import Candidate

logger = get_logger("metadata.tvmaze")

API = "https://api.tvmaze.com"
_TAG_RE = re.compile(r"<[^>]+>")

# 公开 API 友好限速（批量扫描时不至于打爆第三方）
_MIN_INTERVAL = 0.35
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


def _get(path: str, params: dict | None = None, timeout: float = 12.0):
    _throttle()
    proxy = config.effective_tmdb_proxy() or None
    headers = {"accept": "application/json",
               "user-agent": "jzmedia (self-hosted media manager)"}
    with httpx.Client(base_url=API, timeout=timeout, proxy=proxy,
                      headers=headers) as c:
        r = c.get(path, params=params)
        r.raise_for_status()
        return r.json()


def _strip_html(v: str) -> str:
    return html.unescape(_TAG_RE.sub("", str(v or ""))).strip()


def _year_of(show: dict):
    p = str(show.get("premiered") or "")
    return int(p[:4]) if p[:4].isdigit() else None


def _country_of(show: dict) -> str:
    for key in ("network", "webChannel"):
        node = show.get(key) or {}
        code = str(((node.get("country") or {}).get("code")) or "").strip()
        if code:
            return code.upper()
    return ""


def _image(show: dict) -> str:
    img = show.get("image") or {}
    return str(img.get("original") or img.get("medium") or "")


def _detail_of(show: dict) -> dict:
    """show（可含 _embedded）→ 标准 detail。"""
    embedded = show.get("_embedded") or {}
    people = {"directors": [], "cast": []}
    for c in embedded.get("cast") or []:
        person = c.get("person") or {}
        name = str(person.get("name") or "").strip()
        if not name:
            continue
        people["cast"].append({"name": name,
                               "character": str((c.get("character") or {}).get("name")
                                                or ""),
                               "order": len(people["cast"])})
    episodes = []
    for ep in embedded.get("episodes") or []:
        try:
            season, num = int(ep.get("season") or 0), int(ep.get("number") or 0)
        except (TypeError, ValueError):
            continue
        if num <= 0 and season <= 0:
            continue
        episodes.append({
            "season": season, "episode": num,
            "title": str(ep.get("name") or "").strip(),
            "overview": _strip_html(ep.get("summary") or ""),
            "air_date": str(ep.get("airdate") or "")[:10],
            "runtime": 0,
            "still_url": str((ep.get("image") or {}).get("original") or ""),
        })
    network = (show.get("network") or show.get("webChannel") or {}).get("name") or ""
    ext = show.get("externals") or {}
    try:
        tvdb_id = int(ext.get("thetvdb")) if ext.get("thetvdb") else None
    except (TypeError, ValueError):
        tvdb_id = None
    year = _year_of(show)
    premiered = str(show.get("premiered") or "")[:10]
    return {
        "title": str(show.get("name") or "").strip(),
        "original_title": str(show.get("name") or "").strip(),
        "year": year,
        "overview": _strip_html(show.get("summary") or ""),
        "rating": (show.get("rating") or {}).get("average"),
        "genres": [str(g) for g in (show.get("genres") or []) if g],
        "countries": [c for c in [_country_of(show)] if c],
        "studios": [network] if network else [],
        "status": str(show.get("status") or ""),
        "premiered": premiered,
        "first_air_date": premiered,
        "runtime": int(show.get("averageRuntime") or show.get("runtime") or 0),
        "number_of_episodes": len(episodes),
        "aliases": [],
        "people": people,
        "poster_url": _image(show),
        "backdrop_url": "",
        "imdb_id": str(ext.get("imdb") or ""),
        "tvdb_id": tvdb_id,
        "tmdb_id": None,
        "episodes": episodes,
        "url": str(show.get("officialSite") or show.get("url") or ""),
        "_source": "tvmaze",
    }


def search(title: str, year: int | None = None, kind: str = "tv",
           limit: int = 5) -> list[Candidate]:
    if str(kind or "tv") != "tv":
        return []
    term = (title or "").strip()
    if not term:
        return []
    try:
        rows = _get("/search/shows", {"q": term})
    except Exception as e:
        logger.debug("tvmaze search failed term=%s: %s", term, e)
        return []
    out: list[Candidate] = []
    for hit in (rows or [])[:max(1, int(limit)) * 2]:
        show = (hit or {}).get("show") or {}
        if not show.get("id"):
            continue
        detail = _detail_of(show)
        if year and detail.get("year") and abs(int(detail["year"]) - int(year)) > 1:
            continue
        out.append(Candidate(
            title=detail["title"], original_title=detail["original_title"],
            year=detail.get("year"), tmdb_id=None, imdb_id=detail.get("imdb_id") or "",
            source="tvmaze", source_id=str(show["id"]), score=55.0,
            payload={"detail": detail}))
        if len(out) >= max(1, int(limit)):
            break
    return out


def detail(source_id) -> dict | None:
    """完整剧详情（含 cast/episodes embed）；失败返回 None（调用方用候选 detail 兜底）。"""
    try:
        sid = int(source_id)
    except (TypeError, ValueError):
        return None
    try:
        show = _get(f"/shows/{sid}", {"embed[]": ["cast", "episodes"]})
    except Exception as e:
        logger.debug("tvmaze detail failed id=%s: %s", source_id, e)
        return None
    if not isinstance(show, dict) or not show.get("id"):
        return None
    return _detail_of(show)


__all__ = ['search', 'detail']
