"""Batch A：远程直读库扫描（fake SMB）——遍历/入库/离线不删/写降级/重扫。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, storage, store
from app.main import app
from app.storage import smb
from _smb_fake import FakeSmbClient


def _make_lib(tmp_path, monkeypatch, kind="movie"):
    root = tmp_path / "share"
    root.mkdir()
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name=f"smbscan-{tmp_path.name}", kind=kind, source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    return lib, root, fake


@pytest.fixture()
def smb_movie_lib(tmp_path, monkeypatch):
    lib, root, fake = _make_lib(tmp_path, monkeypatch)
    monkeypatch.setattr(scanner.scan, "search_with_fallback",
                        lambda title, year: (None, title, False))
    yield lib, root, fake
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def test_iter_tree_skips_hidden_and_system(smb_movie_lib):
    lib, root, _fake = smb_movie_lib
    (root / "电影").mkdir()
    (root / "电影" / "a.mkv").write_bytes(b"x")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "h.mkv").write_bytes(b"x")
    (root / "@eaDir").mkdir()
    (root / "@eaDir" / "t.mkv").write_bytes(b"x")
    (root / "b.txt").write_bytes(b"x")
    backend = storage.backend_for(lib["id"])
    rels = {e.rel for e in backend.iter_tree("", skip_dirs=scanner.scan_skip_dirs())}
    assert "电影/a.mkv" in rels and "b.txt" in rels
    assert not any(".hidden" in r or "@eaDir" in r for r in rels)


def test_scan_movie_library_direct(smb_movie_lib):
    lib, root, _fake = smb_movie_lib
    d = root / "RemoteOnly Movie (2001)"
    d.mkdir()
    (d / "RemoteOnly Movie (2001).mkv").write_bytes(b"x")
    out = scanner.scan_all(library_id=lib["id"])
    assert [r["status"] for r in out] == ["no_match"]
    rel = "RemoteOnly Movie (2001)/RemoteOnly Movie (2001).mkv"
    row = store.get_by_path(rel, library_id=lib["id"])
    assert row is not None
    # 远程直读不写 NFO/图片（挂载点不存在，绝不凭空造目录）
    assert not list(d.glob("*.nfo")) and not (d / "poster.jpg").exists()


def test_scan_file_matched_remote_writes_nfo_only(smb_movie_lib):
    """D1 起：远程可写库匹配成功会经 backend 写 NFO；默认 artwork_mode=nfo 不写海报。"""
    lib, root, _fake = smb_movie_lib
    store.upsert_tmdb_cache(778899, {"title": "Remote Cached", "year": 2001,
                                     "media_type": "movie"}, {}, "")
    rel = "Remote Cached (2001).mkv"
    (root / rel).write_bytes(b"x")
    r = scanner.scan_file(storage.backend_for(lib["id"]), rel, tmdb_hint=778899)
    assert r["status"] == "ok" and r.get("nfo") is True
    assert (root / "movie.nfo").is_file()
    assert not (root / "poster.jpg").exists()
    assert store.get_by_path(rel, library_id=lib["id"])["tmdb_id"] == 778899


def test_scan_offline_skips_and_keeps_rows(smb_movie_lib):
    lib, root, fake = smb_movie_lib
    (root / "Keep (2000).mkv").write_bytes(b"x")
    mid = store.upsert_movie_by_path("Keep (2000).mkv", library_id=lib["id"])
    store.upsert_extra("Keep (2000)-trailer.mkv", mid, "trailer", library_id=lib["id"])
    fake.offline = True
    out = scanner.scan_all(library_id=lib["id"])
    assert [r["status"] for r in out] == ["library_offline"]
    # 离线 ≠ 删除：影片行与花絮行都保留
    assert store.get_by_path("Keep (2000).mkv", library_id=lib["id"]) is not None
    extras = [e for e in store.list_all_extras() if e["library_id"] == lib["id"]]
    assert any(e["file_path"] == "Keep (2000)-trailer.mkv" for e in extras)


def test_scan_tv_library_direct(tmp_path, monkeypatch):
    lib, root, _fake = _make_lib(tmp_path, monkeypatch, kind="tv")
    try:
        d = root / "Remote Show (2020)" / "Season 01"
        d.mkdir(parents=True)
        (d / "Remote.Show.S01E01.mkv").write_bytes(b"x")
        out = scanner.scan_all(library_id=lib["id"])
        assert [r["status"] for r in out] == ["tv_ok"]
        shows = store.list_shows(lib["id"])
        assert shows and shows[0]["title"] == "Remote Show"
        assert store.count_episodes(lib["id"]) == 1
    finally:
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()
        smb.invalidate()


def test_sidecar_subtitles_fs(smb_movie_lib):
    lib, root, _fake = smb_movie_lib
    (root / "subs").mkdir()
    (root / "Side (2001).mkv").write_bytes(b"x")
    (root / "Side (2001).chs.srt").write_bytes(b"1\n")
    (root / "subs" / "Side (2001).ass").write_bytes(b"[Script Info]")
    items = scanner.sidecar_subtitles_fs(storage.backend_for(lib["id"]),
                                         "Side (2001).mkv")
    by_name = {i["name"]: i for i in items}
    assert by_name["Side (2001).chs.srt"]["codec"] == "srt"
    assert by_name["Side (2001).chs.srt"]["suffix"] == "chs"
    assert by_name["Side (2001).ass"]["codec"] == "ass"
    assert by_name["Side (2001).ass"]["rel"] == "subs/Side (2001).ass"


def test_rescan_remote_movie_api(smb_movie_lib):
    lib, root, _fake = smb_movie_lib
    rel = "Rescan Me (2002).mkv"
    (root / rel).write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
    client = TestClient(app)
    r = client.post(f"/api/movies/{mid}/rescan")
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("no_match", "ok", "ok_needs_review")


def test_scan_remote_generic_extras_dir_backend_aware(smb_movie_lib):
    """远程直读库 Movie/Scenes/ 归花絮（backend 感知，不再依赖 POSIX default_root）。"""
    lib, root, _fake = smb_movie_lib
    d = root / "Remote Movie (2002)"
    d.mkdir()
    (d / "Remote Movie (2002).mkv").write_bytes(b"x")
    (d / "Scenes").mkdir()
    (d / "Scenes" / "clip.mkv").write_bytes(b"x")
    out = scanner.scan_all(library_id=lib["id"])
    statuses = {r["file"]: r["status"] for r in out}
    # 归花絮（能按祖先目录名归属则 attached，否则 orphan）；绝不建 movies 行
    assert statuses.get("Remote Movie (2002)/Scenes/clip.mkv") in (
        "extra_attached", "extra_orphan")
    assert store.get_by_path("Remote Movie (2002)/Scenes/clip.mkv",
                             library_id=lib["id"]) is None
