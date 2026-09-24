"""TMDB客户端（Bearer Token v4，支持TMDB_PROXY中转；瞬时错误自动重试，评审 B8/R03-D5）"""
import os
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
    """电影详情。附加 alternative_titles：TMDB 的 zh-CN 记录可能没有本地化 title
    （回退成英文原名）但中文名在别名表里（如 Top Gun: Maverick → 壮志凌云*），
    由 scanner.match.pick_display_title 择中文显示名。"""
    with _client() as c:
        return _get(c, f"/movie/{tmdb_id}",
                    params=_params(append_to_response="credits,external_ids,alternative_titles")).json()


def search_tv(query: str, year: int | None = None) -> list[dict]:
    """剧集搜索（`first_air_date_year` 过滤）。返回 results（含 name/original_name/first_air_date）。"""
    with _client() as c:
        return _get(c, "/search/tv",
                    params=_params(query=query, first_air_date_year=year)).json().get("results", [])


def tv_detail(tmdb_id: int) -> dict:
    """剧集详情。附加 credits/external_ids/alternative_titles/content_ratings：
    - external_ids 提供 imdb_id/tvdb_id（TVDB 排序/`.plexmatch` 用）；
    - alternative_titles（results 形态）供中文显示名与英文原名匹配兜底。"""
    with _client() as c:
        return _get(c, f"/tv/{int(tmdb_id)}",
                    params=_params(append_to_response=(
                        "credits,external_ids,alternative_titles,content_ratings"))).json()


def tv_season(tmdb_id: int, season_number: int) -> dict:
    """季详情（含 episodes[]：name/overview/still_path/air_date/runtime/vote_average/id，
    每集另带 guest_stars[]/crew[]；顶层 credits 为本季常驻阵容）。"""
    with _client() as c:
        return _get(c, f"/tv/{int(tmdb_id)}/season/{int(season_number)}",
                    params=_params(append_to_response="credits")).json()


def tv_aggregate_credits(tmdb_id: int) -> dict:
    """全剧聚合演职员（`/tv/{id}/aggregate_credits`，网站 Series Cast 同源）。

    注意与 `tv_detail(append=credits)` 的区别：后者官方定义仅返回**最新季**
    阵容；本接口返回全剧所有集的聚合：`cast[].roles[] {character, episode_count}`
    + `total_episode_count`（一人多角可表达），crew 带 `jobs[]`。季/集级展示
    另有季 credits 与季详情 `episodes[].guest_stars`，见 tv_season。"""
    with _client() as c:
        return _get(c, f"/tv/{int(tmdb_id)}/aggregate_credits",
                    params=_params()).json()


def tv_alternative_titles(tmdb_id: int) -> list[dict]:
    """剧集别名表（`/tv/{id}/alternative_titles` → results，含 iso_3166_1/title）。"""
    with _client() as c:
        data = _get(c, f"/tv/{int(tmdb_id)}/alternative_titles").json()
        return data.get("results", []) or []


def tv_images(tmdb_id: int, languages: str = "zh,en,null") -> dict:
    """候选图片（/tv/{id}/images，返回 {posters, backdrops, logos}）。"""
    with _client() as c:
        return _get(c, f"/tv/{int(tmdb_id)}/images",
                    params={"include_image_language": languages}).json()


def find_by_external_id(external_id: str, source: str = "tvdb_id") -> list[dict]:
    """外部 id → TMDB 剧集（/find；source=tvdb_id|imdb_id）。返回 tv_results。"""
    with _client() as c:
        return _get(c, f"/find/{external_id}",
                    params=_params(external_source=source)).json().get("tv_results", []) or []


def person_detail(tmdb_id: int, language: str | None = None) -> dict:
    """人物详情（简介/生日/出生地）；无中文简介时 TMDB 可能返回空字符串，可传 language='en-US' 兜底。"""
    with _client() as c:
        params = _params()
        if language:
            params["language"] = language
        return _get(c, f"/person/{tmdb_id}", params=params).json()


def movie_images(tmdb_id: int, languages: str = "zh,en,null") -> dict:
    """候选图片（Plex 式换海报）：/movie/{id}/images，返回 {posters, backdrops, logos}。
    每项含 file_path/width/height/iso_639_1/vote_average；网络/权限错误上抛。"""
    with _client() as c:
        return _get(c, f"/movie/{int(tmdb_id)}/images",
                    params={"include_image_language": languages}).json()


def download_image(image_path: str, dest: str, size: str = "w500") -> bool:
    """任意 TMDB 图片（poster/backdrop/logo）下载到 dest；原子写（评审 B5a-6）。"""
    if not image_path:
        return False
    from . import config as _config
    url = _config.effective_tmdb_image_base().rstrip("/") + "/t/p/" + size + image_path
    proxy = _config.effective_tmdb_proxy() or None
    try:
        # posters 已按功能拆子目录：目标父目录可能尚不存在（如测试隔离目录），先建
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with httpx.Client(timeout=30.0, proxy=proxy) as c:
            r = c.get(url)
            r.raise_for_status()
            atomic_write_bytes(dest, r.content)
        return True
    except Exception as e:
        logger.warning("image download failed url=%s dest=%s: %s", url, dest, e)
        return False


def download_poster(poster_path: str, dest: str, size: str = "w500") -> bool:
    """poster_path如/p1.jpg；图片走TMDB_IMAGE_BASE（可配代理域名）。size如w500/w185。"""
    return download_image(poster_path, dest, size)
