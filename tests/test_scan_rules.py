"""B1 批次（扫描正确性）回归网：P1-02 样片误杀 / P1-03 剧集入库 / P1-04 walk 剪枝 /
P1-05 clean-sidecars 保护。

红→绿约定：本文件在修复前允许失败（失败即证明 bug 存在）。
"""
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
