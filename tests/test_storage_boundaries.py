"""Explicit library identity, mount availability and effective scan scope guard data."""
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import library_paths, scanner, storage, store
from app.routers import files
from app.routers.fs import copy as copy_routes
from app.routers.fs import routes


@pytest.mark.parametrize("operation,body", [
    (routes.fs_delete, {"paths": ["keep.txt"], "dry_run": False}),
    (routes.fs_rename, {"from": "keep.txt", "name": "renamed.txt", "dry_run": False}),
    (routes.fs_move, {"from": "keep.txt", "to_dir": "moved", "dry_run": False}),
    (routes.fs_mkdir, {"name": "unexpected"}),
    (copy_routes.fs_copy, {"from": ["keep.txt"], "to_dir": "copies", "dry_run": False}),
])
def test_deleted_library_id_cannot_mutate_default_library(operation, body, tmp_path, monkeypatch):
    default_root = tmp_path / "default"
    default_root.mkdir()
    (default_root / "keep.txt").write_bytes(b"keep default data")
    stale_root = tmp_path / "deleted"
    stale_root.mkdir()
    lib = store.create_library(name=f"deleted-{tmp_path.name}", path=str(stale_root))
    stale_id = lib["id"]
    store.delete_media_library(lib["media_library_id"])
    default = {**library_paths.default_library(), "path": str(default_root)}
    monkeypatch.setattr(library_paths, "default_library", lambda: default)
    with pytest.raises(HTTPException) as error:
        operation({**body, "library_id": stale_id})
    assert error.value.status_code == 404
    assert (default_root / "keep.txt").read_bytes() == b"keep default data"
    assert {path.name for path in default_root.iterdir()} == {"keep.txt"}


def test_unknown_library_resolution_is_strict_but_default_still_works():
    with pytest.raises(storage.StorageNotFound):
        storage.backend_for(999_999_999)
    with pytest.raises(ValueError, match="library not found"):
        library_paths.resolve(999_999_999, "keep.txt")
    assert storage.backend_for().library_id == library_paths.default_id()
    assert library_paths.library_root(None) == library_paths.default_root()
    assert storage.backend_for(library_paths.default_id()).root == library_paths.default_root()


@pytest.fixture(params=[("nfs", "movie"), ("nfs", "tv"), ("smb", "movie"), ("smb", "tv")])
def mount_library(request, tmp_path, monkeypatch):
    source, kind = request.param
    monkeypatch.setenv("SMB_DRIVER", "mount")
    options = ({"nfs": {"export": f"192.0.2.1:/{tmp_path.name}"}} if source == "nfs" else
               {"smb": {"host": "192.0.2.2", "share": tmp_path.name}})
    lib = store.create_library(name=f"offline-{tmp_path.name}", source=source, kind=kind, **options)
    root = Path(lib["path"])
    root.mkdir(parents=True, exist_ok=True)
    state = {"mounted": False}
    # The directory simulates a mount; no OS mount or network call is performed.
    monkeypatch.setattr("os.path.ismount", lambda path: str(path) == str(root) and state["mounted"])
    rel = "Existing.2020.mkv" if kind == "movie" else "Show/Show.S01E01.mkv"
    if kind == "movie":
        item_id = store.upsert_movie_by_path(rel, library_id=lib["id"])
        progress_kind = "movie"
    else:
        show_id = store.upsert_show(lib["id"], "Existing show", 2020)
        item_id = store.upsert_episode(show_id, lib["id"], rel, 1, 1)
        progress_kind = "episode"
    store.save_progress(item_id, 50, 100, kind=progress_kind)
    store.set_scan_state(rel, 1, 1, "ok", library_id=lib["id"])
    yield lib, root, state, rel, item_id, progress_kind
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def test_unmounted_library_scan_and_clean_preserve_catalogue(mount_library):
    lib, root, _, rel, item_id, kind = mount_library
    (root / "residue.txt").write_bytes(b"leftover local file")
    with pytest.raises(storage.StorageOffline):
        storage.backend_for(lib["id"])
    result = scanner.scan_all(library_id=lib["id"])
    assert result and result[0]["status"] == "library_offline"
    assert not any(row["status"].startswith("removed_") for row in result)
    assert store.get_progress(item_id, kind=kind)["position"] == 50
    assert store.get_scan_state(rel, lib["id"])
    if kind == "movie":
        assert store.get_movie(item_id)
        assert files.clean({"library_id": lib["id"], "ids": [item_id], "dry_run": False})["deleted"] == 0
        assert store.get_movie(item_id)
    else:
        assert store.get_episode_by_path(rel, lib["id"])["id"] == item_id
    with pytest.raises(HTTPException) as error:
        routes.fs_mkdir({"library_id": lib["id"], "name": "must-not-write"})
    assert error.value.status_code == 503
    assert not (root / "must-not-write").exists()


def test_mount_loss_after_snapshot_does_not_run_gc(mount_library):
    lib, _, state, rel, item_id, kind = mount_library
    state["mounted"] = True

    def after_snapshot(done, total):
        assert done == total == 0
        state["mounted"] = False

    result = scanner.scan_all(library_id=lib["id"], progress_cb=after_snapshot)
    assert any(row["status"] == "library_offline" for row in result)
    assert store.get_progress(item_id, kind=kind)
    assert store.get_scan_state(rel, lib["id"])


@pytest.mark.parametrize("mount_library", [("nfs", "movie"), ("smb", "movie")], indirect=True)
def test_unmounted_restore_does_not_move_mount_point_residue(mount_library):
    _, root, _, rel, item_id, _ = mount_library
    source = root / rel
    source.write_bytes(b"local residue")
    movie = store.get_movie(item_id)
    movie["original_file_path"] = "old/Original.mkv"
    result = files._restore_one(movie, dry_run=False)
    assert result["status"].startswith("error:")
    assert source.read_bytes() == b"local residue"
    assert not (root / "old/Original.mkv").exists()
    assert store.get_movie(item_id)["file_path"] == rel


def test_existing_mount_backend_rechecks_reads_and_writes(tmp_path, monkeypatch):
    mount_root = tmp_path / "mount"
    video_root = mount_root / "Movies"
    video_root.mkdir(parents=True)
    (video_root / "keep.txt").write_bytes(b"keep")
    mounted = {"value": True}
    monkeypatch.setattr("os.path.ismount", lambda path: str(path) == str(mount_root) and mounted["value"])
    backend = storage.backend_for_library({"id": 77, "source": "nfs", "name": "mounted",
                                           "path": str(video_root), "media_path": str(mount_root)})
    assert backend.read("keep.txt") == b"keep"
    mounted["value"] = False
    operations = [lambda: backend.abs_path("keep.txt"), lambda: backend.stat("keep.txt"),
                  lambda: backend.list(""), lambda: backend.read("keep.txt"),
                  lambda: list(backend.iter_tree()), lambda: backend.mkdir("new"),
                  lambda: backend.write("keep.txt", b"replace"),
                  lambda: backend.rename("keep.txt", "renamed.txt"),
                  lambda: backend.delete("keep.txt")]
    for operation in operations:
        with pytest.raises(storage.StorageOffline):
            operation()
    with pytest.raises(storage.StorageOffline):
        with backend.open_write("new.txt"):
            pytest.fail("offline open_write must not yield")
    with pytest.raises(storage.StorageOffline):
        with backend.open_read("keep.txt"):
            pytest.fail("offline open_read must not yield")
    assert (video_root / "keep.txt").read_bytes() == b"keep"
    assert {path.name for path in video_root.iterdir()} == {"keep.txt"}


def test_parent_media_disabled_excludes_video_libraries_from_scan(tmp_path):
    root = tmp_path / "disabled"
    root.mkdir()
    lib = store.create_library(name=f"disabled-{tmp_path.name}", path=str(root))
    item_id = store.upsert_movie_by_path("Missing.2020.mkv", library_id=lib["id"])
    try:
        store.update_media_library(lib["media_library_id"], enabled=False)
        assert lib["id"] not in {item["id"] for item in library_paths.list_libraries(only_enabled=True)}
        assert scanner.scan._scan_libraries(None, lib["media_library_id"]) == []
        assert scanner.scan_all(media_library_id=lib["media_library_id"]) == []
        assert store.get_movie(item_id)
    finally:
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()


def test_all_video_libraries_disabled_does_not_scan_default(monkeypatch):
    disabled = {**library_paths.default_library(), "enabled": 0, "media_enabled": 1}
    monkeypatch.setattr(library_paths, "_snapshot", lambda: [disabled])
    assert scanner.scan._scan_libraries(None) == []
    assert scanner.scan_all() == []
