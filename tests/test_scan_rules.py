"""B1 批次（扫描正确性）回归网：P1-02 样片误杀 / P1-03 剧集入库 / P1-04 walk 剪枝 /
P1-05 clean-sidecars 保护。

红→绿约定：本文件在修复前允许失败（失败即证明 bug 存在）。
"""
import os
import pathlib

import pytest

from app import scanner, store
from app.config import settings
from app.routers import files as files_router


@pytest.fixture(autouse=True)
def _offline_tmdb(monkeypatch):
    """扫描链路禁止触网：search 返回空。"""
    monkeypatch.setattr(scanner.tmdb, "search_movie", lambda q, year=None: [])


def _touch(root: pathlib.Path, rel: str) -> str:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return str(p)


def _make_movie_row(rel: str, tmdb_id=None, title="T", year=2020) -> int:
    mid = store.upsert_movie_by_path(rel)
    meta = {"title": title, "year": year}
    if tmdb_id:
        meta["tmdb_id"] = tmdb_id
    store.update_movie_meta(mid, **meta)
    return mid


# ---------- P1-02：is_sample 误杀 ----------

@pytest.mark.parametrize("name", [
    "Inception.2010.1080p.BluRay.sample.mkv",
    "Inception.2010.sample.mkv",
    "movie-sample.mkv",
    "sample.mkv",
    "Movie.2020.sample.1080p.mkv",
    "Movie.sample.remux.mkv",
    "Samples.720p.mkv",
])
def test_is_sample_true(name):
    assert scanner.is_sample(name) is True


@pytest.mark.parametrize("name", [
    "The Sample Movie (2024).mkv",
    "Sample.This.2012.mkv",
    "Some.Sample.2020.mkv",
    "The.Sample.Movie.2024.mkv",
    "Sampling.2020.mkv",
])
def test_is_sample_false(name):
    assert scanner.is_sample(name) is False


def test_is_sidecar_keeps_sample_titled_movies():
    assert scanner.is_sidecar("The Sample Movie (2024).mkv") is False
    assert scanner.is_feature_video("The Sample Movie (2024).mkv") is True


# ---------- B5a-1（R03-B1）：重扫不覆盖手动标题 ----------

def test_scan_keeps_manual_title_on_no_match(media_root):
    rel = "keep/Manual.Movie.2024.mkv"
    _touch(media_root, rel)
    r = scanner.scan_one(str(media_root / rel))
    assert r["status"] == "no_match"
    mid = store.get_by_path(rel)["id"]
    store.update_movie_local(mid, title="我改的标题")
    scanner.scan_one(str(media_root / rel))    # 重扫（离线仍 no_match）
    assert store.get_by_path(rel)["title"] == "我改的标题"


# ---------- B5a-2（R03-D6）：年份对不上也采信时标 needs_review ----------

def _stub_match(monkeypatch, release_date: str, tmdb_id: int):
    monkeypatch.setattr(scanner.tmdb, "search_movie", lambda q, year=None: [
        {"id": tmdb_id, "title": "Year Test", "original_title": "Year Test",
         "release_date": release_date, "vote_average": 7.0}])
    monkeypatch.setattr(scanner.tmdb, "movie_detail", lambda tid: {
        "id": tid, "title": "Year Test", "original_title": "Year Test",
        "release_date": release_date, "overview": "", "vote_average": 7.0,
        "genres": [], "production_countries": [], "original_language": "en",
        "external_ids": {}, "poster_path": "",
        "credits": {"cast": [], "crew": []}})
    def _apply(mid, detail, abs_path):
        store.update_movie_meta(mid, tmdb_id=detail["id"])
        return {"title": "Year Test", "year": 2024, "tmdb_id": detail["id"],
                "nfo": False}

    monkeypatch.setattr(scanner.scan, "apply_tmdb_detail", _apply)


def test_scan_year_mismatch_marks_needs_review(media_root, monkeypatch):
    rel = "keep/Year.Test.2024.mkv"
    _touch(media_root, rel)
    _stub_match(monkeypatch, "1999-01-01", 424242)
    r = scanner.scan_one(str(media_root / rel))
    assert r["status"] == "ok_needs_review"
    row = store.get_by_path(rel)
    assert row["needs_review"] == 1
    assert row["tmdb_id"] == 424242


def test_scan_year_match_stays_ok(media_root, monkeypatch):
    rel = "keep/Year.Ok.2024.mkv"
    _touch(media_root, rel)
    _stub_match(monkeypatch, "2024-05-01", 424243)
    r = scanner.scan_one(str(media_root / rel))
    assert r["status"] == "ok"


# ---------- P1-03：剧集不得入库 ----------

def test_episode_not_inserted(media_root):
    rel = "Some.Show.S01E01.1080p.mkv"
    _touch(media_root, rel)
    r = scanner.scan_one(str(media_root / rel))
    assert r["status"] == "skipped_episode_v1"
    assert store.get_by_path(rel) is None


# ---------- P1-04：walk 剪枝 ----------

def test_scan_all_prunes_recycle_and_hidden(media_root):
    _touch(media_root, "#recycle/deleted.mkv")
    _touch(media_root, "@eaDir/thumb.mkv")
    _touch(media_root, ".hidden/secret.mkv")
    _touch(media_root, "Normal.Movie.2020.mkv")
    scanner.scan_all()
    assert store.get_by_path("#recycle/deleted.mkv") is None
    assert store.get_by_path("@eaDir/thumb.mkv") is None
    assert store.get_by_path(".hidden/secret.mkv") is None
    # 正向对照：正常文件仍被遍历（离线为 no_match，但会建行）
    assert store.get_by_path("Normal.Movie.2020.mkv") is not None


# ---------- P1-05：clean-sidecars 保护 ----------

def test_clean_sidecars_protects_tmdb_rows(media_root):
    rel = "花絮/making.mkv"          # is_sidecar 为真（花絮目录）
    _touch(media_root, rel)
    mid = _make_movie_row(rel, tmdb_id=990001)
    prev = files_router.clean_sidecars({"dry_run": True})
    assert mid not in {p["id"] for p in prev["plans"]}


def test_clean_sidecars_still_flags_unmatched_sidecar(media_root):
    rel = "junk.movie.trailer.mkv"
    _touch(media_root, rel)
    mid = _make_movie_row(rel)
    prev = files_router.clean_sidecars({"dry_run": True})
    assert mid in {p["id"] for p in prev["plans"]}


# ---------- P1-03 配套：历史剧集脏行清理口 ----------

def test_clean_episodes_preview_and_delete(media_root):
    rel = "Some.Show.S02E03.1080p.mkv"
    _touch(media_root, rel)
    mid = _make_movie_row(rel)      # 模拟历史脏行
    prev = files_router.clean_episodes({"dry_run": True})
    assert mid in {p["id"] for p in prev["plans"]}
    files_router.clean_episodes({"dry_run": False, "ids": [mid]})
    assert store.get_movie(mid) is None


def test_clean_episodes_protects_matched_rows(media_root):
    rel = "Some.Show.S03E01.1080p.mkv"
    _touch(media_root, rel)
    mid = _make_movie_row(rel, tmdb_id=990002, title="Some Show")
    prev = files_router.clean_episodes({"dry_run": True})
    assert mid not in {p["id"] for p in prev["plans"]}


# ---------- B7-DELETE（R02-B2/R08-B5/R09-B5）：删片置空花絮归属 ----------

def test_delete_movie_orphans_extras(media_root):
    rel = "del/owner.mkv"
    _touch(media_root, rel)
    mid = _make_movie_row(rel, title="Owner")
    eid = store.upsert_extra("del/making.mkv", mid, "behindthescenes")
    assert store.list_orphan_extras() == [] or all(
        e["id"] != eid for e in store.list_orphan_extras())

    assert store.delete_movie(mid) is True
    e = store.get_extra(eid)
    assert e is not None and e["movie_id"] is None          # 不悬挂
    assert any(x["id"] == eid for x in store.list_orphan_extras())  # 出现在 orphan 列表


# ---------- B9/R03-Q3：增量扫描（未匹配文件不再每次重打 TMDB） ----------

def test_scan_unmatched_skips_unchanged(media_root, monkeypatch):
    calls = {"n": 0}

    def fake_search(q, year=None):
        calls["n"] += 1
        return []

    monkeypatch.setattr(scanner.tmdb, "search_movie", fake_search)
    rel = "incr/Unmatched.Movie.2021.mkv"
    _touch(media_root, rel)
    r1 = scanner.scan_one(str(media_root / rel))
    assert r1["status"] == "no_match" and calls["n"] == 1
    r2 = scanner.scan_one(str(media_root / rel))
    assert r2["status"] == "skipped_unchanged" and calls["n"] == 1   # 未再打 TMDB
    # 文件变更（mtime/size 变）→ 重新搜索
    p = media_root / rel
    p.write_bytes(b"x" * 32)
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))
    r3 = scanner.scan_one(str(media_root / rel))
    assert r3["status"] == "no_match" and calls["n"] == 2

def test_scan_episode_skips_unchanged(media_root):
    rel = "incr/Show.S01E01.mkv"
    _touch(media_root, rel)
    assert scanner.scan_one(str(media_root / rel))["status"] == "skipped_episode_v1"
    assert scanner.scan_one(str(media_root / rel))["status"] == "skipped_unchanged"


# ---------- B9 后续：刮削失败也建行可见（scan_failed）+ 手动重试 ----------

def test_scan_tmdb_error_keeps_row(media_root, monkeypatch):
    def boom(q, year=None):
        raise RuntimeError("tmdb unreachable")
    monkeypatch.setattr(scanner.tmdb, "search_movie", boom)
    rel = "faildir/The.Mummy.1999.BD1080p.mp4"
    _touch(media_root, rel)
    r = scanner.scan_one(str(media_root / rel))
    assert r["status"] == "scan_failed" and r.get("movie_id")
    row = store.get_by_path(rel)
    assert row and row["tmdb_id"] is None                     # 行已建：海报墙/匹配确认可见
    assert row["title"] == "The Mummy" and row["year"] == 1999


def test_scan_tmdb_error_then_manual_rescan(media_root, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    def boom(q, year=None):
        raise RuntimeError("tmdb unreachable")
    monkeypatch.setattr(scanner.tmdb, "search_movie", boom)
    rel = "faildir/Retry.Movie.2021.mkv"
    _touch(media_root, rel)
    mid = (scanner.scan_one(str(media_root / rel)) or {}).get("movie_id")
    assert mid
    # 恢复 TMDB → 手动重试
    monkeypatch.setattr(scanner.tmdb, "search_movie", lambda q, year=None: [
        {"id": 777001, "title": "Retry Movie", "original_title": "Retry Movie",
         "release_date": "2021-05-01", "vote_average": 7.0}])
    monkeypatch.setattr(scanner.tmdb, "movie_detail", lambda tid: {
        "id": tid, "title": "Retry Movie", "original_title": "Retry Movie",
        "release_date": "2021-05-01", "overview": "", "vote_average": 7.0,
        "genres": [], "production_countries": [], "original_language": "en",
        "external_ids": {}, "poster_path": "", "credits": {"cast": [], "crew": []}})
    def _apply(mid2, detail, abs_path):
        store.update_movie_meta(mid2, tmdb_id=detail["id"])
        return {"title": "Retry Movie", "year": 2021, "tmdb_id": detail["id"], "nfo": False}
    monkeypatch.setattr(scanner.scan, "apply_tmdb_detail", _apply)
    d = client.post(f"/api/movies/{mid}/rescan").json()
    assert d["status"] in ("ok", "ok_needs_review") and d["tmdb_id"] == 777001
    assert (store.get_movie(mid) or {}).get("tmdb_id") == 777001


def test_unmatched_api_lists_failed_scan(media_root, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)

    def boom(q, year=None):
        raise RuntimeError("offline")
    monkeypatch.setattr(scanner.tmdb, "search_movie", boom)
    rel = "faildir/Visible.In.List.2002.mkv"
    _touch(media_root, rel)
    scanner.scan_one(str(media_root / rel))
    d = client.get("/api/files/unmatched").json()
    assert any(u["file_path"] == rel for u in d["unmatched"])
