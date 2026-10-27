"""Device-owned catalogue browsing; upstream addresses remain server-only."""

import asyncio
import sqlite3
import time

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ..network.policy import NetworkPolicyError
from ..online import catalog, catalog_models, providers, service
from ..online.models import SourceId
from ..network.guard import NetworkOperation
from ..store.online import OnlineNotFound


HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _error(category, status):
    return JSONResponse({"detail": category, "code": category}, status_code=status, headers=HEADERS)


class OnlineCatalogRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            try:
                response = await handler(request)
                response.headers.update(HEADERS)
                return response
            except RequestValidationError:
                return _error("invalid_online_catalog_request", 422)
            except catalog.CatalogError as error:
                return _error(error.category, error.status)
            except service.PlaybackError as error:
                return _error(error.category, error.status)
            except providers.ProviderError as error:
                return _error(error.category, catalog.provider_error_status(error))
            except (NetworkPolicyError, OnlineNotFound):
                return _error("online_source_unavailable", 503)
            except sqlite3.Error:
                return _error("online_catalog_unavailable", 503)

        return validated


router = APIRouter(prefix="/api/online/catalog", tags=["online-catalog"], route_class=OnlineCatalogRoute)


def _query(request, allowed):
    if any(key not in allowed or len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise catalog.CatalogError("invalid_online_catalog_request", 422)


def _observe(task):
    if not task.cancelled():
        task.exception()


async def _watch(request, operation):
    try:
        while True:
            message = await request.receive()
            if message["type"] == "http.disconnect":
                operation.abort()
                return
            if message["type"] != "http.request" or message.get("body") or message.get("more_body"):
                operation.abort()
                return
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        raise
    except Exception:
        operation.abort()


async def _read(request, function, *args):
    access = await run_in_threadpool(service.capture_access, request)
    operation = NetworkOperation(time.monotonic() + 30)
    watcher = asyncio.create_task(_watch(request, operation), name="online-catalog-disconnect")
    task = asyncio.create_task(run_in_threadpool(function, access, *args, operation=operation),
                               name="online-catalog-read")
    watcher.add_done_callback(_observe)
    task.add_done_callback(_observe)
    completed = False
    try:
        await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if operation.cancelled.is_set():
            raise providers.ProviderError("provider_cancelled")
        result = task.result()
        completed = True
        return result
    finally:
        # A cancelled HTTP waiter never releases its worker's slot early. Its
        # bounded I/O aborts, then _admit's worker finally releases ownership.
        if not completed:
            operation.abort()
        if not watcher.done():
            watcher.cancel()


@router.get("/sources", response_model=catalog_models.CatalogSources)
def sources(request: Request):
    _query(request, set())
    return catalog.sources(service.capture_access(request))


@router.get("/search", response_model=catalog_models.CatalogSearch)
async def search(request: Request, source_id: SourceId,
                 q: str = Query(default="", max_length=100), page: int = Query(default=1, ge=1, le=1000)):
    _query(request, {"source_id", "q", "page"})
    return await _read(request, catalog.search, source_id, q, page)


@router.get("/detail", response_model=catalog_models.CatalogDetail)
async def detail(request: Request, source_id: SourceId,
                 external_id: str = Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")):
    _query(request, {"source_id", "external_id"})
    return await _read(request, catalog.detail, source_id, external_id)
