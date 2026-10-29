"""API reuse validates fresh DNS/trust while preserving real socket ownership."""

from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import ssl
import time
from urllib.parse import urlsplit

import pytest

from app.network.client import NetworkRequestError, create_client as guarded_client
from app.network.guard import NetworkOperation, NetworkOperationError, operation_context
from app.network.headers import HeaderGrant, HeaderPolicy
from app.network.policy import resolve_online_policy
from test_network_transport import (LoopbackPolicy, bind, certificate as certificate,
                                    create_client, logical_url, origin as origin, profile,
                                    proxy_server, running_server, source as source)


def policy(source, answers=("127.0.0.1",), *, calls=None):
    def resolve(host, port):
        if calls is not None:
            calls.append((host, port))
        return answers

    return LoopbackPolicy(source, ["fixture.test", "next.test"], resolver=resolve)


def socket_of(response):
    return response.extensions["network_stream"].get_extra_info("socket")


@pytest.mark.parametrize("route", ["direct", "http_proxy", "https_proxy"])
def test_fresh_dns_and_trust_allow_the_same_tls_socket_for_a_new_operation(source, origin, certificate, route):
    with running_server(origin["handler"], certificate["context"]) as secure:
        with proxy_server(secure, "reuse-user", "reuse-password",
                          certificate["context"] if route == "https_proxy" else None) as proxy:
            proxy_url = logical_url(proxy["url"], "localhost") if route != "direct" else None
            configured = profile(proxy_url, ca_file=certificate["path"], max_connections=1,
                                 **({"username": "reuse-user", "password": "reuse-password"} if proxy_url else {}))
            bind(source, "api", configured)
            old_dns, new_dns, names = [], [], []
            certificate["context"].set_servername_callback(lambda _socket, name, _context: names.append(name))
            base = logical_url(secure, "fixture.test")
            first = NetworkOperation(time.monotonic() + 5)
            second = NetworkOperation(time.monotonic() + 5)
            with create_client(resolve_online_policy(source, "api"), targets=policy(source, calls=old_dns)) as old:
                with operation_context(first):
                    response = old.request("GET", base + "/first")
                    first_socket = socket_of(response)
                first.close()
                assert old.reusable_for(base)
                with create_client(resolve_online_policy(source, "api"), targets=policy(source, calls=new_dns)) as fresh:
                    with operation_context(second):
                        assert old.can_reuse(fresh, base)
                        assert not fresh.reusable_for(base), "DNS registration alone is not a completed API read"
                        response = old.request("GET", base + "/second")
                        assert socket_of(response) is first_socket
                second.close()
            assert old_dns == new_dns == [("fixture.test", urlsplit(secure).port)]
            assert names.count("fixture.test") == 1, "A second target TLS handshake cannot hide a missed reuse"
            assert names.count("localhost") == (1 if route == "https_proxy" else 0)
            assert len(proxy["records"]) == (0 if route == "direct" else 1)
            assert [row["path"] for row in origin["records"]] == ["/first", "/second"]
            assert all("Proxy-Authorization" not in row["headers"] for row in origin["records"])


@pytest.mark.parametrize("answers", [("10.0.0.1",), ("127.0.0.1", "169.254.169.254")])
def test_reuse_rejects_new_private_or_mixed_dns_answers_without_sending_http(source, origin, answers):
    base = logical_url(origin["url"], "fixture.test")
    with create_client(resolve_online_policy(source, "api"), targets=policy(source)) as old:
        assert old.request("GET", base + "/first").status_code == 200
        with create_client(resolve_online_policy(source, "api"), targets=policy(source, answers)) as fresh:
            with pytest.raises(NetworkRequestError) as rejected:
                old.can_reuse(fresh, base)
            assert rejected.value.category == "non_public_target_address"
            assert not fresh.targets.has_only_root_pin(base), "A mixed answer cannot partly register a valid prefix"
        assert old.reusable_for(base)
    assert [row["path"] for row in origin["records"]] == ["/first"]


def test_different_public_peer_set_does_not_repurpose_old_pins(source, origin):
    base = logical_url(origin["url"], "fixture.test")
    with create_client(resolve_online_policy(source, "api"), targets=policy(source)) as old:
        assert old.request("GET", base + "/first").status_code == 200
        with create_client(resolve_online_policy(source, "api"), targets=policy(source, ("8.8.8.8",))) as fresh:
            assert not old.can_reuse(fresh, base)
            assert fresh.targets.has_only_root_pin(base)
        # The failed comparison never cleared or changed the old connection's
        # peer authorization. This is intentionally not a new DNS lookup.
        assert old.targets.resolve(base).addresses == ("127.0.0.1",)
    assert len(origin["records"]) == 1


def test_root_only_client_cannot_be_reused_after_cross_origin_redirect_even_when_it_returns(source, origin):
    base = logical_url(origin["url"], "fixture.test")
    other = logical_url(origin["url"], "next.test")
    origin["routes"]["/away"] = (302, [("Location", other + "/return")])
    origin["routes"]["/return"] = (302, [("Location", base + "/back")])
    with create_client(resolve_online_policy(source, "api"), targets=policy(source)) as old:
        assert old.request("GET", base + "/away").url == base + "/back"
        assert not old.reusable_for(base)
        assert old.request("GET", base + "/later").status_code == 200
        with create_client(resolve_online_policy(source, "api"), targets=policy(source)) as fresh:
            assert not old.can_reuse(fresh, base)
    assert [row["path"] for row in origin["records"]] == ["/away", "/return", "/back", "/later"]


def test_relative_same_origin_redirect_still_qualifies_for_reuse(source, origin):
    base = logical_url(origin["url"], "fixture.test")
    with create_client(resolve_online_policy(source, "api"), targets=policy(source)) as old:
        assert old.request("GET", base + "/redirect").status_code == 200
        with create_client(resolve_online_policy(source, "api"), targets=policy(source)) as fresh:
            assert old.can_reuse(fresh, base)


@pytest.mark.parametrize("changed", ["ca_set", "verify_flags", "minimum_version", "ciphers", "ca_path"])
def test_changed_trust_rejects_reuse_even_when_the_policy_fingerprint_is_unchanged(source, origin, certificate, changed):
    base = logical_url(origin["url"], "fixture.test")
    previous = resolve_online_policy(source, "api")
    candidate = resolve_online_policy(source, "api")
    if changed == "ca_set":
        candidate.ssl_context.load_verify_locations(cafile=certificate["path"])
    elif changed == "verify_flags":
        candidate.ssl_context.verify_flags ^= ssl.VERIFY_X509_STRICT
    elif changed == "minimum_version":
        candidate.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
    elif changed == "ciphers":
        candidate.ssl_context.set_ciphers("ECDHE-RSA-AES256-GCM-SHA384")
    else:
        candidate = replace(candidate, ca_file="/different/internal-ca.pem")
    assert previous.fingerprint == candidate.fingerprint
    with create_client(previous, targets=policy(source)) as old:
        assert old.request("GET", base + "/first").status_code == 200
        with create_client(candidate, targets=policy(source)) as fresh:
            assert not old.can_reuse(fresh, base)
    assert len(origin["records"]) == 1


def test_closed_unused_media_and_credential_granted_clients_cannot_enter_api_idle_pool(source, origin):
    base = logical_url(origin["url"], "fixture.test")
    snapshot = resolve_online_policy(source, "api")
    with create_client(snapshot, targets=policy(source)) as unused:
        assert not unused.reusable_for(base)
        unused.request("GET", base + "/first")
        with ExitStack() as stack:
            media = stack.enter_context(create_client(resolve_online_policy(source, "media"), targets=policy(source)))
            granted = stack.enter_context(guarded_client(snapshot, targets=policy(source), header_policy=HeaderPolicy(source,
                [HeaderGrant(base, {"Authorization": "test-only-credential"})])))
            assert not unused.can_reuse(media, base)
            assert not unused.can_reuse(granted, base)
        unused.close()
        assert not unused.reusable_for(base)


@pytest.mark.parametrize("change", ["in_place", "delete", "symlink_target"])
def test_lazy_ca_directory_material_changes_prevent_reuse_without_loaded_ca_certificates(source, origin, certificate, tmp_path, change):
    directory = tmp_path / "lazy-ca"
    directory.mkdir()
    outside = tmp_path / "outside-ca.pem"
    outside.write_bytes(Path(certificate["path"]).read_bytes())
    child = directory / "fixture.0"
    if change == "symlink_target":
        child.symlink_to(outside)
    else:
        child.write_bytes(outside.read_bytes())
    bind(source, "api", profile(ca_dir=str(directory)))
    base = logical_url(origin["url"], "fixture.test")
    first = resolve_online_policy(source, "api")
    assert first.ssl_context.get_ca_certs(binary_form=True) == []
    with create_client(first, targets=policy(source)) as old:
        assert old.request("GET", base + "/before").status_code == 200
        if change == "delete":
            child.unlink()
        else:
            target = outside if change == "symlink_target" else child
            target.write_bytes(target.read_bytes() + b"\n")
        current = resolve_online_policy(source, "api")
        assert current.ssl_context.get_ca_certs(binary_form=True) == []
        assert first.fingerprint == current.fingerprint
        with create_client(current, targets=policy(source)) as fresh:
            assert not old.can_reuse(fresh, base)
    assert len(origin["records"]) == 1


def test_excessive_ca_directory_cannot_enter_idle_pool_but_normal_request_still_works(source, origin, tmp_path):
    directory = tmp_path / "large-ca"
    directory.mkdir()
    for index in range(513):
        (directory / str(index)).write_text("unused non-certificate fixture")
    bind(source, "api", profile(ca_dir=str(directory)))
    base = logical_url(origin["url"], "fixture.test")
    with create_client(resolve_online_policy(source, "api"), targets=policy(source)) as client:
        assert client.request("GET", base + "/independent").status_code == 200
        assert not client.reusable_for(base)


def test_reused_socket_ignores_a_previous_operation_cancel_but_current_cancel_still_interrupts(source, origin):
    configured = profile(max_connections=1, connect_timeout_seconds=2, read_timeout_seconds=2)
    bind(source, "api", configured)
    callbacks = []

    def attach(callback):
        callbacks.append(callback)
        return lambda: None

    previous = NetworkOperation(time.monotonic() + 5, attach=attach)
    current = NetworkOperation(time.monotonic() + 5)
    base = logical_url(origin["url"], "fixture.test")
    try:
        with create_client(resolve_online_policy(source, "api"), targets=policy(source)) as old:
            with operation_context(previous):
                first_socket = socket_of(old.request("GET", base + "/first"))
            assert callbacks
            with create_client(resolve_online_policy(source, "api"), targets=policy(source)) as fresh:
                with operation_context(current):
                    assert old.can_reuse(fresh, base)
                    with old.stream("GET", base + "/stream") as response:
                        assert socket_of(response) is first_socket
                        previous.abort()
                        for callback in callbacks:
                            callback()
                        body = response.iter_raw()
                        assert next(body) == b"0"
                        current.abort()
                        with pytest.raises(NetworkOperationError, match="^operation_cancelled$"):
                            next(body)
    finally:
        origin["release"].set()
        previous.close()
        current.close()
