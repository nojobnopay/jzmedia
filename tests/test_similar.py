"""库中类似（Plex 式推荐）：本地相似度评分/排除/门槛/接口回归。"""
from fastapi.testclient import TestClient

from app import store
from app.main import app

client = TestClient(app)


def _row(rel, title="T", year=2020, **meta):
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, title=title, year=year, **meta)
    return mid


def _person(tmdb_id, name="P"):
    return store.upsert_person(tmdb_id, name)


def test_similar_scores_and_excludes(media_root):
    # 主片 A：动作/科幻，导演 91001，主演 91002（番位 0）
    a = _row("sim/a.mkv", title="A", year=1999, tmdb_id=71001,
             genre_ids=[28, 878], genres=["动作", "科幻"],
             origin_country="US", original_language="en", region="美国")
    store.upsert_tmdb_cache(71001, {"title": "A", "collection_tmdb_id": 55501,
                                    "collection_name": "测试系列"})
    d = _person(91001, "导演甲")
    p = _person(91002, "主演乙")
    store.link_person(a, d, "director", cast_order=0)
    store.link_person(a, p, "actor", cast_order=0)
    # 同片第二版本：必须被排除
    _row("sim/a.1080p.mkv", title="A", year=1999, tmdb_id=71001,
         genre_ids=[28, 878], genres=["动作", "科幻"])

    # B：同 TMDB 系列（+100）
    b = _row("sim/b.mkv", title="B", year=2003, tmdb_id=71002,
             genre_ids=[28], genres=["动作"], original_language="en")
    store.upsert_tmdb_cache(71002, {"title": "B", "collection_tmdb_id": 55501,
                                    "collection_name": "测试系列"})
    # C：同导演（+36），不同类型
    c = _row("sim/c.mkv", title="C", year=2010, tmdb_id=71003,
             genre_ids=[18], genres=["剧情"])
    store.link_person(c, d, "director", cast_order=0)
    # D：只同类型（同 hits 数）
    dd = _row("sim/d.mkv", title="D", year=2015, tmdb_id=71004,
              genre_ids=[28, 878], genres=["动作", "科幻"])
    # G：部分类型重合 + 独有类型 → 加权 Jaccard 不因独有类型 KeyError
    g = _row("sim/g.mkv", title="G", year=2016, tmdb_id=71007,
             genre_ids=[28, 878, 12], genres=["动作", "科幻", "冒险"])
    # E：只同产地/语言/年份，无内容信号 → 不应入选
    e = _row("sim/e.mkv", title="E", year=2000, tmdb_id=71005,
             genre_ids=[35], genres=["喜剧"],
             origin_country="US", original_language="en", region="美国")
    # F：完全不同 → 不应入选
    f = _row("sim/f.mkv", title="F", year=1980, tmdb_id=71006,
             genre_ids=[99], genres=["纪录片"], region="法国",
             original_language="fr")
    # 主片同系列的 cache 需要能查到（upsert 已写 71001）
    items = store.similar_movies(a, limit=20)
    ids = [x["id"] for x in items]
    assert b in ids and c in ids and dd in ids and g in ids
    assert e not in ids and f not in ids
    # 同片其它版本（tmdb_id=71001）不得出现在推荐里
    assert all(x["tmdb_id"] != 71001 for x in items)
    # 排序：同系列分数最高
    assert items[0]["id"] == b
    assert "同系列" in items[0]["reason"]
    by_id = {x["id"]: x for x in items}
    assert "同导演" in by_id[c]["reason"]
    assert "同类型" in by_id[dd]["reason"]
    # version_count 海报粒度
    assert by_id[b]["version_count"] == 1
    # limit 生效
    assert len(store.similar_movies(a, limit=2)) == 2


def test_similar_manual_collection(media_root):
    a = _row("sim2/a.mkv", title="MA", year=2001, tmdb_id=72001,
             genre_ids=[16], genres=["动画"])
    b = _row("sim2/b.mkv", title="MB", year=2002, tmdb_id=72002,
             genre_ids=[16], genres=["动画"])
    c = _row("sim2/c.mkv", title="MC", year=2003, tmdb_id=72003,
             genre_ids=[16], genres=["动画"])
    col = store.create_collection("相似测试合集", member_ids=[a, b])
    items = store.similar_movies(a, limit=10)
    by_id = {x["id"]: x for x in items}
    assert b in by_id and "同合集" in by_id[b]["reason"]
    assert c in by_id and "同合集" not in by_id[c]["reason"]


def test_similar_no_signals_returns_empty(media_root):
    a = _row("sim3/a.mkv", title="孤片", year=1970)   # 无 tmdb_id/类型/人物/标签
    assert store.similar_movies(a) == []
    assert store.similar_movies(99999999) == []


def test_similar_api(media_root):
    a = _row("sim4/a.mkv", title="API", year=2020, tmdb_id=74001,
             genre_ids=[53], genres=["惊悚"])
    b = _row("sim4/b.mkv", title="APIB", year=2021, tmdb_id=74002,
             genre_ids=[53], genres=["惊悚"])
    r = client.get(f"/api/movies/{a}/similar", params={"limit": 5})
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == a
    assert any(x["id"] == b for x in d["items"])
    assert client.get(f"/api/movies/{a + 999999}/similar").status_code == 404
