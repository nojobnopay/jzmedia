"""StorageBackend 抽象（Phase 1）：Local 后端行为、边界、只读与工厂选择。"""
import os

import pytest

from app import storage
from app.storage import LocalStorageBackend, StorageInvalidPath, StorageNotFound, StorageReadOnly


def _lib(root, read_only=0, name="测试库", source="local", library_id=1):
    return {"id": library_id, "name": name, "source": source, "path": str(root),
            "read_only": read_only}


@pytest.fixture()
def root(tmp_path):
    d = tmp_path / "lib"
    (d / "电影").mkdir(parents=True)
    (d / "电影" / "a.mkv").write_bytes(b"0123456789")
    (d / "b.txt").write_bytes(b"hello")
    return d


def _backend(root, **kw):
    return LocalStorageBackend(_lib(root, **kw))


def test_norm_and_boundaries(root):
    b = _backend(root)
    assert b.norm("") == ""
    assert b.norm("./电影/./a.mkv") == "电影/a.mkv"
    assert b.norm("电影\\a.mkv") == "电影/a.mkv"
    for bad in ("..", "../x", "/etc/passwd", "电影/../../x", "\x00"):
        with pytest.raises(StorageInvalidPath):
            b.norm(bad)


def test_list_stat_exists(root):
    b = _backend(root)
    entries = {e["name"]: e for e in b.list("")}
    assert set(entries) == {"电影", "b.txt"}
    assert entries["电影"]["is_dir"] is True
    assert entries["b.txt"]["size"] == 5
    st = b.stat("b.txt")
    assert st.size == 5 and st.is_dir is False
    assert b.exists("b.txt") and b.is_dir("电影")
    assert not b.exists("missing.mkv")
    assert b.stat("").is_dir
    with pytest.raises(StorageNotFound):
        b.stat("missing.mkv")


def test_read_offset_and_open_read(root):
    b = _backend(root)
    assert b.read("b.txt") == b"hello"
    assert b.read("b.txt", 1, 3) == b"ell"
    assert b.read("b.txt", 2, -1) == b"llo"
    with b.open_read("电影/a.mkv") as fh:
        assert fh.read(4) == b"0123"
    with pytest.raises(StorageNotFound):
        b.read("nope.mkv")


def test_write_rename_delete(root):
    b = _backend(root)
    b.write("电影/new/sub/n.bin", b"abc")
    assert (root / "电影" / "new" / "sub" / "n.bin").read_bytes() == b"abc"
    assert not list((root / "电影" / "new" / "sub").glob("*.tmp"))
    b.rename("电影/new/sub/n.bin", "电影/new/moved.bin")
    assert b.read("电影/new/moved.bin") == b"abc"
    b.delete("电影/new/moved.bin")
    assert not b.exists("电影/new/moved.bin")
    b.delete("电影/new/sub", recursive=True)
    assert not b.exists("电影/new/sub")
    (root / "emptydir").mkdir()
    b.delete("emptydir")                    # 空目录可非递归删除（rm 语义）
    (root / "full").mkdir()
    (root / "full" / "x.bin").write_bytes(b"x")
    with pytest.raises(storage.StorageError):
        b.delete("full")                    # 非空目录需 recursive
    b.delete("full", recursive=True)
    with pytest.raises(StorageNotFound):
        b.delete("full")


def test_read_only_guards(root):
    b = _backend(root, read_only=1)
    assert b.read("b.txt") == b"hello"
    for call in (lambda: b.write("x.bin", b"x"),
                 lambda: b.mkdir("newdir"),
                 lambda: b.rename("b.txt", "c.txt"),
                 lambda: b.delete("b.txt")):
        with pytest.raises(StorageReadOnly):
            call()
    assert b.test_write() == {"ok": True, "skipped": True, "reason": "read_only"}


def test_symlink_escape_rejected(root, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    os.symlink(outside, root / "link.txt")
    b = _backend(root)
    assert b.exists("link.txt")            # 存在性不拦
    with pytest.raises(StorageInvalidPath):
        b.read("link.txt")
    with pytest.raises(StorageInvalidPath):
        b.write("link.txt", b"x")
    # 指向库内的符号链接放行
    os.symlink(root / "b.txt", root / "inlink.txt")
    assert b.read("inlink.txt") == b"hello"


def test_health_and_diagnostics(root):
    b = _backend(root)
    h = b.health_check()
    assert h["ok"] is True and h["status"] == "online" and h["driver"] == "local"
    assert b.test_read()["ok"] is True
    w = b.test_write()
    assert w == {"ok": True, "skipped": False}
    assert not any(p.name.startswith(".jzmedia-write-test") for p in root.iterdir())


def test_factory_local_and_mount(media_root, monkeypatch):
    from app.db import mount_point

    monkeypatch.setenv("SMB_DRIVER", "mount")
    local = storage.backend_for_library(_lib(media_root, source="local"))
    assert local.driver == "local"
    smb = storage.backend_for_library(
        _lib(mount_point(77), source="smb", name="NAS", library_id=77))
    assert smb.driver == "mount"
    assert smb.root == mount_point(77)
    with pytest.raises(storage.StorageError):
        storage.backend_for_library({"id": 1, "source": "smb", "path": ""})
