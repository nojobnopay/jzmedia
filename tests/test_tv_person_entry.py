"""TV 人物页：ensure 自动建档 + 参演剧集 tv_works。"""
import json

import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app

client = TestClient(app)

_CREATED = (7701, 7702, 7703, 7704, 771)


@pytest.fixture(autouse=True)
def _cleanup_persons():
    yield
    from app.store._base import _conn, _lock
    with _lock, _conn() as c:
        for tid in _CREATED:
            row = c.execute("SELECT id FROM persons WHERE tmdb_id=?",
                            (tid,)).fetchone()
            if row:
                c.execute("DELETE FROM movie_person WHERE person_id=?",
                          (int(row["id"]),))
                c.execute("DELETE FROM persons WHERE tmdb_id=?", (tid,))


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvperson"
    root.mkdir()
    lib = store.create_library(name=f"tvperson-{tmp_path.name}", kind="tv",
                               path=str(root))
    library_paths.invalidate_cache()
    yield lib
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _show(tv_lib, title="人物剧", year=2023, tmdb_id=707,
          cast=None, season_cast=None, guests=None):
    lid = tv_lib["id"]
    sid = store.upsert_show(lid, title, year)
    store.update_show_meta(sid, tmdb_id=tmdb_id)
    credits = {"cast": cast or [], "crew": []}
    store.upsert_tmdb_cache(tmdb_id, {"title": title, "year": year}, credits, "",
                            media_type="tv")
    if season_cast is not None:
        store.upsert_season(sid, lid, 1, name="第 1 季",
                            cast_json=json.dumps(season_cast))
    e1 = store.upsert_episode(sid, lid, f"{title}/Season 01/{title}-S01E01.mkv",
                              1, 1, "一")
    if guests is not None:
        store.update_episode_meta(
            e1, episode_credits=json.dumps({"guests": guests, "directors": []}))
    if tmdb_id is not None:
        store.update_show_meta(sid, poster_path="tv_707.jpg")
    return sid


def test_ensure_creates_person_with_avatar(monkeypatch):
    monkeypatch.setattr("app.scanner.persist.save_person_avatar",
                        lambda tid, profile: f"posters/person_{tid}.jpg")
    r = client.post("/api/persons/ensure",
                    json={"tmdb_id": 7701, "name": "剧演员",
                          "profile_path": "/pp7701.jpg"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "剧演员" and d["avatar"] == "posters/person_7701.jpg"
    assert d["tv_works"] == []
    raw = store.get_person_raw(7701)
    assert raw["profile_tmdb_path"] == "/pp7701.jpg"
    # 幂等：已有头像不再重下（fake 会抛错也应通过）
    monkeypatch.setattr("app.scanner.persist.save_person_avatar",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net")))
    r2 = client.post("/api/persons/ensure",
                     json={"tmdb_id": 7701, "name": "剧演员改名",
                           "profile_path": "/pp7701.jpg"})
    assert r2.status_code == 200
    assert r2.json()["avatar"] == "posters/person_7701.jpg"


def test_ensure_no_profile_marks_dash():
    r = client.post("/api/persons/ensure", json={"tmdb_id": 7702, "name": "无图氏"})
    assert r.status_code == 200
    assert r.json()["avatar"] == "-"


def test_ensure_download_failure_retryable(monkeypatch):
    monkeypatch.setattr("app.scanner.persist.save_person_avatar",
                        lambda *a, **k: "")
    r = client.post("/api/persons/ensure",
                    json={"tmdb_id": 7703, "name": "断网氏",
                          "profile_path": "/pp7703.jpg"})
    assert r.status_code == 200
    assert r.json()["avatar"] == ""  # 非 '-'，下次点击重试


def test_ensure_bad_id():
    assert client.post("/api/persons/ensure", json={"tmdb_id": 0}).status_code == 404
    assert client.post("/api/persons/ensure", json={}).status_code == 422


def test_404_ensure_get_chain():
    """人物页新流程：GET 404 → ensure → GET 200（含 tv_works 键）。"""
    assert client.get("/api/persons/7704").status_code == 404
    r = client.post("/api/persons/ensure",
                    json={"tmdb_id": 7704, "name": "", "profile_path": ""})
    assert r.status_code == 200
    assert r.json()["name"] == "TMDB 7704"  # 无名兜底
    d = client.get("/api/persons/7704").json()
    assert d["name"] == "TMDB 7704" and d["tv_works"] == []


def test_tv_works_from_aggregate(tv_lib):
    _show(tv_lib, cast=[{"id": 771, "name": "聚合演员", "character": "主角",
                         "profile_path": "/a.jpg", "order": 0}])
    assert store.person_exists(771) is False  # 未建档也不影响查询函数
    works = store.get_person_tv_works(771)
    assert len(works) == 1 and works[0]["title"] == "人物剧"
    assert works[0]["character"] == "主角" and works[0]["show_id"] > 0
    # 库范围哨兵 → 空
    assert store.get_person_tv_works(771, library_ids=[-1]) == []
    # GET 透出 tv_works（先建档，否则 404）
    client.post("/api/persons/ensure", json={"tmdb_id": 771, "name": "聚合演员"})
    d = client.get("/api/persons/771").json()
    assert [w["title"] for w in d["tv_works"]] == ["人物剧"]


def test_show_detail_cast_carries_person_id(tv_lib):
    """剧详情 cast 必须带 TMDB 人物 id（前端点击跳转的唯一依据；曾漏掉致无法点击）。"""
    _show(tv_lib, cast=[{"id": 774, "name": "可点氏", "character": "主角",
                         "profile_path": "/c.jpg", "order": 0},
                        {"name": "无名氏", "character": "", "order": 1}])
    sid = store.list_shows(tv_lib["id"])[0]["id"]
    d = client.get(f"/api/tv/shows/{sid}").json()
    assert [(c.get("id"), c["name"]) for c in d["cast"]] == [(774, "可点氏")]
    # 端到端：凭此 id 可建档并进人物页
    r = client.post("/api/persons/ensure",
                    json={"tmdb_id": d["cast"][0]["id"], "name": "可点氏",
                          "profile_path": "/c.jpg"})
    assert r.status_code == 200
    assert client.get("/api/persons/774").status_code == 200
    from app.store._base import _conn, _lock
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM persons WHERE tmdb_id=774").fetchone()
        if row:
            c.execute("DELETE FROM movie_person WHERE person_id=?", (int(row["id"]),))
            c.execute("DELETE FROM persons WHERE tmdb_id=774")


def test_tv_works_from_season_and_guest(tv_lib):
    _show(tv_lib, title="季客串剧", tmdb_id=708, cast=[],
          season_cast=[{"id": 772, "name": "常驻氏", "character": "",
                        "profile_path": None, "order": 0}],
          guests=[{"id": 773, "name": "客串氏", "character": "路人",
                   "profile_path": None, "order": 0}])
    works = store.get_person_tv_works(772)
    assert [w["title"] for w in works] == ["季客串剧"]
    assert works[0]["character"] == ""  # 无角色名也算参演
    works = store.get_person_tv_works(773)
    assert works[0]["character"] == "路人"
    assert store.get_person_tv_works(779) == []
