"""Related metadata remains scoped, ephemeral, bounded and URL-free."""

from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app import store
from app.main import app
from app.network.guard import NetworkOperation
from app.online import artwork, maccms, related, service
from app.online.providers import ProviderError
from app.store import online, online_network
from app import viewing
from test_online_catalog import environment as environment


@pytest.fixture
def related_environment(environment, monkeypatch):
    monkeypatch.setattr(related, "cache", related.RelatedCache())
    monkeypatch.setattr(artwork, "registry", artwork.ArtworkRegistry())
    base = {**environment.show, "type_id": "12", "type_pid": "2", "vod_actor": "演员甲,演员乙",
            "vod_director": "导演甲", "vod_class": "剧情", "vod_pic": "https://cdn.example/poster.jpg?sig=private"}
    rows = [{**base, "vod_id": f"s{number}", "vod_name": f"青山书院第{number}季", "vod_year": str(2020 + number)}
            for number in (4, 1, 3, 2)]
    rows += [{**base, "vod_id": "unmarked", "vod_name": "青山书院"},
             {**base, "vod_id": "cross-category", "vod_name": "青山书院第一季", "type_id": "1"},
             {**base, "vod_id": "similar", "vod_name": "同类其它剧集"},
             {**base, "vod_id": "wrong-category", "vod_name": "其它电影", "type_id": "1"}]
    state = SimpleNamespace(**vars(environment), rows=rows, fail=False, calls=[])

    def fetch(_source, params):
        state.calls.append(dict(params))
        if "ids" in params:
            return {"code": 1, "list": [row for row in rows if row["vod_id"] == params["ids"]]}
        if state.fail:
            raise ProviderError("provider_upstream_error")
        selected = [row for row in rows if params["wd"] in row["vod_name"]] if "wd" in params else rows
        return {"code": 1, "list": selected, "page": 1, "pagecount": 1, "total": len(selected)}

    monkeypatch.setattr(maccms, "fetch", fetch)
    return state


def read(state, selected="s2", client=None, **extra):
    return (client or state.client).get("/api/online/catalog/related",
        params={"source_id": state.source["id"], "external_id": selected, **extra})


def capture(state):
    return service.capture_access(SimpleNamespace(headers={}, cookies={viewing.COOKIE: state.client.cookies.get(viewing.COOKIE)}))


def test_related_uses_three_metadata_calls_no_candidate_details_or_database_writes(related_environment):
    state = related_environment
    response = read(state)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["status"] == "ready" and data["current_season"] == 2 and data["external_id"] == "s2"
    assert [row["external_id"] for row in data["series"]] == ["s1", "s3", "s4"]
    assert {row["external_id"] for row in data["name_related"]} == {"unmarked", "cross-category"}
    assert [row["external_id"] for row in data["related"]] == ["similar"]
    assert len(state.calls) == 3 and sum("ids" in call for call in state.calls) == 1
    assert [call for call in state.calls if "wd" in call] == [{"ac": "list", "wd": "青山书院", "pg": "1"}]
    assert [call for call in state.calls if "t" in call] == [{"ac": "detail", "pg": "1", "t": "12"}]
    assert "api.example" not in response.text and "cdn.example" not in response.text and "sig=" not in response.text
    assert all(row["poster_path"].startswith("/api/online/catalog/artwork/") for row in data["series"])
    with store._base._conn() as connection:
        for table in ("online_items", "online_episodes", "movies", "tv_episodes", "playback_progress"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_cache_hit_renews_evicted_poster_leases_and_preserves_snapshot(related_environment):
    state = related_environment
    first = read(state).json()
    before = len(state.calls)
    artwork.registry.clear()
    second = read(state).json()
    assert len(state.calls) == before
    assert second["series"][0]["poster_path"] != first["series"][0]["poster_path"]
    path = second["series"][0]["poster_path"]
    lease = artwork.registry.lookup(state.source["id"], path.rsplit("/", 1)[1])
    assert lease.url == "https://cdn.example/poster.jpg?sig=private"
    assert related.cache.stats()["entries"] == 1


def test_cache_key_changes_for_device_all_network_purposes_and_source_metadata(related_environment):
    state = related_environment
    assert read(state).status_code == 200
    assert len(state.calls) == 3
    other = TestClient(app)
    try:
        other.get("/api/viewing/session")
        assert read(state, client=other).status_code == 200
    finally:
        other.close()
    assert len(state.calls) == 6
    online_network.update_online_network_default({"mode": "direct"})
    assert read(state).status_code == 200
    assert len(state.calls) == 9
    online.update_online_source(state.source["id"], {"name": "Changed source label"})
    renamed = read(state)
    assert renamed.status_code == 200 and renamed.json()["source"]["name"] == "Changed source label"
    assert len(state.calls) == 12


def test_cache_hit_still_checks_protection_and_feature_source_access(related_environment):
    state = related_environment
    assert read(state).status_code == 200
    online.update_online_source(state.source["id"], {"enabled": False})
    assert read(state).status_code == 503
    online.update_online_source(state.source["id"], {"enabled": True})
    online.update_online_settings(False)
    assert read(state).status_code == 503
    online.update_online_settings(True)
    assert state.client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    assert state.client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    assert read(state).status_code == 403
    assert len(state.calls) == 3


def test_protected_normal_device_can_read_related_without_management_but_children_cannot(related_environment):
    state = related_environment
    assert state.client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    normal = TestClient(app)
    try:
        session = normal.get("/api/viewing/session").json()
        assert session["enabled"] is True and session["mode"] == "normal"
        assert session["management_unlocked"] is False
        response = read(state, client=normal)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ready"
        assert normal.get("/api/online/sources").status_code == 403
        # The exception applies to this exact read-only route. It must not
        # accidentally grant management access to writes or nested paths.
        assert normal.post("/api/online/catalog/related", params={
            "source_id": state.source["id"], "external_id": "s2"}).status_code == 403
        assert normal.get("/api/online/catalog/related/extra").status_code == 403
        assert normal.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
        denied = read(state, client=normal)
        assert denied.status_code == 403 and denied.json()["code"] == "online_children_unavailable"
        assert normal.get("/api/online/sources").status_code == 403
        assert len(state.calls) == 3
    finally:
        normal.close()


def test_failures_are_not_cached_and_retry_is_an_explicit_new_attempt(related_environment):
    state = related_environment
    state.fail = True
    assert read(state).status_code == 502
    assert read(state).status_code == 502
    assert len(state.calls) == 6
    assert related.cache.stats() == {"entries": 0, "bytes": 0}
    state.fail = False
    assert read(state).status_code == 200
    assert len(state.calls) == 9


def test_source_or_network_change_during_request_rejects_snapshot_and_does_not_cache(related_environment, monkeypatch):
    state = related_environment
    original = maccms.search

    def change_network(*args):
        result = original(*args)
        online_network.update_online_network_default({"mode": "direct"})
        return result

    monkeypatch.setattr(maccms, "search", change_network)
    response = read(state)
    assert response.status_code == 409 and response.json()["code"] == "online_source_changed"
    assert related.cache.stats()["entries"] == 0


def test_unsupported_source_is_an_explicit_capability_without_upstream_io(related_environment, monkeypatch):
    state = related_environment
    monkeypatch.setattr(related, "supports_related", lambda _source: False)
    response = read(state)
    assert response.status_code == 200
    assert response.json()["status"] == "unsupported"
    assert response.json()["series"] == [] and state.calls == []
    assert state.client.get("/api/online/catalog/sources").json()["items"][0]["capabilities"]["related"] is False


@pytest.mark.parametrize("query", [
    "external_id=s2&title=caller-title", "external_id=s2&url=https://bad.invalid/", "external_id=s2&external_id=s1",
    "external_id=https%3A%2F%2Fbad.invalid%2F", "external_id=s2%0A", "external_id=", "external_id=s2&page=1",
])
def test_only_one_pair_of_stable_ids_is_accepted_before_provider_io(related_environment, query):
    state = related_environment
    response = state.client.get(f"/api/online/catalog/related?source_id={state.source['id']}&{query}")
    assert response.status_code == 422 and response.json()["code"] == "invalid_online_catalog_request"
    assert state.calls == []


def test_related_requires_device_session_even_with_unprotected_installation(related_environment):
    other = TestClient(app)
    try:
        assert read(related_environment, client=other).status_code == 401
    finally:
        other.close()
    assert related_environment.calls == []


def test_related_child_deadline_is_bounded_and_parent_cancellation_is_not_deferred(related_environment, monkeypatch):
    state = related_environment
    access = capture(state)
    import time
    from app.network.guard import current_operation
    parent = NetworkOperation(time.monotonic() + 30)

    def cancel(_source, _params):
        child = current_operation()
        assert child is not parent and 0 < child.remaining() <= 8
        parent.abort()
        child.check()

    monkeypatch.setattr(maccms, "fetch", cancel)
    with pytest.raises(ProviderError) as error:
        related.read(access, state.source["id"], "s2", operation=parent)
    assert error.value.category == "provider_cancelled"
    assert related.cache.stats()["entries"] == 0


def test_cache_is_ttl_lru_count_and_byte_bounded_and_copies_readers():
    now = [100.0]
    cache = related.RelatedCache(clock=lambda: now[0], max_entries=2, max_bytes=240)
    value = {"status": "ready", "series": [{"title": "snapshot"}]}
    cache.put("a", value)
    cache.get("a")["series"][0]["title"] = "mutated"
    assert cache.get("a")["series"][0]["title"] == "snapshot"
    cache.put("b", value)
    cache.get("a")
    cache.put("c", value)
    assert cache.get("b") is None and cache.stats()["entries"] == 2
    cache.put("huge", {"status": "ready", "text": "x" * 241})
    cache.put("failed", {"status": "deferred"})
    assert cache.get("huge") is None and cache.get("failed") is None
    now[0] += 120
    assert cache.get("a") is None and cache.stats() == {"entries": 0, "bytes": 0}
    with pytest.raises(ValueError):
        related.RelatedCache(max_entries=33)
    with pytest.raises(ValueError):
        related.RelatedCache(max_bytes=related.MAX_CACHE_BYTES + 1)
