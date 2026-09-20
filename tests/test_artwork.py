"""D6 本地图片落盘：poster/fanart/多版本 poster、幂等、只读与模式开关。"""
import os

import pytest

from app import artwork, library_paths, store
from app.db import POSTER_DIR


@pytest.fixture()
def art_lib(tmp_path):
    root = tmp_path / "artlib"
    root.mkdir()
    lib = store.create_library(name=f"art-{tmp_path.name}", path=str(root),
                              artwork_mode="nfo_art")
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _movie(root, rel, lib_id, tmdb_id, poster_file):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel, library_id=lib_id)
    store.update_movie_meta(mid, title="Art", year=2020, tmdb_id=tmdb_id,
                            poster_path=poster_file)
    return mid, p


def _cache(tmdb_id, backdrop="/bd.jpg", **extra):
    store.upsert_tmdb_cache(tmdb_id, {"title": "Art", "media_type": "movie",
                                      "backdrop_tmdb_path": backdrop, **extra})


def test_artwork_nfo_art_roundtrip(art_lib, monkeypatch):
    lib, root = art_lib
    tmdb_id = 771001
    poster = os.path.join(POSTER_DIR, f"{tmdb_id}.jpg")
    with open(poster, "wb") as fh:
        fh.write(b"POSTER")
    _cache(tmdb_id)

    def fake_dl(path, dest, size="w500"):
        with open(dest, "wb") as fh:
            fh.write(b"FANART")
        return True
    monkeypatch.setattr(artwork.tmdb, "download_image", fake_dl)

    mid, ap = _movie(root, "mv/Art.2020.mkv", lib["id"], tmdb_id, f"{tmdb_id}.jpg")
    try:
        out = artwork.write_for_movie(mid, str(ap))
        assert out["ok"] is True
        assert "poster.jpg" in out["wrote"] and "fanart.jpg" in out["wrote"]
        assert (ap.parent / "poster.jpg").read_bytes() == b"POSTER"
        assert (ap.parent / "fanart.jpg").read_bytes() == b"FANART"
        # 幂等：二次不重写
        out2 = artwork.write_for_movie(mid, str(ap))
        assert out2["ok"] is True and out2["wrote"] == []
    finally:
        store.delete_movie(mid)


def test_artwork_disabled_and_readonly(art_lib, monkeypatch):
    lib, root = art_lib
    tmdb_id = 771002
    poster = os.path.join(POSTER_DIR, f"{tmdb_id}.jpg")
    with open(poster, "wb") as fh:
        fh.write(b"POSTER")
    _cache(tmdb_id)
    mid, ap = _movie(root, "mv2/Art.2020.mkv", lib["id"], tmdb_id, f"{tmdb_id}.jpg")
    try:
        store.update_library(lib["id"], artwork_mode="nfo")
        library_paths.invalidate_cache()
        assert artwork.write_for_movie(mid, str(ap))["reason"] == "disabled"

        store.update_library(lib["id"], artwork_mode="nfo_art")
        store.update_media_library(lib["media_library_id"], read_only=True)
        library_paths.invalidate_cache()
        assert artwork.write_for_movie(mid, str(ap))["reason"] == "read_only"
        assert not (ap.parent / "poster.jpg").exists()
    finally:
        store.update_media_library(lib["media_library_id"], read_only=False)
        library_paths.invalidate_cache()
        store.delete_movie(mid)


def test_artwork_multi_version_stem_poster(art_lib, monkeypatch):
    """P3 后默认（kodi/同 edition）：多版本只写一份 poster，不写 per-version。"""
    lib, root = art_lib
    tmdb_id = 771003
    poster = os.path.join(POSTER_DIR, f"{tmdb_id}.jpg")
    with open(poster, "wb") as fh:
        fh.write(b"POSTER")
    _cache(tmdb_id)

    def fake_dl(path, dest, size="w500"):
        with open(dest, "wb") as fh:
            fh.write(b"FANART")
        return True
    monkeypatch.setattr(artwork.tmdb, "download_image", fake_dl)

    mid1, ap1 = _movie(root, "mv3/Art.2020.mkv", lib["id"], tmdb_id, f"{tmdb_id}.jpg")
    mid2, ap2 = _movie(root, "mv3/Art.2020.2160p.mkv", lib["id"], tmdb_id, f"{tmdb_id}.jpg")
    try:
        out = artwork.write_for_movie(mid2, str(ap2))
        assert out["ok"] is True
        assert "poster.jpg" in out["wrote"], out
        assert "Art.2020.2160p-poster.jpg" not in out["wrote"], out
        assert not (ap2.parent / "Art.2020.2160p-poster.jpg").exists()
        # plex + 版本间 edition 不同 → 才写 per-version（Plex 拆独立条目）
        with store._lock, store._conn() as c:
            c.execute("UPDATE libraries SET naming_profile='plex' WHERE id=?",
                      (lib["id"],))
            c.execute("UPDATE movies SET edition='导演剪辑版' WHERE id=?", (mid1,))
        library_paths.invalidate_cache()
        out2 = artwork.write_for_movie(mid2, str(ap2))
        assert "Art.2020.2160p-poster.jpg" in out2["wrote"], out2
        assert (ap2.parent / "Art.2020.2160p-poster.jpg").is_file()
    finally:
        store.delete_movie(mid1)
        store.delete_movie(mid2)
