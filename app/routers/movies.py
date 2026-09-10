import os

from fastapi import APIRouter, HTTPException, Query

from .. import scanner, store, tmdb
from ..config import settings
from ..regions import normalize_tags

router = APIRouter(prefix="/api")

FilterList = list[str] | None


@router.get("/search")
def search(q: str = "", limit: int = 500, grouped: bool = True,
           genre: FilterList = Query(default=None),
           region: FilterList = Query(default=None),
           country: FilterList = Query(default=None),
           year: FilterList = Query(default=None),
           decade: FilterList = Query(default=None),
           tag: FilterList = Query(default=None),
           min_rating: float | None = None,
           rating_source: str = "tmdb"):
    return {"q": q, "items": store.search_fts(
        q, limit, grouped, genres=store._split_multi(genre),
        regions=store._split_multi(region), countries=store._split_multi(country),
        years=store._split_ints(year), decades=store._split_ints(decade),
        tags=store._split_multi(tag), min_rating=min_rating,
        rating_source=rating_source)}


@router.get("/movies")
def list_movies(grouped: bool = True, limit: int = 500,
                genre: FilterList = Query(default=None),
                region: FilterList = Query(default=None),
                country: FilterList = Query(default=None),
                year: FilterList = Query(default=None),
                decade: FilterList = Query(default=None),
                tag: FilterList = Query(default=None),
                min_rating: float | None = None,
                rating_source: str = "tmdb"):
    return {"items": store.list_movies(
        grouped, genres=store._split_multi(genre),
        regions=store._split_multi(region), countries=store._split_multi(country),
        years=store._split_ints(year), decades=store._split_ints(decade),
        tags=store._split_multi(tag), limit=max(1, min(limit, 2000)),
        min_rating=min_rating, rating_source=rating_source)}


@router.get("/facets")
def facets(grouped: bool = True):
    """动态分类计数：类型/大区/国家/年/年代/标签，只返回有片的项。"""
    return store.get_facets(grouped=grouped)


@router.get("/movies/{movie_id}")
def get_movie(movie_id: int):
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    return m


@router.patch("/movies/{movie_id}")
def patch_movie(movie_id: int, body: dict):
    if not store.get_movie(movie_id):
        raise HTTPException(404, "movie not found")
    allowed = {"title", "overview_override", "douban_rating", "custom_rating", "tags"}
    data = {k: v for k, v in body.items() if k in allowed}
    for k in ("douban_rating", "custom_rating"):
        if k in data and data[k] is not None:
            try:
                v = float(data[k])
            except (TypeError, ValueError):
                raise HTTPException(422, f"{k} must be 0-10")
            if not 0 <= v <= 10:
                raise HTTPException(422, f"{k} must be 0-10")
            data[k] = v
    if "tags" in data and not isinstance(data["tags"], list):
        raise HTTPException(422, "tags must be a list")
    if "tags" in data and isinstance(data["tags"], list):
        data["tags"] = normalize_tags(data["tags"])
    store.update_movie_meta(movie_id, **data)
    store.resync_fts(movie_id)
    return store.get_movie(movie_id)


@router.post("/scan")
def run_scan():
    return {"results": scanner.scan_all()}


@router.get("/tmdb/search")
def tmdb_search(q: str, year: int | None = None):
    """手动匹配第一步：按关键词查TMDB候选。"""
    out = []
    for r in tmdb.search_movie(q, year)[:10]:
        out.append({"tmdb_id": r.get("id"), "title": r.get("title"),
                    "original_title": r.get("original_title"),
                    "release_date": r.get("release_date"),
                    "vote_average": r.get("vote_average")})
    return {"items": out}


@router.post("/movies/{movie_id}/match")
def manual_match(movie_id: int, body: dict):
    """手动匹配第二步：用TMDB ID强制绑定。"""
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    tmdb_id = body.get("tmdb_id")
    if not tmdb_id:
        raise HTTPException(422, "tmdb_id required")
    try:
        detail = tmdb.movie_detail(int(tmdb_id))
    except Exception as e:
        raise HTTPException(502, f"tmdb fetch failed: {e}")
    abs_path = os.path.join(settings.media_root, m["file_path"])
    out = scanner.apply_tmdb_detail(movie_id, detail, abs_path)
    store.update_movie_meta(movie_id, needs_review=0)
    return {"id": movie_id, **out}
