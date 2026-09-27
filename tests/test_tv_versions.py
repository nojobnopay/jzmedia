"""剧集多版本（V1/V2）：版本解析、连播/下一集按版本隔离、API version 字段。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvlib"
    root.mkdir()
    lib = store.create_library(name=f"tvver-{tmp_path.name}", kind="tv", path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def test_episode_version_parse():
    assert store.episode_version("Show/Season 01/剧名-V2-S01E01-事故.mp4") == 2
    assert store.episode_version("Show/Season 01/剧名-S01E01-事故-V2.mp4") == 2
    assert store.episode_version("Show/Season 01/剧名-S01E01-事故.mkv") == 1
    assert store.episode_version("Show/Season 01/剧名-V10-S01E01-x.mkv") == 10
    # 目录里的 -V2- 不参与（只看文件名）
    assert store.episode_version("Show-V9/Season 01/S01E01.mkv") == 1
    assert store.episode_version("") == 1


def _make_show(lib_id):
    show_id = store.upsert_show(lib_id, "测试剧", 2020)
    paths = [
        ("测试剧-S01E01-一.mkv", 1, 1),
        ("测试剧-V2-S01E01-一.mp4", 1, 1),
        ("测试剧-S01E02-二.mkv", 1, 2),
        ("测试剧-V2-S01E02-二.mp4", 1, 2),
    ]
    eps = {}
    for path, season, episode in paths:
        eid = store.upsert_episode(show_id, lib_id, path, season, episode,
                                   f"第{episode}集")
        eps[path] = eid
    return show_id, eps


def test_episode_after_same_version(tv_lib):
    lib, _root = tv_lib
    show_id, eps = _make_show(lib["id"])
    n1 = store.episode_after(eps["测试剧-S01E01-一.mkv"])
    assert n1["file_path"] == "测试剧-S01E02-二.mkv"
    n2 = store.episode_after(eps["测试剧-V2-S01E01-一.mp4"])
    assert n2["file_path"] == "测试剧-V2-S01E02-二.mp4"
    # 各版本最后一集：不跨版本
    assert store.episode_after(eps["测试剧-S01E02-二.mkv"]) is None
    assert store.episode_after(eps["测试剧-V2-S01E02-二.mp4"]) is None


def test_next_episode_prefers_same_version(tv_lib):
    lib, _root = tv_lib
    show_id, eps = _make_show(lib["id"])
    store.mark_episode_watched(eps["测试剧-S01E01-一.mkv"], True)
    nxt = store.next_episode(show_id)
    assert nxt["file_path"] == "测试剧-S01E02-二.mkv"      # 不跳 V2E01
    store.mark_episode_watched(eps["测试剧-S01E02-二.mkv"], True)
    nxt = store.next_episode(show_id)
    assert nxt["file_path"] == "测试剧-V2-S01E01-一.mp4"   # V1 看完 → 回退到 V2 首条


def test_show_detail_exposes_version(tv_lib):
    lib, root = tv_lib
    for rel in ("测试剧.2020/Season 01/测试剧-S01E01-一.mkv",
                "测试剧.2020/Season 01/测试剧-V2-S01E01-一.mp4",
                "测试剧.2020/Season 01/测试剧-V2-S01E01-一.nfo"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    d = client.get(f"/api/tv/shows/{show['id']}", params={"include_episodes": 1}).json()
    vers = sorted(e["version"] for e in d["episodes"])
    assert vers == [1, 2]
    nxt = client.get(f"/api/tv/episodes/"
                     f"{next(e['id'] for e in d['episodes'] if e['version'] == 1)}/next")
    assert nxt.status_code == 200


def test_navigation_ranges_versions_and_pagination(tv_lib):
    lib, _ = tv_lib
    lid = lib['id']
    sid = store.upsert_show(lid, '长剧')
    first = store.upsert_episode(sid, lid, '长剧-S01E01-E03.mkv', 1, 1, episode_end=3)
    store.upsert_episode(sid, lid, '长剧-S01E02.mkv', 1, 2)
    v2 = store.upsert_episode(sid, lid, '长剧-V2-S01E01.mkv', 1, 1)
    tail = [store.upsert_episode(sid, lid, f'长剧-S01E{i:03d}.mkv', 1, i) for i in range(4, 507)]
    cross = store.upsert_episode(sid, lid, '长剧-S02E01.mkv', 2, 1)
    assert store.episode_after(first)['id'] == tail[0]
    assert store.episode_neighbors(tail[0])[0]['id'] == first
    assert store.episode_after(tail[-1])['id'] == cross
    detail = client.get(f'/api/tv/episodes/{tail[-2]}').json()
    assert detail['next_episode']['id'] == tail[-1]
    assert detail['previous_episode']['id'] == tail[-3]
    assert client.get(f'/api/tv/episodes/{tail[-2]}/next').json()['next']['id'] == tail[-1]
    store.mark_episode_watched(first, True)
    assert store.next_episode(sid)['id'] == tail[0]
    assert store.season_next_episode(sid, 1)['id'] == tail[0]
    page = client.get(f'/api/tv/shows/{sid}/seasons/1', params={'version': 2}).json()
    assert page['total'] == 1 and page['episodes'][0]['id'] == v2
    assert page['distinct_count'] == 506
    assert store.show_season_stats(sid)[0]['distinct'] == 506
