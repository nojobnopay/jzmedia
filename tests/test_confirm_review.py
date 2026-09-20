"""用户反馈修复：待确认一键确认（单条/批量）+ 库列表访问方式标识。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app
from app.storage import smb
from _smb_fake import FakeSmbClient


@pytest.fixture()
def smb_lib(tmp_path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name=f"drv-{tmp_path.name}", source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def test_driver_hint_in_library_payload(smb_lib, media_root):
    client = TestClient(app)
    d = client.get("/api/libraries").json()
    by_id = {l["id"]: l for l in d["items"]}
    assert by_id[smb_lib[0]["id"]]["driver"] == "smb"          # 直读模式
    local = store.default_library()
    assert by_id[local["id"]]["driver"] == "local"


def test_confirm_match_single(media_root):
    rel = "confirm/demo.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, title="Demo", year=2020, tmdb_id=12345,
                            needs_review=1)
    client = TestClient(app)
    r = client.post(f"/api/movies/{mid}/confirm-match")
    assert r.status_code == 200 and r.json()["needs_review"] == 0
    assert store.get_movie(mid)["needs_review"] == 0
    # 未匹配的行不能确认（先绑定）
    mid2 = store.upsert_movie_by_path("confirm/nomatch.mkv")
    r2 = client.post(f"/api/movies/{mid2}/confirm-match")
    assert r2.status_code == 422
    assert client.post("/api/movies/999999/confirm-match").status_code == 404


def test_confirm_review_batch_expands_versions(media_root):
    rel_a = "confirmb/A.mkv"
    rel_b = "confirmb/B.mkv"
    for rel in (rel_a, rel_b):
        p = media_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    mid_a = store.upsert_movie_by_path(rel_a)
    mid_b = store.upsert_movie_by_path(rel_b)
    store.update_movie_meta(mid_a, title="Batch", year=2020, tmdb_id=54321,
                            needs_review=1)
    store.update_movie_meta(mid_b, title="Batch", year=2020, tmdb_id=54321,
                            needs_review=1)
    client = TestClient(app)
    d = client.post("/api/movies/batch",
                    json={"ids": [mid_a], "ops": {"confirm_review": True}}).json()
    assert d["results"][0]["status"] == "ok"
    # 同 tmdb 全版本一起清除
    assert store.get_movie(mid_a)["needs_review"] == 0
    assert store.get_movie(mid_b)["needs_review"] == 0
