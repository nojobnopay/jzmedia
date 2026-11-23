"""Administrator-reviewed host suggestions from one bounded source response.

Inspection never resolves or contacts a suggested media/image address. Allowing
selected exact hosts is a separate, compare-and-append configuration operation;
the existing transport still checks DNS answers and actual public socket peers.
"""

import ipaddress
import re
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import viewing
from ..network.targets import TargetPolicy, TargetPolicyError, public_address
from ..store import online as online_store
from . import catalog, maccms, service
from .models import SourceId, normalize_host
from .providers import ProviderError
from .source_migration import fingerprint


MAX_HOSTS = 128


class HostCheckError(catalog.CatalogError):
    """Fixed public errors that never include rejected URLs or credentials."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              hide_input_in_errors=True)


class HostCheckRequest(_Contract):
    external_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("external_id")
    @classmethod
    def checked_id(cls, value):
        if value is None:
            return None
        try:
            return maccms.external_id(value)
        except ProviderError:
            raise ValueError("invalid source item identity") from None


class HostCheckReport(_Contract):
    source_id: SourceId
    external_id: str
    title: str
    missing_hosts: list[str]
    missing_poster_hosts: list[str]
    allowed_hosts: list[str]
    rejected_count: int
    configuration_fingerprint: str


class AllowHostsRequest(_Contract):
    hosts: list[str] = Field(min_length=1, max_length=MAX_HOSTS)
    expected_fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator("hosts")
    @classmethod
    def checked_hosts(cls, values):
        return list(dict.fromkeys(_checked_host(value) for value in values))

    @field_validator("expected_fingerprint")
    @classmethod
    def checked_fingerprint(cls, value):
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("invalid source fingerprint")
        return value


def _management(access):
    service._current_access(access)
    if viewing.enabled() and not viewing.check_lease(access.owner, access.generation, management=True):
        raise HostCheckError("online_management_revoked", 403)


def _checked_host(value):
    host = normalize_host(value)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        # Do not suggest machine-local single-label names. DNS is deliberately
        # not queried here; the ordinary transport will reject private answers.
        if "." not in host:
            raise ValueError("host must be a public domain or IP") from None
    else:
        public_address(literal.compressed)
    return host


def _candidate(source_id, value, *, media, page=False):
    if not isinstance(value, str):
        raise ValueError("invalid source URL")
    parts = urlsplit(value)
    host = _checked_host(parts.hostname)
    # This authorizes only syntax against the exact host just extracted. It
    # performs no resolution, request, persistent trust or redirect expansion.
    canonical = TargetPolicy(source_id, [host])._canonical_url(value)[0]
    if media and not page and not unquote(urlsplit(canonical).path).lower().endswith((".mp4", ".m3u8")):
        raise ValueError("unsupported source media")
    return host


def _suggestions(source, row, page_poster):
    # Reuse the provider parser's group/entry/string limits before traversing
    # raw links. Unsupported host entries remain visible without being trusted.
    maccms.parse_detail(source, row)
    hosts, poster_hosts = set(), set()
    rejected = 0
    groups = row.get("vod_play_url", "")
    links = []
    if groups:
        for name, group in zip(row.get("vod_play_from", "").split("$$$"), groups.split("$$$")):
            for entry in group.removesuffix("#").split("#"):
                links.append((entry.split("$", 1)[-1], True, maccms.plain(name, 128, required=True)))
    poster = row.get("vod_pic")
    if poster:
        links.append((poster, False, ""))
    if page_poster and page_poster != poster:
        links.append((page_poster, False, ""))
    from . import liangzi
    for value, media, line_name in links:
        try:
            page = media and liangzi.supports_page(source, line_name, value)
            host = _candidate(source["id"], value, media=media, page=page)
        except (TargetPolicyError, ValueError, TypeError, UnicodeError):
            rejected += 1
            continue
        hosts.add(host)
        if not media:
            poster_hosts.add(host)
        if len(hosts) > MAX_HOSTS:
            raise HostCheckError("online_host_candidate_limit", 422)
    return hosts, poster_hosts, rejected


def _homepage_sample(source, action):
    page = maccms.fetch(source, {"ac": action, "pg": "1"})
    items = maccms._search_result(page, 1)["items"]
    if not items:
        raise HostCheckError("online_host_check_empty", 404)
    for item, row in zip(items, page["list"]):
        try:
            host = _candidate(source["id"], item["_poster_url"], media=False)
        except (TypeError, ValueError):
            continue
        if host not in source["allowed_hosts"]:
            return row
    return page["list"][0]


def check(access, source_id, external_id, *, operation=None):
    """Inspect a requested programme or select one from the first catalogue page."""
    _management(access)
    with catalog._admit(access.owner, operation):
        source = catalog._ready_source(source_id)
        if source["provider"] != "maccms":
            raise HostCheckError("online_host_check_unsupported", 422)
        version = fingerprint(source)
        action = source.get("provider_options", {}).get("detail_action", "detail")
        row = _homepage_sample(source, action) if external_id is None else None
        selected = maccms.external_id(row["vod_id"] if row is not None else external_id)
        page_poster = maccms.poster_url(row) if row is not None else ""
        if row is None or "vod_play_url" not in row:
            if row is not None:
                _management(access)
                if fingerprint(catalog._ready_source(source_id)) != version:
                    raise HostCheckError("online_source_changed", 409)
            rows = maccms.fetch(source, {"ac": action, "ids": selected})["list"]
            if len(rows) != 1 or maccms.external_id(rows[0].get("vod_id")) != selected:
                raise ProviderError("provider_not_found")
            row = rows[0]
        candidates, posters, rejected = _suggestions(source, row, page_poster)
        _management(access)
        latest = online_store.get_online_source(source_id)
        if latest is None or fingerprint(latest) != version:
            raise HostCheckError("online_source_changed", 409)
        catalog._ready_source(source_id)
        allowed = source["allowed_hosts"]
        return {"source_id": source_id, "external_id": selected, "title": maccms.plain(row.get("vod_name"), required=True),
                "missing_hosts": sorted(candidates.difference(allowed)), "missing_poster_hosts": sorted(posters.difference(allowed)),
                "allowed_hosts": allowed, "rejected_count": rejected,
                "configuration_fingerprint": version}


def allow(access, source_id, body):
    """Append explicitly selected hosts atomically; never overwrite other edits."""
    _management(access)
    changes = AllowHostsRequest.model_validate(body)
    with online_store._connection(write=True) as connection:
        previous = online_store._source(connection.execute(
            "SELECT * FROM online_sources WHERE id=?", (source_id,)).fetchone())
        if previous is None:
            raise online_store.OnlineNotFound("在线来源不存在")
        if fingerprint(previous) != changes.expected_fingerprint:
            raise HostCheckError("online_source_changed", 409)
        hosts = list(dict.fromkeys([*previous["allowed_hosts"], *changes.hosts]))
        if len(hosts) > MAX_HOSTS:
            raise HostCheckError("online_allowed_host_limit", 422)
        if hosts == previous["allowed_hosts"]:
            return previous, False
        _management(access)
        return online_store._update_source(connection, previous, {"allowed_hosts": hosts})
