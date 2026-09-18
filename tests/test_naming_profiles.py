"""D 命名档：plex 产物/兼容性告警/off 不改名（归档一律扁平）。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app

client = TestClient(app)


def _lib(tmp_path, name, profile):
    root = tmp_path / name
    root.mkdir()
    lib = store.create_library(name=f"{name}-{tmp_path.name}", path=str(root),
                              naming_profile=profile)
    library_paths.invalidate_cache()
    return lib, root


def _movie(root, rel, lib_id, **meta):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = store.upsert_movie_by_path(rel, library_id=lib_id)
    store.update_movie_meta(mid, **meta)
    return mid, p


def _organize(lib_id, mid, dry=True):
    return client.post("/api/files/organize", json={
        "mode": "inplace", "library_id": lib_id, "ids": [mid],
        "dry_run": dry}).json()


@pytest.fixture()
def plex_lib(tmp_path):
    lib, root = _lib(tmp_path, "plexlib", "plex")
    yield lib, root
    store.delete_library(lib["id"])
    library_paths.invalidate_cache()


def test_plex_edition_folder_and_stem(plex_lib):
    lib, root = plex_lib
    mid, _ = _movie(root, "raw/Old.Movie.2020.mkv", lib["id"],
                    title="Old Movie", year=2020, edition="导演剪辑版")
    d = _organize(lib["id"], mid)
    plan = d["plans"][0]
    assert plan["to"] == "raw/Old Movie (2020) {edition-导演剪辑版}/Old Movie (2020) {edition-导演剪辑版}.mkv"
    assert plan["naming_profile"] == "plex"


def test_plex_spec_variant_dash(plex_lib):
    lib, root = plex_lib
    mid1, _ = _movie(root, "raw/Variant.Movie.2020.2160p.mkv", lib["id"],
                     title="Variant Movie", year=2020, tmdb_id=991001)
    mid2, _ = _movie(root, "raw/Variant.Movie.2020.1080p.mkv", lib["id"],
                     title="Variant Movie", year=2020, tmdb_id=991001)
    d = client.post("/api/files/organize", json={
        "mode": "inplace", "library_id": lib["id"], "ids": [mid1, mid2],
        "dry_run": True}).json()
    tos = [p["to"] for p in d["plans"]]
    assert len(tos) == 2
    assert any("- 2160P" in t for t in tos) and any("- 1080P" in t for t in tos)
    assert all(t.startswith("raw/Variant Movie (2020)/") for t in tos)


def test_off_profile_never_renames(tmp_path):
    lib, root = _lib(tmp_path, "offlib", "off")
    try:
        mid, p = _movie(root, "raw/Keep.Me.2021.mkv", lib["id"],
                        title="Keep Me", year=2021)
        d = _organize(lib["id"], mid)
        assert d["plans"] == [] and d["conflicts"] == []
        d2 = _organize(lib["id"], mid, dry=False)
        assert d2.get("results", []) == []
        assert (root / "raw" / "Keep.Me.2021.mkv").is_file()
    finally:
        store.delete_library(lib["id"])
        library_paths.invalidate_cache()


def test_plex_extras_dir_mapping(plex_lib):
    lib, root = plex_lib
    mid, ap = _movie(root, "raw/Extra.Movie.2022.mkv", lib["id"],
                     title="Extra Movie", year=2022)
    srt = root / "raw" / "Extra.Movie.2022-预告.mkv"
    srt.write_bytes(b"x")
    store.upsert_extra("raw/Extra.Movie.2022-预告.mkv", mid, "trailer",
                       library_id=lib["id"])
    from app.routers.files.executor import move_attached_extras
    r = move_attached_extras(mid, str(ap.parent))
    assert r["moved"] == 1
    assert (ap.parent / "Trailers" / "Extra.Movie.2022-预告.mkv").is_file()


def test_plex_warnings_rules():
    from app.routers.files.planner import _plex_warnings
    w = _plex_warnings("Bad <name>", {"stack": "cd1", "labels": ["x264"]}, "")
    assert any("非法字符" in x for x in w)
    assert any("分卷" in x for x in w)
    assert _plex_warnings("Good Name (2020)", {"stack": "", "labels": []}, "") == []
