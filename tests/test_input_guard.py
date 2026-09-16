"""B6-INPUT 输入校验组回归网：symlink 越界、restore 边界、ids 强转/上界、
tmdb_id 值域、caps 严格 bool、LIKE 转义、VobSub→VTT 415。"""
import os
import pathlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import store
from app.caps import normalize_caps
from app.config import settings
from app.main import app
from app.routers import collections as collections_router
from app.routers import files as files_router

client = TestClient(app)


def _touch(root: pathlib.Path, rel: str) -> pathlib.Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def _row(rel, tmdb_id=None, title="T", year=2020):
    mid = store.upsert_movie_by_path(rel)
    meta = {"title": title, "year": year}
    if tmdb_id:
        meta["tmdb_id"] = tmdb_id
    store.update_movie_meta(mid, **meta)
    return mid


# ---------- R09-B1：符号链接越界 ----------

def test_check_inside_root_rejects_symlink_escape(media_root, tmp_path):
    outside = tmp_path / "outside-root"
    outside.mkdir()
    link = media_root / "escape-link"
    try:
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(str(outside), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlink not supported")
    assert files_router._check_inside_root("normal/a.mkv") == "normal/a.mkv"
    with pytest.raises(HTTPException) as e:
        files_router._check_inside_root("escape-link/x.mkv")
    assert e.value.status_code == 422


# ---------- R09-B4：restore 目标越界防御 ----------

def test_restore_one_rejects_out_of_root_target(media_root):
    rel = "guard/a.mkv"
    _touch(media_root, rel)
    mid = _row(rel)
    store.update_movie_local(mid, original_file_path="../../evil/a.mkv")
    r = files_router._restore_one(store.get_movie(mid), dry_run=False)
    assert r["status"].startswith("error")
    assert (media_root / rel).is_file()          # 未移动


# ---------- R09-B2：only ids 强转与上界 ----------

def test_only_ids_coercion_and_bounds():
    assert files_router._only_ids({"ids": ["1", 2]}) == {1, 2}
    assert files_router._only_ids({"ids": []}) is None
    with pytest.raises(HTTPException) as e:
        files_router._only_ids({"ids": ["abc"]})
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e2:
        files_router._only_ids({"ids": list(range(6000))})
    assert e2.value.status_code == 422


def test_organize_rejects_bad_ids():
    r = client.post("/api/files/organize", json={"mode": "inplace", "ids": ["abc"]})
    assert r.status_code == 422


# ---------- R06-B3：合集 id 上界与列表上限 ----------

def test_collection_ids_bounds():
    with pytest.raises(HTTPException) as e:
        collections_router._ids_from({"movie_ids": [10 ** 30]})
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e2:
        collections_router._ids_from({"movie_ids": list(range(3000))})
    assert e2.value.status_code == 422
    assert collections_router._ids_from({"movie_ids": ["3"]}) == [3]


def test_collection_create_rejects_huge_member_id():
    r = client.post("/api/collections", json={"name": "huge-id", "member_ids": [10 ** 30]})
    assert r.status_code == 422


# ---------- R07-B2：tmdb_id 值域 ----------

def test_person_huge_tmdb_id_404_not_500():
    r = client.get("/api/persons/99999999999999999999")
    assert r.status_code == 404
    r2 = client.post("/api/persons/99999999999999999999/refresh")
    assert r2.status_code == 404


def test_match_huge_tmdb_id_422():
    rel = "guard/match.mkv"
    p = pathlib.Path(settings.media_root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = _row(rel)
    r = client.post(f"/api/movies/{mid}/match", json={"tmdb_id": 10 ** 30})
    assert r.status_code == 422


# ---------- R11-B5：caps 严格 bool ----------

def test_normalize_caps_strict_bools():
    d = normalize_caps({"video": {"h264": "false", "hevc": True},
                        "audio": {"aac": 1, "mp3": True},
                        "hdr": "true", "mse": False, "native_hls": "yes",
                        "probes": {"x": "true", "y": True}})
    assert d["video"]["h264"] is False          # 字符串不再当真
    assert d["video"]["hevc"] is True
    assert d["audio"]["aac"] is False           # 1 不是 True
    assert d["audio"]["mp3"] is True
    assert d["hdr"] is False
    assert d["mse"] is False
    assert d["native_hls"] is False
    assert d["probes"] == {"x": False, "y": True}
    assert normalize_caps({})["mse"] is True    # 缺省仍保守开启


# ---------- R02-B1：LIKE 转义 ----------

def test_list_collections_like_escaped():
    store.create_collection("100%合集")
    store.create_collection("100X合集")
    names = {c["name"] for c in store.list_collections("100%")}
    assert "100%合集" in names
    assert "100X合集" not in names


# ---------- R13-B3：VobSub 请求 VTT 必须 415 ----------

def test_vobsub_vtt_returns_415(media_root):
    rel = "guard/sub.mkv"
    _touch(media_root, rel)
    mid = _row(rel)
    store.upsert_media_info(mid, {
        "playable": True, "container": "matroska", "duration": 10.0,
        "width": 1920, "height": 1080, "vcodec": "h264", "bit_depth": 8,
        "audio": [], "attachments": [],
        "subs": [{"index": 0, "ff_index": 3, "codec": "dvd_subtitle",
                  "image": 1, "lang": "", "title": "", "default": 0, "forced": 0}],
        "probe_ver": 3, "probed_at": 1})
    r = client.get(f"/api/stream/{mid}/sub/0.vtt")
    assert r.status_code == 415
