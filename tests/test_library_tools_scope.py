"""按库过滤的库工具接口（设置页库标签页）：
restore-candidates / rebuild-nfo / backfill-meta / tmdb-refresh / extras-collect
的 library 参数——限定库时只作用于该库，缺省行为不变（全库）。"""
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def second_library(tmp_path):
    root = tmp_path / "libtools"
    root.mkdir()
    lib = store.create_library(name=f"tools-lib-{tmp_path.name}", path=str(root))
    library_paths.invalidate_cache()
    yield lib
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _mk(root, rel, *, library_id=None, **meta):
    p = pathlib.Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    if library_id is None:
        mid = store.upsert_movie_by_path(rel)
    else:
        mid = store.upsert_movie_by_path(rel, library_id=library_id)
    if meta:
        store.update_movie_meta(mid, **meta)
    return mid


def test_restore_candidates_scoped(media_root, second_library):
    lib = second_library
    lib_root = lib["path"]
    a = _mk(media_root, "old/A (2020).mkv", title="A")
    b = _mk(lib_root, "old/B (2020).mkv", library_id=lib["id"], title="B")
    try:
        for root, mid, new in ((media_root, a, "new/A (2020).mkv"),
                               (lib_root, b, "new/B (2020).mkv")):
            (pathlib.Path(root) / new).parent.mkdir(parents=True, exist_ok=True)
            (pathlib.Path(root) / new).write_bytes(b"x")
            store.update_movie_local(mid, file_path=new)
        got = client.get(f"/api/files/restore-candidates?library={lib['id']}").json()
        assert [x["id"] for x in got["items"]] == [b]
        allgot = client.get("/api/files/restore-candidates").json()
        assert {a, b} <= {x["id"] for x in allgot["items"]}
    finally:
        store.delete_movie(a)
        store.delete_movie(b)


def test_rebuild_nfo_scoped(media_root, second_library):
    lib = second_library
    a = _mk(media_root, "nfo/A (2020).mkv", title="A")
    b = _mk(lib["path"], "nfo/B (2020).mkv", library_id=lib["id"], title="B")
    try:
        scoped = client.post("/api/jobs/rebuild-nfo",
                             json={"library_id": lib["id"], "dry_run": True}).json()
        assert scoped["total"] == 1 and scoped["ok"] == 1
        allr = client.post("/api/jobs/rebuild-nfo", json={"dry_run": True}).json()
        assert allr["total"] >= 2
    finally:
        store.delete_movie(a)
        store.delete_movie(b)


def test_backfill_meta_scoped(media_root, second_library):
    lib = second_library
    a = _mk(media_root, "bf/A (2020).mkv", title="A", tmdb_id=888001, year=2020)
    b = _mk(lib["path"], "bf/B (2020).mkv", library_id=lib["id"], title="B",
            tmdb_id=888002, year=2020)
    store.upsert_tmdb_cache(888001, {"title": "A", "year": 2020,
                                     "media_type": "movie"}, {}, "")
    store.upsert_tmdb_cache(888002, {"title": "B", "year": 2020,
                                     "media_type": "movie"}, {}, "")
    try:
        d = client.post("/api/jobs/backfill-meta", json={"library_id": lib["id"]}).json()
        assert [r["id"] for r in d["results"]] == [b]
    finally:
        store.delete_movie(a)
        store.delete_movie(b)


def test_tmdb_refresh_scoped(media_root, second_library, monkeypatch):
    lib = second_library
    a = _mk(media_root, "rf/A (2020).mkv", title="A", tmdb_id=889001, year=2020)
    b = _mk(lib["path"], "rf/B (2020).mkv", library_id=lib["id"], title="B",
            tmdb_id=889002, year=2020)
    seen: list[int] = []
    monkeypatch.setattr(scanner, "refresh_tmdb_id",
                        lambda tid: seen.append(int(tid)) or {"changed": False})
    try:
        d = client.post("/api/jobs/tmdb-refresh", json={"library_id": lib["id"]}).json()
        assert d["total"] == 1 and seen == [889002]
    finally:
        store.delete_movie(a)
        store.delete_movie(b)


def test_extras_collect_scoped(media_root, second_library):
    lib = second_library
    lib_root = lib["path"]
    a = _mk(media_root, "col/A (2020)/A.mkv", title="A")
    b = _mk(lib_root, "col/B (2020)/B.mkv", library_id=lib["id"], title="B")
    store.upsert_extra("col/A-trailer.mkv", a, "trailer")
    store.upsert_extra("col/B-trailer.mkv", b, "trailer", library_id=lib["id"])
    try:
        d = client.post("/api/extras/collect",
                        json={"library_id": lib["id"], "dry_run": True}).json()
        assert [p["id"] for p in d["plans"]] == [b]
    finally:
        store.delete_extra_by_path("col/A-trailer.mkv")
        store.delete_extra_by_path("col/B-trailer.mkv", library_id=lib["id"])
        store.delete_movie(a)
        store.delete_movie(b)
