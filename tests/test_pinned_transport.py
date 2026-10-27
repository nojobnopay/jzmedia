"""Pin enforcement and HTTPX integration with fake streams, no DNS or sockets."""

from dataclasses import replace
import ssl

import httpcore
import httpx
import pytest

from app.network import transport
from app.network.policy import NetworkSnapshot


SOURCE_ID = "src_" + "a" * 32
PUBLIC_IP = "8.8.8.8"
HTTP_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
CONNECT_RESPONSE = b"HTTP/1.1 200 Connection Established\r\n\r\n"


@pytest.fixture
def snapshot():
    return NetworkSnapshot(
        source_id=SOURCE_ID, purpose="api", selected_by="application", mode="direct",
        profile_id=None, application_revision=0, source_revision=0, profile_revision=0,
        connect_timeout_seconds=5.0, read_timeout_seconds=10.0, max_connections=4,
        proxy_url="", certificate_source="default", fingerprint="fake-policy",
        proxy_auth=None, ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT), ca_file="", ca_dir="",
    )


class Pins:
    source_id = SOURCE_ID

    def __init__(self, values=None):
        self.values = values if values is not None else {("media.example", 443): (PUBLIC_IP,)}

    def pin_for(self, host, port):
        try:
            return self.values[host, port]
        except KeyError:
            raise ValueError("not_registered") from None


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeStream(httpcore.NetworkStream):
    def __init__(self, peer=(PUBLIC_IP, 443), *, proxy_response=CONNECT_RESPONSE,
                 clock=None, read_cost=0.0, write_cost=0.0, tls_cost=0.0, read_chunk=4096):
        self.peer, self.proxy_response = peer, proxy_response
        self.clock, self.read_cost, self.write_cost, self.tls_cost = clock, read_cost, write_cost, tls_cost
        self.read_chunk = read_chunk
        self.pending, self.buffer = bytearray(), bytearray()
        self.messages, self.tls, self.read_timeouts, self.write_timeouts = [], [], [], []
        self.closed = False
        self.socket = None

    def read(self, max_bytes, timeout=None):
        self.read_timeouts.append(timeout)
        if self.clock is not None:
            self.clock.now += self.read_cost
        count = min(max_bytes, self.read_chunk, len(self.buffer))
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def write(self, buffer, timeout=None):
        self.write_timeouts.append(timeout)
        if self.clock is not None:
            self.clock.now += self.write_cost
        if not buffer:
            return
        self.pending.extend(buffer)
        if self.pending.endswith(b"\r\n\r\n"):
            message = bytes(self.pending)
            self.pending.clear()
            self.messages.append(message)
            self.buffer.extend(self.proxy_response if message.startswith(b"CONNECT ") else HTTP_RESPONSE)

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls.append((server_hostname, timeout, ssl_context))
        if self.clock is not None:
            self.clock.now += self.tls_cost
        return self

    def close(self):
        self.closed = True

    def get_extra_info(self, info):
        if info == "server_addr":
            return self.peer
        if info == "is_readable":
            return False
        if info == "socket":
            return self.socket
        return None


class FakeBackend(httpcore.NetworkBackend):
    def __init__(self, *results, clock=None, dial_costs=()):
        self.results, self.calls = list(results), []
        self.clock, self.dial_costs = clock, list(dial_costs)

    def connect_tcp(self, **kwargs):
        self.calls.append(kwargs)
        if self.clock is not None and self.dial_costs:
            self.clock.now += self.dial_costs.pop(0)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def proxy_snapshot(snapshot, *, scheme="http", auth=("proxy-user", "proxy-password")):
    return replace(snapshot, mode="proxy", proxy_url=f"{scheme}://127.0.0.1:8443", proxy_auth=auth)


def proxy_stream(**kwargs):
    return FakeStream(("127.0.0.1", 8443), **kwargs)


def test_original_host_sni_and_pool_origin_survive_numeric_dials(snapshot):
    streams = [FakeStream(), FakeStream()]
    backend = FakeBackend(*streams)
    pins = Pins({("a.example", 443): (PUBLIC_IP,), ("b.example", 443): (PUBLIC_IP,)})
    adapter = transport.PinnedTransport(snapshot, pins, network_backend=backend)
    assert type(adapter._pool) is httpcore.ConnectionPool
    assert adapter._pool._max_connections == snapshot.max_connections
    with httpx.Client(transport=adapter, trust_env=False) as client:
        assert client.get("https://a.example/one").content == b"ok"
        assert client.get("https://a.example/two").content == b"ok"
        assert client.get("https://b.example/three").content == b"ok"
        assert {item._origin.host for item in adapter._pool.connections} == {b"a.example", b"b.example"}
    assert [call["host"] for call in backend.calls] == [PUBLIC_IP, PUBLIC_IP]
    assert [len(item.messages) for item in streams] == [2, 1]
    for stream, host in zip(streams, ("a.example", "b.example")):
        assert stream.tls[0][0] == host
        assert stream.tls[0][2] is snapshot.ssl_context
        assert f"Host: {host}\r\n".encode() in stream.messages[0]
        assert stream.closed


@pytest.mark.parametrize("peer", [("1.1.1.1", 443), (PUBLIC_IP, 80), None,
                                  ("media.example", 443), ("fe80::1%eth0", 443), (PUBLIC_IP, "443")])
def test_unexpected_direct_peer_is_closed_without_address_or_route_fallback(snapshot, peer):
    stream = FakeStream(peer)
    backend = FakeBackend(stream, FakeStream())
    pins = Pins({("media.example", 443): (PUBLIC_IP, "1.1.1.1")})
    with pytest.raises(httpcore.ConnectError, match="network_connected_peer_mismatch"):
        transport.PinnedBackend(snapshot, pins, network_backend=backend).connect_tcp("media.example", 443)
    assert stream.closed
    assert len(backend.calls) == 1


def test_ipv6_pin_is_numeric_and_peer_comparison_is_canonical(snapshot):
    address = "2606:4700:4700::1111"
    stream = FakeStream(("2606:4700:4700:0:0:0:0:1111", 443, 0, 0))
    backend = FakeBackend(stream)
    transport.PinnedBackend(snapshot, Pins({("media.example", 443): (address,)}),
                            network_backend=backend).connect_tcp("media.example", 443)
    assert backend.calls[0]["host"] == address


@pytest.mark.parametrize("addresses", [(PUBLIC_IP, "media.example"), (PUBLIC_IP, "fe80::1%eth0"), ()])
def test_entire_pin_set_is_checked_before_any_dial(snapshot, addresses):
    backend = FakeBackend()
    with pytest.raises(httpcore.ConnectError):
        transport.PinnedBackend(snapshot, Pins({("media.example", 443): addresses}),
                                network_backend=backend).connect_tcp("media.example", 443)
    assert not backend.calls


def test_unregistered_target_cannot_dial(snapshot):
    backend = FakeBackend()
    with pytest.raises(httpcore.ConnectError, match="network_target_not_pinned"):
        transport.PinnedBackend(snapshot, Pins({}), network_backend=backend).connect_tcp("unknown.example", 443)
    assert not backend.calls


def test_numeric_address_retries_share_one_budget_and_preserve_target_tls_budget(snapshot, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(transport.time, "monotonic", clock)
    stream = FakeStream(("1.1.1.1", 443), clock=clock)
    backend = FakeBackend(httpcore.ConnectError("fixture failure"), stream, clock=clock, dial_costs=(2.0, 1.0))
    pins = Pins({("media.example", 443): (PUBLIC_IP, "1.1.1.1")})
    connection = transport.PinnedBackend(snapshot, pins, network_backend=backend).connect_tcp("media.example", 443)
    connection.start_tls(snapshot.ssl_context, server_hostname="media.example", timeout=5.0)
    assert [call["host"] for call in backend.calls] == [PUBLIC_IP, "1.1.1.1"]
    assert [call["timeout"] for call in backend.calls] == [5.0, 3.0]
    assert stream.tls[0][1] == 2.0


def test_expired_dial_budget_never_starts_another_address(snapshot, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(transport.time, "monotonic", clock)
    backend = FakeBackend(httpcore.ConnectTimeout("fixture timeout"), clock=clock, dial_costs=(1.1,))
    pins = Pins({("media.example", 443): (PUBLIC_IP, "1.1.1.1")})
    with pytest.raises(httpcore.ConnectTimeout, match="network_connect_timeout"):
        transport.PinnedBackend(snapshot, pins, network_backend=backend).connect_tcp("media.example", 443, timeout=1.0)
    assert len(backend.calls) == 1


def test_target_tls_cannot_outlive_the_shared_connect_deadline(snapshot, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(transport.time, "monotonic", clock)
    stream = FakeStream(clock=clock, tls_cost=2.0)
    backend = FakeBackend(stream, clock=clock, dial_costs=(.5,))
    connection = transport.PinnedBackend(snapshot, Pins(), network_backend=backend).connect_tcp(
        "media.example", 443, timeout=1.0)
    with pytest.raises(httpcore.ConnectTimeout, match="network_connect_timeout"):
        connection.start_tls(snapshot.ssl_context, "media.example", timeout=5.0)
    assert stream.tls[0][1] == .5
    assert stream.closed


class FakeTLSSocket:
    def __init__(self, clock, *, receive_cost=.3, receive_error=None):
        self.clock, self.receive_cost, self.receive_error = clock, receive_cost, receive_error
        self.timeouts, self.receives, self.writes = [], [], []
        self.closed = False

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def recv(self, max_bytes):
        self.receives.append(max_bytes)
        self.clock.now += self.receive_cost
        if self.receive_error is not None:
            raise self.receive_error
        return b"x"

    def sendall(self, data):
        self.writes.append(data)

    def getpeername(self):
        return "127.0.0.1", 8443

    def getsockname(self):
        return "127.0.0.1", 12345

    def close(self):
        self.closed = True


class FakeSSLObject:
    def __init__(self, handshake_reads):
        self.handshake_reads, self.handshakes, self.writes = handshake_reads, 0, []

    def do_handshake(self):
        self.handshakes += 1
        if self.handshakes <= self.handshake_reads:
            raise ssl.SSLWantReadError(ssl.SSL_ERROR_WANT_READ, "fixture want read")

    def read(self, max_bytes):
        return b"application data"[:max_bytes]

    def write(self, data):
        self.writes.append(data)
        return len(data)


class FakeSSLContext:
    def __init__(self, ssl_object):
        self.ssl_object, self.calls = ssl_object, []

    def wrap_bio(self, **kwargs):
        self.calls.append(kwargs)
        return self.ssl_object


def nested_tls_connection(monkeypatch, clock, raw_socket):
    monkeypatch.setattr(transport.time, "monotonic", clock)
    monkeypatch.setattr(transport, "_TLS_SOCKET_CLASS", FakeTLSSocket)
    return transport._ConnectDeadlineStream(transport.SyncStream(raw_socket), 1.0, "media.example")


def test_real_tls_in_tls_state_machine_cannot_be_kept_alive_by_slow_bytes(monkeypatch):
    clock = Clock()
    raw = FakeTLSSocket(clock)
    connection = nested_tls_connection(monkeypatch, clock, raw)
    context = FakeSSLContext(FakeSSLObject(handshake_reads=100))
    with pytest.raises(httpcore.ConnectTimeout, match="network_connect_timeout"):
        connection.start_tls(context, "media.example", timeout=5.0)
    assert len(raw.receives) == 4
    assert context.ssl_object.handshakes == 4
    assert raw.timeouts == sorted(raw.timeouts, reverse=True)
    assert raw.timeouts[-1] == pytest.approx(.1)
    assert context.calls[0]["server_hostname"] == "media.example"
    assert raw.closed


def test_tls_in_tls_deadline_is_removed_after_handshake_and_extra_info_is_preserved(monkeypatch):
    clock = Clock()
    raw = FakeTLSSocket(clock)
    connection = nested_tls_connection(monkeypatch, clock, raw)
    ssl_object = FakeSSLObject(handshake_reads=2)
    context = FakeSSLContext(ssl_object)
    connection.start_tls(context, "media.example", timeout=5.0)
    assert len(raw.receives) == 2
    assert ssl_object.handshakes == 3
    assert isinstance(context.calls[0]["incoming"], ssl.MemoryBIO)
    assert isinstance(context.calls[0]["outgoing"], ssl.MemoryBIO)
    assert connection.get_extra_info("socket") is raw
    assert connection.get_extra_info("server_addr") == ("127.0.0.1", 8443)
    assert connection.get_extra_info("client_addr") == ("127.0.0.1", 12345)
    assert connection.get_extra_info("ssl_object") is ssl_object
    clock.now = 100.0
    assert connection.read(3, timeout=7.0) == b"app"
    connection.write(b"video", timeout=9.0)
    assert ssl_object.writes == [b"video"]
    assert raw.timeouts[-1] == 9.0
    assert not raw.closed
    connection.close()
    assert raw.closed


@pytest.mark.parametrize("failure,expected", [(TimeoutError("fixture timeout"), httpcore.ConnectTimeout),
                                              (OSError("fixture error"), httpcore.ConnectError)])
def test_tls_in_tls_socket_failures_are_mapped_and_closed(monkeypatch, failure, expected):
    clock = Clock()
    raw = FakeTLSSocket(clock, receive_error=failure)
    connection = nested_tls_connection(monkeypatch, clock, raw)
    with pytest.raises(expected):
        connection.start_tls(FakeSSLContext(FakeSSLObject(handshake_reads=100)), "media.example")
    assert raw.closed


@pytest.mark.parametrize("proxy_scheme", ["http", "https"])
@pytest.mark.parametrize("target_scheme", ["http", "https"])
def test_both_target_schemes_use_numeric_connect_and_never_forward_proxy_auth(
        snapshot, proxy_scheme, target_scheme):
    port = 443 if target_scheme == "https" else 80
    stream = FakeStream(("127.0.0.1", 8443))
    backend = FakeBackend(stream)
    configured = proxy_snapshot(snapshot, scheme=proxy_scheme)
    pins = Pins({("media.example", port): (PUBLIC_IP,)})
    adapter = transport.PinnedTransport(configured, pins, network_backend=backend)
    with httpx.Client(transport=adapter, trust_env=False) as client:
        assert client.get(f"{target_scheme}://media.example/watch?q=one").content == b"ok"
    assert [(item["host"], item["port"]) for item in backend.calls] == [("127.0.0.1", 8443)]
    connect, origin = stream.messages
    assert connect.startswith(f"CONNECT {PUBLIC_IP}:{port} HTTP/1.1\r\n".encode())
    assert b"media.example" not in connect
    assert b"Proxy-Authorization: Basic cHJveHktdXNlcjpwcm94eS1wYXNzd29yZA==\r\n" in connect
    assert origin.startswith(b"GET /watch?q=one HTTP/1.1\r\n")
    assert b"Host: media.example\r\n" in origin
    assert b"proxy-authorization" not in origin.lower()
    expected_sni = (["127.0.0.1"] if proxy_scheme == "https" else [])
    expected_sni += ["media.example"] if target_scheme == "https" else []
    assert [call[0] for call in stream.tls] == expected_sni
    assert all(call[2] is snapshot.ssl_context for call in stream.tls)


def test_proxy_connect_brackets_numeric_ipv6_and_supports_no_auth(snapshot):
    address = "2606:4700:4700::1111"
    stream = proxy_stream()
    backend = FakeBackend(stream)
    configured = proxy_snapshot(snapshot, auth=None)
    pins = Pins({("media.example", 443): (address,)})
    transport.PinnedBackend(configured, pins, network_backend=backend).connect_tcp("media.example", 443)
    assert stream.messages[0].startswith(f"CONNECT [{address}]:443 HTTP/1.1\r\n".encode())
    assert b"Authorization" not in stream.messages[0]


def test_proxy_dns_is_bounded_then_only_numeric_proxy_peers_are_dialed(snapshot, monkeypatch):
    clock, lookups = Clock(), []
    monkeypatch.setattr(transport.time, "monotonic", clock)

    def resolve_proxy(host, port, timeout):
        lookups.append((host, port, timeout))
        clock.now += 1.0
        return ("10.0.0.2", "10.0.0.3")

    monkeypatch.setattr(transport, "resolve_operator_addresses", resolve_proxy)
    stream = FakeStream(("10.0.0.3", 8443), clock=clock)
    backend = FakeBackend(httpcore.ConnectError("fixture failure"), stream, clock=clock, dial_costs=(1.0, .5))
    configured = replace(proxy_snapshot(snapshot), proxy_url="https://proxy.internal.example:8443")
    transport.PinnedBackend(configured, Pins(), network_backend=backend).connect_tcp("media.example", 443)
    assert lookups == [("proxy.internal.example", 8443, 5.0)]
    assert [call["host"] for call in backend.calls] == ["10.0.0.2", "10.0.0.3"]
    assert [call["timeout"] for call in backend.calls] == [4.0, 3.0]
    assert stream.tls[0][:2] == ("proxy.internal.example", 2.5)
    assert stream.messages[0].startswith(f"CONNECT {PUBLIC_IP}:443 HTTP/1.1\r\n".encode())


@pytest.mark.parametrize("category,expected", [("target_dns_timeout", httpcore.ConnectTimeout),
                                               ("target_dns_failed", httpcore.ProxyError)])
def test_failed_proxy_resolution_never_dials_a_hostname_or_falls_back(snapshot, monkeypatch, category, expected):
    def fail_proxy_dns(*_args):
        raise transport.TargetPolicyError(category)

    monkeypatch.setattr(transport, "resolve_operator_addresses", fail_proxy_dns)
    backend = FakeBackend()
    configured = replace(proxy_snapshot(snapshot), proxy_url="http://proxy.internal.example:8443")
    with pytest.raises(expected):
        transport.PinnedBackend(configured, Pins(), network_backend=backend).connect_tcp("media.example", 443)
    assert not backend.calls


@pytest.mark.parametrize("response,category", [
    (b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n", "network_proxy_authentication_failed"),
    (b"HTTP/1.1 403 Forbidden\r\n\r\n", "network_proxy_connect_rejected"),
    (b"HTTP/1.1 302 Found\r\nLocation: http://elsewhere.example\r\n\r\n", "network_proxy_connect_rejected"),
    (b"HTTP/1.1 500 Failure\r\n\r\n", "network_proxy_connect_rejected"),
    (b"HTTP/1.1 200\r\n\r\n", "network_proxy_response_invalid"),
    (b"HTTP/1.1 200 OK\r\n folded: value\r\n\r\n", "network_proxy_response_invalid"),
    (b"HTTP/1.1 200 OK\r\nX: a\x00b\r\n\r\n", "network_proxy_response_invalid"),
    (b"HTTP/1.1 200 OK\r\nX: a\nb\r\n\r\n", "network_proxy_response_invalid"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n", "network_proxy_response_invalid"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n", "network_proxy_response_invalid"),
    (CONNECT_RESPONSE + b"unsolicited bytes", "network_proxy_response_invalid"),
    (b"", "network_proxy_response_invalid"),
])
def test_rejected_proxy_handshake_closes_without_fallback(snapshot, response, category):
    stream = proxy_stream(proxy_response=response)
    backend = FakeBackend(stream, FakeStream())
    pins = Pins({("media.example", 443): (PUBLIC_IP, "1.1.1.1")})
    with pytest.raises(httpcore.ProxyError, match=category):
        transport.PinnedBackend(proxy_snapshot(snapshot), pins, network_backend=backend).connect_tcp("media.example", 443)
    assert stream.closed
    assert len(backend.calls) == 1
    assert backend.calls[0]["host"] == "127.0.0.1"


def test_connect_response_header_limit_is_inclusive(snapshot):
    prefix, suffix = b"HTTP/1.1 200 OK\r\nX: ", b"\r\n\r\n"
    response = prefix + b"a" * (transport.CONNECT_HEADER_LIMIT - len(prefix) - len(suffix)) + suffix
    assert len(response) == transport.CONNECT_HEADER_LIMIT
    stream = proxy_stream(proxy_response=response)
    transport.PinnedBackend(proxy_snapshot(snapshot), Pins(), network_backend=FakeBackend(stream)).connect_tcp(
        "media.example", 443)
    assert not stream.closed


def test_oversized_connect_headers_close_the_tunnel(snapshot):
    stream = proxy_stream(proxy_response=b"HTTP/1.1 200 OK\r\nX: " + b"a" * transport.CONNECT_HEADER_LIMIT + b"\r\n\r\n")
    with pytest.raises(httpcore.ProxyError, match="network_proxy_headers_too_large"):
        transport.PinnedBackend(proxy_snapshot(snapshot), Pins(), network_backend=FakeBackend(stream)).connect_tcp(
            "media.example", 443)
    assert stream.closed


def test_slow_connect_headers_use_absolute_deadline_not_an_idle_timeout(snapshot, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(transport.time, "monotonic", clock)
    stream = proxy_stream(clock=clock, read_cost=.3, read_chunk=1)
    backend = FakeBackend(stream)
    with pytest.raises(httpcore.ConnectTimeout, match="network_connect_timeout"):
        transport.PinnedBackend(proxy_snapshot(snapshot), Pins(), network_backend=backend).connect_tcp(
            "media.example", 443, timeout=1.0)
    assert stream.closed
    assert len(stream.read_timeouts) == 4
    assert stream.read_timeouts == sorted(stream.read_timeouts, reverse=True)


class PartialSocket:
    def __init__(self, stream, clock, cost):
        self.stream, self.clock, self.cost = stream, clock, cost
        self.timeouts, self.sent = [], []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def send(self, buffer):
        self.clock.now += self.cost
        part = buffer[:4]
        self.sent.append(part)
        self.stream.write(part)
        return len(part)


def test_partial_connect_sends_receive_the_remaining_absolute_budget(snapshot, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(transport.time, "monotonic", clock)
    stream = proxy_stream(clock=clock)
    stream.socket = PartialSocket(stream, clock, .3)
    with pytest.raises(httpcore.ConnectTimeout, match="network_connect_timeout"):
        transport.PinnedBackend(proxy_snapshot(snapshot), Pins(), network_backend=FakeBackend(stream)).connect_tcp(
            "media.example", 443, timeout=1.0)
    assert len(stream.socket.sent) == 4
    assert stream.socket.timeouts == sorted(stream.socket.timeouts, reverse=True)
    assert not stream.read_timeouts
    assert stream.closed


@pytest.mark.parametrize("options", [{"extensions": {"sni_hostname": "other.example"}},
                                     {"headers": {"Proxy-Authorization": "private"}},
                                     {"headers": {"Proxy-Connection": "keep-alive"}}])
def test_transport_rejects_tls_override_and_proxy_headers_before_dial(snapshot, options):
    backend = FakeBackend()
    adapter = transport.PinnedTransport(snapshot, Pins(), network_backend=backend)
    with httpx.Client(transport=adapter, trust_env=False) as client:
        with pytest.raises(httpx.LocalProtocolError):
            client.get("https://media.example/", **options)
    assert not backend.calls


@pytest.mark.parametrize("headers", [{"Host": "other.example"},
                                      [("Host", "media.example"), ("Host", "other.example")]])
def test_transport_rejects_conflicting_or_duplicate_http_host(snapshot, headers):
    backend = FakeBackend()
    adapter = transport.PinnedTransport(snapshot, Pins(), network_backend=backend)
    with httpx.Client(transport=adapter, trust_env=False) as client:
        with pytest.raises(httpx.LocalProtocolError, match="network_http_host_mismatch"):
            client.get("https://media.example/", headers=headers)
    assert not backend.calls


def test_reused_connection_is_still_subject_to_registered_target_check(snapshot):
    pins, stream = Pins(), FakeStream()
    backend = FakeBackend(stream)
    adapter = transport.PinnedTransport(snapshot, pins, network_backend=backend)
    with httpx.Client(transport=adapter, trust_env=False) as client:
        assert client.get("https://media.example/one").status_code == 200
        pins.values.clear()
        with pytest.raises(httpx.ConnectError, match="network_target_not_pinned"):
            client.get("https://media.example/two")
    assert len(stream.messages) == 1
    assert len(backend.calls) == 1


def test_source_mismatch_or_unverified_tls_context_is_rejected(snapshot):
    pins = Pins()
    pins.source_id = "src_" + "b" * 32
    with pytest.raises(ValueError, match="invalid_pinned_transport_configuration"):
        transport.PinnedTransport(snapshot, pins)
    insecure = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError, match="invalid_pinned_transport_configuration"):
        transport.PinnedTransport(replace(snapshot, ssl_context=insecure), Pins())
