"""Pure network rules and client boundaries; real routing is tested separately."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
import logging
import ssl

import httpx
import pytest
from pydantic import ValidationError

from app.network import client as network_client
from app.network import policy
from app.network.targets import TargetPolicy
from app.network.transport import PinnedTransport
from app.network.models import (ApplicationNetworkPolicy, NetworkProfileCreate,
                                NetworkProfilePatch, NetworkProfilePublic,
                                NetworkRule, SourceNetworkPatch,
                                SourceNetworkPolicy)


SOURCE_ID = "src_" + "1" * 32
PROFILE_ID = "np_" + "2" * 32


def client_targets(source_id=SOURCE_ID):
    return TargetPolicy(source_id, ["example.invalid"], resolver=lambda _host, _port: ("8.8.8.8",))


@pytest.fixture(autouse=True)
def clean_certificate_env(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)


def state():
    return {"application": {"rule": {"mode": "direct"}, "revision": 0},
            "source": {"source_id": SOURCE_ID,
                       "policy": SourceNetworkPolicy().model_dump(mode="json"), "revision": 0},
            "profiles": {}}


def proxy_profile(**changes):
    return {"id": PROFILE_ID, "name": "test proxy", "mode": "proxy",
            "proxy_url": "http://127.0.0.1:12345", "connect_timeout_seconds": 5.0,
            "read_timeout_seconds": 15.0, "max_connections": 4, "ca_file": "", "ca_dir": "",
            "credentials_encrypted": "", "revision": 1, "created_at": 1, "updated_at": 1, **changes}


def proxy_state():
    value = state()
    value["application"]["rule"] = {"mode": "proxy", "profile_id": PROFILE_ID}
    value["profiles"][PROFILE_ID] = proxy_profile()
    return value


def test_missing_configuration_resolves_to_explicit_direct_even_with_proxy_env(monkeypatch):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(key, "http://private-secret@127.0.0.1:9")
    snapshot = policy.resolve_policy_state(state(), "api")
    assert (snapshot.mode, snapshot.selected_by, snapshot.proxy_url, snapshot.proxy_auth) == ("direct", "application", "", None)
    assert snapshot.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert snapshot.ssl_context.check_hostname is True


def test_only_inherit_falls_back_and_purpose_overrides_source_and_application():
    value = proxy_state()
    value["source"]["policy"]["default"] = {"mode": "direct"}
    assert policy.resolve_policy_state(value, "media").selected_by == "source"
    value["source"]["policy"]["api"] = {"mode": "proxy", "profile_id": PROFILE_ID}
    api = policy.resolve_policy_state(value, "api")
    media = policy.resolve_policy_state(value, "media")
    assert (api.mode, api.selected_by, api.profile_id) == ("proxy", "purpose", PROFILE_ID)
    assert (media.mode, media.selected_by, media.profile_id) == ("direct", "source", None)
    assert api.public_summary()["target_dns"] == "local_pinned"
    assert api.public_summary()["destination_validation"] == "required_per_request"


def test_explicit_direct_purpose_overrides_an_application_proxy():
    value = proxy_state()
    value["source"]["policy"]["media"] = {"mode": "direct"}
    assert policy.resolve_policy_state(value, "api").mode == "proxy"
    assert policy.resolve_policy_state(value, "media").mode == "direct"


@pytest.mark.parametrize("purpose", ["api", "media", "image", "subtitle", "subscription"])
def test_all_declared_purposes_are_independent_snapshot_inputs(purpose):
    assert policy.resolve_policy_state(state(), purpose).purpose == purpose


def test_missing_proxy_does_not_fall_back_to_direct():
    value = proxy_state()
    value["profiles"].clear()
    with pytest.raises(policy.NetworkPolicyError, match="network_profile_missing"):
        policy.resolve_policy_state(value, "media")


def test_mode_mismatch_does_not_fall_back():
    value = proxy_state()
    value["profiles"][PROFILE_ID] = proxy_profile(mode="direct", proxy_url="")
    with pytest.raises(policy.NetworkPolicyError, match="network_profile_mode_mismatch"):
        policy.resolve_policy_state(value, "api")


def test_direct_profile_can_supply_its_own_limits():
    value = state()
    value["source"]["policy"]["media"] = {"mode": "direct", "profile_id": PROFILE_ID}
    value["profiles"][PROFILE_ID] = proxy_profile(mode="direct", proxy_url="", max_connections=2,
                                                read_timeout_seconds=40.0)
    snapshot = policy.resolve_policy_state(value, "media")
    assert (snapshot.mode, snapshot.max_connections, snapshot.read_timeout_seconds) == ("direct", 2, 40.0)


def test_snapshot_is_immutable_and_configuration_updates_only_affect_new_snapshots(monkeypatch):
    value = proxy_state()
    value["profiles"][PROFILE_ID]["credentials_encrypted"] = "test-cipher"
    monkeypatch.setattr(policy.secrets, "decrypt_str", lambda _cipher: json.dumps({"username": "first-user", "password": "first-secret"}))
    first = policy.resolve_policy_state(value, "api")
    value["profiles"][PROFILE_ID]["proxy_url"] = "http://127.0.0.1:54321"
    value["profiles"][PROFILE_ID]["revision"] += 1
    monkeypatch.setattr(policy.secrets, "decrypt_str", lambda _cipher: json.dumps({"username": "next-user", "password": "next-secret"}))
    second = policy.resolve_policy_state(value, "api")
    assert first.proxy_auth == ("first-user", "first-secret")
    assert second.proxy_auth == ("next-user", "next-secret")
    assert first.proxy_url.endswith(":12345")
    assert first.fingerprint != second.fingerprint
    with pytest.raises(FrozenInstanceError):
        first.mode = "direct"
    assert "first-user" not in repr(first) and "first-secret" not in repr(first)
    assert "test-cipher" not in json.dumps(first.public_summary())


@pytest.mark.parametrize("broken", ["not-json", "[]", '{"username":"secret"}',
                                    '{"username":1,"password":"secret"}'])
def test_invalid_decrypted_credentials_fail_closed_without_echo(monkeypatch, broken):
    value = proxy_state()
    value["profiles"][PROFILE_ID]["credentials_encrypted"] = "test-cipher"
    monkeypatch.setattr(policy.secrets, "decrypt_str", lambda _cipher: broken)
    with pytest.raises(policy.NetworkPolicyError) as caught:
        policy.resolve_policy_state(value, "api")
    assert str(caught.value) == "invalid_network_credentials"
    assert "secret" not in str(caught.value)


def test_failed_credential_decryption_never_echoes_library_exception(monkeypatch):
    value = proxy_state()
    value["profiles"][PROFILE_ID]["credentials_encrypted"] = "test-cipher"

    def failed(_cipher):
        raise policy.secrets.SecretError("private-secret in private filesystem path")

    monkeypatch.setattr(policy.secrets, "decrypt_str", failed)
    with pytest.raises(policy.NetworkPolicyError, match="^invalid_network_credentials$"):
        policy.resolve_policy_state(value, "api")


def test_certificate_environment_is_captured_without_inheriting_proxy_env(monkeypatch):
    captured = []
    actual = ssl.create_default_context

    def context(**kwargs):
        captured.append(kwargs)
        return actual()

    monkeypatch.setattr(policy.ssl, "create_default_context", context)
    monkeypatch.setenv("SSL_CERT_FILE", "/isolated/environment-ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/isolated/environment-ca-dir")
    snapshot = policy.resolve_policy_state(state(), "api")
    assert captured == [{"cafile": "/isolated/environment-ca.pem", "capath": "/isolated/environment-ca-dir"}]
    monkeypatch.setenv("SSL_CERT_FILE", "/different-ca.pem")
    assert snapshot.ca_file == "/isolated/environment-ca.pem"
    assert snapshot.certificate_source == "environment"


def test_profile_certificate_path_overrides_corresponding_environment_path(monkeypatch):
    captured = []
    actual = ssl.create_default_context
    monkeypatch.setattr(policy.ssl, "create_default_context", lambda **kwargs: (captured.append(kwargs), actual())[1])
    monkeypatch.setenv("SSL_CERT_FILE", "/environment-ca.pem")
    value = proxy_state()
    value["profiles"][PROFILE_ID]["ca_file"] = "/profile-ca.pem"
    snapshot = policy.resolve_policy_state(value, "api")
    assert captured == [{"cafile": "/profile-ca.pem", "capath": None}]
    assert snapshot.certificate_source == "profile"


def test_invalid_ca_path_fails_without_path_or_secret_in_error(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/missing/private-secret.pem")
    with pytest.raises(policy.NetworkPolicyError) as caught:
        policy.resolve_policy_state(state(), "api")
    assert str(caught.value) == "invalid_ca_configuration"


@pytest.mark.parametrize("changes", [
    {"mode": "inherit", "profile_id": PROFILE_ID}, {"mode": "proxy"},
    {"mode": "direct", "profile_id": "bad"}, {"mode": "proxy", "fallback": "direct"},
])
def test_rule_contract_rejects_ambiguous_routing(changes):
    with pytest.raises(ValidationError):
        NetworkRule.model_validate(changes)


def test_application_must_resolve_and_partial_source_rules_reject_null():
    with pytest.raises(ValidationError):
        ApplicationNetworkPolicy.model_validate({"rule": {"mode": "inherit"}})
    with pytest.raises(ValidationError):
        SourceNetworkPatch.model_validate({"media": None})
    assert SourceNetworkPatch.model_validate({}).model_dump(exclude_unset=True) == {}


@pytest.mark.parametrize("address", ["http://user:private-secret@127.0.0.1:8080", "http://127.0.0.1/p",
                                      "http://127.0.0.1?token=private-secret", "socks5://127.0.0.1:9",
                                      "http://127.0.0.1:0", "http://127.0.0.1/#x"])
def test_proxy_address_cannot_hide_credentials_or_non_endpoint_data(address):
    with pytest.raises(ValidationError):
        NetworkProfileCreate(name="test", mode="proxy", proxy_url=address)


def test_profile_credentials_are_write_only_and_direct_profiles_reject_them():
    profile = NetworkProfileCreate(name="test", mode="proxy", proxy_url="http://127.0.0.1:9",
                                   username="private-user", password="private-secret")
    assert "private-user" not in repr(profile) and "private-secret" not in repr(profile)
    assert "username" not in NetworkProfilePublic.model_fields
    with pytest.raises(ValidationError):
        NetworkProfileCreate(name="test", mode="direct", password="private-secret")
    with pytest.raises(ValidationError):
        NetworkProfilePatch.model_validate({"clear_credentials": True, "password": ""})
    assert NetworkProfilePatch(password="").password.get_secret_value() == ""


@pytest.mark.parametrize("changed", [{"max_connections": 0}, {"max_connections": 17},
                                      {"read_timeout_seconds": float("inf")}, {"ca_file": "relative.pem"}])
def test_profile_limits_and_certificate_references_are_bounded(changed):
    with pytest.raises(ValidationError):
        NetworkProfileCreate(name="test", mode="direct", **changed)


class FakeClient:
    def __init__(self, **options):
        self.options = options
        self.closed = False

    def request(self, method, url, **kwargs):
        logging.getLogger("httpx").info("HTTP %s %s", method, url)
        logging.getLogger("httpcore.http11").debug("header Proxy-Authorization=private-secret")
        return httpx.Response(200, content=b"ok", request=httpx.Request(method, url))

    @contextmanager
    def stream(self, method, url, **kwargs):
        response = self.request(method, url, **kwargs)
        streamed = httpx.Response(response.status_code, stream=httpx.ByteStream(response.content),
                                  request=response.request)
        try:
            yield streamed
            logging.getLogger("httpcore.connection").debug("close header private-secret")
        finally:
            streamed.close()

    def close(self):
        self.closed = True
        self.options["transport"].close()


def test_client_factory_uses_fixed_route_limits_and_tls_without_environment(monkeypatch):
    monkeypatch.setattr(network_client.httpx, "Client", FakeClient)
    snapshot = policy.resolve_policy_state(state(), "media")
    with network_client.create_client(snapshot, targets=client_targets()) as client:
        options = client._client.options
        assert options["trust_env"] is False and options["follow_redirects"] is False
        assert "proxy" not in options and "verify" not in options
        assert isinstance(options["transport"], PinnedTransport)
        assert options["transport"]._pool._ssl_context is snapshot.ssl_context
        assert options["limits"].max_connections == 4
        assert options["timeout"].read == 15
    assert client._client.closed is True


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_http_and_https_proxy_snapshots_use_a_pin_enforcing_transport(monkeypatch, scheme):
    monkeypatch.setattr(network_client.httpx, "Client", FakeClient)

    def no_implicit_proxy(*_args, **_kwargs):
        pytest.fail("proxy routing must remain inside the pinned transport")

    monkeypatch.setattr(network_client.httpx, "Proxy", no_implicit_proxy)
    value = proxy_state()
    value["profiles"][PROFILE_ID]["proxy_url"] = f"{scheme}://127.0.0.1:9"
    snapshot = policy.resolve_policy_state(value, "api")
    with network_client.create_client(snapshot, targets=client_targets()) as client:
        transport = client._client.options["transport"]
        assert isinstance(transport, PinnedTransport)
        assert transport._pool._network_backend._snapshot is snapshot
        assert transport._pool._network_backend._proxy.scheme == scheme
        assert transport._pool._ssl_context is snapshot.ssl_context


def test_request_and_stream_logs_are_private_without_suppressing_other_operations(monkeypatch, caplog):
    monkeypatch.setattr(network_client.httpx, "Client", FakeClient)
    caplog.set_level(logging.DEBUG)
    for logger_name in network_client._THIRD_PARTY_LOGGERS:
        caplog.set_level(logging.DEBUG, logger=logger_name)
    snapshot = policy.resolve_policy_state(state(), "media")
    with network_client.create_client(snapshot, targets=client_targets()) as client:
        client.request("GET", "https://example.invalid/a?token=private-secret")
        with client.stream("GET", "https://example.invalid/b?token=private-secret") as response:
            assert response.read() == b"ok"
            logging.getLogger("httpcore.proxy").debug("in-stream private-secret")
    assert "private-secret" not in caplog.text
    logging.getLogger("httpx").info("ordinary service log remains")
    assert "ordinary service log remains" in caplog.text


def test_proxy_failure_is_sanitized_and_never_retried_or_switched(monkeypatch, caplog):
    calls = []

    class FailedClient(FakeClient):
        def request(self, method, url, **kwargs):
            calls.append(url)
            raise httpx.ProxyError("private-secret from proxy response")

    monkeypatch.setattr(network_client.httpx, "Client", FailedClient)
    snapshot = policy.resolve_policy_state(proxy_state(), "api")
    with network_client.create_client(snapshot, targets=client_targets()) as client:
        with pytest.raises(network_client.NetworkRequestError, match="^proxy_connection_failed$"):
            client.request("GET", "https://example.invalid/?token=private-secret")
    assert len(calls) == 1
    assert "private-secret" not in caplog.text


def test_non_streaming_body_budget_stops_and_closes_upstream(monkeypatch):
    class CountedBody(httpx.SyncByteStream):
        def __init__(self):
            self.read_bytes = 0
            self.closed = False

        def __iter__(self):
            for _index in range(40):
                chunk = b"x" * network_client.RESPONSE_CHUNK_BYTES
                self.read_bytes += len(chunk)
                yield chunk

        def close(self):
            self.closed = True

    body = CountedBody()

    class LargeClient(FakeClient):
        @contextmanager
        def stream(self, method, url, **kwargs):
            response = httpx.Response(200, stream=body, request=httpx.Request(method, url))
            try:
                yield response
            finally:
                response.close()

    monkeypatch.setattr(network_client.httpx, "Client", LargeClient)
    with network_client.create_client(policy.resolve_policy_state(state(), "api"), targets=client_targets()) as client:
        with pytest.raises(network_client.NetworkRequestError, match="^response_body_limit$"):
            client.request("GET", "https://example.invalid/data")
    assert body.closed is True
    assert body.read_bytes <= network_client.RESPONSE_BODY_LIMIT + network_client.RESPONSE_CHUNK_BYTES


def test_stream_sanitizes_opening_errors_but_keeps_consumer_exceptions(monkeypatch):
    class InvalidClient(FakeClient):
        @contextmanager
        def stream(self, *_args, **_kwargs):
            raise ValueError("private-secret in invalid request")
            yield  # pragma: no cover

    snapshot = policy.resolve_policy_state(state(), "api")
    monkeypatch.setattr(network_client.httpx, "Client", InvalidClient)
    with network_client.create_client(snapshot, targets=client_targets()) as client:
        with pytest.raises(network_client.NetworkRequestError, match="^network_request_failed$"):
            with client.stream("GET", "https://example.invalid"):
                pass
        with pytest.raises(network_client.NetworkRequestError, match="^invalid_target_url$"):
            with client.stream("GET", object()):
                pass
    monkeypatch.setattr(network_client.httpx, "Client", FakeClient)
    with network_client.create_client(snapshot, targets=client_targets()) as client:
        with pytest.raises(ValueError, match="consumer error"):
            with client.stream("GET", "https://example.invalid"):
                raise ValueError("consumer error")


@pytest.mark.parametrize("options", [{"timeout": 999}, {"follow_redirects": True},
                                      {"extensions": {"timeout": None}},
                                      {"headers": {"Proxy-Authorization": "private-secret"}},
                                      {"headers": {"Proxy-Connection": "keep-alive"}}])
def test_callers_cannot_override_snapshot_controls_or_proxy_headers(monkeypatch, options):
    monkeypatch.setattr(network_client.httpx, "Client", FakeClient)
    with network_client.create_client(policy.resolve_policy_state(state(), "api"), targets=client_targets()) as client:
        with pytest.raises(network_client.NetworkRequestError):
            client.request("GET", "https://example.invalid", **options)


def test_invalid_query_values_are_sanitized_before_opening_a_request(monkeypatch):
    class InvalidValue:
        def __str__(self):
            raise ValueError("private-secret query value")

    class UnusedClient(FakeClient):
        def request(self, *_args, **_kwargs):
            pytest.fail("invalid parameters must not reach the HTTP transport")

    monkeypatch.setattr(network_client.httpx, "Client", UnusedClient)
    with network_client.create_client(policy.resolve_policy_state(state(), "api"), targets=client_targets()) as client:
        with pytest.raises(network_client.NetworkRequestError, match="^invalid_request_params$"):
            client.request("GET", "https://example.invalid", params={"key": InvalidValue()})


def test_unsafe_manually_constructed_snapshot_is_rejected(monkeypatch):
    monkeypatch.setattr(network_client.httpx, "Client", FakeClient)
    snapshot = policy.resolve_policy_state(state(), "api")
    with pytest.raises(network_client.NetworkRequestError):
        network_client.create_client(replace(snapshot, mode="unknown"), targets=client_targets())
    snapshot.ssl_context.check_hostname = False
    with pytest.raises(network_client.NetworkRequestError):
        network_client.create_client(snapshot, targets=client_targets())


@pytest.mark.parametrize("invalid", [None, [], {"application": {}, "source": {}, "profiles": []}])
def test_malformed_loaded_configuration_fails_with_fixed_category(invalid):
    with pytest.raises(policy.NetworkPolicyError, match="^invalid_network_configuration$"):
        policy.resolve_policy_state(deepcopy(invalid), "api")
