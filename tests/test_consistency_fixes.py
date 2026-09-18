"""B7-FAIL 组：失败语义/一致性修复（先删库后删盘、去重 FTS、ff_index 缺失、系列查重、
assert 控制流、成员插入日志）。"""
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import store
from app.config import settings
from app.main import app
from app.routers import movies as movies_router

client = TestClient(app)


def _touch(root: pathlib.Path, rel: str) -> pathlib.Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def _row(rel, title="T", year=2020, tmdb_id=None):
    mid = store.upsert_movie_by_path(rel)
    meta = {"title": title, "year": year}
    if tmdb_id:
        meta["tmdb_id"] = tmdb_id
    store.update_movie_meta(mid, **meta)
    return mid


# ---------- R05-B3：batch-delete 先库后盘 ----------

def test_batch_delete_db_first_and_reports_failures(media_root, monkeypatch):
    rel = "b7del/movie.mkv"
    _touch(media_root, rel)
    mid = _row(rel)

    # 模拟盘删失败：os.remove 抛 OSError → 库行仍应已删除，失败被上报
    import os as _os
    real_remove = _os.remove

    def _boom(path):
        if path.endswith("movie.mkv"):
            raise OSError("permission denied")
        return real_remove(path)

    monkeypatch.setattr(movies_router.os, "remove", _boom)
    r = client.post("/api/movies/batch-delete",
                    json={"ids": [mid], "dry_run": False, "confirm": True})
    assert r.status_code == 200
    res = (r.json().get("results") or [])[0]
    assert res["status"] == "deleted_with_errors"
    assert res["failed_files"] and "permission denied" in res["failed_files"][0]["error"]
    assert store.get_movie(mid) is None          # 库行已清（不会挂死行）
    assert (media_root / rel).is_file()          # 盘上文件因失败保留（重扫可认领）


# ---------- R13-D6：探测缺 ff_index 的烧录请求 422 ----------

def test_burn_without_ff_index_422(media_root):
    rel = "b7sub/movie.mkv"
    _touch(media_root, rel)
    mid = _row(rel)
    store.upsert_media_info(mid, {
        "playable": True, "container": "matroska", "duration": 10.0,
        "width": 1920, "height": 1080, "vcodec": "h264", "bit_depth": 8,
        "audio": [], "attachments": [],
        "subs": [{"index": 0, "codec": "dvd_subtitle", "image": 1,
                  "lang": "", "title": "", "default": 0, "forced": 0}],  # 缺 ff_index
        "probe_ver": 3, "probed_at": 1})
    r = client.post(f"/api/stream/{mid}/sessions", json={"quality": "auto", "sub": 0})
    assert r.status_code == 422
    assert "stream index" in r.text


# ---------- R06-D5：系列已收录 409 ----------

def test_from_tmdb_series_conflict_409(media_root):
    rel = "b7col/film.mkv"
    _touch(media_root, rel)
    tid = 770001
    mid = _row(rel, title="Series Film", tmdb_id=tid)
    store.upsert_tmdb_cache(tid, {
        "title": "Series Film", "original_title": "", "year": 2024,
        "overview": "", "tmdb_id": tid, "imdb_id": "", "tmdb_rating": 7.0,
        "genres": [], "genre_ids": [], "origin_country": "", "origin_countries": [],
        "original_language": "en", "region": "欧美", "media_type": "movie",
        "collection_tmdb_id": 990001, "collection_name": "B7 Series",
        "collection_poster_path": "",
    }, credits={"cast": [], "crew": []})
    assert store.collection_hint_for_movie(mid)["already_collected"] is False

    c = client.post("/api/collections", json={"name": "B7 Series", "tmdb_collection_id": 990001})
    assert c.status_code == 200
    r = client.post("/api/collections/from-tmdb-series", json={"movie_id": mid})
    assert r.status_code == 409


# ---------- R02-B5：upsert_media_info 不再依赖 assert ----------

def test_upsert_media_info_roundtrip(media_root):
    rel = "b7probe/movie.mkv"
    _touch(media_root, rel)
    mid = _row(rel)
    out = store.upsert_media_info(mid, {
        "playable": True, "container": "mp4", "duration": 5.0,
        "width": 640, "height": 360, "vcodec": "h264", "bit_depth": 8,
        "audio": [], "subs": [], "attachments": [],
        "probe_ver": 3, "probed_at": 1})
    assert out["playable"] is True and out["item_id"] == mid and out["kind"] == "movie"


# ---------- R03-B2：头像下载失败保留旧图 ----------

def test_avatar_failure_keeps_old(media_root, monkeypatch):
    from app import scanner
    rel = "b7ava/movie.mkv"
    _touch(media_root, rel)
    mid = _row(rel)
    pid = 880001
    avatar_rel = "posters/person_880001.jpg"
    ap = pathlib.Path(settings.data_dir) / avatar_rel
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_bytes(b"old-avatar")
    store.upsert_person(pid, "Actor", avatar=avatar_rel, profile_tmdb_path="/old.jpg")

    monkeypatch.setattr(scanner.tmdb, "download_poster", lambda *a, **kw: False)
    # 远端换图 + 下载失败 → 保留旧头像，不清空
    n = scanner._sync_jobs(mid, [(pid, "Actor", "/new.jpg", "actor", "", 0)])
    assert n == 0
    assert store.get_person_raw(pid)["avatar"] == avatar_rel
    assert ap.is_file()
