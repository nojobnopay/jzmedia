"""B7-EXTRAS：归属纯函数、planner 规划、合集补全 job 状态机（全部无网络/无 ffmpeg）。"""
import pathlib
import time

import pytest

from app import scanner, store
from app.config import settings


# ---------- R08-Q1：归属纯函数 ----------

@pytest.mark.parametrize("raw,expect", [
    ("预告-功夫", "功夫"),
    ("Making of Inception", "Inception"),
    ("功夫-花絮", "功夫"),
    ("特辑：功夫", "功夫"),
    ("预告-特辑-功夫", "功夫"),
    ("普通标题", "普通标题"),
    ("", ""),
])
def test_strip_kind_affix(raw, expect):
    assert scanner.strip_kind_affix(raw) == expect


def test_extra_kind_by_dir_and_name():
    assert scanner.extra_kind("extras/x.mkv") == "extra"
    assert scanner.extra_kind("电影/Trailers/x.mkv") == "trailer"
    assert scanner.extra_kind("电影/花絮/x.mkv") == "behindthescenes"
    assert scanner.extra_kind("电影/x.deleted.scenes.mkv") == "deleted"
    assert scanner.extra_kind("电影/x.mkv") == "extra"


def test_attribute_extra_attach_and_orphan(media_root):
    rel_owner = "att/功夫 (2004).mkv"
    p = media_root / rel_owner
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel_owner)
    store.update_movie_meta(mid, title="功夫", year=2004)

    extra = media_root / "att/功夫-花絮.mkv"
    extra.write_bytes(b"x")
    r = scanner.attribute_extra(str(extra))
    assert r["status"] == "extra_attached" and r["movie_id"] == mid

    orphan = media_root / "att/无人认领 (1999).mkv"
    orphan.write_bytes(b"x")
    r2 = scanner.attribute_extra(str(orphan))
    assert r2["status"] == "extra_orphan"


# ---------- R09-Q2：planner 规划 fixture ----------

def _row(i, path, title, year, tmdb=None, spec="", region="欧美"):
    return {"id": i, "file_path": path, "title": title, "year": year,
            "tmdb_id": tmdb, "spec": spec, "region": region,
            "edition": "", "original_file_path": path, "media_type": "movie"}


@pytest.fixture()
def _planner(monkeypatch):
    from app.routers import files as files_router

    def run(rows):
        monkeypatch.setattr(files_router.store, "list_movies", lambda **kw: rows)
        return files_router._collect_plans()
    return run


def test_planner_min_suffix_versions(_planner):
    rows = [
        _row(1, "in/功夫 (2004) 1080p.mkv", "功夫", 2004, tmdb=101),
        _row(2, "in/功夫 (2004) 2160p.mkv", "功夫", 2004, tmdb=101),
    ]
    plans, conflicts = _planner(rows)
    assert not conflicts and len(plans) == 2
    tos = {p["to"] for p in plans}
    assert any("-1080P" in t for t in tos) and any("-2160P" in t for t in tos)


def test_planner_suspect_mismatch_not_auto_suffixed(_planner):
    # 同名同 tmdb 但主干不同（不同文件）→ 疑似错配，不自动加后缀
    rows = [
        _row(1, "in/A.Movie.2020.mkv", "Movie", 2020, tmdb=202),
        _row(2, "in/B.Movie.2020.mkv", "Movie", 2020, tmdb=202),
    ]
    plans, conflicts = _planner(rows)
    assert not plans
    assert conflicts and all(c["status"] == "conflict_needs_rematch" for c in conflicts)


def test_planner_numbered_fallback_for_twins(_planner):
    # 规格词完全一致（压制组被主干剔除）→ 首个保持，其余 -版本2
    rows = [
        _row(1, "in/电影 (2020) 1080p.FRDS.mkv", "电影", 2020, tmdb=303),
        _row(2, "in/电影 (2020) 1080p.CHD.mkv", "电影", 2020, tmdb=303),
    ]
    plans, conflicts = _planner(rows)
    assert not conflicts and len(plans) == 2
    assert any(p.get("numbered") == "版本2" for p in plans)


# ---------- R06-Q4：补全 job 状态机（复用/取消） ----------

def test_backfill_resume_and_cancel(monkeypatch):
    from app.routers import collections as col

    monkeypatch.setattr(col.store, "tmdb_ids_missing_collection",
                        lambda limit, force: [1, 2])
    monkeypatch.setattr(scanner, "refresh_tmdb_id_fast",
                        lambda tid: ({"changed": False}, []))
    with col._JOBS_LOCK:
        saved = dict(col._JOBS)
        col._JOBS.clear()
    try:
        r1 = col.suggest_backfill({"limit": 10})
        assert r1["resumed"] is False and r1["total"] == 2
        r2 = col.suggest_backfill({"limit": 10})
        assert r2["resumed"] is True and r2["job_id"] == r1["job_id"]

        jid = r1["job_id"]
        c = col.suggest_backfill_cancel({"job_id": jid})
        assert c["state"] in ("cancelled", "done", "running")
        for _ in range(50):
            st = col.suggest_backfill_status(jid)
            if st["state"] in ("done", "cancelled"):
                break
            time.sleep(0.05)
        assert st["state"] in ("done", "cancelled")
        assert st["done"] <= st["total"]
    finally:
        with col._JOBS_LOCK:
            col._JOBS.clear()
            col._JOBS.update(saved)
