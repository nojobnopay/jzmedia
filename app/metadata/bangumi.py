"""Bangumi（bgm.tv，无 key）：中文/动漫向的离线降级元数据源。

- 搜索优先 v0 `POST /v0/search/subjects`（公开、需 UA），失败回退 legacy GET；
- `detail()` 拉 subject + 分集（公开接口，失败自吞返回 None）；
- 只提供文字/海报，不含 TMDB ID（后续可经手动匹配升级）。
"""
import os
import re
import threading
import time
import urllib.parse

import httpx

from .. import config
from ..log import get_logger
from .base import Candidate

logger = get_logger("metadata.bangumi")

API = "https://api.bgm.tv"
_UA = os.getenv("BANGUMI_UA", "").strip() or \
    "jzmedia/0.18 (self-hosted media manager; contact: local)"
_TYPES = [2, 6]      # 2=anime 6=三次元（真人影视）
_MIN_INTERVAL = 1.0  # 公开 API 友好限速（bgm 明确要求低频率）
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


def _client(timeout: float = 12.0) -> httpx.Client:
    return httpx.Client(base_url=API, timeout=timeout,
                        proxy=config.effective_tmdb_proxy() or None,
                        headers={"accept": "application/json",
                                 "user-agent": _UA})


def _year_of(date) -> int | None:
    s = str(date or "")
    return int(s[:4]) if s[:4].isdigit() else None


def _image(images: dict) -> str:
    images = images or {}
    return str(images.get("large") or images.get("common")
               or images.get("medium") or "")


def _aliases_of(name: str, name_cn: str, infobox: list | None) -> list[str]:
    out: list[str] = []
    for x in (name, name_cn):
        x = str(x or "").strip()
        if x and x not in out:
            out.append(x)
    for box in infobox or []:
        if str((box or {}).get("key") or "") == "别名":
            v = str((box or {}).get("value") or "")
            for part in re.split(r"[/、,，\n]", v):
                part = part.strip()
                if part and part not in out:
                    out.append(part)
    return out[:30]


def _infobox(boxes: list | None) -> dict:
    out: dict[str, str] = {}
    for box in boxes or []:
        key = str((box or {}).get("key") or "").strip()
        val = str((box or {}).get("value") or "").strip()
        if key and val:
            out.setdefault(key, val)
    return out


def _people_from_infobox(info: dict) -> dict:
    """info 表 → people（导演/主演；条目多为 `/`、`、` 分隔的姓名串）。"""
    directors: list[dict] = []
    cast: list[dict] = []
    for key in ("导演", "監督", "监督", "总导演"):
        for name in re.split(r"[/、,，]", info.get(key) or ""):
            name = name.strip().lstrip(":").strip()
            if name:
                directors.append({"name": name})
    for key in ("主演", "声优", "配音", "演员"):
        for name in re.split(r"[/、,，]", info.get(key) or ""):
            name = name.strip()
            if name:
                cast.append({"name": name, "character": "", "order": len(cast)})
    return {"directors": directors, "cast": cast[:10]}


def _countries_from_infobox(info: dict) -> list[str]:
    from ..regions import name_to_country
    out: list[str] = []
    for key in ("地区", "国家", "产地", "制片国家/地区"):
        for part in re.split(r"[/、,，]", info.get(key) or ""):
            code = name_to_country(part.strip())
            if code and code not in out:
                out.append(code)
    return out


def _detail_of(subject: dict, info: dict, episodes: list | None = None) -> dict:
    name = str(subject.get("name") or "").strip()
    name_cn = str(subject.get("name_cn") or "").strip()
    title = name_cn or name
    aliases = _aliases_of(name, name_cn, subject.get("infobox"))
    rating = (subject.get("rating") or {}).get("score")
    try:
        rating = float(rating) if rating else None
    except (TypeError, ValueError):
        rating = None
    eps_out = []
    for ep in episodes or []:
        try:
            num = int(ep.get("ep") or ep.get("sort") or 0)
        except (TypeError, ValueError):
            continue
        if num <= 0:
            continue
        eps_out.append({
            "season": 1, "episode": num,
            "title": str(ep.get("name_cn") or ep.get("name") or "").strip(),
            "overview": str(ep.get("desc") or "").strip(),
            "air_date": str(ep.get("airdate") or "")[:10],
            "runtime": 0,
            "still_url": "",
        })
    return {
        "title": title,
        "original_title": name or title,
        "year": _year_of(subject.get("date")),
        "overview": str(subject.get("summary") or "").strip(),
        "rating": rating,
        "genres": [],
        "countries": _countries_from_infobox(info),
        "studios": [],
        "premiered": str(subject.get("date") or "")[:10],
        "runtime": 0,
        "aliases": [a for a in aliases if a != title],
        "people": _people_from_infobox(info),
        "poster_url": _image(subject.get("images") or {}),
        "backdrop_url": "",
        "imdb_id": "",
        "tmdb_id": None,
        "episodes": eps_out,
        "url": f"https://bgm.tv/subject/{subject.get('id')}",
        "_source": "bgm",
    }


def _v0_search(term: str, limit: int) -> list[dict]:
    _throttle()
    with _client() as c:
        r = c.post("/v0/search/subjects",
                   json={"keyword": term, "filter": {"type": _TYPES}})
        r.raise_for_status()
        data = r.json()
    return [x for x in (data.get("data") or []) if x.get("id")]


def _legacy_search(term: str, limit: int) -> list[dict]:
    """v0 不可用时的旧版搜索（type=2/6 各查一次合并）。"""
    out: list[dict] = []
    for t in _TYPES:
        _throttle()
        try:
            with _client() as c:
                r = c.get(f"/search/subject/{urllib.parse.quote(term)}",
                          params={"type": t, "responseGroup": "large",
                                  "max_results": max(1, int(limit))})
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.debug("bangumi legacy search failed type=%s term=%s: %s", t, term, e)
            continue
        for x in (data.get("list") or [])[:limit]:
            x = dict(x)
            x.setdefault("summary", "")
            out.append(x)
    return out


def search(title: str, year: int | None = None, kind: str = "movie",
           limit: int = 5) -> list[Candidate]:
    term = (title or "").strip()
    if not term:
        return []
    rows: list[dict] = []
    for fn in (_v0_search, _legacy_search):
        try:
            rows = fn(term, limit)
        except Exception as e:
            logger.debug("bangumi search failed (%s) term=%s: %s",
                         fn.__name__, term, e)
            rows = []
        if rows:
            break
    out: list[Candidate] = []
    seen: set = set()
    for x in rows:
        sid = x.get("id")
        if not sid or str(sid) in seen:
            continue
        seen.add(str(sid))
        stars = (x.get("rating") or {}).get("score") if isinstance(x.get("rating"), dict) else None
        try:
            stars = float(stars) if stars else None
        except (TypeError, ValueError):
            stars = None
        out.append(Candidate(
            title=str(x.get("name_cn") or x.get("name") or "").strip(),
            original_title=str(x.get("name") or "").strip(),
            year=_year_of(x.get("date") or x.get("air_date")),
            source="bgm", source_id=str(sid), score=52.0,
            payload={"detail": {
                "title": str(x.get("name_cn") or x.get("name") or "").strip(),
                "original_title": str(x.get("name") or "").strip(),
                "year": _year_of(x.get("date") or x.get("air_date")),
                "overview": str(x.get("summary") or "").strip(),
                "rating": stars,
                "genres": [], "countries": [], "studios": [],
                "aliases": _aliases_of(str(x.get("name") or ""),
                                       str(x.get("name_cn") or ""), None),
                "people": {"directors": [], "cast": []},
                "poster_url": _image(x.get("images") or {}),
                "backdrop_url": "", "imdb_id": "", "tmdb_id": None,
                "episodes": [], "_source": "bgm",
            }}))
        if len(out) >= max(1, int(limit)):
            break
    return out


def detail(source_id) -> dict | None:
    """subject 详情 + 分集（公开接口；分集失败则只有剧级信息）。"""
    try:
        sid = int(source_id)
    except (TypeError, ValueError):
        return None
    subject = None
    try:
        _throttle()
        with _client() as c:
            r = c.get(f"/v0/subjects/{sid}")
            r.raise_for_status()
            subject = r.json()
    except Exception as e:
        logger.debug("bangumi detail failed id=%s: %s", source_id, e)
        return None
    if not isinstance(subject, dict) or not subject.get("id"):
        return None
    episodes: list[dict] = []
    try:
        _throttle()
        with _client() as c:
            r = c.get("/v0/episodes", params={"subject_id": sid, "limit": 200})
            r.raise_for_status()
            episodes = [x for x in (r.json().get("data") or []) if isinstance(x, dict)]
    except Exception as e:
        logger.debug("bangumi episodes failed id=%s: %s", source_id, e)
    info = _infobox(subject.get("infobox"))
    d = _detail_of(subject, info, episodes)
    d["title"] = str(subject.get("name_cn") or subject.get("name") or "").strip()
    return d


__all__ = ['search', 'detail']
