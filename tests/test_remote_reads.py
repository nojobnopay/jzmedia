"""远程直读读路径：详情页文件清单/blob、批量删除范围、TV 存在性、NFO 导入。

此前这些路径直连 POSIX：直读库文件清单全空、blob 404、TV exists=false、
批量删除体积 0、NFO 离线匹配跳过。本文件用 fake SMB 回归。
"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, storage, store
from app.main import app
from app.metadata import nfo_import
from app.storage import smb
from _smb_fake import FakeSmbClient


@pytest.fixture()
def smb_lib(tmp_path, monkeypatch):
    root = tmp_path / "share"
    d = root / "电影" / "片 (2020)"
    d.mkdir(parents=True)
    (d / "片 (2020).mkv").write_bytes(b"0123456789")
    (d / "片 (2020).chs.srt").write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    (d / "extras").mkdir()
    (d / "extras" / "花絮.mkv").write_bytes(b"ex")
    (d / "movie.nfo").write_text(
        "<?xml version='1.0'?><movie><title>片</title><year>2020</year>"
        "<uniqueid type='tmdb'>424242</uniqueid></movie>", encoding="utf-8")
    (root / "剧集" / "Show" / "S01").mkdir(parents=True)
    (root / "剧集" / "Show" / "S01" / "E01.mkv").write_bytes(b"ep")
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name=f"reads-{tmp_path.name}", kind="movie", source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def _movie(lib, rel="电影/片 (2020)/片 (2020).mkv", **meta):
    mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
    store.update_movie_meta(mid, title="片", year=2020, tmdb_id=424242, **meta)
    return mid


def test_movie_files_remote_metadata(smb_lib):
    lib, _root, _fake = smb_lib
    mid = _movie(lib)
    c = TestClient(app)
    d = c.get(f"/api/movies/{mid}/files").json()
    assert d["scoped"] == "dir"
    feat = {x["name"]: x for x in d["feature"]}
    assert feat["片 (2020).mkv"]["size"] == 10
    assert {x["name"] for x in d["subtitles"]} == {"片 (2020).chs.srt"}
    assert {x["name"] for x in d["nfos"]} == {"movie.nfo"}
    # extras/ 子目录文件可见（远程 list）
    assert {x["name"] for x in d["extras"]} == {"extras/花絮.mkv"}


def test_movie_files_remote_attached_extra(smb_lib):
    lib, root, _fake = smb_lib
    mid = _movie(lib)
    (root / "电影" / "片 (2020)" / "散落花絮.mkv").write_bytes(b"ex2")
    store.upsert_extra("电影/片 (2020)/散落花絮.mkv", mid, "extra",
                       library_id=lib["id"])
    c = TestClient(app)
    d = c.get(f"/api/movies/{mid}/files").json()
    rels = {x["rel"] for x in d["extras"]}
    assert "电影/片 (2020)/散落花絮.mkv" in rels


def test_movie_blob_remote(smb_lib):
    lib, _root, _fake = smb_lib
    mid = _movie(lib)
    c = TestClient(app)
    r = c.get(f"/api/movies/{mid}/blob", params={"name": "片 (2020).chs.srt"})
    assert r.status_code == 200, r.text
    assert b"00:00:01" in r.content


def test_movie_file_delete_remote(smb_lib):
    """详情页删单文件（DELETE body）在直读库经 backend 执行。"""
    lib, root, _fake = smb_lib
    mid = _movie(lib)
    c = TestClient(app)
    r = c.request("DELETE", f"/api/movies/{mid}/files",
                  json={"name": "片 (2020).chs.srt", "dry_run": False})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 1
    assert not (root / "电影" / "片 (2020)" / "片 (2020).chs.srt").exists()
    # 正片仍在，目录保留
    assert (root / "电影" / "片 (2020)" / "片 (2020).mkv").is_file()


def test_batch_delete_scope_remote(smb_lib):
    lib, _root, _fake = smb_lib
    mid = _movie(lib)
    c = TestClient(app)
    d = c.post("/api/movies/batch-delete",
               json={"ids": [mid], "dry_run": True}).json()
    p = next(x for x in d["plans"] if x["id"] == mid)
    rels = {f["rel"] for f in p["files"]}
    assert "电影/片 (2020)/片 (2020).mkv" in rels
    assert "电影/片 (2020)/片 (2020).chs.srt" in rels
    assert p["total_size"] > 0 and p["exclusive"] is True


def test_tv_episode_exists_remote(smb_lib):
    lib, _root, _fake = smb_lib
    sid = store.upsert_show(lib["id"], "Show", 2020)
    eid = store.upsert_episode(sid, lib["id"], "剧集/Show/S01/E01.mkv", 1, 1)
    c = TestClient(app)
    d = c.get(f"/api/tv/episodes/{eid}").json()
    assert d["exists"] is True


def test_nfo_import_remote(smb_lib):
    lib, _root, _fake = smb_lib
    backend = storage.backend_for(lib["id"])
    cand = nfo_import.candidates_for_backend(backend, "电影/片 (2020)/片 (2020).mkv")
    assert cand is not None and cand.tmdb_id == 424242 and cand.title == "片"


def test_scan_remote_uses_nfo_offline_match(smb_lib, monkeypatch):
    """TMDB 搜索不可用时，远程直读库也能用同目录 NFO 绑定 tmdb_id（离线缓存重放）。"""
    from app import scanner
    lib, _root, _fake = smb_lib
    store.upsert_tmdb_cache(424242, {"title": "片", "original_title": "Pian",
                                     "year": 2020, "media_type": "movie"}, {}, "")
    monkeypatch.setattr(scanner.scan, "search_with_fallback",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    backend = storage.backend_for(lib["id"])
    r = scanner.scan_file(backend, "电影/片 (2020)/片 (2020).mkv")
    assert r.get("status") in ("ok", "ok_needs_review"), r
    assert r.get("match_source") == "nfo", r
    row = store.get_by_path("电影/片 (2020)/片 (2020).mkv", library_id=lib["id"])
    assert int(row["tmdb_id"] or 0) == 424242
