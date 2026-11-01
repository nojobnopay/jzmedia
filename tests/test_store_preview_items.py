"""Preview inventories fetch only their candidate rows without loading whole shows."""
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import storage, store
from app.routers.stream import common
from app.store import items


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    import app.store._base as base
    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "preview-items.db"))
    store.init_db()
    libraries = []
    for number in (1, 2):
        root = tmp_path / f"media-{number}"
        root.mkdir()
        libraries.append(store.create_library(f"Preview {number}", path=str(root)))
    first, second = (library["id"] for library in libraries)
    movie = store.upsert_movie_by_path("movie.mkv", library_id=first)
    store.update_movie_meta(movie, title="电影标题")
    show = store.upsert_show(first, "剧集标题")
    episode = store.upsert_episode(show, first, "episode.mkv", 1, 3, title="第三集")
    untitled = store.upsert_episode(show, first, "untitled.mkv", 0, 1)
    extra = store.upsert_tv_extra("extras/interview.mkv", show, kind="interview", library_id=first)
    orphan = store.upsert_extra("orphan.mkv", None, kind="unknown", library_id=first)
    other = store.upsert_movie_by_path("other.mkv", library_id=second)
    return libraries, show, [("movie", movie), ("episode", episode), ("episode", untitled),
                             ("extra", extra), ("extra", orphan), ("movie", other)]


def test_preview_playables_preserve_display_names_and_filter_library(catalogue, monkeypatch):
    libraries, _show, refs = catalogue
    expected = {ref: store.get_playable(*ref) for ref in refs}
    connections = []
    original = items._conn

    def connection():
        connections.append(True)
        return original()

    def unexpected(*_args):
        pytest.fail("batch preview lookup must not load playable or whole show details")

    monkeypatch.setattr(items, "_conn", connection)
    monkeypatch.setattr(items, "get_playable", unexpected)
    monkeypatch.setattr(items, "get_show", unexpected)
    rows = store.get_preview_playables(refs + refs + [("movie", 999999)], libraries[0]["id"])
    assert len(connections) == 1
    assert len(rows) == len(refs) - 1
    for row in rows:
        actual = expected[(row["kind"], row["id"])]
        assert row["library_id"] == libraries[0]["id"]
        for field in ("file_path", "title", "kind", "display_title", "extra_kind", "show_title"):
            assert row.get(field) == actual.get(field)


def test_preview_playables_chunk_large_lists_and_ignore_invalid_ids(catalogue):
    libraries, show, refs = catalogue
    library_id = libraries[0]["id"]
    import app.store._base as base
    with base._conn() as connection:
        connection.executemany(
            "INSERT INTO tv_episodes(show_id, library_id, file_path, season, episode) VALUES(?,?,?,?,?)",
            [(show, library_id, f"batch-{number}.mkv", 2, number) for number in range(405)])
        added = [("episode", row["id"]) for row in connection.execute(
            "SELECT id FROM tv_episodes WHERE season=2")]
    result = store.get_preview_playables(added + [("movie", 2 ** 80), ("episode", -1),
                                                 ("extra", "invalid"), ("movie", None)])
    assert {(row["kind"], row["id"]) for row in result} == set(added)
    assert len(store.get_preview_playables(refs)) == len(refs)
    assert store.get_preview_playables([]) == []


def test_library_preview_inventory_uses_three_queries_for_a_long_show(catalogue, monkeypatch):
    libraries, show, refs = catalogue
    library_id = libraries[0]["id"]
    movie = refs[0][1]
    store.update_movie_meta(movie, tmdb_id=940001)
    alternate = store.upsert_movie_by_path("alternate-version.mkv", library_id=library_id)
    store.update_movie_meta(alternate, tmdb_id=940001)
    import app.store._base as base
    with base._conn() as connection:
        connection.executemany(
            "INSERT INTO tv_episodes(show_id, library_id, file_path, season, episode) VALUES(?,?,?,?,?)",
            [(show, library_id, f"long-{number}.mkv", 2, number) for number in range(700)])
    expected = set(store.preview_items(library_id))
    queries = []
    original = items._conn

    def connection():
        conn = original()
        conn.set_trace_callback(queries.append)
        return conn

    def unexpected(*_args):
        pytest.fail("full-library preview inventory must not reload movie/show details")

    monkeypatch.setattr(items, "_conn", connection)
    monkeypatch.setattr(items, "get_movie", unexpected)
    monkeypatch.setattr(items, "get_show", unexpected)
    monkeypatch.setattr(items, "get_playable", unexpected)
    rows = store.get_library_preview_playables(library_id)
    assert {(row["kind"], row["id"]) for row in rows} == expected
    assert {("movie", movie), ("movie", alternate)} <= expected
    assert all(row["library_id"] == library_id for row in rows)
    assert len([query for query in queries if query.lstrip().upper().startswith("SELECT")]) == 3
    assert store.get_library_preview_playables(999999) == []


def test_inventory_sources_reuse_backend_and_keep_source_metadata(catalogue, monkeypatch):
    libraries, _show, _refs = catalogue
    library = libraries[0]
    backend = storage.backend_for_library(library)
    rows = store.get_library_preview_playables(library["id"])
    for row in rows:
        path = Path(backend.abs_path(row["file_path"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"source-{row['kind']}-{row['id']}".encode())
    cleared = []
    monkeypatch.setattr(storage, "clear_meta_cache", cleared.append)

    def unexpected(*_args):
        pytest.fail("loaded inventory sources must reuse their library and playable rows")

    monkeypatch.setattr(storage, "backend_for", unexpected)
    monkeypatch.setattr(store, "get_playable", unexpected)
    sources = [common._playable_source(row, refresh_meta=False, backend=backend) for row in rows]
    assert not cleared
    for row, source in zip(rows, sources):
        stat = backend.stat(row["file_path"])
        assert source.backend is backend
        assert source.rel == row["file_path"]
        assert (source.size, source.mtime) == (stat.size, stat.mtime)


def test_playback_source_still_refreshes_metadata_for_every_request(monkeypatch):
    row = {"id": 7, "kind": "movie", "library_id": 12, "file_path": "fresh.mkv"}
    sentinel = object()
    cleared, resolved = [], []
    monkeypatch.setattr(store, "get_playable", lambda *_args: row)
    monkeypatch.setattr(storage, "clear_meta_cache", cleared.append)

    def source(library_id, path):
        resolved.append((library_id, path))
        return sentinel

    monkeypatch.setattr(storage, "media_source", source)
    assert common._version_source(7) == (row, sentinel)
    assert common._version_source(7) == (row, sentinel)
    assert cleared == [12, 12]
    assert resolved == [(12, "fresh.mkv")] * 2


@pytest.mark.parametrize("storage_error,status", [
    (storage.StorageNotFound, 410), (storage.StorageOffline, 503), (storage.StorageError, 500),
])
def test_inventory_sources_keep_storage_error_classification(catalogue, monkeypatch, storage_error, status):
    libraries, _show, _refs = catalogue
    library = libraries[0]
    backend = storage.backend_for_library(library)

    def fail(_path):
        raise storage_error("fixture unavailable")

    monkeypatch.setattr(backend, "stat", fail)
    with pytest.raises(HTTPException) as error:
        common._playable_source({"library_id": library["id"], "file_path": "missing.mkv"},
                                refresh_meta=False, backend=backend)
    assert error.value.status_code == status


def test_reused_backend_keeps_library_and_path_boundaries(catalogue):
    libraries, _show, _refs = catalogue
    backend = storage.backend_for_library(libraries[0])
    with pytest.raises(storage.StorageInvalidPath, match="another library"):
        storage.media_source(libraries[1]["id"], "movie.mkv", backend=backend)
    with pytest.raises(storage.StorageInvalidPath):
        storage.media_source(libraries[0]["id"], "../outside.mkv", backend=backend)
    outside = Path(libraries[1]["path"]) / "outside.mkv"
    outside.write_bytes(b"another library")
    (Path(libraries[0]["path"]) / "escape.mkv").symlink_to(outside)
    with pytest.raises(storage.StorageInvalidPath):
        storage.media_source(libraries[0]["id"], "escape.mkv", backend=backend)


def test_catalogue_snapshot_contains_only_current_library_source_identities(catalogue):
    libraries, _show, refs = catalogue
    lid = libraries[0]["id"]
    revision = store.media_source_revision(lid, "movie.mkv", 100, 1000.125)
    store.set_scan_state("movie.mkv", 1000, 100, "ok", library_id=lid, source_revision=revision)
    # The same relative path in another video library must not join this row.
    store.set_scan_state("episode.mkv", 2000, 200, "tv_ok", library_id=libraries[1]["id"],
                         source_revision="another-library")
    snapshot = store.preview_catalogue_snapshot(lid)
    assert isinstance(snapshot, tuple)
    assert {(row[0], row[1]) for row in snapshot} == set(refs[:-1])
    assert all(row[2] == lid for row in snapshot)
    assert snapshot[0] == ("movie", refs[0][1], lid, "movie.mkv", revision, 1000, 100)
    assert all(row[4:] == (None, None, None) for row in snapshot[1:])
    assert store.preview_catalogue_snapshot(999999) == ()
    assert store.preview_catalogue_snapshot(lid) == snapshot


@pytest.mark.parametrize("kind,table", [("movie", "movies"), ("episode", "tv_episodes"), ("extra", "extras")])
@pytest.mark.parametrize("change", ["delete", "rename", "other_library"])
def test_catalogue_snapshot_detects_removed_or_relocated_existing_ids(catalogue, kind, table, change):
    libraries, _show, refs = catalogue
    lid = libraries[0]["id"]
    iid = next(iid for candidate, iid in refs if candidate == kind)
    snapshot = store.preview_catalogue_snapshot(lid)
    import app.store._base as base
    with base._conn() as connection:
        if change == "delete":
            connection.execute(f"DELETE FROM {table} WHERE id=?", (iid,))
        elif change == "rename":
            connection.execute(f"UPDATE {table} SET file_path=? WHERE id=?", ("renamed/new.mkv", iid))
        else:
            connection.execute(f"UPDATE {table} SET library_id=? WHERE id=?", (libraries[1]["id"], iid))
    current = store.preview_catalogue_snapshot(lid)
    assert current != snapshot
    if change == "rename":
        assert next(row for row in current if row[:2] == (kind, iid))[3] == "renamed/new.mkv"
    else:
        assert (kind, iid) not in {(row[0], row[1]) for row in current}


def test_catalogue_snapshot_detects_new_items_and_in_place_source_replacement(catalogue):
    libraries, _show, _refs = catalogue
    lid = libraries[0]["id"]
    before = store.preview_catalogue_snapshot(lid)
    added = store.upsert_movie_by_path("new.mkv", library_id=lid)
    assert store.preview_catalogue_snapshot(lid) != before
    revision = store.media_source_revision(lid, "new.mkv", 100, 1000.125)
    store.set_scan_state("new.mkv", 1000, 100, "ok", library_id=lid, source_revision=revision)
    before = store.preview_catalogue_snapshot(lid)
    # Size and integer scan mtime stay equal: the shared full-precision source
    # revision must still detect replacement within the same second.
    replacement = store.media_source_revision(lid, "new.mkv", 100, 1000.375)
    store.set_scan_state("new.mkv", 1000, 100, "ok", library_id=lid, source_revision=replacement)
    after = store.preview_catalogue_snapshot(lid)
    assert after != before
    assert next(row for row in after if row[:2] == ("movie", added))[4:] == (replacement, 1000, 100)


def test_catalogue_snapshot_ignores_unchanged_scan_and_metadata_bookkeeping(catalogue):
    libraries, show, refs = catalogue
    lid = libraries[0]["id"]
    revision = store.media_source_revision(lid, "episode.mkv", 500, 1000.5)
    store.set_scan_state("episode.mkv", 1000, 500, "tv_ok", library_id=lid, source_revision=revision)
    before = store.preview_catalogue_snapshot(lid)
    store.set_scan_state("episode.mkv", 1000, 500, "skipped_unchanged", library_id=lid, source_revision=revision)
    store.update_movie_meta(refs[0][1], title="修订标题", tmdb_id=940011)
    store.update_episode_meta(refs[1][1], title="新分集标题")
    store.update_show_meta(show, title="新剧名")
    import app.store._base as base
    with base._conn() as connection:
        connection.execute("UPDATE scan_state SET updated_at=updated_at+100 WHERE library_id=?", (lid,))
    journal = store.record_fs_change(lid, "mkdir", "empty-directory")
    store.clear_fs_changes(lid, journal)
    assert store.preview_catalogue_snapshot(lid) == before


def test_catalogue_snapshot_query_cost_does_not_grow_with_long_show(catalogue, monkeypatch):
    libraries, show, refs = catalogue
    lid = libraries[0]["id"]
    import app.store._base as base
    with base._conn() as connection:
        connection.executemany(
            "INSERT INTO tv_episodes(show_id, library_id, file_path, season, episode) VALUES(?,?,?,?,?)",
            [(show, lid, f"snapshot-{number}.mkv", 2, number) for number in range(700)])
    original = items._conn
    queries, connections = [], []

    def connection():
        conn = original()
        connections.append(conn)
        conn.set_trace_callback(queries.append)
        return conn

    def unexpected(*_args):
        pytest.fail("source identity snapshot must not load playable or show metadata")

    monkeypatch.setattr(items, "_conn", connection)
    monkeypatch.setattr(items, "get_movie", unexpected)
    monkeypatch.setattr(items, "get_show", unexpected)
    monkeypatch.setattr(items, "get_playable", unexpected)
    snapshot = store.preview_catalogue_snapshot(lid)
    assert len(snapshot) == len(refs) - 1 + 700
    assert len(connections) == 1
    assert len([query for query in queries if query.lstrip().upper().startswith("SELECT")]) == 3
