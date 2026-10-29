"""Small, opaque, source-bound poster leases; never decode or persist images."""

from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid

from ..network.client import NetworkRequestError, create_online_client
from ..network.guard import NetworkOperation, NetworkOperationError, current_operation, operation_context
from ..network.policy import NetworkPolicyError
from ..network.targets import TargetPolicy, TargetPolicyError
from ..store.online import OnlineNotFound
from ..store.online_network import load_online_network_state
from . import health, service
from .artwork_cache import ArtworkCache, ArtworkCacheBusy
from .catalog import CatalogError


MAX_RECORDS = 512
MAX_URL_BYTES = 2 * 1024**2
MAX_AGE_SECONDS = 1800
MAX_BODY_BYTES = 512 * 1024
MAX_READ_SECONDS = 10
_SOURCE_ID = re.compile(r"src_[0-9a-f]{32}\Z")
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_readers = threading.BoundedSemaphore(2)


class ArtworkError(CatalogError):
    """Fixed public categories without upstream URLs or network credentials."""


@dataclass(frozen=True)
class ArtworkLease:
    source_id: str
    url: str = field(repr=False)
    configuration: str = field(repr=False)
    created: float


class ArtworkRegistry:
    def __init__(self, *, capacity=MAX_RECORDS, url_budget=MAX_URL_BYTES,
                 ttl=MAX_AGE_SECONDS, clock=time.monotonic):
        if (type(capacity) is not int or not 1 <= capacity <= MAX_RECORDS
                or type(url_budget) is not int or not 1 <= url_budget <= MAX_URL_BYTES
                or type(ttl) not in {int, float} or not 0 < ttl <= MAX_AGE_SECONDS):
            raise ValueError("invalid artwork limits")
        self._capacity, self._url_budget, self._ttl, self._clock = capacity, url_budget, ttl, clock
        self._entries = OrderedDict()
        self._keys = {}
        self._url_bytes = 0
        self._lock = threading.Lock()

    def _remove(self, token):
        entry = self._entries.pop(token)
        self._keys.pop((entry.source_id, entry.configuration, entry.url), None)
        self._url_bytes -= len(entry.url.encode("utf-8"))

    def _prune(self, now):
        for token, entry in tuple(self._entries.items()):
            if now - entry.created >= self._ttl:
                self._remove(token)

    def register(self, source_id, url, configuration):
        cost = len(url.encode("utf-8"))
        if cost > self._url_budget:
            return ""
        now = self._clock()
        key = (source_id, configuration, url)
        with self._lock:
            self._prune(now)
            token = self._keys.get(key)
            if token is None:
                while self._entries and (len(self._entries) >= self._capacity
                                         or self._url_bytes + cost > self._url_budget):
                    self._remove(next(iter(self._entries)))
                token = uuid.uuid4().hex
                self._entries[token] = ArtworkLease(source_id, url, configuration, now)
                self._keys[key] = token
                self._url_bytes += cost
            self._entries.move_to_end(token)
        return f"/api/online/catalog/artwork/{source_id}/{token}"

    def lookup(self, source_id, token):
        if (not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id)
                or not isinstance(token, str) or not _TOKEN.fullmatch(token)):
            raise ArtworkError("online_artwork_not_found", 404)
        with self._lock:
            self._prune(self._clock())
            entry = self._entries.get(token)
            if entry is None or entry.source_id != source_id:
                raise ArtworkError("online_artwork_not_found", 404)
            self._entries.move_to_end(token)
            return entry

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._keys.clear()
            self._url_bytes = 0


registry = ArtworkRegistry()
cache = ArtworkCache()


def _state(source_id):
    try:
        state = load_online_network_state(source_id)
    except OnlineNotFound:
        raise ArtworkError("online_artwork_not_found", 404) from None
    except (sqlite3.Error, ValueError, TypeError):
        raise ArtworkError("online_artwork_unavailable", 503) from None
    source = state.get("source_config")
    if not state.get("online_enabled") or not source or not source.get("enabled"):
        raise ArtworkError("online_source_disabled", 503)
    return state


def _fingerprint(state):
    # This includes the complete source record and referenced network/profile
    # revisions/ciphertext. Nothing in this server-only digest is sent upstream.
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def register(source, url):
    """Authorize URL syntax and fixed host membership without DNS or HTTP I/O."""
    return register_many(source, [url])[0]


def register_many(source, urls):
    """Register one catalogue page using only one network/source DB snapshot."""
    if not isinstance(urls, (list, tuple)) or len(urls) > 100:
        raise ValueError("invalid artwork catalogue page")
    empty = [""] * len(urls)
    if not isinstance(source, dict) or not any(isinstance(url, str) and url for url in urls):
        return empty
    try:
        policy = TargetPolicy(source["id"], source["allowed_hosts"])
        canonical = []
        for url in urls:
            try:
                canonical.append(policy._canonical_url(url)[0])
            except (TargetPolicyError, TypeError, ValueError):
                canonical.append("")
        if not any(canonical):
            return empty
        state = _state(source["id"])
        if source != state["source_config"]:
            return empty
        fingerprint = _fingerprint(state)
        return [registry.register(source["id"], url, fingerprint) if url else "" for url in canonical]
    except (ArtworkError, TargetPolicyError, KeyError, TypeError, ValueError):
        # Missing artwork must not invalidate an otherwise valid catalogue row.
        return empty


def _current(access, entry):
    service._current_access(access)
    state = _state(entry.source_id)
    if _fingerprint(state) != entry.configuration:
        raise ArtworkError("online_artwork_stale", 404)
    return state


def _mime(data):
    if len(data) >= 3 and data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 33 and data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        return "image/png"
    if (len(data) >= 20 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
            and data[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
            and int.from_bytes(data[4:8], "little") + 8 == len(data)):
        return "image/webp"
    raise ArtworkError("unsupported_online_artwork", 502)


def _body(response, access, entry, operation):
    if response.status_code != 200:
        raise ArtworkError("online_artwork_upstream_error", 502)
    encodings = response.headers.get_list("content-encoding")
    types = response.headers.get_list("content-type")
    lengths = response.headers.get_list("content-length")
    if encodings and encodings != ["identity"]:
        raise ArtworkError("unsupported_online_artwork", 502)
    if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() not in {
            "image/jpeg", "image/png", "image/webp"}:
        raise ArtworkError("unsupported_online_artwork", 502)
    expected = None
    if lengths:
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
            raise ArtworkError("invalid_online_artwork_length", 502)
        expected = int(lengths[0])
        if expected > MAX_BODY_BYTES:
            raise ArtworkError("online_artwork_byte_limit", 502)
    data = bytearray()
    for chunk in response.iter_raw(chunk_size=32 * 1024):
        operation.check()
        if len(data) + len(chunk) > MAX_BODY_BYTES:
            raise ArtworkError("online_artwork_byte_limit", 502)
        data.extend(chunk)
        _current(access, entry)
    if expected is not None and len(data) != expected:
        raise ArtworkError("invalid_online_artwork_length", 502)
    mime = _mime(data)
    if mime != types[0].split(";", 1)[0].strip().lower():
        raise ArtworkError("unsupported_online_artwork", 502)
    return bytes(data), mime


def _authorized(access, entry, token, operation):
    operation.check()
    state = _current(access, entry)
    registry.lookup(entry.source_id, token)
    operation.check()
    return state


def _read(access, entry, token, operation):
    """The producer owns its reader until all I/O and validation have ended."""
    acquired = False
    try:
        while not acquired:
            acquired = _readers.acquire(timeout=min(0.1, operation.remaining()))
        state = _authorized(access, entry, token, operation)
        expected_network, _mode = health.configuration(state, "image")
        with create_online_client(entry.source_id, "image") as client:
            # The factory records its exact immutable DB view. Reject a policy
            # race before its reader can contact an upstream host.
            context = getattr(client, "_online_health_context", None)
            if (context is None or context.configuration_fingerprint != expected_network
                    or context.purpose != "image" or context.source_id != entry.source_id):
                raise ArtworkError("online_artwork_stale", 404)
            _authorized(access, entry, token, operation)
            with client.stream("GET", entry.url, headers={"Accept": "image/jpeg,image/png,image/webp"}) as response:
                result = _body(response, access, entry, operation)
            _authorized(access, entry, token, operation)
            return result
    finally:
        if acquired:
            _readers.release()


def _shared_error(error, access, operation):
    # Never export one request's cancellation or viewing-identity error to
    # another waiter. It may retry only after this producer closes its reader.
    if isinstance(error, ArtworkError):
        return error.category, error.status
    if isinstance(error, NetworkRequestError):
        try:
            operation.check()
            service._current_access(access)
        except (NetworkOperationError, service.PlaybackError):
            return None
        return "online_artwork_unavailable", 502
    if isinstance(error, NetworkPolicyError):
        return "online_artwork_unavailable", 503
    return None


def fetch(access, source_id, token, *, operation=None):
    """Authorize every caller, then reuse or coalesce the bounded image bytes."""
    service._current_access(access)
    entry = registry.lookup(source_id, token)
    operation = operation or current_operation() or NetworkOperation(time.monotonic() + MAX_READ_SECONDS)
    # The same socket-registration owner as the HTTP disconnect watcher also
    # bounds cache/coalescing waits, DNS, pool waits and TLS work.
    operation.deadline = min(operation.deadline, time.monotonic() + MAX_READ_SECONDS)
    key = (source_id, entry.configuration, hashlib.sha256(entry.url.encode()).digest())
    try:
        with operation_context(operation):
            while True:
                _authorized(access, entry, token, operation)
                try:
                    result, flight, producer = cache.acquire(key)
                except ArtworkCacheBusy:
                    raise ArtworkError("online_artwork_busy", 429) from None
                if flight is None:
                    _authorized(access, entry, token, operation)
                    return result
                try:
                    if producer:
                        result, error = None, None
                        try:
                            result = _read(access, entry, token, operation)
                        except BaseException as problem:
                            error = _shared_error(problem, access, operation)
                            raise
                        finally:
                            cache.complete(key, flight, result=result, error=error)
                        _authorized(access, entry, token, operation)
                        return result
                    while not flight.done.wait(min(0.1, operation.remaining())):
                        operation.check()
                    _authorized(access, entry, token, operation)
                    if flight.error is not None:
                        raise ArtworkError(*flight.error)
                    # A successful producer populated the cache. A cancelled
                    # producer left no result, so compete with our own deadline.
                finally:
                    cache.release()
    except NetworkOperationError as error:
        raise ArtworkError("online_artwork_timeout" if str(error) == "operation_timeout"
                           else "online_artwork_cancelled", 504 if str(error) == "operation_timeout" else 409) from None
    except NetworkRequestError:
        try:
            operation.check()
        except NetworkOperationError as stopped:
            raise ArtworkError("online_artwork_timeout" if str(stopped) == "operation_timeout"
                               else "online_artwork_cancelled", 504 if str(stopped) == "operation_timeout" else 409) from None
        raise ArtworkError("online_artwork_unavailable", 502) from None
    except NetworkPolicyError:
        raise ArtworkError("online_artwork_unavailable", 503) from None
    finally:
        operation.close()
