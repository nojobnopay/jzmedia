"""DLNA APIs: authenticated discovery/control and scoped, read-only media URLs."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..casting import dlna
from ..casting.service import manager, suggested_url
from .blob import media_response, _stat_or_http

router = APIRouter(prefix="/api/cast", tags=["cast"])


@router.get("/devices")
def devices(request: Request):
    return {"items": manager.device_list(), "base_url": suggested_url(str(request.base_url))}


@router.post("/discover")
def discover(request: Request):
    try:
        items = manager.discover()
    except dlna.CastError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"items": items, "base_url": suggested_url(str(request.base_url)),
            "scope": "server_lan"}


class PlayBody(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    version_id: int = Field(gt=0)
    kind: Literal["movie", "episode"] = "movie"
    position: float = Field(default=0, ge=0, le=7 * 86400, allow_inf_nan=False)
    base_url: str = Field(default="", max_length=512)


@router.post("/sessions")
def play(body: PlayBody, request: Request):
    try:
        return manager.play(body.device_id, body.version_id, body.kind, body.position,
                            body.base_url, str(request.base_url))
    except dlna.CastError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/current")
def current(version_id: int = Query(gt=0), kind: Literal["movie", "episode"] = "movie"):
    return {"session": manager.current(version_id, kind)}


@router.get("/sessions/{sid}")
def status(sid: str):
    return manager.get(sid).public()


class ControlBody(BaseModel):
    command: Literal["play", "pause", "stop", "seek", "volume"]
    value: float | None = Field(default=None, ge=0, le=7 * 86400, allow_inf_nan=False)


@router.post("/sessions/{sid}/control")
def control(sid: str, body: ControlBody):
    try:
        return manager.control(sid, body.command, body.value)
    except dlna.CastError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.api_route("/media/{sid}", methods=["GET", "HEAD"])
def media(sid: str, request: Request):
    session, backend, path = manager.source(sid)
    headers = {"transferMode.dlna.org": "Streaming",
               "contentFeatures.dlna.org": "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"}
    if request.method == "HEAD":
        st = _stat_or_http(backend, path)
        return Response(headers={**headers, "Content-Length": str(st.size), "Accept-Ranges": "bytes"},
                        media_type=session.mime)
    response = media_response(request, backend, path, media_type=session.mime, inline=True)
    response.headers.update(headers)
    return response
