"""Short, bounded media transfer shared by source discovery and episode selection."""

from contextlib import ExitStack
from dataclasses import replace
from time import monotonic
from types import SimpleNamespace
from urllib.parse import urlsplit

from ..network.client import NetworkRequestError
from ..network.guard import NetworkOperationError, check_operation
from ..network.targets import TargetPolicy, TargetPolicyError
from . import diagnostic_probes as probes
from . import gateway, hls, liangzi, source_hosts
from .providers import ProviderError
from .sessions import SessionLimits


SAMPLE_BYTES = 256 * 1024
MIN_RATE_BYTES = 16 * 1024
MIN_SAMPLE_SECONDS = 0.01


def _realtime_ratio(duration, segment_size, sample_size, elapsed, body_bytes, body_seconds):
    if not duration or not segment_size or sample_size < MIN_RATE_BYTES or elapsed < MIN_SAMPLE_SECONDS:
        return None
    remaining = segment_size - sample_size
    if remaining < 0:
        return None
    if remaining:
        if body_seconds < MIN_SAMPLE_SECONDS or body_bytes < MIN_RATE_BYTES:
            return None
        # The prefix already paid for connection/first-byte latency. Project
        # only the remaining bytes at the rate measured after the first chunk.
        elapsed += remaining * body_seconds / body_bytes
    return round(duration / elapsed, 3)


def _timed_prefix(client, kind, url, restriction, duration):
    if restriction:
        gateway.requested_range(None, SessionLimits.max_segment_response_bytes, restriction)
    ranged = kind == "mp4" or restriction is not None
    start, end = restriction or (0, SAMPLE_BYTES - 1)
    requested = gateway.RequestedRange(start, min(end, start + SAMPLE_BYTES - 1)) if ranged else None
    headers = {"Range": requested.header} if requested else {}
    cap = SAMPLE_BYTES if ranged else SessionLimits.max_segment_response_bytes
    probes.check_access()
    started = monotonic()
    with client.stream("GET", url, headers=headers) as response:
        if response.status_code not in {200, 206, 416}:
            raise probes.ProbeFailure(f"http_{response.status_code}")
        _, _, length = gateway._metadata(response, SimpleNamespace(kind=kind), requested, cap, "GET")
        target_size = min(SAMPLE_BYTES, length)
        segment_size = length
        if duration and restriction:
            # A validated 206 proves the underlying resource length. The
            # manifest's byte range is the segment, not the entire resource.
            file_size = int(response.headers["Content-Range"].rsplit("/", 1)[1])
            segment_size = end - start + 1 if end < file_size else None
        received = 0
        prefix = bytearray()
        first_at, first_bytes = started, 0
        for part in response.iter_raw(chunk_size=min(8192, max(1, target_size))):
            check_operation()
            if not received:
                first_at, first_bytes = monotonic(), len(part)
            if len(prefix) < 512:
                prefix.extend(part[:512 - len(prefix)])
            received += len(part)
            if received >= target_size:
                break
        finished = monotonic()
    if received < target_size or not probes._media_signature(bytes(prefix), kind):
        raise probes.ProbeFailure("invalid_media_sample")
    seconds = max(0.001, finished - started)
    kbps = round(target_size * 8 / seconds / 1000, 1) if target_size >= MIN_RATE_BYTES else None
    ratio = _realtime_ratio(duration, segment_size, target_size, seconds,
                            target_size - first_bytes, finished - first_at)
    return {"sample_bytes": target_size, "sample_kbps": kbps, "realtime_ratio": ratio}


class SampleHosts:
    """Exact public media origins named by this sample, never inferred from redirects."""

    def __init__(self, source, approved, allow_new):
        self.source_id = source["id"]
        self.existing = set(source["allowed_hosts"])
        self.approved = set(approved)
        self.allow_new = allow_new
        self.observed = set()
        self.targets = {}

    def reference(self, address):
        try:
            host = source_hosts._checked_host(urlsplit(address).hostname)
            if host not in self.targets:
                self.targets[host] = TargetPolicy(self.source_id, [host])
            canonical = self.targets[host]._canonical_url(address)[0]
        except (ValueError, TypeError, UnicodeError):
            raise probes.ProbeFailure("invalid_media_host") from None
        if host not in self.existing:
            self.observed.add(host)
        if len(self.observed) > 12 or len(self.existing | self.observed) > source_hosts.MAX_HOSTS:
            raise probes.ProbeFailure("media_host_limit")
        return canonical

    def allowed(self):
        if not self.allow_new and self.observed - self.approved:
            raise probes.ProbeFailure("target_host_not_allowed")
        return sorted(self.existing | self.observed)


def sample_media(source, entry, media_policy, *, host_review=None):
    """Sample one selected URL on its configured route, under the caller's deadline."""
    if entry.reason or not entry.url or entry.transport not in {"hls", "mp4"}:
        raise probes.ProbeFailure(entry.reason or "unsupported_media", stage="entry")
    started = monotonic()
    stage = "entry"
    try:
        if host_review:
            host_review.reference(entry.url)
        hosts = host_review.allowed() if host_review else source["allowed_hosts"]
        if entry.resolver == "liangzi_page":
            probes.check_access()
            page = liangzi.resolve(source, entry.url, client_factory=lambda: probes.client_for(media_policy, hosts))
            if page.media_policy_fingerprint != media_policy.fingerprint:
                raise probes.ProbeFailure("source_changed")
            entry = replace(entry, url=page.url, resolver="direct")
        if entry.resolver != "direct":
            raise probes.ProbeFailure("unsupported_media")
        with ExitStack() as clients:
            client = clients.enter_context(probes.client_for(media_policy, hosts))
            kind = "mp4" if entry.transport == "mp4" else "manifest"
            url, restriction, init, duration = entry.url, None, None, None
            for _ in range(2):
                if kind != "manifest":
                    break
                stage = "manifest"
                kind, (url, restriction), init, duration = probes._manifest(
                    client, url, reference_url=host_review.reference if host_review else None)
                if host_review:
                    next_hosts = host_review.allowed()
                    if set(next_hosts) != set(hosts):
                        hosts = next_hosts
                        client = clients.enter_context(probes.client_for(media_policy, hosts))
            if kind == "manifest":
                raise probes.ProbeFailure("manifest_depth")
            stage = "media"
            if init:
                probes._sample(client, "map", *init)
            sample = _timed_prefix(client, kind, url, restriction, duration)
    except NetworkOperationError as error:
        if str(error) != "operation_timeout":
            raise
        raise probes.ProbeFailure("provider_timeout", stage=stage) from None
    except (probes.ProbeFailure, ProviderError, gateway.GatewayError, hls.ManifestError,
            NetworkRequestError, TargetPolicyError) as error:
        raise probes.ProbeFailure(str(error), stage=stage) from None
    return {**sample, "media_ms": round((monotonic() - started) * 1000)}
