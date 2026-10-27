"""The NAS P0 relay is exercised against in-memory upstreams, never public media."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

import httpx
import pytest

from scripts.online_p0.public_relay import RANGE_BYTES, Relay, child_observation, make_server, validate_config


TOKEN = "isolated_p0_relay_test_token_1234567890"
URL = ("https://video.blender.org/object-storage/web_videos/"
       "8e364d32-8918-403e-a8c0-915634b9f405-360.mp4")
TOTAL = RANGE_BYTES * 2 + 123


def config(**extra):
    return {"token": TOKEN, "sources": {"lecture": URL}, "port": 0, **extra}


def wait_for_idle(relay, timeout=1):
    """Receiving the last response byte precedes the handler's finally cleanup."""
    deadline = time.monotonic() + timeout
    while True:
        with relay.lock:
            active = relay.counts["active"]
        if active == 0:
            return relay.metrics()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail(f"relay did not release {active} active request(s) within {timeout}s")
        # Poll the actual cleanup condition, returning as soon as it is met.
        time.sleep(min(0.005, remaining))


def upstream(request):
    assert request.url == URL
    assert request.headers["Accept-Encoding"] == "identity"
    assert "Proxy-Authorization" not in request.headers
    if "Range" not in request.headers:
        return httpx.Response(200, headers={"Content-Length": str(TOTAL)})
    start, end = map(int, request.headers["Range"][6:].split("-"))
    if start >= TOTAL:
        return httpx.Response(416, headers={"Content-Range": f"bytes */{TOTAL}"})
    end = min(end, TOTAL - 1)
    length = end - start + 1
    body = bytes((i % 251 for i in range(start, end + 1))) if request.method == "GET" else b""
    return httpx.Response(206, headers={"Content-Length": str(length),
                                       "Content-Range": f"bytes {start}-{end}/{TOTAL}"}, stream=httpx.ByteStream(body))


@contextmanager
def running(handler=upstream, **extra):
    relay = Relay(config(**extra), transport=httpx.MockTransport(handler))
    server = make_server(relay)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False,
                          timeout=5) as client:
            yield relay, client, f"/{TOKEN}"
    finally:
        server.shutdown()
        server.server_close()
        relay.close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("url", [
    "http://video.blender.org/object-storage/web_videos/8e364d32-8918-403e-a8c0-915634b9f405-360.mp4",
    URL + "?url=http://localhost/",
    URL.replace("video.blender.org", "127.0.0.1"),
    URL.replace("video.blender.org", "video.blender.org@localhost"),
    URL.replace("360.mp4", "720.mp4"),
])
def test_config_has_no_general_url_gateway(url):
    with pytest.raises(ValueError):
        validate_config(config(sources={"lecture": url}))


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.1", "8.8.8.8", "100.63.255.255", "100.128.0.1", "::1", "localhost"])
def test_bind_rejects_wildcard_public_lan_and_names(host):
    with pytest.raises(ValueError):
        validate_config(config(bind_host=host))


def test_bind_defaults_loopback_and_accepts_explicit_tailscale_address():
    assert validate_config(config())["bind_host"] == "127.0.0.1"
    assert validate_config(config(bind_host="100.122.85.97"))["bind_host"] == "100.122.85.97"


@pytest.mark.parametrize("cap", [0, 65535, 65537, RANGE_BYTES + 65536, True, 65536.0, "65536"])
def test_invalid_diagnostic_range_cap(cap):
    with pytest.raises(ValueError):
        validate_config(config(max_range_bytes=cap))


@pytest.mark.parametrize("cap", [65536, 524288, RANGE_BYTES])
def test_valid_diagnostic_range_cap(cap):
    assert validate_config(config(max_range_bytes=cap))["max_range_bytes"] == cap


def test_smaller_explicit_range_cap_controls_response_suffix_and_metrics():
    cap = 524288
    with running(max_range_bytes=cap) as (relay, client, base):
        result = client.get(base + "/media/lecture", headers={"Range": "bytes=0-"})
        assert result.status_code == 206 and len(result.content) == cap
        assert result.headers["content-range"] == f"bytes 0-{cap - 1}/{TOTAL}"
        result = client.get(base + "/media/lecture", headers={"Range": "bytes=11-9999999"})
        assert result.status_code == 206 and len(result.content) == cap
        assert result.headers["content-range"] == f"bytes 11-{11 + cap - 1}/{TOTAL}"
        result = client.get(base + "/media/lecture", headers={"Range": "bytes=-9999999"})
        assert result.status_code == 206 and len(result.content) == cap
        assert result.headers["content-range"] == f"bytes {TOTAL - cap}-{TOTAL - 1}/{TOTAL}"
        metrics = wait_for_idle(relay)
        assert metrics["response_range_limit_bytes"] == cap
        assert metrics["bytes_sent"] == cap * 3


def test_child_measurement_task_files_and_dsm_stat_fallback(tmp_path):
    task = tmp_path / "100/task/100"
    task.mkdir(parents=True)
    (task / "children").write_text("101 102\n")
    assert child_observation(tmp_path, 100) == {"child_pids": [101, 102], "child_measurement": "task_children"}
    (task / "children").unlink()
    (tmp_path / "100/stat").write_text("100 (python parent) S 99 0 0\n")
    for pid, ppid in ((101, 100), (102, 100), (103, 99)):
        process = tmp_path / str(pid)
        process.mkdir()
        (process / "stat").write_text(f"{pid} (name with ) parentheses) S {ppid} 0 0\n")
    assert child_observation(tmp_path, 100) == {"child_pids": [101, 102], "child_measurement": "proc_stat"}
    for pid in (101, 102):
        (tmp_path / str(pid) / "stat").write_text(f"{pid} (other) S 99 0 0\n")
    assert child_observation(tmp_path, 100) == {"child_pids": [], "child_measurement": "proc_stat"}


@pytest.mark.parametrize("condition", ["missing", "malformed", "unreadable", "own_missing"])
def test_child_measurement_missing_data_never_reports_zero(tmp_path, monkeypatch, condition):
    own = tmp_path / "100"
    other = tmp_path / "101"
    own.mkdir()
    other.mkdir()
    (own / "stat").write_text("100 (python) S 99 0 0\n")
    (other / "stat").write_text("101 (child) S 100 0 0\n")
    if condition == "missing":
        (other / "stat").unlink()
    elif condition == "malformed":
        (other / "stat").write_text("truncated")
    elif condition == "own_missing":
        (own / "stat").unlink()
        own.rmdir()
    else:
        import builtins
        actual_open = builtins.open

        def guarded_open(path, *args, **kwargs):
            if str(path) == str(other / "stat"):
                raise PermissionError("test proc visibility")
            return actual_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", guarded_open)
    assert child_observation(tmp_path, 100) == {"child_measurement": "unavailable"}


def test_range_cap_head_suffix_tail_and_unsatisfiable(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    with running() as (relay, client, base):
        result = client.head(base + "/media/lecture")
        assert result.status_code == 200 and result.headers["content-length"] == str(TOTAL)
        assert not result.content
        result = client.get(base + "/media/lecture", headers={"Range": "bytes=0-"})
        assert result.status_code == 206 and len(result.content) == RANGE_BYTES
        assert result.headers["content-range"] == f"bytes 0-{RANGE_BYTES - 1}/{TOTAL}"
        result = client.get(base + "/media/lecture", headers={"Range": "bytes=-19"})
        assert result.status_code == 206 and len(result.content) == 19
        assert result.headers["content-range"] == f"bytes {TOTAL - 19}-{TOTAL - 1}/{TOTAL}"
        result = client.head(base + "/media/lecture", headers={"Range": "bytes=12-18"})
        assert result.status_code == 206 and result.headers["content-length"] == "7"
        assert not result.content
        result = client.get(base + "/media/lecture", headers={"Range": f"bytes={TOTAL}-"})
        assert result.status_code == 416 and result.headers["content-range"] == f"bytes */{TOTAL}"
        metrics = wait_for_idle(relay)
        assert metrics["active"] == 0 and metrics["completed"] == 2
        assert metrics["bytes_sent"] == RANGE_BYTES + 19 and metrics["upstream_errors"] == 0
        assert metrics.get("child_pids", []) == []  # /proc visibility differs by test sandbox.


def test_missing_invalid_and_multiple_ranges_never_call_upstream():
    def no_call(_request):
        pytest.fail("invalid range reached upstream")

    with running(no_call) as (_relay, client, base):
        for value in (None, "bytes=0-1,4-5", "bytes=20-2", "bytes=-0", "bytes=-", "things=1-2"):
            response = client.get(base + "/media/lecture", headers={"Range": value} if value else {})
            assert response.status_code == 400
        assert client.get("/wrong-token/metrics").status_code == 404
        assert client.get(base + "/media/lecture?url=https://example.com").status_code == 404
        assert client.post("/wrong-token/shutdown").status_code == 404


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "http://127.0.0.1:8080/"}),
    httpx.Response(200, content=b"range ignored"),
    httpx.Response(206, headers={"Content-Range": f"bytes 1-3/{TOTAL}", "Content-Length": "3"}),
    httpx.Response(206, headers={"Content-Range": f"bytes 0-3/{TOTAL}", "Content-Length": "4",
                                 "Content-Encoding": "gzip"}),
])
def test_redirect_and_bad_range_are_rejected(response):
    with running(lambda _request: response) as (relay, client, base):
        result = client.get(base + "/media/lecture", headers={"Range": "bytes=0-3"})
        metrics = wait_for_idle(relay)
        assert result.status_code == 502 and metrics["upstream_errors"] == 1
        assert metrics["active"] == 0


def test_bounded_retry_before_downstream_headers_only():
    calls = []

    def flaky(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            raise httpx.ConnectError("test failure")
        return upstream(request)

    with running(flaky) as (relay, client, base):
        result = client.get(base + "/media/lecture", headers={"Range": "bytes=0-3"})
        assert result.status_code == 206 and len(result.content) == 4
        assert calls == [URL, URL] and relay.metrics()["retries"] == 1


def test_stream_failure_remains_truncated_and_not_retried():
    closed = threading.Event()
    calls = []

    class FailingStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * 65536
            raise httpx.ReadError("isolated midstream failure")

        def close(self):
            closed.set()

    def failed(request):
        calls.append(str(request.url))
        return httpx.Response(206, headers={"Content-Length": "131072",
                                           "Content-Range": f"bytes 0-131071/{TOTAL}"},
                              stream=FailingStream())

    with running(failed) as (relay, client, base):
        with pytest.raises(httpx.RemoteProtocolError):
            client.get(base + "/media/lecture", headers={"Range": "bytes=0-131071"})
        assert closed.wait(1) and calls == [URL]
        metrics = wait_for_idle(relay)
        assert metrics["upstream_errors"] == 1 and metrics["retries"] == 0
        assert metrics["active"] == 0


def test_two_media_requests_limit_and_release():
    release = threading.Event()
    entered = threading.Event()
    counter_lock = threading.Lock()
    count = 0

    def slow(request):
        nonlocal count
        with counter_lock:
            count += 1
            if count == 2:
                entered.set()
        assert release.wait(3)
        return upstream(request)

    with running(slow) as (relay, client, base), ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.get, base + "/media/lecture", headers={"Range": "bytes=0-3"})
                   for _ in range(2)]
        try:
            assert entered.wait(2)
            assert client.get(base + "/metrics").json()["active"] == 2
            assert client.get(base + "/media/lecture", headers={"Range": "bytes=0-3"}).status_code == 429
        finally:
            release.set()
        assert [future.result().status_code for future in futures] == [206, 206]
        metrics = wait_for_idle(relay)
        assert metrics["active"] == 0 and metrics["peak_active"] == 2


def test_disconnect_closes_upstream_and_returns_slot():
    closed = threading.Event()

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(64):
                yield b"x" * 65536
                time.sleep(0.01)

        def close(self):
            closed.set()

    def slow(_request):
        return httpx.Response(206, headers={"Content-Length": str(RANGE_BYTES),
                                           "Content-Range": f"bytes 0-{RANGE_BYTES - 1}/{TOTAL}"},
                              stream=SlowStream())

    with running(slow) as (relay, client, base):
        port = client.base_url.port
        with socket.create_connection(("127.0.0.1", port)) as conn:
            conn.sendall(f"GET {base}/media/lecture HTTP/1.1\r\nHost: localhost\r\nRange: bytes=0-\r\n\r\n".encode())
            assert conn.recv(4096).startswith(b"HTTP/1.1 206")
        assert closed.wait(2)
        metrics = wait_for_idle(relay)
        assert metrics["active"] == 0 and metrics["client_disconnects"] == 1


def test_total_byte_budget_and_authenticated_shutdown():
    with running(max_bytes_total=5) as (relay, client, base):
        assert client.get(base + "/media/lecture", headers={"Range": "bytes=0-3"}).content == bytes(range(4))
        result = client.get(base + "/media/lecture", headers={"Range": "bytes=8-12"})
        assert result.status_code == 206 and result.content == bytes([8])
        assert client.get(base + "/media/lecture", headers={"Range": "bytes=0-3"}).status_code == 503
        metrics = wait_for_idle(relay)
        assert metrics["budget_charged_bytes"] == metrics["bytes_sent"] == 5
        assert client.post(base + "/shutdown").json() == {"stopping": True}
        assert relay.stop.wait(1)


def test_stdin_mode_exits_at_ttl_without_repository_import_or_media_write(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/online_p0/public_relay.py"
    env = dict(os.environ, P0_RELAY_CONFIG=json.dumps(config(ttl_seconds=1)), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([str(Path(os.sys.executable)), "-u", "-"], input=script.read_text(),
                            text=True, capture_output=True, env=env, cwd=tmp_path, timeout=5)
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[0]["event"] == "ready" and events[-1]["event"] == "stopped"
    assert events[-1]["requests"] == 0 and not list(tmp_path.iterdir())
    assert TOKEN not in result.stdout
