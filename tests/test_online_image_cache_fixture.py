"""Disk originals survive isolated process-state reconstruction, with fresh leases.

The Origin and database remain fixed. This intentionally does not restart the P2
server, whose normal startup creates a new source and random upstream port.
"""

import pytest

from app.online import artwork, image_pool
from app.online.artwork_disk import ArtworkDiskCache
from test_online_catalog_fixture import (add_source, client as client,
                                        prepared as prepared)


@pytest.fixture(autouse=True)
def clean_image_connections():
    image_pool.pool.clear()
    yield
    image_pool.pool.clear()
    assert image_pool.pool.stats()["active"] == 0


def browse(client, source):
    response = client.get("/api/online/catalog/browse", params={"source_id": source["id"]})
    assert response.status_code == 200, response.text
    return response.json()


def metrics(client):
    value = client.get("/api/p2/metrics").json()
    assert value["pending_requests"] == value["images"]["active"] == 0
    assert value["image_pool"]["active"] == 0
    assert value["artwork_cache"]["pending"] == value["artwork_cache"]["inflight"] == 0
    assert value["registry"]["sessions"] == value["registry"]["active_requests"] == 0
    assert value["created_sessions"] == []
    assert value["local_rows"] == {"movies": 0, "tv_episodes": 0, "playback_progress": 0}
    assert not any(call["kind"] == "media" or call["app_credentials"] for call in value["origin_calls"])
    return value


def image_reads(value):
    return [call for call in value["origin_calls"] if call["kind"] == "image"]


def test_fresh_lease_registry_and_cache_instances_reuse_originals_without_network(client, monkeypatch):
    source = add_source(client)
    assert client.post("/api/p2/scenario", json={"gallery": True, "clear_artwork_cache": True}).status_code == 200
    original = browse(client, source)["items"][:3]
    bodies = []
    for item in original:
        response = client.get(item["poster_path"])
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "no-store"
        bodies.append(response.content)
    before = metrics(client)
    assert len(image_reads(before)) == 3
    assert before["network_dials"]["image"] == 1, "Three distinct original reads reuse one real image connection"
    assert before["network_dials"]["api"] > 0, "Catalogue HTTP uses a separate API connection"
    assert before["artwork_disk_cache"]["entries"] == 3

    # New in-memory objects prove no old L1 bytes or lease UUID is responsible
    # for the hit. The separate disk unit suite tests actual new-process I/O.
    monkeypatch.setattr(artwork, "registry", artwork.ArtworkRegistry())
    monkeypatch.setattr(artwork, "cache", artwork.ArtworkCache())
    monkeypatch.setattr(artwork, "disk_cache", ArtworkDiskCache())
    image_pool.pool.clear()
    renewed = browse(client, source)["items"][:3]
    assert [row["external_id"] for row in renewed] == [row["external_id"] for row in original]
    for old, new, body in zip(original, renewed, bodies):
        assert old["poster_path"] != new["poster_path"]
        assert client.get(old["poster_path"]).status_code == 404
        response = client.get(new["poster_path"])
        assert response.status_code == 200 and response.content == body
        assert response.headers["cache-control"] == "no-store"
    after = metrics(client)
    assert len(image_reads(after)) == len(image_reads(before))
    assert after["network_dials"]["image"] == before["network_dials"]["image"]
    assert after["image_pool"] == {"idle": 0, "active": 0}
    assert after["artwork_cache"]["entries"] == after["artwork_disk_cache"]["entries"] == 3


def test_full_source_configuration_change_cannot_reuse_a_disk_original(client):
    source = add_source(client)
    first = browse(client, source)["items"][0]["poster_path"]
    assert client.get(first).status_code == 200
    before = metrics(client)
    assert client.post("/api/p2/scenario", json={"clear_artwork_memory": True}).status_code == 200
    assert client.patch(f"/api/online/sources/{source['id']}", json={"name": "新的完整来源配置"}).status_code == 200
    changed = browse(client, source)["items"][0]["poster_path"]
    assert changed != first
    assert client.get(first).status_code == 404
    assert client.get(changed).status_code == 200
    after = metrics(client)
    assert len(image_reads(after)) == len(image_reads(before)) + 1


def test_disk_cache_cannot_restore_content_after_viewing_access_is_revoked(client):
    source = add_source(client)
    assert client.post("/api/viewing/password", json={"new_password": "isolated-image-cache-protection"}).status_code == 200
    assert client.get("/api/viewing/session").status_code == 200
    path = browse(client, source)["items"][0]["poster_path"]
    assert client.get(path).status_code == 200
    assert client.post("/api/p2/scenario", json={"clear_artwork_memory": True}).status_code == 200
    before = metrics(client)
    assert client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    denied = client.get(path)
    assert denied.status_code == 403
    assert len(image_reads(metrics(client))) == len(image_reads(before))


def test_fixture_can_clear_memory_without_removing_persistent_originals(client):
    source = add_source(client)
    path = browse(client, source)["items"][0]["poster_path"]
    assert client.get(path).status_code == 200
    first = metrics(client)
    assert first["artwork_cache"]["entries"] == first["artwork_disk_cache"]["entries"] == 1
    assert client.post("/api/p2/scenario", json={"clear_artwork_memory": True}).status_code == 200
    memory = metrics(client)
    assert memory["artwork_cache"]["entries"] == 0
    assert memory["artwork_disk_cache"] == first["artwork_disk_cache"]
    assert client.get(path).status_code == 200
    assert len(image_reads(metrics(client))) == len(image_reads(first))
    assert client.post("/api/p2/scenario", json={"clear_artwork_cache": True}).status_code == 200
    cleared = metrics(client)
    assert cleared["artwork_cache"]["entries"] == cleared["artwork_disk_cache"]["entries"] == 0
