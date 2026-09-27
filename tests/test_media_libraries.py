"""v17 两层模型：媒体库（连接/根）→ 视频库（子目录+类型）API 与扫描。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def cleanup():
    made: list[int] = []
    yield made
    for mid in made:
        store.delete_media_library(mid)
    library_paths.invalidate_cache()


def _create(root, name="NAS", videos=None):
    r = client.post("/api/media-libraries", json={
        "name": name, "source": "local", "path": str(root),
        "video_libraries": videos if videos is not None else [
            {"name": "电影", "kind": "movie", "subpath": "电影"},
            {"name": "剧集", "kind": "tv", "subpath": "剧集"},
        ]})
    return r


def test_create_media_with_video_libraries(tmp_path, media_root, cleanup):
    root = tmp_path / "media"
    (root / "电影").mkdir(parents=True)
    r = _create(root, "NAS")
    assert r.status_code == 200, r.text
    m = r.json()
    cleanup.append(m["id"])
    assert m["name"] == "NAS" and m["source"] == "local"
    kinds = {v["name"]: v["kind"] for v in m["video_libraries"]}
    assert kinds == {"电影": "movie", "剧集": "tv"}
    vids = {v["name"]: v for v in m["video_libraries"]}
    assert vids["电影"]["path"] == str(root / "电影")
    assert (root / "剧集").is_dir()   # 本地子目录按需创建

    # 扁平视频库接口（前端沿用）：带媒体名与 subpath
    libs = {l["id"]: l for l in client.get("/api/libraries").json()["items"]}
    assert vids["电影"]["id"] in libs
    assert libs[vids["电影"]["id"]]["media_name"] == "NAS"
    assert libs[vids["电影"]["id"]]["subpath"] == "电影"

    # 子目录浏览（添加视频库选题材目录用）
    rsub = client.get(f"/api/media-libraries/{m['id']}/subdirs")
    assert rsub.status_code == 200
    assert {d["name"] for d in rsub.json()["dirs"]} == {"电影", "剧集"}

    # 至少一个视频库
    r2 = _create(root, "X", videos=[])
    assert r2.status_code == 422
    assert "至少" in r2.json()["detail"]

    # 同名媒体库 / 根嵌套
    r3 = _create(root, "NAS", videos=[{"name": "z", "kind": "movie", "subpath": ""}])
    assert r3.status_code == 422
    r4 = client.post("/api/media-libraries", json={
        "name": "Y", "path": str(root / "电影"),
        "video_libraries": [{"name": "z", "kind": "movie", "subpath": ""}]})
    assert r4.status_code == 422


def test_video_subpath_overlap_and_kind_lock(tmp_path, media_root, cleanup):
    root = tmp_path / "m2"
    root.mkdir()
    r = _create(root, "m2", videos=[
        {"name": "Movies", "kind": "movie", "subpath": "Movies"},
        {"name": "TV", "kind": "tv", "subpath": "TV Shows"},
    ])
    assert r.status_code == 200, r.text
    m = r.json()
    cleanup.append(m["id"])
    vid = m["video_libraries"][0]["id"]

    # 同一媒体库内子路径重叠/嵌套（含根）→ 422
    for sub in ("Movies/4K", "movies/4k", ""):
        r2 = client.post("/api/libraries", json={
            "name": f"Nested-{sub or 'root'}", "media_library_id": m["id"],
            "subpath": sub, "kind": "movie"})
        assert r2.status_code == 422, (sub, r2.text)

    # 空库可改类型
    r3 = client.patch(f"/api/libraries/{vid}", json={"kind": "tv"})
    assert r3.status_code == 200, r3.text
    assert r3.json()["kind"] == "tv"
    # 有记录后不可改类型
    mid = store.upsert_movie_by_path("Movies/A/a.mkv", library_id=vid)
    try:
        r4 = client.patch(f"/api/libraries/{vid}", json={"kind": "movie"})
        assert r4.status_code == 422
        assert "类型" in r4.json()["detail"]
    finally:
        store.delete_movie(mid)


def test_media_delete_cascades_video_libraries(tmp_path, media_root, cleanup):
    root = tmp_path / "m3"
    root.mkdir()
    r = _create(root, "m3")
    m = r.json()
    v1, v2 = [v["id"] for v in m["video_libraries"]]
    store.upsert_movie_by_path("电影/A/a.mkv", library_id=v1)
    store.upsert_movie_by_path("剧集/B/b.mkv", library_id=v2)
    j = client.delete(f"/api/media-libraries/{m['id']}").json()
    assert j["deleted"] is True and j["video_libraries"] == 2 and j["movies"] == 2
    assert store.get_media_library(m["id"]) is None
    assert store.get_library(v1) is None and store.get_library(v2) is None
    assert store.get_by_path("电影/A/a.mkv", library_id=v1) is None
    # 磁盘文件不动
    assert not (root / "电影" / "A" / "a.mkv").exists()   # 记录不是文件


def test_scan_all_by_media_library(tmp_path, media_root, cleanup, monkeypatch):
    root = tmp_path / "m4"
    (root / "Movies" / "A (2020)").mkdir(parents=True)
    (root / "Movies" / "A (2020)" / "A (2020).mkv").write_bytes(b"x")
    (root / "Shows" / "Show" / "Season 01").mkdir(parents=True)
    (root / "Shows" / "Show" / "Season 01" / "Show.S01E01.mkv").write_bytes(b"x")
    r = _create(root, "m4", videos=[
        {"name": "Movies", "kind": "movie", "subpath": "Movies"},
        {"name": "Shows", "kind": "tv", "subpath": "Shows"},
    ])
    m = r.json()
    cleanup.append(m["id"])
    vids = {v["name"]: v["id"] for v in m["video_libraries"]}
    monkeypatch.setattr(scanner.scan, "search_with_fallback",
                        lambda title, year: (None, title, False))
    out = scanner.scan_all(media_library_id=m["id"])
    assert {r0["library_id"] for r0 in out} == {vids["Movies"], vids["Shows"]}
    # 结果相对路径是「视频库根」而非媒体库根
    statuses = {r0["file"]: r0["status"] for r0 in out}
    assert "A (2020)/A (2020).mkv" in statuses
    assert statuses["Show/Season 01/Show.S01E01.mkv"] == "tv_ok"
    assert store.count_episodes(vids["Shows"]) == 1

    # 扫描任务接口接受 media_library_id
    rj = client.post("/api/jobs/scan", json={"media_library_id": m["id"]})
    assert rj.status_code == 200, rj.text
    assert rj.json()["media_library_id"] == m["id"]
    client.post(f"/api/jobs/scan/{rj.json()['job_id']}/cancel")


def test_video_update_rejects_media_owned_fields(tmp_path, media_root, cleanup):
    root = tmp_path / "m5"
    root.mkdir()
    m = _create(root, "m5", videos=[
        {"name": "v", "kind": "movie", "subpath": "v"}]).json()
    cleanup.append(m["id"])
    vid = m["video_libraries"][0]["id"]
    for body in ({"read_only": True}, {"path": str(root)}, {"subpath": "other"},
                 {"smb": {"host": "x", "share": "y"}}):
        r = client.patch(f"/api/libraries/{vid}", json=body)
        # read_only/path/smb 属于媒体库 → 422；subpath 与同媒体无冲突 → 200
        if "subpath" in body:
            assert r.status_code == 200, r.text
        else:
            assert r.status_code == 422, (body, r.text)


def test_media_level_aggregation_reads(tmp_path, media_root, cleanup):
    """电影墙/剧集页按媒体库聚合：跨视频库并集、跨媒体库隔离、未知媒体库为空。"""
    root = tmp_path / "agg"
    (root / "Movies").mkdir(parents=True)
    (root / "Unrated").mkdir()
    (root / "Shows").mkdir()
    m = _create(root, "agg", videos=[
        {"name": "Movies", "kind": "movie", "subpath": "Movies"},
        {"name": "Unrated", "kind": "movie", "subpath": "Unrated"},
        {"name": "Shows", "kind": "tv", "subpath": "Shows"},
    ]).json()
    cleanup.append(m["id"])
    vids = {v["name"]: v["id"] for v in m["video_libraries"]}
    a = store.upsert_movie_by_path("A (2001)/a.mkv", library_id=vids["Movies"])
    store.update_movie_meta(a, title="AggA", year=2001)
    b = store.upsert_movie_by_path("B (2002)/b.mkv", library_id=vids["Unrated"])
    store.update_movie_meta(b, title="AggB", year=2002)
    # 另一个媒体库的同名/其它片
    (tmp_path / "other").mkdir()
    other = _create(tmp_path / "other", "other", videos=[
        {"name": "Movies", "kind": "movie", "subpath": "Movies"}]).json()
    cleanup.append(other["id"])
    c = store.upsert_movie_by_path("C (2003)/c.mkv",
                                   library_id=other["video_libraries"][0]["id"])
    store.update_movie_meta(c, title="AggC", year=2003)
    sid = store.upsert_show(vids["Shows"], "AggShow", 2020)
    store.upsert_episode(sid, vids["Shows"], "AggShow/S01/E01.mkv", 1, 1)

    r = client.get("/api/search", params={"media_library": m["id"], "limit": 50})
    assert r.status_code == 200, r.text
    titles = {x["title"] for x in r.json()["items"]}
    assert {"AggA", "AggB"} <= titles and "AggC" not in titles
    r = client.get("/api/movies", params={"media_library": m["id"]})
    assert {x["title"] for x in r.json()["items"]} >= {"AggA", "AggB"}
    r = client.get("/api/facets", params={"media_library": m["id"]})
    assert r.status_code == 200
    r = client.get("/api/search/suggest", params={"media_library": m["id"], "q": "Agg"})
    assert {x["title"] for x in r.json()["items"]} >= {"AggA", "AggB"}

    r = client.get("/api/tv/shows", params={"media_library": m["id"]})
    assert r.status_code == 200, r.text
    assert [s["title"] for s in r.json()["items"]] == ["AggShow"]
    assert r.json()["total"] == 1
    r = client.get("/api/tv/stats", params={"media_library": m["id"]})
    assert {k: r.json()[k] for k in ("shows", "episodes")} == {"shows": 1, "episodes": 1}
    r = client.get("/api/tv/shows", params={"media_library": other["id"]})
    assert r.json()["items"] == []

    # 未知媒体库 → 明确空结果（绝不退化成全库）
    r = client.get("/api/search", params={"media_library": 99999})
    assert r.json()["items"] == []
    r = client.get("/api/movies", params={"media_library": 99999})
    assert r.json()["items"] == []
    r = client.get("/api/tv/shows", params={"media_library": 99999})
    assert r.json()["items"] == []


def test_collections_media_level_api(tmp_path, media_root, cleanup):
    """合集媒体库级：跨视频库成员可加入、跨媒体库 skipped、按媒体库过滤。"""
    root = tmp_path / "col"
    (root / "Movies").mkdir(parents=True)
    (root / "Unrated").mkdir()
    m = _create(root, "col", videos=[
        {"name": "Movies", "kind": "movie", "subpath": "Movies"},
        {"name": "Unrated", "kind": "movie", "subpath": "Unrated"},
    ]).json()
    cleanup.append(m["id"])
    vids = {v["name"]: v["id"] for v in m["video_libraries"]}
    a = store.upsert_movie_by_path("A (2001)/a.mkv", library_id=vids["Movies"])
    store.update_movie_meta(a, title="ColA", year=2001, tmdb_id=970001)
    b = store.upsert_movie_by_path("B (2002)/b.mkv", library_id=vids["Unrated"])
    store.update_movie_meta(b, title="ColB", year=2002, tmdb_id=970002)
    (tmp_path / "o2").mkdir()
    other = _create(tmp_path / "o2", "o2", videos=[
        {"name": "Movies", "kind": "movie", "subpath": "Movies"}]).json()
    cleanup.append(other["id"])
    c = store.upsert_movie_by_path("C (2003)/c.mkv",
                                   library_id=other["video_libraries"][0]["id"])
    store.update_movie_meta(c, title="ColC", year=2003, tmdb_id=970003)

    r = client.post("/api/collections", json={
        "name": "媒体库合集", "media_library_id": m["id"], "member_ids": [a, b, c]})
    assert r.status_code == 200, r.text
    col = r.json()
    assert col["media_library_id"] == m["id"]
    assert {x["id"] for x in col["members"]} == {a, b}
    r = client.get("/api/collections", params={"media_library": m["id"]})
    assert "媒体库合集" in [x["name"] for x in r.json()["items"]]
    r = client.get("/api/collections", params={"media_library": other["id"]})
    assert "媒体库合集" not in [x["name"] for x in r.json()["items"]]
    # 旧 library（视频库）参数兼容映射到媒体库
    r = client.get("/api/collections", params={"library": vids["Unrated"]})
    assert "媒体库合集" in [x["name"] for x in r.json()["items"]]
    client.delete(f"/api/collections/{col['id']}")
