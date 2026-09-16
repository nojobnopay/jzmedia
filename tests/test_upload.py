"""B5a-5（R05-D2）：上传落盘占位防并发覆盖 + blob nosniff。"""
import io
import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import store
from app.config import settings
from app.main import app
from app.routers import movies as movies_router

client = TestClient(app)


class _FakeUpload:
    def __init__(self, data: bytes):
        self.file = io.BytesIO(data)


def test_stream_upload_writes_and_counts(tmp_path):
    dst = str(tmp_path / "a.mkv")
    n = movies_router._stream_upload(_FakeUpload(b"x" * 10), dst)
    assert n == 10
    assert open(dst, "rb").read() == b"x" * 10
    assert not os.path.exists(dst + ".part")


def test_stream_upload_refuses_existing_without_overwrite(tmp_path):
    dst = str(tmp_path / "b.mkv")
    with open(dst, "wb") as fh:
        fh.write(b"old")
    with pytest.raises(HTTPException) as e:
        movies_router._stream_upload(_FakeUpload(b"new"), dst)
    assert e.value.status_code == 409
    assert open(dst, "rb").read() == b"old"       # 绝不覆盖
    assert not os.path.exists(dst + ".part")


def test_stream_upload_cleans_placeholder_on_failure(tmp_path, monkeypatch):
    dst = str(tmp_path / "c.mkv")

    def _boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(movies_router.os, "replace", _boom)
    with pytest.raises(HTTPException) as e:
        movies_router._stream_upload(_FakeUpload(b"data"), dst)
    assert e.value.status_code == 500
    assert not os.path.exists(dst)                  # 占位已清
    assert not os.path.exists(dst + ".part")


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
