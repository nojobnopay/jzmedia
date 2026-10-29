"""Poster leases authorize source-owned bytes without a public URL proxy."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store, viewing
from app.main import app
from app.online import artwork, health, maccms, service
from app.store import online, online_network
from app.network.guard import NetworkOperation


JPEG = b"\xff\xd8\xff\xe0\x00\x04JZ\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00" * 17
WEBP = b"RIFF" + (12).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 4
URL = "https://images.example/poster.jpg?signature=private-image-value"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "artwork.db"))
    library_paths.invalidate_cache()
    store.init_db()
    source = online.create_online_source({"name": "Isolated poster source", "provider": "maccms",
        "api_base_url": "https://api.example/provide/vod/", "allowed_hosts": ["images.example"], "enabled": True})
    online.update_online_settings(True)
    state = SimpleNamespace(source=source, status=200, headers={"Content-Type": "image/jpeg"},
                            chunks=[JPEG], requests=[], readers=[], access_valid=True,
                            before_factory=None, before_chunk=None, real_access_check=service._current_access)
    monkeypatch.setattr(artwork, "registry", artwork.ArtworkRegistry())
    monkeypatch.setattr(artwork, "_readers", threading.BoundedSemaphore(2))

    def access_check(_access):
        if not state.access_valid:
            raise service.PlaybackError("online_access_revoked", 401)
    monkeypatch.setattr(service, "_current_access", access_check)

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            if state.before_chunk:
                state.before_chunk()
            yield from state.chunks

    class Reader:
        def __init__(self, source_id, purpose):
            assert source_id == source["id"] and purpose == "image"
            if state.before_factory:
                state.before_factory()
            snapshot = online_network.load_online_network_state(source_id)
            fingerprint, mode = health.configuration(snapshot, purpose)
            self._online_health_context = health.ReadContext(source_id, purpose, fingerprint, mode)
            self.closed = False
            state.readers.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        @contextmanager
        def stream(self, method, url, **options):
            state.requests.append((method, url, options))
            response = httpx.Response(state.status, headers=state.headers, stream=Body())
            try:
                yield response
            finally:
                response.close()

    monkeypatch.setattr(artwork, "create_online_client", Reader)
    try:
        yield state
    finally:
        library_paths.invalidate_cache()


def lease(environment):
    path = artwork.register(environment.source, URL)
    assert path.startswith("/api/online/catalog/artwork/" + environment.source["id"] + "/")
    assert "signature" not in path and "images.example" not in path
    return path.rsplit("/", 1)[1]


@pytest.mark.parametrize("url", ["", "file:///tmp/private", "https://other.example/poster.jpg",
                                     "https://user:password@images.example/p.jpg", "https://images.example/p.jpg#fragment",
                                     "https://images.example/p%0d%0a.jpg", "http://127.0.0.1/private"])
def test_registration_rejects_unapproved_addresses_without_dns(environment, monkeypatch, url):
    def no_dns(*_args, **_kwargs):
        pytest.fail("poster registration must not resolve DNS")
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    assert artwork.register(environment.source, url) == ""
    assert environment.requests == []


def test_registration_is_local_and_deduplicates_only_the_same_configuration(environment, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: pytest.fail("unexpected DNS"))
    first = lease(environment)
    assert lease(environment) == first
    assert environment.requests == []
    online_network.update_online_network_default({"mode": "direct"})
    assert lease(environment) != first


def test_a_page_of_poster_registrations_uses_one_db_snapshot(environment, monkeypatch):
    original = artwork.load_online_network_state
    loads = []
    def capture(source_id):
        loads.append(source_id)
        return original(source_id)
    monkeypatch.setattr(artwork, "load_online_network_state", capture)
    paths = artwork.register_many(environment.source, [URL, "https://other.example/rejected", URL + "&v=2"])
    assert paths[0] and paths[1] == "" and paths[2]
    assert loads == [environment.source["id"]]


@pytest.mark.parametrize("body,mime", [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")])
def test_fetch_uses_image_policy_and_returns_the_exact_bounded_image(environment, body, mime):
    environment.chunks = [body]
    environment.headers = {"Content-Type": mime, "Content-Length": str(len(body))}
    assert artwork.fetch(object(), environment.source["id"], lease(environment)) == (body, mime)
    assert environment.requests == [("GET", URL, {"headers": {"Accept": "image/jpeg,image/png,image/webp"}})]
    assert all(reader.closed for reader in environment.readers)


@pytest.mark.parametrize("headers,chunks,category", [
    ({"Content-Type": "image/svg+xml"}, [b"<svg/>"], "unsupported_online_artwork"),
    ({"Content-Type": "image/jpeg"}, [b"<html>not an image</html>"], "unsupported_online_artwork"),
    ({"Content-Type": "image/png"}, [JPEG], "unsupported_online_artwork"),
    ({"Content-Type": "image/jpeg", "Content-Encoding": "gzip"}, [JPEG], "unsupported_online_artwork"),
    ({"Content-Type": "image/jpeg", "Content-Length": "4,4"}, [JPEG], "invalid_online_artwork_length"),
    ({"Content-Type": "image/jpeg", "Content-Length": "20"}, [JPEG], "invalid_online_artwork_length"),
    ({"Content-Type": "image/jpeg", "Content-Length": str(artwork.MAX_BODY_BYTES + 1)}, [JPEG], "online_artwork_byte_limit"),
    ({"Content-Type": "image/jpeg"}, [b"\xff\xd8\xff" + b"x" * artwork.MAX_BODY_BYTES], "online_artwork_byte_limit"),
])
def test_fetch_rejects_active_content_mime_mismatches_encoding_and_unbounded_bodies(environment, headers, chunks, category):
    environment.headers, environment.chunks = headers, chunks
    with pytest.raises(artwork.ArtworkError) as error:
        artwork.fetch(object(), environment.source["id"], lease(environment))
    assert error.value.category == category
    assert "private-image-value" not in str(error.value)
    assert all(reader.closed for reader in environment.readers)


def test_expired_unknown_and_cross_source_tokens_never_open_a_reader(environment):
    now = [1.0]
    artwork.registry = artwork.ArtworkRegistry(ttl=1, clock=lambda: now[0])
    token = lease(environment)
    for source_id, selected in [("src_" + "f" * 32, token), (environment.source["id"], "0" * 32)]:
        with pytest.raises(artwork.ArtworkError):
            artwork.fetch(object(), source_id, selected)
    now[0] = 2.0
    with pytest.raises(artwork.ArtworkError):
        artwork.fetch(object(), environment.source["id"], token)
    assert environment.readers == []


def test_registry_bounds_count_total_url_bytes_and_age():
    now = [0]
    registry = artwork.ArtworkRegistry(capacity=2, url_budget=100, ttl=1, clock=lambda: now[0])
    source_id = "src_" + "a" * 32
    first = registry.register(source_id, "https://images.example/one.jpg", "v1").rsplit("/", 1)[1]
    registry.register(source_id, "https://images.example/two.jpg", "v1")
    registry.register(source_id, "https://images.example/three.jpg", "v1")
    with pytest.raises(artwork.ArtworkError):
        registry.lookup(source_id, first)
    assert len(registry._entries) == 2 and registry._url_bytes <= 100
    assert registry.register(source_id, "https://images.example/" + "x" * 100, "v1") == ""
    now[0] = 1
    registry.register(source_id, "https://images.example/fresh.jpg", "v1")
    assert len(registry._entries) == 1


@pytest.mark.parametrize("change", ["disabled_source", "disabled_feature", "source", "network"])
def test_source_feature_and_network_changes_invalidate_old_tokens_before_io(environment, change):
    token = lease(environment)
    if change == "disabled_source":
        online.update_online_source(environment.source["id"], {"enabled": False})
    elif change == "disabled_feature":
        online.update_online_settings(False)
    elif change == "source":
        online.update_online_source(environment.source["id"], {"name": "Renamed source"})
    else:
        online_network.update_online_network_default({"mode": "direct"})
    with pytest.raises(artwork.ArtworkError):
        artwork.fetch(object(), environment.source["id"], token)
    assert environment.readers == []


def test_a_factory_snapshot_race_is_rejected_before_requesting_bytes(environment):
    token = lease(environment)
    environment.before_factory = lambda: online_network.update_online_network_default({"mode": "direct"})
    with pytest.raises(artwork.ArtworkError) as error:
        artwork.fetch(object(), environment.source["id"], token)
    assert error.value.category == "online_artwork_stale"
    assert environment.requests == []


def test_revocation_during_read_never_returns_the_image(environment):
    token = lease(environment)
    environment.before_chunk = lambda: setattr(environment, "access_valid", False)
    with pytest.raises(service.PlaybackError) as error:
        artwork.fetch(object(), environment.source["id"], token)
    assert error.value.status == 401
    assert all(reader.closed for reader in environment.readers)


def test_source_change_during_read_never_returns_the_image(environment):
    token = lease(environment)
    environment.before_chunk = lambda: online.update_online_source(environment.source["id"], {"priority": 7})
    with pytest.raises(artwork.ArtworkError) as error:
        artwork.fetch(object(), environment.source["id"], token)
    assert error.value.category == "online_artwork_stale"


def test_absolute_deadline_is_shorter_than_the_catalogue_parent_and_cancellation_closes_reader(environment):
    token = lease(environment)
    operation = NetworkOperation(time.monotonic() + 30)
    deadline = []
    def cancel():
        deadline.append(operation.remaining())
        operation.abort()
    environment.before_chunk = cancel
    with pytest.raises(artwork.ArtworkError) as error:
        artwork.fetch(object(), environment.source["id"], token, operation=operation)
    assert error.value.category == "online_artwork_cancelled"
    assert 0 < deadline[0] <= 10
    assert all(reader.closed for reader in environment.readers)


def test_stalled_content_exceeds_the_same_absolute_read_deadline(environment):
    operation = NetworkOperation(time.monotonic() + 0.01)
    environment.before_chunk = lambda: time.sleep(0.02)
    with pytest.raises(artwork.ArtworkError) as error:
        artwork.fetch(object(), environment.source["id"], lease(environment), operation=operation)
    assert error.value.category == "online_artwork_timeout"


def test_two_network_readers_are_the_global_limit_and_waiter_cancellation_releases_its_worker(environment):
    token = lease(environment)
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = [0, 0]
    def block():
        with lock:
            active[0] += 1
            active[1] = max(active[0], active[1])
            if active[0] == 2:
                entered.set()
        try:
            assert release.wait(2)
        finally:
            with lock:
                active[0] -= 1
    environment.before_chunk = block
    operation = NetworkOperation(time.monotonic() + 10)
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(artwork.fetch, object(), environment.source["id"], token)
        second = executor.submit(artwork.fetch, object(), environment.source["id"], token)
        try:
            assert entered.wait(2)
            waiting = executor.submit(artwork.fetch, object(), environment.source["id"], token, operation=operation)
            operation.abort()
            with pytest.raises(artwork.ArtworkError):
                waiting.result(timeout=2)
            assert active[1] == 2
        finally:
            release.set()
        assert first.result(timeout=2) == (JPEG, "image/jpeg")
        assert second.result(timeout=2) == (JPEG, "image/jpeg")


def test_artwork_is_an_exact_get_viewing_route_not_a_general_admin_bypass():
    path = "/api/online/catalog/artwork/src_" + "1" * 32 + "/" + "2" * 32
    assert viewing.management_path(path, "GET") is False
    assert viewing.management_path("/api/online/catalog/browse", "GET") is False
    for candidate in [path + "/extra", path.replace("src_", "invalid_"), path[:-1],
                      "/api/online/catalog/artwork/https://private.example/"]:
        assert viewing.management_path(candidate, "GET") is True
    assert viewing.management_path(path, "POST") is True
    assert viewing.management_path("/api/online/catalog/browse", "POST") is True
    assert viewing.management_path("/api/online/source-templates", "GET") is True


def test_protected_normal_viewers_can_browse_and_read_posters_without_unlocking_management(environment, monkeypatch):
    monkeypatch.setattr(service, "_current_access", environment.real_access_check)
    monkeypatch.setattr(maccms, "fetch", lambda *_args, **_kwargs: {
        "code": 1, "list": [], "page": 1, "pagecount": 1, "total": 0, "class": []})
    path = artwork.register(environment.source, URL)
    client = TestClient(app)
    try:
        assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
        assert client.get("/api/viewing/session").json()["management_unlocked"] is False
        assert client.get("/api/online/sources").status_code == 403
        poster = client.get(path)
        assert poster.status_code == 200 and poster.content == JPEG
        assert poster.headers["cache-control"] == "no-store"
        assert poster.headers["x-content-type-options"] == "nosniff"
        assert client.get("/api/online/catalog/browse", params={"source_id": environment.source["id"]}).status_code == 200
        assert client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
        assert client.get(path).status_code == 403
        assert client.get("/api/online/catalog/browse", params={"source_id": environment.source["id"]}).status_code == 403
    finally:
        client.close()


@pytest.mark.parametrize("kind", ["browse", "artwork"])
def test_expired_private_viewers_cannot_start_new_catalogue_or_poster_reads(environment, monkeypatch, kind):
    monkeypatch.setattr(service, "_current_access", environment.real_access_check)
    path = artwork.register(environment.source, URL) if kind == "artwork" else "/api/online/catalog/browse?source_id=" + environment.source["id"]
    client = TestClient(app)
    try:
        assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
        assert client.post("/api/viewing/mode", json={"mode": "private", "password": "123456"}).status_code == 200
        with store._base._conn() as connection:
            connection.execute("UPDATE viewing_sessions SET last_activity=0,playback_until=0 WHERE mode='private'")
        assert client.get(path).status_code == 401
        assert environment.requests == []
    finally:
        client.close()
