"""Administrator-triggered candidate inspection and explicit selection."""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..network.models import NetworkRule
from ..online import catalog, discovery, service
from ..online.models import OnlineSource, SourceId
from .online import OnlineAdminRoute, _management_access
from .online_catalog import _read


router = APIRouter(prefix="/api/online/discovery", tags=["online"], route_class=OnlineAdminRoute,
                   dependencies=[Depends(_management_access)])


class DiscoveryCandidate(BaseModel):
    id: str
    name: str
    origin: str
    api_base_url: str
    source_id: SourceId | None
    enabled: bool


class DiscoveryList(BaseModel):
    items: list[DiscoveryCandidate]


class DiscoveryRefresh(DiscoveryList):
    directory_status: Literal["updated", "cached", "fallback"]


class DiscoveryProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DirectoryRequest(DiscoveryProbe):
    directory_route: NetworkRule | None = None


class DiscoveryEnable(DiscoveryProbe):
    probe_token: str | None = Field(default=None, min_length=16, max_length=64)
    poster_hosts: list[str] = Field(default_factory=list, max_length=128)
    apply_route: bool = False


class ProbeRoute(NetworkRule):
    mode: Literal["direct", "proxy"]

    @model_validator(mode="after")
    def direct_without_profile(self):
        if self.mode == "direct" and self.profile_id is not None:
            raise ValueError("direct probe uses the built-in direct route")
        return self


class DiscoverySample(DiscoveryProbe):
    probe_token: str | None = Field(default=None, min_length=16, max_length=64)
    hosts: list[str] = Field(default_factory=list, max_length=128)
    extended: bool = False
    probe_route: ProbeRoute | None = None


class DiscoveryAttempt(BaseModel):
    stage: Literal["entry", "manifest", "media"]
    reason: str


class DiscoveryResult(BaseModel):
    id: str
    status: Literal["measured", "catalog_only", "empty", "failed"]
    reason: str
    attempts: list[DiscoveryAttempt] = Field(default_factory=list, max_length=3)
    tested_route: ProbeRoute | None = None
    catalog_ms: int | None
    media_ms: int | None
    sample_bytes: int
    sample_kbps: float | None
    realtime_ratio: float | None
    allowed_hosts: list[str]
    missing_hosts: list[str]
    poster_hosts: list[str]
    probe_token: str | None
    checked_at: int


@router.get("", response_model=DiscoveryList)
def candidates():
    return discovery.list_candidates()


@router.post("/refresh", response_model=DiscoveryRefresh)
async def refresh(request: Request, body: DirectoryRequest):
    return await _read(request, discovery.refresh, body.directory_route, deadline_seconds=7)


@router.post("/{candidate_id}/probe", response_model=DiscoveryResult)
async def probe(request: Request, candidate_id: str, body: DiscoverySample):
    try:
        route = body.probe_route.model_dump(mode="json") if body.probe_route is not None else None
        return await _read(request, discovery.probe, candidate_id, body.probe_token, body.hosts, body.extended, route,
                           deadline_seconds=20 if body.extended else 12)
    except catalog.CatalogError as error:
        if error.category != "provider_catalog_busy":
            raise
        return JSONResponse({"detail": error.category, "code": error.category}, status_code=429,
                            headers={"Retry-After": "1", "Cache-Control": "no-store"})


@router.post("/{candidate_id}/enable", response_model=OnlineSource)
def enable(request: Request, candidate_id: str, body: DiscoveryEnable):
    return discovery.enable(service.capture_access(request), candidate_id, body.probe_token, body.poster_hosts, body.apply_route)
