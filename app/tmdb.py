"""TMDB客户端（Bearer Token v4，支持TMDB_PROXY中转；瞬时错误自动重试，评审 B8/R03-D5）"""
import time

import httpx

from . import config
from .fsutil import atomic_write_bytes
from .log import get_logger

API_BASE = "https://api.themoviedb.org/3"
logger = get_logger("tmdb")


def _client() -> httpx.Client:
    headers = {"accept": "application/json"}
    read_token = config.effective_tmdb_read_token()
    if read_token:
        headers["Authorization"] = f"Bearer {read_token}"
    proxy = config.effective_tmdb_proxy() or None
    return httpx.Client(base_url=API_BASE, headers=headers, timeout=20.0,
                        proxy=proxy)


_RETRY_STATUS = {429, 500, 502, 503, 504}


def _get(c: httpx.Client, path: str, params: dict | None = None,
         retries: int = 2) -> httpx.Response:
    """GET + 瞬时错误退避重试（429/5xx/网络错误）；4xx 不重试直接抛。"""
    last: Exception | None = None
    for i in range(retries + 1):
        try:
            r = c.get(path, params=params)
            if r.status_code in _RETRY_STATUS:
                last = httpx.HTTPStatusError(
                    f"retryable status {r.status_code}", request=r.request,
                    response=r)
                raise last
            r.raise_for_status()
            return r
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code not in _RETRY_STATUS:
                raise
            if i >= retries:
                raise
        except httpx.TransportError as e:
            last = e
            if i >= retries:
                raise
        time.sleep(0.8 * (i + 1))
    raise last if last else RuntimeError("tmdb request failed")


def _params(**kw) -> dict:
    p = {"language": config.effective_tmdb_language()}
    api_key = config.effective_tmdb_api_key()
    if api_key and not config.effective_tmdb_read_token():
        p["api_key"] = api_key
    p.update({k: v for k, v in kw.items() if v is not None})
    return p


def search_movie(query: str, year: int | None = None) -> list[dict]:
    with _client() as c:
        return _get(c, "/search/movie",
                    params=_params(query=query, year=year)).json().get("results", [])


def movie_detail(tmdb_id: int) -> dict:
    with _client() as c:
        return _get(c, f"/movie/{tmdb_id}",
                    params=_params(append_to_response="credits,external_ids")).json()


def person_detail(tmdb_id: int, language: str | None = None) -> dict:
    """人物详情（简介/生日/出生地）；无中文简介时 TMDB 可能返回空字符串，可传 language='en-US' 兜底。"""
    with _client() as c:
        params = _params()
        if language:
            params["language"] = language
        return _get(c, f"/person/{tmdb_id}", params=params).json()


def download_poster(poster_path: str, dest: str, size: str = "w500") -> bool:
    """poster_path如/p1.jpg；图片走TMDB_IMAGE_BASE（可配代理域名）。size如w500/w185。"""
    if not poster_path:
        return False
    from . import config as _config
    url = _config.effective_tmdb_image_base().rstrip("/") + "/t/p/" + size + poster_path
    proxy = _config.effective_tmdb_proxy() or None
    try:
        with httpx.Client(timeout=30.0, proxy=proxy) as c:
            r = c.get(url)
            r.raise_for_status()
            # 原子写（评审 B5a-6）：中断/半写不会留下损坏 jpg 被当有效缓存
            atomic_write_bytes(dest, r.content)
        return True
    except Exception as e:
        logger.warning("poster download failed url=%s dest=%s: %s", url, dest, e)
        return False
