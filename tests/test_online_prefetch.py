"""Real, isolated HLS origin reads exercise the two-reader prefetch cache."""

import asyncio
from dataclasses import replace
import hashlib
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
from app.online.prefetch import CacheStore
from app.online.sessions import SessionError, SessionLimits, SessionRegistry
from test_network_transport import LoopbackPolicy, logical_url, running_server


pytest_plugins = ["test_network_transport"]
OWNER = "prefetch-owner-fixture"
SEGMENTS = [bytes([index]) * 2048 for index in range(10)]
RANGE_BODY = bytes(range(64))
LONG_SEGMENT_COUNT = 153
LONG_SEGMENT_BYTES = 2048


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return predicate()


@pytest.fixture
def prefetch_origin():
    records, gates, truncated, threads = [], {}, set(), []
    stopping = threading.Event()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            self.respond(True)

        def do_GET(self):
            self.respond(False)

        def respond(self, head):
            path = urlsplit(self.path).path
            with lock:
                records.append({"path": path, "method": self.command,
                                "range": self.headers.get("Range"),
                                "if_range": self.headers.get("If-Range")})
            if path == "/vod.m3u8":
                lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:4"]
                for index in range(len(SEGMENTS)):
                    lines.extend(["#EXTINF:4,", f"segments/{index}.ts?signature=fixture-secret"])
                body = ("\n".join([*lines, "#EXT-X-ENDLIST"]) + "\n").encode()
                mime = "application/vnd.apple.mpegurl"
            elif path == "/master-switch.m3u8":
                body = (b'#EXTM3U\n'
                        b'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="sound",NAME="main",URI="audio-switch.m3u8"\n'
                        b'#EXT-X-STREAM-INF:BANDWIDTH=100000,AUDIO="sound"\nlow-switch.m3u8\n'
                        b'#EXT-X-STREAM-INF:BANDWIDTH=200000,AUDIO="sound"\nhigh-switch.m3u8\n')
                mime = "application/vnd.apple.mpegurl"
            elif path in {"/low-switch.m3u8", "/high-switch.m3u8", "/audio-switch.m3u8"}:
                track = path.split("-")[0].removeprefix("/")
                lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:4"]
                for index in range(6):
                    lines.extend(["#EXTINF:4,", f"switch/{track}/{index}.ts"])
                body = ("\n".join([*lines, "#EXT-X-ENDLIST"]) + "\n").encode()
                mime = "application/vnd.apple.mpegurl"
            elif path == "/long.m3u8":
                lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:4"]
                for index in range(LONG_SEGMENT_COUNT):
                    lines.extend(["#EXTINF:4,", f"long-segments/{index}.ts"])
                body = ("\n".join([*lines, "#EXT-X-ENDLIST"]) + "\n").encode()
                mime = "application/vnd.apple.mpegurl"
            elif path == "/ranges.m3u8":
                body = (b'#EXTM3U\n#EXT-X-TARGETDURATION:4\n'
                        b'#EXT-X-MAP:URI="asset.bin",BYTERANGE="4@0"\n'
                        b'#EXTINF:4,\n#EXT-X-BYTERANGE:8@4\nasset.bin\n'
                        b'#EXTINF:4,\n#EXT-X-BYTERANGE:8\nasset.bin\n#EXT-X-ENDLIST\n')
                mime = "application/vnd.apple.mpegurl"
            elif path == "/asset.bin":
                body, mime = RANGE_BODY, "application/octet-stream"
            elif path.startswith("/long-segments/"):
                match = re.fullmatch(r"/long-segments/([0-9]+)\.ts", path)
                if not match or int(match[1]) >= LONG_SEGMENT_COUNT:
                    self.send_error(404)
                    return
                body, mime = bytes([int(match[1]) % 256]) * LONG_SEGMENT_BYTES, "video/mp2t"
            elif path.startswith("/switch/"):
                match = re.fullmatch(r"/switch/(?:low|high|audio)/([0-9]+)\.ts", path)
                if not match or int(match[1]) >= 6:
                    self.send_error(404)
                    return
                body, mime = SEGMENTS[int(match[1])], "video/mp2t"
            else:
                match = re.fullmatch(r"/segments/([0-9]+)\.ts", path)
                if not match or int(match[1]) >= len(SEGMENTS):
                    self.send_error(404)
                    return
                body, mime = SEGMENTS[int(match[1])], "video/mp2t"
            status, extra = 200, {}
            raw_range = self.headers.get("Range")
            if raw_range:
                first, last = raw_range.removeprefix("bytes=").split("-", 1)
                start, end = int(first), min(int(last), len(body) - 1)
                total = len(body)
                body = body[start:end + 1]
                status, extra = 206, {"Content-Range": f"bytes {start}-{end}/{total}"}
            try:
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", '"prefetch-fixture-v1"')
                for name, value in extra.items():
                    self.send_header(name, value)
                self.end_headers()
                if head:
                    return
                gate = gates.get(path)
                if gate is not None:
                    while not gate.wait(.02):
                        if stopping.is_set():
                            return
                if path in truncated:
                    self.wfile.write(body[:7])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(body)
                self.wfile.flush()
            except OSError:
                self.close_connection = True

    with running_server(Handler) as url:
        try:
            yield SimpleNamespace(url=url, records=records, gates=gates, truncated=truncated,
                                  threads=threads)
        finally:
            stopping.set()
            for gate in gates.values():
                gate.set()
            for thread in threads:
                thread.join(timeout=3)
            assert all(not thread.is_alive() for thread in threads)


@pytest.fixture
def prefetch_bridge(source, prefetch_origin):
    registries = []
    limits = SessionLimits(ttl_seconds=60, idle_seconds=30, request_seconds=4,
                           max_segment_response_bytes=64 * 1024,
                           max_manifest_bytes=8192, max_session_bytes=8 * 1024**2,
                           max_reserved_bytes=2 * 1024**2, prefetch_seconds=8,
                           prefetch_max_segments=2, prefetch_session_bytes=1024**2,
                           prefetch_global_bytes=2 * 1024**2)

    def make(path="/vod.m3u8", **changes):
        registry = SessionRegistry(replace(limits, **changes))
        registries.append(registry)
        reader = create_client(resolve_online_policy(source, "media"),
                               targets=LoopbackPolicy(source, ["127.0.0.1"]))
        session = registry.create(OnlinePlaybackIdentity(source_id=source, item_id="itm_" + "e" * 32,
                                                         kind="movie"), OWNER, reader,
                                  prefetch_origin.url + path, "hls")
        app = FastAPI()

        async def relay(request, sid, rid=None):
            try:
                owner = request.headers.get("x-fixture-owner", OWNER)
                current = registry.get(sid, owner)
                resource = current.entry if rid is None else registry.resource(current, rid)
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
                               url=session.descriptor.media_url)

    gateway.start_workers()
    try:
        yield make
    finally:
        for registry in registries:
            registry.close_all()
        for gate in prefetch_origin.gates.values():
            gate.set()
        drained = wait_until(lambda: all(registry.stats()["active_requests"] == 0 for registry in registries))
        gateway.shutdown_workers()
        assert drained, "prefetch workers must return all request slots and reservations"


def playlist(client, harness):
    response = client.get(harness.url)
    assert response.status_code == 200, response.text
    assert "fixture-secret" not in response.text
    return re.findall(r"^(/api/online/sessions/[^\n]+)$", response.text, re.MULTILINE)


def segment_reads(origin, index=None):
    prefix = "/segments/" if index is None else f"/segments/{index}.ts"
    return [row for row in origin.records if row["method"] == "GET" and row["path"].startswith(prefix)]


def fetch_later(client, path, origin):
    done, result = threading.Event(), {}

    def run():
        try:
            result["response"] = client.get(path)
        except BaseException as error:
            result["error"] = error
        finally:
            done.set()

    thread = threading.Thread(target=run, name="isolated-prefetch-client", daemon=True)
    origin.threads.append(thread)
    thread.start()
    return done, result


def assert_segment(result, index):
    assert "error" not in result, result.get("error")
    response = result["response"]
    assert response.status_code == 200
    assert response.content == SEGMENTS[index]


def test_master_variant_switch_retires_old_video_but_keeps_audio(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge("/master-switch.m3u8")
    with TestClient(harness.app) as client:
        master = client.get(harness.url)
        assert master.status_code == 200
        variants = re.findall(r"^(/api/online/sessions/[^\n]+)$", master.text, re.MULTILINE)
        audio_path = re.search(r'URI="(/api/online/sessions/[^"]+)"', master.text).group(1)
        assert len(variants) == 2

        def segments(path):
            response = client.get(path)
            assert response.status_code == 200
            return re.findall(r"^(/api/online/sessions/[^\n]+)$", response.text, re.MULTILINE)

        low, high, audio = map(segments, (*variants, audio_path))
        for resources in (low, audio, high):
            response = client.get(resources[0])
            assert response.status_code == 200
        cache = harness.registry._media_cache
        low_id, high_id, audio_id = (path.rsplit("/", 1)[-1] for path in (*variants, audio_path))
        assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)
        assert set(cache._windows) == {(harness.session.id, high_id), (harness.session.id, audio_id)}
        assert (harness.session.id, low_id) not in cache._windows
        assert any(row["path"] == "/switch/high/1.ts" for row in prefetch_origin.records)
        assert any(row["path"] == "/switch/audio/1.ts" for row in prefetch_origin.records)


def test_manifest_and_head_do_not_start_speculative_media_reads(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge()
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        assert not segment_reads(prefetch_origin)
        before = harness.registry.stats()["consumed_bytes"]
        response = client.head(paths[0])
        assert response.status_code == 200 and response.content == b""
        assert not segment_reads(prefetch_origin)
        assert harness.registry.stats()["consumed_bytes"] == before


def test_single_browser_fragment_triggers_ten_minutes_of_complete_server_prefetch(prefetch_bridge, prefetch_origin):
    limits = SessionLimits()
    harness = prefetch_bridge("/long.m3u8", prefetch_seconds=limits.prefetch_seconds,
                              prefetch_max_segments=limits.prefetch_max_segments,
                              prefetch_session_bytes=limits.prefetch_session_bytes,
                              prefetch_global_bytes=limits.prefetch_global_bytes,
                              prefetch_max_entries=limits.prefetch_max_entries,
                              max_segment_response_bytes=limits.max_segment_response_bytes,
                              max_session_bytes=limits.max_session_bytes,
                              max_reserved_bytes=limits.max_reserved_bytes)
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        assert len(paths) == LONG_SEGMENT_COUNT
        assert not [row for row in prefetch_origin.records if row["path"].startswith("/long-segments/")]
        before = harness.registry.stats()["consumed_bytes"]

        # Simulate a browser that asks only for its current fragment and then
        # stops requesting. All later segment reads must be background work.
        response = client.get(paths[0])
        assert response.status_code == 200 and response.content == bytes(LONG_SEGMENT_BYTES)
        cache = harness.registry._media_cache
        assert wait_until(lambda: cache.stats()["prefetch_completed"] == 150
                          and harness.registry.stats()["active_requests"] == 0, timeout=25)

        reads = [row for row in prefetch_origin.records
                 if row["method"] == "GET" and row["path"].startswith("/long-segments/")]
        assert len(reads) == 151
        assert {row["path"] for row in reads} == {f"/long-segments/{i}.ts" for i in range(151)}
        with cache._lock:
            timeline = cache._timelines[(harness.session.id, harness.session.entry.id)][1]
            assert sum(segment.duration for segment in timeline[1:151]) == 600
            for index, segment in enumerate(timeline[:151]):
                key = (harness.session.id, segment.path.rsplit("/", 1)[-1])
                entry = cache._entries[key]
                assert not entry.obsolete and entry.future.done()
                assert entry.future.result().body == bytes([index % 256]) * LONG_SEGMENT_BYTES
            assert (harness.session.id, timeline[151].path.rsplit("/", 1)[-1]) not in cache._entries

        cache_stats, registry_stats = cache.stats(), harness.registry.stats()
        assert cache_stats["entries"] == 151 <= limits.prefetch_max_entries
        assert cache_stats["bytes"] == 151 * LONG_SEGMENT_BYTES <= limits.prefetch_session_bytes
        assert cache_stats["bytes"] <= limits.prefetch_global_bytes
        assert registry_stats["consumed_bytes"] - before == 151 * LONG_SEGMENT_BYTES
        assert registry_stats["reserved_bytes"] == 0
        assert registry_stats["resources"] <= limits.max_resources

        harness.registry.close(harness.session.id, OWNER)
        assert cache.stats()["entries"] == cache.stats()["bytes"] == 0
        assert harness.registry.stats()["active_requests"] == harness.registry.stats()["reserved_bytes"] == 0
        assert harness.registry.stats()["resources"] == 0


def test_completed_segment_is_reused_without_second_origin_read_or_byte_charge(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge(prefetch_seconds=0)
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        first = client.get(paths[0])
        charged = harness.registry.stats()["consumed_bytes"]
        second = client.get(paths[0])
        assert first.status_code == second.status_code == 200
        assert first.content == second.content == SEGMENTS[0]
        for name in ("content-type", "content-length", "etag"):
            assert first.headers[name] == second.headers[name]
        assert second.headers["cache-control"] == "no-store"
        assert len(segment_reads(prefetch_origin, 0)) == 1
        assert harness.registry.stats()["consumed_bytes"] == charged


def test_simultaneous_demand_for_same_segment_joins_one_origin_read(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge(prefetch_seconds=0)
    gate = prefetch_origin.gates["/segments/0.ts"] = threading.Event()
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        first_done, first = fetch_later(client, paths[0], prefetch_origin)
        assert wait_until(lambda: len(segment_reads(prefetch_origin, 0)) == 1)
        second_done, second = fetch_later(client, paths[0], prefetch_origin)
        assert not first_done.wait(.05) and not second_done.is_set()
        gate.set()
        assert first_done.wait(2) and second_done.wait(2)
        assert_segment(first, 0)
        assert_segment(second, 0)
        assert len(segment_reads(prefetch_origin, 0)) == 1


def test_two_origin_reads_overlap_and_out_of_order_completion_keeps_correct_bytes(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge()
    gate0 = prefetch_origin.gates["/segments/0.ts"] = threading.Event()
    gate1 = prefetch_origin.gates["/segments/1.ts"] = threading.Event()
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        first_done, first = fetch_later(client, paths[0], prefetch_origin)
        assert wait_until(lambda: len(segment_reads(prefetch_origin)) >= 2)
        assert not first_done.is_set()
        # The later piece may finish first; its body must never satisfy piece 0.
        gate1.set()
        second_done, second = fetch_later(client, paths[1], prefetch_origin)
        assert second_done.wait(2)
        assert_segment(second, 1)
        assert not first_done.is_set()
        gate0.set()
        assert first_done.wait(2)
        assert_segment(first, 0)
        assert len(segment_reads(prefetch_origin, 1)) == 1


def test_seek_replaces_blocked_lookahead_without_waiting_for_old_origin_reads(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge()
    prefetch_origin.gates["/segments/1.ts"] = threading.Event()
    prefetch_origin.gates["/segments/2.ts"] = threading.Event()
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        assert client.get(paths[0]).content == SEGMENTS[0]
        assert wait_until(lambda: len(segment_reads(prefetch_origin, 1)) == 1)
        done, result = fetch_later(client, paths[7], prefetch_origin)
        assert done.wait(2), "seek demand must preempt obsolete blocked lookahead"
        assert_segment(result, 7)
        assert wait_until(lambda: any(row["path"] == "/segments/8.ts" for row in segment_reads(prefetch_origin)))
        assert not segment_reads(prefetch_origin, 4), "seek must not download the gap sequentially"


def test_background_failure_does_not_break_current_segment_or_poison_next_demand(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge()
    prefetch_origin.truncated.add("/segments/1.ts")
    with TestClient(harness.app, raise_server_exceptions=False) as client:
        paths = playlist(client, harness)
        assert client.get(paths[0]).content == SEGMENTS[0]
        assert wait_until(lambda: len(segment_reads(prefetch_origin, 1)) >= 1)
        assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)
        prefetch_origin.truncated.clear()
        recovered = client.get(paths[1])
        assert recovered.status_code == 200 and recovered.content == SEGMENTS[1]
        assert len(segment_reads(prefetch_origin, 1)) >= 2


def test_truncated_near_segment_recovers_in_background_before_foreground_cache_hit(prefetch_bridge,
                                                                                 prefetch_origin, monkeypatch):
    harness = prefetch_bridge()
    clock = SimpleNamespace(now=1000.0)
    counts = {"active": 0, "peak": 0}
    errors = []
    lock = threading.Lock()
    original_fetch, original_failure = gateway._fetch_segment, gateway._failure

    def observed_failure(error):
        result = original_failure(error)
        with lock:
            errors.append(result.category)
        return result

    def observed_fetch(session, resource, lease):
        with lock:
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
        try:
            return original_fetch(session, resource, lease)
        finally:
            with lock:
                counts["active"] -= 1

    monkeypatch.setattr(gateway, "_failure", observed_failure)
    cache = harness.registry.cached_media(lambda: CacheStore(harness.registry, observed_fetch, gateway._submit,
                                                             clock=lambda: clock.now))
    gate = prefetch_origin.gates["/segments/0.ts"] = threading.Event()
    prefetch_origin.truncated.add("/segments/1.ts")
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        manifest_bytes = harness.registry.stats()["consumed_bytes"]
        first_done, first = fetch_later(client, paths[0], prefetch_origin)
        assert wait_until(lambda: "network_request_failed" in errors and cache.stats()["retry_pending"] == 1)
        assert not first_done.is_set()
        assert len(segment_reads(prefetch_origin, 1)) == 1
        assert not segment_reads(prefetch_origin, 2), "A cooling near hole must precede speculative far work"
        assert harness.registry.stats()["consumed_bytes"] == manifest_bytes + 7
        prefetch_origin.truncated.clear()
        gate.set()
        assert first_done.wait(2)
        assert_segment(first, 0)
        assert hashlib.sha256(first["response"].content).digest() == hashlib.sha256(SEGMENTS[0]).digest()
        assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)

        clock.now += .5
        harness.registry.maintain_prefetch()
        assert len(segment_reads(prefetch_origin, 1)) == 1
        clock.now += .5
        harness.registry.maintain_prefetch()
        assert wait_until(lambda: cache.stats()["prefetch_completed"] == 2
                          and harness.registry.stats()["active_requests"] == 0)
        assert len(segment_reads(prefetch_origin, 1)) == 2
        assert len(segment_reads(prefetch_origin, 2)) == 1
        assert cache.stats()["automatic_retries"] == 1
        assert cache.stats()["retry_pending"] == 0
        # The failed prefix is charged once; complete cache delivery adds no upstream bytes.
        assert harness.registry.stats()["consumed_bytes"] == manifest_bytes + 7 + sum(map(len, SEGMENTS[:3]))
        assert cache.stats()["download_bytes"] == sum(map(len, SEGMENTS[:3]))
        hits = cache.stats()["hits"]
        for index in (1, 2):
            response = client.get(paths[index])
            assert response.status_code == 200
            assert hashlib.sha256(response.content).digest() == hashlib.sha256(SEGMENTS[index]).digest()
        assert cache.stats()["hits"] == hits + 2
        assert len(segment_reads(prefetch_origin, 1)) == 2
        assert len(segment_reads(prefetch_origin, 2)) == 1
        assert counts["peak"] == 2
        harness.registry.close(harness.session.id, OWNER)
        assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)
        assert harness.registry.stats()["reserved_bytes"] == 0
        assert cache.stats()["entries"] == cache.stats()["bytes"] == cache.stats()["waiters"] == 0
        assert cache.stats()["retry_pending"] == cache.stats()["retry_used"] == 0


def test_fixed_byte_ranges_and_initialization_maps_keep_separate_cached_bodies(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge("/ranges.m3u8", prefetch_seconds=0)
    with TestClient(harness.app) as client:
        manifest = client.get(harness.url)
        assert manifest.status_code == 200
        paths = re.findall(r"/api/online/sessions/[A-Za-z0-9_/-]+", manifest.text)
        assert len(paths) == 3
        for path, (start, end) in zip(paths, [(0, 3), (4, 11), (12, 19)]):
            first, cached = client.get(path), client.get(path)
            assert first.status_code == cached.status_code == 206
            assert first.content == cached.content == RANGE_BODY[start:end + 1]
            assert cached.headers["content-range"] == f"bytes {start}-{end}/64"
        reads = [row for row in prefetch_origin.records if row["path"] == "/asset.bin"]
        assert [row["range"] for row in reads] == ["bytes=0-3", "bytes=4-11", "bytes=12-19"]
        bad = client.get(paths[1], headers={"Range": "bytes=0-7"})
        assert bad.status_code == 416
        assert len([row for row in prefetch_origin.records if row["path"] == "/asset.bin"]) == 3


def test_custom_range_and_if_range_bypass_full_resource_cache(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge(prefetch_seconds=0)
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        assert client.get(paths[0]).content == SEGMENTS[0]
        ranged = client.get(paths[0], headers={"Range": "bytes=2-7", "If-Range": '"prefetch-fixture-v1"'})
        assert ranged.status_code == 206 and ranged.content == SEGMENTS[0][2:8]
        assert client.get(paths[0]).content == SEGMENTS[0]
        reads = segment_reads(prefetch_origin, 0)
        assert len(reads) == 2
        assert reads[-1]["range"] == "bytes=2-7"
        assert reads[-1]["if_range"] == '"prefetch-fixture-v1"'


@pytest.mark.parametrize("requested,start,end", [("bytes=2-7", 2, 7), ("bytes=2040-", 2040, 2047),
                                                  ("bytes=-8", 2040, 2047)])
def test_normal_segment_ranges_are_sliced_from_complete_cached_bytes(prefetch_bridge, prefetch_origin,
                                                                    requested, start, end):
    harness = prefetch_bridge(prefetch_seconds=0)
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        assert client.get(paths[0]).content == SEGMENTS[0]
        before = harness.registry.stats()["consumed_bytes"]
        response = client.get(paths[0], headers={"Range": requested})
        assert response.status_code == 206
        assert response.content == SEGMENTS[0][start:end + 1]
        assert response.headers["content-range"] == f"bytes {start}-{end}/2048"
        assert int(response.headers["content-length"]) == end - start + 1
        assert len(segment_reads(prefetch_origin, 0)) == 1
        assert harness.registry.stats()["consumed_bytes"] == before


def test_unsatisfied_cached_segment_range_reports_actual_length_without_origin_read(prefetch_bridge,
                                                                                  prefetch_origin):
    harness = prefetch_bridge(prefetch_seconds=0)
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        assert client.get(paths[0]).content == SEGMENTS[0]
        response = client.get(paths[0], headers={"Range": "bytes=2048-2050"})
        assert response.status_code == 416
        assert response.headers["content-range"] == "bytes */2048"
        assert len(segment_reads(prefetch_origin, 0)) == 1


def test_small_range_remains_available_when_complete_segment_exceeds_response_cap(prefetch_bridge,
                                                                                prefetch_origin):
    harness = prefetch_bridge(prefetch_seconds=0, max_segment_response_bytes=1024)
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        response = client.get(paths[0], headers={"Range": "bytes=4-11"})
        assert response.status_code == 206 and response.content == SEGMENTS[0][4:12]
        assert response.headers["content-range"] == "bytes 4-11/2048"
        assert client.get(paths[0]).status_code == 413
        assert harness.registry._media_cache.stats()["entries"] == 0


def test_cached_bytes_still_require_owner_and_live_session(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge(prefetch_seconds=0)
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        assert client.get(paths[0]).content == SEGMENTS[0]
        stranger = client.get(paths[0], headers={"x-fixture-owner": "another-device"})
        assert stranger.status_code == 404
        harness.registry.close(harness.session.id, OWNER)
        assert client.get(paths[0]).status_code == 404
        assert len(segment_reads(prefetch_origin, 0)) == 1
        assert harness.registry.stats()["active_requests"] == 0


@pytest.mark.parametrize("revocation", ["source", "feature", "viewing_generation"])
def test_production_routes_revoke_warm_cache_before_returning_media(source, prefetch_bridge,
                                                                  prefetch_origin, monkeypatch, revocation):
    from app.config import settings
    from app.main import app
    from app.online import providers, service
    from app.store import online

    monkeypatch.setattr(settings, "jzmedia_token", "")
    online.update_online_source(source, {"enabled": True, "allowed_hosts": ["fixture.test"]})
    online.update_online_settings(True)
    item = online.upsert_online_item(source, "warm-cache-film", "movie", "Isolated cache film")
    identity = {"source_id": source, "item_id": item["id"], "kind": "movie"}
    registry = SessionRegistry(SessionLimits(prefetch_seconds=0))
    monkeypatch.setattr(service, "registry", registry)
    monkeypatch.setattr(providers, "resolve", lambda _identity: providers.ResolvedPlayback(
        logical_url(prefetch_origin.url, "fixture.test") + "/vod.m3u8", "hls"))

    def factory(source_id, purpose, *, header_policy=None):
        return create_client(resolve_online_policy(source_id, purpose),
                             targets=LoopbackPolicy(source_id, ["fixture.test"],
                                                    resolver=lambda _host, _port: ("127.0.0.1",)),
                             header_policy=header_policy)

    monkeypatch.setattr(service, "create_online_client", factory)
    # prefetch_bridge starts the real gateway pool without app/scanner lifespan.
    client = TestClient(app)
    try:
        assert client.get("/api/viewing/session").status_code == 200
        creation = client.post("/api/online/sessions", json=identity)
        assert creation.status_code == 201, creation.text
        manifest = client.get(creation.json()["media_url"])
        assert manifest.status_code == 200
        path = re.findall(r"^(/api/online/sessions/[^\n]+)$", manifest.text, re.MULTILINE)[0]
        assert client.get(path).content == SEGMENTS[0]
        assert client.get(path).content == SEGMENTS[0]
        assert len(segment_reads(prefetch_origin, 0)) == 1
        if revocation == "source":
            changed = client.patch(f"/api/online/sources/{source}", json={"enabled": False})
        elif revocation == "feature":
            changed = client.patch("/api/online/settings", json={"enabled": False})
        else:
            changed = client.post("/api/viewing/lock")
            service.sweep()
        assert changed.status_code == 200, changed.text
        rejected = client.get(path)
        assert rejected.status_code in {401, 404, 410, 503}
        assert rejected.content != SEGMENTS[0]
        assert rejected.headers["cache-control"] == "no-store"
        assert len(segment_reads(prefetch_origin, 0)) == 1
        assert registry.stats()["sessions"] == 0
    finally:
        registry.close_all()
        client.close()


def test_closing_session_interrupts_blocked_prefetch_and_returns_reservations(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge()
    prefetch_origin.gates["/segments/1.ts"] = threading.Event()
    prefetch_origin.gates["/segments/2.ts"] = threading.Event()
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        assert client.get(paths[0]).content == SEGMENTS[0]
        assert wait_until(lambda: harness.registry.stats()["active_requests"] > 0)
        harness.registry.close(harness.session.id, OWNER)
        assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)
        assert harness.registry.stats()["reserved_bytes"] == 0
        assert harness.registry.stats()["sessions"] == 0


def asgi_scope(path):
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "headers": [],
            "client": ("127.0.0.1", 12345), "server": ("testserver", 80), "http_version": "1.1"}


def test_disconnect_while_waiting_for_segment_cancels_its_only_download(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge(prefetch_seconds=0)
    prefetch_origin.gates["/segments/0.ts"] = threading.Event()
    with TestClient(harness.app) as client:
        path = playlist(client, harness)[0]
    sent = []

    async def run():
        disconnected = False

        async def receive():
            if disconnected:
                return {"type": "http.disconnect"}
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        task = asyncio.create_task(harness.app(asgi_scope(path), receive, send))
        deadline = time.monotonic() + 2
        while not segment_reads(prefetch_origin, 0):
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        disconnected = True
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
    assert any(message["type"] == "http.response.start" and message["status"] == 410 for message in sent)
    assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)
    assert harness.registry.stats()["reserved_bytes"] == 0
    assert harness.registry._media_cache.stats()["entries"] == 0


def test_cached_response_send_disconnect_releases_cache_ticket_without_upstream_lease(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge(prefetch_seconds=0)
    with TestClient(harness.app) as client:
        path = playlist(client, harness)[0]
        assert client.get(path).content == SEGMENTS[0]

    async def run():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                raise OSError("isolated cached-media receiver disconnected")

        with pytest.raises(ClientDisconnect):
            await harness.app(asgi_scope(path), receive, send)

    asyncio.run(run())
    assert harness.registry.stats()["active_requests"] == 0
    assert harness.registry._media_cache.stats()["waiters"] == 0
    assert len(segment_reads(prefetch_origin, 0)) == 1
    harness.registry.close(harness.session.id, OWNER)
    assert harness.registry._media_cache.stats()["entries"] == 0


def test_one_disconnected_waiter_does_not_abort_another_waiters_shared_download(prefetch_bridge,
                                                                             prefetch_origin):
    harness = prefetch_bridge(prefetch_seconds=0)
    gate = prefetch_origin.gates["/segments/0.ts"] = threading.Event()
    with TestClient(harness.app) as client:
        path = playlist(client, harness)[0]
        other_done, other = fetch_later(client, path, prefetch_origin)
        assert wait_until(lambda: len(segment_reads(prefetch_origin, 0)) == 1)

        async def run():
            disconnected = False

            async def receive():
                if disconnected:
                    return {"type": "http.disconnect"}
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(_message):
                pass

            task = asyncio.create_task(harness.app(asgi_scope(path), receive, send))
            deadline = time.monotonic() + 2
            while harness.registry._media_cache.stats()["merged"] < 1:
                assert time.monotonic() < deadline
                await asyncio.sleep(.01)
            disconnected = True
            await asyncio.wait_for(task, timeout=2)

        asyncio.run(run())
        assert not other_done.is_set()
        assert len(segment_reads(prefetch_origin, 0)) == 1
        gate.set()
        assert other_done.wait(2)
        assert_segment(other, 0)
        assert harness.registry._media_cache.stats()["entries"] == 1


def test_three_foreground_segments_keep_two_origin_reader_cap(prefetch_bridge, prefetch_origin):
    harness = prefetch_bridge(prefetch_seconds=0)
    gate0 = prefetch_origin.gates["/segments/0.ts"] = threading.Event()
    gate1 = prefetch_origin.gates["/segments/1.ts"] = threading.Event()
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        first_done, first = fetch_later(client, paths[0], prefetch_origin)
        second_done, second = fetch_later(client, paths[1], prefetch_origin)
        assert wait_until(lambda: len(segment_reads(prefetch_origin)) == 2)
        third_done, third = fetch_later(client, paths[2], prefetch_origin)
        assert third_done.wait(3)
        assert "error" not in third
        assert third["response"].status_code == 503
        assert not segment_reads(prefetch_origin, 2)
        assert harness.registry.stats()["active_requests"] == 2
        gate0.set()
        gate1.set()
        assert first_done.wait(2) and second_done.wait(2)
        assert_segment(first, 0)
        assert_segment(second, 1)
        assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)


def test_speculation_respects_byte_budget_without_failing_current_demand(prefetch_bridge, prefetch_origin):
    # Enough to reserve the manifest and one segment, but not two media readers.
    harness = prefetch_bridge(max_reserved_bytes=80 * 1024)
    with TestClient(harness.app) as client:
        paths = playlist(client, harness)
        response = client.get(paths[0])
        assert response.status_code == 200 and response.content == SEGMENTS[0]
        assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)
        assert harness.registry.stats()["reserved_bytes"] == 0
        assert harness.registry.stats()["consumed_bytes"] <= 8192 + 3 * len(SEGMENTS[0])


class ForegroundRequest:
    async def is_disconnected(self):
        return False


def gateway_clock(monkeypatch, step, on_sleep):
    """Advance gateway deadlines only; keep asyncio's actual event loop clock."""
    clock = SimpleNamespace(now=time.monotonic())
    real_sleep = asyncio.sleep

    class AsyncClock:
        def __getattr__(self, name):
            return getattr(asyncio, name)

        async def sleep(self, _duration):
            clock.now += step
            on_sleep()
            await real_sleep(0)

    monkeypatch.setattr(gateway, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(gateway, "asyncio", AsyncClock())
    return clock


def test_cached_delivery_does_not_wait_for_or_cancel_two_valid_background_readers(monkeypatch):
    from test_online_prefetch_cache import Harness

    harness = Harness(max_session_active=2)
    session = harness.session()
    resources = harness.resources(session, 6)
    harness.remember(session, resources)
    first = harness.cache.demand(session, resources[0])
    harness.run()
    harness.run()
    harness.cache.release(first)
    old = [entry for entry in harness.cache._entries.values() if not entry.future.done()]
    assert len(old) == 2 and harness.registry.stats()["active_requests"] == 2
    def unexpected_wait():
        pytest.fail("A complete cache hit must not wait for an upstream slot")

    clock = gateway_clock(monkeypatch, 1.0, unexpected_wait)
    started = clock.now

    async def run():
        response = await gateway._cached_response(ForegroundRequest(), harness.registry, session,
                                                  resources[1], None, deadline=started + 45)
        assert response.status_code == 200
        assert harness.registry.stats()["active_requests"] == 2
        assert harness.registry.stats()["reserved_bytes"] == 16
        assert harness.cache.stats()["waiters"] == 1
        assert b"".join([chunk async for chunk in response.body_iterator]) == b"data"

    try:
        asyncio.run(run())
        assert clock.now == started
        assert all(not entry.lease.cancelled.is_set() and not entry.lease._closed for entry in old)
        assert harness.cache.stats()["active"] == 2
        assert harness.cache.stats()["waiters"] == 0
        harness.drain()
        assert harness.cache.stats()["cancelled"] == 0
        assert not harness.cache._failed
        assert [read[1] for read in harness.reads] == [resource.id for resource in resources]
    finally:
        harness.registry.close_all()
        harness.drain()


def test_real_cache_miss_waits_for_cancelled_background_more_than_two_seconds(monkeypatch):
    from test_online_prefetch_cache import Harness

    harness = Harness(max_session_active=2)
    session = harness.session()
    resources = harness.resources(session, 6)
    harness.remember(session, resources)
    first = harness.cache.demand(session, resources[0])
    harness.run()
    harness.run()
    harness.cache.release(first)
    old = [entry for entry in harness.cache._entries.values() if not entry.future.done()]
    waits = 0

    def drain_later():
        nonlocal waits
        waits += 1
        if waits <= 3:
            assert all(entry.lease.cancelled.is_set() for entry in old)
            assert harness.registry.stats()["active_requests"] == 2
            assert harness.registry.stats()["reserved_bytes"] == 16
        if waits == 3:
            harness.run()
            harness.run()
        if waits == 4:
            harness.run()  # The actual foreground miss now finishes downloading.

    clock = gateway_clock(monkeypatch, 1.0, drain_later)
    started = clock.now

    async def run():
        response = await gateway._cached_response(ForegroundRequest(), harness.registry, session,
                                                  resources[-1], None, deadline=started + 45)
        assert response.status_code == 200
        assert b"".join([chunk async for chunk in response.body_iterator]) == b"data"

    try:
        asyncio.run(run())
        assert clock.now - started == 4
        assert all(entry.lease._closed for entry in old)
        assert harness.registry.stats()["active_requests"] == 0
        assert harness.cache.stats()["waiters"] == 0
        assert [read[1] for read in harness.reads].count(resources[-1].id) == 1
    finally:
        harness.registry.close_all()
        harness.drain()


def test_foreground_preemption_wait_stops_at_original_http_deadline(monkeypatch):
    from test_online_prefetch_cache import Harness

    harness = Harness()
    session = harness.session()
    attempts = 0

    def busy():
        nonlocal attempts
        attempts += 1
        raise SessionError("prefetch_busy")

    clock = gateway_clock(monkeypatch, 10.0, lambda: None)
    started = clock.now

    async def run():
        with pytest.raises(SessionError, match="^request_timeout$"):
            await gateway._foreground(ForegroundRequest(), harness.registry, session, busy,
                                      deadline=started + 45)

    try:
        asyncio.run(run())
        assert attempts == 5
        assert harness.registry.stats()["active_requests"] == 0
        assert not harness.registry._foreground_waits._tickets
    finally:
        harness.registry.close_all()


def test_download_and_cached_send_keep_one_absolute_http_deadline(monkeypatch):
    from test_online_prefetch_cache import Harness

    harness = Harness(max_session_active=2)
    session = harness.session()
    resource = harness.resources(session, 1)[0]
    waits = 0

    def finish_later():
        nonlocal waits
        waits += 1
        if waits == 3:
            harness.run()

    clock = gateway_clock(monkeypatch, 10.0, finish_later)
    started = clock.now
    harness.registry._clock = lambda: clock.now

    async def run():
        response = await gateway._cached_response(ForegroundRequest(), harness.registry, session,
                                                  resource, None, deadline=started + 45)
        assert clock.now == started + 30
        assert response.deadline == started + 45
        assert harness.registry.stats()["active_requests"] == 0
        assert harness.cache.stats()["waiters"] == 1
        clock.now = started + 46
        with pytest.raises(gateway.GatewayError, match="^request_timeout$"):
            async for _chunk in response.body_iterator:
                pytest.fail("Delivery must stop when the original HTTP deadline expires")

    try:
        asyncio.run(run())
        assert harness.registry.stats()["active_requests"] == 0
        assert harness.cache.stats()["waiters"] == 0
    finally:
        harness.registry.close_all()
        harness.drain()


def test_session_close_interrupts_preemption_wait_without_using_remaining_deadline(monkeypatch):
    from test_online_prefetch_cache import Harness

    harness = Harness()
    session = harness.session()

    def close_session():
        harness.registry.close(session.id, session.owner)

    clock = gateway_clock(monkeypatch, 1.0, close_session)
    started = clock.now

    def busy():
        raise SessionError("prefetch_busy")

    async def run():
        with pytest.raises(SessionError, match="^session_closed$"):
            await gateway._foreground(ForegroundRequest(), harness.registry, session, busy,
                                      deadline=started + 45)

    asyncio.run(run())
    assert clock.now - started == 1
    assert not harness.registry._foreground_waits._tickets


def test_many_foreground_requests_cannot_queue_unbounded_behind_background_workers(monkeypatch):
    from test_online_prefetch_cache import Harness

    harness = Harness(max_session_active=2)
    session = harness.session()
    background = [harness.registry.acquire(session, 8, kind="segment", background=True) for _ in range(2)]

    async def run():
        gate = asyncio.Event()
        real_asyncio = asyncio

        class PausedAsync:
            def __getattr__(self, name):
                return getattr(real_asyncio, name)

            async def sleep(self, _duration):
                await gate.wait()

        monkeypatch.setattr(gateway, "asyncio", PausedAsync())
        deadline = time.monotonic() + 45
        tasks = [asyncio.create_task(gateway._foreground(
            ForegroundRequest(), harness.registry, session,
            lambda: harness.registry.acquire(session, 0, kind="segment"), deadline=deadline)) for _ in range(12)]
        await asyncio.sleep(0)
        rejected = [task for task in tasks if task.done()]
        assert len(rejected) == 10
        assert all(isinstance(task.exception(), SessionError) and task.exception().category == "request_limit"
                   for task in rejected)
        assert len(harness.registry._foreground_waits._tickets) == 2
        assert harness.registry.stats()["active_requests"] == 2
        assert harness.registry.stats()["reserved_bytes"] == 16
        assert all(lease.cancelled.is_set() and not lease._closed for lease in background)
        for lease in background:
            lease.close()  # Simulate the workers' actual finally cleanup.
        gate.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        admitted = [result for result in results if not isinstance(result, Exception)]
        assert len(admitted) == 2
        assert not harness.registry._foreground_waits._tickets
        assert harness.registry.stats()["active_requests"] == 2
        for lease in admitted:
            lease.close()

    try:
        asyncio.run(run())
        assert harness.registry.stats()["active_requests"] == 0
    finally:
        for lease in background:
            lease.close()
        harness.registry.close_all()


def test_cancelled_foreground_wait_releases_queue_registration_but_not_background_slot(monkeypatch):
    from test_online_prefetch_cache import Harness

    harness = Harness(max_session_active=2)
    session = harness.session()
    background = [harness.registry.acquire(session, 8, kind="segment", background=True) for _ in range(2)]

    async def run():
        blocked = asyncio.Event()
        real_asyncio = asyncio

        class PausedAsync:
            def __getattr__(self, name):
                return getattr(real_asyncio, name)

            async def sleep(self, _duration):
                await blocked.wait()

        monkeypatch.setattr(gateway, "asyncio", PausedAsync())
        task = asyncio.create_task(gateway._foreground(
            ForegroundRequest(), harness.registry, session,
            lambda: harness.registry.acquire(session, 0, kind="segment"), deadline=time.monotonic() + 45))
        await asyncio.sleep(0)
        assert len(harness.registry._foreground_waits._tickets) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not harness.registry._foreground_waits._tickets
        assert harness.registry.stats()["active_requests"] == 2

    try:
        asyncio.run(run())
    finally:
        for lease in background:
            lease.close()
        harness.registry.close_all()


def test_foreground_wait_registration_bounds_source_global_and_closed_session_entries():
    class Session:
        def __init__(self, source):
            self.source_id = source
            self.cancelled = threading.Event()

    state = gateway._ForegroundWaits()
    limits = SimpleNamespace(max_active=3, max_source_active=2, max_session_active=2)
    one, two, three, other, last = [Session(source) for source in ("a", "a", "a", "b", "c")]
    tokens = [state.claim(one, limits), state.claim(two, limits)]
    with pytest.raises(SessionError, match="^request_limit$"):
        state.claim(three, limits)
    tokens.append(state.claim(other, limits))
    with pytest.raises(SessionError, match="^request_limit$"):
        state.claim(last, limits)
    one.cancelled.set()
    tokens.append(state.claim(last, limits))
    assert len(state._tickets) == 3
    with pytest.raises(SessionError, match="^session_closed$"):
        state.claim(one, limits)
    for token in tokens:
        state.release(token)
    assert not state._tickets


def test_cache_only_request_refreshes_idle_once_without_an_upstream_lease():
    from test_online_prefetch_cache import Harness

    harness = Harness(prefetch_seconds=0, idle_seconds=10, ttl_seconds=100)
    clock = SimpleNamespace(now=time.monotonic())
    harness.registry._clock = lambda: clock.now
    session = harness.session()
    resource = harness.resources(session, 1)[0]
    first = harness.cache.demand(session, resource)
    harness.run()
    harness.cache.release(first)
    clock.now += 9

    async def run():
        response = await gateway._cached_response(ForegroundRequest(), harness.registry, session,
                                                  resource, None, deadline=time.monotonic() + 45)
        admitted_at = clock.now
        assert session.last_activity == admitted_at
        assert harness.registry.stats()["active_requests"] == 0
        assert harness.cache.stats()["waiters"] == 1
        clock.now += 3  # Continuous cache delivery remains valid past the old idle deadline.
        assert b"".join([chunk async for chunk in response.body_iterator]) == b"data"
        assert session.last_activity == admitted_at, "Chunks must not keep renewing the idle deadline"

    try:
        asyncio.run(run())
        assert harness.cache.stats()["waiters"] == 0
        assert len(harness.reads) == 1
        clock.now += 7
        with pytest.raises(SessionError, match="^session_closed$"):
            harness.registry.check(session)
    finally:
        harness.registry.close_all()
        harness.drain()


def test_slow_cached_delivery_pins_payload_until_closed_session_releases_ticket():
    from app.online.prefetch import CachedSegment
    from test_online_prefetch_cache import Harness

    body = b"x" * (gateway.CHUNK_BYTES + 1)
    harness = Harness(prefetch_seconds=0, max_segment_response_bytes=len(body),
                      prefetch_session_bytes=2 * len(body), prefetch_global_bytes=2 * len(body),
                      max_session_bytes=4 * len(body), max_reserved_bytes=4 * len(body))
    session = harness.session()
    resource = harness.resources(session, 1)[0]

    def fetch(_session, _resource, lease):
        lease.consume(len(body))
        return CachedSegment(200, {"Content-Length": str(len(body))}, body)

    harness.cache.fetch = fetch
    first = harness.cache.demand(session, resource)
    harness.run()
    harness.cache.release(first)

    async def run():
        response = await gateway._cached_response(ForegroundRequest(), harness.registry, session,
                                                  resource, None, deadline=time.monotonic() + 45)
        assert await anext(response.body_iterator) == body[:gateway.CHUNK_BYTES]
        assert harness.cache.stats()["waiters"] == 1
        assert harness.registry.stats()["active_requests"] == 0
        harness.registry.close(session.id, session.owner)
        assert harness.cache.stats()["bytes"] == len(body)
        assert harness.cache.stats()["waiters"] == 1
        with pytest.raises(SessionError, match="^session_closed$"):
            await anext(response.body_iterator)
        assert harness.cache.stats()["waiters"] == 0
        assert harness.cache.stats()["bytes"] == 0

    try:
        asyncio.run(run())
    finally:
        harness.registry.close_all()
        harness.drain()
