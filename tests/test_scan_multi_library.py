"""按库扫描（B3）：结果带 library_id、同名相对路径不串、extras GC 按库分区。"""
import pytest

from app import library_paths, scanner, store


@pytest.fixture(autouse=True)
def _offline_tmdb(monkeypatch):
    """扫描链路禁止触网：search 返回空。"""
    monkeypatch.setattr(scanner.tmdb, "search_movie", lambda q, year=None: [])


@pytest.fixture()
def second_library(tmp_path):
    root = tmp_path / "libscan"
    root.mkdir()
    lib = store.create_library(name=f"scan-lib-{tmp_path.name}", path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_scan_only_target_library(media_root, second_library):
    lib, root = second_library
    rel = "mov/Some.Movie.1999.mkv"
    _touch(root, rel)
    # 默认库放一个同名相对路径的文件与行（模拟两库同名文件）
    _touch(media_root, rel)
    default_mid = store.upsert_movie_by_path(rel)

    res = scanner.scan_all(library_id=lib["id"])
    assert res
    assert all(r.get("library_id") == lib["id"] for r in res)
    row = store.get_by_path(rel, library_id=lib["id"])
    assert row is not None and row["library_id"] == lib["id"]
    assert row["id"] != default_mid
    # 默认库行不受影响
    assert store.get_by_path(rel)["id"] == default_mid


def test_extras_gc_scoped_to_library(media_root, second_library):
    lib, root = second_library
    store.upsert_extra("ghost/default.srt", None, "extra",
                       library_id=store.DEFAULT_LIBRARY_ID)
    store.upsert_extra("ghost/lib.srt", None, "extra", library_id=lib["id"])
    scanner.scan_all(library_id=lib["id"])
    paths = {e["file_path"] for e in store.list_all_extras()}
    assert "ghost/default.srt" in paths          # 默认库孤儿行不被误删
    assert "ghost/lib.srt" not in paths          # 目标库孤儿行被 GC
    store.delete_extra_by_path("ghost/default.srt")


def test_scan_all_without_library_id_covers_all(media_root, second_library, monkeypatch):
    lib, root = second_library
    _touch(root, "a/Movie.A.2001.mkv")
    _touch(media_root, "b/Movie.B.2002.mkv")
    res = scanner.scan_all()
    by_lib = {}
    for r in res:
        by_lib.setdefault(int(r.get("library_id")), []).append(r.get("status"))
    assert store.DEFAULT_LIBRARY_ID in by_lib and lib["id"] in by_lib
    rel_a = "a/Movie.A.2001.mkv"
    assert store.get_by_path(rel_a, library_id=lib["id"]) is not None
    assert store.get_by_path(rel_a) is None
