"""B8 后端批回归网：轻查询/分页/EXDEV/校验/bio 语言与限频/Job 修剪。"""
import errno
import os
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import store
from app.config import settings
from app.main import app
from app.routers import collections as collections_router
from app.routers import files as files_router
from app.routers import persons as persons_router

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


# ---------- R05-B2：轻量 tags ----------

def test_get_movie_tags(media_root):
    mid = _row("b8/tags.mkv")
    store.update_movie_local(mid, tags=["科幻", "动作"])
    assert store.get_movie_tags(mid) == ["科幻", "动作"]
    assert store.get_movie_tags(99999999) is None


# ---------- R02-D4/D6：facets/合集聚合 ----------

def test_facets_shape_and_collection_count(media_root):
    mid = _row("b8/facets.mkv", title="Facets", year=2021)
    store.update_movie_local(mid, tags=["t1"], watched=1)
    c = store.create_collection("B8 合集", member_ids=[mid])
    d = store.get_facets()
    assert d["watched"]["watched"] >= 1
    assert any(x["id"] == c["id"] and x["count"] == 1 for x in d["collections"])
    cols = store.list_collections()
    assert any(x["id"] == c["id"] and x["member_count"] == 1 for x in cols)


# ---------- R01-Q6：fs 列表分页 ----------

def test_fs_list_pagination(media_root):
    for i in range(5):
        _touch(media_root, f"b8p/f{i}.txt")
    r = client.get("/api/fs/list", params={"path": "b8p", "limit": 2, "offset": 0}).json()
    assert len(r["files"]) == 2 and r["total_files"] == 5 and r["has_more"] is True
    r2 = client.get("/api/fs/list", params={"path": "b8p", "limit": 2, "offset": 4}).json()
    assert len(r2["files"]) == 1 and r2["has_more"] is False


# ---------- R09-D3：EXDEV 兜底 ----------

def test_rename_or_move_exdev_fallback(tmp_path, monkeypatch):
    src = tmp_path / "a.mkv"
    src.write_bytes(b"x")
    dst = tmp_path / "b.mkv"
    calls = {}

    def _exdev(*a, **kw):
        raise OSError(errno.EXDEV, "cross-device")

    def _fake_move(s, d, *a, **kw):
        calls["moved"] = (s, d)
        pathlib.Path(d).write_bytes(pathlib.Path(s).read_bytes())

    monkeypatch.setattr(files_router.os, "rename", _exdev)
    monkeypatch.setattr(files_router.shutil, "move", _fake_move)
    files_router._rename_or_move(str(src), str(dst))
    assert calls["moved"] == (str(src), str(dst))
    assert dst.is_file()


# ---------- R05-D1：PATCH 长度校验 ----------

def test_patch_title_overview_validation(media_root):
    mid = _row("b8/patch.mkv")
    assert client.patch(f"/api/movies/{mid}", json={"title": "  "}).status_code == 422
    assert client.patch(f"/api/movies/{mid}", json={"title": "x" * 300}).status_code == 422
    assert client.patch(f"/api/movies/{mid}",
                        json={"overview_override": "y" * 20001}).status_code == 422
    ok = client.patch(f"/api/movies/{mid}", json={"title": "  合法标题  "})
    assert ok.status_code == 200 and ok.json()["title"] == "合法标题"


# ---------- R12-D6/D7：探测预筛与并行 ----------

def test_probe_missing_prefilter(media_root):
    a = _row("b8/p1.mkv")
    b = _row("b8/p2.mkv")
    _row("b8/p3.mkv")
    store.upsert_media_info(a, {"playable": True, "container": "mp4", "duration": 5.0,
                                "width": 640, "height": 360, "vcodec": "h264",
                                "bit_depth": 8, "audio": [], "subs": [],
                                "attachments": [], "probe_ver": 0, "probed_at": 1})
    store.upsert_media_info(b, {"playable": True, "container": "mp4", "duration": 5.0,
                                "width": 640, "height": 360, "vcodec": "h264",
                                "bit_depth": 8, "audio": [], "subs": [],
                                "attachments": [], "probe_ver": 999, "probed_at": 1})
    d = client.post("/api/stream/probe-missing", json={"limit": 50}).json()
    assert d["total"] >= 1        # 无行 / 过期行入选（预筛路径无 N+1）


def test_versions_probe_parallel_shape(media_root):
    rel1 = "b8/v1.mkv"
    rel2 = "b8/v2.mkv"
    _touch(media_root, rel1)
    _touch(media_root, rel2)
    tid = 880011
    mid = _row(rel1, title="Multi", tmdb_id=tid)
    store.upsert_movie_by_path(rel2)
    m2 = store.get_by_path(rel2)["id"]
    store.update_movie_meta(m2, title="Multi", year=2020, tmdb_id=tid)
    d = client.post("/api/stream/versions", json={"movie_id": mid}).json()
    assert len(d["versions"]) == 2
    assert all("method" in v for v in d["versions"])


# ---------- R07：bio 语言 / 限频 / person_exists ----------

def test_person_bio_uses_configured_language(monkeypatch):
    seen = []
    monkeypatch.setattr(persons_router.tmdb, "person_detail",
                        lambda tid, language=None: seen.append(language or "") or
                        {"biography": "bio", "birthday": "", "place_of_birth": ""})
    prev = store.get_setting("tmdb_language")
    store.set_setting("tmdb_language", "ja-JP")
    try:
        persons_router._fetch_and_cache_bio(880021)
        assert seen and seen[0] == "ja-JP"
    finally:
        store.set_setting("tmdb_language", prev or "")


def test_bio_rate_limit_and_person_exists(media_root):
    pid = 880022
    store.upsert_person(pid, "Actor")
    assert store.person_exists(pid) is True
    assert persons_router._rate_ok(pid) is True
    assert persons_router._rate_ok(pid) is False     # 10s 内重复被限


# ---------- R06-D1/B2：Job 修剪 ----------

def test_jobs_trim_keeps_recent_finished():
    with collections_router._JOBS_LOCK:
        saved = dict(collections_router._JOBS)
        collections_router._JOBS.clear()
        for i in range(9):
            collections_router._JOBS[f"j{i}"] = {
                "state": "done", "started_at": i, "finished_at": i}
        collections_router._trim_jobs()
        left = set(collections_router._JOBS)
        assert len(left) == collections_router._MAX_FINISHED_JOBS
        assert "j8" in left and "j0" not in left
        collections_router._JOBS.clear()
        collections_router._JOBS.update(saved)


# ---------- R02-D6：合集封面/成员批读正确性 ----------

def test_collection_cover_and_members_batch(media_root):
    tid = 880031
    a = _row("b8c/a.mkv", title="A", year=2001, tmdb_id=tid)
    b = _row("b8c/b.mkv", title="B", year=2002, tmdb_id=tid)
    store.update_movie_meta(a, poster_path="posters/a.jpg")
    c = store.create_collection("B8 封面", member_ids=[a])
    store.update_collection(c["id"], poster_path="")
    cols = {x["id"]: x for x in store.list_collections()}
    assert cols[c["id"]]["cover"] == "posters/a.jpg"
    assert cols[c["id"]]["member_count"] == 1
    full = store.get_collection(c["id"])
    assert full["member_count"] == 1 and full["members"][0]["version_count"] == 2


# ---------- B9/R04-D6：扫描后台任务 ----------

def test_scan_job_lifecycle(monkeypatch):
    import time as _t
    from app import scanner
    from app.routers import jobs as jobs_router

    def fake_scan(progress_cb=None, should_stop=None):
        if progress_cb:
            progress_cb(0, 3)
        out = []
        for i in range(3):
            if should_stop and should_stop():
                return out
            _t.sleep(0.05)
            out.append({"file": f"f{i}.mkv", "status": "no_match"})
            if progress_cb:
                progress_cb(len(out), 3)
        return out

    monkeypatch.setattr(scanner, "scan_all", fake_scan)
    r = client.post("/api/jobs/scan").json()
    assert r["resumed"] is False and r["job_id"]
    for _ in range(60):
        st = client.get(f"/api/jobs/scan/{r['job_id']}").json()
        if st["state"] in ("done", "failed"):
            break
        _t.sleep(0.05)
    assert st["state"] == "done"
    assert st["total"] == 3 and st["summary"]["counts"]["no_match"] == 3
    # 已完成后再次启动 → 新 job
    r2 = client.post("/api/jobs/scan").json()
    assert r2["job_id"] != r["job_id"]


def test_scan_job_cancel(monkeypatch):
    import time as _t
    from app import scanner

    def slow_scan(progress_cb=None, should_stop=None):
        out = []
        for i in range(200):
            if should_stop and should_stop():
                return out
            _t.sleep(0.02)
            out.append({"file": f"f{i}.mkv", "status": "ok"})
            if progress_cb:
                progress_cb(len(out), 200)
        return out

    monkeypatch.setattr(scanner, "scan_all", slow_scan)
    r = client.post("/api/jobs/scan").json()
    _t.sleep(0.1)
    c = client.post(f"/api/jobs/scan/{r['job_id']}/cancel").json()
    assert c["state"] == "cancelled"
    for _ in range(60):
        st = client.get(f"/api/jobs/scan/{r['job_id']}").json()
        if st["state"] == "cancelled":
            break
        _t.sleep(0.05)
    assert st["state"] == "cancelled" and st["done"] < 200
