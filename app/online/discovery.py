"""Explicit administrator discovery of public VOD catalogues, without importing remote code."""

from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from hashlib import sha256
import secrets
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from ..network.guard import NetworkOperationError, check_operation, child_operation, operation_context
from ..network.models import SourceNetworkPolicy
from ..network.policy import NetworkPolicyError
from ..store import online, online_network
from . import (catalog, diagnostic_probes as probes, discovery_directory, discovery_hints, discovery_media,
               liangzi, maccms, open_catalog, service, source_hosts)
from .models import OnlineSourceCreate
from .providers import ProviderError
from .source_templates import list_source_templates


# Built-in fallbacks for times when the public MacCMS directory is unreachable.
# https://github.com/hafrey1/LunaTV-config/blob/main/jin18.json
_COMMUNITY = (
    ("ikun", "iKun 资源", "https://ikunzyapi.com/api.php/provide/vod"),
    ("modu", "魔都资源", "https://www.mdzyapi.com/api.php/provide/vod"),
    ("wujin", "无尽资源", "https://api.wujinapi.me/api.php/provide/vod"),
)
_SLOTS = threading.BoundedSemaphore(3)
_RECEIPTS = OrderedDict()
_RECEIPT_LOCK = threading.Lock()
_RECEIPT_TTL = 300


@dataclass(frozen=True)
class Candidate:
    id: str
    origin: str
    source: dict
    aliases: tuple[str, ...]

    @property
    def saved(self):
        return self.id == self.source["id"]

    def check_directory(self):
        if (self.id.startswith("feed-")
                and (self.source["name"], self.source["api_base_url"]) not in discovery_directory.entries()):
            raise catalog.CatalogError("online_source_changed", 409)


def _candidate(key, name, api, hosts, aliases, origin, provider="maccms", options=None):
    source = OnlineSourceCreate(name=name, provider=provider, api_base_url=api, allowed_hosts=hosts,
                                enabled=True, provider_options=options or {}).model_dump(mode="json")
    source["id"] = "src_" + sha256(key.encode()).hexdigest()[:32]
    return Candidate(key, origin, source, tuple(aliases))


def _fixed_directory():
    candidates = [_candidate(row["id"], row["name"], row["api_base_url"], row["allowed_hosts"], row["known_api_urls"],
                             "运营方公开接口", row["provider"], row["provider_options"])
                  for row in list_source_templates()["items"] if row["provider"] == "maccms"]
    candidates.extend(_candidate(key, name, url, [], [url], "LunaTV-config 社区目录")
                      for key, name, url in _COMMUNITY)
    return candidates


def _directory(feed_entries=None):
    candidates = _fixed_directory()
    if feed_entries is None:
        feed_entries = discovery_directory.entries()
    for name, url in feed_entries:
        key = "feed-" + sha256(url.encode()).hexdigest()[:32]
        candidates.append(_candidate(key, name, url, [], [url], "LunaTV-config 社区目录"))
    return candidates


def refresh(access, directory_route=None, *, operation):
    source_hosts._management(access)
    feed = _candidate("directory", "公共目录", discovery_directory.URL, [], [], "LunaTV-config")
    subscription = SourceNetworkPolicy(subscription=directory_route) if directory_route is not None else None

    def read_state():
        return _snapshot(feed, candidate_policy=subscription)

    state = read_state()
    fingerprint = online_network.online_network_fingerprint(state)

    def guard():
        source_hosts._management(access)
        if online_network.online_network_fingerprint(read_state()) != fingerprint:
            raise catalog.CatalogError("online_source_changed", 409)

    excluded = {url.rstrip("/") for candidate in _fixed_directory() for url in candidate.aliases}
    try:
        with child_operation(operation, time.monotonic() + 5):
            policy = probes.snapshot(state, "subscription")
            result, feed_entries = discovery_directory.refresh(policy, fingerprint, excluded, guard)
    except (NetworkOperationError, NetworkPolicyError):
        operation.check()
        feed_entries = discovery_directory.entries()
        result = "cached" if feed_entries else "fallback"
    guard()
    return {**list_candidates(feed_entries), "directory_status": result}


def _matches(candidate, saved):
    return (candidate.source["provider"] == saved["provider"] and
            saved["api_base_url"].rstrip("/") in _aliases(candidate))


def _aliases(candidate):
    urls = set()
    for alias in candidate.aliases:
        url = alias.rstrip("/")
        urls.add(url)
        parts = urlsplit(url)
        port = 443 if parts.scheme == "https" else 80
        if parts.port is None:
            urls.add(urlunsplit(parts._replace(netloc=f"{parts.netloc}:{port}")))
    return urls


def list_candidates(feed_entries=None):
    stored = online.list_online_sources()
    directory = _directory(feed_entries)
    result = []
    for source in stored:
        known = next((candidate for candidate in directory if _matches(candidate, source)), None)
        result.append(Candidate(source["id"], known.origin if known else "已配置来源", source, ()))
    result.extend(candidate for candidate in directory if not any(_matches(candidate, s) for s in stored))
    return {"items": [{"id": c.id, "name": c.source["name"], "origin": c.origin, "api_base_url": c.source["api_base_url"],
                       "source_id": c.source["id"] if c.saved else None, "enabled": c.saved and c.source["enabled"]}
                      for c in result]}


def _get(key):
    if key.startswith("src_"):
        saved = online.get_online_source(key)
        if saved is not None:
            return Candidate(key, "已配置来源", saved, ())
    else:
        for candidate in _directory():
            if candidate.id == key:
                return candidate
    raise catalog.CatalogError("online_source_not_found", 404)


def _existing(candidate, connection):
    aliases = tuple(_aliases(candidate))
    rows = connection.execute(
        "SELECT * FROM online_sources WHERE provider=? AND RTRIM(api_base_url,'/') IN ("
        + ",".join("?" for _ in aliases) + ")", (candidate.source["provider"], *aliases))
    return [online._source(row) for row in rows]


def _state(candidate, connection, *, candidate_policy=None, probe_route=None):
    if candidate.aliases and not candidate.saved and _existing(candidate, connection):
        raise catalog.CatalogError("online_source_changed", 409)
    candidate.check_directory()
    try:
        state = online_network.online_network_snapshot(connection, candidate.source["id"],
                                                       candidate=None if candidate.saved else candidate.source,
                                                       candidate_policy=candidate_policy, probe_route=probe_route)
    except (online.OnlineNotFound, ValueError):
        raise catalog.CatalogError("online_source_changed", 409) from None
    if not state["online_enabled"]:
        raise catalog.CatalogError("online_source_disabled", 409)
    if candidate.saved and state["source_config"] != candidate.source:
        raise catalog.CatalogError("online_source_changed", 409)
    return state


def _snapshot(candidate, *, candidate_policy=None, probe_route=None):
    with online_network._connection(snapshot=True) as connection:
        return _state(candidate, connection, candidate_policy=candidate_policy, probe_route=probe_route)


@dataclass
class ProbeReceipt:
    expires: float
    access: object
    candidate_id: str
    fingerprint: str
    media: frozenset
    posters: frozenset
    missing: frozenset
    approved: frozenset
    observed: discovery_hints.Measurement
    tested_route: dict | None
    measured: bool
    lock: threading.Lock = field(default_factory=threading.Lock)
    applied: tuple | None = None


def _issue(access, candidate, fingerprint, report, approved, observed):
    token = secrets.token_urlsafe(24)
    with _RECEIPT_LOCK:
        now = time.monotonic()
        while _RECEIPTS and (len(_RECEIPTS) >= 128 or next(iter(_RECEIPTS.values())).expires <= now):
            _RECEIPTS.popitem(last=False)
        _RECEIPTS[token] = ProbeReceipt(now + _RECEIPT_TTL, access, candidate.id, fingerprint,
                                        frozenset(report["allowed_hosts"]), frozenset(report["poster_hosts"]),
                                        frozenset(report["missing_hosts"]), frozenset(approved), observed,
                                        report["tested_route"], report["status"] == "measured")
    return token


def _receipt(access, key, token):
    with _RECEIPT_LOCK:
        receipt = _RECEIPTS.get(token)
    if (not receipt or receipt.expires <= time.monotonic()
            or (receipt.access, receipt.candidate_id) != (access, key)):
        raise catalog.CatalogError("online_discovery_probe_expired", 409)
    return receipt


def _enable(access, candidate, receipt, posters):
    changed = False
    with online._connection(write=True) as connection:
        source_hosts._management(access)
        if receipt.expires <= time.monotonic():
            raise catalog.CatalogError("online_discovery_probe_expired", 409)
        if receipt.applied is not None:
            sid, version, selected = receipt.applied
            current = online_network.online_network_snapshot(connection, sid)
            if online_network.online_network_fingerprint(current) != version or posters != selected:
                raise catalog.CatalogError("online_source_changed", 409)
            return current["source_config"]
        state = _state(candidate, connection, probe_route=receipt.tested_route)
        if online_network.online_network_fingerprint(state) != receipt.fingerprint:
            raise catalog.CatalogError("online_source_changed", 409)
        existing = (candidate.source["allowed_hosts"] if candidate.saved
                    else [urlsplit(candidate.source["api_base_url"]).hostname])
        hosts = list(dict.fromkeys([*existing, *sorted(receipt.media | posters)]))
        if len(hosts) > source_hosts.MAX_HOSTS:
            raise catalog.CatalogError("online_allowed_host_limit", 422)
        if candidate.saved:
            source, changed = online._update_source(connection, state["source_config"], {"enabled": True, "allowed_hosts": hosts})
        else:
            values = {k: v for k, v in candidate.source.items() if k != "id"}
            source = online.insert_online_source(connection, {**values, "allowed_hosts": hosts})
        if receipt.tested_route is not None:
            patch = {purpose: receipt.tested_route for purpose in ("api", "media")
                     if state["persisted_source_policy"][purpose] != receipt.tested_route}
            if patch:
                online_network._update_source_policy(connection, source["id"], patch)
                changed = candidate.saved
        final = online_network.online_network_snapshot(connection, source["id"])
    receipt.applied = (source["id"], online_network.online_network_fingerprint(final), posters)
    if changed:
        service.registry.revoke_source(source["id"])
    discovery_hints.remember(final, receipt.observed)
    return source


def enable(access, key, token, poster_hosts=(), apply_route=False):
    source_hosts._management(access)
    receipt = _receipt(access, key, token)
    if apply_route != (receipt.tested_route is not None) or apply_route and not receipt.measured:
        raise catalog.CatalogError("online_discovery_route_unconfirmed", 422)
    posters = frozenset(poster_hosts)
    if not posters <= receipt.posters:
        raise catalog.CatalogError("online_discovery_invalid_hosts", 422)
    with receipt.lock:
        try:
            candidate = _get(key)
            return _enable(access, candidate, receipt, posters)
        except online.OnlineNotFound:
            raise catalog.CatalogError("online_source_changed", 409) from None
        except catalog.CatalogError as error:
            if error.category == "online_source_not_found":
                raise catalog.CatalogError("online_source_changed", 409) from None
            raise


def _entries(source, row):
    # Validate the provider's bounds and movie/episode identity rules before reading raw links.
    detail = maccms.parse_detail(source, row)
    declared = set()
    groups = row.get("vod_play_url", "").split("$$$")
    for line, group in zip(detail.lines, groups):
        if not any(e.reason == "target_host_not_allowed" for e in line.entries):
            continue
        for raw in group.split("#", 3)[:3]:
            address = raw.partition("$")[2] if "$" in raw else raw
            try:
                page = liangzi.supports_page(source, line.name, address)
                declared.add(source_hosts._candidate(source["id"], address, media=True, page=page))
            except (ValueError, TypeError, UnicodeError):
                continue
    if declared:
        # Reparse locally, granting no network access. Only the selected entry will enter the sample policy.
        hosts = sorted(set(source["allowed_hosts"]) | declared)
        if len(hosts) <= source_hosts.MAX_HOSTS:
            detail = maccms.parse_detail({**source, "allowed_hosts": hosts}, row)
    lines = [iter(entry for entry in line.entries if not entry.reason and entry.transport) for line in detail.lines]
    for _ in range(3):
        for line in lines:
            entry = next(line, None)
            if entry is not None:
                yield entry


def _poster(source, row):
    try:
        return source_hosts._candidate(source["id"], maccms.poster_url(row), media=False)
    except (ValueError, TypeError, UnicodeError, ProviderError):
        return None


def _read_catalogue(source, api):
    if source["provider"] == "open_catalog":
        open_catalog.search(source, "", 1)
        return []
    action = source["provider_options"]["detail_action"]
    payload = probes._payload(source["api_base_url"], {"ac": action, "pg": "1"}, api, source["allowed_hosts"])
    maccms._search_result(payload, 1)
    return payload["list"]


def _sample_candidates(source, rows, api):
    for row in rows:
        check_operation()
        poster = _poster(source, row)
        try:
            if not row.get("vod_play_url"):
                external = maccms.external_id(row["vod_id"])
                params = {"ac": source["provider_options"]["detail_action"], "ids": external}
                detail = probes._payload(source["api_base_url"], params, api, source["allowed_hosts"])
                maccms.detail_from_response(source, external, detail)
                row = detail["list"][0]
                poster = _poster(source, row) or poster
            for entry in _entries(source, row):
                yield row["vod_id"], entry, poster
        except (probes.ProbeFailure, ProviderError):
            continue


def _sample_options(source, rows, api):
    """Use distinct hosts first, then other transports or titles on the same host."""
    seen_urls, tried_hosts, tried_paths, tried_titles = set(), set(), set(), set()
    # Reuse links already supplied on the page before spending time on extra
    # detail requests. A metadata-only list still costs at most three lookups.
    ready = [row for row in rows if row.get("vod_play_url")]
    missing = [row for row in rows if not row.get("vod_play_url")][:3]
    for group in (ready, missing):
        deferred = []
        for title, entry, poster in _sample_candidates(source, group, api):
            if entry.url in seen_urls:
                continue
            seen_urls.add(entry.url)
            host = urlsplit(entry.url).hostname
            path = (host, entry.transport, entry.resolver)
            if host in tried_hosts:
                deferred.append((path, title, entry, poster))
                continue
            tried_hosts.add(host)
            tried_paths.add(path)
            tried_titles.add(title)
            yield entry, poster
        while deferred:
            best = max(deferred, key=lambda option: (option[0] not in tried_paths, option[1] not in tried_titles))
            deferred.remove(best)
            path, title, entry, poster = best
            tried_paths.add(path)
            tried_titles.add(title)
            yield entry, poster


def _sample_rows(candidate, rows, api, media, result, approved, operation, *, sample_seconds=4):
    source = candidate.source
    if not candidate.saved:
        # Discovery grants only observed media/selected artwork, even when
        # the separate manual setup template suggests additional known hosts.
        source = {**source, "allowed_hosts": [urlsplit(source["api_base_url"]).hostname]}
    missing, posters = set(), set()
    attempts = result["attempts"]
    if source["provider"] == "open_catalog":
        detail = open_catalog.detail({**source, "allowed_hosts": [open_catalog.MEDIA_HOST]}, open_catalog.EXTERNAL_ID)
        entries = iter([(detail.lines[0].entries[0], None)])
    elif not rows:
        result.update(status="empty", reason="empty_catalog")
        return
    else:
        entries = _sample_options(source, rows, api)
    # Optional detail reads and media reads both leave time to revalidate
    # permissions and return the evidence already collected.
    cutoff = operation.deadline - 0.15
    for _ in range(3):
        try:
            with child_operation(operation, cutoff):
                option = next(entries, None)
        except NetworkOperationError as exc:
            operation.check()
            if str(exc) != "operation_timeout":
                raise
            break
        if option is None or time.monotonic() >= cutoff:
            break
        entry, poster = option
        new_poster = poster if poster and poster not in source["allowed_hosts"] else None
        if new_poster:
            posters.add(new_poster)
        review = discovery_media.SampleHosts(source, approved, not candidate.saved)
        try:
            # Reserve time for another line when the first CDN stalls.
            deadline = min(time.monotonic() + sample_seconds, cutoff)
            with child_operation(operation, deadline):
                sample = discovery_media.sample_media(source, entry, media, host_review=review)
            attempts.append({"stage": "media", "reason": "ok"})
            ratio = sample["realtime_ratio"]
            best_ratio = result.get("realtime_ratio")
            if best_ratio is None or (ratio is not None and ratio > best_ratio):
                result.update(sample, status="measured", reason="ok", allowed_hosts=sorted(review.observed),
                              poster_hosts=[new_poster] if new_poster and new_poster not in review.observed else [])
                if ratio is None or ratio >= 1:
                    return
            # Keep the strongest measured fallback while another line may still reach playback speed.
            continue
        except (probes.ProbeFailure, ProviderError) as exc:
            operation.check()
            stage = getattr(exc, "stage", None) or "entry"
            reason = _public_reason(str(exc), stage)
            if reason == "target_host_not_allowed" and candidate.saved:
                missing.update(review.observed - approved)
        except NetworkOperationError:
            operation.check()
            stage = "entry"
            reason = "provider_timeout"
        attempts.append({"stage": stage, "reason": reason})
    if result["status"] == "measured":
        return
    result["missing_hosts"] = sorted(missing)
    result["poster_hosts"] = sorted(posters)
    counts = Counter(attempt["reason"] for attempt in attempts)
    if missing:
        result["reason"] = "target_host_not_allowed"
    elif counts:
        result["reason"] = counts.most_common(1)[0][0]
    else:
        result["reason"] = "provider_timeout" if time.monotonic() >= cutoff else "no_media_sample"


def _public_reason(reason, stage):
    if reason in {"network_timeout", "operation_timeout", "network_connect_timeout", "target_dns_timeout"}:
        return "provider_timeout"
    if reason in {"invalid_upstream_range", "upstream_range_mismatch", "upstream_ignored_range", "range_not_satisfiable"}:
        return "invalid_range_response"
    if ((stage == "manifest" and reason == "response_byte_limit")
            or reason in {"hls_manifest_size_limit", "hls_line_count_limit", "hls_output_size_limit", "hls_reference_limit"}):
        return "manifest_limit"
    if reason.startswith(("unsupported_hls_", "invalid_hls_")) or reason in {"manifest_depth", "invalid_manifest_response"}:
        return "unsupported_manifest"
    if reason in {"target_host_not_allowed", "no_media_sample", "unsupported_media", "invalid_media_sample",
                  "response_byte_limit", "provider_timeout", "media_host_limit", "target_dns_failed",
                  "http_401", "http_403", "http_404", "http_429"}:
        return reason
    if reason.startswith("http_") or reason == "online_upstream_status":
        return "upstream_http_error"
    return "catalog_unavailable" if stage == "catalog" else "media_unavailable"


def probe(access, key, probe_token=None, selected_hosts=(), extended=False, probe_route=None, *, operation):
    started_probe = time.monotonic()
    source_hosts._management(access)
    candidate = _get(key)
    state = _snapshot(candidate, probe_route=probe_route)
    fingerprint = online_network.online_network_fingerprint(state)
    approved = frozenset(selected_hosts)
    if probe_token or approved:
        previous = _receipt(access, key, probe_token)
        if previous.fingerprint != fingerprint:
            raise catalog.CatalogError("online_source_changed", 409)
        if not candidate.saved or not approved or not approved <= previous.missing:
            raise catalog.CatalogError("online_discovery_invalid_hosts", 422)
        approved |= previous.approved

    def guard(connection=None):
        source_hosts._management(access)
        current = (_snapshot(candidate, probe_route=probe_route) if connection is None else
                   _state(candidate, connection, probe_route=probe_route))
        if online_network.online_network_fingerprint(current) != fingerprint:
            raise catalog.CatalogError("online_source_changed", 409)
        if probe_token:
            _receipt(access, key, probe_token)

    if not _SLOTS.acquire(blocking=False):
        raise catalog.CatalogError("provider_catalog_busy", 429)
    result = {"id": key, "status": "failed", "reason": "catalog_unavailable", "catalog_ms": None,
              "media_ms": None, "sample_bytes": 0, "sample_kbps": None, "realtime_ratio": None, "tested_route": probe_route,
              "allowed_hosts": [], "missing_hosts": [], "poster_hosts": [], "probe_token": None, "attempts": []}
    try:
        with operation_context(operation), probes.guarded_reads(guard):
            api = probes.snapshot(state, "api", extended=extended)
            started = time.monotonic()
            rows = _read_catalogue(candidate.source, api)
            result.update(status="catalog_only", reason="media_unavailable", catalog_ms=round((time.monotonic() - started) * 1000))
            _sample_rows(candidate, rows, api, probes.snapshot(state, "media", extended=extended), result, approved, operation,
                         sample_seconds=8 if extended else 4)
    except probes.ProbeFailure as exc:
        result["reason"] = _public_reason(exc.code, "entry" if result["status"] == "catalog_only" else "catalog")
    except (NetworkPolicyError, online_network.NetworkConflict, ProviderError):
        pass
    except NetworkOperationError as error:
        if candidate.saved and probe_route is None and str(error) == "operation_timeout":
            discovery_hints.remember(state, discovery_hints.Measurement(started_probe, None))
        raise ProviderError("provider_timeout" if str(error) == "operation_timeout" else "provider_cancelled") from None
    finally:
        _SLOTS.release()
    observed = discovery_hints.measurement(result, started_probe)
    with online_network._connection(snapshot=True) as connection:
        guard(connection)
        if operation.cancelled.is_set():
            raise ProviderError("provider_cancelled")
        if candidate.saved and probe_route is None and not result["allowed_hosts"]:
            discovery_hints.remember(state, observed)
        if result["status"] in {"measured", "catalog_only"}:
            result["probe_token"] = _issue(access, candidate, fingerprint, result, approved, observed)
    return {**result, "checked_at": int(time.time())}
