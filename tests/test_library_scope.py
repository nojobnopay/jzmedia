"""多库读接口 scope（B4）：同名影片两库不串联；列表/搜索/facets/批量展开按库。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _offline_tmdb(monkeypatch):
    monkeypatch.setattr("app.tmdb.search_movie", lambda q, year=None: [])


@pytest.fixture()
def second_library(tmp_path):
    root = tmp_path / "libscope"
    root.mkdir()
    lib = store.create_library(name=f"scope-lib-{tmp_path.name}", path=str(root))
    library_paths.invalidate_cache()
    yield lib
    store.delete_library(lib["id"])
    library_paths.invalidate_cache()


def test_read_scope_by_library(media_root, second_library):
    lib = second_library
    rel = "same/Same.Movie.2000.mkv"
    a = store.upsert_movie_by_path(rel)
    store.update_movie_meta(a, title="测试同名", tmdb_id=777001, year=2000)
    b = store.upsert_movie_by_path(rel, library_id=lib["id"])
    store.update_movie_meta(b, title="测试同名", tmdb_id=777001, year=2000)
    try:
        r = client.get(f"/api/movies?library={lib['id']}").json()
        ids = [x["id"] for x in r["items"]]
        assert b in ids and a not in ids
        assert 0 not in ids

        r = client.get(f"/api/search?q=测试同名&library={lib['id']}").json()
        assert [x["id"] for x in r["items"]] == [b]

        f = client.get(f"/api/facets?library={lib['id']}").json()
        assert f["watched"]["unwatched"] == 1

        # 不带 library = 全库（两库各出一个代表）
        r = client.get("/api/movies").json()
        ids = {x["id"] for x in r["items"]}
        assert {a, b} <= ids

        # 批量展开限同库
        exp = store.expand_ids_to_versions([a, b])
        assert exp[a] == [a] and exp[b] == [b]

        # 版本聚合限同库
        got_a = store.get_movie(a)
        got_b = store.get_movie(b)
        assert [v["id"] for v in got_a["versions"]] == [a]
        assert [v["id"] for v in got_b["versions"]] == [b]
    finally:
        store.delete_movie(a)
        store.delete_movie(b)


def test_jobs_stats_scoped(media_root, second_library):
    lib = second_library
    m = store.upsert_movie_by_path("only/lib.mkv", library_id=lib["id"])
    try:
        all_stats = client.get("/api/jobs/stats").json()
        lib_stats = client.get(f"/api/jobs/stats?library={lib['id']}").json()
        assert all_stats["movies"] >= lib_stats["movies"] == 1
        assert lib_stats["missing_files"] == 1
    finally:
        store.delete_movie(m)
