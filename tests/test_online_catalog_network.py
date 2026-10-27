"""Real bounded provider reads abort before headers and during a stalled body."""

from http.server import BaseHTTPRequestHandler
import threading
import time
from urllib.parse import urlsplit

import pytest

from app.network.client import create_client
from app.network.guard import NetworkOperation, operation_context
from app.network.policy import resolve_online_policy
from app.network.targets import TargetPolicyError
from app.online import catalog, maccms
from app.online.providers import ProviderError
from app.store import online
from test_network_transport import LoopbackPolicy, running_server


pytest_plugins = ["test_network_transport"]


@pytest.mark.parametrize("phase", ["headers", "body"])
@pytest.mark.parametrize("finish", ["cancel", "deadline"])
def test_real_provider_io_stops_and_worker_releases_admission(source, monkeypatch, phase, finish):
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    errors = []
    body = b'{"code":1,"list":[]}'

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            entered.set()
            if phase == "headers":
                release.wait(timeout=3)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if phase == "body":
                    self.wfile.write(body[:1])
                    self.wfile.flush()
                    release.wait(timeout=3)
                    self.wfile.write(body[1:])
                else:
                    self.wfile.write(body)
            except OSError:
                pass

    with running_server(Handler) as address:
        port = urlsplit(address).port
        selected = online.update_online_source(source, {"enabled": True})
        online.update_online_settings(True)
        selected = {**selected, "api_base_url": f"http://fixture.invalid:{port}/provide/vod/",
                    "allowed_hosts": ["fixture.invalid"]}

        def resolver(host, requested_port):
            if host != "fixture.invalid" or requested_port != port:
                raise TargetPolicyError("target_host_not_allowed")
            return ("127.0.0.1",)

        def factory(source_id, purpose):
            assert source_id == source and purpose == "api"
            return create_client(resolve_online_policy(source, purpose),
                                 targets=LoopbackPolicy(source, ["fixture.invalid"], resolver=resolver))

        monkeypatch.setattr(maccms, "create_online_client", factory)
        operation = NetworkOperation(time.monotonic() + (2 if finish == "cancel" else .3))

        def worker():
            try:
                with operation_context(operation), catalog._admit("real-http-provider-owner"):
                    maccms.fetch(selected, {"ac": "list", "wd": "synthetic"})
            except ProviderError as error:
                errors.append(error.category)
            finally:
                completed.set()

        thread = threading.Thread(target=worker, name="catalog-cancel-fixture")
        thread.start()
        try:
            assert entered.wait(timeout=1.5), "the fixture must enter a real socket request"
            started = time.monotonic()
            if finish == "cancel":
                operation.abort()
            assert completed.wait(timeout=1.5), "provider I/O must stop before fixture release or its 3s stall"
            assert time.monotonic() - started < 1.5
            thread.join(timeout=.5)
            assert not thread.is_alive()
            assert errors == ["provider_cancelled" if finish == "cancel" else "provider_timeout"]
            assert not operation._closed, "a borrowed create operation is owned by its caller"
            with catalog._lock:
                assert catalog._active == 0 and "real-http-provider-owner" not in catalog._owners
        finally:
            operation.abort()
            operation.close()
            release.set()
            thread.join(timeout=2)
