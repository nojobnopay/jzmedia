"""离线/无 token 外部元数据（Phase 1+2）：NFO 全量回放、imdb 桥、别名、
external_meta 落库、bind-external、TV 外源落库、provider 解析。"""
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import scanner, store
from app.config import settings
from app.main import app
from app.metadata import auto, bangumi, chain, external, local, tvmaze, wikidata
from app.metadata.base import Candidate
from app.scanner import tv_persist

client = TestClient(app)


@pytest.fixture()
def offline_tmdb(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("tmdb offline")

    monkeypatch.setattr(scanner.tmdb, "search_movie", _boom)


def _write_movie(media_root: pathlib.Path, rel: str, nfo: str = "",
                 poster: bytes | None = None):
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    if nfo:
        (p.parent / "movie.nfo").write_text(nfo, encoding="utf-8")
    if poster is not None:
        (p.parent / "poster.jpg").write_bytes(poster)
    return p


def _cleanup_movie(mid: int, rel: str):
    store.delete_movie(int(mid))
    with store._lock, store._conn() as c:
        c.execute("DELETE FROM scan_state WHERE file_path=?", (rel,))


def test_nfo_full_replay_offline(media_root, offline_tmdb):
    rel = "extmeta/nfo/NFO 离线片 (1999)/NFO 离线片 (1999).mkv"
    nfo = ('<?xml version="1.0"?><movie><title>NFO 离线片</title>'
           '<originaltitle>NFO Offline</originaltitle><year>1999</year>'
           '<plot>离线简介</plot><genre>剧情</genre><country>日本</country>'
           '<director>某导演</director>'
           '<actor><name>某演员</name><role>主角</role></actor>'
           '<uniqueid type="imdb">tt-ext-600020</uniqueid></movie>')
    p = _write_movie(media_root, rel, nfo, b"\xff\xd8\xff" + b"0" * 500)
    r = scanner.scan_one(str(p))
    assert r["status"] == "ok_external", r
    assert r["match_source"] == "nfo"
    row = store.get_by_path(rel)
    assert row["title"] == "NFO 离线片" and row["year"] == 1999
    assert not row["tmdb_id"]                     # 不写 tmdb_id（保留后续升级）
    assert row["overview"] == "离线简介"
    assert row["origin_country"] == "JP"
    assert "某导演" in row["person_names"] and "某演员" in row["person_names"]
    assert row["poster_path"].startswith("posters/ext/")
    assert (pathlib.Path(settings.data_dir) / row["poster_path"]).exists()
    ext = store.get_external("nfo", rel)
    assert ext and ext["title"] == "NFO 离线片"
    assert store.get_scan_state(rel)["status"] == "ok_external"
    # 增量短路：二次扫描不再重复处理
    r2 = scanner.scan_one(str(p))
    assert r2["status"] == "skipped_external"
    _cleanup_movie(r["movie_id"], rel)
    store.delete_external("nfo", rel)
    store.delete_match_entries("nfo", rel)


def test_imdb_bridge_via_cache(media_root):
    store.upsert_match_entry("imdb", "tt-ext-bridge", "movie", "Bridge Movie", "",
                             1991, None, "tt-ext-bridge", {})
    store.upsert_tmdb_cache(710001, {"title": "Bridge Movie", "year": 1991,
                                     "imdb_id": "tt-ext-bridge",
                                     "media_type": "movie"})
    try:
        hits = local.search("Bridge Movie", 1991)
        hit = [c for c in hits if c.imdb_id == "tt-ext-bridge"]
        assert hit and hit[0].tmdb_id == 710001      # imdb → tmdb 离线桥
        assert store.find_tmdb_by_imdb("tt-ext-bridge") == 710001
    finally:
        store.delete_match_entries("imdb", "tt-ext-bridge")
        store.delete_match_entries("tmdb", "710001")


def test_alias_scoring(media_root):
    store.upsert_match_entry("tmdb", "710002", "movie", "别名主标题", "", 2002,
                             710002, "", {}, alt_titles="Alias Title\n另一个别名")
    try:
        hits = local.search("Alias Title", 2002)
        assert hits and hits[0].tmdb_id == 710002
        # 无年份时别名分不足 45：不自动绑定（保守）
        assert local.best("Alias Title", None) is None
        assert local.best("Alias Title", 2002).tmdb_id == 710002
    finally:
        store.delete_match_entries("tmdb", "710002")


def test_apply_external_movie_and_match_index(media_root):
    rel = "extmeta/apply/Apply.Ext.2004.mkv"
    mid = store.upsert_movie_by_path(rel, library_id=1)
    detail = {
        "title": "外源应用片", "original_title": "Apply Ext", "year": 2004,
        "overview": "简介X", "genres": ["动画"], "countries": ["JP"],
        "aliases": ["外源别名"], "imdb_id": "tt-ext-apply",
        "people": {"directors": [{"name": "外导"}],
                   "cast": [{"name": "外演", "character": "主役", "order": 0}]},
        "poster_url": "", "episodes": [], "tmdb_id": None,
    }
    out = external.apply_external_movie(mid, detail, source="wikidata",
                                        source_id="QEXT", write_nfo=False)
    assert out["source"] == "wikidata"
    row = store.get_movie(mid)
    assert row["title"] == "外源应用片" and row["match_source"] == "wikidata"
    assert row["imdb_id"] == "tt-ext-apply"
    assert "外导" in row["person_names"] and "外演" in row["person_names"]
    assert store.get_external("wikidata", "QEXT")
    idx = store.search_match_index("外源别名", kind="movie")
    assert any(r["source"] == "wikidata" for r in idx)
    store.delete_external("wikidata", "QEXT")
    store.delete_match_entries("wikidata", "QEXT")
    _cleanup_movie(mid, rel)


def test_bind_external_movie_api(media_root, monkeypatch):
    rel = "extmeta/api/Bind.External.2005.mkv"
    mid = store.upsert_movie_by_path(rel, library_id=1)
    detail = {"title": "外源接口片", "original_title": "Api Ext", "year": 2005,
              "overview": "接口简介", "genres": ["剧情"], "countries": ["CN"],
              "aliases": [], "people": {"directors": [{"name": "接口导"}],
                                        "cast": []},
              "poster_url": "", "episodes": [], "tmdb_id": None}
    monkeypatch.setitem(chain._DETAILERS, "wikidata",
                        lambda sid: dict(detail))
    try:
        r = client.post(f"/api/movies/{mid}/bind-external",
                        json={"source": "wikidata", "source_id": "QAPI"})
        assert r.status_code == 200, r.text
        row = store.get_movie(mid)
        assert row["title"] == "外源接口片" and row["match_source"] == "wikidata"
        assert int(row["needs_review"] or 0) == 0
        assert store.get_external("wikidata", "QAPI")
        # 缺参 422
        r2 = client.post(f"/api/movies/{mid}/bind-external", json={"source": "wikidata"})
        assert r2.status_code == 422
    finally:
        store.delete_external("wikidata", "QAPI")
        store.delete_match_entries("wikidata", "QAPI")
        _cleanup_movie(mid, rel)


def test_apply_external_show_fills_episodes(media_root):
    show_id = store.upsert_show(1, "外源测试剧X", 2020)
    store.upsert_episode(show_id, 1, "extmeta/show/S01E01.mkv", 1, 1)
    detail = {
        "title": "外源测试剧X", "original_title": "Ext Show X", "year": 2020,
        "overview": "剧简介", "genres": ["动画"], "countries": ["CN"],
        "aliases": ["测试剧别名"],
        "people": {"directors": [], "cast": [{"name": "声优A"}]},
        "poster_url": "", "tmdb_id": None,
        "seasons": [{"season_number": 1, "name": "第 1 季", "episode_count": 1}],
        "episodes": [{"season": 1, "episode": 1, "title": "第一集",
                      "overview": "集简介", "air_date": "2020-01-01"}],
    }
    try:
        out = external.apply_external_show(show_id, detail, source="bgm",
                                           source_id="bm-ext-1", write_nfo=False)
        assert out["source"] == "bgm" and out["episodes_filled"] == 1
        show = store.get_show_meta(show_id)
        assert show["title"] == "外源测试剧X" and show["match_source"] == "bgm"
        assert show["origin_country"] == "CN"
        eps = store.list_episodes(show_id)
        assert eps and eps[0]["title"] == "第一集"
        assert eps[0]["overview"] == "集简介"
        assert store.get_external("bgm", "bm-ext-1")
        seasons = store.list_seasons(show_id)
        assert any(int(s.get("season")) == 1 for s in seasons)
    finally:
        store.delete_external("bgm", "bm-ext-1")
        store.delete_match_entries("bgm", "bm-ext-1")
        with store._lock, store._conn() as c:
            c.execute("DELETE FROM tv_episodes WHERE show_id=?", (show_id,))
            c.execute("DELETE FROM tv_seasons WHERE show_id=?", (show_id,))
            c.execute("DELETE FROM tv_shows WHERE id=?", (show_id,))


def test_chain_detail_for_prefers_provider(monkeypatch):
    from app.metadata.base import Candidate
    monkeypatch.setitem(chain._DETAILERS, "bgm",
                        lambda sid: {"title": "详情标题", "episodes": [],
                                     "_source": "bgm"})
    cand = Candidate(title="候选标题", source="bgm", source_id="42")
    d = chain.detail_for(cand)
    assert d["title"] == "详情标题"
    # 未知源/取详情失败时回退 payload
    cand2 = Candidate(title="Payload 标题", source="unknown", source_id="9")
    assert chain.detail_for(cand2)["title"] == "Payload 标题"


def test_scan_uses_external_provider(media_root, monkeypatch):
    rel = "extmeta/chain/Chain.Ext.2006.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    detail = {"title": "Chain Ext", "original_title": "Chain Ext", "year": 2006,
              "overview": "外源链简介", "genres": [], "countries": [],
              "aliases": [], "people": {"directors": [], "cast": []},
              "poster_url": "", "episodes": [], "tmdb_id": None}
    monkeypatch.setattr(scanner.tmdb, "search_movie", lambda *a, **k: [])
    monkeypatch.setitem(chain._SEARCHERS, "wikidata",
                        lambda term, year, kind, limit: [
                            Candidate(title="Chain Ext", year=2006,
                                      source="wikidata", source_id="QCHAIN",
                                      score=55.0,
                                      payload={"detail": dict(detail)})])
    monkeypatch.setitem(chain._DETAILERS, "wikidata", lambda sid: dict(detail))
    try:
        r = scanner.scan_one(str(p))
        assert r["status"] == "ok_external", r
        assert r["match_source"] == "wikidata"
        row = store.get_by_path(rel)
        assert row["title"] == "Chain Ext" and row["overview"] == "外源链简介"
        assert not row["tmdb_id"]
    finally:
        store.delete_external("wikidata", "QCHAIN")
        store.delete_match_entries("wikidata", "QCHAIN")
        _cleanup_movie(store.get_by_path(rel)["id"], rel)


def test_auto_pick_gate(monkeypatch):
    def cand(title, year, source="wikidata", aliases=None):
        d = {"title": title, "aliases": aliases or []}
        return Candidate(title=title, year=year, source=source, source_id="x",
                         payload={"detail": d})

    # 相似 + 年份符 → 自动
    c, review, why = auto.pick_auto([cand("Target Movie", 2001)], "Target Movie", 2001)
    assert c is not None and review == 0
    # 年份不符 → 待确认
    c, review, _ = auto.pick_auto([cand("Target Movie", 1980)], "Target Movie", 2001)
    assert c is not None and review == 1
    # 不像 → 不绑定
    c, _review, _ = auto.pick_auto([cand("完全不同的片", 2001)], "Target Movie", 2001)
    assert c is None
    # 别名命中视为相似
    c, review, _ = auto.pick_auto([cand("Other Name", 2001, aliases=["Target Movie"])],
                                  "Target Movie", 2001)
    assert c is not None and review == 0


def test_scrape_show_uses_external_provider(media_root, monkeypatch):
    show_id = store.upsert_show(1, "外源刮削剧Y", 2011)
    store.upsert_episode(show_id, 1, "extmeta/scrub/S01E01.mkv", 1, 1)
    show = store.get_show_meta(show_id)
    detail = {
        "title": "外源刮削剧Y", "original_title": "Scrub Ext Y", "year": 2011,
        "overview": "剧简介Y", "genres": [], "countries": [], "aliases": [],
        "people": {"directors": [], "cast": []}, "poster_url": "",
        "tmdb_id": None,
        "seasons": [{"season_number": 1, "episode_count": 1}],
        "episodes": [{"season": 1, "episode": 1, "title": "集Y1",
                      "overview": "集简介Y"}],
    }
    monkeypatch.setattr(tv_persist.tmdb, "search_tv", lambda *a, **k: [])
    monkeypatch.setitem(chain._SEARCHERS, "wikidata",
                        lambda term, year, kind, limit: [
                            Candidate(title="外源刮削剧Y", year=2011, source="wikidata",
                                      source_id="QSCRUB", score=55.0,
                                      payload={"detail": dict(detail)})])
    monkeypatch.setitem(chain._DETAILERS, "wikidata", lambda sid: dict(detail))
    try:
        out = tv_persist.scrape_show(show)
        assert out["status"] == "ok_external", out
        assert out["match_source"] == "wikidata"
        fresh = store.get_show_meta(show_id)
        assert fresh["title"] == "外源刮削剧Y"
        eps = store.list_episodes(show_id)
        assert eps and eps[0]["title"] == "集Y1"
    finally:
        store.delete_external("wikidata", "QSCRUB")
        store.delete_match_entries("wikidata", "QSCRUB")
        with store._lock, store._conn() as c:
            c.execute("DELETE FROM tv_episodes WHERE show_id=?", (show_id,))
            c.execute("DELETE FROM tv_seasons WHERE show_id=?", (show_id,))
            c.execute("DELETE FROM tv_shows WHERE id=?", (show_id,))


def test_tvmaze_detail_of():
    show = {"id": 1, "name": "Show X", "premiered": "2001-02-03",
            "summary": "<p>简介</p>", "genres": ["Drama"],
            "network": {"name": "Net", "country": {"code": "US"}},
            "image": {"original": "https://x/1.jpg"},
            "rating": {"average": 8.2}, "status": "Ended",
            "externals": {"imdb": "tttv1", "thetvdb": 123},
            "_embedded": {
                "cast": [{"person": {"name": "演员P"},
                          "character": {"name": "角色C"}}],
                "episodes": [{"season": 1, "number": 2, "name": "E2",
                              "summary": "<b>集简介</b>", "airdate": "2001-02-10",
                              "image": {"original": "https://x/e2.jpg"}}],
            }}
    d = tvmaze._detail_of(show)
    assert d["title"] == "Show X" and d["year"] == 2001
    assert d["overview"] == "简介" and d["countries"] == ["US"]
    assert d["imdb_id"] == "tttv1" and d["tvdb_id"] == 123
    assert d["people"]["cast"][0]["name"] == "演员P"
    assert d["episodes"][0]["episode"] == 2
    assert d["episodes"][0]["overview"] == "集简介"


def test_tvmaze_search_only_tv(monkeypatch):
    monkeypatch.setattr(tvmaze, "_get", lambda *a, **kw: [
        {"show": {"id": 7, "name": "TV Show", "premiered": "2010-01-01",
                  "externals": {}}}])
    assert tvmaze.search("TV Show", 2010, kind="movie") == []
    hits = tvmaze.search("TV Show", 2010, kind="tv")
    assert hits and hits[0].source == "tvmaze" and hits[0].source_id == "7"


def test_bangumi_detail_of():
    subject = {"id": 237, "name": "GHOST", "name_cn": "攻壳机动队",
               "date": "1995-11-18", "summary": "简介",
               "images": {"large": "https://x/b.jpg"},
               "rating": {"score": 9.1},
               "infobox": [{"key": "导演", "value": "押井守"},
                           {"key": "别名", "value": "Ghost in the Shell"},
                           {"key": "地区", "value": "日本"}]}
    info = bangumi._infobox(subject["infobox"])
    d = bangumi._detail_of(subject, info, [{"ep": 1, "name": "EP1",
                                            "name_cn": "第一话", "airdate": "1995-11-18"}])
    assert d["title"] == "攻壳机动队" and d["year"] == 1995
    assert d["countries"] == ["JP"]
    assert d["people"]["directors"][0]["name"] == "押井守"
    assert "Ghost in the Shell" in d["aliases"]
    assert d["episodes"][0]["title"] == "第一话"


def test_wikidata_detail(monkeypatch):
    entities = {
        "Q100": {
            "labels": {"zh": {"value": "某片"}, "en": {"value": "Some Film"}},
            "descriptions": {"zh": {"value": "简介Z"}},
            "claims": {
                "P345": [{"mainsnak": {"datavalue": {"value": "tt1234567"}}}],
                "P577": [{"mainsnak": {"datavalue": {"value": "+1999-05-06T00:00:00Z"}}}],
                "P57": [{"mainsnak": {"datavalue": {"value": {"id": "Q200"}}}}],
                "P161": [{"mainsnak": {"datavalue": {"value": {"id": "Q300"}}}}],
                "P495": [{"mainsnak": {"datavalue": {"value": {"id": "Q400"}}}}],
                "P18": [{"mainsnak": {"datavalue": {"value": "Poster file.jpg"}}}],
            },
        },
        "Q200": {"labels": {"zh": {"value": "某导演"}}, "claims": {}},
        "Q300": {"labels": {"zh": {"value": "某演员"}}, "claims": {}},
        "Q400": {"labels": {"zh": {"value": "日本"}},
                 "claims": {"P297": [{"mainsnak": {"datavalue": {"value": "JP"}}}]}},
    }
    monkeypatch.setattr(wikidata, "_fetch_entities", lambda ids: entities)
    d = wikidata.detail("Q100")
    assert d["title"] == "某片" and d["year"] == 1999
    assert d["imdb_id"] == "tt1234567"
    assert d["countries"] == ["JP"]
    assert d["people"]["directors"][0]["name"] == "某导演"
    assert d["poster_url"].startswith("https://commons.wikimedia.org/")
    assert wikidata.detail("Q404") is None
