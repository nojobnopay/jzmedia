"""Bounded, read-only probes using the same destination and response checks as playback."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from urllib.parse import urlsplit

from ..network.client import NetworkRequestError, create_client
from ..network.guard import check_operation
from ..network.policy import resolve_policy_state
from ..network.targets import TargetPolicy, TargetPolicyError
from . import gateway, hls, maccms, open_catalog
from .providers import ProviderError
from .sessions import SessionLimits


_read_guard = ContextVar("assistant_diagnosis_read_guard", default=None)


@contextmanager
def guarded_reads(guard):
    token = _read_guard.set(guard)
    try:
        check_access()
        yield
    finally:
        _read_guard.reset(token)


def check_access():
    check_operation()
    guard = _read_guard.get()
    if guard is not None:
        guard()
    check_operation()


class ProbeFailure(Exception):
    """Only fixed categories and HTTP status numbers reach evidence."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def snapshot(state, purpose):
    policy = resolve_policy_state(state, purpose)
    return replace(policy, connect_timeout_seconds=min(3, policy.connect_timeout_seconds),
                   read_timeout_seconds=min(4, policy.read_timeout_seconds))


def client_for(policy, hosts):
    targets = TargetPolicy(policy.source_id, hosts, dns_timeout_seconds=policy.connect_timeout_seconds)
    return create_client(policy, targets=targets)


def _payload(url, params, policy, hosts):
    try:
        with client_for(policy, hosts) as client:
            check_access()
            response = client.request("GET", url, params=params, headers={"Accept": "application/json"})
            if response.status_code != 200:
                raise ProbeFailure(f"http_{response.status_code}")
            return maccms.decode(response.content)
    except (ProviderError, NetworkRequestError, TargetPolicyError) as exc:
        raise ProbeFailure(str(exc)) from None


def catalogue(source, url, policy, *, candidate=False):
    # A candidate gets access to just its published host, never to media hosts.
    hosts = [urlsplit(url).hostname] if candidate else source["allowed_hosts"]
    action = source["provider_options"].get("detail_action", "detail")
    payload = _payload(url, {"ac": action, "pg": "1"}, policy, hosts)
    try:
        return len(maccms._search_result(payload, 1)["items"])
    except ProviderError as exc:
        raise ProbeFailure(exc.category) from None


def item_detail(source, url, policy, item, *, candidate=False):
    try:
        if source["provider"] == "open_catalog":
            detail = open_catalog.detail(source, item["external_id"])
        else:
            hosts = [urlsplit(url).hostname] if candidate else source["allowed_hosts"]
            params = {"ac": source["provider_options"].get("detail_action", "detail"), "ids": item["external_id"]}
            payload = _payload(url, params, policy, hosts)
            detail = maccms.detail_from_response({**source, "api_base_url": url}, item["external_id"], payload)
    except ProviderError as exc:
        raise ProbeFailure(exc.category) from None
    if item.get("title") and item["title"].strip().casefold() != detail.title.strip().casefold():
        raise ProbeFailure("item_mismatch")
    if item.get("kind") and item["kind"] != detail.kind:
        raise ProbeFailure("item_mismatch")
    return detail


def selected_entry(detail, line_id, episode_key):
    choices = [entry for line in detail.lines if not line_id or line.id == line_id
               for entry in line.entries if not episode_key
               or maccms.episode_external_id(line.name, entry.label) == episode_key]
    if not choices:
        raise ProbeFailure("entry_missing")
    if len(choices) > 1:
        raise ProbeFailure("entry_ambiguous")
    entry = choices[0]
    if entry.reason or entry.resolver != "direct" or not entry.url:
        raise ProbeFailure(entry.reason or "unsupported_media")
    return entry


def _manifest(client, url):
    check_access()
    with client.stream("GET", url) as response:
        if response.status_code != 200:
            raise ProbeFailure(f"http_{response.status_code}")
        _, _, length = gateway._metadata(response, SimpleNamespace(kind="manifest"), None, hls.MANIFEST_BYTES, "GET")
        body = bytearray()
        for chunk in response.iter_raw(chunk_size=8192):
            check_operation()
            if len(body) + len(chunk) > hls.MANIFEST_BYTES:
                raise ProbeFailure("response_byte_limit")
            body.extend(chunk)
        if length is not None and length != len(body):
            raise ProbeFailure("upstream_length_mismatch")
        base = str(response.url)
    references, segments, variants = {}, [], []

    def register(address, *, kind, byte_range=None):
        canonical = client.targets._canonical_url(address)[0]
        path = f"/sample/{len(references)}"
        references[path] = (canonical, byte_range)
        return path

    hls.rewrite_manifest(bytes(body), base, register, on_media=segments.extend, on_master=variants.extend)
    for segment in segments:
        if not segment.gap:
            return "segment", references[segment.path], references.get(segment.init_path)
    for path, kind in variants:
        if kind == "variant":
            return "manifest", references[path], None
    raise ProbeFailure("no_media_sample")


def _sample(client, kind, url, restriction):
    start, end = restriction or (0, 511)
    requested = gateway.RequestedRange(start, min(end, start + 511))
    # Ordinary HLS segments are fetched whole by the player. Stream just their
    # prefix here, allowing a server that supports GET without byte ranges.
    ranged = kind == "mp4" or restriction is not None
    headers = {"Range": requested.header} if ranged else {}
    check_access()
    with client.stream("GET", url, headers=headers) as response:
        if response.status_code not in {200, 206}:
            raise ProbeFailure(f"http_{response.status_code}")
        cap = 512 if ranged else SessionLimits.max_segment_response_bytes
        _, _, length = gateway._metadata(response, SimpleNamespace(kind=kind), requested if ranged else None, cap, "GET")
        prefix = next(response.iter_raw(chunk_size=min(512, length or 512)), b"")
        if not prefix or len(prefix) != min(512, length) or not _media_signature(prefix, kind):
            raise ProbeFailure("invalid_media_sample")
        check_operation()


def _media_signature(prefix, kind):
    # A 200 binary/blob MIME type alone does not distinguish a video from an
    # image, a block page or an arbitrary document. Verify a container header.
    box = prefix[4:8] in {b"ftyp", b"styp", b"moof", b"moov", b"mdat", b"sidx", b"emsg", b"free", b"prft"}
    size = int.from_bytes(prefix[:4], "big")
    if box and (size in {0, 1} or size >= 8):
        return True
    if kind == "mp4":
        return False
    return ((len(prefix) >= 188 and prefix[0] == 0x47 and (len(prefix) <= 188 or prefix[188] == 0x47))
            or prefix.startswith((b"ID3", b"\x0b\x77"))
            or len(prefix) >= 4 and prefix[0] == 0xff and prefix[1] & 0xe0 == 0xe0)


@dataclass
class MediaResult:
    code: str
    stage: str
    # Compare the exact failed request (including byte range) across routes.
    # Signed URLs may occur here; this object stays inside the probe.
    trail: list = field(repr=False)


def media(source, entry, policy):
    stage, trail = "bytes", []
    try:
        with client_for(policy, source["allowed_hosts"]) as client:
            url, restriction, initialization = entry.url, None, None
            kind = "mp4" if entry.transport == "mp4" else "manifest"
            for _ in range(2):
                if kind != "manifest":
                    break
                stage = "manifest"
                trail.append((stage, url, None))
                check_operation()
                kind, (url, restriction), initialization = _manifest(client, url)
            if kind == "manifest":
                raise ProbeFailure("manifest_depth")
            stage = "bytes"
            if initialization:
                trail.append((stage, *initialization))
                check_operation()
                _sample(client, "map", *initialization)
            trail.append((stage, url, restriction))
            check_operation()
            _sample(client, kind, url, restriction)
        return MediaResult("ok", stage, trail)
    except (ProbeFailure, NetworkRequestError, TargetPolicyError, gateway.GatewayError, hls.ManifestError) as exc:
        return MediaResult(str(exc), stage, trail)
