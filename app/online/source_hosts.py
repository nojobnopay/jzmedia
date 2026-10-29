"""Administrator-reviewed host suggestions from one bounded source response.

Inspection never resolves or contacts a suggested media/image address. Allowing
selected exact hosts is a separate, compare-and-append configuration operation;
the existing transport still checks DNS answers and actual public socket peers.
"""

import hashlib
import ipaddress
import json
import re
import time
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import viewing
from ..network.targets import TargetPolicy, TargetPolicyError, public_address
from ..store import online as online_store
from . import catalog, maccms, service
from .models import OnlineSource, SourceId, normalize_host
from .providers import ProviderError


MAX_HOSTS = 128


class HostCheckError(catalog.CatalogError):
    """Fixed public errors that never include rejected URLs or credentials."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              hide_input_in_errors=True)


class HostCheckRequest(_Contract):
    external_id: str = Field(min_length=1, max_length=128)

    @field_validator("external_id")
    @classmethod
    def checked_id(cls, value):
        try:
            return maccms.external_id(value)
        except ProviderError:
            raise ValueError("invalid source item identity") from None


class HostCheckReport(_Contract):
    source_id: SourceId
    external_id: str
    missing_hosts: list[str]
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


def fingerprint(source):
    """Compare the complete administrator record, including concurrent edits."""
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


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


def _candidate(source_id, value, *, media):
    if not isinstance(value, str):
        raise ValueError("invalid source URL")
    parts = urlsplit(value)
    host = _checked_host(parts.hostname)
    # This authorizes only syntax against the exact host just extracted. It
    # performs no resolution, request, persistent trust or redirect expansion.
    canonical = TargetPolicy(source_id, [host])._canonical_url(value)[0]
    if media and not unquote(urlsplit(canonical).path).lower().endswith((".mp4", ".m3u8")):
        raise ValueError("unsupported source media")
    return host


def _suggestions(source, row):
    # Reuse the provider parser's group/entry/string limits before traversing
    # raw links. Unsupported host entries remain visible without being trusted.
    maccms.parse_detail(source, row)
    hosts = set()
    rejected = 0
    groups = row.get("vod_play_url", "")
    links = []
    if groups:
        for group in groups.split("$$$"):
            for entry in group.split("#"):
                links.append((entry.split("$", 1)[-1], True))
    poster = row.get("vod_pic")
    if poster:
        links.append((poster, False))
    for value, media in links:
        try:
            host = _candidate(source["id"], value, media=media)
        except (TargetPolicyError, ValueError, TypeError, UnicodeError):
            rejected += 1
            continue
        hosts.add(host)
        if len(hosts) > MAX_HOSTS:
            raise HostCheckError("online_host_candidate_limit", 422)
    return sorted(hosts), rejected


def check(access, source_id, external_id, *, operation=None):
    """Read one source item and return host names only, without mutating trust."""
    _management(access)
    with catalog._admit(access.owner, operation):
        source = catalog._ready_source(source_id)
        if source["provider"] != "maccms":
            raise HostCheckError("online_host_check_unsupported", 422)
        version = fingerprint(source)
        selected = maccms.external_id(external_id)
        action = source.get("provider_options", {}).get("detail_action", "detail")
        payload = maccms.fetch(source, {"ac": action, "ids": selected})
        rows = payload.get("list")
        if (not isinstance(rows, list) or len(rows) != 1
                or maccms.external_id(rows[0].get("vod_id")) != selected):
            raise ProviderError("provider_not_found")
        candidates, rejected = _suggestions(source, rows[0])
        _management(access)
        latest = online_store.get_online_source(source_id)
        if latest is None or fingerprint(latest) != version:
            raise HostCheckError("online_source_changed", 409)
        catalog._ready_source(source_id)
        allowed = source["allowed_hosts"]
        return {"source_id": source_id, "external_id": selected,
                "missing_hosts": [host for host in candidates if host not in allowed],
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
        record = OnlineSource.model_validate({**previous, "allowed_hosts": hosts,
                                             "updated_at": int(time.time())}).model_dump(mode="json")
        _management(access)
        connection.execute("UPDATE online_sources SET allowed_hosts=?,updated_at=? WHERE id=?",
                           (json.dumps(record["allowed_hosts"]), record["updated_at"], source_id))
    return record, True
