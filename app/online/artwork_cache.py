"""Bounded image bytes and synchronous request coalescing, without I/O workers."""

from collections import OrderedDict
from dataclasses import dataclass, field
import threading
import time


MAX_CACHE_BYTES = 16 * 1024**2
MAX_CACHE_RECORDS = 512
MAX_CACHE_SECONDS = 600
MAX_PENDING_READS = 64
MAX_IMAGE_BYTES = 512 * 1024


class ArtworkCacheBusy(RuntimeError):
    pass


@dataclass
class _Flight:
    done: threading.Event = field(default_factory=threading.Event)
    error: tuple[str, int] | None = None


class ArtworkCache:
    """Only successful validated bytes persist; callers retain authorization.

    A producer uses its existing request thread and deadline. A cancelled
    producer publishes no error, so another authorized waiter may take over
    after it has closed its reader. Waiters never own the producer's sockets.
    """

    def __init__(self, *, capacity=MAX_CACHE_RECORDS, byte_budget=MAX_CACHE_BYTES,
                 ttl=MAX_CACHE_SECONDS, pending_limit=MAX_PENDING_READS,
                 clock=time.monotonic):
        if (type(capacity) is not int or not 1 <= capacity <= MAX_CACHE_RECORDS
                or type(byte_budget) is not int or not 1 <= byte_budget <= MAX_CACHE_BYTES
                or type(ttl) not in {int, float} or not 0 < ttl <= MAX_CACHE_SECONDS
                or type(pending_limit) is not int or not 1 <= pending_limit <= MAX_PENDING_READS):
            raise ValueError("invalid artwork cache limits")
        self._capacity, self._byte_budget, self._ttl = capacity, byte_budget, ttl
        self._pending_limit, self._clock = pending_limit, clock
        self._entries = OrderedDict()
        self._bytes = 0
        self._pending = 0
        self._flights = {}
        self._lock = threading.Lock()

    def _remove(self, key):
        _created, result = self._entries.pop(key)
        self._bytes -= len(result[0])

    def _prune(self, now):
        for key, (created, _result) in tuple(self._entries.items()):
            if now - created >= self._ttl:
                self._remove(key)

    def _lookup(self, key, now):
        self._prune(now)
        cached = self._entries.get(key)
        if cached is not None:
            self._entries.move_to_end(key)
            return cached[1]
        return None

    def _store(self, key, result, now):
        if (not isinstance(result, tuple) or len(result) != 2 or type(result[0]) is not bytes
                or not 0 < len(result[0]) <= MAX_IMAGE_BYTES
                or type(result[1]) is not str
                or result[1] not in {"image/jpeg", "image/png", "image/webp"}):
            raise ValueError("invalid artwork cache body")
        self._prune(now)
        if len(result[0]) > self._byte_budget:
            return
        if key in self._entries:
            self._remove(key)
        while self._entries and (len(self._entries) >= self._capacity
                                 or self._bytes + len(result[0]) > self._byte_budget):
            self._remove(next(iter(self._entries)))
        self._entries[key] = (now, result)
        self._bytes += len(result[0])

    def acquire(self, key):
        """Return cached bytes or one bounded participation in a live read."""
        with self._lock:
            cached = self._lookup(key, self._clock())
            if cached is not None:
                return cached, None, False
            if self._pending >= self._pending_limit:
                raise ArtworkCacheBusy("online_artwork_busy")
            self._pending += 1
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = self._flights[key] = _Flight()
            return None, flight, leader

    def complete(self, key, flight, *, result=None, error=None):
        """Publish only after the producer has closed its upstream reader."""
        with self._lock:
            try:
                if result is not None:
                    self._store(key, result, self._clock())
                flight.error = error
            finally:
                if self._flights.get(key) is flight:
                    del self._flights[key]
                flight.done.set()

    def release(self):
        with self._lock:
            self._pending -= 1

    def clear(self):
        # Do not remove active flights: doing so could start duplicate readers.
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self):
        """Small internal diagnostics; no URLs, keys or image bytes escape."""
        with self._lock:
            return {"bytes": self._bytes, "entries": len(self._entries),
                    "inflight": len(self._flights), "pending": self._pending,
                    "waiters": self._pending - len(self._flights)}
