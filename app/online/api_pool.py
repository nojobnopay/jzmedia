"""Short, exclusive foreground API connection leases; no response caching.

Every lease still constructs a fresh guarded candidate for current TLS trust
and health binding. Only an older client's socket pool may be reused, after a
fresh complete DNS validation. No network I/O or client close runs under the
registry lock. Catalogue admission owns the existing global/owner limits.
"""

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import threading
import time

from ..network.client import NetworkClient
from ..network.guard import current_operation
from ..network.stages import observe_request


MAX_IDLE = 8
MAX_ACTIVE = 4
IDLE_SECONDS = 5
LIFETIME_SECONDS = 30
_current = ContextVar("online_foreground_api_pool", default=None)


def _close(client):
    try:
        client.close()
    except Exception:
        # Cleanup never replaces a response or its original failure. Never
        # retain clients that failed to close or log their upstream details.
        pass


@dataclass
class _Entry:
    client: NetworkClient
    created: float
    returned: float


class ClientPool:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._idle = OrderedDict()
        self._active = {}
        self._generation = 0
        self._enabled = True
        self._lock = threading.Lock()

    def _expired(self, entry, now):
        return now - entry.returned >= IDLE_SECONDS or now - entry.created >= LIFETIME_SECONDS

    def _collect(self, now):
        removed = []
        for key, entry in tuple(self._idle.items()):
            if self._expired(entry, now):
                removed.append(self._idle.pop(key).client)
        return removed

    def begin(self, key, *, fresh=False):
        with self._lock:
            removed = self._collect(self._clock())
            entry = self._idle.pop(key, None)
            if fresh and entry is not None:
                removed.append(entry.client)
                entry = None
            ticket = None
            if self._enabled and len(self._active) < MAX_ACTIVE:
                ticket = object()
                self._active[ticket] = self._generation, key
            elif entry is not None:
                removed.append(entry.client)
                entry = None
        for client in removed:
            _close(client)
        return ticket, entry

    def finish(self, ticket, entry=None):
        removed = []
        with self._lock:
            reservation = self._active.pop(ticket, None)
            now = self._clock()
            removed.extend(self._collect(now))
            if entry is not None:
                if (reservation is not None and self._enabled
                        and reservation[0] == self._generation and not self._expired(entry, now)):
                    key = reservation[1]
                    previous = self._idle.pop(key, None)
                    if previous is not None:
                        removed.append(previous.client)
                    self._idle[key] = entry
                    while len(self._idle) > MAX_IDLE:
                        removed.append(self._idle.popitem(last=False)[1].client)
                else:
                    removed.append(entry.client)
        for client in removed:
            _close(client)

    def discard(self, key):
        with self._lock:
            entry = self._idle.pop(key, None)
            # A retired worker still owns and closes its active client.
            for ticket, (generation, active_key) in tuple(self._active.items()):
                if active_key == key:
                    self._active[ticket] = -1, active_key
        if entry is not None:
            _close(entry.client)

    def sweep(self):
        with self._lock:
            removed = self._collect(self._clock())
        for client in removed:
            _close(client)

    def clear(self, *, enabled=None):
        with self._lock:
            self._generation += 1
            if enabled is not None:
                self._enabled = enabled
            removed = [entry.client for entry in self._idle.values()]
            self._idle.clear()
        for client in removed:
            _close(client)

    def stats(self):
        with self._lock:
            return {"idle": len(self._idle), "active": len(self._active)}


pool = ClientPool()


def clear():
    pool.clear()


def start():
    pool.clear(enabled=True)


def shutdown():
    pool.clear(enabled=False)


def sweep():
    pool.sweep()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class _Scope:
    key: str
    source: str
    check: object

    def confirm(self):
        operation = current_operation()
        if operation is not None:
            operation.check()
        self.check()
        if operation is not None:
            operation.check()


@contextmanager
def scope(access, source, configuration, check):
    if (not callable(check) or not isinstance(configuration, str) or len(configuration) != 64
            or any(char not in "0123456789abcdef" for char in configuration)):
        raise ValueError("invalid api pool scope")
    context = _Scope(_digest(["api-connections-v1", access.owner, access.generation,
                              access.protection_revision, source, configuration]), _digest(source), check)
    token = _current.set(context)
    try:
        context.confirm()
        yield
        context.confirm()
    except BaseException:
        pool.discard(context.key)
        raise
    finally:
        _current.reset(token)


class Lease:
    def __init__(self, client):
        self.client = client
        self.accepted = False
        self.finished = False

    def accept(self):
        if self.finished:
            raise RuntimeError("api lease already finished")
        self.accepted = True


@contextmanager
def borrow(source, factory, *, fresh=False):
    context = _current.get()
    if context is None or context.source != _digest(source):
        with factory(source["id"], "api") as client:
            yield Lease(client)
        return
    context.confirm()
    created = pool._clock()
    candidate = factory(source["id"], "api")
    if not isinstance(candidate, NetworkClient):
        # Preserve deliberately minimal provider test doubles and independent
        # readers. Only the concrete guarded HTTP client may enter this pool.
        with candidate as client:
            context.confirm()
            yield Lease(client)
            context.confirm()
        return
    ticket, entry, client, lease = None, None, candidate, None
    try:
        context.confirm()
        ticket, entry = pool.begin(context.key, fresh=fresh)
        if entry is not None:
            try:
                # Preflight DNS is separate from the later HTTP observation;
                # status=null distinguishes validation without an HTTP read.
                with observe_request(candidate.snapshot):
                    reusable = entry.client.can_reuse(candidate, source["api_base_url"])
                context.confirm()
                if reusable:
                    client, created = entry.client, entry.created
                    if hasattr(candidate, "_online_health_context"):
                        client._online_health_context = candidate._online_health_context
                    _close(candidate)
                    candidate = None
                else:
                    _close(entry.client)
            except BaseException:
                _close(entry.client)
                raise
        context.confirm()
        lease = Lease(client)
        yield lease
        context.confirm()
        if (ticket is not None and lease.accepted
                and client.reusable_for(source["api_base_url"])):
            returned = pool._clock()
            pool.finish(ticket, _Entry(client, created, returned))
            if client is candidate:
                candidate = None
            ticket, client = None, None
    finally:
        if lease is not None:
            lease.finished = True
        if ticket is not None:
            pool.finish(ticket)
        if client is not None:
            _close(client)
        if candidate is not None and candidate is not client:
            _close(candidate)
