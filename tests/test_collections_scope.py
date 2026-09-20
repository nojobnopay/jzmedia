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


def test_same_name_collections_per_library(media_root, second_library):
    """合集跟随媒体库（v16）：同名合集可分库共存，同库重名仍拒绝，影片 chips 不跨库。"""
    lib = second_library
    a = _movie("dup/a.mkv", tmdb_id=883001, title="A")
    b = _movie("dup/b.mkv", library_id=lib["id"], tmdb_id=883001, title="A")
    c1 = store.create_collection("同名合集")
    c2 = store.create_collection("同名合集", library_id=lib["id"])
    try:
        assert c1["id"] != c2["id"]
        assert c1["library_id"] == store.DEFAULT_LIBRARY_ID
        assert c2["library_id"] == lib["id"]
        with pytest.raises(ValueError):
            store.create_collection("同名合集", library_id=lib["id"])

        store.add_collection_members(c1["id"], [a])
        store.add_collection_members(c2["id"], [b])
        # 影片详情 chips / 接口只返回同库合集（同 tmdb 也不串）
        assert [x["id"] for x in store.list_collections_for_movie(a)] == [c1["id"]]
        assert [x["id"] for x in store.list_collections_for_movie(b)] == [c2["id"]]
        assert [x["id"] for x in store.get_movie(a)["collections"]] == [c1["id"]]
        assert [x["id"] for x in store.get_movie(b)["collections"]] == [c2["id"]]
        # 列表按库过滤
        in_default = [x["id"] for x in store.list_collections(library_id=store.DEFAULT_LIBRARY_ID)]
        assert c1["id"] in in_default and c2["id"] not in in_default
    finally:
        store.delete_collection(c1["id"])
        store.delete_collection(c2["id"])
        store.delete_movie(a)
        store.delete_movie(b)


def test_create_collection_defaults_to_existing_library(media_root, second_library,
                                                        monkeypatch):
    """创建未指定 library_id 时落到真实默认库，而非已不存在的 id=1（用户反馈）。"""
    from app.store import libraries as libraries_mod
    lib = second_library
    monkeypatch.setattr(libraries_mod, "default_library", lambda: lib)
    col = store.create_collection("默认库解析合集")
    try:
        assert col["library_id"] == lib["id"]
    finally:
        store.delete_collection(col["id"])


def test_manual_collection_stays_in_library(media_root, second_library):
    """相似推荐的“同合集”加分不跨库（同 tmdb 的其它库合集不算）。"""
    lib = second_library
    a = _movie("csl/a.mkv", tmdb_id=883101, title="A")
    e = _movie("csl/e.mkv", tmdb_id=883102, title="E")
    c = _movie("csl/c.mkv", library_id=lib["id"], tmdb_id=883101, title="A")
    d = _movie("csl/d.mkv", library_id=lib["id"], tmdb_id=883102, title="E")
    pid = store.upsert_person(991001, "同导演")
    for mid in (a, e, c, d):
        store.link_person(mid, pid, "director")
    col = store.create_collection("跨库合集", member_ids=[a, e])
    try:
        by_id = {x["id"]: x for x in store.similar_movies(c, limit=10)}
        assert d in by_id                              # 同导演仍推荐
        assert "同合集" not in by_id[d]["reason"]       # 但不认其它库的合集
    finally:
        store.delete_collection(col["id"])
        for mid in (a, e, c, d):
            store.delete_movie(mid)


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
