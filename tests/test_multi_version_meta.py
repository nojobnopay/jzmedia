"""P3：多版本元数据简化（默认只写一份；plex+不同 edition 才写 per-version）。"""
import os

import pytest

from app import artwork, library_paths, scanner, store
from app.db import POSTER_DIR


@pytest.fixture()
def libs(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    made = []

    def _mk(name, profile="kodi"):
        lib = store.create_library(name=f"{name}-{tmp_path.name}", path=str(root),
                                   naming_profile=profile, artwork_mode="nfo_art")
        made.append(lib["id"])
        return lib
    library_paths.invalidate_cache()
    yield _mk, root
    for lid in made:
        store.delete_library(lid)
    library_paths.invalidate_cache()


def _version(lib, root, rel, title, tmdb, edition=""):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
    store.update_movie_meta(mid, title=title, year=2020, tmdb_id=tmdb,
                            edition=edition)
    return mid


def _poster(tmdb):
    path = os.path.join(POSTER_DIR, f"{tmdb}.jpg")
    with open(path, "wb") as fh:
        fh.write(b"\xff\xd8POSTER")
    return path


def test_kodi_multi_version_single_meta(libs, monkeypatch):
    mk, root = libs
    lib = mk("kodi")
    d = root / "Multi (2020)"
    mid_a = _version(lib, root, "Multi (2020)/A (2020).mkv", "Multi", 424242)
    _version(lib, root, "Multi (2020)/B (2020).mkv", "Multi", 424242,
             edition="导演剪辑版")
    poster = _poster(424242)
    try:
        # 预置旧 per-version 残留（NFO 与海报），应被清理
        (d / "A (2020).nfo").write_bytes(b"<movie>old</movie>")
        (d / "A (2020)-poster.jpg").write_bytes(b"\xff\xd8POSTER")
        r = scanner.nfo_link.sync_nfos_for(mid_a, str(d / "A (2020).mkv"))
        assert r["wrote"] == ["movie.nfo"], r
        assert "A (2020).nfo" in r["deleted"]
        assert not (d / "A (2020).nfo").exists()
        art = artwork.write_for_movie(mid_a, str(d / "A (2020).mkv"))
        assert art["wrote"] == ["poster.jpg"], art
        assert "A (2020)-poster.jpg" in art.get("cleaned", [])
        assert not (d / "A (2020)-poster.jpg").exists()
        assert (d / "poster.jpg").is_file()
        assert not (d / "B (2020)-poster.jpg").exists()
    finally:
        os.remove(poster)


def test_plex_editions_keep_per_version(libs):
    mk, root = libs
    lib = mk("plex", profile="plex")
    d = root / "Ed (2020)"
    mid_a = _version(lib, root, "Ed (2020)/Ed (2020).mkv", "Ed", 434343)
    _version(lib, root, "Ed (2020)/Ed (2020) DC.mkv", "Ed", 434343,
             edition="导演剪辑版")
    poster = _poster(434343)
    try:
        r = scanner.nfo_link.sync_nfos_for(mid_a, str(d / "Ed (2020).mkv"))
        # plex + 不同 edition：movie.nfo + 各自版本同名
        assert set(r["wrote"]) == {"movie.nfo", "Ed (2020).nfo",
                                   "Ed (2020) DC.nfo"}, r
        art = artwork.write_for_movie(mid_a, str(d / "Ed (2020).mkv"))
        assert "poster.jpg" in art["wrote"]
        assert "Ed (2020)-poster.jpg" in art["wrote"]
    finally:
        os.remove(poster)


def test_plex_same_edition_single(libs):
    mk, root = libs
    lib = mk("plex2", profile="plex")
    d = root / "Same (2020)"
    mid_a = _version(lib, root, "Same (2020)/A (2020).mkv", "Same", 444444)
    _version(lib, root, "Same (2020)/B (2020).mkv", "Same", 444444)
    poster = _poster(444444)
    try:
        r = scanner.nfo_link.sync_nfos_for(mid_a, str(d / "A (2020).mkv"))
        assert r["wrote"] == ["movie.nfo"], r
        art = artwork.write_for_movie(mid_a, str(d / "A (2020).mkv"))
        assert art["wrote"] == ["poster.jpg"], art
    finally:
        os.remove(poster)


def test_env_override_per_version(libs, monkeypatch):
    mk, root = libs
    lib = mk("kodi-env")
    d = root / "Env (2020)"
    mid_a = _version(lib, root, "Env (2020)/A (2020).mkv", "Env", 454545)
    _version(lib, root, "Env (2020)/B (2020).mkv", "Env", 454545)
    monkeypatch.setenv("PER_VERSION_META", "1")
    r = scanner.nfo_link.sync_nfos_for(mid_a, str(d / "A (2020).mkv"))
    assert "A (2020).nfo" in r["wrote"] and "movie.nfo" in r["wrote"]
