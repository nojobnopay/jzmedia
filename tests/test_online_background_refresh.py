"""Optional catalogue refresh yields to real foreground work without retry."""

from contextlib import contextmanager
import socket
import threading
import time

import pytest

from app import viewing
from app.network.client import NetworkRequestError
from app.network.guard import NetworkOperation, current_operation
from app.online import api_pool, catalog, maccms, taxonomy_cache
from app.online.providers import ProviderError
from app.store import online, online_network
from test_online_catalog import environment as environment
from test_online_detail_retry import access, readers
from test_online_metadata_retry import payload


def request(state, caller, *, priority="background"):
    params = {"source_id": state.source["id"], "page": 1}
    if caller == "search":
        params["q"] = "合成"
    if priority is not None:
        params["priority"] = priority
    return state.client.get("/api/online/catalog/" + caller, params=params)


def direct(state, caller, *, operation=None):
    reader = catalog.background_search if caller == "search" else catalog.background_browse
    args = ("合成",) if caller == "search" else ()
    return reader(access(state), state.source["id"], *args, operation=operation)


@pytest.mark.parametrize("caller", ["browse", "search"])
def test_background_wire_ready_is_preserved_and_foreground_shape_is_unchanged(environment, caller):
    source = environment.client.get("/api/online/catalog/sources").json()["items"][0]
    response = request(environment, caller)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready" and body["items"]
    assert {"source", "items", "page", "page_count", "total", "categories", "status", "configuration_token"} == set(body)
    assert body["configuration_token"] == source["configuration_token"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "https://" not in str(body)
    foreground = request(environment, caller, priority=None)
    assert foreground.status_code == 200 and "status" not in foreground.json()
    assert set(foreground.json()) == set(body) - {"status", "configuration_token"}


@pytest.mark.parametrize("caller", ["browse", "search"])
def test_background_token_identifies_the_configuration_actually_read(environment, caller):
    before = environment.client.get("/api/online/catalog/sources").json()["items"][0]["configuration_token"]
    online.update_online_source(environment.source["id"], {"name": "A new source configuration"})
    actual = environment.client.get("/api/online/catalog/sources").json()["items"][0]["configuration_token"]
    response = request(environment, caller)
    assert response.status_code == 200 and response.json()["status"] == "ready"
    assert response.json()["configuration_token"] == actual != before
    assert response.json()["source"]["name"] == "A new source configuration"


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize(("failure", "code"), [
    (NetworkRequestError("network_timeout"), "provider_timeout"),
    (502, "provider_upstream_error"), (503, "provider_upstream_error"), (504, "provider_upstream_error"),
])
def test_background_primary_failures_never_retry_or_borrow_foreground_pool(environment, monkeypatch,
                                                                         caller, failure, code):
    def factory_check():
        assert api_pool._current.get() is None
        assert maccms._metadata_retry.get() is None and maccms._detail_retry.get() is None

    record = readers(environment, monkeypatch, [failure], on_factory=factory_check)
    before = time.monotonic()
    response = request(environment, caller)
    assert response.status_code in {502, 504} and response.json()["code"] == code
    assert len(record.calls) == 1 and all(client.closed for client in record.clients)
    assert record.operations[0].deadline <= before + catalog.BACKGROUND_REFRESH_SECONDS + .1
    assert record.admissions == [{"active": 1, "owners": 1, "background": 1, "preempt_waiters": 0}]
    assert catalog.admission_stats()["active"] == 0 and api_pool.pool.stats()["active"] == 0


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize("occupied", ["same_owner", "other_background", "global_foreground"])
def test_busy_background_defers_without_reading_a_provider(environment, caller, occupied):
    from contextlib import ExitStack

    state = environment
    owner = access(state).owner
    with ExitStack() as stack:
        if occupied == "same_owner":
            stack.enter_context(catalog._admit(owner))
        elif occupied == "other_background":
            stack.enter_context(catalog._admit("other-device", background=True))
        else:
            for index in range(4):
                stack.enter_context(catalog._admit(f"device-{index}"))
        response = request(state, caller)
        assert response.status_code == 200 and response.json() == {"status": "deferred"}
        assert response.headers["cache-control"] == "no-store"
        assert not state.requests
    assert catalog.admission_stats() == {"active": 0, "owners": 0, "background": 0, "preempt_waiters": 0}


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize("reason", ["parent_cancel", "child_timeout", "unowned_cancel"])
def test_disconnect_deadline_and_unowned_provider_cancel_are_hard_errors(environment, monkeypatch, caller, reason):
    parent = NetworkOperation(time.monotonic() + 30)

    def fail():
        if reason == "parent_cancel":
            parent.abort()
        elif reason == "child_timeout":
            current_operation().deadline = time.monotonic() - .01
        return NetworkRequestError("operation_cancelled" if reason == "unowned_cancel" else "network_timeout")

    record = readers(environment, monkeypatch, [fail])
    with pytest.raises(ProviderError) as error:
        direct(environment, caller, operation=parent)
    assert error.value.category == ("provider_timeout" if reason == "child_timeout" else "provider_cancelled")
    assert len(record.calls) == 1 and all(client.closed for client in record.clients)
    assert catalog.admission_stats()["active"] == 0


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize(("change", "status", "code"), [
    ("source", 409, "online_source_changed"), ("network", 409, "online_source_changed"),
    ("disabled", 503, "online_source_disabled"), ("access", 401, "online_access_revoked"),
])
def test_background_final_delivery_does_not_hide_configuration_or_permission_changes(environment, monkeypatch,
                                                                                   caller, change, status, code):
    state = environment

    def change_during_read():
        if change == "source":
            online.update_online_source(state.source["id"], {"name": "Changed during background read"})
        elif change == "network":
            online_network.update_online_network_default({"mode": "direct"})
        elif change == "disabled":
            online.update_online_source(state.source["id"], {"enabled": False})
        else:
            viewing.set_password("isolated-background-test")
        return payload(state)

    record = readers(state, monkeypatch, [change_during_read])
    response = request(state, caller)
    assert response.status_code == status and response.json()["code"] == code
    assert "status" not in response.json() and len(record.calls) == 1
    assert catalog.admission_stats()["active"] == 0


@pytest.mark.parametrize("caller", ["browse", "search"])
def test_quiet_defer_still_rechecks_complete_configuration(environment, monkeypatch, caller):
    real_admit = catalog._admit

    @contextmanager
    def changed_admission(*args, **kwargs):
        if kwargs.get("background"):
            online_network.update_online_network_default({"mode": "direct"})
            raise catalog.BackgroundDeferred()
        with real_admit(*args, **kwargs) as operation:
            yield operation

    monkeypatch.setattr(catalog, "_admit", changed_admission)
    response = request(environment, caller)
    assert response.status_code == 409 and response.json()["code"] == "online_source_changed"
    assert not environment.requests


@pytest.mark.parametrize("caller", ["browse", "search"])
def test_final_check_covers_artwork_preparation(environment, monkeypatch, caller):
    original = catalog._artwork

    def changed(source, result):
        public = original(source, result)
        online_network.update_online_network_default({"mode": "direct"})
        return public

    monkeypatch.setattr(catalog, "_artwork", changed)
    response = request(environment, caller)
    assert response.status_code == 409 and response.json()["code"] == "online_source_changed"


def test_background_taxonomy_cache_cannot_hide_a_late_network_change(environment, monkeypatch):
    original = taxonomy_cache.cache.put

    def changed_after_put(*args):
        original(*args)
        online_network.update_online_network_default({"mode": "direct"})
        raise RuntimeError("optional cache hook interrupted")

    monkeypatch.setattr(taxonomy_cache.cache, "put", changed_after_put)
    readers(environment, monkeypatch, [payload(environment)])
    response = request(environment, "browse")
    assert response.status_code == 409 and response.json()["code"] == "online_source_changed"
    assert catalog.admission_stats()["active"] == 0


@pytest.mark.parametrize("caller", ["browse", "search"])
@pytest.mark.parametrize("query", ["priority=foreground", "priority=normal", "priority=", "priority=Background",
                                    "priority=background&priority=background", "priority=background&unknown=1"])
def test_priority_parameter_is_fixed_and_duplicates_or_unknown_queries_are_rejected(environment, caller, query):
    suffix = "&q=合成" if caller == "search" else ""
    response = environment.client.get(f"/api/online/catalog/{caller}?source_id={environment.source['id']}&{query}{suffix}")
    assert response.status_code == 422 and response.json()["code"] == "invalid_online_catalog_request"
    assert not environment.requests


@pytest.mark.parametrize("change_after_preempt", [False, True])
def test_foreground_waits_for_background_socket_and_actual_worker_exit(environment, monkeypatch, change_after_preempt):
    state = environment
    selected_access = access(state)
    started, aborted, release, foreground_entered = (threading.Event() for _ in range(4))
    results, failures = {}, []
    sockets = []

    def fetch(source, params):
        operation = current_operation()
        if catalog.admission_stats()["background"]:
            reader, peer = socket.socketpair()
            sockets.extend([reader, peer])
            reader.settimeout(2)
            operation.bind_socket(reader)
            started.set()
            try:
                reader.recv(1)
            except OSError:
                pass
            finally:
                aborted.set()
                assert release.wait(2)
                reader.close()
                peer.close()
            if change_after_preempt:
                online_network.update_online_network_default({"mode": "direct"})
            operation.check()
        foreground_entered.set()
        return {"code": 1, "page": 1, "pagecount": 1, "total": 1, "list": [state.movie],
                "class": [{"type_id": "1", "type_name": "电影"}]}

    monkeypatch.setattr(maccms, "fetch", fetch)

    def background():
        try:
            results["background"] = catalog.background_search(selected_access, state.source["id"], "合成")
        except (ProviderError, catalog.CatalogError) as error:
            failures.append(error.category)

    def foreground():
        try:
            results["foreground"] = catalog.search(selected_access, state.source["id"], "合成")
        except (ProviderError, catalog.CatalogError) as error:
            failures.append(error.category)

    slow, fast = threading.Thread(target=background), threading.Thread(target=foreground)
    try:
        slow.start()
        assert started.wait(1)
        fast.start()
        assert aborted.wait(1)
        assert not foreground_entered.wait(.03)
        assert catalog.admission_stats() == {"active": 1, "owners": 1, "background": 1, "preempt_waiters": 1}
        release.set()
        slow.join(2)
        fast.join(2)
        assert not slow.is_alive() and not fast.is_alive()
        assert foreground_entered.is_set() and results["foreground"]["items"]
        if change_after_preempt:
            assert failures == ["online_source_changed"] and "background" not in results
        else:
            assert failures == [] and results["background"] == {"status": "deferred"}
    finally:
        release.set()
        for connection in sockets:
            connection.close()
        if slow.ident:
            slow.join(2)
        if fast.ident:
            fast.join(2)
    assert catalog.admission_stats() == {"active": 0, "owners": 0, "background": 0, "preempt_waiters": 0}
