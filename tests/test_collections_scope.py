"""合集媒体库级（v18）：成员可跨同媒体库视频库、跨媒体库拒绝、同名按媒体库唯一；
相似推荐仍按视频库（不跨库串候选），但“同合集”加分按媒体库。"""
import pytest

from app import library_paths, store


@pytest.fixture()
def second_library(tmp_path):
    """默认媒体库之外的另一个媒体库（兼容建库：一个媒体库 + 根视频库）。"""
    root = tmp_path / "libcol"
    root.mkdir()
    lib = store.create_library(name=f"col-lib-{tmp_path.name}", path=str(root))
    library_paths.invalidate_cache()
    yield lib
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


@pytest.fixture()
def two_video_media(tmp_path):
    """一个媒体库下两个电影类视频库（Movies + Unrated），验证跨视频库成员。"""
    root = tmp_path / "media2v"
    (root / "Movies").mkdir(parents=True)
    (root / "Unrated").mkdir()
    m = store.create_media_library(
        name=f"ml-{tmp_path.name}", path=str(root),
        video_libraries=[{"name": "Movies", "kind": "movie", "subpath": "Movies"},
                         {"name": "Unrated", "kind": "movie", "subpath": "Unrated"}])
    library_paths.invalidate_cache()
    vids = {v["name"]: v for v in store.video_libraries_of(m["id"])}
    yield m, vids
    store.delete_media_library(m["id"])
    library_paths.invalidate_cache()


def _movie(rel, library_id=None, tmdb_id=None, title="T", year=2000):
    mid = store.upsert_movie_by_path(rel, library_id=library_id) if library_id is not None \
        else store.upsert_movie_by_path(rel)
    meta = {"title": title, "year": year}
    if tmdb_id:
        meta["tmdb_id"] = tmdb_id
    store.update_movie_meta(mid, **meta)
    return mid


def test_collection_members_across_media_video_libraries(media_root, two_video_media):
    """同媒体库的任意视频库成员都可加入；跨媒体库 id 计入 skipped。"""
    m, vids = two_video_media
    a = _movie("a.mkv", library_id=vids["Movies"]["id"], tmdb_id=880001, title="A")
    b = _movie("b.mkv", library_id=vids["Unrated"]["id"], tmdb_id=880002, title="B")
    other = _movie("other.mkv", tmdb_id=880003, title="C")   # 默认媒体库（另一个）
    col = store.create_collection("跨视频库合集", media_library_id=m["id"])
    try:
        assert col["media_library_id"] == m["id"]
        r = store.add_collection_members(col["id"], [a, b, other])
        assert r["added"] == 2 and r["total"] == 2 and r["skipped"] == 1
        got = store.get_collection(col["id"])
        assert {x["id"] for x in got["members"]} == {a, b}
        # 列表按媒体库过滤
        assert col["id"] in [x["id"] for x in
                             store.list_collections(media_library_id=m["id"])]
        assert col["id"] not in [x["id"] for x in
                                 store.list_collections(
                                     media_library_id=store.DEFAULT_LIBRARY_ID)]
    finally:
        store.delete_collection(col["id"])
        for mid in (a, b, other):
            store.delete_movie(mid)


def test_series_hint_scoped(media_root, second_library):
    lib = second_library
    a = _movie("hint/a.mkv", tmdb_id=881001, title="A")
    b = _movie("hint/b.mkv", library_id=lib["id"], tmdb_id=881001, title="A")
    # 两库各有同 tmdb 影片；cache 标记同系列
    store.upsert_tmdb_cache(881001, {"title": "A", "media_type": "movie",
                                     "collection_tmdb_id": 777, "collection_name": "S"})
    # 默认媒体库已建合集后，另一个媒体库的 hint 不应认为“已收录”
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


def test_same_name_collections_per_media(media_root, second_library):
    """合集跟随媒体库（v18）：同名合集可分媒体库共存，同媒体库重名仍拒绝，影片 chips 不跨媒体库。"""
    lib = second_library
    a = _movie("dup/a.mkv", tmdb_id=883001, title="A")
    b = _movie("dup/b.mkv", library_id=lib["id"], tmdb_id=883001, title="A")
    c1 = store.create_collection("同名合集")
    c2 = store.create_collection("同名合集", media_library_id=lib["media_library_id"])
    try:
        assert c1["id"] != c2["id"]
        assert c1["media_library_id"] == store.DEFAULT_LIBRARY_ID
        assert c2["media_library_id"] == lib["media_library_id"]
        with pytest.raises(ValueError):
            store.create_collection("同名合集",
                                    media_library_id=lib["media_library_id"])

        store.add_collection_members(c1["id"], [a])
        store.add_collection_members(c2["id"], [b])
        # 影片详情 chips / 接口只返回同媒体库合集（同 tmdb 也不串）
        assert [x["id"] for x in store.list_collections_for_movie(a)] == [c1["id"]]
        assert [x["id"] for x in store.list_collections_for_movie(b)] == [c2["id"]]
        assert [x["id"] for x in store.get_movie(a)["collections"]] == [c1["id"]]
        assert [x["id"] for x in store.get_movie(b)["collections"]] == [c2["id"]]
        # 列表按媒体库过滤
        in_default = [x["id"] for x in
                      store.list_collections(media_library_id=store.DEFAULT_LIBRARY_ID)]
        assert c1["id"] in in_default and c2["id"] not in in_default
    finally:
        store.delete_collection(c1["id"])
        store.delete_collection(c2["id"])
        store.delete_movie(a)
        store.delete_movie(b)


def test_create_collection_defaults_to_existing_media(media_root, second_library,
                                                      monkeypatch):
    """创建未指定媒体库时落到真实默认库所属媒体库（而非已不存在的 id=1）。"""
    from app.store import media_libraries as ml
    lib = second_library
    monkeypatch.setattr(ml, "default_media_id",
                        lambda: int(lib["media_library_id"]))
    col = store.create_collection("默认库解析合集")
    try:
        assert col["media_library_id"] == lib["media_library_id"]
    finally:
        store.delete_collection(col["id"])


def test_manual_collection_stays_in_media(media_root, second_library):
    """相似推荐的“同合集”加分不跨媒体库（同 tmdb 的其它媒体库合集不算）。"""
    lib = second_library
    a = _movie("csl/a.mkv", tmdb_id=883101, title="A")
    e = _movie("csl/e.mkv", tmdb_id=883102, title="E")
    c = _movie("csl/c.mkv", library_id=lib["id"], tmdb_id=883101, title="A")
    d = _movie("csl/d.mkv", library_id=lib["id"], tmdb_id=883102, title="E")
    pid = store.upsert_person(991001, "同导演")
    for mid in (a, e, c, d):
        store.link_person(mid, pid, "director")
    col = store.create_collection("跨媒体库合集", member_ids=[a, e])
    try:
        by_id = {x["id"]: x for x in store.similar_movies(c, limit=10)}
        assert d in by_id                              # 同导演仍推荐
        assert "同合集" not in by_id[d]["reason"]       # 但不认其它媒体库的合集
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
        assert b in ids          # 同视频库同导演 → 推荐
        assert c not in ids      # 跨库同导演 → 不串
    finally:
        store.delete_movie(a)
        store.delete_movie(b)
        store.delete_movie(c)
