"""TMDB客户端（Bearer Token v4，支持TMDB_PROXY中转）"""
import httpx

from . import config
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


def _params(**kw) -> dict:
    p = {"language": config.effective_tmdb_language()}
    api_key = config.effective_tmdb_api_key()
    if api_key and not config.effective_tmdb_read_token():
        p["api_key"] = api_key
    p.update({k: v for k, v in kw.items() if v is not None})
    return p


def search_movie(query: str, year: int | None = None) -> list[dict]:
    with _client() as c:
        r = c.get("/search/movie", params=_params(query=query, year=year))
        r.raise_for_status()
        return r.json().get("results", [])


def movie_detail(tmdb_id: int) -> dict:
    with _client() as c:
        r = c.get(f"/movie/{tmdb_id}",
                  params=_params(append_to_response="credits,external_ids"))
        r.raise_for_status()
        return r.json()


def person_detail(tmdb_id: int, language: str | None = None) -> dict:
    """人物详情（简介/生日/出生地）；无中文简介时 TMDB 可能返回空字符串，可传 language='en-US' 兜底。"""
    with _client() as c:
        params = _params()
        if language:
            params["language"] = language
        r = c.get(f"/person/{tmdb_id}", params=params)
        r.raise_for_status()
        return r.json()


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
            with open(dest, "wb") as f:
                f.write(r.content)
        return True
    except Exception as e:
        logger.warning("poster download failed url=%s dest=%s: %s", url, dest, e)
        return False
