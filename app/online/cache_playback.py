"""Owned local media delivery, with no provider, DNS or upstream fallback."""

import mimetypes
import os
import re
import stat
import threading
from urllib.parse import unquote, urlsplit

import anyio
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse

from . import cache, hls
from .sessions import CHUNK_BYTES, SessionError


HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_RANGE = re.compile(r"bytes=([0-9]{0,19})-([0-9]{0,19})\Z")


def _range(raw, size):
    if raw is None:
        return 0, size, 200
    match = _RANGE.fullmatch(raw)
    if not match or not any(match.groups()) or size == 0:
        return None
    first, last = match.groups()
    if not first:
        length = int(last)
        if length == 0:
            return None
        return max(0, size - length), size, 206
    start = int(first)
    end = min(size, int(last) + 1) if last else size
    return (start, end, 206) if start < end and start < size else None


class _LocalResponse(StreamingResponse):
    """Own the open file even when ASGI disconnects before body iteration."""

    def __init__(self, body, release, **kwargs):
        super().__init__(body, **kwargs)
        self._release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self._release)


def _open(registry, session, resource):
    registry.check(session)
    try:
        path = cache.asset_path(session.cache_id, resource.url)
    except cache.CacheError as error:
        if error.category in {"online_cache_corrupt", "online_cache_path_unsafe"}:
            cache.mark_corrupt(session.cache_id)
        raise
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError()
        source = os.fdopen(descriptor, "rb")
        descriptor = None
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        cache.mark_corrupt(session.cache_id)
        raise cache.CacheError("online_cache_corrupt", 409) from None
    try:
        release = registry.attach_local_reader(session, source.close)
    except BaseException:
        source.close()
        raise
    return source, info.st_size, release


class _OpenAttempt:
    """Reclaim a worker's open file if its awaiting request was cancelled."""

    def __init__(self, registry, session, resource):
        self._args = registry, session, resource
        self._lock = threading.Lock()
        self._cancelled = False
        self._result = None

    def run(self):
        result = _open(*self._args)
        with self._lock:
            cancelled = self._cancelled
            if not cancelled:
                self._result = result
        if cancelled:
            result[2]()
            raise SessionError("session_closed")
        return result

    def cancel(self):
        with self._lock:
            self._cancelled = True
            result, self._result = self._result, None
        if result is not None:
            result[2]()


def _manifest(registry, session, resource):
    source, size, release = _open(registry, session, resource)
    try:
        if size > hls.OUTPUT_BYTES:
            raise hls.ManifestError("hls_output_size_limit")
        body = source.read(hls.OUTPUT_BYTES + 1)
        registry.check(session)

        def register(url, *, kind, byte_range=None):
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.netloc != "cache.invalid"
                    or parsed.query or parsed.fragment):
                raise hls.ManifestError("invalid_local_hls_reference")
            name = unquote(parsed.path).removeprefix("/")
            selected = session._local_files.get(name)
            if selected is None:
                raise hls.ManifestError("invalid_local_hls_reference")
            return selected.path(session.id)

        return hls.rewrite_manifest(body, "https://cache.invalid/" + resource.url, register)
    except (OSError, ValueError, hls.ManifestError):
        if session.cancelled.is_set():
            raise SessionError("session_closed") from None
        cache.mark_corrupt(session.cache_id)
        raise cache.CacheError("online_cache_corrupt", 409) from None
    finally:
        release()


async def stream_response(request, registry, session, resource):
    if resource.kind == "manifest":
        body = await run_in_threadpool(_manifest, registry, session, resource)
        return Response(body if request.method != "HEAD" else b"", headers={
            **HEADERS, "Content-Type": "application/vnd.apple.mpegurl", "Content-Length": str(len(body))})
    attempt = _OpenAttempt(registry, session, resource)
    try:
        source, size, release = await run_in_threadpool(attempt.run)
    except BaseException:
        attempt.cancel()
        raise
    selected = _range(request.headers.get("range"), size)
    if selected is None:
        release()
        return Response(status_code=416, headers={**HEADERS, "Content-Range": f"bytes */{size}"})
    start, end, status = selected
    extension = resource.url.rsplit(".", 1)[-1]
    media_type = {"ts": "video/mp2t", "m4s": "video/iso.segment", "vtt": "text/vtt"}.get(
        extension, mimetypes.guess_type(resource.url)[0] or "application/octet-stream")
    headers = {**HEADERS, "Accept-Ranges": "bytes", "Content-Length": str(end - start),
               "Content-Type": media_type}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end - 1}/{size}"
    if request.method == "HEAD":
        release()
        return Response(status_code=status, headers=headers)
    try:
        source.seek(start)
    except BaseException:
        release()
        raise

    def chunks():
        remaining = end - start
        try:
            while remaining:
                registry.touch(session)
                body = source.read(min(CHUNK_BYTES, remaining))
                if not body:
                    cache.mark_corrupt(session.cache_id)
                    raise cache.CacheError("online_cache_corrupt", 409)
                remaining -= len(body)
                yield body
        except (OSError, ValueError):
            if not session.cancelled.is_set():
                cache.mark_corrupt(session.cache_id)
            raise SessionError("session_closed") from None
        finally:
            release()

    return _LocalResponse(chunks(), release, status_code=status, headers=headers)
