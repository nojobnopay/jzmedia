"""豆瓣建议接口（爬虫类，默认关闭；env `DOUBAN_ENABLED=1` 显式开启）。

- 仅取公开的 subject_suggest JSON，用于**候选提示**（无 TMDB ID，不参与自动绑定）。
- 限速/反爬不可控：失败一律静默降级，绝不阻塞主链。
"""
import os

import httpx

from .. import config
from ..log import get_logger
from .base import Candidate

logger = get_logger("metadata.douban")

URL = "https://movie.douban.com/j/subject_suggest"


def enabled() -> bool:
    return os.getenv("DOUBAN_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _get_json(params: dict, timeout: float = 8.0):
    proxy = config.effective_tmdb_proxy() or None
    headers = {"accept": "application/json",
               "user-agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                              " (KHTML, like Gecko) Chrome/120 Safari/537.36")}
    with httpx.Client(timeout=timeout, proxy=proxy, headers=headers) as c:
        r = c.get(URL, params=params)
        r.raise_for_status()
        return r.json()


def search(title: str, year: int | None = None, kind: str = "movie",
           limit: int = 5) -> list[Candidate]:
    if not enabled():
        logger.debug("douban provider disabled (set DOUBAN_ENABLED=1 to opt in)")
        return []
    term = (title or "").strip()
    if not term:
        return []
    try:
        data = _get_json({"q": term})
    except Exception as e:
        logger.debug("douban suggest failed term=%s: %s", term, e)
        return []
    out: list[Candidate] = []
    for it in (data or [])[:max(1, int(limit))]:
        t = str(it.get("title") or "").strip()
        if not t:
            continue
        y = str(it.get("year") or "").strip()
        try:
            yi = int(y[:4]) if y[:4].isdigit() else None
        except (TypeError, ValueError):
            yi = None
        out.append(Candidate(title=t, year=yi, source="douban",
                             source_id=str(it.get("id") or ""), score=15.0))
    return out
