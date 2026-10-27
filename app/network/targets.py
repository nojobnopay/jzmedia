"""Exact-host public target authorization and immutable per-client DNS pins."""

from dataclasses import dataclass, field
import ipaddress
import math
import queue
import re
import socket
import threading
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from .guard import limit_timeout, wait_for_event


URL_LIMIT = 8192
ADDRESS_LIMIT = 16
ORIGIN_LIMIT = 128
_SOURCE_ID = re.compile(r"src_[0-9a-f]{32}\Z")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_UNSAFE = re.compile(r"[\s\\\x00-\x1f\x7f]")
_ENCODED_CONTROL = re.compile(r"[\x00-\x1f\x7f\\]")
_NUMERIC_LABEL = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)\Z", re.I)
_V4_SPECIAL = (ipaddress.ip_network("192.0.0.0/24"), ipaddress.ip_network("192.88.99.0/24"))
_V6_GLOBAL_UNICAST = ipaddress.ip_network("2000::/3")
_V6_TUNNELS = (ipaddress.ip_network("2001::/32"), ipaddress.ip_network("2002::/16"))


class TargetPolicyError(ValueError):
    """A fixed category without the requested URL, signature, or DNS answers."""


def canonical_host(value: str) -> str:
    """Canonical exact host syntax; public-address checks happen separately."""
    if (not isinstance(value, str) or not value or len(value) > 253
            or _UNSAFE.search(value) or any(char in value for char in "/@?#%*")):
        raise TargetPolicyError("invalid_target_host")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        pass
    if ":" in value or "[" in value or "]" in value or value.endswith(".."):
        raise TargetPolicyError("invalid_target_host")
    try:
        normalized = value.removesuffix(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise TargetPolicyError("invalid_target_host") from None
    labels = normalized.split(".")
    if (len(normalized) > 253 or not all(_LABEL.fullmatch(label) for label in labels)
            or all(_NUMERIC_LABEL.fullmatch(label) for label in labels)
            or normalized == "localhost" or normalized.endswith((".localhost", ".local"))):
        raise TargetPolicyError("invalid_target_host")
    return normalized


def public_address(value: str) -> str:
    """Reject special, private and transition addresses before any socket opens."""
    try:
        if not isinstance(value, str) or "%" in value:
            raise ValueError
        address = ipaddress.ip_address(value)
    except ValueError:
        raise TargetPolicyError("non_public_target_address") from None
    if (not address.is_global or address.is_multicast or address.is_reserved
            or address.is_unspecified or address.is_loopback or address.is_link_local
            or address.is_private):
        raise TargetPolicyError("non_public_target_address")
    if isinstance(address, ipaddress.IPv4Address):
        if any(address in network for network in _V4_SPECIAL):
            raise TargetPolicyError("non_public_target_address")
    elif (address not in _V6_GLOBAL_UNICAST or address.ipv4_mapped is not None
          or address.sixtofour is not None or address.teredo is not None
          or any(address in network for network in _V6_TUNNELS)):
        # Only native global unicast is supported. This also excludes both
        # standard NAT64 prefixes and mapped/compatible IPv4 encodings.
        raise TargetPolicyError("non_public_target_address")
    return address.compressed


def _system_addresses(host, port):
    records = socket.getaddrinfo(host, port, family=socket.AF_UNSPEC,
                                 type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    if len(records) > 256:
        raise TargetPolicyError("too_many_target_addresses")
    return tuple(record[4][0] for record in records)


@dataclass
class _Lookup:
    resolver: object = field(repr=False)
    host: str = field(repr=False)
    port: int
    deadline: float
    ready: threading.Event = field(default_factory=threading.Event, repr=False)
    result: object = field(default=None, repr=False)
    error: str | None = None


class _ResolverPool:
    """Two daemon workers and two queue slots bound timed-out resolver work."""

    def __init__(self):
        self.pending = queue.Queue(maxsize=2)
        for index in range(2):
            threading.Thread(target=self._worker, name=f"online-dns-{index}", daemon=True).start()

    def _worker(self):
        while True:
            lookup = self.pending.get()
            try:
                if time.monotonic() >= lookup.deadline:
                    lookup.error = "target_dns_timeout"
                else:
                    try:
                        lookup.result = lookup.resolver(lookup.host, lookup.port)
                        if time.monotonic() >= lookup.deadline:
                            lookup.error = "target_dns_timeout"
                    except TargetPolicyError as error:
                        lookup.error = str(error) if str(error) in {
                            "too_many_target_addresses", "target_dns_timeout"} else "target_dns_failed"
                    except Exception:
                        # Boundary isolates arbitrary platform resolver failures;
                        # no exception text or hostname crosses into diagnostics.
                        lookup.error = "target_dns_failed"
            finally:
                lookup.ready.set()
                self.pending.task_done()

    def resolve(self, resolver, host, port, timeout):
        lookup = _Lookup(resolver, host, port, time.monotonic() + timeout)
        try:
            self.pending.put_nowait(lookup)
        except queue.Full:
            raise TargetPolicyError("target_dns_busy") from None
        if not wait_for_event(lookup.ready, max(0, lookup.deadline - time.monotonic())):
            raise TargetPolicyError("target_dns_timeout")
        if time.monotonic() >= lookup.deadline:
            raise TargetPolicyError("target_dns_timeout")
        if lookup.error:
            raise TargetPolicyError(lookup.error)
        return lookup.result


_resolver_pool = None
_resolver_lock = threading.Lock()


def _lookup_addresses(resolver, host, port, timeout):
    global _resolver_pool
    with _resolver_lock:
        if _resolver_pool is None:
            _resolver_pool = _ResolverPool()
        pool = _resolver_pool
    return pool.resolve(resolver, host, port, limit_timeout(timeout))


def resolve_operator_addresses(host, port, timeout):
    """Bound DNS for an administrator-configured proxy, which may be on a LAN.

    This is never a source-target validator. It only shares the bounded worker
    pool with public validation; callers must already hold a proxy snapshot.
    """
    if (not isinstance(host, str) or not host or len(host) > 253
            or _UNSAFE.search(host) or any(char in host for char in "/@?#%*")
            or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
            or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise TargetPolicyError("invalid_operator_resolution")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            canonical = host.removesuffix(".").encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise TargetPolicyError("invalid_operator_resolution") from None
        if not all(_LABEL.fullmatch(label) for label in canonical.split(".")):
            raise TargetPolicyError("invalid_operator_resolution")
        answers = _lookup_addresses(_system_addresses, canonical, port, timeout)
    else:
        answers = (literal.compressed,)
    if not isinstance(answers, (tuple, list)) or not answers or len(answers) > 256:
        raise TargetPolicyError("invalid_target_dns_answers")
    try:
        if any(not isinstance(value, str) or "%" in value for value in answers):
            raise ValueError
        addresses = tuple(dict.fromkeys(ipaddress.ip_address(value).compressed for value in answers))
    except ValueError:
        raise TargetPolicyError("invalid_target_dns_answers") from None
    if len(addresses) > ADDRESS_LIMIT:
        raise TargetPolicyError("too_many_target_addresses")
    return addresses


@dataclass(frozen=True)
class ValidatedTarget:
    url: str = field(repr=False)
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]
    origin: str
    authority: str


class TargetPolicy:
    """Authorize each URL and retain only the first approved IP set per peer.

    Resolver/address-validator injection is an internal testing seam, not an
    administrator option. Production construction always uses public_address.
    """

    def __init__(self, source_id, allowed_hosts, *, resolver=None, dns_timeout_seconds=5.0):
        if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
            raise TargetPolicyError("invalid_target_policy")
        if (not isinstance(allowed_hosts, (list, tuple)) or not 1 <= len(allowed_hosts) <= ORIGIN_LIMIT
                or isinstance(dns_timeout_seconds, bool) or not isinstance(dns_timeout_seconds, (int, float))
                or not 0 < dns_timeout_seconds <= 30):
            raise TargetPolicyError("invalid_target_policy")
        self.source_id = source_id
        self.allowed_hosts = frozenset(canonical_host(host) for host in allowed_hosts)
        self._resolver = _system_addresses if resolver is None else resolver
        if not callable(self._resolver):
            raise TargetPolicyError("invalid_target_policy")
        self._timeout = float(dns_timeout_seconds)
        self._pins = {}
        self._lock = threading.Lock()

    def __repr__(self):
        return f"TargetPolicy(source_id={self.source_id!r}, pinned_origins={len(self._pins)})"

    def _validate_address(self, value):
        return public_address(value)

    def _canonical_url(self, value):
        if (not isinstance(value, str) or not value or len(value) > URL_LIMIT
                or _UNSAFE.search(value) or "#" in value):
            raise TargetPolicyError("invalid_target_url")
        try:
            value.encode("utf-8")
            parts = urlsplit(value)
            port = parts.port
            if (parts.scheme not in {"http", "https"} or not parts.hostname
                    or parts.username is not None or parts.password is not None
                    or any(char in parts.netloc for char in "@%")
                    or parts.netloc.endswith(":")):
                raise ValueError
            host = canonical_host(parts.hostname)
            port = port if port is not None else 443 if parts.scheme == "https" else 80
            if not 1 <= port <= 65535:
                raise ValueError
            if _ENCODED_CONTROL.search(unquote(parts.path + "?" + parts.query)):
                raise ValueError
        except (ValueError, UnicodeError):
            raise TargetPolicyError("invalid_target_url") from None
        if host not in self.allowed_hosts:
            raise TargetPolicyError("target_host_not_allowed")
        bracketed = f"[{host}]" if ":" in host else host
        authority = bracketed if port == (443 if parts.scheme == "https" else 80) else f"{bracketed}:{port}"
        origin = f"{parts.scheme}://{bracketed}:{port}"
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="/?%:@!$&'()*+,;=-._~")
        url = urlunsplit((parts.scheme, authority, path, query, ""))
        if len(url) > URL_LIMIT:
            raise TargetPolicyError("invalid_target_url")
        return url, parts.scheme, host, port, origin, authority

    def resolve(self, url, base_url=None):
        if base_url is not None:
            base = self._canonical_url(base_url)[0]
            if (not isinstance(url, str) or not url or len(url) > URL_LIMIT
                    or _UNSAFE.search(url) or "#" in url):
                raise TargetPolicyError("invalid_target_url")
            url = urljoin(base, url)
        canonical, scheme, host, port, origin, authority = self._canonical_url(url)
        key = (host, port)
        with self._lock:
            cached = self._pins.get(key)
        if cached is None:
            try:
                literal = ipaddress.ip_address(host)
            except ValueError:
                answers = _lookup_addresses(self._resolver, host, port, self._timeout)
            else:
                answers = (literal.compressed,)
            if not isinstance(answers, (tuple, list)) or not answers or len(answers) > 256:
                raise TargetPolicyError("invalid_target_dns_answers")
            # Validate every answer before applying the deduplicated address cap.
            approved = tuple(dict.fromkeys(self._validate_address(value) for value in answers))
            if len(approved) > ADDRESS_LIMIT:
                raise TargetPolicyError("too_many_target_addresses")
            with self._lock:
                if key not in self._pins and len(self._pins) >= ORIGIN_LIMIT:
                    raise TargetPolicyError("target_origin_limit")
                cached = self._pins.setdefault(key, approved)
        return ValidatedTarget(canonical, scheme, host, port, cached, origin, authority)

    def pin_for(self, host, port):
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise TargetPolicyError("unregistered_target_connection")
        try:
            host = canonical_host(host.decode("ascii") if isinstance(host, bytes) else host)
        except (UnicodeError, ValueError):
            raise TargetPolicyError("unregistered_target_connection") from None
        with self._lock:
            value = self._pins.get((host, port))
        if value is None:
            raise TargetPolicyError("unregistered_target_connection")
        return value
