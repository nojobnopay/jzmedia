"""Foreground TV App receiver protocol; registration secrets never enter URLs."""
from typing import Literal

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from ..casting.tv_app import manager

router = APIRouter(prefix="/api/cast/apps", tags=["cast"])


class RegisterBody(BaseModel):
    device_id: str = Field(min_length=16, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(default="jzmedia-tv", max_length=80)
    protocol_version: Literal[1] = 1


class PollBody(BaseModel):
    foreground: bool = True
    session_id: str | None = Field(default=None, max_length=80)
    state: Literal["IDLE", "LOADING", "PLAYING", "PAUSED_PLAYBACK", "STOPPED", "ERROR"] = "IDLE"
    position: float | None = Field(default=None, ge=0, le=7 * 86400, allow_inf_nan=False)
    duration: float | None = Field(default=None, ge=0, le=7 * 86400, allow_inf_nan=False)
    error: str = Field(default="", max_length=200)


class AckBody(BaseModel):
    command_id: str = Field(min_length=1, max_length=80)
    accepted: bool
    error: str = Field(default="", max_length=200)


@router.get("")
def devices():
    return {"items": manager.device_list(), "protocol_version": 1}


@router.post("/register")
def register(body: RegisterBody, request: Request):
    return manager.register(body.device_id, body.name, request.client.host if request.client else "")


@router.post("/{receiver_id}/poll")
def poll(receiver_id: str, body: PollBody, x_receiver_token: str = Header(default="", max_length=128)):
    return manager.poll(receiver_id, x_receiver_token, body)


@router.post("/{receiver_id}/ack")
def ack(receiver_id: str, body: AckBody, x_receiver_token: str = Header(default="", max_length=128)):
    return manager.ack(receiver_id, x_receiver_token, body.command_id, body.accepted, body.error)


@router.delete("/{receiver_id}")
def unregister(receiver_id: str, x_receiver_token: str = Header(default="", max_length=128)):
    return manager.unregister(receiver_id, x_receiver_token)
