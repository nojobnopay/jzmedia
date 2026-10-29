"""On-demand single-source catalogues with bounded work and stable identities."""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import threading
import time

from ..network.guard import NetworkOperation, NetworkOperationError, current_operation, operation_context
from ..store import online as online_store
from ..store.online import OnlineNotFound
from ..store.online_network import load_online_network_state
from . import health, maccms, open_catalog, service
from .models import OnlinePlaybackIdentity
from .providers import ProviderError, ResolvedPlayback


_owner = ContextVar("online_catalog_owner", default=None)
_lock = threading.Lock()
_active = 0
_owners = set()


class CatalogError(RuntimeError):
    def __init__(self, category, status=502):
        self.category, self.status = category, status
        super().__init__(category)


@contextmanager
def resolution_owner(owner):
    token = _owner.set(owner)
    try:
        yield
    finally:
        _owner.reset(token)


@contextmanager
def _admit(owner=None, operation=None):
    global _active
    borrowed = operation is None and current_operation() is not None
    operation = operation or current_operation() or NetworkOperation(time.monotonic() + 30)
    admitted = False
    try:
        operation.check()
        with _lock:
            if _active >= 4 or owner is not None and owner in _owners:
                raise ProviderError("provider_catalog_busy")
            _active += 1
            if owner is not None:
                _owners.add(owner)
            admitted = True
        with operation_context(operation):
            yield operation
            operation.check()
    except NetworkOperationError as error:
        raise ProviderError("provider_timeout" if str(error) == "operation_timeout" else "provider_cancelled") from None
    finally:
        if not borrowed:
            operation.close()
        if admitted:
            with _lock:
                _active -= 1
                if owner is not None:
                    _owners.discard(owner)


def _ready_source(source_id, access=None):
    if access is not None:
        service._current_access(access)
    if not online_store.get_online_settings()["enabled"]:
        raise CatalogError("online_source_disabled", 503)
    source = online_store.get_online_source(source_id)
    if source is None:
        raise CatalogError("online_source_not_found", 404)
    if not source["enabled"]:
        raise CatalogError("online_source_disabled", 503)
    return source


def _version(source):
    return json.dumps({key: source.get(key) for key in
                       ("id", "provider", "api_base_url", "allowed_hosts", "provider_options", "enabled")},
                      sort_keys=True, separators=(",", ":"))


def _after(source, access=None):
    if _version(_ready_source(source["id"], access)) != _version(source):
        raise CatalogError("online_source_changed", 409)


def assert_source_version(source_id, fingerprint):
    if fingerprint is not None and _version(_ready_source(source_id)) != fingerprint:
        raise CatalogError("online_source_changed", 409)


def _adapter(source):
    if source["provider"] == "maccms":
        return maccms
    if source["provider"] == "open_catalog":
        return open_catalog
    raise ProviderError("provider_not_ready")


def _public_source(source):
    return {key: source[key] for key in ("id", "name", "provider")}


def sources(access):
    service._current_access(access)
    enabled = online_store.get_online_settings()["enabled"]
    # Cache partition only: this is deliberately distinct from state API write
    # intent tokens and never exposes the device's owner hash to the browser.
    scope = hashlib.sha256(json.dumps(["online-catalogue", access.owner, access.generation,
                                      access.protection_revision], separators=(",", ":")).encode()).hexdigest()
    result = []
    for selected in online_store.list_online_sources() if enabled else []:
        if not selected["enabled"]:
            continue
        try:
            state = load_online_network_state(selected["id"])
        except OnlineNotFound:
            continue
        source = state["source_config"]
        if not state["online_enabled"] or not source or not source["enabled"]:
            continue
        # Include referenced encrypted credentials/revisions and every network
        # purpose, while returning only an opaque invalidation tag.
        configuration = hashlib.sha256(json.dumps(state, sort_keys=True,
                                                 separators=(",", ":")).encode()).hexdigest()
        result.append({**_public_source(source), "priority": source["priority"],
                       "configuration_token": configuration,
                       "capabilities": {"search": True, "episodes": source["provider"] == "maccms", "refresh": True}})
    service._current_access(access)
    if not online_store.get_online_settings()["enabled"]:
        return {"enabled": False, "items": [], "scope_token": scope}
    return {"enabled": enabled, "items": result, "scope_token": scope}


def search(access, source_id, query, page=1, *, operation=None):
    if not isinstance(query, str) or len(query) > 100 or any(ord(char) < 32 or ord(char) == 127 for char in query):
        raise CatalogError("invalid_online_catalog_request", 422)
    if type(page) is not int or not 1 <= page <= 1000:
        raise CatalogError("invalid_online_catalog_request", 422)
    query = query.strip()
    with _admit(access.owner, operation):
        source = _ready_source(source_id, access)
        if not query and source["provider"] != "open_catalog":
            raise CatalogError("online_query_required", 422)
        with health.observe_catalogue():
            result = _adapter(source).search(source, query, page)
        _after(source, access)
        return {"source": _public_source(source), **_artwork(source, result)}


def _artwork(source, result):
    from . import artwork
    urls = [item.pop("_poster_url", "") for item in result["items"]]
    for item, path in zip(result["items"], artwork.register_many(source, urls)):
        item["poster_path"] = path
    return result


def browse(access, source_id, page=1, category_id="", *, operation=None):
    if type(page) is not int or not 1 <= page <= 1000:
        raise CatalogError("invalid_online_catalog_request", 422)
    try:
        if category_id:
            category_id = maccms.external_id(category_id)
    except ProviderError:
        raise CatalogError("invalid_online_catalog_request", 422) from None
    with _admit(access.owner, operation):
        source = _ready_source(source_id, access)
        with health.observe_catalogue():
            result = (open_catalog.search(source, "", page) if source["provider"] == "open_catalog"
                      else _adapter(source).browse(source, page, category_id))
        _after(source, access)
        return {"source": _public_source(source), **_artwork(source, result)}


def _persist(source, detail):
    episodes = []
    if detail.kind == "tv":
        for line in detail.lines:
            for entry in line.entries:
                episodes.append({"external_id": maccms.episode_external_id(line.name, entry.label),
                                 "title": entry.label, "season": None, "episode": None,
                                 "sort_order": len(episodes)})
    return online_store.save_online_catalog(source["id"], detail.external_id, detail.kind, detail.title, episodes)


def _public_detail(source, detail, saved):
    from . import artwork
    item = saved["item"]
    episodes = {episode["external_id"]: episode for episode in saved["episodes"]}
    lines = []
    for line in detail.lines:
        entries = []
        for entry in line.entries:
            identity = None
            if entry.transport is not None and not entry.reason:
                identity = {"source_id": source["id"], "item_id": item["id"], "line_id": line.id,
                            "kind": "movie" if item["kind"] == "movie" else "episode"}
                if item["kind"] == "tv":
                    identity["episode_id"] = episodes[maccms.episode_external_id(line.name, entry.label)]["id"]
            entries.append({"label": entry.label, "identity": identity, "transport": entry.transport,
                            "supported": identity is not None, "reason": entry.reason})
        lines.append({"id": line.id, "label": line.name, "entries": entries})
    return {"source": _public_source(source), "item": {key: item[key] for key in ("id", "source_id", "kind", "title")},
            "description": detail.description, "year": detail.year, "lines": lines,
            "attribution": [{"label": label, "url": url} for label, url in detail.attribution],
            "poster_path": artwork.register(source, detail.poster_url)}


def detail(access, source_id, selected_id, *, operation=None):
    try:
        selected_id = maccms.external_id(selected_id)
    except ProviderError:
        raise CatalogError("invalid_online_catalog_request", 422) from None
    with _admit(access.owner, operation):
        source = _ready_source(source_id, access)
        with health.observe_catalogue():
            selected = _adapter(source).detail(source, selected_id)
        _after(source, access)
        saved = _persist(source, selected)
        _after(source, access)
        return _public_detail(source, selected, saved)


def resolve(identity):
    identity = OnlinePlaybackIdentity.model_validate(identity)
    with _admit(_owner.get()):
        source = _ready_source(identity.source_id)
        item = online_store.get_online_item(identity.item_id)
        if item is None or item["source_id"] != identity.source_id:
            raise ProviderError("provider_not_found")
        with health.observe_catalogue():
            selected = _adapter(source).detail(source, item["external_id"])
        _after(source)
        if selected.kind != item["kind"]:
            raise ProviderError("provider_not_found")
        line_id = getattr(identity, "line_id", None)
        if identity.kind == "movie":
            if item["kind"] != "movie":
                raise ProviderError("provider_not_found")
            if line_id is None and len(selected.lines) != 1:
                raise ProviderError("provider_line_required")
            lines = [line for line in selected.lines if line_id is None or line.id == line_id]
            if len(lines) != 1:
                raise ProviderError("provider_line_not_found")
            # A film with multiple entries has no ordering-based default.
            if len(lines[0].entries) != 1:
                raise ProviderError("provider_media_unsupported")
            entry = lines[0].entries[0]
        else:
            episode = online_store.get_online_episode(identity.episode_id)
            if item["kind"] != "tv" or episode is None or episode["item_id"] != item["id"]:
                raise ProviderError("provider_episode_not_found")
            found = [(line, entry) for line in selected.lines for entry in line.entries
                     if maccms.episode_external_id(line.name, entry.label) == episode["external_id"]
                     and (line_id is None or line.id == line_id)]
            if len(found) != 1:
                raise ProviderError("provider_episode_not_found")
            _line, entry = found[0]
        if not entry.transport or entry.reason:
            raise ProviderError("provider_media_unsupported")
        return ResolvedPlayback(entry.url, entry.transport, duration_seconds=selected.duration_seconds,
                                source_fingerprint=_version(source))


def provider_error_status(error):
    return {"provider_not_ready": 503, "provider_not_found": 404,
            "provider_timeout": 504, "provider_cancelled": 410, "provider_catalog_busy": 429,
            "provider_rate_limited": 503, "provider_line_required": 409,
            "provider_line_not_found": 404, "provider_episode_not_found": 404,
            "provider_media_unsupported": 422}.get(error.category, 502)
