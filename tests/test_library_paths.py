"""多库路径解析层（v12）：默认库/多库最长前缀/边界校验/只读标记。"""
import os

import pytest

from app import library_paths, store
from app.store import _base

_EXTRA_IDS = (2, 3)


def _insert_library(lid, name, path, kind="movie", read_only=0, enabled=1):
    """直接落两行：媒体库（连接/只读）+ 视频库（根 subpath=''）。"""
    with store._lock, store._conn() as c:
        c.execute("INSERT INTO media_libraries(id, name, source, path, read_only,"
                  " enabled, created_at, updated_at) VALUES(?, ?, 'local', ?, ?, ?, 0, 0)",
                  (lid, name, path, read_only, enabled))
        c.execute("INSERT INTO libraries(id, media_library_id, name, kind, subpath, path,"
                  " enabled, created_at, updated_at)"
                  " VALUES(?, ?, ?, ?, '', ?, ?, 0, 0)",
                  (lid, lid, name, kind, path, enabled))
    library_paths.invalidate_cache()


@pytest.fixture()
def extra_libraries():
    yield
    with store._lock, store._conn() as c:
        ids = ",".join(map(str, _EXTRA_IDS))
        c.execute(f"DELETE FROM libraries WHERE id IN ({ids})")
        c.execute(f"DELETE FROM media_libraries WHERE id IN ({ids})")
    library_paths.invalidate_cache()


def test_default_library_and_resolve(media_root):
    lib = library_paths.default_library()
    assert lib["id"] == _base.DEFAULT_LIBRARY_ID
    assert library_paths.default_root() == str(media_root)
    assert library_paths.default_id() == _base.DEFAULT_LIBRARY_ID
    assert library_paths.resolve(lib["id"], "a/b.mkv") == os.path.join(
        str(media_root), "a/b.mkv")
    assert library_paths.abs_path("x.mkv") == os.path.join(str(media_root), "x.mkv")
    assert library_paths.resolve(lib["id"], "") == str(media_root)


def test_locate_longest_prefix(media_root, tmp_path, extra_libraries):
    root2 = tmp_path / "second"
    root2.mkdir()
    _insert_library(2, "second", str(root2))
    nested = root2 / "nested"
    nested.mkdir()
    lib, rel = library_paths.locate(str(nested / "a.mkv"))
    assert lib["id"] == 2 and rel == os.path.join("nested", "a.mkv")
    # 不在任何库内
    assert library_paths.locate("/nowhere/x.mkv") == (None, None)
    # 默认库内
    lib, rel = library_paths.locate(str(media_root / "x/y.mkv"))
    assert lib["id"] == _base.DEFAULT_LIBRARY_ID and rel == os.path.join("x", "y.mkv")


def test_check_inside(media_root):
    assert library_paths.check_inside(1, "sub/./a.mkv") == os.path.join("sub", "a.mkv")
    for bad in ("", ".", "..", "../x", "/etc/passwd"):
        with pytest.raises(ValueError):
            library_paths.check_inside(1, bad)


def test_read_only_flag(media_root, tmp_path, extra_libraries):
    root2 = tmp_path / "ro"
    root2.mkdir()
    _insert_library(3, "ro", str(root2), read_only=1)
    assert library_paths.is_read_only(3) is True
    assert library_paths.is_read_only(1) is False


def test_list_only_enabled(media_root, tmp_path, extra_libraries):
    root2 = tmp_path / "disabled"
    root2.mkdir()
    _insert_library(2, "disabled", str(root2), enabled=0)
    ids = {l["id"] for l in library_paths.list_libraries(only_enabled=True)}
    assert 2 not in ids and _base.DEFAULT_LIBRARY_ID in ids
    ids_all = {l["id"] for l in library_paths.list_libraries()}
    assert 2 in ids_all
