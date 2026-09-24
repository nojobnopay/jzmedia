"""T2：TV 刮削/匹配/绝对集号映射/已看/继续观看/集版本（TMDB 打桩）。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store
from app.main import app
from app.scanner import tv_match, tv_persist

client = TestClient(app)


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvlib"
    root.mkdir()
    lib = store.create_library(name=f"tv3-{tmp_path.name}", kind="tv", path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def _detail(tmdb_id=100, seasons=None, name="测试剧"):
    return {
        "id": tmdb_id, "name": name, "original_name": "Test Show",
        "first_air_date": "2020-01-01", "last_air_date": "2020-02-01",
        "overview": "剧情简介", "tagline": "", "status": "Ended",
        "vote_average": 8.0, "genres": [{"id": 18, "name": "剧情"}],
        "origin_country": ["CN"], "original_language": "zh",
        "number_of_seasons": len(seasons or [1]), "number_of_episodes": 2,
        "episode_run_time": [45], "networks": [{"name": "CCTV"}],
        "production_companies": [],
        "created_by": [{"id": 7, "name": "编剧甲", "profile_path": None}],
        "poster_path": "/p.jpg", "backdrop_path": "/b.jpg",
        "external_ids": {"imdb_id": "tt100", "tvdb_id": 42},
        "credits": {"cast": [{"id": 9, "name": "演员甲", "character": "甲",
                              "profile_path": None}], "crew": []},
        "seasons": seasons or [{"id": 1, "season_number": 1, "episode_count": 2,
                                "name": "第 1 季", "overview": "", "air_date": "2020-01-01",
                                "poster_path": "/s1.jpg"}],
    }


def _season(n=1, eps=2):
    return {"episodes": [
        {"id": 1000 + n * 10 + i, "episode_number": i, "name": f"第{n}季第{i}集",
         "overview": f"简介{i}", "still_path": f"/st{n}{i}.jpg",
         "air_date": f"2020-0{n}-0{i}", "runtime": 45, "vote_average": 8.0}
        for i in range(1, eps + 1)]}


@pytest.fixture()
def fake_tmdb(monkeypatch):
    calls = {"detail": 0, "season": 0, "search": 0, "download": 0}
    detail = _detail()
    seasons = {1: _season(1, 2)}

    def search_tv(q, year=None):
        calls["search"] += 1
        return [{"id": 100, "name": "测试剧", "original_name": "Test Show",
                 "first_air_date": "2020-01-01", "overview": "", "poster_path": "/p.jpg"}]

    def tv_detail(tmdb_id):
        calls["detail"] += 1
        return detail

    def tv_season(tmdb_id, sn):
        calls["season"] += 1
        return seasons[int(sn)]

    def download_image(path, dest, size="w500"):
        calls["download"] += 1
        with open(dest, "wb") as fh:
            fh.write(b"img")
        return True

    monkeypatch.setattr("app.tmdb.search_tv", search_tv)
    monkeypatch.setattr("app.tmdb.tv_detail", tv_detail)
    monkeypatch.setattr("app.tmdb.tv_season", tv_season)
    monkeypatch.setattr("app.tmdb.download_image", download_image)
    return calls


def test_scrape_show_and_api(tv_lib, fake_tmdb):
    lib, root = tv_lib
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E01.mkv")
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E02.mkv")
    scanner.scan_all(library_id=lib["id"])
    res = tv_persist.scrape_pending(library_ids=[lib["id"]])
    assert [r["status"] for r in res] == ["ok"]
    assert res[0]["episodes_matched"] == 2
    show = store.list_shows(lib["id"])[0]
    assert show["tmdb_id"] == 100 and show["title"] == "测试剧"
    assert show["status"] == "Ended" and show["poster_path"] == "tv/100.jpg"
    assert show["number_of_seasons"] == 1 and show["episode_run_time"] == 45
    # 同标题在别的用例已刮过时走本地索引（library）——两者都算正确绑定
    assert show["match_source"] in ("tmdb", "library") and show["needs_review"] == 0
    eps = store.get_show(show["id"])["episodes"]
    assert [(e["season"], e["episode"], e["title"], e["tmdb_episode_id"]) for e in eps] == \
        [(1, 1, "第1季第1集", 1011), (1, 2, "第1季第2集", 1012)]
    assert eps[0]["still_path"] == "/st11.jpg" and eps[0]["air_date"] == "2020-01-01"
    # cache 走 tv 命名空间
    cached = store.get_tmdb_cached(100, "tv")
    assert cached and cached["media_type"] == "tv" and cached["title"] == "测试剧"
    # 季元数据（TMDB 集数供绝对号映射）
    assert store.season_offsets(show["id"]) == [(1, 2)]
    # API：详情带季/断点/下一集
    d = client.get(f"/api/tv/shows/{show['id']}").json()
    assert d["seasons"][0]["name"] == "第 1 季"
    assert d["next_episode"]["episode"] == 1
    assert d["watched_count"] == 0


def test_absolute_remap_on_scrape(tv_lib, fake_tmdb, monkeypatch):
    lib, root = tv_lib
    _touch(root, "Anime/001.mkv")
    _touch(root, "Anime/002.mkv")
    _touch(root, "Anime/003.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    # 假剧分两季：S1 两集 + S2 一集
    detail = _detail(200, seasons=[
        {"id": 1, "season_number": 1, "episode_count": 2, "name": "S1",
         "overview": "", "air_date": "", "poster_path": ""},
        {"id": 2, "season_number": 2, "episode_count": 1, "name": "S2",
         "overview": "", "air_date": "", "poster_path": ""}])
    monkeypatch.setattr("app.tmdb.tv_detail", lambda tid: detail)
    monkeypatch.setattr("app.tmdb.tv_season",
                        lambda tid, sn: _season(int(sn), 2 if int(sn) == 1 else 1))
    monkeypatch.setattr("app.tmdb.search_tv",
                        lambda q, year=None: [{"id": 200, "name": "测试剧",
                                               "original_name": "Test Show",
                                               "first_air_date": "2020-01-01"}])
    res = tv_persist.scrape_pending(library_ids=[lib["id"]])
    assert res[0]["remapped"] == 1   # 003 → S2E1（001/002 本来就对）
    eps = sorted(store.get_show(show["id"])["episodes"],
                 key=lambda e: (e["season"], e["episode"]))
    assert [(e["season"], e["episode"], e["absolute_number"]) for e in eps] == \
        [(1, 1, 1), (1, 2, 2), (2, 1, 3)]
    assert eps[2]["title"] == "第2季第1集"


def test_manual_match_and_search(tv_lib, fake_tmdb):
    lib, root = tv_lib
    _touch(root, "Whatever/Season 01/Whatever.S01E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    assert show["tmdb_id"] is None
    r = client.get("/api/tv/search?q=测试")
    assert r.status_code == 200 and r.json()["items"][0]["tmdb_id"] == 100
    r = client.post(f"/api/tv/shows/{show['id']}/match", json={"tmdb_id": 100})
    assert r.status_code == 200, r.text
    show = store.get_show_meta(show["id"])
    assert show["tmdb_id"] == 100 and show["match_source"] == "manual"
    assert show["needs_review"] == 0
    assert store.get_episode(store.get_show(show["id"])["episodes"][0]["id"])["title"] \
        == "第1季第1集"


def test_manual_match_forces_title_after_rebind(tv_lib, fake_tmdb, monkeypatch):
    """显式换绑必须跟新条目标题（title_auto=0 只保护真正的手工标题）。"""
    lib, root = tv_lib
    _touch(root, "Whatever/Season 01/Whatever.S01E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    tv_persist.scrape_pending(library_ids=[lib["id"]])
    show = store.list_shows(lib["id"])[0]
    assert show["title"] == "测试剧"
    monkeypatch.setattr("app.tmdb.tv_detail", lambda tid: _detail(name="改名后的剧"))
    r = client.post(f"/api/tv/shows/{show['id']}/match", json={"tmdb_id": 100})
    assert r.status_code == 200, r.text
    assert store.get_show_meta(show["id"])["title"] == "改名后的剧"


def test_watched_progress_and_recent(tv_lib, fake_tmdb):
    lib, root = tv_lib
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E01.mkv")
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E02.mkv")
    scanner.scan_all(library_id=lib["id"])
    tv_persist.scrape_pending(library_ids=[lib["id"]])
    show = store.list_shows(lib["id"])[0]
    eps = store.get_show(show["id"])["episodes"]
    e1 = eps[0]["id"]
    # 断点 → 继续观看
    client.post(f"/api/stream/progress?version_id={e1}&kind=episode",
                json={"position": 100, "duration": 1000})
    items = client.get("/api/tv/recent-played").json()["items"]
    assert items and items[0]["progress"]["version_id"] == e1
    assert items[0]["subtitle"].startswith("S01E01")
    # 下一集 + 连播接口
    assert client.get(f"/api/tv/episodes/{e1}/next").json()["next"]["episode"] == 2
    # 标已看 → 清断点、离开继续观看；next_episode 推进
    r = client.post(f"/api/tv/episodes/{e1}/watched", json={"watched": True})
    assert r.status_code == 200
    assert store.get_progress(e1, "episode") is None
    assert client.get("/api/tv/recent-played").json()["items"] == []
    assert client.get(f"/api/tv/shows/{show['id']}").json()["next_episode"]["episode"] == 2
    # 整剧已看/未看
    r = client.post(f"/api/tv/shows/{show['id']}/watched", json={"watched": True})
    assert r.json()["episodes"] == 2
    assert client.get(f"/api/tv/shows/{show['id']}").json()["next_episode"] is None
    client.post(f"/api/tv/shows/{show['id']}/watched", json={"watched": False})
    assert client.get(f"/api/tv/shows/{show['id']}").json()["next_episode"]["episode"] == 1


def test_episode_versions(tv_lib, fake_tmdb):
    lib, root = tv_lib
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E01.1080p.mkv")
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E01.2160p.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    eps = store.get_show(show["id"])["episodes"]
    assert len(eps) == 2   # 多版本同集
    d = client.get(f"/api/stream/versions?movie_id={eps[0]['id']}&kind=episode").json()
    assert d["kind"] == "episode" and len(d["versions"]) == 2
    assert d["versions"][0]["season"] == 1


def test_manual_title_protected_on_rescrape(tv_lib, fake_tmdb):
    lib, root = tv_lib
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    tv_persist.scrape_pending(library_ids=[lib["id"]])
    show = store.list_shows(lib["id"])[0]
    assert show["title"] == "测试剧" and show["title_auto"] == 0
    r = client.patch(f"/api/tv/shows/{show['id']}", json={"title": "我的剧名"})
    assert r.status_code == 200, r.text
    assert store.get_show_meta(show["id"])["title"] == "我的剧名"
    tv_persist.scrape_pending(ids=[show["id"]], force=True)
    assert store.get_show_meta(show["id"])["title"] == "我的剧名"   # 手工标题不被覆盖
    assert client.patch(f"/api/tv/shows/{show['id']}",
                        json={"title": "  "}).status_code == 422
    assert client.patch(f"/api/tv/shows/{show['id']}",
                        json={"custom_rating": 99}).status_code == 422


def test_confirm_local_episode_survives_rescrape(tv_lib, fake_tmdb):
    """TMDB 无对应集：confirm-local 清 needs_review 并标 local_only，重刮不覆盖。"""
    lib, root = tv_lib
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E01.mkv")
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E02.mkv")
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E03.mkv")   # TMDB 只有 2 集
    scanner.scan_all(library_id=lib["id"])
    tv_persist.scrape_pending(library_ids=[lib["id"]])
    show = store.list_shows(lib["id"])[0]
    eps = sorted(store.get_show(show["id"])["episodes"], key=lambda e: e["episode"])
    ep3 = eps[2]
    assert ep3["needs_review"] == 1 and not ep3["tmdb_episode_id"]

    r = client.post(f"/api/tv/episodes/{ep3['id']}/confirm-local",
                    json={"title": "本地特典集"})
    assert r.status_code == 200, r.text
    assert r.json()["local_only"] == 1
    # 重刮（force）：不重标 needs_review、标题保留、不绑 TMDB 集
    tv_persist.scrape_pending(ids=[show["id"]], force=True)
    ep3b = store.get_episode(ep3["id"])
    assert ep3b["needs_review"] == 0 and ep3b["local_only"] == 1
    assert ep3b["title"] == "本地特典集" and not ep3b["tmdb_episode_id"]
    # 季接口带 local_only（前端「本地集」徽标；剧详情已瘦身不再带全量集）
    d = client.get(f"/api/tv/shows/{show['id']}/seasons/1").json()
    assert any(e.get("local_only") for e in d["episodes"])
    # 重新绑定 TMDB 集 → 取消 local_only
    client.post(f"/api/tv/episodes/{ep3['id']}/match-episode",
                json={"tmdb_episode_id": 1011, "season": 1})
    assert store.get_episode(ep3["id"])["local_only"] == 0


def test_confirm_local_clears_stale_tmdb_binding(tv_lib, fake_tmdb):
    """confirm-local 同时清除此前错误的 TMDB 绑定（Disc49 类：本地集含 TMDB 未收录片段）。"""
    lib, root = tv_lib
    _touch(root, "Test Show (2020)/Season 01/Test.Show.S01E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    tv_persist.scrape_pending(library_ids=[lib["id"]])
    show = store.list_shows(lib["id"])[0]
    ep = store.get_show(show["id"])["episodes"][0]
    assert ep["tmdb_episode_id"]
    r = client.post(f"/api/tv/episodes/{ep['id']}/confirm-local",
                    json={"title": "本地集"})
    assert r.status_code == 200, r.text
    ep2 = store.get_episode(ep["id"])
    assert ep2["local_only"] == 1 and ep2["needs_review"] == 0
    assert ep2["tmdb_episode_id"] is None and ep2["title"] == "本地集"
    tv_persist.scrape_pending(ids=[show["id"]], force=True)
    ep3 = store.get_episode(ep["id"])
    assert ep3["local_only"] == 1 and ep3["tmdb_episode_id"] is None


def test_cross_season_fallback_guard(tv_lib, fake_tmdb, monkeypatch):
    """跨季回退守卫：本地 (季,集) 超出 TMDB 该季集数时不静默跨季绑定（AoT S01E26→S04E26 教训）。

    假剧 S1=E1-E2、S2=E3-E4（TMDB 式跨季连续编号，越狱兔类）：
    - S01E03（显式季号、超 S1 集数）→ 不绑 S02E03，标 needs_review；
    - S02E01（同季合理 fits）→ 仍按①回退绑 S02E03，不受守卫影响。
    """
    lib, root = tv_lib
    for f in ("S01E01", "S01E02", "S01E03", "S02E01", "S02E02"):
        _touch(root, f"Test Show (2020)/Season {f[1:3]}/Test.Show.{f}.mkv")
    scanner.scan_all(library_id=lib["id"])

    def season(sn):
        sn = int(sn)
        base = 2000 + sn * 10
        # S1: E1-E2；S2: E3-E4（跨季连续编号）
        nums = (1, 2) if sn == 1 else (3, 4)
        return {"episodes": [
            {"id": base + n, "episode_number": n, "name": f"S{sn}E{n}",
             "overview": "", "still_path": "", "air_date": "2020-01-01",
             "runtime": 45, "vote_average": 8.0} for n in nums]}

    detail = _detail(300, seasons=[
        {"id": 1, "season_number": 1, "episode_count": 2, "name": "S1",
         "overview": "", "air_date": "", "poster_path": ""},
        {"id": 2, "season_number": 2, "episode_count": 2, "name": "S2",
         "overview": "", "air_date": "", "poster_path": ""}])
    monkeypatch.setattr("app.tmdb.tv_detail", lambda tid: detail)
    monkeypatch.setattr("app.tmdb.tv_season", lambda tid, sn: season(sn))
    monkeypatch.setattr("app.tmdb.search_tv",
                        lambda q, year=None: [{"id": 300, "name": "测试剧",
                                               "original_name": "Test Show",
                                               "first_air_date": "2020-01-01"}])
    res = tv_persist.scrape_pending(library_ids=[lib["id"]])
    assert res[0]["status"] == "ok"
    eps = {(e["season"], e["episode"]): e
           for e in store.get_show(store.list_shows(lib["id"])[0]["id"])["episodes"]}
    # 精确命中不受影响
    assert eps[(1, 1)]["tmdb_episode_id"] == 2011
    assert eps[(1, 2)]["tmdb_episode_id"] == 2012
    # S01E03：旧逻辑会静默绑 S02E03（abs 唯一集号 3）；守卫后标待确认
    assert eps[(1, 3)]["tmdb_episode_id"] is None
    assert eps[(1, 3)]["needs_review"] == 1
    # S02E01：fits → ①回退仍生效（cand=2+1=3 → S02E03，未被 S01E03 占用）
    assert eps[(2, 1)]["tmdb_episode_id"] == 2023
    assert eps[(2, 2)]["tmdb_episode_id"] == 2024


def test_match_quality_gate(monkeypatch):
    """不像的候选不绑定（电影同门）。"""
    monkeypatch.setattr("app.tmdb.search_tv", lambda q, year=None: [
        {"id": 5, "name": "完全不相关的剧", "original_name": "Unrelated",
         "first_air_date": "2020-01-01"}])
    monkeypatch.setattr("app.tmdb.tv_alternative_titles", lambda tid: [])
    res = tv_match.resolve_show("龙珠Z", 1989, {})
    assert res["tmdb_id"] is None
    # 相似 + 年份符 → 直接采信
    monkeypatch.setattr("app.tmdb.search_tv", lambda q, year=None: [
        {"id": 6, "name": "龙珠Z", "original_name": "Dragon Ball Z",
         "first_air_date": "1989-04-26"}])
    res = tv_match.resolve_show("龙珠Z", 1989, {})
    assert res["tmdb_id"] == 6 and res["needs_review"] is False
    # 别名兜底：本地化标题 vs 英文目录名
    monkeypatch.setattr("app.tmdb.search_tv", lambda q, year=None: [
        {"id": 7, "name": "胜者即是正义", "original_name": "リーガル・ハイ",
         "first_air_date": "2012-04-17"}])
    monkeypatch.setattr("app.tmdb.tv_alternative_titles",
                        lambda tid: [{"iso_3166_1": "US", "title": "Legal High"}])
    res = tv_match.resolve_show("Legal High", 2012, {})
    assert res["tmdb_id"] == 7 and res["needs_review"] is True
