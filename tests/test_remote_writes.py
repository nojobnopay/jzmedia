"""Batch D1：远程直读库 NFO/海报经 StorageBackend 落盘（fake SMB）。"""
import os

import pytest

from app import artwork, library_paths, scanner, storage, store
from app.db import POSTER_DIR
from app.storage import smb
from _smb_fake import FakeSmbClient


@pytest.fixture()
def smb_lib(tmp_path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name=f"smbw-{tmp_path.name}", kind="movie", source="smb",
                               artwork_mode="nfo_art",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_library(lib["id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def _movie_row(lib, rel, tmdb_id=778900):
    mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
    store.update_movie_meta(mid, title="Remote Write", year=2020, tmdb_id=tmdb_id)
    return mid


def test_sync_nfo_remote_single_and_ownership(smb_lib):
    lib, root, _fake = smb_lib
    d = root / "RW (2020)"
    d.mkdir()
    (d / "RW (2020).mkv").write_bytes(b"x")
    mid = _movie_row(lib, "RW (2020)/RW (2020).mkv")
    backend = storage.backend_for(lib["id"])
    r = scanner.nfo_link.sync_nfos_for(mid, backend=backend, rel="RW (2020)/RW (2020).mkv")
    assert r["ok"] is True and "movie.nfo" in r["wrote"]
    nfo = d / "movie.nfo"
    assert nfo.is_file() and b"<movie>" in nfo.read_bytes()
    assert store.get_movie(mid)["nfo_hash"]

    # 外部改过 NFO → 默认不覆盖（所有权保护）
    nfo.write_bytes(b"<movie>user-edited</movie>")
    r2 = scanner.nfo_link.sync_nfos_for(mid, backend=backend,
                                        rel="RW (2020)/RW (2020).mkv")
    assert r2["ok"] is True and nfo.read_bytes() == b"<movie>user-edited</movie>"
    r3 = scanner.nfo_link.sync_nfos_for(mid, backend=backend,
                                        rel="RW (2020)/RW (2020).mkv", force=True)
    assert r3["ok"] is True and b"user-edited" not in nfo.read_bytes()


def test_artwork_remote_write(smb_lib):
    lib, root, _fake = smb_lib
    d = root / "Art (2019)"
    d.mkdir()
    mid = _movie_row(lib, "Art (2019)/Art (2019).mkv", tmdb_id=778901)
    poster = os.path.join(POSTER_DIR, "778901.jpg")
    with open(poster, "wb") as fh:
        fh.write(b"\xff\xd8POSTER")
    try:
        backend = storage.backend_for(lib["id"])
        r = artwork.write_for_movie(mid, backend=backend, rel="Art (2019)/Art (2019).mkv")
        assert r["ok"] is True and "poster.jpg" in r["wrote"]
        assert (d / "poster.jpg").read_bytes() == b"\xff\xd8POSTER"
        # 内容相同不重写
        r2 = artwork.write_for_movie(mid, backend=backend,
                                     rel="Art (2019)/Art (2019).mkv")
        assert r2["ok"] is True and r2["wrote"] == []
    finally:
        os.remove(poster)


def test_scan_remote_writes_nfo_and_poster(smb_lib):
    lib, root, _fake = smb_lib
    store.upsert_tmdb_cache(778902, {"title": "Scan Write", "year": 2018,
                                     "media_type": "movie"}, {}, "")
    rel = "Scan Write (2018)/Scan Write (2018).mkv"
    p = root / rel
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    poster = os.path.join(POSTER_DIR, "778902.jpg")
    with open(poster, "wb") as fh:
        fh.write(b"\xff\xd8SCANPOSTER")
    try:
        r = scanner.scan_file(storage.backend_for(lib["id"]), rel, tmdb_hint=778902)
        assert r["status"] == "ok" and r.get("nfo") is True
        assert (p.parent / "movie.nfo").is_file()
        assert (p.parent / "poster.jpg").read_bytes() == b"\xff\xd8SCANPOSTER"
    finally:
        os.remove(poster)


def test_read_only_remote_skips_writes(smb_lib):
    lib, root, _fake = smb_lib
    d = root / "RO (2020)"
    d.mkdir()
    (d / "RO (2020).mkv").write_bytes(b"x")
    with store._lock, store._conn() as c:
        c.execute("UPDATE libraries SET read_only=1 WHERE id=?", (lib["id"],))
    library_paths.invalidate_cache()
    mid = _movie_row(lib, "RO (2020)/RO (2020).mkv", tmdb_id=778903)
    backend = storage.backend_for(lib["id"])
    assert backend.read_only is True
    r = scanner.nfo_link.sync_nfos_for(mid, backend=backend, rel="RO (2020)/RO (2020).mkv")
    assert r["ok"] is False
    assert not (d / "movie.nfo").exists()
    art = artwork.write_for_movie(mid, backend=backend, rel="RO (2020)/RO (2020).mkv")
    assert art["ok"] is False and art["reason"] == "read_only"
