"""Pure URL/address/DNS authorization with injected resolvers, never public I/O."""

from dataclasses import FrozenInstanceError
import ipaddress
import socket
import threading
import time

import pytest

from app.network import targets
from app.network.targets import (TargetPolicy, TargetPolicyError, canonical_host,
                                 public_address, resolve_operator_addresses)


SOURCE_ID = "src_" + "1" * 32


def policy(*, hosts=("cdn.example",), addresses=("8.8.8.8",), resolver=None, **kwargs):
    return TargetPolicy(SOURCE_ID, hosts, resolver=resolver or (lambda _host, _port: addresses), **kwargs)


def test_canonical_idna_host_port_and_url_keep_query_out_of_repr():
    value = policy(hosts=("例子.example",)).resolve("HTTPS://例子.EXAMPLE.:443/a%20b?token=private-secret")
    assert value.url == "https://xn--fsqu00a.example/a%20b?token=private-secret"
    assert (value.scheme, value.host, value.port, value.authority) == (
        "https", "xn--fsqu00a.example", 443, "xn--fsqu00a.example")
    assert value.origin == "https://xn--fsqu00a.example:443"
    assert "private-secret" not in repr(value)
    with pytest.raises(FrozenInstanceError):
        value.host = "other.example"


def test_public_ipv6_literal_uses_brackets_and_bypasses_dns():
    def unexpected(*_args):
        raise AssertionError("literal addresses must not invoke DNS")

    value = policy(hosts=("2606:4700:4700::1111",), resolver=unexpected).resolve(
        "http://[2606:4700:4700:0:0:0:0:1111]:8080/file")
    assert value.host == "2606:4700:4700::1111"
    assert value.authority == "[2606:4700:4700::1111]:8080"
    assert value.origin == "http://[2606:4700:4700::1111]:8080"
    assert value.addresses == ("2606:4700:4700::1111",)


def test_first_approved_dns_pin_survives_rebinding_and_is_port_scoped():
    calls = []

    def rebinding(host, port):
        calls.append((host, port))
        return ("8.8.8.8", "1.1.1.1") if len(calls) == 1 else ("127.0.0.1",)

    value = policy(resolver=rebinding)
    first = value.resolve("https://cdn.example/a")
    assert value.resolve("https://cdn.example/b").addresses == first.addresses
    assert calls == [("cdn.example", 443)]
    assert value.pin_for(b"CDN.EXAMPLE", 443) == ("8.8.8.8", "1.1.1.1")
    with pytest.raises(TargetPolicyError, match="^non_public_target_address$"):
        value.resolve("https://cdn.example:8443/b")
    with pytest.raises(TargetPolicyError, match="^unregistered_target_connection$"):
        value.pin_for("cdn.example", 8443)


@pytest.mark.parametrize("address", [
    "0.0.0.0", "0.1.2.3", "10.0.0.1", "127.0.0.1", "169.254.169.254", "169.254.170.2",
    "172.16.0.1", "192.168.1.1", "100.64.0.1", "100.100.100.200", "192.0.0.9",
    "192.88.99.1", "192.0.2.1", "198.18.0.1", "198.51.100.1", "203.0.113.1",
    "224.0.0.1", "240.0.0.1", "255.255.255.255", "::", "::1", "fc00::1", "fe80::1",
    "ff02::1", "2001:db8::1", "::ffff:8.8.8.8", "::8.8.8.8", "64:ff9b::808:808",
    "64:ff9b:1::808:808", "2002:0808:0808::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2",
    "fe80::1%eth0", "not-an-address",
])
def test_private_special_transition_and_invalid_dns_answers_fail_closed(address):
    with pytest.raises(TargetPolicyError, match="^non_public_target_address$"):
        public_address(address)


@pytest.mark.parametrize("addresses", [
    ("8.8.8.8", "127.0.0.1"), ("127.0.0.1", "8.8.8.8"),
    ("2606:4700:4700::1111", "fc00::1"), ("8.8.8.8", "::ffff:127.0.0.1"),
])
def test_one_non_public_answer_rejects_entire_dns_set_without_registering_pin(addresses):
    value = policy(addresses=addresses)
    with pytest.raises(TargetPolicyError, match="^non_public_target_address$"):
        value.resolve("https://cdn.example/?signed=private-secret")
    with pytest.raises(TargetPolicyError, match="^unregistered_target_connection$"):
        value.pin_for("cdn.example", 443)


@pytest.mark.parametrize("url", [
    "ftp://cdn.example/a", "file:///etc/passwd", "https:cdn.example", "//cdn.example/a",
    "https://user@cdn.example/a", "https://user:private-secret@cdn.example/a",
    "https://cdn.example/a#part", "https://cdn.example/a#", "https://cdn.example/a b",
    " https://cdn.example/a", "https://cdn.example/a\n", "https://cdn.example\\evil/a",
    "https://cdn.example/a%0d%0aHost:evil", "https://cdn.example/a%00", "https://cdn.example/a%5cfoo",
    "https://%63dn.example/a", "https://cdn.example%2f.evil/a", "https://cdn.example:/a",
    "https://cdn.example:0/a", "https://cdn.example:65536/a", "https://cdn.example:abc/a",
    "https://[fe80::1%25eth0]/a", "https://[8.8.8.8]/a", "https://cdn.example../a",
    "https://127.1/a", "https://2130706433/a", "https://0177.0.0.1/a", "https://0x7f000001/a",
])
def test_unsafe_url_forms_never_reach_dns(url):
    def unexpected(*_args):
        raise AssertionError("invalid URLs must be rejected before DNS")

    with pytest.raises(TargetPolicyError) as caught:
        policy(resolver=unexpected).resolve(url)
    assert "private-secret" not in str(caught.value)


@pytest.mark.parametrize("host", ["*.example", "localhost", "service.localhost", "nas.local", "127.1",
                                  "2130706433", "0177.0.0.1", "0x7f.0.0.1", "bad_host.example"])
def test_allowlist_is_exact_canonical_host_syntax(host):
    with pytest.raises(TargetPolicyError, match="^invalid_target_host$"):
        canonical_host(host)


@pytest.mark.parametrize("url", ["https://cdn.example.evil/a", "https://other.example/a"])
def test_unlisted_hosts_cannot_trigger_dns(url):
    def unexpected(*_args):
        raise AssertionError("unlisted host must not trigger DNS")

    with pytest.raises(TargetPolicyError, match="^target_host_not_allowed$"):
        policy(resolver=unexpected).resolve(url)


def test_relative_targets_repeat_validation_and_pin_each_allowed_origin():
    value = policy(hosts=("cdn.example", "media.example"))
    base = value.resolve("https://cdn.example/path/manifest.m3u8")
    assert value.resolve("../segment.ts?part=1", base.url).url == "https://cdn.example/segment.ts?part=1"
    assert value.resolve("//media.example/segment.ts", base.url).origin == "https://media.example:443"
    with pytest.raises(TargetPolicyError, match="^target_host_not_allowed$"):
        value.resolve("//other.example/segment.ts", base.url)
    with pytest.raises(TargetPolicyError, match="^invalid_target_url$"):
        value.resolve("a" * (targets.URL_LIMIT + 1), base.url)


def test_url_length_is_bounded_and_unregistered_connections_are_rejected():
    value = policy()
    with pytest.raises(TargetPolicyError, match="^invalid_target_url$"):
        value.resolve("https://cdn.example/" + "a" * targets.URL_LIMIT)
    for host, port in (("cdn.example", 443), ("other.example", 443), ("bad%host", 443), ("cdn.example", True)):
        with pytest.raises(TargetPolicyError, match="^unregistered_target_connection$"):
            value.pin_for(host, port)


@pytest.mark.parametrize("answers", [(), [], None, ("8.8.8.8",) * 257, "8.8.8.8"])
def test_empty_malformed_and_excessive_raw_dns_answers_are_rejected(answers):
    with pytest.raises(TargetPolicyError, match="^invalid_target_dns_answers$"):
        policy(resolver=lambda *_args: answers).resolve("https://cdn.example/")


def test_address_set_is_deduplicated_but_distinct_addresses_are_capped():
    assert policy(addresses=("8.8.8.8",) * 256).resolve("https://cdn.example/").addresses == ("8.8.8.8",)
    addresses = tuple(str(ipaddress.ip_address("8.8.8.1") + index) for index in range(targets.ADDRESS_LIMIT + 1))
    with pytest.raises(TargetPolicyError, match="^too_many_target_addresses$"):
        policy(addresses=addresses).resolve("https://cdn.example/")


@pytest.mark.parametrize("count", [25, 32])
def test_large_public_cdn_pools_keep_every_validated_pin(count):
    addresses = tuple(str(ipaddress.ip_address("8.8.8.1") + index) for index in range(count))
    selected = policy(addresses=addresses)
    target = selected.resolve("https://cdn.example/movie.m3u8")
    assert target.addresses == addresses
    assert selected.pin_for("cdn.example", 443) == addresses


@pytest.mark.parametrize("private", ["10.0.0.1", "127.0.0.1", "169.254.169.254", "::1"])
def test_private_answer_after_sixteen_public_addresses_still_rejects_entire_pool(private):
    addresses = tuple(str(ipaddress.ip_address("8.8.8.1") + index) for index in range(24)) + (private,)
    selected = policy(addresses=addresses)
    with pytest.raises(TargetPolicyError, match="^non_public_target_address$"):
        selected.resolve("https://cdn.example/movie.m3u8")
    with pytest.raises(TargetPolicyError, match="^unregistered_target_connection$"):
        selected.pin_for("cdn.example", 443)


def test_dns_failure_never_echoes_resolver_message_or_signed_url():
    def failed(*_args):
        raise OSError("private-secret resolver address 192.168.1.2")

    with pytest.raises(TargetPolicyError, match="^target_dns_failed$") as caught:
        policy(resolver=failed).resolve("https://cdn.example/?key=private-secret")
    assert "private-secret" not in repr(caught.value)


def test_default_resolver_parameters_are_explicit_without_real_dns(monkeypatch):
    calls = []

    def records(host, port, **kwargs):
        calls.append((host, port, kwargs))
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port))]

    monkeypatch.setattr(targets.socket, "getaddrinfo", records)
    value = TargetPolicy(SOURCE_ID, ["cdn.example"]).resolve("https://cdn.example/a")
    assert value.addresses == ("8.8.8.8",)
    assert calls == [("cdn.example", 443, {"family": socket.AF_UNSPEC,
                                          "type": socket.SOCK_STREAM, "proto": socket.IPPROTO_TCP})]


def test_operator_proxy_resolution_shares_bounds_but_accepts_only_its_trusted_lan_endpoint(monkeypatch):
    calls = []

    def records(host, port, **_kwargs):
        calls.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]

    monkeypatch.setattr(targets.socket, "getaddrinfo", records)
    assert resolve_operator_addresses("localhost", 8080, 1) == ("127.0.0.1",)
    assert resolve_operator_addresses("192.168.1.2", 8080, 1) == ("192.168.1.2",)
    assert calls == [("localhost", 8080)]
    with pytest.raises(TargetPolicyError, match="^non_public_target_address$"):
        policy(addresses=("127.0.0.1",)).resolve("https://cdn.example/")


def test_slow_dns_times_out_without_spawning_a_worker_per_request():
    # Initialize the shared pool using an injected immediate resolver.
    policy().resolve("https://cdn.example/")
    workers = [thread for thread in threading.enumerate() if thread.name.startswith("online-dns-")]
    assert len(workers) == 2 and all(thread.daemon for thread in workers)
    release = threading.Event()

    def slow(*_args):
        release.wait(1)
        return ("8.8.8.8",)

    started = time.monotonic()
    try:
        for _ in range(5):
            with pytest.raises(TargetPolicyError, match="^target_dns_(timeout|busy)$"):
                policy(resolver=slow, dns_timeout_seconds=0.02).resolve("https://cdn.example/")
        assert time.monotonic() - started < 1
        assert [thread.ident for thread in threading.enumerate() if thread.name.startswith("online-dns-")] == [
            thread.ident for thread in workers]
        assert targets._resolver_pool.pending.maxsize == 2
    finally:
        release.set()
        targets._resolver_pool.pending.join()


def test_only_test_subclass_can_authorize_one_exact_loopback_address():
    class LoopbackFixturePolicy(TargetPolicy):
        def _validate_address(self, address):
            return "127.0.0.1" if address == "127.0.0.1" else super()._validate_address(address)

    local = LoopbackFixturePolicy(SOURCE_ID, ["127.0.0.1"])
    assert local.resolve("http://127.0.0.1:12345/").addresses == ("127.0.0.1",)
    with pytest.raises(TargetPolicyError, match="^non_public_target_address$"):
        TargetPolicy(SOURCE_ID, ["127.0.0.1"]).resolve("http://127.0.0.1:12345/")
