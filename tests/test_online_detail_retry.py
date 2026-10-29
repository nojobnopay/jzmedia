"""A foreground metadata retry stays inside its original ownership and budget."""

import json
from types import SimpleNamespace
import time

import httpx
import pytest

from app import store, viewing
from app.network.client import NetworkRequestError
from app.network.guard import NetworkOperation, current_operation
from app.online import catalog, maccms, related, service
from app.online.models import OnlinePlaybackIdentity
from app.online.providers import ProviderError
from app.store import online, online_network
from test_online_catalog import environment as environment


REAL_FETCH = maccms.fetch


def response(state, status=200, *, body=None):
    return httpx.Response(status, content=(body if body is not None else json.dumps({
        "code": 1, "list": [state.movie]}).encode()), request=httpx.Request("GET", "https://api.example/provide/vod/"))


def access(state):
    return service.capture_access(SimpleNamespace(headers={}, cookies={
        viewing.COOKIE: state.client.cookies.get(viewing.COOKIE)}))


def detail(state):
    return state.client.get("/api/online/catalog/detail", params={
        "source_id": state.source["id"], "external_id": "movie-1"})


def readers(state, monkeypatch, outcomes, *, on_factory=None):
    record = SimpleNamespace(clients=[], calls=[], operations=[], admissions=[], parameters=[])

    class Reader:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def request(self, method, url, **kwargs):
            index = len(record.calls)
            record.calls.append(self)
            record.operations.append(current_operation())
            record.admissions.append(catalog.admission_stats())
            record.parameters.append((method, url, kwargs))
            outcome = outcomes[index]
            if callable(outcome):
                outcome = outcome()
            if isinstance(outcome, Exception):
                raise outcome
            return response(state, outcome) if type(outcome) is int else outcome

    def factory(source_id, purpose):
        assert source_id == state.source["id"] and purpose == "api"
        assert all(client.closed for client in record.clients), "The prior real reader must close before a retry"
        if on_factory is not None:
            on_factory()
        client = Reader()
        record.clients.append(client)
        return client

    monkeypatch.setattr(maccms, "fetch", REAL_FETCH)
    monkeypatch.setattr(maccms, "create_online_client", factory)
    return record


@pytest.mark.parametrize("failure", [NetworkRequestError("network_timeout"), 502, 503, 504])
def test_foreground_transient_failure_retries_once_with_same_operation_and_admission(environment, monkeypatch, failure):
    state = environment
    record = readers(state, monkeypatch, [failure, 200])
    result = detail(state)
    assert result.status_code == 200, result.text
    assert len(record.calls) == len(record.clients) == 2
    assert record.operations[0] is record.operations[1]
    assert all(value["active"] == value["owners"] == 1 and value["background"] == 0 for value in record.admissions)
    assert all(client.closed for client in record.clients)
    assert record.parameters[0] == record.parameters[1]
    assert record.parameters[0][2]["params"] == {"ac": "detail", "ids": "movie-1"}
    assert catalog.admission_stats()["active"] == 0


@pytest.mark.parametrize(("failure", "code"), [
    (NetworkRequestError("network_timeout"), "provider_timeout"), (503, "provider_upstream_error"),
])
def test_two_transient_failures_stop_at_two_reads(environment, monkeypatch, failure, code):
    record = readers(environment, monkeypatch, [failure, failure])
    result = detail(environment)
    assert result.json()["code"] == code
    assert len(record.calls) == 2 and all(client.closed for client in record.clients)
    assert catalog.admission_stats()["active"] == 0


@pytest.mark.parametrize("category", [
    "target_dns_timeout", "target_dns_busy", "target_host_not_allowed", "non_public_target_address",
    "network_request_failed", "proxy_connection_failed", "response_body_limit",
    "encoded_response_not_supported", "operation_cancelled",
])
def test_non_whitelisted_network_failures_never_retry(environment, monkeypatch, category):
    record = readers(environment, monkeypatch, [NetworkRequestError(category)])
    assert detail(environment).status_code != 200
    assert len(record.calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 500])
def test_non_whitelisted_upstream_statuses_never_retry(environment, monkeypatch, status):
    record = readers(environment, monkeypatch, [status])
    assert detail(environment).status_code != 200
    assert len(record.calls) == 1


@pytest.mark.parametrize("body", [b"not-json", b'{"code":0,"list":[]}', b"x" * (maccms.MAX_BODY + 1)])
def test_decode_and_body_limits_never_retry(environment, monkeypatch, body):
    record = readers(environment, monkeypatch, [response(environment, body=body)])
    assert detail(environment).status_code != 200
    assert len(record.calls) == 1


def test_less_than_one_second_remaining_never_starts_another_read(environment, monkeypatch):
    record = readers(environment, monkeypatch, [NetworkRequestError("network_timeout")])
    operation = NetworkOperation(time.monotonic() + .9)
    deadline = operation.deadline
    with pytest.raises(ProviderError) as error:
        catalog.detail(access(environment), environment.source["id"], "movie-1", operation=operation)
    assert error.value.category == "provider_timeout"
    assert len(record.calls) == 1 and operation.deadline == deadline


@pytest.mark.parametrize("reason", ["cancel", "deadline"])
def test_parent_cancellation_and_total_deadline_win_over_a_transport_timeout(environment, monkeypatch, reason):
    operation = NetworkOperation(time.monotonic() + 30)

    def stopped():
        if reason == "cancel":
            operation.abort()
        else:
            operation.deadline = time.monotonic() - .01
        return NetworkRequestError("network_timeout")

    record = readers(environment, monkeypatch, [stopped])
    with pytest.raises(ProviderError) as error:
        catalog.detail(access(environment), environment.source["id"], "movie-1", operation=operation)
    assert error.value.category == ("provider_cancelled" if reason == "cancel" else "provider_timeout")
    assert len(record.calls) == 1 and catalog.admission_stats()["active"] == 0


@pytest.mark.parametrize(("change", "status", "code"), [
    ("name", 409, "online_source_changed"), ("network", 409, "online_source_changed"),
    ("disabled", 503, "online_source_disabled"), ("access", 401, "online_access_revoked"),
])
@pytest.mark.parametrize("request_failed", [True, False])
def test_configuration_or_access_changes_block_retry_and_delivery(environment, monkeypatch,
                                                                  change, status, code, request_failed):
    state = environment

    def changing():
        if change == "name":
            online.update_online_source(state.source["id"], {"name": "Changed while reading"})
        elif change == "network":
            online_network.update_online_network_default({"mode": "direct"})
        elif change == "disabled":
            online.update_online_source(state.source["id"], {"enabled": False})
        else:
            viewing.set_password("isolated-detail-retry")
        return NetworkRequestError("network_timeout") if request_failed else response(state)

    record = readers(state, monkeypatch, [changing])
    result = detail(state)
    assert result.status_code == status and result.json()["code"] == code
    assert len(record.calls) == 1
    with store._base._conn() as connection:
        assert connection.execute("SELECT count(*) FROM online_items").fetchone()[0] == 0


def test_factory_configuration_race_prevents_even_the_first_request(environment, monkeypatch):
    record = readers(environment, monkeypatch, [], on_factory=lambda:
        online_network.update_online_network_default({"mode": "direct"}))
    result = detail(environment)
    assert result.status_code == 409 and result.json()["code"] == "online_source_changed"
    assert len(record.clients) == 1 and record.clients[0].closed and record.calls == []


@pytest.mark.parametrize("caller", ["adapter", "related", "resolver"])
def test_adapter_background_and_playback_resolution_do_not_inherit_foreground_retries(environment, monkeypatch, caller):
    state = environment
    item = online.save_online_catalog(state.source["id"], "movie-1", "movie", "合成电影", [])
    record = readers(state, monkeypatch, [NetworkRequestError("network_timeout")])
    with pytest.raises(ProviderError) as error:
        if caller == "adapter":
            maccms.detail(state.source, "movie-1")
        elif caller == "related":
            related.read(access(state), state.source["id"], "movie-1")
        else:
            catalog.resolve(OnlinePlaybackIdentity(source_id=state.source["id"], item_id=item["item"]["id"], kind="movie"))
    assert error.value.category == "provider_timeout"
    assert len(record.calls) == 1


def test_retry_opt_in_is_removed_after_successful_foreground_read(environment, monkeypatch):
    record = readers(environment, monkeypatch, [503, 200, NetworkRequestError("network_timeout")])
    assert detail(environment).status_code == 200
    with pytest.raises(ProviderError):
        maccms.detail(environment.source, "movie-1")
    assert len(record.calls) == 3
