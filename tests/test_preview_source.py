"""Preview inventories share source revisions without acquiring media read URLs."""
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import storage, store
from app.routers.stream.preview_source import (
    InventoryChanged, preview_source, raw_preview_source, source_snapshot, verify_sources,
)


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "media"
    root.mkdir()
    path = root / "video.mkv"
    path.write_bytes(b"isolated source")
    os.utime(path, (1700000000.125, 1700000000.125))
    backend = storage.LocalStorageBackend({"id": 7, "source": "local", "path": str(root)})
    row = {"id": 11, "kind": "movie", "library_id": 7, "file_path": path.name}

    def forbidden(*_args, **_kwargs):
        pytest.fail("metadata inventories must not resolve media read URLs")

    monkeypatch.setattr(backend, "get_read_url", forbidden)
    return SimpleNamespace(root=root, path=path, row=row, backend=backend)


def test_metadata_source_keeps_canonical_full_precision_identity_without_urls(source):
    src = preview_source(source.row, source.backend)
    assert src.input == "" and src.local_path is None
    assert src.backend is source.backend and src.rel == source.path.name
    assert source_snapshot(src, source.row) == store.media_source_revision(
        7, source.path.name, len(b"isolated source"), 1700000000.125)


def test_raw_and_http_sources_use_the_same_metadata_snapshot(source):
    raw = raw_preview_source(source.row, source.backend)
    exposed = preview_source(source.row, source.backend)
    assert raw == exposed
    assert source_snapshot(raw, source.row) == source_snapshot(exposed, source.row)


def test_metadata_source_resolves_backend_only_when_not_provided(source, monkeypatch):
    calls = []

    def backend_for(lid):
        calls.append(lid)
        return source.backend

    monkeypatch.setattr(storage, "backend_for", backend_for)
    assert preview_source(source.row).backend is source.backend
    assert calls == [7]


def test_metadata_source_freshness_is_explicit(source, monkeypatch):
    cleared = []
    monkeypatch.setattr(storage, "clear_meta_cache", cleared.append)
    preview_source(source.row, source.backend)
    assert cleared == []
    preview_source(source.row, source.backend, fresh=True)
    assert cleared == [7]


@pytest.mark.parametrize("error,status", [
    (storage.StorageNotFound("missing"), 410),
    (storage.StorageOffline("offline"), 503),
    (storage.StorageDenied("denied"), 500),
])
def test_metadata_source_keeps_missing_distinct_from_offline(source, monkeypatch, error, status):
    def fail(_path):
        raise error

    monkeypatch.setattr(source.backend, "stat", fail)
    with pytest.raises(HTTPException) as result:
        preview_source(source.row, source.backend)
    assert result.value.status_code == status


@pytest.mark.parametrize("error", [
    storage.StorageNotFound("missing"), storage.StorageOffline("offline"),
    storage.StorageDenied("denied"), storage.StorageInvalidPath("outside library"),
])
def test_raw_metadata_source_preserves_original_storage_failure(source, monkeypatch, error):
    def fail(_path):
        raise error

    monkeypatch.setattr(source.backend, "stat", fail)
    with pytest.raises(type(error)) as result:
        raw_preview_source(source.row, source.backend)
    assert result.value is error


@pytest.mark.parametrize("path", ["../outside.mkv", "/outside.mkv", "escape.mkv"])
def test_metadata_source_preserves_path_and_symlink_boundaries(source, path):
    outside = source.root.parent / "outside.mkv"
    outside.write_bytes(b"outside library")
    (source.root / "escape.mkv").symlink_to(outside)
    with pytest.raises(HTTPException) as result:
        preview_source(dict(source.row, file_path=path), source.backend)
    assert result.value.status_code == 500


def test_directory_cannot_be_a_preview_source(source):
    with pytest.raises(HTTPException) as result:
        preview_source(dict(source.row, file_path=""), source.backend)
    assert result.value.status_code == 410


def test_backend_from_another_library_is_rejected_before_stat(source, monkeypatch):
    monkeypatch.setattr(source.backend, "stat", lambda *_a: pytest.fail("must not stat wrong library"))
    with pytest.raises(HTTPException) as result:
        preview_source(dict(source.row, library_id=8), source.backend)
    assert result.value.status_code == 500


def test_verify_sources_clears_once_per_library_and_reads_each_source(source, monkeypatch):
    src = preview_source(source.row, source.backend)
    expected = source_snapshot(src, source.row)
    cleared, read = [], []
    original = source.backend.stat

    def stat(path):
        read.append(path)
        return original(path)

    monkeypatch.setattr(storage, "clear_meta_cache", cleared.append)
    monkeypatch.setattr(source.backend, "stat", stat)
    assert verify_sources([(source.row, src, expected), (source.row, src, expected)])
    assert cleared == [7] and read == [source.path.name, source.path.name]


def test_verify_sources_refreshes_each_library_independently(source, monkeypatch):
    second = storage.LocalStorageBackend({"id": 8, "source": "local", "path": str(source.root)})
    second_row = dict(source.row, library_id=8)
    first_src = preview_source(source.row, source.backend)
    second_src = preview_source(second_row, second)
    cleared = []
    monkeypatch.setattr(storage, "clear_meta_cache", cleared.append)
    assert verify_sources([(source.row, first_src, source_snapshot(first_src, source.row)),
                           (second_row, second_src, source_snapshot(second_src, second_row)),
                           (source.row, first_src, source_snapshot(first_src, source.row))])
    assert cleared == [7, 8]


@pytest.mark.parametrize("change", ["size", "mtime", "path"])
def test_verify_sources_rejects_changes_since_the_collected_snapshot(source, change):
    src = preview_source(source.row, source.backend)
    expected = source_snapshot(src, source.row)
    row = source.row
    if change == "size":
        source.path.write_bytes(b"changed size")
    elif change == "mtime":
        os.utime(source.path, (1700000000.12501, 1700000000.12501))
    else:
        moved = source.root / "moved.mkv"
        source.path.rename(moved)
        row = dict(row, file_path=moved.name)
    with pytest.raises(InventoryChanged, match="文件或目录发生变化"):
        verify_sources([(row, src, expected)])


def test_verify_sources_uses_immutable_expected_revision_for_mutable_sources(source):
    src = preview_source(source.row, source.backend)
    expected = source_snapshot(src, source.row)
    source.path.write_bytes(b"replacement")
    mutated = SimpleNamespace(**src._asdict())
    mutated.size = source.path.stat().st_size
    mutated.mtime = source.path.stat().st_mtime
    with pytest.raises(InventoryChanged):
        verify_sources([(source.row, mutated, expected)])


@pytest.mark.parametrize("error,status", [
    (storage.StorageNotFound("missing"), 410),
    (storage.StorageOffline("offline"), 503),
])
def test_verify_sources_keeps_storage_errors_distinct_from_a_changed_revision(source, monkeypatch, error, status):
    src = preview_source(source.row, source.backend)
    expected = source_snapshot(src, source.row)

    def fail(_path):
        raise error

    monkeypatch.setattr(source.backend, "stat", fail)
    with pytest.raises(HTTPException) as result:
        verify_sources([(source.row, src, expected)])
    assert result.value.status_code == status


def test_verify_sources_stops_without_io_when_cancelled(source, monkeypatch):
    src = preview_source(source.row, source.backend)
    monkeypatch.setattr(storage, "clear_meta_cache", lambda *_a: pytest.fail("must not clear on stop"))
    monkeypatch.setattr(source.backend, "stat", lambda *_a: pytest.fail("must not stat on stop"))
    assert not verify_sources([(source.row, src, source_snapshot(src, source.row))], stopped=lambda: True)


def test_verify_sources_stops_after_a_blocked_stat_returns(source, monkeypatch):
    src = preview_source(source.row, source.backend)
    expected = source_snapshot(src, source.row)
    cancelled = [False]
    original = source.backend.stat

    def changed_and_stopped(path):
        source.path.write_bytes(b"replaced while stopping")
        cancelled[0] = True
        return original(path)

    monkeypatch.setattr(source.backend, "stat", changed_and_stopped)
    assert not verify_sources([(source.row, src, expected)], stopped=lambda: cancelled[0])
