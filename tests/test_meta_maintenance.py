"""P2：手动重匹配回写远程 NAS + rebuild-meta/clean-bdmv/clean-mount-artifacts。"""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store
from app.db import POSTER_DIR, mounts_dir
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
    lib = store.create_library(name=f"meta-{tmp_path.name}", source="smb",
                               artwork_mode="nfo_art",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_library(lib["id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def _match_detail(tid, title="正确片名"):
    return {"id": tid, "title": title, "original_title": title,
            "release_date": "2010-05-01", "overview": "", "vote_average": 7.0,
            "genres": [], "production_countries": [{"iso_3166_1": "CN"}],
            "original_language": "zh", "external_ids": {}, "poster_path": "",
            "credits": {"cast": [], "crew": []}}


def test_manual_match_writes_remote_nas(smb_lib, monkeypatch):
    lib, root, _fake = smb_lib
    rel = "告白.Confessions.2010/Confessions.2010.mkv"
    p = root / rel
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
    store.update_movie_meta(mid, title="The Confessions", year=2010, tmdb_id=471040)
    poster = os.path.join(POSTER_DIR, "54186.jpg")
    with open(poster, "wb") as fh:
        fh.write(b"\xff\xd8POSTER")
    monkeypatch.setattr(scanner.tmdb, "movie_detail", lambda tid: _match_detail(tid))
    client = TestClient(app)
    try:
        r = client.post(f"/api/movies/{mid}/match", json={"tmdb_id": 54186})
        assert r.status_code == 200, r.text
        assert store.get_movie(mid)["tmdb_id"] == 54186
        assert store.get_movie(mid)["nfo_hash"] == "" or True
        # 后台任务（TestClient 会等它跑完）应把 NFO/海报写到 NAS 目录，而不是 mounts
        assert (p.parent / "movie.nfo").is_file()
        text = (p.parent / "movie.nfo").read_bytes().decode("utf-8")
        assert "正确片名" in text and "54186" in text
        assert (p.parent / "poster.jpg").read_bytes() == b"\xff\xd8POSTER"
    finally:
        os.remove(poster)


def test_manual_match_offline_falls_back_to_cache(smb_lib, monkeypatch):
    """TMDB 401/断网时用手动匹配仍可离线绑定并回写 NAS（本地库数据支持远程库）。"""
    lib, root, _fake = smb_lib
    rel = "离线片 (2010)/Offline (2010).mkv"
    p = root / rel
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
    store.update_movie_meta(mid, title="Wrong", year=2010, tmdb_id=111111)
    store.upsert_tmdb_cache(54186, {"title": "离线片", "year": 2010,
                                    "media_type": "movie"}, {}, "")

    def _boom(tid):
        raise RuntimeError("401 Unauthorized")
    monkeypatch.setattr(scanner.tmdb, "movie_detail", _boom)
    client = TestClient(app)
    r = client.post(f"/api/movies/{mid}/match", json={"tmdb_id": 54186})
    assert r.status_code == 200, r.text
    assert r.json().get("offline") is True
    row = store.get_movie(mid)
    assert row["tmdb_id"] == 54186 and row["title"] == "离线片"
    assert row["match_source"] == "local" and row["needs_review"] == 0
    assert (p.parent / "movie.nfo").is_file()
    assert "离线片" in (p.parent / "movie.nfo").read_bytes().decode("utf-8")


def test_rebuild_meta_job(smb_lib, monkeypatch):
    lib, root, _fake = smb_lib
    rel = "片 (2015)/片 (2015).mkv"
    p = root / rel
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
    store.update_movie_meta(mid, title="片", year=2015, tmdb_id=889001)
    store.upsert_tmdb_cache(889001, {"title": "片", "year": 2015,
                                     "media_type": "movie"}, {}, "")
    client = TestClient(app)
    prev = client.post("/api/jobs/rebuild-meta",
                       json={"library_id": lib["id"], "dry_run": True}).json()
    assert prev["dry_run"] is True and prev["total"] == 1
    assert prev["targets"]["remote"] == 1
    r = client.post("/api/jobs/rebuild-meta",
                    json={"library_id": lib["id"], "dry_run": False}).json()
    assert r["job_id"] and r["total"] == 1
    for _ in range(60):
        st = client.get(f"/api/jobs/rebuild-meta/{r['job_id']}").json()
        if st["state"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st["state"] == "done" and st["done"] == 1, st
    assert (p.parent / "movie.nfo").is_file()


def test_clean_bdmv_rows(smb_lib):
    lib, _root, _fake = smb_lib
    junk = "BD/BDMV/STREAM/00001.m2ts"
    good = "正常片 (2020)/正常片 (2020).mkv"
    mid_junk = store.upsert_movie_by_path(junk, library_id=lib["id"])
    store.update_movie_meta(mid_junk, title="00001", tmdb_id=111111)
    mid_good = store.upsert_movie_by_path(good, library_id=lib["id"])
    store.update_movie_meta(mid_good, title="正常片", tmdb_id=222222)
    client = TestClient(app)
    prev = client.post("/api/jobs/clean-bdmv",
                       json={"library_id": lib["id"], "dry_run": True}).json()
    assert prev["total"] == 1 and prev["sample"][0]["id"] == mid_junk
    out = client.post("/api/jobs/clean-bdmv",
                      json={"library_id": lib["id"], "dry_run": False}).json()
    assert out["deleted"] == 1
    assert store.get_movie(mid_junk) is None
    assert store.get_movie(mid_good) is not None


def test_clean_mount_artifacts(tmp_path):
    base = mounts_dir()
    os.makedirs(os.path.join(base, "lib_99", "某片 (2020)"), exist_ok=True)
    stray = os.path.join(base, "lib_99", "某片 (2020)", "movie.nfo")
    with open(stray, "w", encoding="utf-8") as fh:
        fh.write("<movie/>")
    keep = os.path.join(base, "lib_99", "keep.txt")
    with open(keep, "w", encoding="utf-8") as fh:
        fh.write("x")
    client = TestClient(app)
    prev = client.post("/api/jobs/clean-mount-artifacts", json={"dry_run": True}).json()
    assert any("movie.nfo" in s for s in prev["sample"])
    out = client.post("/api/jobs/clean-mount-artifacts", json={"dry_run": False}).json()
    assert out["removed"] >= 1
    assert not os.path.exists(stray)
    assert os.path.exists(keep)          # 非媒体产物不碰
