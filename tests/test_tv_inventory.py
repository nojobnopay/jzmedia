"""F：TV 只读清单（扫描入库/幂等/未知集跳过/API 浏览）。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def tv_lib(tmp_path):
    root = tmp_path / "tvlib"
    root.mkdir()
    lib = store.create_library(name=f"tv-{tmp_path.name}", kind="tv",
                              path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_tv_scan_inventory_and_idempotent(tv_lib):
    lib, root = tv_lib
    _touch(root, "Show (2019)/Season 01/Show.S01E01.mkv")
    _touch(root, "Show (2019)/Show.S01E02.mkv")
    _touch(root, "random.mkv")
    res = scanner.scan_all(library_id=lib["id"])
    statuses = [r["status"] for r in res]
    assert statuses.count("tv_ok") == 2
    assert "skipped_tv_unknown" in statuses

    shows = store.list_shows(lib["id"])
    assert len(shows) == 1
    assert shows[0]["title"] == "Show" and shows[0]["year"] == 2019
    assert shows[0]["episode_count"] == 2 and shows[0]["season_count"] == 1

    # 重扫幂等：不重复建剧/集
    scanner.scan_all(library_id=lib["id"])
    assert store.count_shows(lib["id"]) == 1
    assert store.count_episodes(lib["id"]) == 2


def test_tv_api_browse(tv_lib):
    lib, root = tv_lib
    _touch(root, "Another (2020)/Season 02/Another.S02E05.mkv")
    scanner.scan_all(library_id=lib["id"])
    shows = client.get(f"/api/tv/shows?library={lib['id']}").json()
    assert shows["total"] == 1
    show = shows["items"][0]
    detail = client.get(f"/api/tv/shows/{show['id']}").json()
    assert detail["title"] == "Another"
    assert detail["seasons"][0]["season"] == 2
    assert detail["seasons"][0]["episode_count"] == 1
    ep = detail["episodes"][0]
    assert ep["season"] == 2 and ep["episode"] == 5 and ep["exists"] is True
    one = client.get(f"/api/tv/episodes/{ep['id']}").json()
    assert one["show_title"] == "Another"
    stats = client.get(f"/api/tv/stats?library={lib['id']}").json()
    assert stats == {"shows": 1, "episodes": 1}
    assert client.get("/api/tv/shows/999999").status_code == 404


def test_tv_playback_kind_isolation(tv_lib, monkeypatch):
    lib, root = tv_lib
    _touch(root, "Kind (2021)/Season 01/Kind.S01E01.mkv")
    scanner.scan_all(library_id=lib["id"])
    show = store.list_shows(lib["id"])[0]
    ep = store.get_show(show["id"])["episodes"][0]
    eid = ep["id"]

    # 断点按 (kind,id) 隔离
    r = client.post(f"/api/stream/progress?version_id={eid}&kind=episode",
                    json={"position": 5, "duration": 10})
    assert r.status_code == 200, r.text
    got = client.get(f"/api/stream/progress?version_id={eid}&kind=episode").json()
    assert got["position"] == 5

    # 媒体信息（探测打桩）：kind=episode 命中剧集行
    monkeypatch.setattr("app.media.probe", lambda p, size=None, timeout=30: {
        "playable": True, "duration": 10.0, "audio": [], "subs": [],
        "attachments": [], "container": "mkv", "probe_ver": 99, "probed_at": 1})
    d = client.get(f"/api/stream/{eid}/media?kind=episode").json()
    assert d["kind"] == "episode" and d["title"] and d["file_path"].endswith("E01.mkv")

    # 直链（blob）可用
    b = client.get(f"/api/tv/episodes/{eid}/blob")
    assert b.status_code == 200 and b.content == b"x"


def test_session_dir_kind_prefix():
    import os as _os
    from app.db import TRANSCODE_DIR
    from app.routers.stream.common import _session_dir
    d = _session_dir(7, "f", 0, "episode")
    assert d.startswith(_os.path.join(TRANSCODE_DIR, "e7"))
    assert _session_dir(7, "f", 0, "movie").startswith(
        _os.path.join(TRANSCODE_DIR, "m7"))
