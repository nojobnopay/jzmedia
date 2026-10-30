"""Short exclusive image connections, independent of API and media readers.

The artwork admission semaphore owns the existing two actual readers. This
registry only retains their successfully completed connections. Every borrow
still creates the current guarded client and revalidates DNS and TLS trust.
"""

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import threading
import time

from ..network.client import NetworkClient, NetworkRequestError
from ..network.guard import current_operation
from ..network.stages import observe_request
from ..network.targets import TargetPolicy


MAX_IDLE = 4
MAX_ACTIVE = 2
MAX_IDLE_PER_KEY = 2
IDLE_SECONDS = 5
LIFETIME_SECONDS = 30


def _close(client):
    try:
        client.close()
    except Exception:
        # Closing an optimization must neither replace the original failure
        # nor export upstream addresses, trust state or credentials.
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
        for token, (_key, entry) in tuple(self._idle.items()):
            if self._expired(entry, now):
                self._idle.pop(token)
                removed.append(entry.client)
        return removed

    def begin(self, key):
        with self._lock:
            removed = self._collect(self._clock())
            ticket, entry = None, None
            if self._enabled and len(self._active) < MAX_ACTIVE:
                ticket = object()
                self._active[ticket] = self._generation, key
                for token, (candidate, value) in reversed(tuple(self._idle.items())):
                    if candidate == key:
                        self._idle.pop(token)
                        entry = value
                        break
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
                    matching = [token for token, (candidate, _value) in self._idle.items() if candidate == key]
                    while len(matching) >= MAX_IDLE_PER_KEY:
                        removed.append(self._idle.pop(matching.pop(0))[1].client)
                    self._idle[object()] = key, entry
                    while len(self._idle) > MAX_IDLE:
                        removed.append(self._idle.popitem(last=False)[1][1].client)
                else:
                    removed.append(entry.client)
        for client in removed:
            _close(client)

    def discard(self, key):
        with self._lock:
            removed = []
            for token, (candidate, entry) in tuple(self._idle.items()):
                if candidate == key:
                    self._idle.pop(token)
                    removed.append(entry.client)
            # The worker remains the sole owner of active I/O and closes it on
            # exit. A failure cannot let a sibling return this retired scope.
            for ticket, (_generation, active_key) in tuple(self._active.items()):
                if active_key == key:
                    self._active[ticket] = -1, active_key
        for client in removed:
            _close(client)

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
            removed = [entry.client for _key, entry in self._idle.values()]
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


def _confirm(check):
    operation = current_operation()
    if operation is not None:
        operation.check()
    check()
    if operation is not None:
        operation.check()


def _key(access, source, configuration, url):
    origin = TargetPolicy(source["id"], source["allowed_hosts"])._canonical_url(url)[4]
    values = ["image-connections-v1", access.owner, access.generation,
              access.protection_revision, source["id"], configuration, origin]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


class Lease:
    def __init__(self, client):
        self.client = client
        self.accepted = False
        self.finished = False

    def accept(self, response):
        if self.finished:
            raise RuntimeError("image lease already finished")
        self.accepted = (self.client.accept_image_response(response)
                         if isinstance(self.client, NetworkClient) else True)


@contextmanager
def borrow(access, source, configuration, url, check, factory):
    if (not callable(check) or not isinstance(configuration, str) or len(configuration) != 64
            or any(char not in "0123456789abcdef" for char in configuration)):
        raise ValueError("invalid image pool scope")
    _confirm(check)
    created = pool._clock()
    candidate = factory(source["id"], "image")
    if not isinstance(candidate, NetworkClient):
        # Preserve deliberately minimal guarded-reader substitutes in isolated
        # provider tests. Concrete clients alone participate in this pool.
        with candidate as client:
            _confirm(check)
            lease = Lease(client)
            try:
                yield lease
                _confirm(check)
            finally:
                lease.finished = True
        return
    ticket, entry, client, lease, key = None, None, candidate, None, None
    try:
        _confirm(check)
        if candidate.snapshot.purpose != "image":
            raise NetworkRequestError("network_client_configuration_failed")
        key = _key(access, source, configuration, url)
        ticket, entry = pool.begin(key)
        if entry is not None:
            try:
                with observe_request(candidate.snapshot):
                    reusable = entry.client.can_reuse(candidate, url)
                _confirm(check)
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
        _confirm(check)
        lease = Lease(client)
        yield lease
        _confirm(check)
        if ticket is not None and lease.accepted and client.reusable_for(url):
            returned = pool._clock()
            pool.finish(ticket, _Entry(client, created, returned))
            if client is candidate:
                candidate = None
            ticket, client = None, None
    except BaseException:
        if key is not None:
            pool.discard(key)
        raise
    finally:
        if lease is not None:
            lease.finished = True
        if ticket is not None:
            pool.finish(ticket)
        if client is not None:
            _close(client)
        if candidate is not None and candidate is not client:
            _close(candidate)
