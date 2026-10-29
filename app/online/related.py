"""Optional same-source navigation with bounded metadata work and memory.

Recommendations never persist catalogue identities and never resolve or read
media. The foreground catalogue/playback worker can cancel this work while its
actual worker retains admission until its I/O and finally have ended.
"""

from collections import OrderedDict
import hashlib
import json
import threading
import time

from ..network.guard import NetworkOperation, NetworkOperationError, current_operation, operation_context
from ..store.online_network import load_online_network_state
from . import catalog, health, maccms, related_matching
from .providers import ProviderError


DEADLINE_SECONDS = 8
STEP_SECONDS = 3
MAX_CACHE_RECORDS = 32
MAX_CACHE_BYTES = 2 * 1024 * 1024
_SOFT_ERRORS = {"provider_timeout", "provider_network_error", "provider_upstream_error",
                "provider_not_found", "provider_rate_limited", "provider_auth_failed",
                "provider_response_limit", "provider_entry_limit", "invalid_provider_response", "provider_ambiguous_identity"}


class RelatedCache:
    def __init__(self, *, clock=time.monotonic, max_entries=MAX_CACHE_RECORDS, max_bytes=MAX_CACHE_BYTES, ttl=120):
        if (type(max_entries) is not int or not 1 <= max_entries <= MAX_CACHE_RECORDS
                or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_CACHE_BYTES
                or type(ttl) not in {int, float} or not 60 <= ttl <= 300):
            raise ValueError("invalid related cache limits")
        self.clock, self.max_entries, self.max_bytes, self.ttl = clock, max_entries, max_bytes, ttl
        self._lock = threading.Lock()
        self._items = OrderedDict()
        self._bytes = 0

    def _expire(self):
        now = self.clock()
        for key, (expires, body) in tuple(self._items.items()):
            if expires <= now:
                self._items.pop(key)
                self._bytes -= len(body)

    def _get(self, key):
        with self._lock:
            self._expire()
            entry = self._items.get(key)
            if entry is None:
                return None
            self._items.move_to_end(key)
            # Each reader owns its copy: artwork registration removes private
            # URL fields and must not mutate another request's cached snapshot.
            return json.loads(entry[1])

    def _put(self, key, value):
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > self.max_bytes:
            return
        with self._lock:
            self._expire()
            old = self._items.pop(key, None)
            if old is not None:
                self._bytes -= len(old[1])
            self._items[key] = self.clock() + self.ttl, body
            self._bytes += len(body)
            while len(self._items) > self.max_entries or self._bytes > self.max_bytes:
                _key, (_expires, removed) = self._items.popitem(last=False)
                self._bytes -= len(removed)

    def get(self, key):
        return self._get("result:" + key)

    def put(self, key, value):
        if value.get("status") == "ready" and value.get("partial", False) is False:
            self._put("result:" + key, value)

    def get_target(self, key):
        return self._get("target:" + key)

    def put_target(self, key, value):
        self._put("target:" + key, value)

    def clear_results(self):
        with self._lock:
            for key in tuple(self._items):
                if key.startswith("result:"):
                    _expires, body = self._items.pop(key)
                    self._bytes -= len(body)

    def clear(self):
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def stats(self):
        with self._lock:
            self._expire()
            return {"entries": len(self._items), "bytes": self._bytes}


cache = RelatedCache()


def supports_related(source):
    """A pure capability check; local fixtures may narrow this test seam."""
    return source.get("provider") == "maccms"


def _snapshot(access, source_id):
    catalog._ready_source(source_id, access)
    state = load_online_network_state(source_id)
    source = state["source_config"]
    if not state["online_enabled"] or not source or not source["enabled"]:
        raise catalog.CatalogError("online_source_disabled", 503)
    fingerprint = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return source, fingerprint


def _confirm(access, source_id, fingerprint):
    _source, current = _snapshot(access, source_id)
    if current != fingerprint:
        raise catalog.CatalogError("online_source_changed", 409)


def _cache_key(access, source_id, external_id, configuration):
    # A digest contains no configuration secrets and no reusable session token.
    return hashlib.sha256(json.dumps([access.owner, access.generation, access.protection_revision,
                                     source_id, external_id, configuration], separators=(",", ":")).encode()).hexdigest()


def _result(source, external_id, status, **groups):
    return {"source": catalog._public_source(source), "external_id": external_id,
            "current_season": None, "status": status, "partial": False,
            "series": [], "related": [], "name_related": [], **groups}


def _public(source, result):
    cards = [card for group in ("series", "related", "name_related") for card in result[group]]
    catalog._artwork(source, {"items": cards})
    return result


def _detail_item(detail):
    return {"external_id": detail.external_id, "title": detail.title, "kind": detail.kind,
            "description": detail.description, "year": detail.year, **detail.metadata}


def target_context(access, source):
    """Capture optional cache provenance before the foreground provider read."""
    try:
        if not supports_related(source):
            return None
        current, configuration = _snapshot(access, source["id"])
        return configuration if current == source else None
    except Exception:
        # Optional cache bookkeeping must not prevent normal detail reads.
        return None


def remember_target(access, source, detail, configuration):
    """Share only trusted metadata, never lines, media URLs or public leases."""
    if configuration is None:
        return
    try:
        operation = current_operation()
        if operation is not None:
            operation.check()
        _confirm(access, source["id"], configuration)
        value = _detail_item(detail)
        if operation is not None:
            operation.check()
        cache.put_target(_cache_key(access, source["id"], detail.external_id, configuration), value)
    except Exception:
        # Identity persistence and detail delivery remain independent of this
        # small, process-local optimization, including configuration races.
        pass


def _category_page(source, category_id):
    # maccms.browse also discovers the taxonomy when an endpoint omits `class`.
    # Here the detail already supplies a specific category: one detail-action
    # page suffices and keeps this entire operation at <=3 upstream requests.
    action = source.get("provider_options", {}).get("detail_action", "detail")
    payload = maccms.fetch(source, {"ac": action, "pg": "1", "t": category_id})
    return maccms._search_result(payload, 1)["items"]


class _ChildCancellation:
    def __init__(self, operation):
        self.operation = operation

    def shutdown(self, _how):
        self.operation.abort()


def _optional_step(operation, access, source_id, configuration, function):
    """One optional branch may time out without cancelling its sibling."""
    operation.check()
    _confirm(access, source_id, configuration)
    child = NetworkOperation(min(operation.deadline, time.monotonic() + STEP_SECONDS))
    cancellation = _ChildCancellation(child)
    try:
        operation.bind_socket(cancellation)
        try:
            with operation_context(child), health.observe_catalogue():
                result = function()
                child.check()
        except (ProviderError, NetworkOperationError) as caught:
            # Parent cancellation, preemption and the total deadline cannot be
            # turned into a recoverable optional failure.
            operation.check()
            _confirm(access, source_id, configuration)
            error = (caught if isinstance(caught, ProviderError) else ProviderError(
                "provider_timeout" if str(caught) == "operation_timeout" else "provider_cancelled"))
            if error.category not in _SOFT_ERRORS:
                raise
            return None, error
        operation.check()
        _confirm(access, source_id, configuration)
        return result, None
    finally:
        operation.release_socket(cancellation)
        child.close()


def read(access, source_id, external_id, *, operation=None):
    try:
        external_id = maccms.external_id(external_id)
    except ProviderError:
        raise catalog.CatalogError("invalid_online_catalog_request", 422) from None
    parent = operation or NetworkOperation(time.monotonic() + DEADLINE_SECONDS)
    parent.deadline = min(parent.deadline, time.monotonic() + DEADLINE_SECONDS)
    child = NetworkOperation(parent.deadline)
    cancellation = _ChildCancellation(child)
    source = None
    try:
        parent.bind_socket(cancellation)
        source, configuration = _snapshot(access, source_id)
        if not supports_related(source):
            parent.check()
            _confirm(access, source_id, configuration)
            return _result(source, external_id, "unsupported")
        key = _cache_key(access, source_id, external_id, configuration)
        cached = cache.get(key)
        if cached is not None:
            with operation_context(parent):
                _confirm(access, source_id, configuration)
                result = _public(source, cached)
                _confirm(access, source_id, configuration)
                parent.check()
                return result
        try:
            with catalog._admit(access.owner, child, background=True):
                _confirm(access, source_id, configuration)
                current = cache.get_target(key)
                if current is None:
                    with health.observe_catalogue():
                        current = _detail_item(maccms.detail(source, external_id))
                child.check()
                _confirm(access, source_id, configuration)
                root = related_matching.search_root(current)
                failures, candidates, category_items = [], [], []
                if root:
                    candidates, error = _optional_step(child, access, source_id, configuration,
                        lambda: maccms.search(source, root, 1)["items"])
                    if error is not None:
                        failures.append(error)
                        candidates = []
                groups = related_matching.select(current, candidates)
                if len(groups["series"]) < 6 and current.get("category_id"):
                    category_items, error = _optional_step(child, access, source_id, configuration,
                        lambda: _category_page(source, current["category_id"]))
                    if error is not None:
                        failures.append(error)
                        category_items = []
                    groups = related_matching.select(current, [*candidates, *category_items], category_items)
                child.check()
                _confirm(access, source_id, configuration)
                if failures and not any(groups[name] for name in ("series", "related", "name_related")):
                    raise failures[0]
                result = _result(source, external_id, "ready", partial=bool(failures), **groups)
                # Cache metadata and private artwork URLs, not short-lived
                # public artwork paths. Every hit must register fresh leases.
                cached_result = json.loads(json.dumps(result))
                _public(source, result)
                _confirm(access, source_id, configuration)
                child.check()
            parent.check()
            _confirm(access, source_id, configuration)
            cache.put(key, cached_result)
            return result
        except catalog.BackgroundDeferred:
            parent.check()
            _confirm(access, source_id, configuration)
            return _result(source, external_id, "deferred")
        except ProviderError as error:
            if error.category != "provider_cancelled":
                raise
            # Only optional-work preemption is a quiet deferred response. A
            # real HTTP disconnect still cancels the parent operation.
            parent.check()
            _confirm(access, source_id, configuration)
            return _result(source, external_id, "deferred")
    except NetworkOperationError as error:
        raise ProviderError("provider_timeout" if str(error) == "operation_timeout" else "provider_cancelled") from None
    finally:
        parent.release_socket(cancellation)
        child.close()
        parent.close()
