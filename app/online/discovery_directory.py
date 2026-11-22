"""Bounded catalogue discovery from one known HTTPS JSON subscription."""

import json
import re
from threading import Lock
import time
from urllib.parse import urlsplit

from ..network.client import NetworkRequestError
from ..network.guard import check_operation
from ..network.targets import TargetPolicy, TargetPolicyError
from . import diagnostic_probes as probes, source_hosts


URL = "https://raw.githubusercontent.com/hafrey1/LunaTV-config/main/jin18.json"
_MAX_BYTES = 256 * 1024
_MAX_CANDIDATES = 24
_CACHE_SECONDS = 10 * 60
_STALE_SECONDS = 24 * 3600
_cache_lock = Lock()
_refresh_lock = Lock()
_entries = ()
_last_success = _retry_at = 0.0
_fingerprint = None


def entries():
    with _cache_lock:
        return _entries if time.monotonic() - _last_success <= _STALE_SECONDS else ()


def _decode(body, source_id, excluded):
    # Keep malformed text localized to its row rather than dropping every
    # well-formed catalogue when a publisher supplies one invalid display name.
    raw = json.loads(body.decode("utf-8-sig", errors="surrogateescape"))
    sites = raw.get("api_site") if isinstance(raw, dict) else None
    if not isinstance(sites, dict) or len(sites) > 512:
        raise ValueError("invalid_source_directory")
    found, seen = [], set(excluded)
    for row in sites.values():
        if not isinstance(row, dict) or any(k in row for k in ("player", "parse", "proxy", "headers", "spider", "ext")):
            continue
        url, name = row.get("api"), row.get("name")
        if (not isinstance(name, str) or not (1 <= len(name.strip()) <= 80)
                or re.search(r"[\x00-\x1f<>\x7f-\x9f\ud800-\udfff]", name)):
            continue
        if not isinstance(url, str) or len(url) > 512 or "?" in url or "#" in url:
            continue
        try:
            parts = urlsplit(url)
            if (parts.scheme != "https" or parts.port not in (None, 443)
                    or parts.path not in {"/api.php/provide/vod", "/api.php/provide/vod/"}):
                continue
            host = source_hosts._checked_host(parts.hostname)
            endpoint = TargetPolicy(source_id, [host])._canonical_url(url)[0]
        except (AttributeError, TypeError, ValueError, TargetPolicyError):
            continue
        normalized = endpoint.rstrip("/")
        if normalized in seen:
            continue
        found.append((name.strip(), endpoint))
        seen.add(normalized)
        if len(found) == _MAX_CANDIDATES:
            break
    if not found:
        raise ValueError("empty_source_directory")
    return tuple(found)


def _fetch(policy, excluded):
    with probes.client_for(policy, ["raw.githubusercontent.com"]) as client:
        with client.stream("GET", URL, headers={"Accept": "application/json, text/plain"}) as response:
            if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                raise ValueError("directory_unavailable")
            body = bytearray()
            for chunk in response.iter_raw(chunk_size=8192):
                check_operation()
                if len(body) + len(chunk) > _MAX_BYTES:
                    raise ValueError("source_directory_too_large")
                body.extend(chunk)
    return _decode(body, policy.source_id, excluded)


def refresh(policy, fingerprint, excluded, guard):
    """Share a short cache and cooldown across scans; a failed refresh keeps recent results."""
    global _entries, _last_success, _retry_at, _fingerprint
    now = time.monotonic()
    with _cache_lock:
        if _fingerprint == fingerprint and now < _retry_at:
            return "cached" if _entries and now - _last_success <= _STALE_SECONDS else "fallback"
    if not _refresh_lock.acquire(blocking=False):
        return "cached" if entries() else "fallback"
    try:
        guard()
        try:
            values = _fetch(policy, excluded)
        except (ValueError, UnicodeError, RecursionError, NetworkRequestError, TargetPolicyError):
            values = None
        guard()
        now = time.monotonic()
        with _cache_lock:
            _fingerprint = fingerprint
            _retry_at = now + (_CACHE_SECONDS if values is not None else 60)
            if values is not None:
                _entries, _last_success = values, now
                return "updated"
            return "cached" if _entries and now - _last_success <= _STALE_SECONDS else "fallback"
    finally:
        _refresh_lock.release()
