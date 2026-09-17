"""B9 后续（问题 3）：文件浏览复制——冲突副本命名 / 目录递归 / 嵌套拒绝 / 正片登记。"""
import os
import pathlib
import time

from fastapi.testclient import TestClient

from app import scanner, store
from app.main import app

client = TestClient(app)


def _mk(root: pathlib.Path, rel: str, data: bytes = b"x") -> pathlib.Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _wait_copy(job_id: str, timeout: float = 10.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/fs/copy/{job_id}").json()
        if st.get("state") in ("done", "failed", "cancelled"):
            return st
        time.sleep(0.02)
    raise AssertionError(f"copy job timeout: {st}")


def test_copy_file_conflict_autoname(media_root):
    _mk(media_root, "src/待整理/Movie.mkv", b"AAA")
    _mk(media_root, "dst/Movie.mkv", b"BBB")
    d = client.post("/api/fs/copy", json={"from": ["src/待整理/Movie.mkv"], "to_dir": "dst"}).json()
    assert d["dry_run"] and d["conflicts"] == ["Movie.mkv"]
    r = client.post("/api/fs/copy", json={"from": ["src/待整理/Movie.mkv"], "to_dir": "dst", "dry_run": False}).json()
    st = _wait_copy(r["job_id"])
    assert st["state"] == "done" and st["renamed"] == 1
    assert (media_root / "dst/Movie.mkv").read_bytes() == b"BBB"          # 原文件不动
    assert (media_root / "dst/Movie (副本).mkv").read_bytes() == b"AAA"
    # 再复制一次 → (副本 2)
    r2 = client.post("/api/fs/copy", json={"from": ["src/待整理/Movie.mkv"], "to_dir": "dst", "dry_run": False}).json()
    _wait_copy(r2["job_id"])
    assert (media_root / "dst/Movie (副本 2).mkv").exists()


def test_copy_dir_recursive_and_nested_reject(media_root):
    _mk(media_root, "tree/Show/a.mkv", b"A")
    _mk(media_root, "tree/Show/sub/b.srt", b"B")
    # 目录复制进自身 → 422
    bad = client.post("/api/fs/copy", json={"from": ["tree/Show"], "to_dir": "tree/Show"})
    assert bad.status_code == 422
    r = client.post("/api/fs/copy", json={"from": ["tree/Show"], "to_dir": "backup", "dry_run": False}).json()
    st = _wait_copy(r["job_id"])
    assert st["state"] == "done"
    root = media_root / "backup/Show"
    assert (root / "a.mkv").read_bytes() == b"A"
    assert (root / "sub/b.srt").read_bytes() == b"B"


def test_scan_one_tmdb_hint_skips_search(media_root, monkeypatch):
    rel = "hint/Copied.Movie.2020.mkv"
    _mk(media_root, rel, b"X")
    store.upsert_tmdb_cache(123321, {
        "title": "Hinted", "original_title": "Hinted", "year": 2020,
        "overview": "", "tmdb_id": 123321, "imdb_id": "", "tmdb_rating": 8.0,
        "genres": [], "genre_ids": [], "original_language": "en",
        "origin_countries": [], "origin_country": "", "region": "",
        "media_type": "movie", "collection_tmdb_id": None,
        "collection_name": "", "collection_poster_path": "",
    }, {"cast": [], "crew": []}, "")
    def boom(q, year=None):
        raise AssertionError("search should not be called with tmdb_hint")
    monkeypatch.setattr(scanner.tmdb, "search_movie", boom)
    def _apply_cached(mid, tmdb_id, abs_path):
        store.update_movie_meta(mid, tmdb_id=tmdb_id)
        return {"title": "Hinted", "year": 2020, "tmdb_id": tmdb_id, "nfo": False}
    monkeypatch.setattr(scanner.scan, "apply_cached_to_movie", _apply_cached)
    r = scanner.scan_one(str(media_root / rel), tmdb_hint=123321)
    assert r["status"] in ("ok", "ok_needs_review") and r["tmdb_id"] == 123321
    assert (store.get_by_path(rel) or {}).get("tmdb_id") == 123321


def test_copy_feature_registers_version(media_root, monkeypatch):
    """正片复制 → 目标自动登记（tmdb_hint 直绑，不重搜）。"""
    rel = "lib/Feature.2019.mkv"
    _mk(media_root, rel, b"F")
    store.upsert_tmdb_cache(556677, {
        "title": "Feature", "original_title": "Feature", "year": 2019,
        "overview": "", "tmdb_id": 556677, "imdb_id": "", "tmdb_rating": 7.0,
        "genres": [], "genre_ids": [], "original_language": "en",
        "origin_countries": [], "origin_country": "", "region": "",
        "media_type": "movie", "collection_tmdb_id": None,
        "collection_name": "", "collection_poster_path": "",
    }, {"cast": [], "crew": []}, "")
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, tmdb_id=556677, title="Feature", year=2019)
    def boom(q, year=None):
        raise AssertionError("no search expected for copy of matched feature")
    monkeypatch.setattr(scanner.tmdb, "search_movie", boom)
    def _apply_cached(m, tmdb_id, abs_path):
        store.update_movie_meta(m, tmdb_id=tmdb_id)
        return {"title": "Feature", "year": 2019, "tmdb_id": tmdb_id, "nfo": False}
    monkeypatch.setattr(scanner.scan, "apply_cached_to_movie", _apply_cached)
    r = client.post("/api/fs/copy", json={"from": [rel], "to_dir": "copies", "dry_run": False}).json()
    st = _wait_copy(r["job_id"])
    assert st["state"] == "done" and st["registered"] == 1
    copy_rel = "copies/Feature.2019.mkv"
    row = store.get_by_path(copy_rel)
    assert row and row["tmdb_id"] == 556677


def test_copy_to_root(media_root):
    _mk(media_root, "inbox/Stray.srt", b"S")
    r = client.post("/api/fs/copy", json={"from": ["inbox/Stray.srt"], "dry_run": False}).json()
    st = _wait_copy(r["job_id"])
    assert st["state"] == "done"
    assert (media_root / "Stray.srt").read_bytes() == b"S"
