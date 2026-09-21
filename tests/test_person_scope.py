"""人物页作品列表按库过滤（v18 读聚合补齐）：GET/refresh 接受 media_library / library。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def second_library(tmp_path):
    root = tmp_path / "person-lib"
    root.mkdir()
    lib = store.create_library(name=f"person-lib-{tmp_path.name}", path=str(root))
    library_paths.invalidate_cache()
    yield lib
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _movie(rel, library_id=None, tmdb_id=None, title="T", year=2000):
    mid = (store.upsert_movie_by_path(rel, library_id=library_id)
           if library_id is not None else store.upsert_movie_by_path(rel))
    store.update_movie_meta(mid, title=title, tmdb_id=tmdb_id, year=year)
    return mid


def test_person_works_scoped_by_library_and_media(media_root, second_library):
    lib = second_library
    pid = store.upsert_person(992001, "范围演员")
    a = _movie("person/a.mkv", tmdb_id=992101, title="A")
    b = _movie("person/b.mkv", library_id=lib["id"], tmdb_id=992102, title="B")
    store.link_person(a, pid, "actor", cast_order=0)
    store.link_person(b, pid, "actor", cast_order=0)
    try:
        # 缺省=全库（兼容脚本/旧前端）；空列表同义（store 口径）
        all_p = client.get("/api/persons/992001").json()
        assert {w["id"] for w in all_p["acting"]} == {a, b}
        assert {w["id"] for w in
                store.get_person(992001, library_ids=[])["acting"]} == {a, b}

        only = client.get(f"/api/persons/992001?library={lib['id']}").json()
        assert [w["id"] for w in only["acting"]] == [b]

        media = client.get(
            f"/api/persons/992001?media_library={lib['media_library_id']}").json()
        assert [w["id"] for w in media["acting"]] == [b]

        # 未知媒体库 → 空作品（哨兵不退化全库），详情本身不受影响
        none = client.get("/api/persons/992001?media_library=999999").json()
        assert none["acting"] == [] and none["directing"] == []
        assert none["name"] == "范围演员"
    finally:
        store.delete_movie(a)
        store.delete_movie(b)


def test_person_refresh_respects_scope(media_root, second_library, monkeypatch):
    lib = second_library
    pid = store.upsert_person(992002, "范围导演")
    a = _movie("person/c.mkv", tmdb_id=992103, title="C")
    store.link_person(a, pid, "director")
    # 手动刷新不得触网：摘掉远端抓取
    monkeypatch.setattr("app.routers.persons._fetch_and_cache_bio", lambda tid: None)
    try:
        r = client.post(
            f"/api/persons/992002/refresh?media_library={lib['media_library_id']}").json()
        assert r["directing"] == []
        # 全量刷新仍看得到默认库作品
        r2 = client.post("/api/persons/992002/refresh").json()
        assert [w["id"] for w in r2["directing"]] == [a]
    finally:
        store.delete_movie(a)
