"""Explicit, authenticated AI requests. No mutation of media records or files."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import store
from ..ai import discovery, discovery_sessions
from ..ai.search import SearchFilters, ShortText, Year
from ..ai.client import AiUnavailable, operation_session
from ..ai.match import suggest_match
from ..ai.search import propose_search, scope_libraries

router = APIRouter(prefix="/api/ai", tags=["ai"])


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=1, max_length=500)
    kind: Literal["movie", "tv"] = "movie"
    media_library_id: int | None = Field(default=None, gt=0)
    library_id: int | None = Field(default=None, gt=0)


class MatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["movie", "tv"] = "movie"
    id: int = Field(gt=0)


@router.post("/search")
def smart_search(body: SearchRequest):
    discovery_sessions.allowed()
    query = body.q.strip()
    if not query:
        raise HTTPException(422, "请输入搜索描述。")
    libraries = scope_libraries(body.kind, body.media_library_id, body.library_id)
    if libraries == [-1]:
        return {"ok": False, "code": "empty_scope", "message": "当前范围没有对应的视频库，请先选择媒体库。"}
    try:
        with operation_session():
            result = propose_search(query, body.kind, libraries)
            discovery_sessions.verify_access()
            return result
    except AiUnavailable as exc:
        return {"ok": False, "code": exc.code, "message": exc.message}


@router.post("/match")
def smart_match(body: MatchRequest):
    row = store.get_show(body.id) if body.kind == "tv" else store.get_movie(body.id)
    if not row:
        raise HTTPException(404, "条目不存在。")
    lib = store.get_library(row["library_id"])
    if not lib or lib.get("kind") != body.kind:
        raise HTTPException(422, "条目所属视频库类型不符。")
    try:
        with operation_session():
            return suggest_match(row, body.kind)
    except AiUnavailable as exc:
        return {"ok": False, "code": exc.code, "message": exc.message}


class ResultsScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["movie", "tv"] = "movie"
    kinds: list[Literal["movie", "tv"]] = Field(default_factory=list, max_length=2)
    media_library_id: int | None = Field(default=None, gt=0)
    library_id: int | None = Field(default=None, gt=0)
    source: Literal["library", "public"] | None = None


class WallFilters(SearchFilters):
    # The ordinary wall allows users to select more facets than a model may generate.
    genre: list[ShortText] = Field(default_factory=list, max_length=512)
    region: list[ShortText] = Field(default_factory=list, max_length=512)
    country: list[ShortText] = Field(default_factory=list, max_length=512)
    year: list[Year] = Field(default_factory=list, max_length=401)
    decade: list[Year] = Field(default_factory=list, max_length=41)
    tag: list[ShortText] = Field(default_factory=list, max_length=512)


class ResultsRequest(ResultsScope):
    q: str = Field(min_length=1, max_length=500)
    session_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{32}$")
    filters: WallFilters | None = None
    reuse_plan: bool = False


class RefreshRequest(ResultsScope):
    session_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{32}$")


def _results(body, request, response, operation):
    discovery_sessions.allowed()
    response.headers["Cache-Control"] = "no-store"
    access = discovery_sessions.owner(request, response, body)
    try:
        with operation_session():
            return operation(body, access)
    except AiUnavailable as exc:
        return {"ok": False, "code": exc.code, "message": exc.message}


@router.post("/search/results")
def search_results(body: ResultsRequest, request: Request, response: Response):
    if not body.q.strip():
        raise HTTPException(422, "请输入想找的作品或要求。")
    return _results(body, request, response, discovery.run)


@router.post("/search/results/refresh")
def refresh_results(body: RefreshRequest, request: Request, response: Response):
    return _results(body, request, response, discovery.refresh)
