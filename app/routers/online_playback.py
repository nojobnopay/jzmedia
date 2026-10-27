"""Owned online sessions and opaque resource delivery; no URL input endpoint."""

import asyncio
import threading
import time

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from ..network.client import NetworkRequestError
from ..network.guard import NetworkOperation, NetworkOperationError, operation_context
from ..network.policy import NetworkPolicyError
from ..online import gateway, providers, service
from ..online.models import OnlinePlaybackDescriptor, OnlinePlaybackIdentity
from ..online.sessions import SessionError


HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _error(category, status):
    return JSONResponse({"detail": category, "code": category}, status_code=status, headers=HEADERS)


def _session_error(error):
    if error.category in {"session_not_found", "resource_not_found"}:
        status = 404
    elif error.category in {"session_closed", "session_expired"}:
        status = 410
    elif error.category in {"session_limit", "request_limit", "byte_budget_exhausted", "resource_limit"}:
        status = 429
    else:
        status = 502
    return _error(error.category, status)


class _CreateAttempt:
    """Retain ownership until a descriptor is serialized for a live request.

    Cancelling an HTTP request must not lose the result of its synchronous
    provider worker. The worker checks cancellation after creation, and the
    request can reclaim a result already published by that worker.
    """

    def __init__(self, identity, access):
        self._identity, self._access = identity, access
        self._registry = service.registry
        self._lock = threading.Lock()
        self.cancelled = threading.Event()
        self.operation = NetworkOperation(time.monotonic() + 30, cancelled=self.cancelled)
        self._session = None
        self._committed = False
        self.watcher = None

    def _close(self, session):
        try:
            self._registry.close(session.id, session.owner)
        except SessionError:
            pass

    def run(self):
        try:
            with operation_context(self.operation):
                return self._run()
        finally:
            self.operation.close()

    def _run(self):
        if self.cancelled.is_set():
            raise service.PlaybackError("online_create_cancelled", 410)
        session = service.create(self._identity, self._access)
        with self._lock:
            cancelled = self.cancelled.is_set()
            if not cancelled:
                self._session = session
        if cancelled:
            self._close(session)
            raise service.PlaybackError("online_create_cancelled", 410)
        return session

    def cancel(self):
        with self._lock:
            self.cancelled.set()
            session = None if self._committed else self._session
            self._session = None
        self.operation.abort()
        if session is not None:
            # This new session has not been handed to a client, so it has no
            # active media reads. Cleanup cannot depend on another await after
            # the ASGI request's cancellation scope has already been cancelled.
            self._close(session)

    def commit(self):
        with self._lock:
            if self.cancelled.is_set():
                return False
            self._committed = True
            self._session = None
            return True

    def watch(self, request):
        self.watcher = asyncio.create_task(self._watch_disconnect(request), name="online-create-disconnect")
        self.watcher.add_done_callback(_observe_creation)

    async def _watch_disconnect(self, request):
        try:
            # FastAPI has already consumed and parsed the JSON body. A normal
            # await is essential here: is_disconnected() first cancels its
            # receive scope, which can prevent BaseHTTPMiddleware's receive
            # task group from ever forwarding a queued disconnect.
            while True:
                message = await request.receive()
                if message["type"] == "http.disconnect":
                    self.cancel()
                    return
                if message["type"] != "http.request" or message.get("body") or message.get("more_body"):
                    self.cancel()
                    return
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.cancel()

    def stop_watching(self):
        watcher, self.watcher = self.watcher, None
        if watcher is not None and not watcher.done():
            # Do not await a cancelled watcher and accidentally swallow the
            # request task's own cancellation. Its done callback observes it.
            watcher.cancel()


def _observe_creation(task):
    # A disconnected request intentionally does not await its provider result.
    # Observe both that worker and the short-lived receive watcher.
    if not task.cancelled():
        task.exception()


class OnlinePlaybackRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            delivered = False
            try:
                response = await handler(request)
                attempt = getattr(request.state, "online_creation", None)
                if attempt is not None:
                    # FastAPI has serialized and validated the descriptor by
                    # now. Let the receive watcher finish a queued notification
                    # before releasing pending-create ownership.
                    await asyncio.sleep(0)
                    attempt.stop_watching()
                    if not attempt.commit():
                        return _error("online_create_cancelled", 410)
                    delivered = True
                response.headers.update(HEADERS)
                return response
            except RequestValidationError:
                return _error("invalid_online_playback_request", 422)
            except service.PlaybackError as error:
                return _error(error.category, error.status)
            except providers.ProviderError as error:
                from ..online.catalog import provider_error_status
                return _error(error.category, provider_error_status(error))
            except SessionError as error:
                return _session_error(error)
            except NetworkPolicyError:
                return _error("online_source_unavailable", 503)
            except NetworkRequestError as error:
                return _error(error.category, 502)
            except NetworkOperationError as error:
                timeout = str(error) == "operation_timeout"
                return _error("online_create_timeout" if timeout else "online_create_cancelled", 504 if timeout else 410)
            except gateway.GatewayError as error:
                return gateway.error_response(error)
            finally:
                attempt = getattr(request.state, "online_creation", None)
                if attempt is not None:
                    if not delivered:
                        attempt.cancel()
                    attempt.stop_watching()

        return validated


router = APIRouter(prefix="/api/online", tags=["online-playback"], route_class=OnlinePlaybackRoute)


@router.post("/sessions", response_model=OnlinePlaybackDescriptor, status_code=201)
async def create_session(body: OnlinePlaybackIdentity, request: Request):
    access = await run_in_threadpool(service.capture_access, request)
    attempt = _CreateAttempt(body, access)
    request.state.online_creation = attempt
    attempt.watch(request)
    task = asyncio.create_task(run_in_threadpool(attempt.run), name="online-session-create")
    task.add_done_callback(_observe_creation)
    returned = False
    try:
        # Neither wait() nor cancellation of the request discards the provider
        # result. The worker still receives and reclaims any late session.
        await asyncio.wait({task, attempt.watcher}, return_when=asyncio.FIRST_COMPLETED)
        if attempt.cancelled.is_set():
            raise service.PlaybackError("online_create_cancelled", 410)
        session = task.result()
        if attempt.cancelled.is_set():
            raise service.PlaybackError("online_create_cancelled", 410)
        descriptor = session.descriptor
        returned = True
        return descriptor
    finally:
        if not returned:
            attempt.cancel()


@router.delete("/sessions/{session_id}", status_code=204, response_class=Response)
def delete_session(session_id: str, request: Request):
    service.delete(session_id, service.capture_access(request))
    return Response(status_code=204, headers=HEADERS)


async def _stream(request, session_id, resource_id=None):
    access = await run_in_threadpool(service.capture_access, request)
    session = await run_in_threadpool(service.authorize, session_id, access)
    resource = (await run_in_threadpool(service.registry.resource, session, resource_id)
                if resource_id is not None else session.entry)
    return await gateway.stream_response(request, service.registry, session, resource)


@router.get("/sessions/{session_id}/media", response_class=Response, operation_id="online_media_get")
@router.head("/sessions/{session_id}/media", response_class=Response, operation_id="online_media_head")
async def media(session_id: str, request: Request):
    return await _stream(request, session_id)


@router.get("/sessions/{session_id}/resources/{resource_id}", response_class=Response, operation_id="online_resource_get")
@router.head("/sessions/{session_id}/resources/{resource_id}", response_class=Response, operation_id="online_resource_head")
async def resource(session_id: str, resource_id: str, request: Request):
    return await _stream(request, session_id, resource_id)
