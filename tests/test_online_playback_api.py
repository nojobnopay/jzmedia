"""Owned online HTTP sessions use only isolated catalogue and fake providers."""

from types import SimpleNamespace
import time

from fastapi.responses import Response
from fastapi.testclient import TestClient
import pytest

from app import library_paths, store, viewing
from app.config import settings
from app.main import app
from app.network.targets import TargetPolicy
from app.online import gateway, providers, service
from app.online.sessions import SessionRegistry
from app.store import online


UPSTREAM = "https://cdn.example/media.mp4?sig=private-upstream-secret"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "online-playback.db"))
    monkeypatch.setattr(store._base, "ensure_dirs", lambda: None)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    library_paths.invalidate_cache()
    store.init_db()
    registry = SessionRegistry()
    monkeypatch.setattr(service, "registry", registry)
    factories, deliveries = [], []

    class FakeClient:
        def __init__(self, source_id, header_policy):
            self.snapshot = SimpleNamespace(source_id=source_id)
            self.targets = TargetPolicy(source_id, ["cdn.example"], resolver=lambda _h, _p: ("8.8.8.8",))
            self.header_policy, self.closed = header_policy, False

        def close(self):
            self.closed = True

    def client_factory(source_id, purpose, *, header_policy=None):
        assert purpose == "media"
        client = FakeClient(source_id, header_policy)
        factories.append(client)
        return client

    async def deliver(request, selected_registry, session, resource):
        assert selected_registry is registry
        deliveries.append((request.method, session, resource))
        return Response(b"isolated media", media_type="video/mp4")

    monkeypatch.setattr(service, "create_online_client", client_factory)
    monkeypatch.setattr(gateway, "stream_response", deliver)
    candidate = TestClient(app)
    try:
        yield SimpleNamespace(client=candidate, registry=registry, factories=factories, deliveries=deliveries)
    finally:
        candidate.close()
        registry.close_all()
        library_paths.invalidate_cache()


@pytest.fixture
def client(environment):
    candidate = environment.client
    assert candidate.get("/api/viewing/session").status_code == 200
    return candidate


@pytest.fixture
def identity():
    source = online.create_online_source({"name": "Isolated provider", "provider": "maccms",
                                          "api_base_url": "https://source.example/api/", "allowed_hosts": ["cdn.example"],
                                          "enabled": True})
    item = online.upsert_online_item(source["id"], "movie-1", "movie", "Isolated movie")
    online.update_online_settings(True)
    return {"source_id": source["id"], "item_id": item["id"], "kind": "movie"}


@pytest.fixture
def resolved(monkeypatch):
    value = providers.ResolvedPlayback(UPSTREAM, "mp4", duration_seconds=24.0)
    monkeypatch.setattr(providers, "resolve", lambda _identity: value)
    return value


def assert_private_response(response, status):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "private-upstream-secret" not in response.text


def test_creation_requires_an_existing_device_session_even_without_protection(environment, identity, resolved):
    response = environment.client.post("/api/online/sessions", json=identity)
    assert_private_response(response, 401)
    assert response.json()["code"] == "viewing_session_required"
    assert environment.factories == []


def test_unavailable_provider_does_not_create_a_media_client(client, identity, environment, monkeypatch):
    def unavailable(_identity):
        raise providers.ProviderError("provider_not_ready")
    monkeypatch.setattr(providers, "resolve", unavailable)
    response = client.post("/api/online/sessions", json=identity)
    assert_private_response(response, 503)
    assert response.json()["code"] == "provider_not_ready"
    assert environment.factories == []
    assert environment.registry.stats()["sessions"] == 0


def test_source_changed_between_resolution_and_registration_reclaims_new_session(client, identity, environment, monkeypatch):
    from app.online import catalog
    source = online.get_online_source(identity["source_id"])
    value = providers.ResolvedPlayback(UPSTREAM, "mp4", source_fingerprint=catalog._version(source))
    monkeypatch.setattr(providers, "resolve", lambda _identity: value)
    original_factory = service.create_online_client

    def changing_factory(source_id, purpose, **kwargs):
        reader = original_factory(source_id, purpose, **kwargs)
        online.update_online_source(source_id, {"api_base_url": "https://source.example/changed/"})
        return reader

    monkeypatch.setattr(service, "create_online_client", changing_factory)
    response = client.post("/api/online/sessions", json=identity)
    assert_private_response(response, 409)
    assert response.json()["code"] == "online_source_changed"
    assert len(environment.factories) == 1 and environment.factories[0].closed
    assert environment.registry.stats()["sessions"] == 0


@pytest.mark.parametrize("gate", ["feature", "source"])
def test_feature_and_source_gates_precede_provider_resolution(client, identity, monkeypatch, gate, environment):
    if gate == "feature":
        online.update_online_settings(False)
    else:
        online.update_online_source(identity["source_id"], {"enabled": False})

    def forbidden(_identity):
        pytest.fail("disabled online playback must not resolve a provider")

    monkeypatch.setattr(providers, "resolve", forbidden)
    response = client.post("/api/online/sessions", json=identity)
    assert_private_response(response, 503)
    assert environment.factories == []


@pytest.mark.parametrize("transport", ["mp4", "hls"])
def test_descriptor_exposes_only_same_origin_passthrough_and_owned_delivery(client, identity, monkeypatch, transport, environment):
    monkeypatch.setattr(providers, "resolve", lambda _identity: providers.ResolvedPlayback(
        UPSTREAM, transport, seekable=True, duration_seconds=24.0))
    response = client.post("/api/online/sessions", json=identity)
    assert_private_response(response, 201)
    descriptor = response.json()
    assert descriptor["processing"] == "passthrough"
    assert descriptor["transport"] == transport
    assert descriptor["time_offset_seconds"] == 0
    assert descriptor["identity"] == {**identity, "episode_id": None}
    assert descriptor["duration_seconds"] == 24
    assert descriptor["expires_at"] > time.time()
    assert descriptor["media_url"] == f'/api/online/sessions/{descriptor["session_id"]}/media'
    assert "cdn.example" not in response.text
    assert_private_response(client.get(descriptor["media_url"]), 200)
    assert_private_response(client.head(descriptor["media_url"]), 200)
    assert [request[0] for request in environment.deliveries] == ["GET", "HEAD"]
    session = environment.deliveries[0][1]
    resource_path = session.entry.path(session.id)
    assert_private_response(client.get(resource_path), 200)
    assert_private_response(client.head(resource_path), 200)
    assert environment.deliveries[-1][2] is session.entry
    assert_private_response(client.delete(f'/api/online/sessions/{session.id}'), 204)
    assert environment.factories[0].closed
    assert_private_response(client.get(descriptor["media_url"]), 404)


def test_other_device_cannot_read_or_close_a_session_with_its_opaque_id(client, identity, resolved, environment):
    descriptor = client.post("/api/online/sessions", json=identity).json()
    sid = descriptor["session_id"]
    # Do not enter lifespan: no scanner, media workers, device or network tests.
    stranger = TestClient(app)
    no_session = TestClient(app)
    try:
        stranger.get("/api/viewing/session")
        for method, path in (("GET", descriptor["media_url"]), ("HEAD", descriptor["media_url"]),
                             ("DELETE", f"/api/online/sessions/{sid}")):
            assert_private_response(stranger.request(method, path), 404)
        assert_private_response(no_session.get(descriptor["media_url"]), 401)
        assert environment.registry.stats()["sessions"] == 1
        assert_private_response(client.get(descriptor["media_url"]), 200)
    finally:
        stranger.close()
        no_session.close()


@pytest.mark.parametrize("extra", [{"url": UPSTREAM}, {"headers": {"Authorization": "private-upstream-secret"}},
                                   {"processing": "transcode"}, {"identity": {}}])
def test_http_creation_cannot_accept_urls_headers_or_processing_options(client, identity, extra, environment):
    response = client.post("/api/online/sessions", json={**identity, **extra})
    assert_private_response(response, 422)
    assert response.json()["code"] == "invalid_online_playback_request"
    assert environment.factories == []


def test_identity_relationships_are_checked_before_provider_resolution(client, identity, resolved, environment):
    other_source = online.create_online_source({"name": "Other", "provider": "open_catalog",
                                                "api_base_url": "https://other.example/api/", "enabled": True})
    show = online.upsert_online_item(identity["source_id"], "show-1", "tv")
    other_show = online.upsert_online_item(identity["source_id"], "show-2", "tv")
    episode = online.upsert_online_episode(show["id"], "ep-1")
    invalid = [
        {**identity, "source_id": other_source["id"]},
        {**identity, "item_id": show["id"]},
        {**identity, "kind": "episode", "episode_id": episode["id"]},
        {**identity, "item_id": other_show["id"], "kind": "episode", "episode_id": episode["id"]},
    ]
    for body in invalid:
        assert_private_response(client.post("/api/online/sessions", json=body), 404)
    assert environment.factories == []
    valid = {**identity, "item_id": show["id"], "kind": "episode", "episode_id": episode["id"]}
    assert_private_response(client.post("/api/online/sessions", json=valid), 201)


def test_online_creation_and_delete_use_global_write_token_but_media_read_does_not(client, identity, resolved):
    store.set_setting("jzmedia_token", "isolated-api-write-token")
    assert_private_response(client.post("/api/online/sessions", json=identity), 401)
    response = client.post("/api/online/sessions", json=identity, headers={"x-api-token": "isolated-api-write-token"})
    assert_private_response(response, 201)
    descriptor = response.json()
    assert_private_response(client.get(descriptor["media_url"]), 200)
    path = f'/api/online/sessions/{descriptor["session_id"]}'
    assert_private_response(client.delete(path), 401)
    assert_private_response(client.delete(path, headers={"x-api-token": "isolated-api-write-token"}), 204)


def test_children_cannot_create_or_access_external_online_content(client, identity, resolved, environment):
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    descriptor = client.post("/api/online/sessions", json=identity).json()
    assert client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    assert_private_response(client.post("/api/online/sessions", json=identity), 403)
    assert_private_response(client.get(descriptor["media_url"]), 403)
    service.sweep()
    assert environment.registry.stats()["sessions"] == 0
    assert environment.factories[0].closed


def test_new_viewing_generation_revokes_an_existing_online_session(client, identity, resolved, environment):
    descriptor = client.post("/api/online/sessions", json=identity).json()
    assert client.post("/api/viewing/lock").status_code == 200
    assert_private_response(client.get(descriptor["media_url"]), 401)
    assert environment.factories[0].closed
    assert environment.registry.stats()["sessions"] == 0


@pytest.mark.parametrize("change", ["source", "feature", "revision", "private_expiry"])
def test_periodic_sweep_revokes_source_and_access_gates(client, identity, resolved, environment, change):
    if change == "private_expiry":
        assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
        assert client.post("/api/viewing/mode", json={"mode": "private", "password": "123456"}).status_code == 200
    descriptor = client.post("/api/online/sessions", json=identity).json()
    if change == "source":
        online.update_online_source(identity["source_id"], {"enabled": False})
    elif change == "feature":
        online.update_online_settings(False)
    else:
        with store._base._conn() as connection:
            if change == "revision":
                connection.execute("UPDATE protection_settings SET revision=revision+1 WHERE id=1")
            else:
                connection.execute("UPDATE viewing_sessions SET last_activity=?,playback_until=0 WHERE token_hash=?",
                                   (time.time() - viewing.IDLE_SECONDS - 1,
                                    viewing.token_hash(client.cookies.get(viewing.COOKIE))))
    service.sweep()
    assert environment.registry.stats()["sessions"] == 0
    assert environment.factories[0].closed
    assert descriptor["session_id"]


@pytest.mark.parametrize("change", ["source", "generation"])
def test_gate_changes_during_provider_resolution_cannot_publish_a_live_session(client, identity, monkeypatch, environment, change):
    def changed(_identity):
        if change == "source":
            online.update_online_source(identity["source_id"], {"enabled": False})
        else:
            with store._base._conn() as connection:
                connection.execute("UPDATE viewing_sessions SET generation=generation+1")
        return providers.ResolvedPlayback(UPSTREAM, "mp4")

    monkeypatch.setattr(providers, "resolve", changed)
    assert_private_response(client.post("/api/online/sessions", json=identity), 503 if change == "source" else 401)
    assert environment.registry.stats()["sessions"] == 0
    assert environment.factories[0].closed


def test_provider_exception_text_never_reaches_the_api(client, identity, monkeypatch, environment):
    def failed(_identity):
        raise ValueError(UPSTREAM)

    monkeypatch.setattr(providers, "resolve", failed)
    response = client.post("/api/online/sessions", json=identity)
    assert_private_response(response, 502)
    assert response.json()["code"] == "provider_resolution_failed"
    assert environment.factories == []


def test_upstream_reference_is_hidden_from_server_result_representation():
    assert "private-upstream-secret" not in repr(providers.ResolvedPlayback(UPSTREAM, "mp4"))
