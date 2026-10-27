"""Operation deadlines/cancellation use fake socket I/O, never external media."""

from contextlib import contextmanager
import ssl
import threading
import time

import httpcore
import pytest

from app.network import guard, targets, transport
from app.network.client import NetworkRequestError, create_client
from app.network.guard import (NetworkOperation, NetworkOperationError,
                               OperationSocket, operation_context)
from app.network.policy import NetworkSnapshot
from app.network.targets import TargetPolicy


SOURCE_ID = "src_" + "a" * 32


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeSocket:
    def __init__(self, clock, *, receive_cost=.3, send_cost=0.0, partial=False, body=b"x" * 100):
        self.clock, self.receive_cost, self.send_cost = clock, receive_cost, send_cost
        self.partial, self.body = partial, bytearray(body)
        self.timeouts, self.received, self.sent = [], [], []
        self.shutdowns, self.closed = 0, False
        self.timeout = None

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value
        self.timeouts.append(value)

    def recv(self, max_bytes):
        self.clock.now += self.receive_cost
        self.received.append(max_bytes)
        count = min(1, max_bytes, len(self.body))
        result = bytes(self.body[:count])
        del self.body[:count]
        return result

    def send(self, body):
        self.clock.now += self.send_cost
        count = min(1, len(body)) if self.partial else len(body)
        self.sent.append(body[:count])
        return count

    def sendall(self, body):
        self.clock.now += self.send_cost
        self.sent.append(body)

    def shutdown(self, _how):
        self.shutdowns += 1

    def close(self):
        self.closed = True

    def getpeername(self):
        return "8.8.8.8", 80

    def getsockname(self):
        return "127.0.0.1", 12345

    def fileno(self):
        return -1


class Attachments:
    def __init__(self):
        self.callbacks, self.detached = [], 0

    def attach(self, callback):
        self.callbacks.append(callback)

        def detach():
            self.callbacks.remove(callback)
            self.detached += 1

        return detach


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(guard.time, "monotonic", clock)
    return clock


@pytest.mark.parametrize("deadline,cancelled,attach", [
    (True, None, None), (float("nan"), None, None), (float("inf"), None, None),
    (0, None, None), (-1, None, None), ("private-secret", None, None),
    (1, object(), None), (1, None, object()),
])
def test_invalid_operation_arguments_have_a_fixed_error(deadline, cancelled, attach):
    with pytest.raises(NetworkOperationError, match="^invalid_network_operation$"):
        NetworkOperation(deadline, cancelled, attach=attach)


def test_remaining_timeout_checks_cancel_and_absolute_deadline(clock):
    operation = NetworkOperation(5.0)
    assert operation.remaining() == 5.0
    assert operation.remaining(2.0) == 2.0
    clock.now = 4.5
    assert operation.remaining(2.0) == .5
    clock.now = 5.0
    with pytest.raises(NetworkOperationError, match="^operation_timeout$"):
        operation.check()
    assert operation.cancelled.is_set()
    assert "5.0" not in repr(operation)


def test_cancelled_operation_does_not_open_an_io_context(clock):
    event = threading.Event()
    event.set()
    operation = NetworkOperation(5.0, event)
    with pytest.raises(NetworkOperationError, match="^operation_cancelled$"):
        with operation_context(operation):
            pytest.fail("cancelled operations must fail before entering")
    assert guard.current_operation() is None


def test_each_partial_socket_send_receives_the_remaining_budget(clock):
    raw = FakeSocket(clock, send_cost=.4, partial=True)
    operation = NetworkOperation(1.0)
    stream = transport.SyncStream(OperationSocket(raw))
    with operation_context(operation):
        with pytest.raises(NetworkOperationError, match="^operation_timeout$"):
            stream.write(b"video", timeout=10.0)
    assert len(raw.sent) == 3
    assert raw.timeouts == sorted(raw.timeouts, reverse=True)
    assert raw.timeouts[-1] == pytest.approx(.2)
    assert raw.shutdowns == 1


def test_success_detaches_without_shutdown_and_reuse_gets_a_new_operation(clock):
    raw = FakeSocket(clock, receive_cost=1.0)
    pooled_socket = OperationSocket(raw)
    attachments = Attachments()
    first = NetworkOperation(10.0, attach=attachments.attach)
    with operation_context(first):
        assert pooled_socket.recv(1) == b"x"
        assert len(attachments.callbacks) == 1
    first.close()
    first.close()
    assert raw.shutdowns == 0
    assert attachments.callbacks == [] and attachments.detached == 1
    second = NetworkOperation(3.0, attach=attachments.attach)
    with operation_context(second):
        assert pooled_socket.recv(1) == b"x"
        assert raw.timeouts[-1] == 2.0
        second.abort()
        second.abort()
        with pytest.raises(NetworkOperationError, match="^operation_cancelled$"):
            pooled_socket.recv(1)
    assert raw.shutdowns == 1
    second.close()
    assert attachments.callbacks == [] and attachments.detached == 2


def test_lease_abort_callback_can_shutdown_before_the_next_read(clock):
    raw = FakeSocket(clock)
    event, attachments = threading.Event(), Attachments()
    operation = NetworkOperation(5.0, event, attach=attachments.attach)
    with operation_context(operation):
        OperationSocket(raw).recv(1)
        event.set()
        attachments.callbacks[0]()
        with pytest.raises(NetworkOperationError, match="^operation_cancelled$"):
            operation.check()
    assert raw.shutdowns >= 1
    operation.close()
    assert attachments.callbacks == []


def test_queued_old_abort_cannot_shutdown_a_released_socket_reused_by_another_operation(clock):
    raw = FakeSocket(clock, receive_cost=.1)
    pooled_socket = OperationSocket(raw)
    attachments = Attachments()
    previous = NetworkOperation(5.0, attach=attachments.attach)
    with operation_context(previous):
        assert pooled_socket.recv(1) == b"x"
    # Simulate a lease having copied its callback just before response.close
    # returns this socket to the pool. Detaching must also invalidate that copy.
    queued_callback = attachments.callbacks[0]
    previous.release_socket(raw)
    assert attachments.callbacks == []
    assert not previous._closed
    current = NetworkOperation(5.0, attach=attachments.attach)
    with operation_context(current):
        assert pooled_socket.recv(1) == b"x"
        previous.abort()
        queued_callback()
        assert raw.shutdowns == 0
        assert pooled_socket.recv(1) == b"x"
        current.abort()
    assert raw.shutdowns == 1
    previous.close()
    current.close()
    assert attachments.callbacks == []


def test_failed_cancellation_registration_is_sanitized_and_shuts_the_socket(clock):
    raw = FakeSocket(clock)

    def failed(_callback):
        raise ValueError("private-secret callback internals")

    operation = NetworkOperation(1.0, attach=failed)
    with operation_context(operation):
        with pytest.raises(NetworkOperationError, match="^operation_registration_failed$"):
            OperationSocket(raw).recv(1)
    assert raw.received == [] and raw.shutdowns == 1
    operation.close()


class ReadHungrySSL:
    def __init__(self):
        self.reads = 0

    def do_handshake(self):
        pass

    def read(self, _size):
        self.reads += 1
        raise ssl.SSLWantReadError(ssl.SSL_ERROR_WANT_READ, "fixture want read")


class FakeSSLContext:
    def __init__(self):
        self.ssl_object = ReadHungrySSL()

    def wrap_bio(self, **_kwargs):
        return self.ssl_object


def test_nested_tls_body_inner_recv_cannot_reset_the_operation_deadline(clock, monkeypatch):
    raw = FakeSocket(clock)
    monkeypatch.setattr(transport, "_TLS_SOCKET_CLASS", FakeSocket)
    connection = transport._ConnectDeadlineStream(transport.SyncStream(raw), 10.0, "media.example")
    context = FakeSSLContext()
    operation = NetworkOperation(1.0)
    with operation_context(operation):
        connection.start_tls(context, "media.example")
        with pytest.raises(NetworkOperationError, match="^operation_timeout$"):
            connection.read(1, timeout=10.0)
    assert len(raw.received) == context.ssl_object.reads == 4
    assert raw.timeouts[-1] == pytest.approx(.1)
    assert raw.shutdowns == 1
    assert connection.get_extra_info("socket") is raw
    operation.close()
    connection.close()


def snapshot():
    return NetworkSnapshot(
        source_id=SOURCE_ID, purpose="media", selected_by="application", mode="direct", profile_id=None,
        application_revision=0, source_revision=0, profile_revision=0, connect_timeout_seconds=5.0,
        read_timeout_seconds=10.0, max_connections=2, proxy_url="", certificate_source="default",
        fingerprint="isolated", proxy_auth=None, ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        ca_file="", ca_dir="",
    )


def test_guarded_client_slow_headers_fail_with_fixed_error_and_close_the_socket(clock):
    raw = FakeSocket(clock, body=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")

    class Backend(httpcore.NetworkBackend):
        def connect_tcp(self, **_kwargs):
            return transport.SyncStream(raw)

    with create_client(snapshot(), targets=TargetPolicy(SOURCE_ID, ["8.8.8.8"])) as client:
        client._client._transport._pool._network_backend._backend = Backend()
        operation = NetworkOperation(1.0)
        with pytest.raises(NetworkRequestError, match="^operation_timeout$"):
            with client.guarded_stream("GET", "http://8.8.8.8/?signed=private-secret", operation):
                pytest.fail("slow headers must not become a returned response")
        assert guard.current_operation() is None
    assert len(raw.received) == 4
    assert raw.closed and raw.shutdowns == 1


def test_guarded_client_preserves_consumer_exceptions_and_detaches(clock, monkeypatch):
    import httpx

    @contextmanager
    def body(*_args, **_kwargs):
        yield httpx.Response(200, content=b"ok")

    with create_client(snapshot(), targets=TargetPolicy(SOURCE_ID, ["8.8.8.8"])) as client:
        monkeypatch.setattr(client, "stream", body)
        operation = NetworkOperation(1.0)
        with pytest.raises(ValueError, match="consumer error"):
            with client.guarded_stream("GET", "http://8.8.8.8/", operation):
                raise ValueError("consumer error")
        assert guard.current_operation() is None
        assert operation._closed


def test_guarded_dns_uses_remaining_deadline_and_keeps_the_fixed_worker_pool(monkeypatch):
    release = threading.Event()
    before = {thread.ident for thread in threading.enumerate() if thread.name.startswith("online-dns-")}

    def blocked(*_args):
        release.wait(timeout=1.0)
        return ("8.8.8.8",)

    policy = TargetPolicy(SOURCE_ID, ["cdn.example"], resolver=blocked, dns_timeout_seconds=5.0)
    operation = NetworkOperation(time.monotonic() + .05)
    started = time.monotonic()
    try:
        with operation_context(operation):
            with pytest.raises(NetworkOperationError, match="^operation_timeout$"):
                policy.resolve("https://cdn.example/")
    finally:
        release.set()
        operation.close()
    assert time.monotonic() - started < .5
    after = {thread.ident for thread in threading.enumerate() if thread.name.startswith("online-dns-")}
    assert len(after) <= 2 and before <= after
    assert targets._resolver_pool.pending.maxsize == 2


def test_guarded_dns_wait_is_cancelled_without_a_new_resolver_thread(monkeypatch):
    event, release, started = threading.Event(), threading.Event(), threading.Event()

    def blocked(*_args):
        started.set()
        release.wait(timeout=1.0)
        return ("8.8.8.8",)

    policy = TargetPolicy(SOURCE_ID, ["cancel.example"], resolver=blocked, dns_timeout_seconds=5.0)

    class CancellingEvent:
        def __init__(self):
            self.original = threading.Event()

        def wait(self, timeout):
            started.wait(timeout=.5)
            event.set()
            return self.original.wait(min(timeout, .001))

        def set(self):
            self.original.set()

    original_lookup = targets._Lookup

    def lookup(*args):
        return original_lookup(*args, ready=CancellingEvent())

    monkeypatch.setattr(targets, "_Lookup", lookup)
    operation = NetworkOperation(time.monotonic() + 5.0, event)
    before = time.monotonic()
    try:
        with operation_context(operation):
            with pytest.raises(NetworkOperationError, match="^operation_cancelled$"):
                policy.resolve("https://cancel.example/")
    finally:
        release.set()
        operation.close()
    assert time.monotonic() - before < .5
    assert len([thread for thread in threading.enumerate() if thread.name.startswith("online-dns-")]) <= 2
