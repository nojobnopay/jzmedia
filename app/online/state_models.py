"""Device and viewing-mode scoped online favourites and progress contracts."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .models import EpisodeId, ItemId, LineId, OnlinePlaybackIdentity, SourceId


MAX_INTEGER = 2**53 - 1
OpaqueId = Annotated[str, StringConstraints(min_length=32, max_length=128,
                                          pattern=r"^[A-Za-z0-9_-]{32,128}$")]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              validate_default=True, hide_input_in_errors=True)


class FavoriteBody(_Contract):
    favorite: bool


class ClaimBody(_Contract):
    session_id: OpaqueId
    expected_revision: int = Field(ge=0, le=MAX_INTEGER)

    @field_validator("session_id")
    @classmethod
    def exact_session_id(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
            raise ValueError("invalid session")
        return value


class ProgressBody(_Contract):
    writer_id: OpaqueId
    writer_revision: int = Field(ge=1, le=MAX_INTEGER)
    seq: int = Field(ge=1, le=MAX_INTEGER)
    position: float = Field(ge=0, allow_inf_nan=False)
    duration: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    paused: bool
    completed: bool = False

    @field_validator("writer_id")
    @classmethod
    def exact_writer_id(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
            raise ValueError("invalid writer")
        return value


class Progress(_Contract):
    identity: OnlinePlaybackIdentity
    position: float
    duration: float | None
    paused: bool
    completed: bool
    updated_at: float
    writer_revision: int


class Claim(_Contract):
    identity: OnlinePlaybackIdentity
    writer_id: OpaqueId
    writer_revision: int
    expires_at: float
    progress: Progress | None
    generation: int
    scope_token: str


class ProgressResult(_Contract):
    progress: Progress
    generation: int
    scope_token: str


class FavoriteResult(_Contract):
    item_id: ItemId
    favorite: bool
    generation: int
    scope_token: str


class DeleteResult(_Contract):
    deleted: int
    generation: int
    scope_token: str


class ItemState(_Contract):
    item_id: ItemId
    favorite: bool
    last_progress: Progress | None
    selected_progress: Progress | None
    available: bool
    reason: str
    generation: int
    scope_token: str


class LibraryItem(_Contract):
    id: ItemId
    source_id: SourceId
    external_id: str
    kind: Literal["movie", "tv"]
    title: str


class LibrarySource(_Contract):
    id: SourceId
    name: str
    provider: Literal["maccms", "open_catalog"]
    enabled: bool


class LibraryEntry(_Contract):
    item: LibraryItem
    source: LibrarySource
    favorite: bool
    progress: Progress | None
    available: bool
    reason: str
    poster_path: str = ""
    episode_label: str = ""


class Library(_Contract):
    items: list[LibraryEntry]
    total: int
    limit: int
    offset: int
    generation: int
    scope_token: str


class StateSelection(_Contract):
    episode_id: EpisodeId | None = None
    line_id: LineId | None = None
