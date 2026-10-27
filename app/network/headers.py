"""Bounded caller headers and explicit, origin-scoped upstream credentials."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from urllib.parse import urlsplit

from .targets import TargetPolicyError, ValidatedTarget, canonical_host


HEADER_COUNT_LIMIT = 32
HEADER_VALUE_LIMIT = 8192
HEADER_BYTES_LIMIT = 16384
GRANT_COUNT_LIMIT = 128
_SOURCE_ID = re.compile(r"src_[0-9a-f]{32}\Z")
_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}\Z")
_VALUE_UNSAFE = re.compile(r"[\x00-\x1f\x7f]")
_ORIGIN_UNSAFE = re.compile(r"[\s\\\x00-\x1f\x7f]")
_ORDINARY = {
    "accept": "Accept", "accept-language": "Accept-Language", "user-agent": "User-Agent",
    "range": "Range", "if-range": "If-Range", "if-none-match": "If-None-Match",
    "if-modified-since": "If-Modified-Since",
}
_CREDENTIALS = {
    "authorization": "Authorization", "cookie": "Cookie", "referer": "Referer", "origin": "Origin",
}


class HeaderPolicyError(ValueError):
    """Fixed categories never include header values or credential material."""


def _canonical_origin(value):
    if (not isinstance(value, str) or not value or len(value) > 512
            or _ORIGIN_UNSAFE.search(value) or any(char in value for char in "?#")):
        raise HeaderPolicyError("invalid_header_grant_origin")
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or any(char in parts.netloc for char in "@%")
                or parts.netloc.endswith(":") or parts.path not in {"", "/"}):
            raise ValueError
        host = canonical_host(parts.hostname)
        port = parts.port if parts.port is not None else 443 if parts.scheme == "https" else 80
        if not 1 <= port <= 65535:
            raise ValueError
    except (ValueError, UnicodeError, TargetPolicyError):
        raise HeaderPolicyError("invalid_header_grant_origin") from None
    authority = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{authority}:{port}"


def _header_items(headers, allowed):
    if headers is None:
        return ()
    if not isinstance(headers, (Mapping, list, tuple)) or len(headers) > HEADER_COUNT_LIMIT:
        raise HeaderPolicyError("invalid_upstream_headers")
    # Preserve repeated fields from httpx.Headers so duplicate credentials and
    # case-insensitive aliases cannot acquire order-dependent meaning.
    if isinstance(headers, Mapping):
        entries = headers.multi_items() if callable(getattr(headers, "multi_items", None)) else headers.items()
    else:
        entries = headers
    result = []
    seen = set()
    size = 0
    for entry in entries:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise HeaderPolicyError("invalid_upstream_headers")
        name, value = entry
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise HeaderPolicyError("invalid_upstream_headers")
        lower = name.lower()
        # A small allowlist also rejects Proxy-*, hop-by-hop and Connection
        # extensions, Host, and every jzmedia authentication/session field.
        if lower not in allowed or lower in seen:
            raise HeaderPolicyError("upstream_header_not_allowed")
        if (not isinstance(value, str) or len(value) > HEADER_VALUE_LIMIT
                or _VALUE_UNSAFE.search(value)):
            raise HeaderPolicyError("invalid_upstream_headers")
        try:
            size += len(name.encode("ascii")) + len(value.encode("ascii")) + 4
        except UnicodeError:
            raise HeaderPolicyError("invalid_upstream_headers") from None
        if (size > HEADER_BYTES_LIMIT or len(result) >= HEADER_COUNT_LIMIT
                or lower == "cookie" and any(
                    cookie.split("=", 1)[0].strip().strip('"').lower() == "jz_viewing"
                    for cookie in value.split(";"))):
            raise HeaderPolicyError("invalid_upstream_headers")
        seen.add(lower)
        result.append((allowed[lower], value))
    return tuple(result)


@dataclass(frozen=True)
class HeaderGrant:
    """A server-owned secret set authorized for one exact scheme/host/port."""

    origin: str
    headers: object = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, "origin", _canonical_origin(self.origin))
        object.__setattr__(self, "headers", _header_items(self.headers, _CREDENTIALS))


class HeaderPolicy:
    def __init__(self, source_id, grants=()):
        if (not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id)
                or not isinstance(grants, (tuple, list)) or len(grants) > GRANT_COUNT_LIMIT):
            raise HeaderPolicyError("invalid_header_policy")
        self.source_id = source_id
        self._grants = {}
        for grant in grants:
            if not isinstance(grant, HeaderGrant) or grant.origin in self._grants:
                raise HeaderPolicyError("invalid_header_policy")
            self._grants[grant.origin] = grant.headers

    def __repr__(self):
        return f"HeaderPolicy(source_id={self.source_id!r}, grants={len(self._grants)})"

    def headers_for(self, target: ValidatedTarget, headers=None):
        if not isinstance(target, ValidatedTarget):
            raise HeaderPolicyError("invalid_header_target")
        ordinary = _header_items(headers, _ORDINARY)
        granted = self._grants.get(target.origin, ())
        combined = (*ordinary, *granted)
        if (len(combined) > HEADER_COUNT_LIMIT
                or sum(len(name) + len(value) + 4 for name, value in combined) > HEADER_BYTES_LIMIT):
            raise HeaderPolicyError("invalid_upstream_headers")
        # Fresh selection for each hop removes all credentials from the prior
        # origin, including cookies; no response Set-Cookie is persisted here.
        return dict(combined)
