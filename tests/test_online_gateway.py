"""Real loopback gateway HTTP, HLS, deadlines and downstream cancellation."""

import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler
import re
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from starlette.requests import ClientDisconnect

from app.network.client import create_client
from app.network.policy import resolve_online_policy
from app.online import gateway
from app.online.models import OnlinePlaybackIdentity
from app.online.sessions import SessionError, SessionLimits, SessionRegistry
from test_network_transport import LoopbackPolicy, running_server


pytest_plugins = ["test_network_transport"]

OWNER = "gateway-owner-fixture"
DATA = bytes(range(100))
MASTER = (b"#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-STREAM-INF:BANDWIDTH=1000\n"
          b"child.m3u8?token=media-secret\n")
CHILD = (b'#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:4\n'
         b'#EXT-X-MAP:URI="asset.bin?init=private-secret",BYTERANGE="4@0"\n'
         b'#EXTINF:4.0,\n#EXT-X-BYTERANGE:8@4\nasset.bin?part=private-secret\n'
         b'#EXTINF:4.0,\n#EXT-X-BYTERANGE:8\nasset.bin?part=private-secret\n#EXT-X-ENDLIST\n')
OVERSIZED_MANIFEST = (b"#EXTM3U\n" + b"# harmless comment\n" * 150 +
                      b"#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nasset.bin\n#EXT-X-ENDLIST\n")


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return predicate()


@pytest.fixture
def upstream():
    records = []
    stop, release, two_blocked = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    blocked = [0]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, *_args):
            pass

        def handle_one_request(self):
            try:
                super().handle_one_request()
            except OSError:
                self.close_connection = True

        def do_HEAD(self):
            self.respond(head=True)

        def do_GET(self):
            self.respond(head=False)

        def respond(self, head):
            records.append({"method": self.command, "path": self.path, "headers": dict(self.headers)})
            path = urlsplit(self.path).path
            if path == "/slow-headers.mp4":
                stop.wait(timeout=1.5)
            body, content_type = DATA, "video/mp4"
            status, extra, length = 200, {}, None
            unknown = path in {"/unknown.mp4", "/too-large.m3u8"}
            if path == "/head-large.mp4":
                length = 10**9
            elif path == "/ignore.mp4":
                body = DATA[:16]
            elif path == "/html.mp4":
                body, content_type = b"<html>no</html>", "text/html"
            elif path == "/master.m3u8":
                body, content_type = MASTER, "application/vnd.apple.mpegurl"
            elif path == "/child.m3u8":
                body, content_type = CHILD, "application/x-mpegurl"
            elif path == "/too-large.m3u8":
                body, content_type = OVERSIZED_MANIFEST, "application/vnd.apple.mpegurl"
            elif path == "/encrypted.m3u8":
                body = (b'#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
                        b'#EXTINF:4.0,\nasset.bin\n#EXT-X-ENDLIST\n')
                content_type = "application/vnd.apple.mpegurl"
            elif path == "/partial-invalid.m3u8":
                body = (b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nasset.bin\n"
                        b"#EXTINF:4.0,\nhttps://unregistered.invalid/second.ts?secret=private-token\n#EXT-X-ENDLIST\n")
                content_type = "application/vnd.apple.mpegurl"
            elif path == "/asset.bin":
                content_type = "application/octet-stream"
            elif path == "/fragment.m4s":
                content_type = "video/iso.segment"
            elif path in {"/blocked.mp4", "/slow-body.mp4"}:
                body = DATA[:16]
            raw_range = self.headers.get("Range")
            if raw_range and path not in {"/ignore.mp4", "/head-large.mp4"}:
                first, last = raw_range.removeprefix("bytes=").split("-", 1)
                if first:
                    start, end = int(first), min(int(last), len(body) - 1)
                else:
                    start, end = max(0, len(body) - int(last)), len(body) - 1
                if start >= len(body):
                    status, extra, body = 416, {"Content-Range": f"bytes */{len(body)}"}, b""
                else:
                    total = len(body)
                    body = body[start:end + 1]
                    status, extra = 206, {"Content-Range": f"bytes {start}-{end}/{total}"}
                    if path == "/false-range.mp4":
                        extra["Content-Range"] = f"bytes {start + 1}-{end + 1}/{total}"
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Set-Cookie", "origin_cookie=origin-secret; Path=/")
                self.send_header("ETag", '"fixture-v1"')
                for name, value in extra.items():
                    self.send_header(name, value)
                if not unknown:
                    self.send_header("Content-Length", str(len(body) if length is None else length))
                else:
                    self.send_header("Connection", "close")
                    self.close_connection = True
                self.end_headers()
                if head:
                    return
                if path == "/blocked.mp4":
                    with lock:
                        blocked[0] += 1
                        if blocked[0] >= 2:
                            two_blocked.set()
                    release.wait(timeout=2)
                if path == "/slow-body.mp4":
                    for value in body:
                        if stop.wait(timeout=.1):
                            return
                        self.wfile.write(bytes([value]))
                        self.wfile.flush()
                else:
                    self.wfile.write(body)
            except OSError:
                self.close_connection = True

    with running_server(Handler) as url:
        try:
            yield {"url": url, "records": records, "stop": stop, "release": release, "two_blocked": two_blocked}
        finally:
            stop.set()
            release.set()


@pytest.fixture
def bridge(source, upstream):
    limits = SessionLimits(ttl_seconds=60, idle_seconds=30, request_seconds=3,
                           max_session_bytes=1024**2, max_reserved_bytes=512 * 1024,
                           max_mp4_response_bytes=32, max_segment_response_bytes=32,
                           max_manifest_bytes=2048, max_subtitle_bytes=2048)
    registries = []

    def make(path="/file.mp4", transport="mp4", *, configured=None):
        registry = SessionRegistry(configured or limits)
        registries.append(registry)
        snapshot = resolve_online_policy(source, "media")
        reader = create_client(snapshot, targets=LoopbackPolicy(source, ["127.0.0.1"]))
        session = registry.create(OnlinePlaybackIdentity(source_id=source, item_id="itm_" + "e" * 32,
                                                         kind="movie"), OWNER, reader,
                                  upstream["url"] + path, transport)
        app = FastAPI()

        async def relay(request, sid, rid=None):
            try:
                current = registry.get(sid, OWNER)
                resource = registry.resource(current, rid) if rid is not None else current.entry
                return await gateway.stream_response(request, registry, current, resource)
            except SessionError as error:
                return gateway.error_response(error)

        @app.api_route("/api/online/sessions/{sid}/media", methods=["GET", "HEAD"])
        async def media(request: Request, sid: str):
            return await relay(request, sid)

        @app.api_route("/api/online/sessions/{sid}/resources/{rid}", methods=["GET", "HEAD"])
        async def resource(request: Request, sid: str, rid: str):
            return await relay(request, sid, rid)

        return SimpleNamespace(registry=registry, session=session, app=app,
                               url=session.descriptor.media_url, limits=configured or limits)

    gateway.start_workers()
    try:
        yield make
    finally:
        for registry in registries:
            registry.close_all()
        upstream["stop"].set()
        upstream["release"].set()
        drained = wait_until(lambda: all(registry.stats()["active_requests"] == 0 for registry in registries))
        gateway.shutdown_workers()
        assert drained, "gateway workers/leases must finish after each isolated test"


def test_mp4_head_reports_full_length_without_reading_or_charging_body(bridge, upstream):
    harness = bridge("/head-large.mp4")
    with TestClient(harness.app) as client:
        response = client.head(harness.url)
    assert response.status_code == 200 and response.content == b""
    assert response.headers["content-length"] == "1000000000"
    assert response.headers["content-type"] == "video/mp4"
    assert [record["method"] for record in upstream["records"]] == ["HEAD"]
    assert harness.registry.stats()["consumed_bytes"] == 0


@pytest.mark.parametrize("requested,start,end,forwarded", [("bytes=10-", 10, 41, "bytes=10-41"),
                                                          ("bytes=2-9", 2, 9, "bytes=2-9"),
                                                          ("bytes=-12", 88, 99, "bytes=-12")])
def test_mp4_range_is_bounded_and_validated(bridge, upstream, requested, start, end, forwarded):
    harness = bridge()
    with TestClient(harness.app) as client:
        response = client.get(harness.url, headers={"Range": requested})
    assert response.status_code == 206
    assert response.content == DATA[start:end + 1]
    assert response.headers["content-range"] == f"bytes {start}-{end}/100"
    assert int(response.headers["content-length"]) == end - start + 1
    assert upstream["records"][0]["headers"]["Range"] == forwarded
    assert harness.registry.stats()["consumed_bytes"] == len(response.content)


def test_unsatisfied_range_returns_safe_content_range(bridge):
    harness = bridge()
    with TestClient(harness.app) as client:
        response = client.get(harness.url, headers={"Range": "bytes=500-"})
    assert response.status_code == 416 and response.headers["content-range"] == "bytes */100"
    assert response.json()["code"] == "range_not_satisfiable"


@pytest.mark.parametrize("path,headers,code,status", [
    ("/file.mp4", {}, "range_required", 416),
    ("/ignore.mp4", {"Range": "bytes=0-7"}, "upstream_ignored_range", 502),
    ("/false-range.mp4", {"Range": "bytes=0-7"}, "upstream_range_mismatch", 502),
    ("/html.mp4", {}, "unsupported_upstream_content_type", 502),
    ("/unknown.mp4", {}, "invalid_upstream_headers", 502),
])
def test_invalid_upstream_metadata_never_becomes_a_truncated_success(bridge, path, headers, code, status):
    harness = bridge(path)
    with TestClient(harness.app) as client:
        response = client.get(harness.url, headers=headers)
    assert response.status_code == status and response.json()["code"] == code
    assert harness.registry.stats()["consumed_bytes"] == 0
    assert "Set-Cookie" not in response.headers


def test_hls_nested_manifests_maps_and_explicit_ranges_are_opaque_and_bound(bridge, upstream):
    harness = bridge("/master.m3u8", "hls")
    with TestClient(harness.app) as client:
        master = client.get(harness.url, headers={"x-api-token": "server-secret", "x-viewing-session": "watch-secret",
                                                "x-receiver-token": "receiver-secret", "Authorization": "Bearer server-auth",
                                                "Cookie": "jz_viewing=watch-secret"})
        assert master.status_code == 200
        child_path = next(line for line in master.text.splitlines() if line and not line.startswith("#"))
        child = client.get(child_path)
        assert child.status_code == 200
        map_path = re.search(r'URI="([^"]+)"', child.text).group(1)
        segments = [line for line in child.text.splitlines() if line and not line.startswith("#")]
        assert len(segments) == 2 and segments[0] != segments[1]
        initialized = client.get(map_path)
        first, second = (client.get(path) for path in segments)
        assert initialized.status_code == first.status_code == second.status_code == 206
        assert initialized.content == DATA[:4]
        assert first.content == DATA[4:12] and second.content == DATA[12:20]
        before = len(upstream["records"])
        mismatch = client.get(segments[0], headers={"Range": "bytes=0-3"})
        assert mismatch.status_code == 416 and mismatch.json()["code"] == "resource_range_mismatch"
        assert len(upstream["records"]) == before
    assert '#EXT-X-BYTERANGE:8@12' in child.text
    for response in (master, child, initialized, first, second):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "Set-Cookie" not in response.headers
    for response in (master, child):
        assert not any(secret in response.text for secret in ("127.0.0.1", "media-secret", "private-secret"))
    binary = [record for record in upstream["records"] if urlsplit(record["path"]).path == "/asset.bin"]
    assert [record["headers"]["Range"] for record in binary] == ["bytes=0-3", "bytes=4-11", "bytes=12-19"]
    for record in upstream["records"]:
        names = {name.lower() for name in record["headers"]}
        assert not names & {"x-api-token", "x-viewing-session", "x-receiver-token", "authorization", "cookie", "proxy-authorization"}


def test_unknown_length_manifest_over_limit_is_charged_before_rejection(bridge):
    harness = bridge("/too-large.m3u8", "hls")
    with TestClient(harness.app) as client:
        response = client.get(harness.url)
    assert response.status_code == 413 and response.json()["code"] == "response_byte_limit"
    assert harness.registry.stats()["consumed_bytes"] == len(OVERSIZED_MANIFEST)
    assert len(OVERSIZED_MANIFEST) > harness.limits.max_manifest_bytes


def test_total_session_budget_rejects_before_another_upstream_request(bridge, upstream):
    limits = replace(SessionLimits(), max_session_bytes=48, max_reserved_bytes=64,
                     max_mp4_response_bytes=32)
    harness = bridge(configured=limits)
    with TestClient(harness.app) as client:
        first = client.get(harness.url, headers={"Range": "bytes=0-31"})
        assert first.status_code == 206 and first.content == DATA[:32]
        assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)
        refused = client.get(harness.url, headers={"Range": "bytes=32-63"})
    assert refused.status_code == 429 and refused.json()["code"] == "byte_budget_exhausted"
    assert len(upstream["records"]) == 1
    assert harness.registry.stats()["consumed_bytes"] == 32


def test_manifest_head_does_not_advertise_an_incorrect_rewritten_length(bridge, upstream):
    harness = bridge("/master.m3u8", "hls")
    with TestClient(harness.app) as client:
        response = client.head(harness.url)
    assert response.status_code == 200 and response.content == b""
    assert "content-length" not in response.headers
    assert response.headers["content-type"] == "application/vnd.apple.mpegurl"
    assert harness.registry.stats()["consumed_bytes"] == 0
    assert [(record["method"], record["path"]) for record in upstream["records"]] == [("HEAD", "/master.m3u8")]


@pytest.mark.parametrize("value", ["bytes=0-", "bytes=0-1", "bytes=12-23", "bytes=-32"])
def test_manifest_range_probe_returns_the_complete_rewritten_representation(bridge, upstream, value):
    harness = bridge("/master.m3u8", "hls")
    with TestClient(harness.app) as client:
        response = client.get(harness.url, headers={"Range": value, "If-Range": '"origin-version"'})
    assert response.status_code == 200 and response.content.startswith(b"#EXTM3U\n")
    assert int(response.headers["content-length"]) == len(response.content)
    assert "content-range" not in response.headers
    assert harness.session.id.encode() in response.content
    assert len(upstream["records"]) == 1
    assert not {"Range", "If-Range"} & upstream["records"][0]["headers"].keys()


@pytest.mark.parametrize("value", ["bytes=20-1", "bytes=0-1,4-5", "bytes=-0"])
def test_invalid_manifest_range_is_rejected_before_an_upstream_request(bridge, upstream, value):
    harness = bridge("/master.m3u8", "hls")
    with TestClient(harness.app) as client:
        assert client.get(harness.url, headers={"Range": value}).status_code == 416
    assert upstream["records"] == []


def test_iso_bmff_segment_mime_is_relayed_without_changing_its_content(bridge):
    harness = bridge()
    resource = harness.registry.register(harness.session, "/fragment.m4s", "segment",
                                         base_url=harness.session.entry.url)
    with TestClient(harness.app) as client:
        response = client.get(resource.path(harness.session.id), headers={"Range": "bytes=4-11"})
    assert response.status_code == 206 and response.content == DATA[4:12]
    assert response.headers["content-type"] == "video/iso.segment"


def test_encrypted_hls_is_rejected_without_registering_or_fetching_its_key(bridge, upstream):
    harness = bridge("/encrypted.m3u8", "hls")
    with TestClient(harness.app) as client:
        response = client.get(harness.url)
    assert response.status_code == 502
    assert response.json()["code"] in {"unsupported_hls_encryption", "unsupported_hls_attributes"}
    assert [record["path"] for record in upstream["records"]] == ["/encrypted.m3u8"]


def test_failed_later_hls_reference_revokes_previously_registered_resources(bridge, upstream):
    harness = bridge("/partial-invalid.m3u8", "hls")
    with TestClient(harness.app) as client:
        response = client.get(harness.url)
        assert response.status_code == 502 and response.json()["code"] == "target_host_not_allowed"
        assert "private-token" not in response.text
        assert client.get(harness.url).status_code == 404
    assert harness.session.cancelled.is_set()
    assert harness.registry.stats()["sessions"] == 0
    assert harness.registry.stats()["resources"] == 0
    assert [record["path"] for record in upstream["records"]] == ["/partial-invalid.m3u8"]


def exception_leaves(error):
    if hasattr(error, "exceptions"):
        for nested in error.exceptions:
            yield from exception_leaves(nested)
    else:
        yield error


@pytest.mark.parametrize("stage", ["headers", "body"])
def test_absolute_request_deadline_stops_slow_upstream_io(bridge, stage):
    defaults = SessionLimits(ttl_seconds=60, idle_seconds=30, request_seconds=.25,
                             max_mp4_response_bytes=32)
    harness = bridge(f"/slow-{stage}.mp4", configured=defaults)
    started = time.monotonic()
    with TestClient(harness.app) as client:
        if stage == "headers":
            response = client.get(harness.url, headers={"Range": "bytes=0-7"})
            assert response.status_code == 504
            assert response.json()["code"] in {"operation_timeout", "request_timeout", "network_timeout"}
        else:
            with pytest.raises(Exception) as failure:
                client.get(harness.url)
            assert any(str(error) in {"operation_timeout", "request_timeout", "network_timeout"}
                       for error in exception_leaves(failure.value))
    assert time.monotonic() - started < 1.25
    assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)
    if stage == "body":
        # A few real bytes arrive before the peer stalls. Cancellation must
        # not lose/refund an HTTPX sub-64KiB chunk buffered for downstream.
        assert harness.registry.stats()["consumed_bytes"] > 0


def test_two_streams_hold_slots_until_cancelled_workers_really_exit(bridge, upstream, monkeypatch):
    harness = bridge("/blocked.mp4")
    finish_gate, both_finishing = threading.Event(), threading.Event()
    finishing, finish_lock = [0], threading.Lock()
    original = gateway._Transfer._finish_worker

    def delay_finally(transfer):
        with finish_lock:
            finishing[0] += 1
            if finishing[0] >= 2:
                both_finishing.set()
        finish_gate.wait(timeout=2)
        original(transfer)

    monkeypatch.setattr(gateway._Transfer, "_finish_worker", delay_finally)
    outcomes = []
    with TestClient(harness.app) as client:
        def fetch():
            try:
                outcomes.append(client.get(harness.url, headers={"Range": "bytes=0-7"}))
            except Exception as error:
                outcomes.append(error)

        workers = [threading.Thread(target=fetch) for _index in range(2)]
        for worker in workers:
            worker.start()
        try:
            assert upstream["two_blocked"].wait(timeout=1)
            rejected = client.get(harness.url, headers={"Range": "bytes=0-7"})
            assert rejected.status_code == 429 and rejected.json()["code"] == "request_limit"
            harness.registry.close(harness.session.id, OWNER)
            assert both_finishing.wait(timeout=1)
            assert harness.registry.stats()["active_requests"] == 2
            assert harness.registry.stats()["reserved_bytes"] > 0
        finally:
            finish_gate.set()
            upstream["release"].set()
            for worker in workers:
                worker.join(timeout=2)
                assert not worker.is_alive()
    assert len(outcomes) == 2
    for outcome in outcomes:
        # Cancellation can race the response headers. Before headers it is a
        # fixed error response; after headers the byte stream raises instead.
        # Both paths must keep the reservation until the worker really exits.
        if isinstance(outcome, Exception):
            continue
        assert outcome.status_code in {404, 410}
        assert outcome.json()["code"] in {"operation_cancelled", "request_cancelled", "session_closed"}
    assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)


def test_asgi_send_disconnect_closes_generator_and_releases_worker(bridge):
    harness = bridge("/ignore.mp4")
    sent = []

    async def run():
        requested = False

        async def receive():
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                raise OSError("fixture downstream disconnected")

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "method": "GET", "scheme": "http", "path": harness.url,
                 "raw_path": harness.url.encode(), "query_string": b"", "root_path": "",
                 "headers": [], "client": ("127.0.0.1", 12345), "server": ("testserver", 80),
                 "http_version": "1.1"}
        with pytest.raises(ClientDisconnect):
            await harness.app(scope, receive, send)

    asyncio.run(run())
    assert any(message["type"] == "http.response.start" and message["status"] == 200 for message in sent)
    assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)
    assert harness.registry.stats()["reserved_bytes"] == 0
