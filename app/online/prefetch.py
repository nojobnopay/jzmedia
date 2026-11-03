"""Bounded session-local HLS downloads, coalescing and forward prefetch.

Only complete, validated responses become cache entries. The injected reader
owns network validation; every download owns a normal registry byte lease.
"""

from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
import threading
import time

from .sessions import SessionError


@dataclass(frozen=True)
class CachedSegment:
    status: int
    headers: dict
    body: bytes


class CacheUnavailable(RuntimeError):
    """Foreground reads may fall back to the ordinary streaming gateway."""


@dataclass(eq=False)
class _Entry:
    session: object
    resource: object
    lease: object
    future: Future
    reserved: int
    waiters: int = 0
    obsolete: bool = False
    prefetched: bool = False
    started: float = 0.0

    @property
    def key(self):
        return self.session.id, self.resource.id


@dataclass(eq=False)
class Ticket:
    future: Future
    cached: bool
    _entry: _Entry
    _released: bool = False


class CacheStore:
    def __init__(self, registry, fetch, submit):
        self.registry, self.fetch, self.submit = registry, fetch, submit
        limits = registry.limits
        self._seconds = getattr(limits, "prefetch_seconds", 90.0)
        self._segment_limit = getattr(limits, "prefetch_max_segments", 24)
        self._session_limit = getattr(limits, "prefetch_session_bytes", 64 * 1024**2)
        self._global_limit = getattr(limits, "prefetch_global_bytes", 128 * 1024**2)
        self._entry_limit = getattr(limits, "prefetch_max_entries", 256)
        # Registry cancellation callbacks run outside its lock. RLock also lets
        # registry.sweep synchronously close this same session during admission.
        self._lock = threading.RLock()
        self._entries = OrderedDict()
        self._timelines = OrderedDict()
        self._windows = OrderedDict()
        self._failed = set()
        self._foreground_until = 0.0
        self._hits = self._misses = self._merged = self._completed = 0
        self._download_bytes = self._failures = self._cancelled = 0
        self._download_seconds = 0.0

    @staticmethod
    def _resource_id(path):
        return path.rsplit("/", 1)[-1]

    def remember(self, session, manifest_id, segments):
        """Record validated ordering only; registration never starts work."""
        with self._lock:
            if session.cancelled.is_set():
                return
            key = session.id, manifest_id
            self._timelines[key] = (session, tuple(segments))
            self._timelines.move_to_end(key)
            # Repeated references cannot grow metadata independently of the
            # registry's resource budget, even when every manifest reuses URLs.
            while (len(self._timelines) > 256
                   or sum(len(value[1]) for value in self._timelines.values())
                   > self.registry.limits.max_global_resources
                   or sum(len(value[1]) for value in self._timelines.values() if value[0] is session)
                   > self.registry.limits.max_resources):
                session_full = sum(len(value[1]) for value in self._timelines.values()
                                   if value[0] is session) > self.registry.limits.max_resources
                removed = next(key for key, value in self._timelines.items()
                               if not session_full or value[0] is session)
                self._timelines.pop(removed)
                self._windows.pop(removed, None)

    def _discard_locked(self, entry):
        if self._entries.get(entry.key) is entry:
            self._entries.pop(entry.key)

    def set_length(self, session, resource, lease, length):
        """Shrink a live cache reservation after validated response headers.

        The upstream byte lease keeps its original budget. Only this cache's
        memory reservation changes; no partial response becomes readable.
        Stale workers cannot resize a replacement entry with the same key.
        """
        with self._lock:
            entry = self._entries.get((session.id, resource.id))
            if (entry is None or entry.session is not session or entry.resource is not resource
                    or entry.lease is not lease or entry.future.done() or entry.obsolete
                    or session.cancelled.is_set() or lease.cancelled.is_set()):
                return False
            if (type(length) is not int or not 0 <= length <= self.registry.response_limit(resource.kind)
                    or length > entry.reserved):
                raise CacheUnavailable("online_cache_length")
            entry.reserved = length
        self._pump()
        return True

    def _desired_locked(self):
        return {key for window in self._windows.values() for key in window}

    def _anchor_locked(self, session, resource):
        if resource.kind != "segment":
            return
        matches = []
        for key, (_owner, segments) in self._timelines.items():
            if key[0] == session.id:
                matches.extend((key, segments, index) for index, segment in enumerate(segments)
                               if self._resource_id(segment.path) == resource.id)
        if len(matches) != 1:
            return  # Repeated paths do not identify a unique play position.
        timeline_key, segments, position = matches[0]
        desired, elapsed, count = [], 0.0, 0
        for segment in segments[position + 1:]:
            if elapsed >= self._seconds or count >= self._segment_limit:
                break
            elapsed += segment.duration
            count += 1
            if segment.gap:
                continue
            if segment.init_path is not None:
                desired.append((session.id, self._resource_id(segment.init_path)))
            desired.append((session.id, self._resource_id(segment.path)))
        self._windows[timeline_key] = tuple(dict.fromkeys(desired))
        self._windows.move_to_end(timeline_key)
        wanted = self._desired_locked()
        for entry in list(self._entries.values()):
            if (entry.session is session and not entry.future.done() and not entry.waiters
                    and entry.resource.id != resource.id
                    and entry.lease.background and entry.key not in wanted):
                entry.obsolete = True
                entry.lease.abort()

    def _size_locked(self, session_id=None):
        return sum(entry.reserved for entry in self._entries.values()
                   if session_id is None or entry.session.id == session_id)

    def _room_locked(self, session, size, foreground):
        if size > min(self._session_limit, self._global_limit):
            return False
        wanted = self._desired_locked() if not foreground else set()
        while (self._size_locked() + size > self._global_limit
               or self._size_locked(session.id) + size > self._session_limit
               or len(self._entries) >= self._entry_limit):
            session_full = self._size_locked(session.id) + size > self._session_limit
            victim = next((entry for entry in self._entries.values()
                           if entry.future.done() and not entry.waiters and entry.key not in wanted
                           and (not session_full or entry.session is session)), None)
            if victim is None:
                return False
            self._discard_locked(victim)
        return True

    def _new_locked(self, session, resource, background):
        size = self.registry.response_limit(resource.kind)
        if not self._room_locked(session, size, foreground=not background):
            raise CacheUnavailable("online_cache_capacity")
        active = [entry for entry in self._entries.values()
                  if entry.session is session and not entry.future.done()]
        if len(active) >= 2:
            if not background:
                victim = next((entry for entry in reversed(active)
                               if entry.lease.background and not entry.waiters), None)
                if victim is not None:
                    victim.obsolete = True
                    victim.lease.abort()
            raise SessionError("prefetch_busy")
        lease = self.registry.acquire(session, size, kind=resource.kind, background=background)
        entry = _Entry(session, resource, lease, Future(), size, prefetched=background)
        self._entries[entry.key] = entry
        return entry

    def demand(self, session, resource):
        if resource.kind not in {"segment", "map"}:
            raise CacheUnavailable("online_cache_resource")
        self.registry.check(session)
        launch = []
        with self._lock:
            waiting = [entry for entry in self._entries.values() if entry.waiters]
            limits = self.registry.limits
            if (sum(entry.waiters for entry in waiting) >= limits.max_active
                    or sum(entry.waiters for entry in waiting if entry.session is session)
                    >= limits.max_session_active
                    or sum(entry.waiters for entry in waiting
                           if entry.session.source_id == session.source_id) >= limits.max_source_active):
                raise SessionError("request_limit")
            self._anchor_locked(session, resource)
            key = session.id, resource.id
            self._failed.discard(key)
            entry = self._entries.get(key)
            if entry is not None:
                if entry.obsolete or entry.lease.cancelled.is_set():
                    self._foreground_until = time.monotonic() + .5
                    raise SessionError("prefetch_busy")
                cached = entry.future.done()
                if not cached and entry.lease.background:
                    self.registry.promote(entry.lease)
                self._hits += int(cached)
                self._merged += int(not cached)
                self._entries.move_to_end(key)
            else:
                try:
                    entry = self._new_locked(session, resource, False)
                except (SessionError, CacheUnavailable):
                    self._foreground_until = time.monotonic() + .5
                    raise
                self._misses += 1
                cached = False
                launch.append(entry)
            entry.waiters += 1
            self._foreground_until = 0.0
            ticket = Ticket(entry.future, cached, entry)
        self._launch(launch)
        self._pump()
        return ticket

    def release(self, ticket):
        with self._lock:
            if ticket._released:
                return
            ticket._released = True
            entry = ticket._entry
            entry.waiters = max(0, entry.waiters - 1)
            if not entry.waiters:
                if not entry.future.done():
                    entry.obsolete = True
                    entry.lease.abort()
                elif entry.obsolete:
                    self._discard_locked(entry)
        self._pump()

    def close_session(self, session):
        with self._lock:
            for key in list(self._timelines):
                if key[0] == session.id:
                    self._timelines.pop(key, None)
                    self._windows.pop(key, None)
            self._failed = {key for key in self._failed if key[0] != session.id}
            for entry in list(self._entries.values()):
                if entry.session is session:
                    entry.obsolete = True
                    if not entry.future.done():
                        entry.lease.abort()
                    elif not entry.waiters:
                        self._discard_locked(entry)

    def _launch(self, entries):
        for entry in entries:
            try:
                self.submit(lambda entry=entry: self._run(entry))
            except Exception as error:
                self._finish(entry, error=error)

    def _run(self, entry):
        entry.started = time.monotonic()
        try:
            entry.lease.check()
            result = self.fetch(entry.session, entry.resource, entry.lease)
            entry.lease.check()
            if (not isinstance(result, CachedSegment) or not isinstance(result.body, bytes)
                    or len(result.body) > entry.reserved):
                raise CacheUnavailable("online_cache_response")
        except Exception as error:
            self._finish(entry, error=error)
        else:
            self._finish(entry, result=result)

    def _finish(self, entry, result=None, error=None):
        with self._lock:
            if entry.obsolete or entry.session.cancelled.is_set() or entry.lease.cancelled.is_set():
                error = error or SessionError("request_cancelled")
            if error is None:
                entry.reserved = len(result.body)
                self._completed += int(entry.prefetched)
                self._download_bytes += len(result.body)
                self._download_seconds += max(0.0, time.monotonic() - entry.started)
                entry.future.set_result(result)
            else:
                cancelled = (entry.obsolete or entry.session.cancelled.is_set()
                             or entry.lease.cancelled.is_set())
                self._cancelled += int(cancelled)
                self._failures += int(not cancelled)
                self._discard_locked(entry)
                if entry.prefetched and not entry.obsolete and not entry.session.cancelled.is_set():
                    self._failed.add(entry.key)
                entry.future.set_exception(error)
        entry.lease.close()
        self._pump()

    def _pump(self):
        launch = []
        with self._lock:
            if time.monotonic() < self._foreground_until:
                return
            attempted = set()
            for timeline_key, window in list(self._windows.items()):
                remembered = self._timelines.get(timeline_key)
                if remembered is None:
                    continue
                session = remembered[0]
                for key in window:
                    if key in attempted or key in self._entries or key in self._failed:
                        continue
                    attempted.add(key)
                    active = [entry for entry in self._entries.values()
                              if entry.session is session and not entry.future.done()]
                    foreground = any(entry.session is session and entry.waiters
                                     for entry in self._entries.values())
                    if (len(active) >= 2 or sum(entry.lease.background for entry in active)
                            >= (1 if foreground else 2)):
                        break
                    try:
                        resource = self.registry.resource(session, key[1])
                        if resource.kind not in {"segment", "map"}:
                            continue
                        entry = self._new_locked(session, resource, True)
                    except (SessionError, CacheUnavailable):
                        break
                    launch.append(entry)
        self._launch(launch)

    def stats(self):
        with self._lock:
            return {"hits": self._hits, "misses": self._misses, "merged": self._merged,
                    "prefetch_completed": self._completed, "bytes": self._size_locked(),
                    "download_bytes": self._download_bytes,
                    "download_seconds": round(self._download_seconds, 6),
                    "failures": self._failures, "cancelled": self._cancelled,
                    "entries": len(self._entries),
                    "active": sum(not entry.future.done() for entry in self._entries.values()),
                    "timelines": len(self._timelines)}
