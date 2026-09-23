"""T3：TV NFO/海报落盘（tvshow/季/集 NFO、所有权保护、artwork_mode 门槛、重建任务）。"""
import os
import time
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import artwork, library_paths, scanner, storage, store
from app.main import app
from app.scanner import tv_nfo_link, tv_persist

client = TestClient(app)


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvlib"
    root.mkdir()
    lib = store.create_library(name=f"tv4-{tmp_path.name}", kind="tv", path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def _detail(tmdb_id=100):
    return {
        "id": tmdb_id, "name": "测试剧", "original_name": "Test Show",
        "first_air_date": "2020-01-01", "overview": "剧情简介", "status": "Ended",
        "vote_average": 8.0, "genres": [{"id": 18, "name": "剧情"}],
        "origin_country": ["CN"], "original_language": "zh",
        "number_of_seasons": 1, "number_of_episodes": 2, "episode_run_time": [45],
        "networks": [{"name": "CCTV"}], "production_companies": [],
        "created_by": [], "poster_path": "/p.jpg", "backdrop_path": "/b.jpg",
        "external_ids": {"imdb_id": "tt100", "tvdb_id": 42},
        "credits": {"cast": [{"id": 9, "name": "演员甲", "character": "甲",
                              "profile_path": None}], "crew": []},
        "seasons": [{"id": 1, "season_number": 1, "episode_count": 2,
                     "name": "第一季", "overview": "季简介",
                     "air_date": "2020-01-01", "poster_path": "/s1.jpg"}],
    }


def _season():
    return {"episodes": [
        {"id": 1011, "episode_number": 1, "name": "第一集", "overview": "集一简介",
         "still_path": "/st1.jpg", "air_date": "2020-01-01", "runtime": 45,
         "vote_average": 8.0},
        {"id": 1012, "episode_number": 2, "name": "第二集", "overview": "集二简介",
         "still_path": "/st2.jpg", "air_date": "2020-01-08", "runtime": 44,
         "vote_average": 7.5},
    ]}


@pytest.fixture()
def scraped_show(tv_lib, monkeypatch):
    lib, root = tv_lib
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E01.mkv")
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E02.mkv")
    scanner.scan_all(library_id=lib["id"])
    monkeypatch.setattr("app.tmdb.tv_detail", lambda tid: _detail())
    monkeypatch.setattr("app.tmdb.tv_season", lambda tid, sn: _season())
    monkeypatch.setattr("app.tmdb.search_tv", lambda q, year=None: [
        {"id": 100, "name": "测试剧", "original_name": "Test Show",
         "first_air_date": "2020-01-01", "popularity": 10.0, "vote_count": 100}])
    monkeypatch.setattr("app.tmdb.download_image",
                        lambda path, dest, size="w500": True)
    res = tv_persist.scrape_pending(library_ids=[lib["id"]], force=True)
    assert res[0]["status"] == "ok"
    show = store.list_shows(lib["id"])[0]
    return lib, root, show


def test_sync_tv_nfos_writes_all_levels(scraped_show):
    lib, root, show = scraped_show
    backend = storage.backend_for(lib["id"])
    r = tv_nfo_link.sync_tv_nfos_for(show["id"], backend=backend)
    assert r["ok"], r
    show_dir = root / "Test Show (2020)"
    season_dir = show_dir / "Season 01"
    assert (show_dir / "tvshow.nfo").is_file()
    assert (season_dir / "season.nfo").is_file()
    assert (season_dir / "Test.Show.S01E01.nfo").is_file()
    assert (season_dir / "Test.Show.S01E02.nfo").is_file()
    tv = ET.fromstring((show_dir / "tvshow.nfo").read_bytes())
    assert tv.tag == "tvshow"
    assert tv.findtext("title") == "测试剧"
    assert tv.findtext("originaltitle") == "Test Show"
    assert tv.findtext("premiered") == "2020-01-01"
    assert tv.findtext("status") == "Ended"
    assert [g.text for g in tv.findall("genre")] == ["剧情"]
    assert tv.findtext("namedseason") == "第一季"
    uids = {u.get("type"): u.text for u in tv.findall("uniqueid")}
    assert uids["tmdb"] == "100" and uids["imdb"] == "tt100" and uids["tvdb"] == "42"
    assert tv.find("actor/name").text == "演员甲"
    season = ET.fromstring((season_dir / "season.nfo").read_bytes())
    assert season.findtext("seasonnumber") == "1"
    assert season.findtext("plot") == "季简介"
    ep = ET.fromstring((season_dir / "Test.Show.S01E01.nfo").read_bytes())
    assert ep.tag == "episodedetails"
    assert ep.findtext("showtitle") == "测试剧"
    assert ep.findtext("season") == "1" and ep.findtext("episode") == "1"
    assert ep.findtext("title") == "第一集"
    assert ep.findtext("aired") == "2020-01-01"
    assert ep.findtext("runtime") == "45"
    assert ep.find("uniqueid").text == "1011"
    # 剧/集哈希已落库（所有权保护依据）
    assert store.get_show_meta(show["id"])["nfo_hash"]
    assert store.get_episode(store.get_show(show["id"])["episodes"][0]["id"])["nfo_hash"]


def test_nfo_ownership_protection(scraped_show):
    lib, root, show = scraped_show
    backend = storage.backend_for(lib["id"])
    tv_nfo_link.sync_tv_nfos_for(show["id"], backend=backend)
    ep_path = root / "Test Show (2020)/Season 01/Test.Show.S01E01.nfo"
    ep_path.write_bytes(b"<episodedetails>user edited</episodedetails>")
    tv_nfo_link.sync_tv_nfos_for(show["id"], backend=backend)
    assert b"user edited" in ep_path.read_bytes()          # 外部改动不被覆盖
    tv_nfo_link.sync_tv_nfos_for(show["id"], backend=backend, force=True)
    assert b"user edited" not in ep_path.read_bytes()      # force 才重写


def test_write_for_show_artwork_gate(scraped_show, tmp_path, monkeypatch):
    lib, root, show = scraped_show
    posters = tmp_path / "posters"
    posters.mkdir()
    for name in ("tv_100.jpg", "tv_backdrop_100.jpg", "tv_100_s1.jpg"):
        (posters / name).write_bytes(b"img-" + name.encode())
    monkeypatch.setattr(artwork, "POSTER_DIR", str(posters))
    # 默认 nfo 模式：只写 NFO 不写海报
    r = artwork.write_for_show(show["id"])
    assert r == {"ok": False, "reason": "disabled"}
    store.update_library(lib["id"], artwork_mode="nfo_art")
    library_paths.invalidate_cache()
    r = artwork.write_for_show(show["id"])
    assert r["ok"], r
    show_dir = root / "Test Show (2020)"
    assert (show_dir / "poster.jpg").read_bytes() == b"img-tv_100.jpg"
    assert (show_dir / "fanart.jpg").read_bytes() == b"img-tv_backdrop_100.jpg"
    assert (show_dir / "Season 01" / "season01-poster.jpg").is_file()


def test_rebuild_tv_nfo_job(scraped_show):
    lib, root, show = scraped_show
    # 刮削已写过一轮 NFO；删掉两个验证重建任务确实重写（其余同内容记 skipped）
    (root / "Test Show (2020)/tvshow.nfo").unlink()
    (root / "Test Show (2020)/Season 01/Test.Show.S01E01.nfo").unlink()
    r = client.post("/api/jobs/rebuild-tv-nfo", json={"ids": [show["id"]]})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    state = ""
    for _ in range(50):
        j = client.get(f"/api/jobs/rebuild-tv-nfo/{jid}").json()
        state = j.get("state")
        if state in ("done", "failed", "cancelled"):
            break
        time.sleep(0.1)
    assert state == "done", j
    totals = j["summary"]["totals"]
    assert totals["nfo_wrote"] >= 2, j["summary"]
    assert totals["nfo_wrote"] + totals["nfo_skipped"] >= 4
    assert (root / "Test Show (2020)/tvshow.nfo").is_file()


def test_wrapper_dir_show_root(tv_lib, monkeypatch):
    """发布包装目录（Show/<release>/Season NN/）下剧根仍是顶层目录；误放的 tvshow.nfo 清理。"""
    lib, root = tv_lib
    _touch(root, "Wrapped Show (2021)/Wrapped.Show.1080p/Season 01/Wrapped.S01E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    monkeypatch.setattr("app.tmdb.tv_detail", lambda tid: _detail(300))
    monkeypatch.setattr("app.tmdb.tv_season", lambda tid, sn: _season())
    monkeypatch.setattr("app.tmdb.search_tv", lambda q, year=None: [
        {"id": 300, "name": "测试剧", "original_name": "Wrapped Show",
         "first_air_date": "2021-01-01", "popularity": 9.0, "vote_count": 50}])
    monkeypatch.setattr("app.tmdb.download_image",
                        lambda path, dest, size="w500": True)
    tv_persist.scrape_pending(library_ids=[lib["id"]], force=True)
    show = store.list_shows(lib["id"])[0]
    backend = storage.backend_for(lib["id"])
    r = tv_nfo_link.sync_tv_nfos_for(show["id"], backend=backend)
    top = root / "Wrapped Show (2021)"
    wrapper = top / "Wrapped.Show.1080p"
    assert (top / "tvshow.nfo").is_file(), r
    assert (wrapper / "Season 01" / "season.nfo").is_file()
    assert not (wrapper / "tvshow.nfo").exists()
    # 模拟旧行为误放：内容与所有权哈希一致 → 下次同步清理
    (wrapper / "tvshow.nfo").write_bytes((top / "tvshow.nfo").read_bytes())
    r = tv_nfo_link.sync_tv_nfos_for(show["id"], backend=backend)
    assert "Wrapped Show (2021)/Wrapped.Show.1080p/tvshow.nfo" in r["deleted"], r
    assert not (wrapper / "tvshow.nfo").exists()


def test_fs_list_tv_episode_jump(scraped_show):
    """fs 浏览器剧库内正片带 show_id（双击进剧详情，T3）。"""
    lib, root, show = scraped_show
    r = client.get("/api/fs/list", params={"library": lib["id"],
                                           "path": "Test Show (2020)/Season 01"}).json()
    ep = next(f for f in r["files"] if f["name"].endswith("S01E01.mkv"))
    assert ep["kind"] == "feature" and ep["show_id"] == show["id"]
    assert ep["movie_id"] is None and ep["season"] == 1 and ep["episode"] == 1


def test_wrapper_stale_art_cleanup(scraped_show, tmp_path, monkeypatch):
    """包装层里旧 show_dir_of 误放的 poster/fanart（内容一致）会被清理（否则无法删空目录）。"""
    lib, root, show = scraped_show
    posters = tmp_path / "posters"
    posters.mkdir()
    (posters / "tv_100.jpg").write_bytes(b"poster-bytes")
    (posters / "tv_backdrop_100.jpg").write_bytes(b"backdrop-bytes")
    monkeypatch.setattr(artwork, "POSTER_DIR", str(posters))
    store.update_library(lib["id"], artwork_mode="nfo_art")
    library_paths.invalidate_cache()
    wrapper = root / "Test Show (2020)/Release.Wrapper"
    wrapper.mkdir(parents=True, exist_ok=True)
    (wrapper / "poster.jpg").write_bytes(b"poster-bytes")
    (wrapper / "fanart.jpg").write_bytes(b"backdrop-bytes")
    r = artwork.write_for_show(show["id"])
    assert r["ok"], r
    assert not (wrapper / "poster.jpg").exists()
    assert not (wrapper / "fanart.jpg").exists()


def test_plexmatch_opt_in(scraped_show, monkeypatch):
    lib, root, show = scraped_show
    backend = storage.backend_for(lib["id"])
    assert tv_nfo_link.write_plexmatch(show["id"], backend=backend)["ok"] is True
    pm = root / "Test Show (2020)/.plexmatch"
    assert pm.is_file()
    text = pm.read_text(encoding="utf-8")
    assert "title: 测试剧" in text and "tmdb: 100" in text and "tvdb: 42" in text
