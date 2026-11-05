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


_RETRYABLE_ERRORS = frozenset({"network_timeout", "network_request_failed", "operation_timeout"})
_RETRY_COOLDOWN_SECONDS = 1.0


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
    def __init__(self, registry, fetch, submit, *, clock=time.monotonic):
        if not callable(clock):
            raise ValueError("invalid_cache_clock")
        self.registry, self.fetch, self.submit = registry, fetch, submit
        self._clock = clock
        limits = registry.limits
        self._seconds = getattr(limits, "prefetch_seconds", 600.0)
        self._segment_limit = getattr(limits, "prefetch_max_segments", 600)
        self._session_limit = getattr(limits, "prefetch_session_bytes", 256 * 1024**2)
        self._global_limit = getattr(limits, "prefetch_global_bytes", 512 * 1024**2)
        self._entry_limit = getattr(limits, "prefetch_max_entries", 1280)
        # Registry cancellation callbacks run outside its lock. RLock also lets
        # registry.sweep synchronously close this same session during admission.
        self._lock = threading.RLock()
        self._entries = OrderedDict()
        self._timelines = OrderedDict()
        self._windows = OrderedDict()
        self._masters = OrderedDict()
        self._failed = set()
        # Pending retries only belong to current windows. The spent budget is
        # session-long and bounded by the registry's resource inventory, so a
        # foreground recovery or seek cannot give a key infinite BG attempts.
        self._retry_at = {}
        self._retried = set()
        self._automatic_retries = 0
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
            segments = tuple(segments)
            previous = self._timelines.get(key)
            if previous is not None and previous[1] != segments:
                self._windows.pop(key, None)
            self._timelines[key] = (session, segments)
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
            self._retire_retries_locked()

    def remember_master(self, session, manifest_id, paths):
        """Record mutually exclusive roles from a fully validated master.

        Only siblings of the same master and role can retire each other.
        Unknown or conflicting relationships remain independent.
        """
        with self._lock:
            if session.cancelled.is_set():
                return
            key = session.id, manifest_id
            self._masters[key] = tuple((self._resource_id(path), role) for path, role in paths)
            self._masters.move_to_end(key)
            while (len(self._masters) > 256
                   or sum(len(paths) for paths in self._masters.values()) > self.registry.limits.max_global_resources
                   or sum(len(paths) for (owner, _), paths in self._masters.items() if owner == session.id)
                   > self.registry.limits.max_resources):
                session_full = sum(len(paths) for (owner, _), paths in self._masters.items()
                                   if owner == session.id) > self.registry.limits.max_resources
                removed = next(key for key in self._masters if not session_full or key[0] == session.id)
                self._masters.pop(removed)

    def _roles_locked(self, timeline_key):
        session_id, manifest_id = timeline_key
        return {(master_id, role) for (owner, master_id), paths in self._masters.items()
                if owner == session_id for path, role in paths if path == manifest_id}

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

    def _retire_retries_locked(self):
        wanted = self._desired_locked()
        self._retry_at = {key: due for key, due in self._retry_at.items() if key in wanted}

    def _cancel_unwanted_locked(self, session, resource):
        wanted = self._desired_locked()
        for entry in list(self._entries.values()):
            if (entry.session is session and not entry.future.done() and not entry.waiters
                    and entry.resource.id != resource.id
                    and entry.lease.background and entry.key not in wanted):
                entry.obsolete = True
                entry.lease.abort()

    def _anchor_locked(self, session, resource):
        if resource.kind != "segment":
            return
        matches = []
        for key, (_owner, segments) in self._timelines.items():
            if key[0] == session.id:
                matches.extend((key, segments, index) for index, segment in enumerate(segments)
                               if self._resource_id(segment.path) == resource.id)
        if len(matches) != 1:
            # A repeated resource cannot identify the requested play position.
            # Stop speculative work for those ambiguous timelines rather than
            # continuing to download an obsolete window after a seek.
            for key, _segments, _position in matches:
                self._windows.pop(key, None)
            self._retire_retries_locked()
            self._cancel_unwanted_locked(session, resource)
            return
        timeline_key, segments, position = matches[0]
        roles = self._roles_locked(timeline_key)
        if len(roles) == 1:
            for other in list(self._windows):
                if other != timeline_key and other[0] == session.id and self._roles_locked(other) == roles:
                    self._windows.pop(other, None)
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
        self._retire_retries_locked()
        self._cancel_unwanted_locked(session, resource)

    def _size_locked(self, session_id=None):
        return sum(entry.reserved for entry in self._entries.values()
                   if session_id is None or entry.session.id == session_id)

    def _room_locked(self, session, size, foreground, key):
        """Plan evictions without changing cache state before admission."""
        if size > min(self._session_limit, self._global_limit):
            return None
        ranks = {}
        for window in self._windows.values():
            for index, wanted in enumerate(window):
                ranks[wanted] = min(index, ranks.get(wanted, index))
        priority = ranks.get(key, float("inf"))
        candidates = [entry for entry in self._entries.values()
                      if entry.future.done() and not entry.waiters
                      and (foreground or ranks.get(entry.key, float("inf")) > priority)]
        if not foreground:
            # Preserve the nearer portion of every window before speculative
            # distant data, including when another track or session arrives.
            candidates.sort(key=lambda entry: ranks.get(entry.key, float("inf")), reverse=True)
        total, owned, count = self._size_locked(), self._size_locked(session.id), len(self._entries)
        victims = []
        while (total + size > self._global_limit or owned + size > self._session_limit
               or count >= self._entry_limit):
            session_full = owned + size > self._session_limit
            victim = next((entry for entry in candidates if not session_full or entry.session is session), None)
            if victim is None:
                return None
            candidates.remove(victim)
            victims.append(victim)
            total -= victim.reserved
            owned -= victim.reserved if victim.session is session else 0
            count -= 1
        return victims

    def _new_locked(self, session, resource, background):
        size = self.registry.response_limit(resource.kind)
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
        victims = self._room_locked(session, size, foreground=not background, key=(session.id, resource.id))
        if victims is None:
            raise CacheUnavailable("online_cache_capacity")
        lease = self.registry.acquire(session, size, kind=resource.kind, background=background)
        for victim in victims:
            self._discard_locked(victim)
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
            self._retry_at.pop(key, None)
            entry = self._entries.get(key)
            if entry is not None:
                if entry.obsolete or entry.lease.cancelled.is_set():
                    self._foreground_until = self._clock() + .5
                    raise SessionError("prefetch_busy")
                cached = entry.future.done()
                if not cached and entry.lease.background:
                    try:
                        self.registry.promote(entry.lease)
                    except SessionError as error:
                        if error.category not in {"request_cancelled", "request_timeout"}:
                            raise
                        # Another foreground request can preempt this lease
                        # after our cancellation check. Retry the same resource
                        # once its worker exits, but never revive a closed or
                        # expired session or release its slot ahead of cleanup.
                        self.registry.check(session)
                        entry.obsolete = True
                        entry.lease.abort()
                        self._foreground_until = self._clock() + .5
                        raise SessionError("prefetch_busy") from None
                self._hits += int(cached)
                self._merged += int(not cached)
                self._entries.move_to_end(key)
            else:
                try:
                    entry = self._new_locked(session, resource, False)
                except (SessionError, CacheUnavailable):
                    self._foreground_until = self._clock() + .5
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
            for key in list(self._masters):
                if key[0] == session.id:
                    self._masters.pop(key, None)
            for key in list(self._timelines):
                if key[0] == session.id:
                    self._timelines.pop(key, None)
                    self._windows.pop(key, None)
            self._failed = {key for key in self._failed if key[0] != session.id}
            self._retry_at = {key: due for key, due in self._retry_at.items() if key[0] != session.id}
            self._retried = {key for key in self._retried if key[0] != session.id}
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
        entry.started = self._clock()
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
                self._failed.discard(entry.key)
                self._retry_at.pop(entry.key, None)
                entry.reserved = len(result.body)
                self._completed += int(entry.prefetched)
                self._download_bytes += len(result.body)
                self._download_seconds += max(0.0, self._clock() - entry.started)
                entry.future.set_result(result)
            else:
                cancelled = (entry.obsolete or entry.session.cancelled.is_set()
                             or entry.lease.cancelled.is_set())
                self._cancelled += int(cancelled)
                self._failures += int(not cancelled)
                self._discard_locked(entry)
                if entry.prefetched and not entry.obsolete and not entry.session.cancelled.is_set():
                    self._failed.add(entry.key)
                    if (entry.lease.background and not cancelled
                            and getattr(error, "category", None) in _RETRYABLE_ERRORS
                            and entry.key not in self._retried and entry.key in self._desired_locked()):
                        self._retry_at[entry.key] = self._clock() + _RETRY_COOLDOWN_SECONDS
                    else:
                        self._retry_at.pop(entry.key, None)
                entry.future.set_exception(error)
        entry.lease.close()
        self._pump()

    def maintain(self):
        """Existing application maintenance supplies wakeups; never a timer."""
        self._pump()

    def _pump(self):
        launch = []
        with self._lock:
            self._retire_retries_locked()
            now = self._clock()
            if now < self._foreground_until:
                return
            blocked = set()
            while True:
                candidates = []
                for timeline_key, window in list(self._windows.items()):
                    remembered = self._timelines.get(timeline_key)
                    if remembered is None or timeline_key in blocked:
                        continue
                    for index, key in enumerate(window):
                        if key in self._entries:
                            continue
                        retry = key in self._failed
                        if retry:
                            due = self._retry_at.get(key)
                            if due is None:
                                continue
                            if now < due:
                                break
                        candidates.append((index, timeline_key, key, remembered[0], retry))
                        break
                if not candidates:
                    break
                # Closer missing fragments take precedence. Stable ordering
                # rotates after each admission so equally close tracks share
                # the available lanes instead of filling one entire window.
                _index, timeline_key, key, session, retry = min(candidates, key=lambda item: item[0])
                active = [entry for entry in self._entries.values()
                          if entry.session is session and not entry.future.done()]
                foreground = any(entry.session is session and entry.waiters
                                 for entry in self._entries.values())
                if (len(active) >= 2 or sum(entry.lease.background for entry in active)
                        >= (1 if foreground else 2)):
                    blocked.add(timeline_key)
                    continue
                try:
                    resource = self.registry.resource(session, key[1])
                    if resource.kind not in {"segment", "map"}:
                        blocked.add(timeline_key)
                        continue
                    entry = self._new_locked(session, resource, True)
                except (SessionError, CacheUnavailable):
                    blocked.add(timeline_key)
                    continue
                if retry:
                    self._retried.add(key)
                    self._retry_at.pop(key, None)
                    self._failed.discard(key)
                    self._automatic_retries += 1
                launch.append(entry)
                self._windows.move_to_end(timeline_key)
        self._launch(launch)

    def stats(self):
        with self._lock:
            return {"hits": self._hits, "misses": self._misses, "merged": self._merged,
                    "prefetch_completed": self._completed, "bytes": self._size_locked(),
                    "download_bytes": self._download_bytes,
                    "download_seconds": round(self._download_seconds, 6),
                    "failures": self._failures, "cancelled": self._cancelled,
                    "entries": len(self._entries),
                    "waiters": sum(entry.waiters for entry in self._entries.values()),
                    "active": sum(not entry.future.done() for entry in self._entries.values()),
                    "retry_pending": len(self._retry_at), "retry_used": len(self._retried),
                    "automatic_retries": self._automatic_retries,
                    "timelines": len(self._timelines)}
