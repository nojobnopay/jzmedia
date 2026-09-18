"""多库合集/相似推荐 scope（B5）：合集按库隔离、成员加入限同库、推荐不跨库。"""
import pytest

from app import library_paths, store


@pytest.fixture()
def second_library(tmp_path):
    root = tmp_path / "libcol"
    root.mkdir()
    lib = store.create_library(name=f"col-lib-{tmp_path.name}", path=str(root))
    library_paths.invalidate_cache()
    yield lib
    store.delete_library(lib["id"])
    library_paths.invalidate_cache()


def _movie(rel, library_id=None, tmdb_id=None, title="T", year=2000):
    mid = store.upsert_movie_by_path(rel, library_id=library_id) if library_id is not None \
        else store.upsert_movie_by_path(rel)
    meta = {"title": title, "year": year}
    if tmdb_id:
        meta["tmdb_id"] = tmdb_id
    store.update_movie_meta(mid, **meta)
    return mid


def test_collection_scope_and_members(media_root, second_library):
    lib = second_library
    a = _movie("col/a.mkv", tmdb_id=880001, title="A")
    b = _movie("col/b.mkv", library_id=lib["id"], tmdb_id=880002, title="B")
    try:
        col = store.create_collection("测试合集", library_id=lib["id"])
        cid = col["id"]
        # 跨库 id 拒绝加入
        r = store.add_collection_members(cid, [a])
        assert r["added"] == 0 and r["total"] == 0
        r = store.add_collection_members(cid, [b])
        assert r["added"] == 1 and r["total"] == 1

        # 列表按库过滤
        in_lib = store.list_collections(library_id=lib["id"])
        assert cid in [x["id"] for x in in_lib]
        in_default = store.list_collections(library_id=store.DEFAULT_LIBRARY_ID)
        assert cid not in [x["id"] for x in in_default]

        # 详情成员限同库（即使有跨库同 tmdb 成员记录也不串）
        got = store.get_collection(cid)
        assert [m["id"] for m in got["members"]] == [b]
    finally:
        store.delete_collection(cid)
        store.delete_movie(a)
        store.delete_movie(b)


def test_series_hint_scoped(media_root, second_library):
    lib = second_library
    a = _movie("hint/a.mkv", tmdb_id=881001, title="A")
    b = _movie("hint/b.mkv", library_id=lib["id"], tmdb_id=881001, title="A")
    # 两库各有同 tmdb 影片；cache 标记同系列
    for tid in (881001,):
        store.upsert_tmdb_cache(tid, {"title": "A", "media_type": "movie",
                                      "collection_tmdb_id": 777, "collection_name": "S"})
    # 默认库已建合集后，lib2 的 hint 不应认为“已收录”
    col = store.create_collection("S", tmdb_collection_id=777)
    try:
        hint1 = store.collection_hint_for_movie(a)
        hint2 = store.collection_hint_for_movie(b)
        assert hint1["already_collected"] is True
        assert hint2["already_collected"] is False
        assert [x["id"] for x in hint2["in_library"]] == [b]
    finally:
        store.delete_collection(col["id"])
        store.delete_movie(a)
        store.delete_movie(b)


def test_similar_candidates_stay_in_library(media_root, second_library):
    lib = second_library
    a = _movie("sim/a.mkv", tmdb_id=882001, title="A", year=2001)
    b = _movie("sim/b.mkv", tmdb_id=882002, title="B", year=2002)
    c = _movie("sim/c.mkv", library_id=lib["id"], tmdb_id=882003, title="C", year=2003)
    pid = store.upsert_person(990001, "同导演")
    for mid in (a, b, c):
        store.link_person(mid, pid, "director")
    try:
        out = store.similar_movies(a, limit=10)
        ids = {x["id"] for x in out}
        assert b in ids          # 同库同导演 → 推荐
        assert c not in ids      # 跨库同导演 → 不串
    finally:
        store.delete_movie(a)
        store.delete_movie(b)
        store.delete_movie(c)
