"""上传独占发布、断点后的重传、已有文件保护与 blob nosniff。"""
import io
import os
import subprocess
import sys

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import storage, store
from app.config import settings
from app.main import app
from app.routers.movies.common import _stream_upload_backend

client = TestClient(app)


class _FakeUpload:
    def __init__(self, data: bytes):
        self.file = io.BytesIO(data)


def upload(data, dst):
    backend = storage.LocalStorageBackend({"path": os.path.dirname(dst)})
    return _stream_upload_backend(_FakeUpload(data), backend, os.path.basename(dst))


def test_stream_upload_writes_and_counts(tmp_path):
    dst = str(tmp_path / "a.mkv")
    n = upload(b"x" * 10, dst)
    assert n == 10
    assert open(dst, "rb").read() == b"x" * 10
    assert not os.path.exists(dst + ".part")


def test_stream_upload_refuses_existing_without_overwrite(tmp_path):
    dst = str(tmp_path / "b.mkv")
    with open(dst, "wb") as fh:
        fh.write(b"old")
    with pytest.raises(HTTPException) as e:
        upload(b"new", dst)
    assert e.value.status_code == 409
    assert open(dst, "rb").read() == b"old"       # 绝不覆盖
    assert not os.path.exists(dst + ".part")


def test_stream_upload_cleans_temporary_file_on_failure(tmp_path, monkeypatch):
    dst = str(tmp_path / "c.mkv")

    def _boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("app.fsutil.rename_no_replace", _boom)
    with pytest.raises(HTTPException) as e:
        upload(b"data", dst)
    assert e.value.status_code == 500
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("entry", ["library", "detail"])
def test_library_and_detail_uploads_preserve_existing_download_part(tmp_path, entry):
    root = tmp_path / "movies"
    root.mkdir()
    lib = store.create_library("upload-part-" + tmp_path.name, path=str(root))
    try:
        mid = store.upsert_movie_by_path("movie.mkv", library_id=lib["id"])
        partial_download = root / "notes.txt.part"
        partial_download.write_bytes(b"download still in progress")
        url = f"/api/movies/{mid}/upload" if entry == "detail" else "/api/uploads"
        response = client.post(url, params={"library_id": lib["id"]}, files={"file": ("notes.txt", b"upload")})
        assert response.status_code == 200
        assert partial_download.read_bytes() == b"download still in progress"
        assert (root / "notes.txt").read_bytes() == b"upload"
    finally:
        store.delete_media_library(lib["media_library_id"])


@pytest.mark.parametrize("driver", ["local", "mount"])
def test_interrupted_upload_does_not_reserve_the_final_name(tmp_path, monkeypatch, driver):
    script = """
import os, sys
from app.storage import LocalStorageBackend
from app.routers.movies.common import _stream_upload_backend
root, driver = sys.argv[1:]
os.path.ismount = lambda path: path == root
backend = LocalStorageBackend({"path": root}, driver=driver)
class Interrupted:
    def __init__(self):
        self.file, self.started = self, False
    def read(self, size):
        if self.started:
            os._exit(73)
        self.started = True
        return b"incomplete" * 8192
_stream_upload_backend(Interrupted(), backend, "movie.mkv")
"""
    proc = subprocess.run([sys.executable, "-c", script, str(tmp_path), driver], timeout=15)
    assert proc.returncode == 73
    assert not (tmp_path / "movie.mkv").exists()
    monkeypatch.setattr(os.path, "ismount", lambda path: path == str(tmp_path))
    backend = storage.LocalStorageBackend({"path": str(tmp_path)}, driver=driver)
    assert _stream_upload_backend(_FakeUpload(b"complete"), backend, "movie.mkv") == 8
    assert (tmp_path / "movie.mkv").read_bytes() == b"complete"


def test_blob_sets_nosniff(media_root):
    rel = "blobdir/movie.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"0123456789")
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, title="Blob", year=2024)
    r = client.get(f"/api/movies/{mid}/blob", params={"name": rel})
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"


def test_upload_refuses_missing_and_substituted_registered_root(tmp_path):
    root = tmp_path / "mount"
    root.mkdir()
    lib = store.create_library("upload-root-" + tmp_path.name, path=str(root))
    try:
        root.rename(tmp_path / "original")
        def post():
            return client.post("/api/uploads", params={"library_id": lib["id"]},
                               files={"file": ("notes.txt", b"keep off wrong disk")})
        assert post().status_code == 503
        assert not root.exists()
        root.mkdir()
        assert post().status_code == 409
        assert list(root.iterdir()) == []
        root.rmdir()
        (tmp_path / "original").rename(root)
        assert post().status_code == 200 and (root / "notes.txt").is_file()
    finally:
        store.delete_media_library(lib["media_library_id"])
