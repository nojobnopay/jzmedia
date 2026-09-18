"""E 阶段离线/降级：本地索引、NFO 导入、链路顺序、Wikidata 桥接、IMDb 导入。"""
import gzip
import os

import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store
from app.main import app
from app.metadata import chain, douban, local, nfo_import, wikidata

client = TestClient(app)


@pytest.fixture()
def offline_tmdb(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("tmdb offline")
    monkeypatch.setattr(scanner.tmdb, "search_movie", _boom)


def _entry(tmdb_id, title, year, imdb="", source="tmdb"):
    store.upsert_match_entry(source, str(tmdb_id) if source == "tmdb" else imdb or title,
                             "movie", title, "", year, tmdb_id, imdb, {})


def test_local_search_and_best(media_root):
    _entry(600001, "离线测试片", 2001, "tt600001")
    _entry(600002, "另一部片", 2002, "tt600002")
    hits = local.search("离线测试片", 2001)
    assert hits and hits[0].tmdb_id == 600001 and hits[0].score >= local.MIN_AUTO_SCORE
    assert local.best("离线测试片", 2001).tmdb_id == 600001
    # 年份差太多自动匹配降级（分数不足返回 None，但候选仍在）
    assert local.best("离线测试片", 1980) is None
    assert local.search("离线测试片", 1980)
    store.delete_match_entries("tmdb", "600001")
    store.delete_match_entries("tmdb", "600002")


def test_chain_order_and_douban_default_off(monkeypatch):
    assert chain.chain_for(None) == ["local", "tmdb", "wikidata"]
    assert douban.enabled() is False
    monkeypatch.setattr(douban, "search", lambda *a, **kw: [])
    _entry(600010, "链路测试", 2010)
    hits = chain.search("链路测试", 2010)
    assert hits and hits[0].source == "tmdb"
    store.delete_match_entries("tmdb", "600010")


def test_nfo_import(tmp_path):
    p = tmp_path / "movie.nfo"
    p.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<movie><title>NFO 电影</title><originaltitle>NFO Movie</originaltitle>
<year>1999</year><uniqueid type="tmdb">600020</uniqueid>
<uniqueid type="imdb">tt600020</uniqueid></movie>""", encoding="utf-8")
    c = nfo_import.read_nfo(str(p))
    assert c.tmdb_id == 600020 and c.year == 1999 and c.source == "nfo"


def test_wikidata_stub(monkeypatch):
    def fake_get(params):
        if params.get("action") == "wbsearchentities":
            return {"search": [{"id": "Q1", "label": "某片"}]}
        return {"entities": {"Q1": {"labels": {"zh": {"value": "某片"}},
                                    "claims": {"P345": [{"mainsnak": {"datavalue": {"value": "tt1234567"}}}],
                                               "P4947": [{"mainsnak": {"datavalue": {"value": "600030"}}}]}}}}
    monkeypatch.setattr(wikidata, "_get_json", fake_get)
    hits = wikidata.search("某片", 2000)
    assert hits and hits[0].tmdb_id == 600030 and hits[0].imdb_id == "tt1234567"


def test_scan_offline_local_fallback(media_root, offline_tmdb):
    rel = "offline/Offline.Movie.2001.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    # 候选索引 + tmdb 缓存（apply_cached_to_movie 复用）
    _entry(600040, "Offline Movie", 2001, "tt600040")
    store.upsert_tmdb_cache(600040, {"title": "Offline Movie", "year": 2001,
                                     "media_type": "movie"})
    try:
        r = scanner.scan_one(str(p))
        assert r["status"].startswith("ok"), r
        assert r.get("match_source") == "tmdb"   # 来源标注：候选来自 tmdb 缓存
        row = store.get_by_path(rel)
        assert row and row["tmdb_id"] == 600040
    finally:
        store.delete_match_entries("tmdb", "600040")


def test_scan_offline_nfo_fallback(media_root, offline_tmdb):
    rel = "offline2/Nfo.Movie.2002.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    (p.parent / "movie.nfo").write_text(
        '<?xml version="1.0"?><movie><title>Nfo Movie</title><year>2002</year>'
        '<uniqueid type="tmdb">600050</uniqueid></movie>', encoding="utf-8")
    store.upsert_tmdb_cache(600050, {"title": "Nfo Movie", "year": 2002,
                                     "media_type": "movie"})
    try:
        r = scanner.scan_one(str(p))
        assert r["status"].startswith("ok"), r
        assert r.get("match_source") == "nfo"
    finally:
        store.delete_match_entries("nfo", "movie.nfo")


def test_tmdb_search_api_offline_fallback(media_root):
    _entry(600060, "接口离线片", 2003)
    try:
        d = client.get("/api/tmdb/search", params={"q": "接口离线片"}).json()
        assert d["source"] == "offline"
        assert any(i.get("tmdb_id") == 600060 for i in d["items"])
    finally:
        store.delete_match_entries("tmdb", "600060")


def test_imdb_tsv_import(tmp_path):
    p = tmp_path / "title.basics.tsv.gz"
    with gzip.open(p, "wt", encoding="utf-8", newline="") as fh:
        fh.write("tconst\ttitleType\tprimaryTitle\toriginalTitle\tstartYear\n")
        fh.write("tt7000001\tmovie\tIMDb 片一\tIMDb One\t1990\n")
        fh.write("tt7000002\tshort\t短片\t\t1991\n")
    r = store.import_imdb_tsv(str(p))
    assert r["imported"] == 1
    hits = local.search("IMDb 片一", 1990)
    assert any(c.imdb_id == "tt7000001" for c in hits)
    store.delete_match_entries("imdb", "tt7000001")
