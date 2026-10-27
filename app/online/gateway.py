"""Bounded byte passthrough with one worker owning each upstream lifecycle.

The upstream context manager, private-log ContextVars and socket I/O all stay
on one worker. A two-chunk queue applies downstream backpressure; registry
leases limit the number of workers and remain held until both sides finish.
"""

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextvars import copy_context
from dataclasses import dataclass
import re
import threading

from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..network.client import NetworkRequestError
from ..network.guard import NetworkOperation, NetworkOperationError
from . import health
from .hls import ManifestError, rewrite_manifest
from .sessions import CHUNK_BYTES, SessionError


_RANGE = re.compile(r"bytes=([0-9]{0,19})-([0-9]{0,19})\Z")
_CONTENT_RANGE = re.compile(r"bytes ([0-9]{1,19})-([0-9]{1,19})/([0-9]{1,19})\Z")
_UNSATISFIED = re.compile(r"bytes \*/([0-9]{1,19})\Z")
_LENGTH = re.compile(r"[0-9]{1,19}\Z")
_SAFE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_MANIFEST_TYPES = {"application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl",
                   "audio/x-mpegurl", "text/plain", "application/octet-stream"}
_SEGMENT_TYPES = {"video/mp2t", "video/mp4", "video/iso.segment", "audio/mp4", "audio/aac", "audio/mpeg",
                  "audio/ac3", "audio/eac3", "text/vtt", "application/octet-stream"}
_executor = None
_executor_lock = threading.Lock()
_accepting = True


class GatewayError(RuntimeError):
    def __init__(self, category, status=502, headers=None):
        self.category, self.status = category, status
        self.headers = headers or {}
        super().__init__(category)


def _failure(error):
    if isinstance(error, GatewayError):
        return error
    if isinstance(error, (SessionError, ManifestError, NetworkRequestError, NetworkOperationError)):
        category = str(error)
        if category in {"session_not_found", "resource_not_found", "session_closed", "session_expired"}:
            status = 404
        elif category in {"request_limit", "session_limit", "byte_budget_exhausted", "resource_limit"}:
            status = 429
        elif category in {"operation_timeout", "request_timeout", "network_timeout"}:
            status = 504
        elif category in {"request_cancelled", "operation_cancelled"}:
            status = 410
        elif category == "response_byte_limit":
            status = 413
        else:
            status = 502
        return GatewayError(category, status)
    return GatewayError("online_gateway_failed")


def error_response(error):
    failure = _failure(error)
    return JSONResponse({"detail": "在线取流请求未完成", "code": failure.category},
                        status_code=failure.status, headers={**_SAFE, **failure.headers})


@dataclass(frozen=True)
class RequestedRange:
    start: int | None
    end: int

    @property
    def header(self):
        return f"bytes=-{self.end}" if self.start is None else f"bytes={self.start}-{self.end}"


def requested_range(raw, cap, restriction=None):
    if restriction is not None:
        start, end = restriction
        if end - start + 1 > cap:
            raise GatewayError("response_byte_limit", 413)
        fixed = f"bytes={start}-{end}"
        if raw is not None and raw != fixed:
            raise GatewayError("resource_range_mismatch", 416)
        return RequestedRange(start, end)
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw) > 64:
        raise GatewayError("invalid_range", 416)
    match = _RANGE.fullmatch(raw)
    if match is None or not any(match.groups()):
        raise GatewayError("invalid_range", 416)
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if not 0 < suffix <= cap:
            raise GatewayError("range_byte_limit", 416)
        return RequestedRange(None, suffix)
    start = int(first)
    end = int(last) if last else min(2**63 - 1, start + cap - 1)
    if not 0 <= start <= end <= 2**63 - 1 or end - start + 1 > cap:
        raise GatewayError("range_byte_limit", 416)
    return RequestedRange(start, end)


def _one(headers, name, *, required=False):
    values = headers.get_list(name)
    if len(values) > 1 or required and not values:
        raise GatewayError("invalid_upstream_headers")
    value = values[0] if values else None
    if value is not None and (len(value) > 8192 or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise GatewayError("invalid_upstream_headers")
    return value


def _length(headers, *, required=False):
    raw = _one(headers, "content-length", required=required)
    if raw is None:
        return None
    if not _LENGTH.fullmatch(raw) or int(raw) > 2**63 - 1:
        raise GatewayError("invalid_upstream_length")
    return int(raw)


def _metadata(response, resource, requested, cap, method):
    """Validate before exposing headers; never advertise a truncated 200 file."""
    if _one(response.headers, "content-encoding") not in {None, "identity"}:
        raise GatewayError("encoded_response_not_supported")
    if response.status_code == 416:
        raw = _one(response.headers, "content-range", required=True)
        if _UNSATISFIED.fullmatch(raw) is None:
            raise GatewayError("invalid_upstream_range")
        raise GatewayError("range_not_satisfiable", 416, {"Content-Range": raw})
    if response.status_code not in {200, 206}:
        raise GatewayError("online_upstream_status", 503 if response.status_code == 429 else 502)
    content_type = (_one(response.headers, "content-type") or "application/octet-stream").split(";", 1)[0].strip().lower()
    if resource.kind == "manifest":
        if content_type not in _MANIFEST_TYPES or response.status_code != 200 or requested is not None:
            raise GatewayError("invalid_manifest_response")
        length = _length(response.headers)
        if method != "HEAD" and length is not None and length > cap:
            raise GatewayError("response_byte_limit", 413)
        # The rewritten body has different bytes and length from the origin.
        return 200, {**_SAFE, "Content-Type": "application/vnd.apple.mpegurl"}, length
    allowed = {"video/mp4", "application/octet-stream"} if resource.kind == "mp4" else _SEGMENT_TYPES
    if content_type not in allowed:
        raise GatewayError("unsupported_upstream_content_type")
    length = _length(response.headers, required=True)
    result = {**_SAFE, "Content-Type": "video/mp4" if resource.kind == "mp4" else content_type,
              "Content-Length": str(length), "Accept-Ranges": "bytes"}
    if method != "HEAD" and length > cap:
        raise GatewayError("range_required" if requested is None and resource.kind == "mp4" else "response_byte_limit",
                           416 if requested is None and resource.kind == "mp4" else 413)
    if response.status_code == 206:
        raw = _one(response.headers, "content-range", required=True)
        match = _CONTENT_RANGE.fullmatch(raw)
        if match is None or requested is None:
            raise GatewayError("invalid_upstream_range")
        start, end, total = map(int, match.groups())
        if not 0 <= start <= end < total <= 2**63 - 1 or end - start + 1 != length:
            raise GatewayError("invalid_upstream_range")
        expected = ((max(0, total - requested.end), total - 1) if requested.start is None
                    else (requested.start, min(requested.end, total - 1)))
        if (start, end) != expected:
            raise GatewayError("upstream_range_mismatch")
        result["Content-Range"] = raw
    elif requested is not None and method != "HEAD":
        raise GatewayError("upstream_ignored_range")
    for name in ("ETag", "Last-Modified"):
        value = _one(response.headers, name)
        if value is not None:
            result[name] = value
    return response.status_code, result, length


def start_workers():
    global _accepting
    with _executor_lock:
        _accepting = True


def shutdown_workers():
    global _executor, _accepting
    with _executor_lock:
        executor, _executor = _executor, None
        _accepting = False
    if executor is not None:
        # Registry aborts leases first. Pending work still runs its finally
        # cleanup instead of losing a lease when an executor Future is cancelled.
        executor.shutdown(wait=False, cancel_futures=False)


def _submit(worker):
    global _executor
    with _executor_lock:
        if not _accepting:
            raise GatewayError("online_gateway_stopped", 503)
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="online-bytes")
        context = copy_context()
        _executor.submit(context.run, worker)


class _Transfer:
    def __init__(self, registry, session, resource, method, headers, selected, lease):
        self.registry, self.session, self.resource = registry, session, resource
        self.method, self.headers, self.selected, self.lease = method, headers, selected, lease
        self.loop = asyncio.get_running_loop()
        self.messages = asyncio.Queue(maxsize=2)
        self.ready = Future()
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self._worker_done = self._consumer_done = False

    def _emit(self, message):
        self.lease.check()
        if self.stopped.is_set():
            raise GatewayError("request_cancelled", 410)
        coroutine = self.messages.put(message)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        except RuntimeError:
            coroutine.close()
            raise GatewayError("request_cancelled", 410) from None
        try:
            while True:
                try:
                    future.result(timeout=.1)
                    return
                except FutureTimeout:
                    self.lease.check()
                    if self.stopped.is_set():
                        raise GatewayError("request_cancelled", 410)
        finally:
            if not future.done():
                future.cancel()

    def finish_consumer(self, *, abort=False):
        self.stopped.set()
        if abort:
            self.lease.abort()
        with self._lock:
            self._consumer_done = True
            release = self._worker_done
        if release:
            self.lease.close()

    def _finish_worker(self):
        with self._lock:
            self._worker_done = True
            release = self._consumer_done
        if release:
            self.lease.close()

    def _signal_failure(self, failure):
        # Error delivery must work even after a lease is cancelled. Drop queued
        # bytes rather than leaving a consumer waiting for an absent sentinel.
        def wake():
            while not self.messages.empty():
                self.messages.get_nowait()
            self.messages.put_nowait(failure)

        try:
            self.loop.call_soon_threadsafe(wake)
        except RuntimeError:
            pass

    def run(self):
        operation = NetworkOperation(self.lease.deadline, self.lease.cancelled, attach=self.lease.attach)
        observation = (None if self.method == "HEAD" else health.begin_media(
            self.session.client, "manifest" if self.resource.kind == "manifest" else "bytes"))
        cap = self.registry.response_limit(self.resource.kind)
        try:
            with self.session.client.guarded_stream(self.method, self.resource.url, operation=operation,
                                                    headers=self.headers) as response:
                status, headers, length = _metadata(response, self.resource, self.selected, cap, self.method)
                if self.method == "HEAD":
                    self.ready.set_result((status, headers))
                elif self.resource.kind == "manifest":
                    content = bytearray()
                    # Charge each raw read before aggregation. HTTPX's sized
                    # iterator can otherwise hide an incomplete last chunk
                    # when cancellation or a timeout interrupts the source.
                    for chunk in response.iter_raw():
                        self.lease.consume(len(chunk))
                        content.extend(chunk)
                        if len(content) > cap:
                            raise GatewayError("response_byte_limit", 413)
                    if length is not None and len(content) != length:
                        raise GatewayError("upstream_length_mismatch")

                    def register(url, *, kind, byte_range=None):
                        self.lease.check()
                        return self.registry.register(self.session, url, kind, byte_range=byte_range).path(self.session.id)

                    try:
                        body = rewrite_manifest(bytes(content), str(response.url), register)
                    except Exception:
                        # A later URI may fail target or resource validation
                        # after earlier references were registered. Revoke the
                        # whole session so none of that partial manifest stays
                        # addressable and all reserved resources are reclaimed.
                        try:
                            self.registry.close(self.session.id, self.session.owner)
                        except SessionError:
                            pass
                        raise
                    headers["Content-Length"] = str(len(body))
                    self.ready.set_result((status, headers))
                    for start in range(0, len(body), CHUNK_BYTES):
                        self._emit(body[start:start + CHUNK_BYTES])
                else:
                    self.ready.set_result((status, headers))
                    total = 0
                    pending = bytearray()
                    for chunk in response.iter_raw():
                        self.lease.consume(len(chunk))
                        total += len(chunk)
                        if total > length:
                            raise GatewayError("upstream_length_mismatch")
                        pending.extend(chunk)
                        while len(pending) >= CHUNK_BYTES:
                            self._emit(bytes(pending[:CHUNK_BYTES]))
                            del pending[:CHUNK_BYTES]
                    if total != length:
                        raise GatewayError("upstream_length_mismatch")
                    if pending:
                        self._emit(bytes(pending))
            self._emit(None)
            health.registry.finish(observation)
        except Exception as error:
            health.registry.finish(observation, error)
            failure = _failure(error)
            if not self.ready.done():
                self.ready.set_exception(failure)
            elif not self.stopped.is_set():
                self._signal_failure(failure)
        finally:
            operation.close()
            self._finish_worker()

    async def body(self):
        completed = False
        try:
            while True:
                self.lease.check()
                try:
                    message = await asyncio.wait_for(self.messages.get(), timeout=.1)
                except asyncio.TimeoutError:
                    continue
                if message is None:
                    completed = True
                    return
                if isinstance(message, GatewayError):
                    # Headers may already be sent. Abort the HTTP body with a
                    # fixed error; never turn an incomplete file into success.
                    raise message
                yield message
        finally:
            self.finish_consumer(abort=not completed)


class _TransferResponse(StreamingResponse):
    def __init__(self, transfer, *, status_code, headers):
        self.transfer = transfer
        super().__init__(transfer.body(), status_code=status_code, headers=headers)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # ASGI 2.4 reports a disconnect from send(). Close the suspended
            # async generator explicitly; its finally owns the lease cleanup.
            try:
                await self.body_iterator.aclose()
            finally:
                self.transfer.finish_consumer(abort=not self.transfer._consumer_done)


async def stream_response(request, registry, session, resource):
    """Relay one registered resource, with no caller-supplied upstream URL."""
    transfer = None
    lease = None
    try:
        cap = registry.response_limit(resource.kind)
        if len(request.headers.getlist("range")) > 1 or len(request.headers.getlist("if-range")) > 1:
            raise GatewayError("invalid_range", 416)
        raw_range = request.headers.get("range")
        manifest = resource.kind == "manifest"
        if manifest:
            # Native media engines can probe the playlist with Range. Its
            # representation is rewritten, so validate the hint but return
            # the complete bounded 200 representation, without forwarding an
            # origin byte range or claiming partial rewritten bytes.
            requested_range(raw_range, 2**63 - 1)
            selected = None
        else:
            selected = requested_range(raw_range, cap, resource.byte_range)
        headers = {"Range": selected.header} if selected is not None else {}
        if "if-range" in request.headers:
            value = request.headers["if-range"]
            if (raw_range is None or len(value) > 1024
                    or any(ord(char) < 32 or ord(char) > 126 for char in value)):
                raise GatewayError("invalid_if_range", 416)
            if not manifest:
                headers["If-Range"] = value
        # Unknown manifest lengths may need one final raw chunk to detect an
        # overrun. Reserve and charge it too; it is never sent or parsed.
        reserve = cap + CHUNK_BYTES if resource.kind == "manifest" else cap
        lease = registry.acquire(session, 0 if request.method == "HEAD" else reserve, kind=resource.kind)
        transfer = _Transfer(registry, session, resource, request.method, headers, selected, lease)
        try:
            _submit(transfer.run)
        except Exception:
            transfer._finish_worker()
            raise
        ready = asyncio.wrap_future(transfer.ready)
        while not ready.done():
            lease.check()
            if await request.is_disconnected():
                raise GatewayError("request_cancelled", 410)
            await asyncio.wait({ready}, timeout=.1)
        status, output_headers = await ready
        if request.method == "HEAD":
            transfer.finish_consumer()
            response = Response(status_code=status, headers=output_headers)
            if resource.kind == "manifest":
                # The GET representation is rewritten. Its length is unknown
                # without reading it; Response's automatic zero is incorrect.
                del response.headers["content-length"]
            return response
        return _TransferResponse(transfer, status_code=status, headers=output_headers)
    except BaseException as error:
        if transfer is not None:
            transfer.finish_consumer(abort=True)
        elif lease is not None:
            lease.close()
        if not isinstance(error, Exception):
            raise
        return error_response(error)
