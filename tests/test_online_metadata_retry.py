"""Foreground catalogue retries never escape their request or safety scope."""

import json
import time

import httpx
import pytest

from app import store, viewing
from app.network.client import NetworkRequestError
from app.network.guard import NetworkOperation
from app.online import catalog, maccms
from app.online.providers import ProviderError
from app.store import online, online_network
from test_online_catalog import environment as environment
from test_online_detail_retry import access, readers


def payload(state, *, categories=True, page=1):
    value = {"code": 1, "page": page, "pagecount": page, "total": 1, "list": [state.movie]}
    if categories:
        value["class"] = [{"type_id": "10", "type_name": "电影", "type_pid": "0"}]
    return httpx.Response(200, content=json.dumps(value).encode(),
                          request=httpx.Request("GET", "https://api.example/provide/vod/"))


def read(state, caller):
    endpoint = "/api/online/catalog/" + caller
    params = {"source_id": state.source["id"], "page": 1}
    params.update({"q": "合成"} if caller == "search" else {"category_id": "10"})
    return state.client.get(endpoint, params=params)


def direct(state, caller, operation):
    if caller == "search":
        return catalog.search(access(state), state.source["id"], "合成", operation=operation)
    return catalog.browse(access(state), state.source["id"], operation=operation)


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize("failure", [NetworkRequestError("network_timeout"), 502, 503, 504])
def test_primary_transient_retry_keeps_original_operation_slot_and_parameters(environment, monkeypatch, caller, failure):
    state = environment
    record = readers(state, monkeypatch, [failure, payload(state)])
    result = read(state, caller)
    assert result.status_code == 200, result.text
    assert len(record.calls) == len(record.clients) == 2
    assert record.operations[0] is record.operations[1]
    assert all(value["active"] == value["owners"] == 1 and value["background"] == 0
               for value in record.admissions)
    assert record.parameters[0] == record.parameters[1]
    expected = ({"ac": "list", "wd": "合成", "pg": "1"} if caller == "search"
                else {"ac": "detail", "pg": "1", "t": "10"})
    assert record.parameters[0][2]["params"] == expected
    assert all(client.closed for client in record.clients)
    assert catalog.admission_stats()["active"] == 0
    with store._base._conn() as connection:
        assert connection.execute("SELECT count(*) FROM online_items").fetchone()[0] == 0


@pytest.mark.parametrize("caller", ["browse", "search"])
def test_two_failures_stop_and_do_not_turn_into_a_successful_empty_catalogue(environment, monkeypatch, caller):
    record = readers(environment, monkeypatch, [503, 503])
    result = read(environment, caller)
    assert result.status_code == 502 and result.json()["code"] == "provider_upstream_error"
    assert len(record.calls) == 2 and all(client.closed for client in record.clients)
    assert catalog.admission_stats()["active"] == 0


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize("category", ["target_dns_timeout", "target_host_not_allowed", "non_public_target_address",
    "network_request_failed", "proxy_connection_failed", "response_body_limit", "operation_cancelled"])
def test_safety_rejection_and_other_non_whitelisted_failures_never_retry(environment, monkeypatch, caller, category):
    record = readers(environment, monkeypatch, [NetworkRequestError(category)])
    assert read(environment, caller).status_code != 200
    assert len(record.calls) == 1


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_upstream_auth_rate_limit_and_non_whitelisted_statuses_never_retry(environment, monkeypatch, caller, status):
    record = readers(environment, monkeypatch, [status])
    assert read(environment, caller).status_code != 200
    assert len(record.calls) == 1


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize("body", [b"not-json", b"x" * (maccms.MAX_BODY + 1)])
def test_malformed_or_oversized_body_never_retries(environment, monkeypatch, caller, body):
    response = httpx.Response(200, content=body, request=httpx.Request("GET", "https://api.example/"))
    record = readers(environment, monkeypatch, [response])
    assert read(environment, caller).status_code == 502
    assert len(record.calls) == 1


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize(("change", "status", "code"), [
    ("name", 409, "online_source_changed"), ("network", 409, "online_source_changed"),
    ("disabled", 503, "online_source_disabled"), ("access", 401, "online_access_revoked"),
])
@pytest.mark.parametrize("request_failed", [True, False])
def test_full_configuration_or_access_changes_block_retry_and_delivery(environment, monkeypatch,
                                                                       caller, change, status, code, request_failed):
    state = environment

    def changing():
        if change == "name":
            online.update_online_source(state.source["id"], {"name": "Changed while reading"})
        elif change == "network":
            online_network.update_online_network_default({"mode": "direct"})
        elif change == "disabled":
            online.update_online_source(state.source["id"], {"enabled": False})
        else:
            viewing.set_password("isolated-metadata-retry")
        return NetworkRequestError("network_timeout") if request_failed else payload(state)

    record = readers(state, monkeypatch, [changing])
    result = read(state, caller)
    assert result.status_code == status and result.json()["code"] == code
    assert len(record.calls) == 1


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize("factory_number", [1, 2])
def test_factory_races_cannot_send_a_request_using_the_changed_route(environment, monkeypatch, caller, factory_number):
    factories = []

    def changed_factory():
        factories.append(True)
        if len(factories) == factory_number:
            online_network.update_online_network_default({"mode": "direct"})

    record = readers(environment, monkeypatch, [503], on_factory=changed_factory)
    result = read(environment, caller)
    assert result.status_code == 409 and result.json()["code"] == "online_source_changed"
    assert len(record.calls) == factory_number - 1
    assert all(client.closed for client in record.clients)


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize("reason", ["cancel", "deadline", "low_budget"])
def test_cancel_or_insufficient_original_budget_prevents_a_second_attempt(environment, monkeypatch, caller, reason):
    operation = NetworkOperation(time.monotonic() + (.9 if reason == "low_budget" else 30))
    original_deadline = operation.deadline

    def stopped():
        if reason == "cancel":
            operation.abort()
        elif reason == "deadline":
            operation.deadline = time.monotonic() - .01
        return NetworkRequestError("network_timeout")

    record = readers(environment, monkeypatch, [stopped])
    with pytest.raises(ProviderError) as caught:
        direct(environment, caller, operation)
    assert caught.value.category == ("provider_cancelled" if reason == "cancel" else "provider_timeout")
    assert len(record.calls) == 1
    if reason != "deadline":
        assert operation.deadline == original_deadline
    assert catalog.admission_stats()["active"] == 0


def test_optional_discovery_cannot_reuse_a_primary_grant_even_with_identical_parameters(environment, monkeypatch):
    state = environment
    record = readers(state, monkeypatch, [payload(state, categories=False), 503])
    parameters = {"ac": "list", "pg": "1"}
    configuration, check = catalog._detail_retry_guard(access(state), state.source)
    with maccms._operation() as operation, maccms.foreground_metadata_retry(configuration, check, parameters):
        result = maccms.fetch(state.source, dict(parameters))
        assert result["list"] == [state.movie]
        assert maccms._discover_categories(state.source) == []
    assert len(record.calls) == 2
    assert record.parameters[0][2]["params"] == record.parameters[1][2]["params"] == parameters
    assert record.operations[0] is operation
    assert record.operations[0] is not record.operations[1], "optional discovery keeps its own bounded child"
    assert record.operations[1].deadline <= min(operation.deadline, time.monotonic() + 3)
    assert all(client.closed for client in record.clients)


def test_mismatched_first_fetch_consumes_the_grant_instead_of_authorizing_a_later_lookup(environment, monkeypatch):
    state = environment
    record = readers(state, monkeypatch, [payload(state), NetworkRequestError("network_timeout")])
    with maccms.foreground_metadata_retry("a" * 64, lambda *_args: None, {"ac": "list", "pg": "1"}):
        maccms.fetch(state.source, {"ac": "detail", "pg": "1"})
        with pytest.raises(ProviderError):
            maccms.fetch(state.source, {"ac": "list", "pg": "1"})
    assert len(record.calls) == 2


@pytest.mark.parametrize("caller", ["browse", "search"])
def test_final_delivery_guard_rejects_a_change_during_artwork_registration(environment, monkeypatch, caller):
    original = catalog._artwork

    def changed_artwork(source, result):
        response = original(source, result)
        online_network.update_online_network_default({"mode": "direct"})
        return response

    monkeypatch.setattr(catalog, "_artwork", changed_artwork)
    record = readers(environment, monkeypatch, [payload(environment)])
    result = read(environment, caller)
    assert result.status_code == 409 and result.json()["code"] == "online_source_changed"
    assert len(record.calls) == 1


@pytest.mark.parametrize("caller", ["browse", "search"])
def test_retry_context_does_not_escape_to_a_later_direct_adapter_read(environment, monkeypatch, caller):
    record = readers(environment, monkeypatch, [503, payload(environment), NetworkRequestError("network_timeout")])
    assert read(environment, caller).status_code == 200
    with pytest.raises(ProviderError):
        maccms.search(environment.source, "合成", 1)
    assert len(record.calls) == 3
