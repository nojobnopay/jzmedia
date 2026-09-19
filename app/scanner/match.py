"""scanner.match（自 app/scanner.py 拆分，评审 B9/R03-Q1；对外经 app.scanner 门面使用）。"""
import difflib
from .. import tmdb
from ..regions import resolve as resolve_region
from .parse import short_candidates, normalize_title
from ..log import get_logger
logger = get_logger("scanner.match")
__all__ = ['pick_match', 'search_with_fallback', 'title_similar', 'SIM_THRESHOLD',
           'meta_from_detail', 'extract_credits', 'jobs_from_credits']

# 标题相似门（2026-09 错配修复）：TMDB 命中标题必须与文件名标题足够像，
# 否则不自动绑定（年份精确也只是“待确认”），避免“龙珠Z剧场版→世界大战”式错配。
SIM_THRESHOLD = 0.7


def title_similar(a: str, b: str) -> float:
    """两个标题的归一化相似度（0..1）：互为子串直接 1.0，否则 difflib 比率。
    大小写不敏感（normalize_title 不做 casefold，这里补上）。"""
    na = normalize_title(a or "").casefold()
    nb = normalize_title(b or "").casefold()
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _hit_titles(r: dict) -> list[str]:
    return [str(r.get("title") or ""), str(r.get("original_title") or "")]


def _sim(r: dict, query: str) -> float:
    return max((title_similar(query, t) for t in _hit_titles(r)), default=0.0)


def _year_ok(r: dict, year) -> bool:
    if not year:
        return True
    rd = (r.get("release_date") or "")[:4]
    try:
        return rd.isdigit() and abs(int(rd) - int(year)) <= 1
    except (TypeError, ValueError):
        return False


def pick_match(results: list[dict], year: int | None,
               query: str = "") -> tuple[dict | None, bool]:
    """挑结果（严格门，2026-09）：
    1) 相似度 ≥ 阈值且年份 ±1 → 直接采信；
    2) 年份 ±1 但相似度低 → 采信并标记待确认；
    3) 相似度高但年份对不上 → 采信并标记待确认；
    4) 都不满足 → 不绑定（返回 None，落 no_match / 候选待选），
       绝不静默采信 results[0]（错配之源）。

    `query` 为空时退回旧行为（年份优先，否则首个候选），仅供无查询上下文的调用。"""
    if not results:
        return None, False
    if not query:
        if year:
            for r in results:
                if _year_ok(r, year):
                    return r, False
        m = results[0]
        return m, bool(year and not _year_ok(m, year))
    scored = [(r, _sim(r, query)) for r in results]
    for r, s in scored:
        if s >= SIM_THRESHOLD and _year_ok(r, year):
            return r, False
    for r, s in scored:
        if _year_ok(r, year):
            return r, True
    for r, s in scored:
        if s >= SIM_THRESHOLD:
            return r, True
    return None, False


def search_with_fallback(title: str, year: int | None) -> tuple[dict | None, str, bool]:
    """依次试短查询，返回(命中, 实际生效的查询词, 是否需人工确认)。
    命中须过标题相似门（短查询本身即查询词，过短候选命中也会标待确认）。"""
    for q in short_candidates(title):
        results = tmdb.search_movie(q, year)
        m, review = pick_match(results, year, query=q)
        if m:
            if q != title:
                review = True     # 用了短查询：可能过度截断，一律待确认
            return m, q, review
    return None, title, False


def meta_from_detail(detail: dict) -> dict:
    """TMDB详情 → 可写入 tmdb_cache 的字典（产地/类型/语言，不含海报/NFO）。"""
    year = None
    if detail.get("release_date", "")[:4].isdigit():
        year = int(detail["release_date"][:4])
    countries = [c.get("iso_3166_1", "") for c in detail.get("production_countries", [])
                 if c.get("iso_3166_1")]
    primary, region = resolve_region(countries, detail.get("original_language"))
    belongs = detail.get("belongs_to_collection") or {}
    try:
        col_id = int(belongs.get("id")) if belongs.get("id") is not None else None
    except (TypeError, ValueError):
        col_id = None
    return {
        "title": detail.get("title", ""),
        "original_title": detail.get("original_title", ""),
        "year": year,
        "overview": detail.get("overview", ""),
        "tmdb_id": detail["id"],
        "imdb_id": (detail.get("external_ids") or {}).get("imdb_id", ""),
        "tmdb_rating": detail.get("vote_average"),
        "genres": [g["name"] for g in detail.get("genres", []) if g.get("name")],
        "genre_ids": [g["id"] for g in detail.get("genres", []) if g.get("id")],
        "original_language": detail.get("original_language", "") or "",
        "origin_countries": countries,
        "origin_country": primary,
        "region": region,
        "media_type": "movie",
        "collection_tmdb_id": col_id,
        "collection_name": belongs.get("name", "") or "",
        "collection_poster_path": belongs.get("poster_path", "") or "",
        # D6：NFO/本地图片所需补充字段（Plex NFO Agent 可读）
        "premiered": (detail.get("release_date") or "")[:10],
        "tagline": detail.get("tagline", "") or "",
        "runtime": int(detail.get("runtime") or 0),
        "studios": [c["name"] for c in detail.get("production_companies", [])
                    if c.get("name")],
        "backdrop_tmdb_path": detail.get("backdrop_path") or "",
        "logo_tmdb_path": "",
    }


def extract_credits(detail: dict) -> dict:
    """从 movie_detail.credits 提取最小可用集（导演+前10演员），存入 tmdb_cache 供复用。"""
    cast = []
    for i, c in enumerate((detail.get("credits") or {}).get("cast", [])[:10]):
        if not c.get("id"):
            continue
        cast.append({"id": c["id"], "name": c.get("name", ""),
                     "profile_path": c.get("profile_path"),
                     "character": c.get("character", ""), "order": i})
    crew = []
    for d in (detail.get("credits") or {}).get("crew", []):
        if d.get("job") == "Director" and d.get("id"):
            crew.append({"id": d["id"], "name": d.get("name", ""),
                         "profile_path": d.get("profile_path")})
    return {"cast": cast, "crew": crew}


def jobs_from_credits(credits: dict) -> list[tuple]:
    """credits(原始detail或cache) → jobs[(tmdb_id, name, profile_path, role, character, order)]。"""
    credits = credits or {}
    jobs: list[tuple] = []
    for d in credits.get("crew", []):
        if d.get("id"):
            jobs.append((d["id"], d.get("name", ""), d.get("profile_path"),
                         "director", "", 99))
    for c in (credits.get("cast", []) or [])[:10]:
        if not c.get("id"):
            continue
        jobs.append((c["id"], c.get("name", ""), c.get("profile_path"),
                     "actor", c.get("character", ""), c.get("order", 99)))
    return jobs

