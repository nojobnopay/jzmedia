"""Real image keep-alive reuse preserves DNS, TLS and socket cancellation."""

from dataclasses import replace
import ssl
import time
from types import SimpleNamespace

import httpx
import pytest

from app.network.client import NetworkRequestError
from app.network.guard import NetworkOperation, NetworkOperationError, operation_context
from app.network.policy import resolve_online_policy
from app.online import image_pool
from test_network_client_reuse import policy, socket_of
from test_network_transport import (bind, certificate as certificate, create_client,
                                    logical_url, origin as origin, profile, proxy_server,
                                    running_server, source as source)


ACCESS = SimpleNamespace(owner="isolated-image-device", generation=1, protection_revision=1)
CONFIGURATION = "a" * 64


@pytest.fixture(autouse=True)
def clean_pool():
    image_pool.start()
    yield
    image_pool.shutdown()
    assert image_pool.pool.stats() == {"idle": 0, "active": 0}


def source_record(source):
    return {"id": source, "allowed_hosts": ["fixture.test", "next.test"]}


def read(source, url, factory, *, check=lambda: None):
    with image_pool.borrow(ACCESS, source_record(source), CONFIGURATION, url, check, factory) as lease:
        with lease.client.stream("GET", url) as response:
            body = b"".join(response.iter_raw())
            sock = socket_of(response)
        lease.accept(response)
        return lease.client, sock, body


@pytest.mark.parametrize("route", ["direct", "http_proxy", "https_proxy"])
def test_images_share_one_actual_tls_socket_after_fresh_dns_and_trust_checks(source, origin, certificate, route):
    with running_server(origin["handler"], certificate["context"]) as secure:
        with proxy_server(secure, "image-user", "image-password",
                          certificate["context"] if route == "https_proxy" else None) as proxy:
            proxy_url = logical_url(proxy["url"], "localhost") if route != "direct" else None
            configured = profile(proxy_url, ca_file=certificate["path"], max_connections=1,
                                 **({"username": "image-user", "password": "image-password"} if proxy_url else {}))
            bind(source, "image", configured)
            candidates, dns, names = [], [], []
            certificate["context"].set_servername_callback(lambda _socket, name, _context: names.append(name))

            def factory(source_id, purpose):
                assert source_id == source and purpose == "image"
                calls = []
                candidate = create_client(resolve_online_policy(source, purpose), targets=policy(source, calls=calls))
                candidate._online_health_context = object()
                candidates.append(candidate)
                dns.append(calls)
                return candidate

            base = logical_url(secure, "fixture.test")
            first, second = NetworkOperation(time.monotonic() + 5), NetworkOperation(time.monotonic() + 5)
            try:
                with operation_context(first):
                    old, first_socket, _body = read(source, base + "/poster-one", factory)
                first.close()
                with operation_context(second):
                    current, second_socket, _body = read(source, base + "/poster-two", factory)
                assert current is old and second_socket is first_socket
                assert current._online_health_context is candidates[-1]._online_health_context
                assert len(candidates) == 2 and all(len(calls) == 1 for calls in dns)
                assert names.count("fixture.test") == 1
                assert names.count("localhost") == (1 if route == "https_proxy" else 0)
                assert len(proxy["records"]) == (0 if route == "direct" else 1)
                assert [row["path"] for row in origin["records"]] == ["/poster-one", "/poster-two"]
                assert all("Proxy-Authorization" not in row["headers"] for row in origin["records"])
                assert image_pool.pool.stats() == {"idle": 1, "active": 0}
            finally:
                first.close()
                second.close()


@pytest.mark.parametrize("answers", [("10.0.0.1",), ("127.0.0.1", "169.254.169.254")])
def test_image_reuse_rejects_new_private_or_mixed_dns_before_another_http_request(source, origin, answers):
    base = logical_url(origin["url"], "fixture.test")
    old, _socket, _body = read(source, base + "/poster", lambda *_args: create_client(
        resolve_online_policy(source, "image"), targets=policy(source)))
    with pytest.raises(NetworkRequestError) as rejected:
        read(source, base + "/another", lambda *_args: create_client(
            resolve_online_policy(source, "image"), targets=policy(source, answers)))
    assert rejected.value.category == "non_public_target_address"
    assert not old.reusable_for(base)
    assert len(origin["records"]) == 1 and image_pool.pool.stats() == {"idle": 0, "active": 0}


@pytest.mark.parametrize("changed", ["peer_set", "ca_set", "verify_flags", "minimum_version", "ciphers", "ca_path"])
def test_changed_dns_or_tls_never_repurposes_an_image_connection(source, origin, certificate, changed):
    base = logical_url(origin["url"], "fixture.test")
    old, _socket, _body = read(source, base + "/poster", lambda *_args: create_client(
        resolve_online_policy(source, "image"), targets=policy(source)))
    fresh = resolve_online_policy(source, "image")
    if changed == "ca_set":
        fresh.ssl_context.load_verify_locations(cafile=certificate["path"])
    elif changed == "verify_flags":
        fresh.ssl_context.verify_flags ^= ssl.VERIFY_X509_STRICT
    elif changed == "minimum_version":
        fresh.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
    elif changed == "ciphers":
        fresh.ssl_context.set_ciphers("ECDHE-RSA-AES256-GCM-SHA384")
    elif changed == "ca_path":
        fresh = replace(fresh, ca_file="/different/internal-ca.pem")
    candidate = create_client(fresh, targets=policy(source, ("8.8.8.8",) if changed == "peer_set" else ("127.0.0.1",)))
    with image_pool.borrow(ACCESS, source_record(source), CONFIGURATION, base, lambda: None, lambda *_args: candidate) as lease:
        assert lease.client is candidate and lease.client is not old
        # In particular the changed public DNS answer is never contacted.
    assert len(origin["records"]) == 1
    assert image_pool.pool.stats() == {"idle": 0, "active": 0}


def test_image_stream_must_reach_eof_and_close_before_explicit_acceptance(source, origin):
    base = logical_url(origin["url"], "fixture.test")
    client = create_client(resolve_online_policy(source, "image"), targets=policy(source))
    try:
        with client.stream("GET", base + "/stream") as response:
            iterator = response.iter_raw(chunk_size=1)
            assert next(iterator) == b"0"
            assert response.is_stream_consumed
        assert response.is_closed
        assert not client.accept_image_response(response), "Starting an iterator is not a complete body read"
        assert not client.reusable_for(base)
        with client.stream("GET", base + "/complete") as completed:
            assert b"".join(completed.iter_raw())
            assert not client.reusable_for(base), "Still-active response contexts never qualify"
        assert client.accept_image_response(completed)
        assert client.reusable_for(base)
    finally:
        origin["release"].set()
        client.close()


def test_other_responses_and_unvalidated_generic_image_requests_cannot_qualify(source, origin):
    base = logical_url(origin["url"], "fixture.test")
    with create_client(resolve_online_policy(source, "image"), targets=policy(source)) as client:
        copied = client.request("GET", base + "/request-without-image-validation")
        assert not client.reusable_for(base)
        assert not client.accept_image_response(copied), "A copied request() response is not its underlying stream"
        assert not client.accept_image_response(httpx.Response(200, content=b"foreign"))
    with create_client(resolve_online_policy(source, "media"), targets=policy(source)) as media:
        with media.stream("GET", base + "/media") as response:
            b"".join(response.iter_raw())
        assert not media.accept_image_response(response)
        assert not media.reusable_for(base)


@pytest.mark.parametrize("failure", ["status", "validation", "cancelled"])
def test_failed_image_body_or_final_guard_discards_a_real_socket(source, origin, failure):
    base = logical_url(origin["url"], "fixture.test")
    if failure == "status":
        origin["routes"]["/failed"] = (503, [])
    operation = NetworkOperation(time.monotonic() + 5)
    try:
        try:
            with operation_context(operation), image_pool.borrow(ACCESS, source_record(source), CONFIGURATION,
                    base + "/failed", lambda: None, lambda *_args: create_client(
                        resolve_online_policy(source, "image"), targets=policy(source))) as lease:
                with lease.client.stream("GET", base + "/failed") as response:
                    b"".join(response.iter_raw())
                if failure == "validation":
                    raise ValueError("isolated unsupported image bytes")
                if failure == "cancelled":
                    operation.abort()
                lease.accept(response)
        except ValueError:
            assert failure == "validation"
        except NetworkOperationError:
            assert failure == "cancelled"
        assert image_pool.pool.stats() == {"idle": 0, "active": 0}
        assert not lease.client.reusable_for(base)
        assert len(origin["records"]) == 1
    finally:
        operation.close()


def test_old_image_operation_callbacks_cannot_cancel_a_socket_borrowed_by_new_work(source, origin):
    bind(source, "image", profile(max_connections=1, connect_timeout_seconds=2, read_timeout_seconds=2))
    base = logical_url(origin["url"], "fixture.test")
    callbacks = []

    def attach(callback):
        callbacks.append(callback)
        return lambda: None

    factory = lambda *_args: create_client(resolve_online_policy(source, "image"), targets=policy(source))
    previous = NetworkOperation(time.monotonic() + 5, attach=attach)
    current = NetworkOperation(time.monotonic() + 5)
    try:
        with operation_context(previous):
            old, old_socket, _body = read(source, base + "/first", factory)
        assert callbacks
        with operation_context(current), pytest.raises(NetworkOperationError, match="^operation_cancelled$"):
            with image_pool.borrow(ACCESS, source_record(source), CONFIGURATION, base + "/stream",
                    lambda: None, factory) as lease:
                assert lease.client is old
                with lease.client.stream("GET", base + "/stream") as response:
                    assert socket_of(response) is old_socket
                    previous.abort()
                    for callback in callbacks:
                        callback()
                    body = response.iter_raw(chunk_size=1)
                    assert next(body) == b"0"
                    current.abort()
                    next(body)
        assert image_pool.pool.stats() == {"idle": 0, "active": 0}
    finally:
        origin["release"].set()
        previous.close()
        current.close()
