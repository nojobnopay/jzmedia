"""Fixed-source HLS diagnostics use isolated HTTP and in-memory upstreams."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import threading
import time

import httpx
import pytest

from scripts.online_diagnostics import hls_relay
from scripts.online_diagnostics.hls_relay import (
    CHUNK_BYTES, MANIFEST_BYTES, ORIGIN, PREFIX, SEGMENT_BYTES,
    HlsRelay, RelayError, make_server, validate_config,
)


TOKEN = "isolated_hls_relay_test_token_1234567890"
ENTRY = ORIGIN + PREFIX + "master.m3u8"
MEDIA = bytes(range(251)) * 600
MASTER = b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI="audio/stream.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=500000\nvideo/stream.m3u8\n'
PLAYLIST = b'#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:6,\nsegment.ts\n#EXT-X-ENDLIST\n'


def config(**extra):
    return {"token": TOKEN, "entry_url": ENTRY, "port": 0, **extra}


def upstream(request):
    assert request.url.host == "devstreaming-cdn.apple.com"
    assert "Authorization" not in request.headers and "Cookie" not in request.headers
    assert request.headers["Accept-Encoding"] == "identity"
    if request.url.path.endswith("master.m3u8"):
        return httpx.Response(200, headers={"Content-Length": str(len(MASTER))}, stream=httpx.ByteStream(MASTER))
    if request.url.path.endswith("stream.m3u8"):
        return httpx.Response(200, headers={"Content-Length": str(len(PLAYLIST))}, stream=httpx.ByteStream(PLAYLIST))
    body = MEDIA
    headers = {"Content-Type": "video/mp2t", "Content-Length": str(len(body))}
    status = 200
    if "Range" in request.headers:
        first, last = request.headers["Range"][6:].split("-")
        start = int(first) if first else max(0, len(body) - int(last))
        end = min(int(last), len(body) - 1) if first and last else len(body) - 1
        if start >= len(body):
            return httpx.Response(416, headers={"Content-Range": f"bytes */{len(body)}"})
        body = body[start:end + 1]
        headers.update({"Content-Range": f"bytes {start}-{end}/{len(MEDIA)}", "Content-Length": str(len(body))})
        status = 206
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body if request.method == "GET" else b""))


def idle(relay):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if relay.metrics()["active"] == 0:
            return relay.metrics()
        time.sleep(0.005)
    pytest.fail("request did not release its slot")


def wait_for_metric(relay, key, value, timeout=1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stats = relay.metrics()
        if stats[key] == value:
            return stats
        time.sleep(0.005)
    pytest.fail(f"metric {key} did not reach {value}")


@contextmanager
def running(handler=upstream, **extra):
    relay = HlsRelay(config(**extra), transport=httpx.MockTransport(handler))
    server = make_server(relay)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False, timeout=3) as client:
            yield relay, client, f"/{TOKEN}"
    finally:
        server.shutdown()
        server.server_close()
        relay.close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize("entry", [
    "http://devstreaming-cdn.apple.com" + PREFIX + "master.m3u8",
    "https://127.0.0.1" + PREFIX + "master.m3u8",
    "https://devstreaming-cdn.apple.com.evil.test" + PREFIX + "master.m3u8",
    ORIGIN + "/other/master.m3u8", ORIGIN + PREFIX + "%2e%2e/master.m3u8",
    ORIGIN + PREFIX + "segment.ts", ENTRY + "#fragment",
])
def test_entry_is_a_fixed_https_playlist(entry):
    with pytest.raises(ValueError):
        validate_config(config(entry_url=entry))


def test_legacy_family_selected_once_and_cannot_cross_directories():
    legacy = ORIGIN + "/videos/streaming/examples/bipbop_4x3/"
    relay = HlsRelay(config(entry_url=legacy + "bipbop_4x3_variant.m3u8"))
    try:
        assert relay.register("gear1/prog_index.m3u8", relay.config["entry_url"])
        with pytest.raises(RelayError):
            relay.register(ENTRY)
    finally:
        relay.close()


def test_rewrite_all_uri_attributes_relative_nested_and_query_manifest():
    with running() as (relay, _client, base):
        manifest = (b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI="../audio/stream.m3u8?x=1"\n'
                    b'#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=1,URI="iframe.m3u8"\n'
                    b'#EXT-X-MAP:URI="init.mp4"\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
                    b'#EXT-X-SESSION-DATA:DATA-ID="sample",URI="meta.json"\nsegment.ts\n')
        rewritten = relay.rewrite(manifest, ORIGIN + PREFIX + "video/stream.m3u8")
        assert rewritten.count((base + "/media/").encode()) == 6
        targets = list(relay.resources.values())
        assert (ORIGIN + PREFIX + "audio/stream.m3u8?x=1", "manifest") in targets
        assert (ORIGIN + PREFIX + "video/segment.ts", "media") in targets
        count = len(targets)
        relay.rewrite(manifest, ORIGIN + PREFIX + "video/stream.m3u8")
        assert len(relay.resources) == count


@pytest.mark.parametrize("uri", ["https://evil.test/a.ts", "../../../../private.ts", "//127.0.0.1/a.ts", "%2e%2e/secret", "skd://key", "../bipbop_4x3/gear1/prog_index.m3u8"])
def test_manifest_cannot_register_a_target_outside_selected_directory(uri):
    with running() as (relay, _client, _base):
        with pytest.raises(RelayError):
            relay.rewrite(f'#EXTM3U\n#EXT-X-MAP:URI="{uri}"\n'.encode(), ENTRY)


def test_resource_cap_and_manifest_size_limit(monkeypatch):
    with running() as (relay, _client, _base):
        monkeypatch.setattr(hls_relay, "RESOURCE_LIMIT", 2)
        relay.register("one.ts", ENTRY)
        with pytest.raises(RelayError):
            relay.register("two.ts", ENTRY)
        with pytest.raises(RelayError):
            relay.rewrite(b"#EXTM3U\n" + b"x" * MANIFEST_BYTES, ENTRY)


def test_health_auth_manifest_head_and_nested_playback():
    with running() as (relay, client, base):
        health = client.get(base + "/health")
        assert health.status_code == 200
        assert TOKEN not in health.text and ORIGIN not in health.text
        entry = base + "/" + health.json()["entry_path"]
        assert client.get("/wrong/health").status_code == 404
        assert client.get(base + "/media/https://evil.test/a").status_code == 404
        assert client.get(entry + "?url=https://evil.test").status_code == 404
        response = client.get(entry, headers={"Authorization": "private", "Cookie": "session=private"})
        assert response.status_code == 200 and response.headers["access-control-allow-origin"] == "*"
        assert response.text.startswith("#EXTM3U")
        header = client.head(entry)
        assert header.status_code == 200 and not header.content
        assert header.headers["content-length"] == response.headers["content-length"]
        child = next(line for line in response.text.splitlines() if line and not line.startswith("#"))
        nested = client.get(child)
        media = next(line for line in nested.text.splitlines() if line and not line.startswith("#"))
        assert client.get(media).content == MEDIA
        assert idle(relay)["completed"] == 4


@pytest.mark.parametrize("range_header,expected", [
    ("bytes=2-8", MEDIA[2:9]), ("bytes=10-", MEDIA[10:]), ("bytes=-5", MEDIA[-5:]),
], ids=["closed", "open", "suffix"])
def test_media_range_and_head_are_forwarded_exactly(range_header, expected):
    with running() as (relay, client, base):
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        response = client.get(path, headers={"Range": range_header})
        assert response.status_code == 206 and response.content == expected
        header = client.head(path, headers={"Range": range_header})
        assert header.status_code == 206 and not header.content
        assert header.headers["content-range"] == response.headers["content-range"]
        assert header.headers["content-length"] == str(len(expected))
        assert client.head(path).headers["content-length"] == str(len(MEDIA))
        assert idle(relay)["active"] == 0


def test_416_and_invalid_ranges_do_not_fetch_full_media():
    with running() as (relay, client, base):
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        response = client.get(path, headers={"Range": f"bytes={len(MEDIA)+1}-"})
        assert response.status_code == 416 and response.headers["content-range"] == f"bytes */{len(MEDIA)}"
        for value in ("bytes=1-0", "bytes=0-2,8-9", f"bytes=0-{SEGMENT_BYTES}", "bytes=-0"):
            assert client.get(path, headers={"Range": value}).status_code == 400
        assert idle(relay)["upstream_bytes"] == 0


def test_head_reports_large_backing_object_without_reading_media():
    total = SEGMENT_BYTES * 10
    class NoReadBody(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("HEAD or rejected oversized GET must not read its body")
            yield b""  # Keep this a byte stream iterator.
    def large(request):
        if "Range" in request.headers:
            return httpx.Response(206, headers={"Content-Length": "11", "Content-Type": "video/mp4",
                                              "Content-Range": f"bytes 100-110/{total}"}, stream=NoReadBody())
        return httpx.Response(200, headers={"Content-Length": str(total), "Content-Type": "video/mp4"},
                              stream=NoReadBody())
    with running(large) as (relay, client, base):
        path = base + "/media/" + relay.register("main.mp4", ENTRY)
        head = client.head(path)
        assert head.status_code == 200 and head.headers["content-length"] == str(total)
        assert client.head(path, headers={"Range": "bytes=100-110"}).status_code == 206
        assert client.get(path).status_code == 502
        assert idle(relay)["upstream_bytes"] == 0


def test_unknown_length_media_has_complete_chunked_framing():
    def response(_request):
        return httpx.Response(200, headers={"Content-Type": "video/mp2t"}, stream=httpx.ByteStream(b"payload"))
    with running(response) as (relay, client, base):
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        result = client.get(path)
        assert result.status_code == 200 and result.content == b"payload"
        assert result.headers["transfer-encoding"] == "chunked"
        stats = idle(relay)
        assert stats["completed"] == 1 and stats["budget_charged_bytes"] == 7


def test_short_body_with_clean_eof_still_fails_content_length_contract():
    def response(_request):
        return httpx.Response(200, headers={"Content-Type": "video/mp2t", "Content-Length": "10"},
                              stream=httpx.ByteStream(b"short"))
    with running(response) as (relay, client, base):
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        with pytest.raises(httpx.RemoteProtocolError):
            client.get(path)
        stats = idle(relay)
        assert stats["upstream_errors"] == 1 and stats["completed"] == 0


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}),
    httpx.Response(200, headers={"Content-Length": str(SEGMENT_BYTES + 1)}),
    httpx.Response(200, headers={"Content-Encoding": "gzip", "Content-Length": "10"}),
    httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"blocked"),
])
def test_redirect_encoding_oversize_and_html_are_rejected(response):
    with running(lambda _request: response) as (relay, client, base):
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        assert client.get(path).status_code == 502
        assert idle(relay)["upstream_errors"] == 1


def test_total_budget_rejects_before_sending_resource_body():
    with running(max_bytes_total=10) as (relay, client, base):
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        assert client.get(path).status_code == 502
        stats = idle(relay)
        assert stats["rejected_budget"] == 1 and stats["bytes_sent"] == stats["upstream_bytes"] == 0


class FailingBody(httpx.SyncByteStream):
    def __init__(self):
        self.closed = threading.Event()

    def __iter__(self):
        yield b"a" * CHUNK_BYTES
        raise httpx.ReadError("isolated upstream interruption")

    def close(self):
        self.closed.set()


@pytest.mark.parametrize("known_length", [True, False])
def test_midstream_failure_is_incomplete_http_not_a_success(known_length):
    body = FailingBody()
    headers = {"Content-Type": "video/mp2t"}
    if known_length:
        headers["Content-Length"] = str(CHUNK_BYTES * 2)
    with running(lambda _req: httpx.Response(200, headers=headers, stream=body)) as (relay, client, base):
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        with pytest.raises(httpx.RemoteProtocolError):
            client.get(path)
        assert body.closed.wait(1)
        stats = idle(relay)
        assert stats["upstream_errors"] == 1 and stats["completed"] == 0


def test_timeout_before_headers_has_a_finite_error_response():
    def timeout(_request):
        raise httpx.ReadTimeout("isolated timeout")
    with running(timeout) as (relay, client, base):
        assert client.get(base + "/media/" + relay.entry_id).status_code == 502
        assert idle(relay)["upstream_errors"] == 1


@pytest.mark.parametrize("queued", [1, 8])
def test_four_active_requests_have_a_bounded_queue_and_metrics_stay_available(queued):
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    count = 0
    def blocked(_request):
        nonlocal count
        with lock:
            count += 1
            if count == 4:
                entered.set()
        assert release.wait(3)
        return httpx.Response(200, headers={"Content-Length": "7"}, stream=httpx.ByteStream(b"segment"))
    with running(blocked) as (relay, client, base), ThreadPoolExecutor(max_workers=12) as pool:
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        futures = [pool.submit(client.get, path) for _ in range(4)]
        try:
            assert entered.wait(1)
            futures.extend(pool.submit(client.get, path) for _ in range(queued))
            assert wait_for_metric(relay, "queued", queued)["active"] == 4
            assert client.get(base + "/metrics").json()["active"] == 4
            if queued == 8:
                assert client.get(path).status_code == 429
        finally:
            release.set()
        assert all(future.result().status_code == 200 for future in futures)
        stats = idle(relay)
        assert stats["peak_active"] == 4 and stats["peak_queued"] == queued
        assert stats["rejected_busy"] == int(queued == 8)
        assert stats["queued"] == 0 and stats["rejected_handlers"] == 0


def test_queued_request_has_a_finite_deadline(monkeypatch):
    monkeypatch.setattr(hls_relay, "QUEUE_WAIT_SECONDS", 0.05)
    release = threading.Event()
    def blocked(_request):
        assert release.wait(2)
        return httpx.Response(200, headers={"Content-Length": "1"}, stream=httpx.ByteStream(b"x"))
    with running(blocked) as (relay, client, base), ThreadPoolExecutor(max_workers=4) as pool:
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        futures = [pool.submit(client.get, path) for _ in range(4)]
        try:
            wait_for_metric(relay, "active", 4)
            assert client.get(path).status_code == 429
            stats = relay.metrics()
            assert stats["queued"] == 0 and stats["rejected_busy"] == 1
            assert stats["queue_wait_seconds"] >= 0.05
        finally:
            release.set()
        assert all(future.result().status_code == 200 for future in futures)
        assert idle(relay)["active"] == 0


def test_client_disconnect_closes_upstream_and_shutdown_is_authenticated():
    closed = threading.Event()
    class SlowBody(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                time.sleep(0.002)
                yield b"x" * CHUNK_BYTES
        def close(self):
            closed.set()
    with running(lambda _req: httpx.Response(200, headers={"Content-Length": str(100 * CHUNK_BYTES)}, stream=SlowBody())) as (relay, client, base):
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        with client.stream("GET", path) as response:
            assert next(response.iter_raw())
        assert closed.wait(2)
        stats = idle(relay)
        assert stats["client_disconnects"] == 1
        assert client.post("/wrong/shutdown").status_code == 404 and not relay.stop.is_set()
        assert client.post(base + "/shutdown").status_code == 200
        assert relay.stop.wait(1)


def test_shutdown_rejects_new_media_without_an_upstream_request():
    calls = []
    def observed(request):
        calls.append(request)
        return upstream(request)
    with running(observed) as (relay, client, base):
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        assert client.post(base + "/shutdown").status_code == 200
        assert client.get(path).status_code == 503
        assert calls == []
        stats = relay.metrics()
        assert stats["stopping"] is True and stats["cancelled_requests"] == 1
        assert stats["upstream_errors"] == stats["rejected_busy"] == 0
        assert relay.drain(timeout=0)["drained"] is True


def test_shutdown_cancels_queued_requests_and_reports_blocked_upstreams():
    release = threading.Event()
    entered = threading.Event()
    lock = threading.Lock()
    calls = 0
    def blocked(_request):
        nonlocal calls
        with lock:
            calls += 1
            if calls == 4:
                entered.set()
        assert release.wait(3)
        return httpx.Response(200, headers={"Content-Length": "1"}, stream=httpx.ByteStream(b"x"))
    with running(blocked) as (relay, client, base), ThreadPoolExecutor(max_workers=5) as pool:
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        active = [pool.submit(client.get, path) for _ in range(4)]
        try:
            assert entered.wait(1)
            queued = pool.submit(client.get, path)
            wait_for_metric(relay, "queued", 1)
            assert client.post(base + "/shutdown").status_code == 200
            assert queued.result(timeout=1).status_code == 503
            drain = relay.drain(timeout=0.05)
            assert drain["drained"] is False
            assert drain["drain_active_remaining"] == 4 and drain["drain_queued_remaining"] == 0
            assert 0.04 <= drain["drain_wait_seconds"] < 0.5
            assert calls == 4
        finally:
            release.set()
        assert all(future.result(timeout=1).status_code == 503 for future in active)
        drain = relay.drain(timeout=1)
        assert drain["drained"] is True
        stats = relay.metrics()
        assert stats["active"] == stats["queued"] == stats["upstream_errors"] == 0
        assert stats["cancelled_requests"] == 5 and stats["rejected_busy"] == 0


def test_shutdown_observes_inflight_body_cleanup_without_a_success_tail():
    first_chunk = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    class BlockedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * CHUNK_BYTES
            first_chunk.set()
            assert release.wait(3)
            yield b"y" * CHUNK_BYTES
        def close(self):
            closed.set()
    with running(lambda _req: httpx.Response(200, headers={"Content-Length": str(2 * CHUNK_BYTES)},
                                           stream=BlockedBody())) as (relay, client, base), ThreadPoolExecutor(max_workers=1) as pool:
        path = base + "/media/" + relay.register("segment.ts", ENTRY)
        future = pool.submit(client.get, path)
        try:
            assert first_chunk.wait(1)
            assert client.post(base + "/shutdown").status_code == 200
            drain = relay.drain(timeout=0.05)
            assert drain["drained"] is False and drain["drain_active_remaining"] == 1
        finally:
            release.set()
        with pytest.raises(httpx.RemoteProtocolError):
            future.result(timeout=1)
        assert closed.wait(1)
        drain = relay.drain(timeout=1)
        assert drain["drained"] is True
        stats = relay.metrics()
        assert stats["completed"] == stats["upstream_errors"] == 0
        assert stats["cancelled_requests"] == 1 and stats["bytes_sent"] == CHUNK_BYTES
        assert stats["upstream_bytes"] == stats["budget_charged_bytes"] == 2 * CHUNK_BYTES


def test_defaults_serialize_without_credentials_or_source_url():
    with running() as (relay, _client, _base):
        report = json.dumps(relay.metrics())
        assert TOKEN not in report and ENTRY not in report
        assert relay.config["max_bytes_total"] == 512 * 1024 * 1024
        assert relay.metrics()["resource_limit"] == 8192
        assert relay.metrics()["max_media_requests"] == 4
        assert relay.client.trust_env is False
