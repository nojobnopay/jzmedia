"""Optional branches preserve useful metadata without weakening cancellation."""

from contextlib import contextmanager
import time

from fastapi.testclient import TestClient
import pytest

from app import store, viewing
from app.network.guard import NetworkOperation, current_operation
from app.online import catalog, health, maccms, related
from app.online.providers import ProviderError
from app.store import online, online_network
from test_online_related import capture, read, related_environment as related_environment
from test_online_catalog import environment as environment


def warm(state, selected="s2"):
    response = state.client.get("/api/online/catalog/detail", params={
        "source_id": state.source["id"], "external_id": selected})
    assert response.status_code == 200, response.text
    return response.json()


def test_foreground_detail_warms_metadata_only_and_related_skips_the_duplicate_read(related_environment):
    state = related_environment
    warm(state)
    assert len(state.calls) == 1 and related.cache.stats()["entries"] == 1
    access = capture(state)
    _source, configuration = related._snapshot(access, state.source["id"])
    key = related._cache_key(access, state.source["id"], "s2", configuration)
    target = related.cache.get_target(key)
    assert target["external_id"] == "s2" and target["category_id"] == "12"
    assert "lines" not in target and "poster_path" not in target and "_poster_url" not in target
    assert "https://" not in str(target)
    result = read(state)
    assert result.status_code == 200 and result.json()["partial"] is False
    assert len(state.calls) == 3 and sum("ids" in call for call in state.calls) == 1
    assert related.cache.stats()["entries"] == 2
    related.cache.clear_results()
    assert related.cache.stats()["entries"] == 1
    assert read(state).status_code == 200
    assert len(state.calls) == 5 and sum("ids" in call for call in state.calls) == 1


@pytest.mark.parametrize("change", ["network", "source"])
def test_target_provenance_does_not_follow_changed_network_or_source(related_environment, change):
    state = related_environment
    warm(state)
    if change == "network":
        online_network.update_online_network_default({"mode": "direct"})
    else:
        online.update_online_source(state.source["id"], {"name": "New provider label"})
    assert read(state).status_code == 200
    assert len(state.calls) == 4 and sum("ids" in call for call in state.calls) == 2


def test_target_is_device_scoped_and_still_checks_current_access(related_environment):
    state = related_environment
    warm(state)
    other = TestClient(state.client.app)
    try:
        other.get("/api/viewing/session")
        assert read(state, client=other).status_code == 200
    finally:
        other.close()
    assert len(state.calls) == 4 and sum("ids" in call for call in state.calls) == 2
    online.update_online_source(state.source["id"], {"enabled": False})
    assert read(state).status_code == 503
    assert len(state.calls) == 4


@pytest.mark.parametrize("change", ["network", "source"])
def test_detail_cannot_stamp_old_metadata_with_a_new_configuration(related_environment, monkeypatch, change):
    state = related_environment
    original = maccms.fetch

    def changing(source, params):
        result = original(source, params)
        if "ids" in params:
            if change == "network":
                online_network.update_online_network_default({"mode": "direct"})
            else:
                online.update_online_source(state.source["id"], {"name": "Renamed while reading"})
        return result

    monkeypatch.setattr(maccms, "fetch", changing)
    response = state.client.get("/api/online/catalog/detail", params={
        "source_id": state.source["id"], "external_id": "s2"})
    assert response.status_code == 409 and response.json()["code"] == "online_source_changed"
    assert related.cache.stats() == {"entries": 0, "bytes": 0}
    with store._base._conn() as connection:
        assert connection.execute("SELECT count(*) FROM online_items").fetchone()[0] == 0
    monkeypatch.setattr(maccms, "fetch", original)
    assert read(state).status_code == 200
    assert sum("ids" in call for call in state.calls) == 2


def test_optional_target_cache_failure_never_blocks_detail_identity_persistence(related_environment, monkeypatch):
    state = related_environment

    def unavailable(*_args):
        raise RuntimeError("optional metadata cache unavailable")

    monkeypatch.setattr(related.cache, "put_target", unavailable)
    warm(state)
    assert related.cache.stats()["entries"] == 0
    with store._base._conn() as connection:
        assert connection.execute("SELECT count(*) FROM online_items").fetchone()[0] == 1


@pytest.mark.parametrize(("change", "status", "code"), [
    ("source", 503, "online_source_disabled"),
    ("access", 401, "online_access_revoked"),
])
def test_detail_rechecks_access_after_optional_target_cache_work(related_environment, monkeypatch, change, status, code):
    state = related_environment
    original = related.cache.put_target

    def revoke(*args):
        original(*args)
        if change == "source":
            online.update_online_source(state.source["id"], {"enabled": False})
        else:
            viewing.set_password("123456")
        # The cache optimization may suppress its own failure. It must not
        # suppress the final foreground access check or deliver this detail.
        raise RuntimeError("optional cache write interrupted")

    monkeypatch.setattr(related.cache, "put_target", revoke)
    response = state.client.get("/api/online/catalog/detail", params={
        "source_id": state.source["id"], "external_id": "s2"})
    assert response.status_code == status and response.json()["code"] == code
    assert "lines" not in response.json()


@pytest.mark.parametrize("failing_branch", ["search", "category"])
def test_one_failed_branch_keeps_useful_cards_and_partial_retry_is_not_cached(related_environment, monkeypatch, failing_branch):
    state = related_environment
    original = maccms.fetch
    fail = [True]

    def fetch(source, params):
        if fail[0] and ("wd" in params if failing_branch == "search" else "t" in params):
            state.calls.append(dict(params))
            raise ProviderError("provider_upstream_error")
        return original(source, params)

    monkeypatch.setattr(maccms, "fetch", fetch)
    for _attempt in range(2):
        response = read(state)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "ready" and result["partial"] is True
        assert any(result[group] for group in ("series", "related", "name_related"))
        assert related.cache.stats()["entries"] == 0
    assert len(state.calls) == 6
    fail[0] = False
    assert read(state).json()["partial"] is False
    assert related.cache.stats()["entries"] == 1
    assert read(state).status_code == 200 and len(state.calls) == 9


@pytest.mark.parametrize("both_fail", [False, True])
def test_failure_with_no_useful_cards_is_an_error_and_is_never_an_empty_cache_hit(related_environment, monkeypatch, both_fail):
    state = related_environment
    original = maccms.fetch

    def fetch(source, params):
        if "ids" in params:
            return original(source, params)
        state.calls.append(dict(params))
        if both_fail or "wd" in params:
            raise ProviderError("provider_upstream_error")
        return {"code": 1, "list": [], "page": 1, "pagecount": 0, "total": 0}

    monkeypatch.setattr(maccms, "fetch", fetch)
    for _attempt in range(2):
        response = read(state)
        assert response.status_code == 502 and response.json()["code"] == "provider_upstream_error"
        assert related.cache.stats()["entries"] == 0
    assert len(state.calls) == 6


def test_a_successfully_empty_search_and_category_can_be_cached(related_environment, monkeypatch):
    state = related_environment
    original = maccms.fetch

    def fetch(source, params):
        if "ids" in params:
            return original(source, params)
        state.calls.append(dict(params))
        return {"code": 1, "list": [], "page": 1, "pagecount": 0, "total": 0}

    monkeypatch.setattr(maccms, "fetch", fetch)
    first = read(state)
    assert first.status_code == 200 and first.json()["partial"] is False
    assert all(first.json()[group] == [] for group in ("series", "related", "name_related"))
    assert read(state).status_code == 200 and len(state.calls) == 3
    assert related.cache.stats()["entries"] == 1


def test_optional_branch_deadline_aborts_its_socket_without_cancelling_the_sibling(related_environment, monkeypatch):
    state = related_environment
    original = maccms.fetch
    operations, shutdowns = [], []
    monkeypatch.setattr(related, "STEP_SECONDS", .015)

    class Socket:
        def shutdown(self, _how):
            shutdowns.append(True)

    def fetch(source, params):
        operation = current_operation()
        if "wd" in params:
            operations.append(operation)
            operation.bind_socket(Socket())
            state.calls.append(dict(params))
            time.sleep(.025)
            operation.check()
        if "t" in params:
            operations.append(operation)
            assert operation is not operations[0] and operation.remaining() > 0
        return original(source, params)

    monkeypatch.setattr(maccms, "fetch", fetch)
    response = read(state)
    assert response.status_code == 200, response.text
    assert response.json()["partial"] is True and response.json()["related"]
    assert len(operations) == 2 and shutdowns == [True]
    assert related.cache.stats()["entries"] == 0


@pytest.mark.parametrize("cancel", ["http", "preempt", "deadline"])
def test_optional_failure_cannot_hide_http_cancel_preemption_or_total_deadline(related_environment, monkeypatch, cancel):
    state = related_environment
    access = capture(state)
    original = maccms.fetch
    parent = NetworkOperation(time.monotonic() + (.03 if cancel == "deadline" else 8))

    def fetch(source, params):
        if "t" in params:
            if cancel == "http":
                parent.abort()
            elif cancel == "preempt":
                with catalog._lock:
                    background = catalog._background[access.owner]
                background.abort()
            else:
                time.sleep(.04)
            current_operation().check()
        return original(source, params)

    monkeypatch.setattr(maccms, "fetch", fetch)
    if cancel == "preempt":
        result = related.read(access, state.source["id"], "s2", operation=parent)
        assert result["status"] == "deferred" and result["partial"] is False
    else:
        with pytest.raises(ProviderError) as error:
            related.read(access, state.source["id"], "s2", operation=parent)
        assert error.value.category == ("provider_timeout" if cancel == "deadline" else "provider_cancelled")
    assert related.cache.stats()["entries"] == 0
    assert catalog.admission_stats() == {"active": 0, "owners": 0, "background": 0, "preempt_waiters": 0}


def test_soft_failure_is_observed_for_its_actual_branch_not_as_overall_success(related_environment, monkeypatch):
    state = related_environment
    original = maccms.fetch
    observed = []

    @contextmanager
    def observe():
        try:
            yield
        except Exception as error:
            observed.append(getattr(error, "category", "operation_error"))
            raise
        else:
            observed.append(None)

    def fetch(source, params):
        if "t" in params:
            raise ProviderError("provider_network_error")
        return original(source, params)

    monkeypatch.setattr(health, "observe_catalogue", observe)
    monkeypatch.setattr(maccms, "fetch", fetch)
    response = read(state)
    assert response.status_code == 200 and response.json()["partial"] is True
    assert observed == [None, None, "provider_network_error"]


def test_target_and_results_share_one_count_byte_and_ttl_budget():
    now = [100.0]
    cache = related.RelatedCache(clock=lambda: now[0], max_entries=2, max_bytes=240)
    target = {"external_id": "same-id", "title": "trusted metadata"}
    result = {"status": "ready", "partial": False, "series": []}
    cache.put_target("same", target)
    cache.put("same", result)
    assert cache.get_target("same") == target and cache.get("same") == result
    assert cache.stats()["entries"] == 2
    cache.get_target("same")["title"] = "mutated"
    assert cache.get_target("same") == target
    cache.put("other", result)
    assert cache.get("same") is None and cache.get_target("same") == target
    cache.put("partial", {"status": "ready", "partial": True, "series": []})
    assert cache.get("partial") is None
    cache.clear_results()
    assert cache.stats()["entries"] == 1 and cache.get_target("same") == target
    now[0] += 120
    assert cache.stats() == {"entries": 0, "bytes": 0}

