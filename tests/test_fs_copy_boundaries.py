"""Queued copies retain storage availability and real-directory boundaries."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import library_paths, store
from app.jobkit import JobRegistry
from app.routers.fs import copy as copies


@pytest.fixture(params=["local", "nfs", "smb"])
def library(request, tmp_path, monkeypatch):
    source = request.param
    if source == "local":
        root = tmp_path / "media"
        root.mkdir()
        lib = store.create_library(name=f"copy-boundary-{tmp_path.name}", kind="tv", path=str(root))
    else:
        monkeypatch.setenv("SMB_DRIVER", "mount")
        options = ({"nfs": {"export": f"192.0.2.1:/{tmp_path.name}"}} if source == "nfs" else
                   {"smb": {"host": "192.0.2.2", "share": tmp_path.name}})
        lib = store.create_library(name=f"copy-boundary-{tmp_path.name}", kind="tv", source=source, **options)
        root = Path(lib["path"])
        root.mkdir(parents=True)
    mounted = {"value": True}
    monkeypatch.setattr(os.path, "ismount", lambda path: str(path) == str(root) and mounted["value"])
    yield lib, root, mounted
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


@pytest.fixture()
def queued(monkeypatch):
    registry = JobRegistry(prefix="copy-boundary")
    monkeypatch.setattr(copies, "_COPY_JOBS", registry)
    pending = []

    def thread(*, target, args, daemon):
        assert daemon
        pending.append((target, args))
        return SimpleNamespace(start=lambda: None)

    monkeypatch.setattr(copies, "threading", SimpleNamespace(Thread=thread))

    def run():
        target, args = pending.pop(0)
        target(*args)
        return registry.get(args[0])

    return pending, run


def start(lib, paths, to_dir, **extra):
    return copies.fs_copy({"library_id": lib["id"], "from": paths,
                           "to_dir": to_dir, "dry_run": False, **extra})


@pytest.mark.parametrize("library", ["nfs", "smb"], indirect=True)
def test_copy_unmounted_after_queue_does_not_touch_mount_residue(library, queued):
    lib, root, mounted = library
    (root / "source.txt").write_bytes(b"mount residue")
    job = start(lib, ["source.txt"], "copies")
    mounted["value"] = False
    status = queued[1]()
    assert status["job_id"] == job["job_id"]
    assert status["state"] == "failed" and status["worker_finished"]
    assert not (root / "copies").exists()
    assert (root / "source.txt").read_bytes() == b"mount residue"
    assert not store.fs_change_summary(lib["id"])["pending"]


@pytest.mark.parametrize("library", ["nfs", "smb"], indirect=True)
def test_cancelled_queued_copy_stays_cancelled_after_unmount(library, queued):
    lib, root, mounted = library
    (root / "source.txt").write_bytes(b"keep")
    job = start(lib, ["source.txt"], "copies")
    copies.fs_copy_cancel(job["job_id"])
    mounted["value"] = False
    status = queued[1]()
    assert status["state"] == "cancelled" and status["worker_finished"]
    assert not (root / "copies").exists()


def test_copy_read_only_change_after_queue_is_respected(library, queued):
    lib, root, _ = library
    (root / "source.txt").write_bytes(b"keep")
    start(lib, ["source.txt"], "copies")
    store.update_media_library(lib["media_library_id"], read_only=True)
    status = queued[1]()
    assert status["state"] == "failed" and status["worker_finished"]
    assert not (root / "copies").exists()


@pytest.mark.parametrize("library", ["nfs", "smb"], indirect=True)
@pytest.mark.parametrize("directory", [False, True], ids=["items", "directory"])
def test_copy_stops_between_files_after_unmount(library, queued, monkeypatch, directory):
    lib, root, mounted = library
    source = root / "source"
    source.mkdir()
    for name in ("a.txt", "b.txt"):
        (source / name).write_bytes(name.encode())
    original = copies._copy_file

    def copy_then_unmount(*args):
        copied = original(*args)
        mounted["value"] = False
        return copied

    monkeypatch.setattr(copies, "_copy_file", copy_then_unmount)
    paths = ["source"] if directory else ["source/a.txt", "source/b.txt"]
    start(lib, paths, "copies")
    status = queued[1]()
    assert status["state"] == "failed" and status["worker_finished"]
    destination = root / "copies/source" if directory else root / "copies"
    assert len(list(destination.glob("*.txt"))) == 1
    assert store.fs_change_summary(lib["id"])["pending"]


@pytest.mark.parametrize("library", ["nfs", "smb"], indirect=True)
def test_copy_stops_between_chunks_and_records_retained_partial_file(library, queued, monkeypatch):
    lib, root, mounted = library
    (root / "source.txt").write_bytes(b"x" * (3 * 1024 * 1024))
    (root / "copies").mkdir()
    original_update = copies._COPY_JOBS.update

    def disconnect_after_write(job_id, **fields):
        original_update(job_id, **fields)
        if fields.get("bytes_done", 0):
            mounted["value"] = False

    monkeypatch.setattr(copies._COPY_JOBS, "update", disconnect_after_write)
    start(lib, ["source.txt"], "copies")
    status = queued[1]()
    assert status["state"] == "failed" and status["worker_finished"]
    assert (root / "copies/source.txt").stat().st_size == 1024 * 1024
    assert status["bytes_done"] == 1024 * 1024
    assert "copies/source.txt" in store.fs_change_summary(lib["id"])["paths"]


@pytest.mark.parametrize("library", ["nfs", "smb"], indirect=True)
def test_copy_records_partial_file_hidden_by_unmount(library, queued, monkeypatch):
    lib, root, mounted = library
    (root / "source.txt").write_bytes(b"x" * (3 * 1024 * 1024))
    (root / "copies").mkdir()
    detached = root.with_name(root.name + "-detached")
    original_update = copies._COPY_JOBS.update

    def hide_mount_after_write(job_id, **fields):
        original_update(job_id, **fields)
        if fields.get("bytes_done", 0):
            root.rename(detached)
            root.mkdir()
            mounted["value"] = False

    monkeypatch.setattr(copies._COPY_JOBS, "update", hide_mount_after_write)
    start(lib, ["source.txt"], "copies")
    status = queued[1]()
    assert status["state"] == "failed" and status["worker_finished"]
    assert not (root / "copies/source.txt").exists()
    assert (detached / "copies/source.txt").stat().st_size == 1024 * 1024
    assert "copies/source.txt" in store.fs_change_summary(lib["id"])["paths"]


@pytest.mark.parametrize("library", ["nfs", "smb"], indirect=True)
def test_directory_and_symlink_writes_recheck_mount(library):
    lib, root, mounted = library
    (root / "source.txt").write_bytes(b"keep")
    (root / "link.txt").symlink_to("source.txt")
    mounted["value"] = False
    from app import storage

    with pytest.raises(storage.StorageOffline):
        copies._mkdir_tracked(lib["id"], str(root / "new"))
    with pytest.raises(storage.StorageOffline):
        copies._symlink_tracked(str(root / "link.txt"), str(root / "copied-link.txt"), lib["id"])
    assert not (root / "new").exists() and not (root / "copied-link.txt").is_symlink()


def test_local_copy_cancellation_removes_partial_target(library):
    lib, root, _ = library
    (root / "source.txt").write_bytes(b"keep")
    result = copies._copy_file_tracked(str(root / "source.txt"), str(root / "cancelled.txt"),
                                       lib["id"], None, lambda: True)
    assert result is False and not (root / "cancelled.txt").exists()
    assert not store.fs_change_summary(lib["id"])["pending"]


def test_competing_copy_target_is_not_overwritten_or_recorded(library, monkeypatch):
    lib, root, _ = library
    (root / "source.txt").write_bytes(b"source")
    destination = root / "competing.txt"
    original = copies._copy_file

    def another_writer_wins(*args):
        destination.write_bytes(b"another writer")
        return original(*args)

    monkeypatch.setattr(copies, "_copy_file", another_writer_wins)
    with pytest.raises(FileExistsError):
        copies._copy_file_tracked(str(root / "source.txt"), str(destination), lib["id"], None, None)
    assert destination.read_bytes() == b"another writer"
    assert not store.fs_change_summary(lib["id"])["pending"]


@pytest.mark.parametrize("library", ["local"], indirect=True)
@pytest.mark.parametrize("target", ["tree", "tree/sub", "alias", "alias/sub"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_nested_copy_rejects_real_and_symlink_targets(library, queued, target, dry_run):
    lib, root, _ = library
    (root / "tree/sub").mkdir(parents=True)
    (root / "tree/sub/source.txt").write_bytes(b"keep")
    (root / "alias").symlink_to("tree", target_is_directory=True)
    with pytest.raises(HTTPException) as error:
        start(lib, ["tree"], target, dry_run=dry_run)
    assert error.value.status_code == 422
    assert queued[0] == [] and not (root / "tree/sub/tree").exists()


@pytest.mark.parametrize("library", ["local"], indirect=True)
def test_nested_copy_resolves_source_symlink_ancestors(library, queued):
    lib, root, _ = library
    (root / "real/tree/sub").mkdir(parents=True)
    (root / "source-alias").symlink_to("real", target_is_directory=True)
    with pytest.raises(HTTPException) as error:
        start(lib, ["source-alias/tree"], "real/tree/sub")
    assert error.value.status_code == 422 and queued[0] == []


@pytest.mark.parametrize("library", ["local"], indirect=True)
def test_queued_copy_rechecks_changed_destination_alias(library, queued):
    lib, root, _ = library
    (root / "tree/sub").mkdir(parents=True)
    (root / "tree/source.txt").write_bytes(b"keep")
    (root / "outside").mkdir()
    alias = root / "destination"
    alias.symlink_to("outside", target_is_directory=True)
    start(lib, ["tree"], "destination")
    alias.unlink()
    alias.symlink_to("tree/sub", target_is_directory=True)
    status = queued[1]()
    assert status["state"] == "failed" and "target inside source" in status["error"]
    assert not (root / "tree/sub/tree").exists()


@pytest.mark.parametrize("library", ["local"], indirect=True)
def test_copy_rechecks_alias_between_directory_entries(library, queued, monkeypatch):
    lib, root, _ = library
    (root / "tree/sub").mkdir(parents=True)
    for name in ("a.txt", "b.txt"):
        (root / "tree" / name).write_bytes(b"keep")
    (root / "outside").mkdir()
    alias = root / "destination"
    alias.symlink_to("outside", target_is_directory=True)
    original = copies._copy_file

    def change_alias_after_first_file(*args):
        copied = original(*args)
        alias.unlink()
        alias.symlink_to("tree/sub", target_is_directory=True)
        return copied

    monkeypatch.setattr(copies, "_copy_file", change_alias_after_first_file)
    start(lib, ["tree"], "destination")
    status = queued[1]()
    assert status["state"] == "failed" and "target inside source" in status["error"]
    assert not (root / "tree/sub/tree").exists()
    assert len(list((root / "outside/tree").glob("*.txt"))) == 1


def test_legal_internal_symlinks_are_preserved(library, queued):
    lib, root, _ = library
    (root / "source/real-dir").mkdir(parents=True)
    (root / "source/target.txt").write_bytes(b"keep")
    (root / "source/real-dir/.hidden").write_bytes(b"hidden")
    (root / "source/link.txt").symlink_to("target.txt")
    (root / "source/dir-link").symlink_to("real-dir", target_is_directory=True)
    (root / "destination").mkdir()
    (root / "destination-alias").symlink_to("destination", target_is_directory=True)
    start(lib, ["source"], "destination-alias")
    status = queued[1]()
    assert status["state"] == "done" and status["results"][0]["status"] == "copied"
    target = root / "destination/source"
    assert (target / "target.txt").read_bytes() == b"keep"
    assert (target / "link.txt").is_symlink() and os.readlink(target / "link.txt") == "target.txt"
    assert (target / "dir-link").is_symlink() and os.readlink(target / "dir-link") == "real-dir"
    assert (target / "real-dir/.hidden").read_bytes() == b"hidden"
