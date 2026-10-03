"""Small, additive APIs for remote-operated television clients."""
from typing import Literal

from fastapi import APIRouter, Query

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
