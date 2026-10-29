"""Foreground API leases reuse real loopback sockets without reading media."""

import threading

from fastapi.testclient import TestClient
import pytest

from app.network.transport import PinnedBackend
from app.online import api_pool, health
from scripts.online_p2.server import FIXTURE_TOKEN
from test_online_catalog_fixture import (add_source, client as client,
                                        detail, prepared as prepared)


@pytest.fixture(autouse=True)
def clean_api_pool():
    api_pool.clear()
    yield
    api_pool.clear()
    assert api_pool.pool.stats()["idle"] == 0
    assert api_pool.pool.stats()["active"] == 0


@pytest.fixture
def api_dials(monkeypatch):
    calls, lock = [], threading.Lock()
    connect = PinnedBackend.connect_tcp

    def counted(backend, *args, **kwargs):
        with lock:
            calls.append(backend._snapshot.purpose)
        return connect(backend, *args, **kwargs)

    monkeypatch.setattr(PinnedBackend, "connect_tcp", counted)
    return calls


def browse(client, source):
    response = client.get("/api/online/catalog/browse", params={"source_id": source["id"]})
    assert response.status_code == 200, response.text
    return response.json()


def search(client, source):
    response = client.get("/api/online/catalog/search", params={"source_id": source["id"], "q": "合成"})
    assert response.status_code == 200, response.text
    return response.json()


def quiet(client):
    metrics = client.get("/api/p2/metrics").json()
    assert metrics["pending_requests"] == metrics["catalogue_active"] == metrics["catalogue_owners"] == 0
    assert metrics["registry"]["sessions"] == metrics["registry"]["active_requests"] == 0
    assert metrics["registry"]["reserved_bytes"] == 0
    assert metrics["created_sessions"] == []
    assert metrics["local_rows"] == {"movies": 0, "tv_episodes": 0, "playback_progress": 0}
    assert all(call["kind"] == "api" and not call.get("app_credentials") for call in metrics["origin_calls"])
    assert all(call["purpose"] == "api" for call in metrics["client_calls"])
    assert api_pool.pool.stats()["active"] == 0
    return metrics


def test_browse_search_and_detail_share_one_real_socket_within_the_same_viewing_scope(client, api_dials):
    source = add_source(client)
    assert browse(client, source)["items"]
    assert search(client, source)["items"]
    assert detail(client, source)["item"]["title"] == "合成电影"

    metrics = quiet(client)
    assert len(metrics["origin_calls"]) == 3
    assert api_dials == ["api"], "Three separate HTTP queries reuse the same actual connection"
    assert len(metrics["client_calls"]) == 3, "Each lease still rebuilds the current configuration and trust snapshot"
    assert api_pool.pool.stats()["idle"] == 1


def test_transient_status_closes_the_borrowed_socket_before_the_single_retry(client, api_dials):
    source = add_source(client)
    browse(client, source)
    assert api_dials == ["api"]
    assert client.post("/api/p2/scenario", json={"metadata_failures_remaining": 1,
        "metadata_failure_status": 503}).status_code == 200

    search(client, source)
    metrics = quiet(client)
    assert [call["metadata_status"] for call in metrics["origin_calls"]] == [200, 503, 200]
    assert api_dials == ["api", "api"], "The retry establishes a fresh connection after the failed lease closes"
    detail(client, source)
    assert api_dials == ["api", "api"], "Only the successful retry connection can be reused"
    quiet(client)


def test_malformed_metadata_discards_the_connection_without_an_automatic_retry(client, api_dials):
    source = add_source(client)
    browse(client, source)
    assert client.post("/api/p2/scenario", json={"next_mode": "malformed"}).status_code == 200
    failed = client.get("/api/online/catalog/search", params={"source_id": source["id"], "q": "合成"})
    assert failed.status_code == 502 and failed.json()["code"] == "invalid_provider_response"
    assert len(quiet(client)["origin_calls"]) == 2
    assert api_dials == ["api"]
    assert api_pool.pool.stats()["idle"] == 0

    search(client, source)
    assert api_dials == ["api", "api"]
    assert len(quiet(client)["origin_calls"]) == 3


def test_source_and_network_changes_cannot_borrow_an_old_configuration_socket(client, api_dials):
    source = add_source(client)
    browse(client, source)
    assert client.patch(f"/api/online/sources/{source['id']}", json={"name": "隔离配置变更"}).status_code == 200
    search(client, source)
    assert api_dials == ["api", "api"]

    profile = client.post("/api/online/network/profiles", json={"name": "隔离API出口", "mode": "direct"})
    assert profile.status_code == 201, profile.text
    assert client.patch(f"/api/online/sources/{source['id']}/network", json={
        "api": {"mode": "direct", "profile_id": profile.json()["id"]}}).status_code == 200
    search(client, source)
    assert api_dials == ["api", "api", "api"]

    assert client.patch(f"/api/online/network/profiles/{profile.json()['id']}",
        json={"read_timeout_seconds": 2.0}).status_code == 200
    detail(client, source)
    assert api_dials == ["api", "api", "api", "api"]
    quiet(client)


def test_viewing_generation_and_another_device_have_separate_socket_leases(client, api_dials):
    source = add_source(client)
    search(client, source)
    generation = client.get("/api/viewing/session").json()["generation"]
    changed = client.post("/api/viewing/mode", json={"mode": "normal"})
    assert changed.status_code == 200 and changed.json()["generation"] > generation
    search(client, source)
    assert api_dials == ["api", "api"]

    stranger = TestClient(client.app)
    stranger.headers["X-Api-Token"] = FIXTURE_TOKEN
    try:
        assert stranger.get("/api/viewing/session").status_code == 200
        search(stranger, source)
        assert api_dials == ["api", "api", "api"]
        search(client, source)
        assert api_dials == ["api", "api", "api"], "The original device can still borrow its own current-generation socket"
    finally:
        stranger.close()
    quiet(client)


def test_a_pool_hit_rebinds_the_current_health_observation_without_extra_probes(client, api_dials):
    source = add_source(client)
    detail(client, source)
    assert api_dials == ["api"]
    health.registry.clear()
    path, params = "/api/online/catalog/health", {"source_id": source["id"]}
    assert client.get(path, params=params).json()["purposes"]["api"]["state"] == "unknown"
    before = len(quiet(client)["origin_calls"])

    search(client, source)
    assert api_dials == ["api"], "The health update is exercised on a real connection-pool hit"
    observed = client.get(path, params=params)
    assert observed.status_code == 200
    assert observed.json()["purposes"]["api"]["state"] == "success"
    assert observed.json()["purposes"]["media"]["state"] == "unknown"
    assert "fixture.invalid" not in observed.text
    assert len(quiet(client)["origin_calls"]) == before + 1, "Reading health never starts a probe"
