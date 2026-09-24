"""UI 统一 P1：演职员前后端契约（电影 persons ↔ TV credits 双源归一）。

前端 cast.js 只认归一字段；后端三处出口必须同时带双字段别名：
- 电影 get_movie.persons：id/tmdb_id、character/character_name、profile_path/avatar
- 剧 _show_cast / 季回退：同上
- 集 episode_cast：cast + directors 同上（含 guest 透传）
"""
from app.routers import tv as tv_router
from app import store
from app.store import tv as tv_store


def test_norm_tv_person_aliases():
    p = tv_router._norm_tv_person({"id": 11, "name": "A",
                                   "character": "B", "profile_path": "/x.jpg"})
    assert p["id"] == 11 and p["tmdb_id"] == 11
    assert p["character"] == "B" and p["character_name"] == "B"
    assert p["profile_path"] == "/x.jpg" and p["avatar"] == "/x.jpg"


def test_season_cast_fallback_normalized():
    cast, source = tv_router._season_cast_with_fallback(
        None, [{"id": 5, "name": "N", "character": "C", "profile_path": "/p.jpg"}])
    assert source == "season"
    assert cast[0]["tmdb_id"] == 5
    assert cast[0]["character_name"] == "C"
    assert cast[0]["avatar"] == "/p.jpg"
    assert tv_router._season_cast_with_fallback(None, []) == ([], "none")


def test_episode_cast_normalized_entries():
    assert tv_store._norm_cast_entry(
        {"id": 9, "name": "G", "character": "R",
         "profile_path": "/g.jpg"}, True) == {
        "id": 9, "tmdb_id": 9, "name": "G", "character": "R",
        "character_name": "R", "profile_path": "/g.jpg",
        "avatar": "/g.jpg", "guest": True}
    assert tv_store._norm_director({"id": 3, "name": "D"}) == {
        "id": 3, "tmdb_id": 3, "name": "D"}


def test_get_movie_persons_aliases(tmp_path, monkeypatch):
    import app.store._base as base
    monkeypatch.setattr(base.settings, "data_dir", str(tmp_path))
    base.init_db()
    # 注意：_conn 用 import 期绑定的 DB_PATH，上面的 data_dir monkeypatch
    # 实际隔离不了，行会落进共享测试库 → 必须自清理，否则绝对路径行污染
    # 后续用例（planner 子树循环 hang、storage 相对路径校验失败）。
    mid = store.upsert_movie_by_path("/m/Film (2020)/Film.mkv", 1)
    pid = store.upsert_person(101, "Actor A", "person_101.jpg", "/prof.jpg")
    store.link_person(mid, pid, "actor", "Hero", 0)
    try:
        d = store.get_movie(mid)
        assert d and len(d["persons"]) == 1
        p = d["persons"][0]
        assert p["id"] == 101 and p["tmdb_id"] == 101
        assert p["character"] == "Hero" and p["character_name"] == "Hero"
        assert p["profile_path"] == "/prof.jpg" and p["avatar"] == "person_101.jpg"
    finally:
        store.delete_movie(mid)
        with store._lock, store._conn() as c:
            c.execute("DELETE FROM persons WHERE id=?", (pid,))
