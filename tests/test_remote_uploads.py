"""远程直读库上传：详情页/库页经 StorageBackend 流式落 NAS（不再写本地挂载点）。"""
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store
from app.main import app
from app.storage import smb
from _smb_fake import FakeSmbClient


@pytest.fixture()
def smb_lib(tmp_path, monkeypatch):
    root = tmp_path / "share"
    d = root / "电影" / "片 (2020)"
    d.mkdir(parents=True)
    (d / "片 (2020).mkv").write_bytes(b"x")
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    monkeypatch.setattr(scanner.scan, "search_with_fallback",
                        lambda title, year: (None, title, False))
    lib = store.create_library(name=f"up-{tmp_path.name}", kind="movie", source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def _movie(lib, rel="电影/片 (2020)/片 (2020).mkv"):
    mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
    store.update_movie_meta(mid, title="片", year=2020, tmdb_id=424200)
    return mid


def _mount_stub(lib) -> pathlib.Path:
    from app.db import mount_point
    return pathlib.Path(mount_point(lib["media_library_id"]))


def test_movie_upload_remote_writes_nas(smb_lib):
    lib, root, _fake = smb_lib
    mid = _movie(lib)
    c = TestClient(app)
    r = c.post(f"/api/movies/{mid}/upload",
               files={"file": ("note.txt", b"hello-nas")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["rel"] == "电影/片 (2020)/note.txt" and out["size"] == 9
    assert (root / "电影" / "片 (2020)" / "note.txt").read_bytes() == b"hello-nas"
    # 绝不写本地挂载点空目录
    assert not (_mount_stub(lib) / "电影" / "片 (2020)" / "note.txt").exists()
    # 同名冲突 → 409
    r2 = c.post(f"/api/movies/{mid}/upload",
                files={"file": ("note.txt", b"again")})
    assert r2.status_code == 409
    assert (root / "电影" / "片 (2020)" / "note.txt").read_bytes() == b"hello-nas"


def test_movie_upload_remote_feature_registers(smb_lib):
    lib, root, _fake = smb_lib
    mid = _movie(lib)
    # 本地优先会用父目录「片 (2020)」绑定宿主片的 tmdb；种子缓存让 apply 离线可走通
    store.upsert_tmdb_cache(424200, {"title": "片", "original_title": "Pian",
                                     "year": 2020, "media_type": "movie"}, {}, "")
    c = TestClient(app)
    r = c.post(f"/api/movies/{mid}/upload",
               files={"file": ("New Movie (2021).mkv", b"v")})
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("ok", "ok_needs_review"), r.json()
    rel = "电影/片 (2020)/New Movie (2021).mkv"
    assert (root / rel).is_file()
    assert store.get_by_path(rel, library_id=lib["id"]) is not None


def test_movie_upload_remote_extras_subdir(smb_lib):
    lib, root, _fake = smb_lib
    mid = _movie(lib)
    c = TestClient(app)
    r = c.post(f"/api/movies/{mid}/upload?subdir=extras",
               files={"file": ("花絮.mkv", b"e")})
    assert r.status_code == 200, r.text
    assert (root / "电影" / "片 (2020)" / "extras" / "花絮.mkv").is_file()


def test_library_upload_remote_creates_dirs(smb_lib):
    lib, root, _fake = smb_lib
    c = TestClient(app)
    r = c.post("/api/uploads",
               params={"library_id": lib["id"], "relpath": "剧集/子目录/x.txt"},
               files={"file": ("x.txt", b"data")})
    assert r.status_code == 200, r.text
    assert r.json()["rel"] == "剧集/子目录/x.txt"
    assert (root / "剧集" / "子目录" / "x.txt").read_bytes() == b"data"
    assert not (_mount_stub(lib) / "剧集" / "子目录" / "x.txt").exists()


def test_upload_remote_read_only_lib(smb_lib):
    lib, _root, _fake = smb_lib
    mid = _movie(lib)
    store.update_media_library(lib["media_library_id"], read_only=True)
    library_paths.invalidate_cache()
    c = TestClient(app)
    assert c.post(f"/api/movies/{mid}/upload",
                  files={"file": ("a.txt", b"x")}).status_code == 409
    assert c.post("/api/uploads", params={"library_id": lib["id"]},
                  files={"file": ("a.txt", b"x")}).status_code == 409


def test_upload_local_still_works(tmp_path):
    """本地库上传路径回归（不经 backend 分支）。"""
    root = tmp_path / "uplocal"
    root.mkdir(parents=True, exist_ok=True)
    lib = store.create_library(name=f"up-local-{tmp_path.name}", path=str(root))
    library_paths.invalidate_cache()
    try:
        c = TestClient(app)
        r = c.post("/api/uploads", params={"library_id": lib["id"]},
                   files={"file": ("l.txt", b"local")})
        assert r.status_code == 200, r.text
        assert (pathlib.Path(lib["path"]) / "l.txt").read_bytes() == b"local"
    finally:
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()
