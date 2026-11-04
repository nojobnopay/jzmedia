"""Progressive catalogue searches use isolated storage and synthetic pages."""

import asyncio
from copy import deepcopy
import json
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import store, viewing
from app.main import app
from app.network.guard import NetworkOperation, NetworkOperationError, current_operation
from app.online import catalog, maccms, search_sessions
from app.routers import online_catalog
from app.store import online, online_network
from test_online_catalog import environment as environment


__all__ = ["environment"]


PATH = "/api/online/catalog/search-sessions"
CLASSES = [
    {"type_id": 1, "type_name": "电影", "type_pid": 0},
    {"type_id": 11, "type_name": "剧情片", "type_pid": 1},
    {"type_id": 2, "type_name": "连续剧", "type_pid": 0},
    {"type_id": 13, "type_name": "国产剧", "type_pid": 2},
    {"type_id": 16, "type_name": "欧美剧", "type_pid": 2},
    {"type_id": 161, "type_name": "欧美悬疑", "type_pid": 16},
]


@pytest.fixture
def search_environment(environment, monkeypatch):
    state = environment
    online.update_online_source(state.source["id"], {
        "api_base_url": "https://cj.lziapi.com/api.php/provide/vod/", "allowed_hosts": ["cj.lziapi.com"]})
    state.clock = [10.0]
    monkeypatch.setattr(search_sessions, "registry", search_sessions.SearchSessions(clock=lambda: state.clock[0]))
    state.rows = [{"vod_id": f"result-{index}", "vod_name": f"搜索条目 {index}",
                   "type_id": 161 if index == 205 else 13 if index % 2 else 11,
                   "type_name": "目录分类", "vod_year": "" if index % 3 == 0 else "2026(中国大陆)"}
                  for index in range(400)]
    state.calls = []

    def fetch(_source, params):
        state.calls.append(dict(params))
        page = int(params["pg"])
        assert params["wd"] == "边军" and params["pagesize"] == "100"
        assert "t" not in params and "year" not in params and "ids" not in params
        return {"code": 1, "page": page, "pagecount": (len(state.rows) + 99) // 100,
                "total": len(state.rows), "list": deepcopy(state.rows[(page - 1) * 100:page * 100]),
                "class": deepcopy(CLASSES)}

    state.fetch = fetch
    monkeypatch.setattr(maccms, "fetch", fetch)
    return state


def create(state, **extra):
    return state.client.post(PATH, json={"source_id": state.source["id"], "q": "边军", **extra})


def more(state, identity, **selection):
    return state.client.post(f"{PATH}/{identity}/more", json=selection)


def read(state, identity, **selection):
    return state.client.get(f"{PATH}/{identity}", params=selection)


def test_first_search_reads_one_page_and_exposes_only_a_display_window(search_environment):
    state = search_environment
    response = create(state)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "partial" and data["scanned"] == data["found"] == 100
    assert len(data["items"]) == 20 and data["total"] is None
    assert data["has_more"] and data["can_continue"]
    assert len(state.calls) == 1 and state.calls[0]["pg"] == "1"
    assert not any(field in response.text for field in ("api_base_url", "_poster_url", "vod_play_url"))
    sources = state.client.get("/api/online/catalog/sources").json()
    assert sources["items"][0]["capabilities"]["search_filters"] is True


def test_filters_read_cached_prefix_and_include_all_descendants(search_environment):
    state = search_environment
    identity = create(state).json()["session_id"]
    roots = read(state, identity, category_id="2", category_scope="descendants").json()
    assert roots["found"] == 50 and all(row["category_id"] == "13" for row in roots["items"])
    exact = read(state, identity, category_id="2").json()
    assert exact["found"] == 0 and exact["total"] is None and exact["can_continue"]
    years = read(state, identity, year="2026").json()
    assert years["found"] == 66
    assert all(row["year"] == "2026(中国大陆)" for row in years["items"])
    combined = read(state, identity, category_id="2", category_scope="descendants", year="2026").json()
    assert combined["found"] == 33
    # A request already satisfied by cached matching rows must do no I/O.
    assert more(state, identity, category_id="2", category_scope="descendants").status_code == 200
    assert len(state.calls) == 1


def test_rare_filter_advances_at_most_two_pages_and_does_not_claim_empty(search_environment):
    state = search_environment
    identity = create(state).json()["session_id"]
    empty_prefix = read(state, identity, category_id="161").json()
    assert empty_prefix["items"] == [] and empty_prefix["total"] is None
    data = more(state, identity, category_id="161").json()
    assert [call["pg"] for call in state.calls] == ["1", "2", "3"]
    assert data["status"] == "partial" and data["scanned"] == 300
    assert [row["external_id"] for row in data["items"]] == ["result-205"]
    data = more(state, identity, category_id="16", category_scope="descendants").json()
    assert data["status"] == "complete" and data["scanned"] == 400
    assert data["total"] == data["found"] == 1 and not data["has_more"] and not data["can_continue"]


def test_complete_zero_is_distinct_and_more_can_load_cached_windows(search_environment):
    state = search_environment
    state.rows = state.rows[:120]
    identity = create(state).json()["session_id"]
    complete = more(state, identity, category_id="161").json()
    assert complete["status"] == "complete" and complete["total"] == 0 and complete["items"] == []
    all_rows = read(state, identity).json()
    assert all_rows["total"] == 120 and all_rows["has_more"] and not all_rows["can_continue"]
    next_rows = more(state, identity, offset=20).json()
    assert next_rows["items"][0]["external_id"] == "result-20"
    assert len(state.calls) == 2


def test_reuse_refresh_and_expiry_do_not_silently_clear_selection(search_environment):
    state = search_environment
    first = create(state).json()
    reused = create(state, category_id="2", category_scope="descendants", year="2026").json()
    assert reused["session_id"] == first["session_id"] and len(state.calls) == 1
    refreshed = create(state, refresh=True).json()
    assert refreshed["session_id"] != first["session_id"] and len(state.calls) == 2
    state.clock[0] += search_sessions.FRESH_SECONDS
    response = more(state, refreshed["session_id"], category_id="2")
    assert response.status_code == 410 and response.json()["code"] == "online_search_session_expired"
    # Retained stale results can still be read; more must explicitly recreate.
    assert read(state, refreshed["session_id"], category_id="2").status_code == 200
    state.clock[0] += search_sessions.IDLE_SECONDS
    assert read(state, refreshed["session_id"]).status_code == 410


def test_other_device_cannot_observe_or_advance_a_session(search_environment):
    state = search_environment
    identity = create(state).json()["session_id"]
    with TestClient(app) as other:
        assert other.get("/api/viewing/session").status_code == 200
        assert other.get(f"{PATH}/{identity}").status_code == 410
        assert other.post(f"{PATH}/{identity}/more", json={}).status_code == 410
    assert len(state.calls) == 1


def test_source_network_change_revokes_cached_data(search_environment):
    state = search_environment
    identity = create(state).json()["session_id"]
    online_network.update_online_network_default({"mode": "direct"})
    response = read(state, identity)
    assert response.status_code == 409 and response.json()["code"] == "online_source_changed"
    assert len(state.calls) == 1


@pytest.mark.parametrize("fault", ["total", "duplicate", "taxonomy", "missing"])
def test_inconsistent_pages_preserve_prefix_without_becoming_complete(search_environment, monkeypatch, fault):
    state = search_environment
    identity = create(state).json()["session_id"]

    def fetch(source, params):
        result = state.fetch(source, params)
        if fault == "total":
            result["total"] += 1
            result["pagecount"] = 5
        elif fault == "duplicate":
            result["list"][0]["vod_id"] = "result-1"
        elif fault == "taxonomy":
            result["class"][0]["type_name"] = "已变化的分类"
        else:
            result["list"].pop()
        return result

    monkeypatch.setattr(maccms, "fetch", fetch)
    response = more(state, identity, category_id="161")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "error" and data["error_code"] == "online_search_results_changed"
    assert data["scanned"] == 100 and data["total"] is None and not data["can_continue"]
    assert read(state, identity).json()["found"] == 100


def test_network_failure_retains_data_and_explicit_retry_resumes(search_environment, monkeypatch):
    state = search_environment
    identity = create(state).json()["session_id"]
    monkeypatch.setattr(maccms, "fetch", lambda *_args: (_ for _ in ()).throw(maccms.ProviderError("provider_timeout")))
    failed = more(state, identity, category_id="161").json()
    assert failed["status"] == "error" and failed["scanned"] == 100 and failed["can_continue"]
    monkeypatch.setattr(maccms, "fetch", state.fetch)
    resumed = more(state, identity, category_id="161").json()
    assert resumed["status"] == "partial" and resumed["scanned"] == 300


def test_first_page_failure_with_filters_preserves_network_error(search_environment, monkeypatch):
    state = search_environment
    monkeypatch.setattr(maccms, "fetch", lambda *_args: (_ for _ in ()).throw(maccms.ProviderError("provider_timeout")))
    response = create(state, category_id="2", category_scope="descendants", year="2026")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "error" and data["error_code"] == "provider_timeout"
    assert data["items"] == [] and data["total"] is None and data["can_continue"]
    monkeypatch.setattr(maccms, "fetch", state.fetch)
    resumed = more(state, data["session_id"], category_id="2", category_scope="descendants", year="2026").json()
    assert resumed["scanned"] == 100 and resumed["found"] == 33


def test_parallel_read_observes_loading_and_completed_prefix(search_environment, monkeypatch):
    state = search_environment
    identity = create(state).json()["session_id"]
    captured = catalog.sources
    access = []
    from app.online import service
    monkeypatch.setattr(catalog, "sources", lambda value: (access.append(value), captured(value))[1])
    state.client.get("/api/online/catalog/sources")
    entered, release = threading.Event(), threading.Event()
    errors = []

    def blocked_fetch(source, params):
        if params["pg"] == "3":
            entered.set()
            assert release.wait(3)
        return state.fetch(source, params)

    monkeypatch.setattr(maccms, "fetch", blocked_fetch)

    def advance():
        try:
            search_sessions.more(access[0], identity, {"category_id": "161"})
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=advance)
    worker.start()
    try:
        assert entered.wait(3)
        partial = search_sessions.read(access[0], identity)
        assert partial["status"] == "loading" and partial["scanned"] == 200
        assert len(partial["items"]) == 20
    finally:
        release.set()
        worker.join(3)
    assert not errors and not worker.is_alive()
    assert service._current_access(access[0]) is None


def test_page_budget_stops_without_marking_partial_data_complete(search_environment, monkeypatch):
    state = search_environment
    monkeypatch.setattr(search_sessions, "MAX_PAGES", 2)
    identity = create(state).json()["session_id"]
    data = more(state, identity, category_id="161").json()
    assert data["status"] == "error" and data["error_code"] == "online_search_limit_reached"
    assert data["scanned"] == 200 and data["total"] is None and not data["can_continue"]
    assert len(state.calls) == 2


def captured_access(state, monkeypatch):
    captured = []
    original = catalog.sources
    monkeypatch.setattr(catalog, "sources", lambda value: (captured.append(value), original(value))[1])
    assert state.client.get("/api/online/catalog/sources").status_code == 200
    return captured[0]


def test_final_page_parallel_get_has_consistent_complete_status_and_total(search_environment, monkeypatch):
    state = search_environment
    state.rows = state.rows[:120]
    identity = create(state).json()["session_id"]
    access = captured_access(state, monkeypatch)
    original_commit = search_sessions.registry._commit
    entered, release = threading.Event(), threading.Event()
    errors = []

    def commit(value, result):
        original_commit(value, result)
        if value.complete:
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr(search_sessions.registry, "_commit", commit)

    def advance():
        try:
            search_sessions.more(access, identity, {"category_id": "161"})
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=advance)
    worker.start()
    try:
        assert entered.wait(3)
        response = read(state, identity)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "complete" and data["total"] == data["found"] == 120
        assert data["has_more"] and not data["can_continue"]
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors


@pytest.mark.parametrize("stop", ["foreground", "disconnect"])
def test_foreground_preemption_and_disconnect_release_worker_without_late_page(
        search_environment, monkeypatch, stop):
    state = search_environment
    identity = create(state).json()["session_id"]
    access = captured_access(state, monkeypatch)
    entered = threading.Event()
    parent = NetworkOperation(time.monotonic() + 30)
    results, errors = [], []

    def blocked_fetch(_source, _params):
        operation = current_operation()
        entered.set()
        assert operation.cancelled.wait(3), "the active child socket must be cancelled promptly"
        operation.check()

    monkeypatch.setattr(maccms, "fetch", blocked_fetch)

    def advance():
        try:
            results.append(search_sessions.more(access, identity, {"category_id": "161"}, operation=parent))
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=advance)
    worker.start()
    try:
        assert entered.wait(3)
        if stop == "foreground":
            with catalog._admit(access.owner, NetworkOperation(time.monotonic() + 3)):
                assert not parent.cancelled.is_set()
        else:
            parent.abort()
        worker.join(3)
        assert not worker.is_alive()
        if stop == "foreground":
            assert not errors and results[0]["status"] == "partial"
        else:
            assert len(errors) == 1 and isinstance(errors[0], NetworkOperationError)
        assert search_sessions.read(access, identity)["scanned"] == 100
        assert catalog.admission_stats()["active"] == catalog.admission_stats()["background"] == 0
    finally:
        parent.abort()
        worker.join(3)
        parent.close()


def test_batch_deadline_bounds_io_and_retains_prefix(search_environment, monkeypatch):
    state = search_environment
    identity = create(state).json()["session_id"]
    started = time.monotonic()

    def expired_fetch(_source, _params):
        operation = current_operation()
        assert operation.deadline <= started + search_sessions.BATCH_SECONDS + 0.1
        operation.deadline = time.monotonic() - 1
        operation.check()

    monkeypatch.setattr(maccms, "fetch", expired_fetch)
    response = more(state, identity, category_id="161")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "error" and data["error_code"] == "provider_timeout"
    assert data["scanned"] == 100 and data["can_continue"]
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("middleware", [False, True], ids=["router", "base-http-middleware"])
@pytest.mark.parametrize("cancellation", ["disconnect", "asgi_cancel"])
def test_real_post_disconnect_or_cancel_stops_active_page(search_environment, monkeypatch, middleware, cancellation):
    state = search_environment
    identity = create(state).json()["session_id"]
    application = FastAPI()
    application.include_router(online_catalog.router)
    if middleware:
        @application.middleware("http")
        async def passthrough(incoming, call_next):
            return await call_next(incoming)
    entered = threading.Event()

    def blocked_fetch(_source, _params):
        operation = current_operation()
        entered.set()
        assert operation.cancelled.wait(3)
        operation.check()

    monkeypatch.setattr(maccms, "fetch", blocked_fetch)

    async def scenario():
        body = json.dumps({"category_id": "161"}).encode()
        path = f"{PATH}/{identity}/more"
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": body, "more_body": False})
        messages = []
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"content-type", b"application/json"),
                             (b"content-length", str(len(body)).encode()),
                             (b"x-viewing-session", state.client.cookies.get(viewing.COOKIE).encode())],
                 "server": ("fixture", 80), "client": ("127.0.0.1", 12345), "state": {}}

        async def receive():
            await asyncio.sleep(0)
            return await incoming.get()

        async def send(message):
            messages.append(message)

        task = asyncio.create_task(application(scope, receive, send))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            if cancellation == "disconnect":
                incoming.put_nowait({"type": "http.disconnect"})
                await asyncio.wait_for(task, 2)
                assert [message["status"] for message in messages if message["type"] == "http.response.start"] == [410]
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            deadline = time.monotonic() + 2
            while catalog.admission_stats()["active"] and time.monotonic() < deadline:
                await asyncio.sleep(.01)
            assert catalog.admission_stats()["active"] == 0
            assert read(state, identity).json()["scanned"] == 100
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    asyncio.run(scenario())


def test_memory_limit_retains_existing_prefix(search_environment, monkeypatch):
    state = search_environment
    identity = create(state).json()["session_id"]
    monkeypatch.setattr(search_sessions, "MAX_SESSION_BYTES", 1)
    data = more(state, identity, category_id="161").json()
    assert data["scanned"] == 100 and data["error_code"] == "online_search_limit_reached"
    assert not data["can_continue"] and data["total"] is None


def test_no_match_in_complete_empty_source_is_final(search_environment):
    state = search_environment
    state.rows = []
    data = create(state).json()
    assert data["status"] == "complete" and data["total"] == 0 and data["scanned"] == 0
    assert not data["has_more"] and not data["can_continue"]
    assert len(state.calls) == 1


def test_lru_capacity_and_idle_cleanup_are_bounded(search_environment, monkeypatch):
    state = search_environment
    monkeypatch.setattr(search_sessions, "MAX_SESSIONS", 2)
    first = create(state).json()["session_id"]
    second = create(state, refresh=True).json()["session_id"]
    assert read(state, first).status_code == 200
    third = create(state, refresh=True).json()["session_id"]
    assert read(state, second).status_code == 410
    assert read(state, first).status_code == read(state, third).status_code == 200


@pytest.mark.parametrize("body", [
    {"category_scope": "all"}, {"category_id": "https://bad.example"}, {"year": "0000"},
    {"offset": -1}, {"limit": 101}, {"limit": True}, {"q": ""}, {"q": "边军\n"},
    {"refresh": "true"}, {"unexpected": True},
])
def test_invalid_create_is_rejected_before_provider_io(search_environment, body):
    response = create(search_environment, **body)
    assert response.status_code == 422, response.text
    assert search_environment.calls == []


def test_unverified_source_does_not_advertise_or_accept_filters(environment):
    source = environment.client.get("/api/online/catalog/sources").json()["items"][0]
    assert source["capabilities"]["search_filters"] is False
    response = environment.client.post(PATH, json={"source_id": source["id"], "q": "边军"})
    assert response.status_code == 422 and response.json()["code"] == "online_search_filter_unsupported"
    assert environment.requests == []


def test_protected_normal_device_can_search_without_management_but_children_cannot(search_environment):
    state = search_environment
    configured = state.client.post("/api/viewing/password", json={"new_password": "isolated-search-password"})
    assert configured.status_code == 200, configured.text
    assert state.client.get("/api/viewing/session").json()["management_unlocked"] is False
    response = create(state)
    assert response.status_code == 200, response.text
    identity = response.json()["session_id"]
    assert read(state, identity).status_code == 200
    assert more(state, identity, category_id="161").status_code == 200
    assert state.client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    assert create(state).status_code == 403
    assert read(state, identity).status_code == 403
    assert more(state, identity).status_code == 403


def test_session_posts_preserve_existing_write_token_gate(search_environment):
    state = search_environment
    store.set_setting("jzmedia_token", "isolated-search-api-token")
    assert create(state).status_code == 401 and state.calls == []
    response = state.client.post(PATH, headers={"X-Api-Token": "isolated-search-api-token"},
                                 json={"source_id": state.source["id"], "q": "边军"})
    assert response.status_code == 200, response.text
    identity = response.json()["session_id"]
    assert read(state, identity).status_code == 200
    assert more(state, identity, category_id="161").status_code == 401
    assert len(state.calls) == 1


@pytest.mark.parametrize("url,action,supported", [
    ("https://cj.lziapi.com/api.php/provide/vod/", "list", True),
    ("https://cj.lziapi.com:443/api.php/provide/vod", "detail", True),
    ("http://cj.lziapi.com/api.php/provide/vod/", "list", False),
    ("https://cj.lzcaiji.com/api.php/provide/vod/", "list", False),
    ("https://bfzyapi.com/api.php/provide/vod/", "list", False),
    ("https://cj.lziapi.com/api.php/provide/vod/?x=1", "list", False),
    ("https://cj.lziapi.com/api.php/provide/vod/", "videolist", False),
])
def test_search_capability_is_limited_to_verified_endpoint_and_actions(url, action, supported):
    assert maccms.supports_search_filters({"provider": "maccms", "api_base_url": url,
                                            "provider_options": {"search_action": action}}) is supported


@pytest.mark.parametrize("method,path,expected", [
    ("POST", PATH, False), ("GET", PATH, True),
    ("GET", PATH + "/srch_" + "a" * 32, False),
    ("POST", PATH + "/srch_" + "a" * 32 + "/more", False),
    ("DELETE", PATH + "/srch_" + "a" * 32, True),
    ("POST", PATH + "/srch_" + "a" * 32 + "/other", True),
])
def test_new_management_allowlist_is_exact(method, path, expected):
    assert viewing.management_path(path, method) is expected
