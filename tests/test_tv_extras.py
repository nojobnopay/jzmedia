"""T4.1：剧集花絮/剧场版登记（extras.show_id）+ kind=extra 播放 + 未匹配集手动绑定。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, storage, store
from app.main import app
from app.scanner import tv_persist

client = TestClient(app)


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvlib"
    root.mkdir()
    lib = store.create_library(name=f"tv5-{tmp_path.name}", kind="tv", path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def _scan(lib, **kw):
    return scanner.scan_all(library_id=lib["id"], **kw)


def test_extras_registered_and_sample_skipped(tv_lib):
    lib, root = tv_lib
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E01.mkv")
    _touch(root, "Test Show (2020)/Featurettes/gag.mkv")
    _touch(root, "Test Show (2020)/Featurettes/Season 1/old.mkv")
    _touch(root, "Test Show (2020)/Behind The Scene/intro.mkv")
    _touch(root, "Test Show (2020)/Movies/Test.Movie.2021.mkv")
    _touch(root, "Test Show (2020)/Sample/sample.sample.mkv")
    res = _scan(lib)
    by = {}
    for r in res:
        by.setdefault(r["status"], []).append(r["file"])
    assert len(by.get("tv_ok", [])) == 1
    assert len(by.get("extra_attached", [])) == 4
    assert by.get("skipped_sample") == ["Test Show (2020)/Sample/sample.sample.mkv"]
    show = store.list_shows(lib["id"])[0]
    extras = store.list_extras_by_show(show["id"])
    kinds = sorted(x["kind"] for x in extras)
    assert kinds == ["behindthescenes", "featurette", "featurette", "movie"]
    assert all(x["show_id"] == show["id"] and x["movie_id"] is None for x in extras)
    # 剧详情：花絮/剧场版带回（含中文标签）
    d = client.get(f"/api/tv/shows/{show['id']}").json()
    assert len(d["extras"]) == 4
    labels = {x["label"] for x in d["extras"]}
    assert labels == {"特辑", "幕后", "剧场版"}
    assert all(x["exists"] is True for x in d["extras"])


def test_extras_gc(tv_lib):
    lib, root = tv_lib
    _touch(root, "Show (2020)/Season 01/Show.S01E01.mkv")
    _touch(root, "Show (2020)/Featurettes/a.mkv")
    _touch(root, "Show (2020)/Featurettes/b.mkv")
    _scan(lib)
    show = store.list_shows(lib["id"])[0]
    assert len(store.list_extras_by_show(show["id"])) == 2
    (root / "Show (2020)/Featurettes/a.mkv").unlink()
    _scan(lib)
    left = store.list_extras_by_show(show["id"])
    assert [x["file_path"].split("/")[-1] for x in left] == ["b.mkv"]
    # 剧只剩花絮时不删剧行（花絮仍可见）
    (root / "Show (2020)/Season 01/Show.S01E01.mkv").unlink()
    _scan(lib)
    assert store.get_show_meta(show["id"]) is not None


def test_extra_playback_kind(tv_lib, monkeypatch):
    lib, root = tv_lib
    _touch(root, "Show (2020)/Season 01/Show.S01E01.mkv")
    _touch(root, "Show (2020)/Featurettes/gag.mkv")
    _scan(lib)
    show = store.list_shows(lib["id"])[0]
    x = store.list_extras_by_show(show["id"])[0]
    monkeypatch.setattr("app.media.probe", lambda p, size=None, timeout=30: {
        "playable": True, "duration": 10.0, "audio": [], "subs": [],
        "attachments": [], "container": "mkv", "probe_ver": 99, "probed_at": 1})
    d = client.get(f"/api/stream/{x['id']}/media?kind=extra").json()
    assert d["kind"] == "extra" and d["file_path"].endswith("gag.mkv")
    assert d["title"] == "gag"
    decide = client.get(f"/api/stream/{x['id']}/decide?kind=extra&quality=auto").json()
    assert "/api/tv/extras/" in decide["direct_url"]
    b = client.get(f"/api/tv/extras/{x['id']}/blob")
    assert b.status_code == 200 and b.content == b"x"
    # 断点按 kind=extra 隔离
    r = client.post(f"/api/stream/progress?version_id={x['id']}&kind=extra",
                    json={"position": 5, "duration": 10})
    assert r.status_code == 200
    assert store.get_progress(x["id"], "extra")["position"] == 5


def test_extras_attach_to_renamed_show(tv_lib, monkeypatch):
    """花絮归属按剧根前缀解析：TMDB 改名后不再按目录名新建重复行。"""
    lib, root = tv_lib
    _touch(root, "Breaking.Bad.2008/Season 01/Breaking.Bad.S01E01.mkv")
    _scan(lib)
    detail = _detail(1396)
    detail["name"] = "绝命毒师"
    monkeypatch.setattr("app.tmdb.tv_detail", lambda tid: detail)
    monkeypatch.setattr("app.tmdb.tv_season", lambda tid, sn: {"episodes": [
        {"id": 62085, "episode_number": 1, "name": "向导", "overview": "",
         "still_path": "", "air_date": "2008-01-20", "runtime": 58, "vote_average": 8.2}]})
    monkeypatch.setattr("app.tmdb.search_tv", lambda q, year=None: [
        {"id": 1396, "name": "绝命毒师", "original_name": "Breaking Bad",
         "first_air_date": "2008-01-20", "popularity": 99.0, "vote_count": 900}])
    monkeypatch.setattr("app.tmdb.download_image",
                        lambda path, dest, size="w500": True)
    tv_persist.scrape_pending(library_ids=[lib["id"]], force=True)
    assert store.list_shows(lib["id"])[0]["title"] == "绝命毒师"
    # 新增花絮 → 再扫：应挂到已改名的剧行，而不是新建「Breaking Bad」
    _touch(root, "Breaking.Bad.2008/Featurettes/gag.mkv")
    res = _scan(lib)
    assert any(r["status"] == "extra_attached" for r in res)
    shows = store.list_shows(lib["id"])
    assert len(shows) == 1 and shows[0]["title"] == "绝命毒师"
    assert len(store.list_extras_by_show(shows[0]["id"])) == 1


def test_reattach_repairs_duplicate_show_row(tv_lib):
    """历史脏数据：花絮挂在按目录名新建的重复剧行 → 重挂+空行清理。"""
    lib, root = tv_lib
    _touch(root, "Breaking.Bad.2008/Season 01/Breaking.Bad.S01E01.mkv")
    _touch(root, "Breaking.Bad.2008/Featurettes/gag.mkv")
    _scan(lib)
    show = store.list_shows(lib["id"])[0]
    # 模拟历史 bug：另建目录名剧行并把花絮挂过去（正片行已被 TMDB 改名）
    dup = store.upsert_show(lib["id"], "Breaking Bad 目录名", 2008)
    x = store.list_extras_by_show(show["id"])[0]
    store.upsert_tv_extra(x["file_path"], dup, kind="featurette", library_id=lib["id"])
    assert store.get_show_meta(dup) is not None
    fixed = store.reattach_tv_extras(lib["id"])
    assert fixed == 1
    assert [y["show_id"] for y in store.list_extras_by_show(show["id"])] == [show["id"]]
    assert store.list_extras_by_show(dup) == []
    # 清理后再扫：重复行被 prune（无集也无花絮）
    _scan(lib)
    assert store.get_show_meta(dup) is None


def _detail(tmdb_id=100, episodes=2):
    return {
        "id": tmdb_id, "name": "测试剧", "original_name": "Test Show",
        "first_air_date": "2020-01-01", "overview": "简介", "status": "Ended",
        "vote_average": 8.0, "genres": [], "origin_country": ["CN"],
        "original_language": "zh", "number_of_seasons": 1,
        "number_of_episodes": episodes, "episode_run_time": [45],
        "networks": [], "production_companies": [], "created_by": [],
        "poster_path": "", "backdrop_path": "",
        "external_ids": {"imdb_id": "", "tvdb_id": None},
        "credits": {"cast": [], "crew": []},
        "seasons": [{"id": 1, "season_number": 1, "episode_count": episodes,
                     "name": "S1", "overview": "", "air_date": "", "poster_path": ""}],
    }


def test_unmatched_episode_marking_and_manual_bind(tv_lib, monkeypatch):
    lib, root = tv_lib
    for i in (1, 2, 3):
        _touch(root, f"Test Show (2020)/Season 01/Test.Show.S01E0{i}.mkv")
    _scan(lib)
    monkeypatch.setattr("app.tmdb.tv_detail", lambda tid: _detail())
    monkeypatch.setattr("app.tmdb.tv_season", lambda tid, sn: {"episodes": [
        {"id": 1011, "episode_number": 1, "name": "第一集", "overview": "o1",
         "still_path": "/s1.jpg", "air_date": "2020-01-01", "runtime": 45,
         "vote_average": 8.0},
        {"id": 1012, "episode_number": 2, "name": "第二集", "overview": "o2",
         "still_path": "/s2.jpg", "air_date": "2020-01-08", "runtime": 44,
         "vote_average": 7.5}]})
    monkeypatch.setattr("app.tmdb.search_tv", lambda q, year=None: [
        {"id": 100, "name": "测试剧", "original_name": "Test Show",
         "first_air_date": "2020-01-01", "popularity": 9.0, "vote_count": 50}])
    monkeypatch.setattr("app.tmdb.download_image",
                        lambda path, dest, size="w500": True)
    tv_persist.scrape_pending(library_ids=[lib["id"]], force=True)
    show = store.list_shows(lib["id"])[0]
    eps = sorted(store.get_show(show["id"])["episodes"], key=lambda e: e["episode"])
    assert [bool(e["tmdb_episode_id"]) for e in eps] == [True, True, False]
    assert eps[2]["needs_review"] == 1
    d = client.get(f"/api/tv/shows/{show['id']}").json()
    assert d["review_count"] == 1
    # 候选（按需拉 TMDB 季）
    cand = client.get(f"/api/tv/shows/{show['id']}/tmdb-episodes?season=1").json()
    assert [c["tmdb_episode_id"] for c in cand["items"]] == [1011, 1012]
    # 手动绑定第三集 → 第二集元数据
    r = client.post(f"/api/tv/episodes/{eps[2]['id']}/match-episode",
                    json={"tmdb_episode_id": 1012, "season": 1})
    assert r.status_code == 200, r.text
    e3 = store.get_episode(eps[2]["id"])
    assert e3["title"] == "第二集" and e3["needs_review"] == 0
    assert e3["tmdb_episode_id"] == 1012
    # 重刮不重复标（已手工绑定）
    tv_persist.scrape_pending(ids=[show["id"]], force=True)
    assert store.get_episode(eps[2]["id"])["needs_review"] == 0
