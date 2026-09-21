"""v20 入库时间（movies.added_at）：迁移回填、不可变、排序参数回归。"""
import sqlite3

from fastapi.testclient import TestClient

from app import store
from app.main import app
from app.store import _base

client = TestClient(app)


def test_m20_backfills_from_updated_at_and_idempotent(tmp_path):
    dbp = tmp_path / "m20.db"
    with sqlite3.connect(dbp) as c:
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE movies (id INTEGER PRIMARY KEY, updated_at INTEGER)")
        c.execute("INSERT INTO movies VALUES(1, 111)")
        c.execute("INSERT INTO movies VALUES(2, 0)")
        _base._m20(c)
        rows = dict(c.execute("SELECT id, added_at FROM movies").fetchall())
        assert rows[1] == 111          # 存量行用 updated_at 近似
        assert rows[2] > 0             # 无 updated_at 的用 now 兜底
        _base._m20(c)                  # 幂等：已有值不回退
        assert dict(c.execute("SELECT id, added_at FROM movies").fetchall()) == rows


def test_added_at_written_once_and_immutable():
    mid = store.upsert_movie_by_path("added/immutable-1.mkv")
    first = store.get_movie(mid)["added_at"]
    assert first > 0
    store.upsert_movie_by_path("added/immutable-1.mkv")      # 重扫/再次命中
    assert store.get_movie(mid)["added_at"] == first
    store.update_movie_meta(mid, title="Changed", watched=1, watched_at=123)
    assert store.get_movie(mid)["added_at"] == first         # 元数据/标已看不碰


def _seed_added(prefix: str, times: list[int]) -> list[int]:
    ids = []
    for i, t in enumerate(times):
        mid = store.upsert_movie_by_path(f"added/{prefix}-{i}.mkv")
        store.update_movie_meta(mid, title=f"{prefix} {i}", year=2000 + i)
        with _base._lock, _base._conn() as c:
            c.execute("UPDATE movies SET added_at=? WHERE id=?", (t, mid))
        ids.append(mid)
    return ids


def _mine(items, ids):
    s = set(ids)
    return [m["id"] for m in items if m["id"] in s]


def test_sort_added_desc_and_asc():
    ids = _seed_added("AddA", [500, 100, 900])
    desc = store.list_movies(grouped=False, limit=2000, sort="added", order="desc")
    assert _mine(desc, ids) == [ids[2], ids[0], ids[1]]
    asc = store.list_movies(grouped=False, limit=2000, sort="added", order="asc")
    assert _mine(asc, ids) == [ids[1], ids[0], ids[2]]


def test_sort_year_desc_and_unknown_key_falls_back():
    ids = _seed_added("AddB", [1, 2, 3])
    got = store.list_movies(grouped=False, limit=2000, sort="year", order="desc")
    assert _mine(got, ids) == [ids[2], ids[1], ids[0]]
    got2 = store.list_movies(grouped=False, limit=2000, sort="bogus", order="asc")
    assert set(_mine(got2, ids)) == set(ids)     # 未知键回落 updated，不报错


def test_sort_grouped_movies_api_and_stable_pagination():
    ids = _seed_added("AddC", [10, 20, 30, 40])
    r = client.get("/api/movies", params={"sort": "added", "order": "desc", "limit": 2000})
    assert r.status_code == 200
    assert _mine(r.json()["items"], ids) == list(reversed(ids))
    # /api/search 空 q 走 list_movies，排序参数同样生效
    r2 = client.get("/api/search", params={"q": "", "sort": "added", "order": "asc", "limit": 2000})
    assert r2.status_code == 200
    assert _mine(r2.json()["items"], ids) == ids
    # id 兜底：即使 added_at 相同，翻页也不重叠
    _seed_added("AddD", [7, 7, 7])
    p1 = client.get("/api/movies", params={"sort": "added", "order": "desc", "limit": 1, "offset": 0}).json()
    p2 = client.get("/api/movies", params={"sort": "added", "order": "desc", "limit": 1, "offset": 1}).json()
    assert {m["id"] for m in p1["items"]} != {m["id"] for m in p2["items"]}


def test_added_at_exposed_on_movie_payload():
    mid = store.upsert_movie_by_path("added/payload-1.mkv")
    d = client.get(f"/api/movies/{mid}").json()
    assert d["added_at"] == store.get_movie(mid)["added_at"]
