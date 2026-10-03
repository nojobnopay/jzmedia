"""Small, additive APIs for remote-operated television clients."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from ..store.tv_actors import search_tv_actors, tv_actor_works
from ..store.tv_search import search_tv_titles

router = APIRouter(prefix="/api/tv-client")


@router.get("/search")
def search(q: str = Query(default="", max_length=80),
           kind: Literal["all", "movie", "show", "collection"] = "all",
           media_library: int | None = Query(default=None, ge=1, le=2 ** 63 - 1),
           limit: int = Query(default=24, ge=1, le=60),
           offset: int = Query(default=0, ge=0, le=2 ** 31 - 1)):
    """Title suggestions/results from the complete selected library, including pinyin."""
    return search_tv_titles(q, kind, media_library, limit, offset)


@router.get("/actors")
def actors(q: str = Query(default="", max_length=80),
           media_library: int | None = Query(default=None, ge=1, le=2 ** 63 - 1),
           limit: int = Query(default=24, ge=1, le=60),
           offset: int = Query(default=0, ge=0, le=2 ** 31 - 1)):
    return search_tv_actors(q, media_library, limit, offset)


@router.get("/actor-works")
def actor_works(actor: str = Query(min_length=1, max_length=512),
                kind: Literal["all", "movie", "show"] = "all",
                media_library: int | None = Query(default=None, ge=1, le=2 ** 63 - 1),
                limit: int = Query(default=24, ge=1, le=60),
                offset: int = Query(default=0, ge=0, le=2 ** 31 - 1)):
    result = tv_actor_works(actor, kind, media_library, limit, offset)
    if result is None:
        raise HTTPException(404, "actor not found in this library")
    return result
