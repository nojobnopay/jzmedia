"""In-memory online sessions, opaque resources and pre-reserved read budgets.

Only server-owned resolvers create sessions. This module never accepts a URL
from an HTTP request, opens an upstream response, or starts a background thread.
The application calls sweep() and rechecks its source/access gates periodically.
"""

from collections import Counter
from dataclasses import dataclass, field, fields
import hmac
import math
import re
import secrets
import threading
import time

from ..network.targets import TargetPolicyError
from .models import OnlinePlaybackDescriptor, OnlinePlaybackIdentity


CHUNK_BYTES = 64 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
_RESOURCE_KINDS = {"mp4", "manifest", "segment", "map", "subtitle"}


class SessionError(RuntimeError):
    """A fixed category, never containing a target URL or credentials."""

    def __init__(self, category):
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class SessionLimits:
    ttl_seconds: float = 7200.0
    idle_seconds: float = 300.0
    request_seconds: float = 45.0
    max_sessions: int = 16
    max_owner_sessions: int = 2
    max_source_sessions: int = 8
    max_active: int = 8
    max_source_active: int = 4
    max_session_active: int = 2
    max_session_bytes: int = 8 * 1024**3
    max_reserved_bytes: int = 256 * 1024**2
    max_mp4_response_bytes: int = 8 * 1024**2
    max_segment_response_bytes: int = 32 * 1024**2
    max_manifest_bytes: int = 256 * 1024
    max_subtitle_bytes: int = 2 * 1024**2
    max_resources: int = 4096
    max_global_resources: int = 16384
    max_resource_url_bytes: int = 16 * 1024**2

    def __post_init__(self):
        for definition in fields(self):
            value = getattr(self, definition.name)
            valid = (type(value) in {int, float} and math.isfinite(value) and value > 0
                     if definition.name.endswith("seconds") else type(value) is int and value > 0)
            if not valid:
                raise SessionError("invalid_session_limits")

    def response_limit(self, kind):
        if not isinstance(kind, str):
            raise SessionError("invalid_resource_kind")
        if kind == "mp4":
            return self.max_mp4_response_bytes
        if kind in {"segment", "map"}:
            return self.max_segment_response_bytes
        if kind == "manifest":
            return self.max_manifest_bytes
        if kind == "subtitle":
            return self.max_subtitle_bytes
        raise SessionError("invalid_resource_kind")


@dataclass(frozen=True)
class Resource:
    id: str
    kind: str
    url: str = field(repr=False)
    byte_range: tuple[int, int] | None = None

    def path(self, session_id):
        if not isinstance(session_id, str) or not _IDENTIFIER.fullmatch(session_id):
            raise SessionError("session_not_found")
        return f"/api/online/sessions/{session_id}/resources/{self.id}"


@dataclass(eq=False)
class OnlineSession:
    id: str
    identity: OnlinePlaybackIdentity
    transport: str
    owner: str = field(repr=False)
    client: object = field(repr=False)
    entry: Resource = field(repr=False)
    created_monotonic: float
    expires_monotonic: float
    expires_at: int
    last_activity: float
    idle_seconds: float = field(repr=False)
    seekable: bool = True
    duration_seconds: float | None = None
    context: object = field(default=None, repr=False)
    cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    bytes_consumed: int = 0
    bytes_reserved: int = 0
    _resources: dict = field(default_factory=dict, repr=False)
    _resource_keys: dict = field(default_factory=dict, repr=False)
    _leases: dict = field(default_factory=dict, repr=False)

    @property
    def source_id(self):
        return self.identity.source_id

    @property
    def idle_deadline(self):
        return self.last_activity + self.idle_seconds

    @property
    def descriptor(self):
        return OnlinePlaybackDescriptor(identity=self.identity, session_id=self.id,
                                        transport=self.transport, seekable=self.seekable,
                                        duration_seconds=self.duration_seconds, expires_at=self.expires_at)


class Lease:
    """A worker owns this slot until its finally block calls close().

    Abort only cancels and interrupts attached I/O. It deliberately retains the
    slot and unused reservation until that worker actually exits. Normal close
    forgets interruption hooks without invoking them, preserving keepalive.
    """

    def __init__(self, registry, session, reserved, deadline):
        self._registry, self.session = registry, session
        self.reserved_bytes, self.consumed_bytes = reserved, 0
        self.deadline = deadline
        self.cancelled = threading.Event()
        self._callbacks = {}
        self._closed = False
        self._reason = "request_cancelled"

    def __repr__(self):
        return f"Lease(reserved_bytes={self.reserved_bytes}, consumed_bytes={self.consumed_bytes}, closed={self._closed})"

    @property
    def remaining_bytes(self):
        return self.reserved_bytes - self.consumed_bytes

    def consume(self, count):
        self._registry._consume(self, count)

    def check(self):
        self._registry.check_lease(self)

    def attach(self, callback):
        return self._registry._attach(self, callback)

    def abort(self, category="request_cancelled"):
        self._registry._abort(self, category)

    def close(self):
        self._registry._release(self)

    def __enter__(self):
        self.check()
        return self

    def __exit__(self, *_args):
        self.close()


def _owner(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and value.isascii() and all(32 <= ord(c) < 127 for c in value)


def _same_owner(first, second):
    return _owner(second) and hmac.compare_digest(first, second)


def _run_callbacks(callbacks):
    for callback in callbacks:
        try:
            callback()
        except Exception:
            # Cleanup must continue for all sockets. Never log an exception
            # whose third-party message may contain a signed URL or headers.
            pass


class SessionRegistry:
    def __init__(self, limits=None, *, clock=time.monotonic, wall_clock=time.time):
        self.limits = limits or SessionLimits()
        if not isinstance(self.limits, SessionLimits) or not callable(clock) or not callable(wall_clock):
            raise SessionError("invalid_session_limits")
        self._clock, self._wall_clock = clock, wall_clock
        self._lock = threading.Lock()
        self._sessions, self._pending, self._leases = {}, {}, {}
        self._clients = set()
        self._cancelled_pending = set()
        self._source_active = Counter()
        self._reserved = self._resource_count = self._resource_bytes = self._consumed = 0

    def response_limit(self, kind):
        return self.limits.response_limit(kind)

    @staticmethod
    def _close_client(client):
        if callable(getattr(client, "close", None)):
            _run_callbacks([client.close])

    def create(self, identity, owner, client, entry_url, transport, *, seekable=True,
               duration_seconds=None, context=None):
        """Take ownership of a newly created server-side client, even on failure."""
        pending_id = None
        published = False
        duplicate_client = False
        try:
            try:
                identity = OnlinePlaybackIdentity.model_validate(identity)
            except (ValueError, TypeError):
                raise SessionError("invalid_session_configuration") from None
            if (not _owner(owner) or transport not in {"mp4", "hls"} or type(seekable) is not bool
                    or getattr(getattr(client, "snapshot", None), "source_id", None) != identity.source_id
                    or getattr(getattr(client, "targets", None), "source_id", None) != identity.source_id
                    or not callable(getattr(client.targets, "resolve", None))
                    or not callable(getattr(client, "close", None))):
                raise SessionError("invalid_session_configuration")
            # Validate descriptor fields before DNS; it never stores the URL.
            session_id = secrets.token_urlsafe(32)
            now = self._clock()
            expires_at = max(1, math.ceil(self._wall_clock() + self.limits.ttl_seconds))
            OnlinePlaybackDescriptor(identity=identity, session_id=session_id, transport=transport,
                                     seekable=seekable, duration_seconds=duration_seconds, expires_at=expires_at)
            self.sweep()
            with self._lock:
                if id(client) in self._clients:
                    duplicate_client = True
                    raise SessionError("session_client_already_bound")
                records = [(s.owner, s.source_id) for s in self._sessions.values()] + list(self._pending.values())
                if (len(records) >= self.limits.max_sessions
                        or sum(o == owner for o, _s in records) >= self.limits.max_owner_sessions
                        or sum(s == identity.source_id for _o, s in records) >= self.limits.max_source_sessions):
                    raise SessionError("session_limit")
                pending_id = object()
                self._pending[pending_id] = (owner, identity.source_id)
                self._clients.add(id(client))
            # DNS may block for its bounded timeout. The pending reservation
            # already occupies session/owner/source slots, outside this lock.
            try:
                target = client.targets.resolve(entry_url)
            except TargetPolicyError as error:
                raise SessionError(str(error)) from None
            entry = Resource(secrets.token_urlsafe(24), "mp4" if transport == "mp4" else "manifest", target.url)
            current = self._clock()
            session = OnlineSession(session_id, identity, transport, owner, client, entry, now,
                                    now + self.limits.ttl_seconds, expires_at, current, self.limits.idle_seconds,
                                    seekable, duration_seconds, context)
            with self._lock:
                if pending_id in self._cancelled_pending:
                    raise SessionError("session_closed")
                if current >= session.expires_monotonic:
                    raise SessionError("session_expired")
                self._add_resource_locked(session, entry)
                self._sessions[session.id] = session
                self._pending.pop(pending_id)
                self._cancelled_pending.discard(pending_id)
                pending_id = None
                published = True
                return session
        except (ValueError, TypeError):
            raise SessionError("invalid_session_configuration") from None
        finally:
            if not published and not duplicate_client:
                with self._lock:
                    if pending_id is not None:
                        self._pending.pop(pending_id, None)
                        self._cancelled_pending.discard(pending_id)
                        self._clients.discard(id(client))
                self._close_client(client)

    def _live_locked(self, session):
        now = self._clock()
        return (self._sessions.get(session.id) is session and not session.cancelled.is_set()
                and now < session.expires_monotonic and now < session.idle_deadline)

    def get(self, session_id, owner):
        self.sweep()
        with self._lock:
            session = self._sessions.get(session_id) if isinstance(session_id, str) else None
            if session is None or not _same_owner(session.owner, owner) or not self._live_locked(session):
                raise SessionError("session_not_found")
            session.last_activity = self._clock()
            return session

    def check(self, session):
        self.sweep()
        with self._lock:
            if not self._live_locked(session):
                raise SessionError("session_closed")

    def sessions(self):
        """Return a bounded snapshot for application access/source rechecks."""
        self.sweep()
        with self._lock:
            return tuple(self._sessions.values())

    def _add_resource_locked(self, session, resource):
        size = len(resource.url.encode("utf-8"))
        if (len(session._resources) >= self.limits.max_resources
                or self._resource_count >= self.limits.max_global_resources
                or self._resource_bytes + size > self.limits.max_resource_url_bytes):
            raise SessionError("resource_limit")
        key = (resource.url, resource.kind, resource.byte_range)
        session._resources[resource.id] = resource
        session._resource_keys[key] = resource
        self._resource_count += 1
        self._resource_bytes += size

    def register(self, session, url, kind, *, base_url=None, byte_range=None):
        if not isinstance(kind, str) or kind not in _RESOURCE_KINDS or (byte_range is not None and (
                kind not in {"segment", "map"} or not isinstance(byte_range, tuple) or len(byte_range) != 2
                or any(type(value) is not int for value in byte_range)
                or not 0 <= byte_range[0] <= byte_range[1] <= 2**63 - 1)):
            raise SessionError("invalid_resource_configuration")
        self.check(session)
        try:
            target = session.client.targets.resolve(url, base_url=base_url)
        except TargetPolicyError as error:
            raise SessionError(str(error)) from None
        resource = Resource(secrets.token_urlsafe(24), kind, target.url, byte_range)
        with self._lock:
            if not self._live_locked(session):
                raise SessionError("session_closed")
            previous = session._resource_keys.get((resource.url, kind, byte_range))
            if previous is not None:
                return previous
            self._add_resource_locked(session, resource)
            return resource

    def resource(self, session, resource_id):
        self.check(session)
        with self._lock:
            resource = session._resources.get(resource_id) if isinstance(resource_id, str) else None
            if resource is None:
                raise SessionError("resource_not_found")
            return resource

    def acquire(self, session, reserve_bytes, *, kind=None):
        maximum = self.response_limit(kind) if kind is not None else max(
            self.limits.max_mp4_response_bytes, self.limits.max_segment_response_bytes,
            self.limits.max_manifest_bytes, self.limits.max_subtitle_bytes)
        if kind == "manifest":
            # An unknown-length body needs one bounded look-ahead chunk to
            # distinguish an exactly-full manifest from an oversized one.
            # Those actual bytes must be reserved and charged even on reject.
            maximum += CHUNK_BYTES
        if type(reserve_bytes) is not int or not 0 <= reserve_bytes <= maximum:
            raise SessionError("response_byte_limit")
        self.check(session)
        with self._lock:
            if not self._live_locked(session):
                raise SessionError("session_closed")
            if (len(self._leases) >= self.limits.max_active
                    or self._source_active[session.source_id] >= self.limits.max_source_active
                    or len(session._leases) >= self.limits.max_session_active):
                raise SessionError("request_limit")
            if (session.bytes_consumed + session.bytes_reserved + reserve_bytes > self.limits.max_session_bytes
                    or self._reserved + reserve_bytes > self.limits.max_reserved_bytes):
                raise SessionError("byte_budget_exhausted")
            now = self._clock()
            lease = Lease(self, session, reserve_bytes, min(now + self.limits.request_seconds,
                                                           session.expires_monotonic))
            self._leases[id(lease)] = session._leases[id(lease)] = lease
            self._source_active[session.source_id] += 1
            self._reserved += reserve_bytes
            session.bytes_reserved += reserve_bytes
            session.last_activity = now
            return lease

    def check_lease(self, lease):
        self.sweep()
        with self._lock:
            if lease._closed or lease.cancelled.is_set():
                raise SessionError(lease._reason)
            if not self._live_locked(lease.session):
                raise SessionError("session_closed")

    def _consume(self, lease, count):
        if type(count) is not int or count < 0:
            raise SessionError("invalid_byte_count")
        with self._lock:
            if lease._closed:
                raise SessionError(lease._reason)
            if count > lease.remaining_bytes:
                raise SessionError("response_byte_limit")
            lease.consumed_bytes += count
            lease.session.bytes_consumed += count
            lease.session.bytes_reserved -= count
            self._reserved -= count
            self._consumed += count
            lease.session.last_activity = self._clock()
        # Actual bytes already read remain charged if cancellation races with
        # delivery of a chunk. A cancelled request may not refund those bytes.
        self.check_lease(lease)

    @staticmethod
    def _abort_locked(lease, category):
        if lease._closed or lease.cancelled.is_set():
            return []
        lease._reason = category
        lease.cancelled.set()
        callbacks = list(lease._callbacks.values())
        lease._callbacks.clear()
        return callbacks

    def _abort(self, lease, category):
        if category not in {"request_cancelled", "request_timeout", "session_closed", "session_expired"}:
            category = "request_cancelled"
        with self._lock:
            callbacks = self._abort_locked(lease, category)
        _run_callbacks(callbacks)

    def _attach(self, lease, callback):
        if not callable(callback):
            raise SessionError("invalid_interrupt_callback")
        key = object()
        with self._lock:
            immediate = lease._closed or lease.cancelled.is_set() or not self._live_locked(lease.session)
            if not immediate:
                lease._callbacks[key] = callback
        if immediate:
            _run_callbacks([callback])

        def detach():
            with self._lock:
                lease._callbacks.pop(key, None)

        return detach

    def _release(self, lease):
        with self._lock:
            if lease._closed:
                return
            lease._closed = True
            lease._callbacks.clear()
            self._leases.pop(id(lease), None)
            lease.session._leases.pop(id(lease), None)
            self._source_active[lease.session.source_id] -= 1
            if not self._source_active[lease.session.source_id]:
                del self._source_active[lease.session.source_id]
            remaining = lease.remaining_bytes
            self._reserved -= remaining
            lease.session.bytes_reserved -= remaining

    def _drop_locked(self, session, category="session_closed"):
        if self._sessions.pop(session.id, None) is None:
            return []
        session.cancelled.set()
        callbacks = []
        for lease in session._leases.values():
            callbacks.extend(self._abort_locked(lease, category))
        self._resource_count -= len(session._resources)
        self._resource_bytes -= sum(len(r.url.encode("utf-8")) for r in session._resources.values())
        session._resources.clear()
        session._resource_keys.clear()
        self._clients.discard(id(session.client))
        callbacks.append(session.client.close)
        return callbacks

    def close(self, session_id, owner):
        self.sweep()
        with self._lock:
            session = self._sessions.get(session_id) if isinstance(session_id, str) else None
            if session is None or not _same_owner(session.owner, owner):
                raise SessionError("session_not_found")
            callbacks = self._drop_locked(session)
        _run_callbacks(callbacks)

    def _revoke(self, predicate, pending_predicate):
        with self._lock:
            self._cancelled_pending.update(key for key, value in self._pending.items() if pending_predicate(value))
            selected = [s for s in self._sessions.values() if predicate(s)]
            callbacks = []
            for session in selected:
                callbacks.extend(self._drop_locked(session))
        _run_callbacks(callbacks)

    def revoke_source(self, source_id):
        self._revoke(lambda session: session.source_id == source_id, lambda pending: pending[1] == source_id)

    def close_owner(self, owner):
        self._revoke(lambda session: _same_owner(session.owner, owner), lambda pending: _same_owner(pending[0], owner))

    def close_all(self):
        self._revoke(lambda _session: True, lambda _pending: True)

    def sweep(self):
        now = self._clock()
        with self._lock:
            callbacks = []
            for session in list(self._sessions.values()):
                if now >= min(session.expires_monotonic, session.idle_deadline):
                    callbacks.extend(self._drop_locked(session, "session_expired"))
            for lease in self._leases.values():
                if now >= lease.deadline:
                    callbacks.extend(self._abort_locked(lease, "request_timeout"))
        _run_callbacks(callbacks)

    def stats(self):
        with self._lock:
            return {"sessions": len(self._sessions), "pending_sessions": len(self._pending),
                    "active_requests": len(self._leases), "reserved_bytes": self._reserved,
                    "consumed_bytes": self._consumed, "resources": self._resource_count,
                    "resource_url_bytes": self._resource_bytes}
