"""Shared persistent episode downloads, admitted through current viewing access."""

import mimetypes
import os
import sqlite3
import time
from contextlib import closing
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..network.client import NetworkRequestError
from ..network.guard import NetworkOperation, NetworkOperationError, operation_context
from ..network.policy import NetworkPolicyError
from ..online import cache, catalog, providers, service
from ..online.models import EpisodeId, ItemId, LineId, SourceId


HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
CacheId = Annotated[str, StringConstraints(min_length=16, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class TasksBody(_Body):
    source_id: SourceId
    item_id: ItemId
    line_id: LineId
    episode_ids: list[EpisodeId] = Field(min_length=1, max_length=20000)


class ActionBody(_Body):
    action: Literal["pause", "resume", "retry"]


class DeleteBody(_Body):
    ids: list[CacheId] = Field(min_length=1, max_length=20000)


def _error(category, status):
    return JSONResponse({"detail": category, "code": category}, status_code=status, headers=HEADERS)


class CacheRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            try:
                response = await handler(request)
                response.headers.update(HEADERS)
                return response
            except RequestValidationError:
                return _error("invalid_online_cache_request", 422)
            except (cache.CacheError, service.PlaybackError, catalog.CatalogError) as error:
                return _error(error.category, error.status)
            except providers.ProviderError as error:
                return _error(error.category, catalog.provider_error_status(error))
            except NetworkRequestError as error:
                return _error(error.category, 502)
            except NetworkOperationError:
                return _error("online_cache_request_timeout", 504)
            except (sqlite3.Error, NetworkPolicyError):
                return _error("online_cache_unavailable", 503)

        return validated


router = APIRouter(prefix="/api/online/cache", tags=["online-cache"], route_class=CacheRoute)


def _access(request, *, require_enabled=True):
    access = service.capture_access(request)
    service._current_access(access)
    if require_enabled and not service._state()["enabled"]:
        raise service.PlaybackError("online_source_disabled", 503)
    return access


@router.get("/items")
def items(request: Request):
    _access(request, require_enabled=False)
    return {**cache.list_items(), "enabled": service._state()["enabled"]}


@router.get("/items/{item_id}")
def item(item_id: ItemId, request: Request):
    _access(request, require_enabled=False)
    return {**cache.item_detail(item_id), "enabled": service._state()["enabled"]}


@router.get("/lookup")
def lookup(request: Request, source_id: SourceId,
           external_id: str = Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")):
    _access(request, require_enabled=False)
    # A catalogue external ID is source-scoped; title and episode number are
    # deliberately never used to substitute a different source or edition.
    from .. import viewing
    from ..store import _base
    with viewing.unrestricted(), closing(_base._conn()) as connection:
        row = connection.execute("SELECT id FROM online_items WHERE source_id=? AND external_id=? AND kind='tv'",
                                 (source_id, external_id)).fetchone()
    if row is None:
        raise cache.CacheError("online_cache_not_found", 404)
    return {**cache.item_detail(row["id"]), "enabled": service._state()["enabled"]}


@router.post("/tasks", status_code=202)
def tasks(body: TasksBody, request: Request):
    access = _access(request)
    operation = NetworkOperation(time.monotonic() + 30)
    try:
        with operation_context(operation), catalog.resolution_owner(access.owner):
            return cache.enqueue(body.source_id, body.item_id, body.line_id,
                                 body.episode_ids, access=access)
    finally:
        operation.close()


@router.post("/tasks/{task_id}/actions")
def action(task_id: CacheId, body: ActionBody, request: Request):
    _access(request, require_enabled=body.action != "pause")
    return cache.action(task_id, body.action)


@router.post("/entries/delete")
def delete(body: DeleteBody, request: Request):
    _access(request, require_enabled=False)
    return cache.delete(body.ids, before_delete=service.registry.close_cache)


@router.get("/artwork/{item_id}", response_class=Response)
def artwork(item_id: ItemId, request: Request):
    _access(request, require_enabled=False)
    path = cache.artwork_path(item_id)
    if path is None:
        raise cache.CacheError("online_cache_artwork_not_found", 404)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(descriptor, "rb") as source:
            body = source.read(2 * 1024**2 + 1)
        if len(body) > 2 * 1024**2:
            raise OSError()
    except OSError:
        raise cache.CacheError("online_cache_artwork_not_found", 404) from None
    return Response(body, media_type=mimetypes.guess_type(str(path))[0] or "image/jpeg", headers=HEADERS)
