"""Explicit, authenticated AI requests. No mutation of media records or files."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import store
from ..ai.client import AiUnavailable
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
    query = body.q.strip()
    if not query:
        raise HTTPException(422, "请输入搜索描述。")
    libraries = scope_libraries(body.kind, body.media_library_id, body.library_id)
    if libraries == [-1]:
        return {"ok": False, "code": "empty_scope", "message": "当前范围没有对应的视频库，请先选择媒体库。"}
    try:
        return propose_search(query, body.kind, libraries)
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
        return suggest_match(row, body.kind)
    except AiUnavailable as exc:
        return {"ok": False, "code": exc.code, "message": exc.message}
