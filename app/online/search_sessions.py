"""Device-scoped, bounded search prefixes, advanced only by active requests.

No independent worker or media request is started here. A POST owns its I/O;
parallel GETs may observe each successfully committed source page. Filters are
evaluated against that continuous prefix, never an upstream filtered page.
"""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import json
import re
import threading
import time
import uuid

from ..network.guard import NetworkOperation, NetworkOperationError
from . import catalog, health, maccms
from .providers import ProviderError


FRESH_SECONDS = 15 * 60
IDLE_SECONDS = 30 * 60
MAX_SESSIONS = 8
MAX_BYTES = 64 * 1024**2
MAX_SESSION_BYTES = 8 * 1024**2
MAX_ITEMS = 5000
MAX_PAGES = 50
BATCH_SECONDS = 8
_SESSION_ID = re.compile(r"srch_[0-9a-f]{32}\Z")
_YEAR = re.compile(r"([1-9][0-9]{3})(?:\D|$)")


@dataclass
class _Session:
    identity: str
    key: tuple = field(repr=False)
    source: dict = field(repr=False)
    configuration: str = field(repr=False)
    query: str
    created: float
    accessed: float
    validated_at: int
    items: list = field(default_factory=list, repr=False)
    categories: list = field(default_factory=list)
    pages: int = 0
    expected_total: int | None = None
    expected_pages: int | None = None
    version: int = 0
    complete: bool = False
    loading: bool = False
    error: str = ""
    terminal: bool = False
    size: int = 0


class SearchSessions:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self._lock = threading.RLock()
        self._entries = OrderedDict()

    def clear(self):
        with self._lock:
            self._entries.clear()

    def _prune(self):
        now = self.clock()
        for identity, value in tuple(self._entries.items()):
            if not value.loading and now - value.accessed >= IDLE_SECONDS:
                self._entries.pop(identity)

    def _make_room(self, *, additional=0, preserve=None, count=False):
        while (sum(value.size for value in self._entries.values()) + additional > MAX_BYTES
               or count and len(self._entries) >= MAX_SESSIONS):
            victim = next((identity for identity, value in self._entries.items()
                           if identity != preserve and not value.loading), None)
            if victim is None:
                raise catalog.CatalogError("online_search_limit_reached", 429)
            self._entries.pop(victim)

    @staticmethod
    def _key(access, source, configuration, query):
        return (access.owner, access.generation, access.protection_revision,
                source["id"], configuration, query)

    def _get(self, access, identity):
        if not isinstance(identity, str) or not _SESSION_ID.fullmatch(identity):
            raise catalog.CatalogError("invalid_online_catalog_request", 422)
        with self._lock:
            self._prune()
            value = self._entries.get(identity)
            if value is None or value.key[:3] != (access.owner, access.generation, access.protection_revision):
                raise catalog.CatalogError("online_search_session_expired", 410)
            value.accessed = self.clock()
            self._entries.move_to_end(identity)
        self._confirm(access, value)
        return value

    @staticmethod
    def _confirm(access, value):
        if catalog._detail_configuration(access, value.source) != value.configuration:
            raise catalog.CatalogError("online_source_changed", 409)

    @staticmethod
    def _validate(selection):
        category = selection.get("category_id", "")
        scope = selection.get("category_scope", "exact")
        year = selection.get("year", "")
        offset = selection.get("offset", 0)
        limit = selection.get("limit", 20)
        if (not isinstance(category, str) or scope not in {"exact", "descendants"}
                or not maccms.valid_year(year) or type(offset) is not int or not 0 <= offset <= MAX_ITEMS
                or type(limit) is not int or not 1 <= limit <= 100):
            raise catalog.CatalogError("invalid_online_catalog_request", 422)
        if category:
            try:
                maccms.external_id(category)
            except ProviderError:
                raise catalog.CatalogError("invalid_online_catalog_request", 422) from None
        return {"category_id": category, "category_scope": scope, "year": year, "offset": offset, "limit": limit}

    @staticmethod
    def _matching(value, selection):
        category = selection["category_id"]
        allowed = {category} if category else None
        if category:
            parents = {row["id"]: row["parent_id"] for row in value.categories}
            if not value.pages:
                return []
            if category not in parents:
                raise catalog.CatalogError("online_search_category_unavailable", 422)
            if selection["category_scope"] == "descendants":
                for identity in parents:
                    current, seen = identity, set()
                    while current and current not in seen:
                        if current == category:
                            allowed.add(identity)
                            break
                        seen.add(current)
                        current = parents.get(current, "")
        year = selection["year"]
        matches = []
        for item in value.items:
            if allowed is not None and item["category_id"] not in allowed:
                continue
            parsed_year = _YEAR.match(item["year"].strip())
            if year and (parsed_year is None or parsed_year[1] != year):
                continue
            matches.append(item)
        return matches

    def _response(self, access, value, selection):
        self._confirm(access, value)
        with self._lock:
            matches = self._matching(value, selection)
            end = selection["offset"] + selection["limit"]
            can_continue = not value.complete and not value.terminal
            result = {
                "session_id": value.identity, "version": value.version,
                "source": catalog._public_source(value.source), "configuration_token": value.configuration,
                "query": value.query, "status": ("complete" if value.complete else "loading" if value.loading
                                                   else "error" if value.error else "partial"),
                "items": deepcopy(matches[selection["offset"]:end]), "categories": deepcopy(value.categories),
                "scanned": len(value.items), "found": len(matches),
                "total": len(matches) if value.complete else None,
                "has_more": len(matches) > end or can_continue,
                "can_continue": can_continue, "error_code": value.error,
                "validated_at": value.validated_at,
            }
        # Register only the visible window, not thousands of offscreen posters.
        catalog._artwork(value.source, result)
        self._confirm(access, value)
        return result

    def create(self, access, source_id, query, refresh=False, selection=None, *, operation=None):
        selection = self._validate(selection or {})
        if (not isinstance(query, str) or not query.strip() or len(query) > 100
                or any(ord(char) < 32 or ord(char) == 127 for char in query) or type(refresh) is not bool):
            raise catalog.CatalogError("invalid_online_catalog_request", 422)
        source = catalog._ready_source(source_id, access)
        if not maccms.supports_search_filters(source):
            raise catalog.CatalogError("online_search_filter_unsupported", 422)
        configuration = catalog._detail_configuration(access, source)
        query = query.strip()
        key = self._key(access, source, configuration, query)
        with self._lock:
            self._prune()
            existing = next((value for value in reversed(self._entries.values())
                             if value.key == key and self.clock() - value.created < FRESH_SECONDS), None)
            if existing is not None and not refresh:
                existing.accessed = self.clock()
                value, advance = existing, not existing.pages and not existing.loading
                if advance:
                    value.loading = True
            else:
                self._make_room(count=True)
                value = _Session("srch_" + uuid.uuid4().hex, key, deepcopy(source), configuration, query,
                                 self.clock(), self.clock(), int(time.time() * 1000))
                self._entries[value.identity] = value
                advance = True
                value.loading = True
        if advance:
            self._advance(access, value, selection, 1, operation, background=False, reserved=True)
        return self._response(access, value, selection)

    def read(self, access, identity, selection=None, *, operation=None):
        if operation is not None:
            operation.check()
        selection = self._validate(selection or {})
        return self._response(access, self._get(access, identity), selection)

    def more(self, access, identity, selection=None, *, operation=None):
        selection = self._validate(selection or {})
        value = self._get(access, identity)
        if self.clock() - value.created >= FRESH_SECONDS:
            raise catalog.CatalogError("online_search_session_expired", 410)
        self._advance(access, value, selection, 2, operation, background=True)
        return self._response(access, value, selection)

    def _commit(self, value, result):
        with self._lock:
            categories = result["categories"] or value.categories
            if not categories:
                raise catalog.CatalogError("online_search_results_changed", 409)
            known = {row["id"] for row in categories}
            if (value.expected_total is not None and (value.expected_total != result["total"]
                    or value.expected_pages != result["page_count"])
                    or value.categories and value.categories != categories
                    or any(not row["category_id"] or row["category_id"] not in known for row in result["items"])):
                raise catalog.CatalogError("online_search_results_changed", 409)
            ids = {item["external_id"] for item in value.items}
            if any(item["external_id"] in ids for item in result["items"]):
                raise catalog.CatalogError("online_search_results_changed", 409)
            items = value.items + result["items"]
            if value.pages + 1 >= result["page_count"] and len(items) != result["total"]:
                raise catalog.CatalogError("online_search_results_changed", 409)
            size = len(json.dumps([items, categories], ensure_ascii=False, separators=(",", ":")).encode())
            if len(items) > MAX_ITEMS or size > MAX_SESSION_BYTES:
                raise catalog.CatalogError("online_search_limit_reached", 429)
            self._make_room(additional=size - value.size, preserve=value.identity)
            value.items, value.categories, value.size = items, categories, size
            value.expected_total, value.expected_pages = result["total"], result["page_count"]
            value.pages += 1
            value.version += 1
            value.complete = value.pages >= result["page_count"] and len(items) == result["total"]
            if not value.complete and (value.pages >= MAX_PAGES or len(items) >= MAX_ITEMS):
                value.error, value.terminal = "online_search_limit_reached", True

    def _advance(self, access, value, selection, pages, operation, *, background, reserved=False):
        with self._lock:
            if (value.loading and not reserved) or value.complete or value.terminal:
                return
            if value.pages and len(self._matching(value, selection)) >= selection["offset"] + selection["limit"]:
                return
            value.loading, value.error = True, ""
        parent = operation or NetworkOperation(time.monotonic() + BATCH_SECONDS)
        parent.deadline = min(parent.deadline, time.monotonic() + BATCH_SECONDS)
        try:
            for _ in range(pages):
                parent.check()
                self._confirm(access, value)
                with self._lock:
                    if value.complete or value.terminal:
                        break
                    next_page = value.pages + 1
                child = NetworkOperation(parent.deadline)
                cancellation = catalog._RefreshCancellation(child)
                try:
                    parent.bind_socket(cancellation)
                    with catalog._admit(access.owner, child, background=background):
                        self._confirm(access, value)
                        with health.observe_catalogue():
                            result = maccms.search_session_page(value.source, value.query, next_page)
                        self._confirm(access, value)
                        child.check()
                        parent.check()
                        self._commit(value, result)
                except catalog.BackgroundDeferred:
                    break
                except ProviderError as error:
                    if error.category == "provider_cancelled" and child.cancelled.is_set():
                        parent.check()
                        break
                    raise
                finally:
                    parent.release_socket(cancellation)
                    child.close()
                with self._lock:
                    if len(self._matching(value, selection)) >= selection["offset"] + selection["limit"]:
                        break
        except ProviderError as error:
            with self._lock:
                value.error = ("online_search_results_changed" if error.category == "invalid_provider_response"
                               else error.category)
                value.terminal = error.category == "invalid_provider_response"
        except catalog.CatalogError as error:
            if error.category not in {"online_search_results_changed", "online_search_limit_reached"}:
                raise
            with self._lock:
                value.error, value.terminal = error.category, True
        except NetworkOperationError as error:
            # HTTP disconnects remain hard failures. A normal batch deadline
            # preserves the prefix so the next explicit action can continue.
            with self._lock:
                value.error = "provider_timeout" if str(error) == "operation_timeout" else "provider_cancelled"
            raise
        finally:
            with self._lock:
                value.loading = False
                value.accessed = self.clock()
            if operation is None:
                parent.close()


registry = SearchSessions()


def create(access, source_id, query, refresh=False, selection=None, *, operation=None):
    return registry.create(access, source_id, query, refresh, selection, operation=operation)


def read(access, identity, selection=None, *, operation=None):
    return registry.read(access, identity, selection, operation=operation)


def more(access, identity, selection=None, *, operation=None):
    return registry.more(access, identity, selection, operation=operation)
