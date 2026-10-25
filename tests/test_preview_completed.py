"""Completed previews are a read-only, on-demand inventory of usable current sources."""
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import library_paths, media, storage, store
from app.main import app
from app.routers.stream import previews as pv


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    import app.store._base as base
    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "inventory.db"))
    store.init_db()
    library_paths.invalidate_cache()
    libraries = []
    for number in (1, 2):
        root = tmp_path / f"media-{number}"
        root.mkdir()
        libraries.append(store.create_library(f"Inventory {number}", path=str(root)))
    cache = tmp_path / "previews"
    cache.mkdir()
    monkeypatch.setattr(pv, "_root", lambda: cache)
    monkeypatch.delenv("PREVIEW_INTERVAL", raising=False)

    def unexpected(*_args, **_kwargs):
        pytest.fail("inventory must not probe, generate, resolve a read URL, or use the mutating GET")

    monkeypatch.setattr(pv, "_start", unexpected)
    monkeypatch.setattr(pv, "_media_cached_or_probe", unexpected)
    monkeypatch.setattr(pv, "preview_manifest", unexpected)
    monkeypatch.setattr(media, "probe", unexpected)
    monkeypatch.setattr(storage, "media_source", unexpected)
    monkeypatch.setattr(storage.LocalStorageBackend, "get_read_url", unexpected)

    def item(kind="movie", *, library=None, name=None):
        lib = library or libraries[0]
        name = name or f"{kind}.mkv"
        path = Path(lib["path"]) / name
        path.write_bytes(b"synthetic-source")
        if kind == "movie":
            vid = store.upsert_movie_by_path(name, library_id=lib["id"])
            store.update_movie_meta(vid, title="电影标题")
        elif kind == "episode":
            show = store.upsert_show(lib["id"], "剧集标题")
            vid = store.upsert_episode(show, lib["id"], name, 1, 3, title="分集标题")
        else:
            movie = store.upsert_movie_by_path("parent.mkv", library_id=lib["id"])
            vid = store.upsert_extra(name, movie, kind="behindthescenes", library_id=lib["id"])
        return kind, vid

    def cached(ref, *, revision=pv.QUALITY_REVISION, interval=10, owner=True):
        kind, vid = ref
        row = store.get_playable(kind, vid)
        backend = storage.backend_for(row["library_id"])
        stat = backend.stat(row["file_path"])
        src = storage.MediaSource(backend, backend.norm(row["file_path"]), "", None, stat.size, stat.mtime)
        key = pv._cache_key(row, src, interval, quality_revision=revision)
        directory = cache / key
        directory.mkdir()
        count = math.ceil(265 / interval)
        pages = [f"page_{n:04d}.jpg" for n in range(math.ceil(count / (pv.COLUMNS * pv.ROWS)))]
        for name in pages:
            (directory / name).write_bytes(b"synthetic-sprite")
        width, height = (160, 90) if revision in (1, 2) else (480, 270)
        data = dict(version=pv.VERSION, state="ready", duration=265, interval=interval,
                    count=count, width=width, height=height, columns=pv.COLUMNS,
                    rows=pv.ROWS, pages=pages)
        if revision != 1:
            data["quality_revision"] = revision
        if owner:
            data.update(item_kind=kind, item_id=vid)
        pv._write_manifest(directory, data)
        return directory, data

    # Avoid lifespan background work; the inventory itself needs no workers.
    client = TestClient(app)
    yield SimpleNamespace(client=client, libraries=libraries, cache=cache, item=item, cached=cached)
    client.close()
    library_paths.invalidate_cache()


def test_completed_paginates_titles_kinds_and_libraries(inventory):
    inv = inventory
    refs = [inv.item(kind) for kind in ("extra", "episode", "movie")]
    refs.append(inv.item(library=inv.libraries[1]))
    for ref in reversed(refs):
        inv.cached(ref)
    path = "/api/stream/previews/completed"
    first = inv.client.get(path, params={"offset": 0, "limit": 2})
    assert first.status_code == 200
    data = first.json()
    assert (data["total"], data["offset"], data["limit"]) == (4, 0, 2)
    assert [item["kind"] for item in data["items"]] == ["movie", "episode"]
    assert data["items"][0]["title"] == "电影标题"
    assert data["items"][1]["title"] == "剧集标题 S01E03 · 分集标题"
    second = inv.client.get(path, params={"offset": 2, "limit": 2}).json()
    assert [item["kind"] for item in second["items"]] == ["extra", "movie"]
    assert second["items"][0]["title"].endswith("幕后 · extra")
    assert all(item["frames"] == 27 and item["interval"] == 10
               and item["quality_revision"] == pv.QUALITY_REVISION and not item["needs_update"]
               for item in data["items"] + second["items"])
    assert all("file_path" not in item and "/" not in item["file_name"]
               for item in data["items"] + second["items"])
    scoped = inv.client.get(path, params={"library_id": inv.libraries[1]["id"]}).json()
    assert scoped["total"] == 1 and scoped["items"][0]["library_id"] == inv.libraries[1]["id"]
    assert inv.client.get(path, params={"offset": 4}).json()["items"] == []


@pytest.mark.parametrize('revision', [1, 2])
def test_completed_prefers_current_but_includes_whole_legacy_without_writes(inventory, monkeypatch, revision):
    ref = inventory.item()
    legacy, _ = inventory.cached(ref, revision=revision, owner=False)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in legacy.iterdir()}
    directory_mtime = legacy.stat().st_mtime_ns
    write_manifest = pv._write_manifest
    monkeypatch.setattr(pv, "_write_manifest", lambda *_a: pytest.fail("GET must not write ownership"))
    response = inventory.client.get("/api/stream/previews/completed").json()
    assert response["total"] == 1
    assert response["items"][0]["quality_revision"] == revision and response["items"][0]["needs_update"]
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in legacy.iterdir()}
    assert legacy.stat().st_mtime_ns == directory_mtime
    monkeypatch.setattr(pv, "_write_manifest", write_manifest)
    current, _ = inventory.cached(ref)
    result = pv.preview_completed(library_id=None, offset=0, limit=50)
    assert result["total"] == 1 and not result["items"][0]["needs_update"]
    assert legacy.is_dir() and current.is_dir()  # Listing also leaves old-quality cleanup alone.


@pytest.mark.parametrize("change", ["missing", "partial", "missing_page", "short_pages", "duplicate",
                                   "empty_page", "count", "duration", "huge_duration", "dimensions",
                                   "owner", "huge_owner", "quality"])
def test_completed_excludes_invalid_or_incomplete_manifests(inventory, change):
    ref = inventory.item()
    directory, data = inventory.cached(ref)
    if change == "missing":
        (directory / "manifest.json").unlink()
    elif change == "missing_page":
        (directory / data["pages"][-1]).unlink()
    else:
        if change == "partial":
            data["state"] = "partial"
        elif change == "short_pages":
            data["pages"].pop()
        elif change == "duplicate":
            data["pages"][-1] = data["pages"][0]
        elif change == "empty_page":
            (directory / data["pages"][0]).write_bytes(b"")
        elif change == "count":
            data["count"] -= 1
        elif change == "duration":
            data["duration"] = float("nan")
        elif change == "huge_duration":
            data["duration"] = 10 ** 400
        elif change == "dimensions":
            data["width"] *= 2
        elif change == "owner":
            data["item_id"] += 1
        elif change == "huge_owner":
            data["item_id"] = 10 ** 400
        else:
            data["quality_revision"] = 99
        pv._write_manifest(directory, data)
    result = inventory.client.get("/api/stream/previews/completed").json()
    assert result["total"] == 0 and result["items"] == []


@pytest.mark.parametrize("change", ["source_changed", "source_missing", "interval"])
def test_completed_uses_current_source_and_sampling(inventory, monkeypatch, change):
    ref = inventory.item()
    inventory.cached(ref)
    path = Path(inventory.libraries[0]["path"]) / "movie.mkv"
    if change == "source_changed":
        path.write_bytes(b"new-source-revision")
    elif change == "source_missing":
        path.unlink()
    else:
        monkeypatch.setenv("PREVIEW_INTERVAL", "20")
    assert inventory.client.get("/api/stream/previews/completed").json()["total"] == 0


@pytest.mark.parametrize('revision', [1, 2])
def test_completed_partial_current_falls_back_to_complete_legacy(inventory, revision):
    ref = inventory.item()
    inventory.cached(ref, revision=revision)
    directory, data = inventory.cached(ref)
    data["pages"].pop()
    pv._write_manifest(directory, data)
    result = inventory.client.get("/api/stream/previews/completed").json()
    assert result["total"] == 1 and result["items"][0]["needs_update"]


def test_completed_prefers_second_revision_over_first_until_current_ready(inventory):
    ref = inventory.item()
    inventory.cached(ref, revision=1)
    inventory.cached(ref, revision=2)
    result = inventory.client.get("/api/stream/previews/completed").json()
    assert result["total"] == 1 and result["items"][0]["quality_revision"] == 2


def test_completed_owned_cache_does_not_enumerate_the_catalogue(inventory, monkeypatch):
    inventory.cached(inventory.item())
    def unexpected(*_args, **_kwargs):
        pytest.fail("owned caches must not enumerate catalogue items")
    monkeypatch.setattr(store, "preview_items", unexpected)
    assert inventory.client.get("/api/stream/previews/completed").json()["total"] == 1


def test_completed_without_cached_previews_never_stats_unfinished_sources(inventory, monkeypatch):
    inventory.item()
    def unexpected(*_args, **_kwargs):
        pytest.fail("an empty inventory must not enumerate or stat unfinished sources")
    monkeypatch.setattr(store, "preview_items", unexpected)
    monkeypatch.setattr(storage, "backend_for", unexpected)
    assert inventory.client.get("/api/stream/previews/completed").json()["total"] == 0


def test_completed_skips_an_offline_source_without_hiding_other_libraries(inventory, monkeypatch):
    for library in inventory.libraries:
        inventory.cached(inventory.item(library=library))
    first, second = inventory.libraries
    original = storage.backend_for
    def backend(library_id):
        if library_id == first["id"]:
            raise storage.StorageOffline("isolated offline source")
        return original(library_id)
    monkeypatch.setattr(storage, "backend_for", backend)
    result = inventory.client.get("/api/stream/previews/completed").json()
    assert result["total"] == 1 and result["items"][0]["library_id"] == second["id"]


def test_completed_ownerless_fallback_only_stats_the_selected_library(inventory, monkeypatch):
    first, second = inventory.libraries
    inventory.cached(inventory.item(), revision=1, owner=False)
    inventory.item(library=second)
    original = storage.backend_for
    seen = []
    def backend(library_id):
        seen.append(library_id)
        return original(library_id)
    monkeypatch.setattr(storage, "backend_for", backend)
    response = inventory.client.get("/api/stream/previews/completed", params={"library_id": first["id"]})
    assert response.json()["total"] == 1 and seen == [first["id"]]


@pytest.mark.parametrize("link", ["root", "directory", "manifest", "page"])
def test_completed_ignores_symlinks(inventory, tmp_path, link):
    directory, data = inventory.cached(inventory.item())
    path = {"root": inventory.cache, "directory": directory,
            "manifest": directory / "manifest.json", "page": directory / data["pages"][0]}[link]
    target = tmp_path / "outside-cache"
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    assert inventory.client.get("/api/stream/previews/completed").json()["total"] == 0


@pytest.mark.parametrize("query", ["library_id=0", "offset=-1", "limit=0", "limit=101"])
def test_completed_validates_query(inventory, query):
    assert inventory.client.get(f"/api/stream/previews/completed?{query}").status_code == 422


def test_completed_unknown_library_is_404(inventory):
    assert inventory.client.get("/api/stream/previews/completed?library_id=999999").status_code == 404


def test_completed_reuses_persistent_inventory_and_only_rechecks_returned_page(inventory, monkeypatch):
    inv = inventory
    directories = [inv.cached(inv.item(name=f"movie-{n}.mkv"))[0] for n in range(4)]
    assert pv.preview_completed(library_id=None, offset=0, limit=1)["total"] == 4
    pv._inventory.cache_clear()  # A new service process still reuses the disk index.
    from app.routers.stream import preview_inventory
    monkeypatch.setattr(preview_inventory.os, "scandir", lambda *_a: pytest.fail("must reuse directory index"))
    seen = []
    original = pv._read_completed_manifest

    def read(directory):
        seen.append(directory)
        return original(directory)

    monkeypatch.setattr(pv, "_read_completed_manifest", read)
    result = pv.preview_completed(library_id=None, offset=1, limit=1)
    assert result["total"] == 4 and len(result["items"]) == 1
    assert seen == [directories[1]]


def test_completed_scope_does_not_read_other_library_sprites(inventory, monkeypatch):
    inv = inventory
    first, second = inv.libraries
    expected, _ = inv.cached(inv.item(library=first))
    inv.cached(inv.item(library=second))
    seen = []
    original = pv._read_completed_manifest

    def read(directory):
        seen.append(directory)
        return original(directory)

    monkeypatch.setattr(pv, "_read_completed_manifest", read)
    result = pv.preview_completed(library_id=first["id"], offset=0, limit=50)
    assert result["total"] == 1 and seen == [expected]


@pytest.mark.parametrize("change", ["source", "missing_page", "empty_page", "manifest", "symlink"])
def test_completed_rechecks_damage_after_index_is_warm(inventory, change, tmp_path):
    inv = inventory
    directory, data = inv.cached(inv.item())
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 1
    if change == "source":
        (Path(inv.libraries[0]["path"]) / "movie.mkv").write_bytes(b"changed-source")
    elif change == "missing_page":
        (directory / data["pages"][0]).unlink()
    elif change == "empty_page":
        (directory / data["pages"][0]).write_bytes(b"")
    elif change == "manifest":
        data["state"] = "partial"
        # Simulate a process stopping between manifest replace and index update.
        import json
        (directory / "manifest.json").write_text(json.dumps(data))
    else:
        outside = tmp_path / "outside"
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 0


def test_completed_discovers_unannounced_new_and_repaired_partial_caches(inventory):
    inv = inventory
    directory, data = inv.cached(inv.item())
    data["state"] = "partial"
    pv._write_manifest(directory, data)
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 0
    import json
    data["state"] = "ready"
    (directory / "manifest.json").write_text(json.dumps(data))
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 1
    inv.cached(inv.item(name="another.mkv"))
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 2


def test_completed_index_failure_falls_back_without_failing_generation(inventory, monkeypatch):
    from app.routers.stream.preview_inventory import PreviewInventory
    monkeypatch.setattr(PreviewInventory, "_connect", lambda *_a: (_ for _ in ()).throw(OSError("read-only index")))
    inventory.cached(inventory.item())
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 1


def test_completed_does_not_retain_entries_after_cache_cleanup(inventory):
    inv = inventory
    inv.cached(inv.item())
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 1
    pv.preview_cache_clean(pv.PreviewCacheCleanBody(dry_run=False))
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 0


@pytest.mark.parametrize("damage", ["metadata", "key"])
def test_completed_corrupt_index_records_fall_back_to_cache(inventory, damage):
    import sqlite3

    inv = inventory
    ref = inv.item()
    inv.cached(ref)
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 1
    connection = sqlite3.connect(pv._inventory(inv.cache).path)
    try:
        with connection:
            if damage == "metadata":
                connection.execute("UPDATE entries SET data=?", ("[]",))
            else:
                connection.execute("UPDATE entries SET key=?", ("invalid-preview-key",))
    finally:
        connection.close()
    pv._inventory.cache_clear()

    response = inv.client.get("/api/stream/previews/completed")
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 1
    assert [(item["kind"], item["id"]) for item in result["items"]] == [ref]


def test_completed_recovers_after_warm_manifest_owner_is_corrupted_and_repaired(inventory):
    import json

    inv = inventory
    ref = inv.item()
    other = inv.item(name="other.mkv")
    directory, data = inv.cached(ref)
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 1

    # Bypass the writer to simulate an external edit, with an otherwise valid ID.
    data["item_id"] = other[1]
    (directory / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 0

    data["item_id"] = ref[1]
    (directory / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    result = pv.preview_completed(library_id=None, offset=0, limit=50)
    assert result["total"] == 1
    assert [(item["kind"], item["id"]) for item in result["items"]] == [ref]


def test_completed_recovers_when_initially_discovered_owner_is_repaired(inventory, monkeypatch):
    import json

    inv = inventory
    ref = inv.item()
    other = inv.item(name="other.mkv")
    with monkeypatch.context() as unindexed:
        unindexed.setattr(pv, "_inventory_changed", lambda *_a: None)
        directory, data = inv.cached(ref)
    data["item_id"] = other[1]
    (directory / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 0

    data["item_id"] = ref[1]
    (directory / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    result = pv.preview_completed(library_id=None, offset=0, limit=50)
    assert result["total"] == 1
    assert [(item["kind"], item["id"]) for item in result["items"]] == [ref]


def test_completed_failed_invalidation_drops_pending_wrong_owner(inventory, monkeypatch):
    import json

    inv = inventory
    ref = inv.item()
    other = inv.item(name="other.mkv")
    directory, data = inv.cached(ref)
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 1
    data["item_id"] = other[1]
    (directory / "manifest.json").write_text(json.dumps(data))
    index = pv._inventory(inv.cache)
    connect = index._connect
    calls = 0

    def temporary_failure():
        nonlocal calls
        calls += 1
        if calls == 2:  # Snapshot succeeds; the ownership invalidation cannot persist.
            raise OSError("temporarily read-only index")
        return connect()

    with monkeypatch.context() as unavailable:
        unavailable.setattr(index, "_connect", temporary_failure)
        assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 0
    assert directory.name not in index.pending
    data["item_id"] = ref[1]
    (directory / "manifest.json").write_text(json.dumps(data))
    assert pv.preview_completed(library_id=None, offset=0, limit=50)["total"] == 1
