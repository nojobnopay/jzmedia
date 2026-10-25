"""DLNA APIs: authenticated discovery/control and scoped, read-only media URLs."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..casting import dlna
from ..casting.service import manager, suggested_url
from ..casting.tv_app import manager as tv_manager
from .blob import media_response, _stat_or_http

router = APIRouter(prefix="/api/cast", tags=["cast"])


@router.get("/devices")
def devices(request: Request):
    return {"items": manager.device_list(), "base_url": suggested_url(str(request.base_url))}


class ManualDeviceBody(BaseModel):
    location: str = Field(min_length=1, max_length=512)


@router.post("/devices")
def add_device(body: ManualDeviceBody, request: Request):
    try:
        item = manager.add_device(body.location)
    except dlna.CastError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"item": item, "base_url": suggested_url(str(request.base_url))}


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
    receiver_type: Literal["dlna", "jzmedia_tv"] = "dlna"
    audio: int | None = Field(default=None, ge=0, le=63)
    sub: int | None = Field(default=None, ge=-1, le=1024)


@router.post("/sessions")
def play(body: PlayBody, request: Request):
    if body.receiver_type == "jzmedia_tv":
        return tv_manager.play(body.device_id, body.version_id, body.kind, body.position, body.audio, body.sub)
    try:
        return manager.play(body.device_id, body.version_id, body.kind, body.position,
                            body.base_url, str(request.base_url), body.audio, body.sub)
    except dlna.CastError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/current")
def current(version_id: int = Query(gt=0), kind: Literal["movie", "episode"] = "movie"):
    from ..viewing import current as access_context, visible_item
    if access_context.get() is not None:
        visible_item(kind, version_id)
    sessions = [session for session in (manager.current(version_id, kind), tv_manager.current(version_id, kind)) if session]
    return {"session": max(sessions, key=lambda session: session.get("created_at", 0)) if sessions else None}


@router.get("/sessions/{sid}")
def status(sid: str):
    if sid.startswith("tv_"):
        return tv_manager.get(sid).public()
    return manager.get(sid).public()


class ControlBody(BaseModel):
    command: Literal["play", "pause", "stop", "seek", "volume"]
    value: float | None = Field(default=None, ge=0, le=7 * 86400, allow_inf_nan=False)


@router.post("/sessions/{sid}/control")
def control(sid: str, body: ControlBody):
    if sid.startswith("tv_"):
        return tv_manager.control(sid, body.command, body.value)
    try:
        return manager.control(sid, body.command, body.value)
    except dlna.CastError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.api_route("/media/{sid}", methods=["GET", "HEAD"])
def media(sid: str, request: Request):
    from .. import viewing
    # Receiver URLs are scoped bearer capabilities. Reconstruct their creator's
    # current scope only after checking revocation; no cookie is needed on TV.
    with manager.lock:
        cast_session = manager.sessions.get(sid)
    access_token = None
    if cast_session and cast_session.viewing_owner:
        if not viewing.check_lease(cast_session.viewing_owner, cast_session.viewing_generation):
            raise HTTPException(404, "投屏授权已结束")
        access_token = viewing.current.set(viewing.context_for(viewing.session_by_hash(cast_session.viewing_owner)))
    try:
        return _media_response(sid, request)
    finally:
        if access_token is not None:
            viewing.current.reset(access_token)


def _media_response(sid, request):
    session, backend, path = manager.source(sid)
    converted = 1 if session.prepared_path else 0
    headers = {"transferMode.dlna.org": "Streaming",
               "contentFeatures.dlna.org": f"DLNA.ORG_OP=01;DLNA.ORG_CI={converted};DLNA.ORG_FLAGS=01700000000000000000000000000000"}
    if request.method == "HEAD":
        st = _stat_or_http(backend, path)
        return Response(headers={**headers, "Content-Length": str(st.size), "Accept-Ranges": "bytes"},
                        media_type=session.mime)
    response = media_response(request, backend, path, media_type=session.mime, inline=True)
    response.headers.update(headers)
    return response
