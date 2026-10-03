"""Health capacity snapshots are scoped, deduplicated, and safe on partial failure."""
import errno
import json
import os
import stat
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import media, store, transcode
from app.main import app
from app.routers import health


@pytest.fixture
def disk_dirs(tmp_path, monkeypatch):
    data = tmp_path / "private-data"
    cache = tmp_path / "separate-cache"
    data.mkdir()
    cache.mkdir()
    monkeypatch.setattr(health.settings, "data_dir", str(data))
    monkeypatch.setattr(health, "TRANSCODE_DIR", str(cache))
    return {"data": data, "transcode": cache}


def _usage(blocks=100, free=40, available=30):
    return SimpleNamespace(f_frsize=4096, f_bsize=8192, f_blocks=blocks,
                           f_bfree=free, f_bavail=available)


def _disk_io(monkeypatch, *, stat_call=os.stat, usage_call=lambda _: _usage()):
    # Replace this module's os binding, not the process-wide os.stat used by pytest.
    monkeypatch.setattr(health, "os", SimpleNamespace(
        stat=stat_call, statvfs=usage_call, path=os.path, access=os.access, R_OK=os.R_OK))


def test_same_filesystem_has_one_snapshot_and_excludes_reserved_blocks(disk_dirs, monkeypatch):
    queried = []

    def usage(path):
        queried.append(path)
        return _usage()

    _disk_io(monkeypatch, usage_call=usage)
    result = health._disk_space()
    assert result["ok"] is True
    assert result["same_filesystem"] is True
    assert result["data"]["filesystem_id"] == result["transcode"]["filesystem_id"]
    assert result["filesystems"] == [{
        "id": result["data"]["filesystem_id"], "total_bytes": 409600,
        "used_bytes": 245760, "available_bytes": 122880,
    }]
    assert queried == [str(disk_dirs["data"])]
    assert all(str(path) not in json.dumps(result) for path in disk_dirs.values())


def test_distinct_filesystems_keep_separate_capacity_and_identity(disk_dirs, monkeypatch):
    devices = {str(disk_dirs["data"]): 101, str(disk_dirs["transcode"]): 202}
    queried = []

    def info(path):
        # Any parent-directory, media-directory, or mount traversal fails this test.
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_dev=devices[path])

    def usage(path):
        queried.append(path)
        return _usage(blocks=devices[path])

    _disk_io(monkeypatch, stat_call=info, usage_call=usage)
    result = health._disk_space()
    assert result["ok"] is True
    assert result["same_filesystem"] is False
    capacities = {row["id"]: row for row in result["filesystems"]}
    assert len(capacities) == 2
    assert capacities[result["data"]["filesystem_id"]]["total_bytes"] == 101 * 4096
    assert capacities[result["transcode"]["filesystem_id"]]["total_bytes"] == 202 * 4096
    assert queried == list(devices)


@pytest.mark.parametrize("missing", [("data",), ("transcode",), ("data", "transcode")])
def test_missing_directory_is_not_created_or_replaced_with_parent_capacity(disk_dirs, monkeypatch, missing):
    for name in missing:
        disk_dirs[name].rmdir()
    queried = []

    def usage(path):
        queried.append(path)
        return _usage()

    _disk_io(monkeypatch, usage_call=usage)
    result = health._disk_space()
    assert result["ok"] is False
    assert result["same_filesystem"] is None
    assert queried == [str(path) for name, path in disk_dirs.items() if name not in missing]
    for name in missing:
        assert result[name]["error"]["code"] == "not_found"
        assert result[name]["filesystem_id"] is None
        assert not disk_dirs[name].exists()


@pytest.mark.parametrize("stage", ["stat", "statvfs"])
def test_permission_failure_is_local_and_does_not_leak_paths(disk_dirs, monkeypatch, stage):
    data = str(disk_dirs["data"])

    def deny(path):
        if path == data:
            raise PermissionError(errno.EACCES, "private owner secret", path)

    def info(path):
        if stage == "stat":
            deny(path)
        return os.stat(path)

    def usage(path):
        if stage == "statvfs":
            deny(path)
        return _usage()

    _disk_io(monkeypatch, stat_call=info, usage_call=usage)
    result = health._disk_space()
    assert result["ok"] is False
    assert result["data"]["error"]["code"] == "permission_denied"
    assert result["transcode"]["ok"] is True
    assert len(result["filesystems"]) == 1
    assert result["same_filesystem"] is (None if stage == "stat" else True)
    assert data not in json.dumps(result)
    assert "private owner secret" not in json.dumps(result)


def test_file_instead_of_directory_is_structured_error(disk_dirs, monkeypatch):
    disk_dirs["transcode"].rmdir()
    disk_dirs["transcode"].write_text("not a directory")
    _disk_io(monkeypatch)
    result = health._disk_space()
    assert result["data"]["ok"] is True
    assert result["transcode"]["error"]["code"] == "not_directory"
    assert result["transcode"]["filesystem_id"] is None


def test_symlink_uses_target_filesystem_without_exposing_resolved_path(disk_dirs, monkeypatch):
    disk_dirs["transcode"].rmdir()
    disk_dirs["transcode"].symlink_to(disk_dirs["data"], target_is_directory=True)
    _disk_io(monkeypatch)
    result = health._disk_space()
    assert result["ok"] is True and result["same_filesystem"] is True
    assert len(result["filesystems"]) == 1
    assert "private-data" not in json.dumps(result)


def test_io_failure_does_not_return_exception_details(disk_dirs, monkeypatch):
    def usage(path):
        raise OSError(errno.EIO, "private mount label", path)

    _disk_io(monkeypatch, usage_call=usage)
    result = health._disk_space()
    assert result["ok"] is False
    assert all(result[name]["error"]["code"] == "unavailable" for name in disk_dirs)
    assert result["filesystems"] == []
    assert "private mount label" not in json.dumps(result)


def test_exhausted_filesystem_is_a_valid_zero_available_observation(disk_dirs, monkeypatch):
    _disk_io(monkeypatch, usage_call=lambda _: _usage(free=0, available=-1))
    result = health._disk_space()
    assert result["ok"] is True
    assert result["filesystems"][0]["available_bytes"] == 0
    assert result["filesystems"][0]["used_bytes"] == result["filesystems"][0]["total_bytes"]


@pytest.mark.parametrize("db_ok,media_ok", [(True, True), (False, True), (True, False)])
def test_health_preserves_existing_response_and_status_when_capacity_fails(disk_dirs, monkeypatch, db_ok, media_ok):
    disk_dirs["transcode"].rmdir()
    _disk_io(monkeypatch)
    monkeypatch.setattr(media, "bin_status", lambda: {"ffmpeg": True, "ffprobe": False})
    backend = {"name": "software", "hw": False, "reason": "test"}
    monkeypatch.setattr(transcode, "backend_info", lambda: backend)
    db_health = {"ok": db_ok, "readable": db_ok, "writable": db_ok, "bytes": 123}
    monkeypatch.setattr(store, "health_check", lambda: db_health)
    # Existing media-check behavior is tested locally, with no configured NAS.
    monkeypatch.setattr(store, "list_media_libraries", lambda **_: [] if media_ok else [
        {"id": 1, "name": "unavailable", "source": "local", "path": str(disk_dirs["transcode"])}])
    monkeypatch.setattr(store, "video_libraries_of", lambda _: [])
    monkeypatch.setattr(health, "_build_commit", lambda: "test123")
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == ("ok" if db_ok and media_ok else "degraded")
    assert body["db"] == db_health
    assert body["media"]["ok"] is media_ok
    assert body["ffmpeg"] is True and body["ffprobe"] is False
    assert body["transcoder"] == backend and body["build"] == "test123"
    assert body["disks"]["ok"] is False
    assert body["disks"]["transcode"]["error"]["code"] == "not_found"
