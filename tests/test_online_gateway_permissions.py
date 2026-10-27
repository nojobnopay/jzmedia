"""Full online routes revoke real blocked loopback reads without NAS access."""

from http.server import BaseHTTPRequestHandler
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.config import settings
from app.main import app
from app.network.client import create_client
from app.network.policy import resolve_online_policy
from app.online import gateway, providers, service
from app.online.sessions import CHUNK_BYTES, SessionLimits, SessionRegistry
from app.store import online
from test_network_transport import LoopbackPolicy, logical_url, running_server


pytest_plugins = ["test_network_transport"]

PREFIX = b"already-read"
TOTAL = CHUNK_BYTES + 5


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return predicate()


@pytest.fixture
def full_gateway(source, monkeypatch):
    """Use the real application without entering its media/scanner lifespan."""
    monkeypatch.setattr(settings, "jzmedia_token", "")
    online.update_online_source(source, {"enabled": True, "allowed_hosts": ["fixture.test"]})
    online.update_online_settings(True)
    item = online.upsert_online_item(source, "isolated-blocked-film", "movie", "Isolated film")
    identity = {"source_id": source, "item_id": item["id"], "kind": "movie"}
    limits = SessionLimits(ttl_seconds=60, idle_seconds=30, request_seconds=10,
                           max_mp4_response_bytes=2 * CHUNK_BYTES,
                           max_reserved_bytes=4 * CHUNK_BYTES,
                           max_session_bytes=8 * CHUNK_BYTES)
    registry = SessionRegistry(limits)
    monkeypatch.setattr(service, "registry", registry)
    readers, request_threads, records = [], [], []
    sent, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def handle_one_request(self):
            try:
                super().handle_one_request()
            except OSError:
                self.close_connection = True

        def do_GET(self):
            records.append({"path": self.path, "headers": dict(self.headers)})
            try:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(TOTAL))
                self.end_headers()
                self.wfile.write(PREFIX)
                self.wfile.flush()
                sent.set()
                # Cancellation, rather than an upstream idle/read timeout,
                # must finish the downstream request in the test's 2s gate.
                release.wait(timeout=10)
                if release.is_set():
                    self.wfile.write(b"x" * (TOTAL - len(PREFIX)))
                    self.wfile.flush()
            except OSError:
                self.close_connection = True

    gateway.start_workers()
    with running_server(Handler) as upstream:
        url = logical_url(upstream, "fixture.test") + "/blocked.mp4?signature=private-origin-secret"
        monkeypatch.setattr(providers, "resolve", lambda _identity: providers.ResolvedPlayback(url, "mp4"))

        def factory(source_id, purpose, *, header_policy=None):
            assert source_id == source and purpose == "media"
            reader = create_client(resolve_online_policy(source_id, purpose),
                                   targets=LoopbackPolicy(source_id, ["fixture.test"],
                                                          resolver=lambda _host, _port: ("127.0.0.1",)),
                                   header_policy=header_policy)
            readers.append(reader)
            return reader

        monkeypatch.setattr(service, "create_online_client", factory)
        client = TestClient(app, raise_server_exceptions=False)
        try:
            assert client.get("/api/viewing/session").status_code == 200
            yield SimpleNamespace(client=client, registry=registry, identity=identity, readers=readers,
                                  sent=sent, records=records, threads=request_threads)
        finally:
            registry.close_all()
            release.set()
            for thread in request_threads:
                thread.join(timeout=2)
            client.close()
            drained = wait_until(lambda: registry.stats()["active_requests"] == 0)
            gateway.shutdown_workers()
            assert drained, "isolated upstream workers must release their leases"
            assert all(not thread.is_alive() for thread in request_threads)


@pytest.mark.parametrize("revocation", ["source", "feature", "viewing_generation"])
def test_http_source_and_viewing_revocation_interrupt_real_inflight_bytes(full_gateway, revocation):
    harness = full_gateway
    creation = harness.client.post("/api/online/sessions", json=harness.identity)
    assert creation.status_code == 201, creation.text
    assert "private-origin-secret" not in creation.text
    descriptor = creation.json()
    finished, result = threading.Event(), {}

    def fetch():
        try:
            result["response"] = harness.client.get(descriptor["media_url"])
        except BaseException as error:
            # An incomplete ASGI response may surface as a cancelled/failed
            # request; it must never become a complete media body.
            result["error"] = error
        finally:
            finished.set()

    thread = threading.Thread(target=fetch, name="online-permission-read", daemon=True)
    harness.threads.append(thread)
    thread.start()
    assert harness.sent.wait(timeout=2)
    # The sub-64KiB prefix must already have been read and charged before the
    # gate changes. This guards against sized-iterator buffering false positives.
    assert wait_until(lambda: harness.registry.stats()["consumed_bytes"] == len(PREFIX))
    before = harness.registry.stats()
    assert before["active_requests"] == 1 and before["reserved_bytes"] > 0
    assert not finished.is_set()

    started = time.monotonic()
    if revocation == "source":
        response = harness.client.patch(f'/api/online/sources/{harness.identity["source_id"]}',
                                        json={"enabled": False})
    elif revocation == "feature":
        response = harness.client.patch("/api/online/settings", json={"enabled": False})
    else:
        # This endpoint updates the same cookie's generation even when content
        # protection is disabled. Explicit sweep models the one-second task.
        response = harness.client.post("/api/viewing/lock")
        assert response.status_code == 200, response.text
        service.sweep()
    assert response.status_code == 200, response.text
    assert finished.wait(timeout=2), "revocation must interrupt a blocked socket read"
    thread.join(timeout=.1)
    assert time.monotonic() - started < 2
    assert not thread.is_alive()
    assert wait_until(lambda: harness.registry.stats()["active_requests"] == 0)
    final = harness.registry.stats()
    assert final["sessions"] == final["active_requests"] == final["reserved_bytes"] == 0
    assert final["consumed_bytes"] == len(PREFIX)
    assert len(harness.readers) == 1 and harness.readers[0]._client.is_closed
    assert len(harness.records) == 1
    assert "jz_viewing" not in str(harness.records[0]["headers"])
    assert "response" in result or "error" in result
    if "response" in result:
        # TestClient can retain the 200 headers after a streaming failure. A
        # truncated transfer is explicitly not a successful full-file result.
        assert len(result["response"].content) < TOTAL
