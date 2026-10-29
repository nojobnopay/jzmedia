"""Small successful taxonomy snapshots for explicitly scoped foreground reads.

Only normalized category identifiers and labels enter this process-local cache.
The caller owns access/configuration validation and retains its original I/O
deadline. Optional cache failures never replace a catalogue result; validation
and cancellation errors remain hard boundaries, including after cache hooks.
"""

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import math
import re
import threading
import time

from ..network.guard import NetworkOperationError, current_operation


MAX_RECORDS = 32
MAX_BYTES = 512 * 1024
MAX_CATEGORIES = 256
TTL_SECONDS = 300
_KEY = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_current = ContextVar("online_foreground_taxonomy_cache", default=None)


def _body(categories):
    if not isinstance(categories, list) or not 1 <= len(categories) <= MAX_CATEGORIES:
        return None
    known = set()
    for row in categories:
        if not isinstance(row, dict) or set(row) != {"id", "name", "parent_id"}:
            return None
        identity, name, parent = row["id"], row["name"], row["parent_id"]
        if (not isinstance(identity, str) or not _ID.fullmatch(identity) or identity in known
                or not isinstance(parent, str) or parent and not _ID.fullmatch(parent)
                or not isinstance(name, str) or not name.strip() or len(name) > 128
                or _CONTROL.search(name) or "://" in name):
            return None
        known.add(identity)
    parents = {row["id"]: row["parent_id"] for row in categories}
    if any(parent and parent not in known for parent in parents.values()):
        return None
    for identity in parents:
        seen = set()
        while identity:
            if identity in seen:
                return None
            seen.add(identity)
            identity = parents[identity]
    return json.dumps(categories, ensure_ascii=False, separators=(",", ":")).encode()


class TaxonomyCache:
    def __init__(self, *, clock=time.monotonic, max_entries=MAX_RECORDS,
                 max_bytes=MAX_BYTES, ttl=TTL_SECONDS):
        if (type(max_entries) is not int or not 1 <= max_entries <= MAX_RECORDS
                or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BYTES
                or type(ttl) not in {int, float} or not math.isfinite(ttl) or not 0 < ttl <= TTL_SECONDS):
            raise ValueError("invalid taxonomy cache limits")
        self.clock, self.max_entries, self.max_bytes, self.ttl = clock, max_entries, max_bytes, ttl
        self._items = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def _expire(self):
        now = self.clock()
        for key, (expires, body) in tuple(self._items.items()):
            if expires <= now:
                self._items.pop(key)
                self._bytes -= len(body)

    def get(self, key):
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            return None
        with self._lock:
            self._expire()
            entry = self._items.get(key)
            if entry is None:
                return None
            self._items.move_to_end(key)
            # Reading changes LRU position, never the original expiry.
            return json.loads(entry[1])

    def put(self, key, categories):
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            return False
        body = _body(categories)
        with self._lock:
            self._expire()
            previous = self._items.pop(key, None)
            if previous is not None:
                self._bytes -= len(previous[1])
            if body is None or len(body) > self.max_bytes:
                return False
            self._items[key] = self.clock() + self.ttl, body
            self._bytes += len(body)
            while len(self._items) > self.max_entries or self._bytes > self.max_bytes:
                _key, (_expires, removed) = self._items.popitem(last=False)
                self._bytes -= len(removed)
        return True

    def remove(self, key):
        with self._lock:
            previous = self._items.pop(key, None)
            if previous is not None:
                self._bytes -= len(previous[1])

    def clear(self):
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def stats(self):
        with self._lock:
            self._expire()
            return {"entries": len(self._items), "bytes": self._bytes}


cache = TaxonomyCache()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class _Context:
    key: str
    source_version: str
    check: object
    pending: bytes | None = None

    def confirm(self):
        operation = current_operation()
        if operation is not None:
            operation.check()
        self.check()
        if operation is not None:
            operation.check()


def _optional(function, *args):
    try:
        return function(*args)
    except Exception as error:
        # The real cache performs no authorization or I/O. Preserve any
        # categorized boundary nevertheless, including injected hook errors.
        if isinstance(error, NetworkOperationError) or getattr(error, "category", None) is not None:
            raise
        return None


@contextmanager
def foreground(access, source, configuration, check):
    """Opt in one foreground browse through its completed public result.

    The registry retains neither the access record, source nor callback. Only
    their opaque partition digest and normalized category bytes are stored.
    """
    if not isinstance(configuration, str) or not _KEY.fullmatch(configuration) or not callable(check):
        raise ValueError("invalid taxonomy cache context")
    context = _Context(_digest(["taxonomy-v1", access.owner, access.generation, access.protection_revision,
                              source["id"], source.get("provider_options", {}), configuration]),
                       _digest(source), check)
    token = _current.set(context)
    try:
        context.confirm()
        yield
        context.confirm()
        if context.pending is not None:
            try:
                _optional(cache.put, context.key, json.loads(context.pending))
                # Keep access/configuration checks outside the optional hook's
                # catch: a cache failure cannot turn revoked reads into success.
                context.confirm()
            except BaseException:
                try:
                    cache.remove(context.key)
                except Exception:
                    pass
                raise
    finally:
        _current.reset(token)


def get(source):
    context = _current.get()
    if context is None:
        return None
    context.confirm()
    if context.source_version != _digest(source):
        return None
    result = _optional(cache.get, context.key)
    if result is not None and _optional(_body, result) is None:
        result = None
    context.confirm()
    return result


def stage(source, categories):
    context = _current.get()
    if context is None:
        return
    context.confirm()
    if context.source_version == _digest(source):
        # Freeze only known-valid, bounded metadata until the whole foreground
        # catalogue/artwork preparation and its final guards have succeeded.
        body = _optional(_body, categories)
        if body is not None and len(body) <= MAX_BYTES:
            context.pending = body
    context.confirm()
