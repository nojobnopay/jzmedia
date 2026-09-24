"""TV 单剧整理预览 + 匹配状态透出（详情页匹配后直接整理入口）。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvlib"
    root.mkdir()
    lib = store.create_library(name=f"tvhint-{tmp_path.name}", kind="tv",
                               path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def _detail(tmdb_id=700, name="蜡笔小新"):
    return {
        "id": tmdb_id, "name": name, "original_name": "Crayon Shin-chan",
        "first_air_date": "1992-04-13", "last_air_date": "2020-01-01",
        "overview": "野原新之助的日常", "tagline": "", "status": "Continuing",
        "vote_average": 7.5, "genres": [{"id": 16, "name": "动画"}],
        "origin_country": ["JP"], "original_language": "ja",
        "number_of_seasons": 1, "number_of_episodes": 2,
        "episode_run_time": [24], "networks": [{"name": "TV Asahi"}],
        "production_companies": [],
        "created_by": [{"id": 1, "name": "臼井仪人", "profile_path": None}],
        "poster_path": "/p.jpg", "backdrop_path": "/b.jpg",
        "external_ids": {"imdb_id": "tt0112694", "tvdb_id": 76885},
        "credits": {"cast": [], "crew": []},
        "seasons": [{"id": 11, "season_number": 1, "episode_count": 2,
                     "name": "第 1 季", "overview": "", "air_date": "1992-04-13",
                     "poster_path": "/s1.jpg"}],
    }


def _season(n=1, eps=2):
    return {"episodes": [
        {"id": 7000 + n * 10 + i, "episode_number": i,
         "name": f"第{i}话", "overview": f"简介{i}",
         "still_path": f"/st{i}.jpg", "air_date": "1992-04-13",
         "runtime": 24, "vote_average": 7.5}
        for i in range(1, eps + 1)]}


@pytest.fixture()
def fake_tmdb(monkeypatch):
    def download_image(path, dest, size="w500"):
        with open(dest, "wb") as fh:
            fh.write(b"img")
        return True

    monkeypatch.setattr("app.tmdb.tv_detail", lambda tid: _detail(int(tid)))
    monkeypatch.setattr("app.tmdb.tv_season",
                        lambda tid, sn: _season(int(sn), 2))
    monkeypatch.setattr("app.tmdb.tv_aggregate_credits", lambda tid: None)
    monkeypatch.setattr("app.tmdb.download_image", download_image)


def test_hint_flat_show_needs_season(tv_lib):
    lib, root = tv_lib
    _touch(root, "Crayon Shin-chan/蜡笔小新.S01E01.mkv")
    _touch(root, "Crayon Shin-chan/蜡笔小新.S01E02.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    r = client.get(f"/api/tv/shows/{show['id']}/organize-hint")
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["needs"] is True and h["reason"] == "ok"
    assert h["matched"] is False  # 未匹配也照样给出目录动作
    assert any(g["action"] == "season" for g in h["groups"])
    assert h["params"]["ids"] == [show["id"]]
    assert "rename" in h["params"]["actions"]
    # 最终分布（含无需移动的正片）：2 集都落到 Season 01
    assert sum(r["count"] for r in h["dir_totals"]) == 2
    assert any(r["dir"].endswith("/Season 01") for r in h["dir_totals"])


def test_hint_404():
    r = client.get("/api/tv/shows/999999999/organize-hint")
    assert r.status_code == 404


def test_hint_absolute_risk_gating(tv_lib):
    lib, root = tv_lib
    _touch(root, "Anime/001.mkv")
    _touch(root, "Anime/002.mkv")
    _touch(root, "Anime/003.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    store.update_show_meta(show["id"], title="测试番", year=2020, tmdb_id=701)
    for e in store.list_episodes(show["id"]):
        store.update_episode_meta(e["id"], tmdb_episode_id=800000 + int(e["id"]),
                                  title=f"话{int(e['episode'])}")
    base = f"/api/tv/shows/{show['id']}/organize-hint?actions=rename"
    h0 = client.get(base).json()
    assert h0["absolute_risk"] is True and h0["needs"] is False
    assert any(m["reason"] == "absolute" for m in h0["manual"])
    h1 = client.get(base + "&allow_absolute=true").json()
    assert h1["needs"] is True and h1["reason"] == "ok"
    assert h1["params"]["allow_absolute_shows"] == [show["id"]]


def test_match_returns_show_and_media(tv_lib, fake_tmdb):
    lib, root = tv_lib
    _touch(root, "Whatever/Season 01/Whatever.S01E01.mkv")
    _touch(root, "Whatever/Season 01/Whatever.S01E02.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    r = client.post(f"/api/tv/shows/{show['id']}/match", json={"tmdb_id": 700})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["offline"] is False
    assert d["episodes_matched"] == 2
    # 剧快照：标题/简介/海报一次回包，前端免二次 reload 即可刷新
    assert d["show"]["tmdb_id"] == 700 and d["show"]["title"] == "蜡笔小新"
    assert d["show"]["poster_path"] == "tv/700.jpg"
    assert d["show"]["has_overview"] is True
    # 小剧同步落盘：media 直接给出结果而非 queued
    assert d["media"].get("queued") is not True
    assert "artwork" in d["media"]


def test_match_offline_fallback(tv_lib, fake_tmdb, monkeypatch):
    lib, root = tv_lib
    _touch(root, "Whatever/Season 01/Whatever.S01E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    r = client.post(f"/api/tv/shows/{show['id']}/match", json={"tmdb_id": 700})
    assert r.status_code == 200
    # TMDB 断网：有缓存走离线绑定，无缓存 502
    monkeypatch.setattr("app.tmdb.tv_detail", lambda tid: (_ for _ in ()).throw(
        RuntimeError("network down")))
    r = client.post(f"/api/tv/shows/{show['id']}/match", json={"tmdb_id": 700})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["offline"] is True and d["show"]["title"] == "蜡笔小新"
    assert d["show"]["tmdb_id"] == 700
    r = client.post(f"/api/tv/shows/{show['id']}/match", json={"tmdb_id": 701})
    assert r.status_code == 502


def test_match_tmdb_id_validation(tv_lib):
    lib, root = tv_lib
    _touch(root, "Whatever/Season 01/Whatever.S01E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    assert client.post(f"/api/tv/shows/{show['id']}/match",
                       json={"tmdb_id": 0}).status_code == 422
    assert client.post(f"/api/tv/shows/{show['id']}/match",
                       json={"tmdb_id": -5}).status_code == 422


def test_match_reports_failed_seasons(tv_lib, fake_tmdb, monkeypatch):
    """单季拉取失败必须在响应里可见（不静默丢一季的集号回填）。"""
    lib, root = tv_lib
    _touch(root, "Whatever/Season 01/Whatever.S01E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]

    def boom(tid, sn):
        raise RuntimeError("season fetch down")

    monkeypatch.setattr("app.tmdb.tv_season", boom)
    r = client.post(f"/api/tv/shows/{show['id']}/match", json={"tmdb_id": 700})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["seasons_failed"] == [1]
    assert d["episodes_matched"] == 0
    assert d["show"]["title"] == "蜡笔小新"      # 换绑仍完成（标题/海报照常）
