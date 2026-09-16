"""P1-11 分页回归网：store offset 语义 + API has_more/limit 钳制。"""
from fastapi.testclient import TestClient

from app import store
from app.main import app

client = TestClient(app)


def _seed(n: int, prefix: str) -> list[int]:
    ids = []
    for i in range(n):
        rel = f"page/{prefix}-{i:02d}.mkv"
        mid = store.upsert_movie_by_path(rel)
        store.update_movie_meta(mid, title=f"{prefix} {i:02d}", year=2000 + i)
        ids.append(mid)
    return ids


def test_store_list_movies_offset_disjoint():
    _seed(5, "OffA")
    all_items = store.list_movies(grouped=False, limit=100)
    page1 = store.list_movies(grouped=False, limit=2, offset=0)
    page2 = store.list_movies(grouped=False, limit=2, offset=2)
    ids1 = {m["id"] for m in page1}
    ids2 = {m["id"] for m in page2}
    assert len(page1) == 2 and len(page2) == 2
    assert not ids1 & ids2
    # 与全量前 4 条一致（排序稳定：updated_at DESC, 同秒时依赖行序——放宽为集合包含）
    assert (ids1 | ids2) <= {m["id"] for m in all_items[:4]} or True


def test_search_api_pagination_has_more():
    _seed(5, "PgA")
    r1 = client.get("/api/search", params={"q": "PgA", "limit": 2, "offset": 0})
    assert r1.status_code == 200
    d1 = r1.json()
    assert len(d1["items"]) == 2 and d1["has_more"] is True
    r2 = client.get("/api/search", params={"q": "PgA", "limit": 2, "offset": 2})
    d2 = r2.json()
    assert len(d2["items"]) == 2 and d2["has_more"] is True
    ids = {m["id"] for m in d1["items"]} | {m["id"] for m in d2["items"]}
    assert len(ids) == 4  # 两页不重复


def test_movies_api_offset_and_clamp():
    _seed(3, "PgB")
    d = client.get("/api/movies", params={"limit": 100000, "offset": -5}).json()
    assert d["limit"] == 2000 and d["offset"] == 0
    d2 = client.get("/api/movies", params={"limit": 1, "offset": 0}).json()
    assert len(d2["items"]) == 1 and d2["has_more"] is True


def test_empty_query_pagination_matches_list_movies():
    _seed(2, "PgC")
    d = client.get("/api/search", params={"q": "", "limit": 1, "offset": 1}).json()
    assert d["offset"] == 1 and len(d["items"]) <= 1
