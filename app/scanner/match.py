"""scanner.match（自 app/scanner.py 拆分，评审 B9/R03-Q1；对外经 app.scanner 门面使用）。"""
from .. import tmdb
from ..regions import resolve as resolve_region
from .parse import short_candidates
from ..log import get_logger
logger = get_logger("scanner.match")
__all__ = ['pick_match', 'search_with_fallback', 'meta_from_detail', 'extract_credits', 'jobs_from_credits']

def pick_match(results: list[dict], year: int | None) -> tuple[dict | None, bool]:
    """挑结果：优先年份±1 内命中；都超出时退回首个候选，并报告年份未对上
    （评审 B5a-2/R03-D6：此前静默采信错年份结果，不标待确认）。"""
    if not results:
        return None, False
    if year:
        for r in results:
            rd = (r.get("release_date") or "")[:4]
            if rd.isdigit() and abs(int(rd) - year) <= 1:
                return r, False
    m = results[0]
    mismatch = False
    if year:
        rd = (m.get("release_date") or "")[:4]
        mismatch = not (rd.isdigit() and abs(int(rd) - year) <= 1)
    return m, mismatch


def search_with_fallback(title: str, year: int | None) -> tuple[dict | None, str, bool]:
    """依次试短查询，返回(命中, 实际生效的查询词, 年份是否未对上)。"""
    for q in short_candidates(title):
        results = tmdb.search_movie(q, year)
        m, year_mismatch = pick_match(results, year)
        if m:
            return m, q, year_mismatch
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

