"""Batch D3：存储身份防重复 + SMB 连接地址分离（Tailscale）。"""
import pytest

from app import library_paths, store
from app.storage import smb
from _smb_fake import FakeSmbClient


def _smb(host="nas", share="video", subpath="Movies", connect=""):
    d = {"host": host, "share": share, "subpath": subpath,
         "username": "u", "password": "pw"}
    if connect:
        d["connect_host"] = connect
    return d


@pytest.fixture()
def cleanup():
    ids = []
    yield ids
    for lid in ids:
        store.delete_library(lid)
    library_paths.invalidate_cache()


def test_identity_of_variants(tmp_path):
    from app.store._base import _identity_of
    d = tmp_path / "x"
    d.mkdir()
    assert _identity_of({"source": "local", "path": str(d)}) == f"local:{d.resolve()}"
    assert _identity_of({"source": "smb", "smb_host": "NAS", "smb_share": "Video",
                         "smb_subpath": "Movies/"}) == "smb:nas/video/movies"
    assert _identity_of({"source": "nfs", "nfs_export": "NAS:/vol1"}) == "nfs:nas:/vol1"


def test_duplicate_smb_identity_rejected(cleanup):
    a = store.create_library(name="dup-a", source="smb", smb=_smb())
    cleanup.append(a["id"])
    assert a["storage_identity"] == "smb:nas/video/movies"
    with pytest.raises(ValueError) as ei:
        store.create_library(name="dup-b", source="smb", smb=_smb())
    assert "同一存储" in str(ei.value)
    # 不同子目录/不同共享不冲突
    b = store.create_library(name="dup-c", source="smb", smb=_smb(subpath="TV"))
    cleanup.append(b["id"])
    c = store.create_library(name="dup-d", source="smb", smb=_smb(share="photo"))
    cleanup.append(c["id"])
    # 改库到已占用身份 → 拒绝
    with pytest.raises(ValueError):
        store.update_library(c["id"], smb=_smb())
    # 改名/其它字段不触发误判
    store.update_library(c["id"], name="dup-d2")


def test_nfs_identity_rejected(cleanup):
    a = store.create_library(name="nfs-a", source="nfs",
                             nfs={"export": "nas:/vol1"})
    cleanup.append(a["id"])
    with pytest.raises(ValueError):
        store.create_library(name="nfs-b", source="nfs", nfs={"export": "NAS:/vol1"})


def test_connect_host_used_for_connection(smb_lib):
    lib, root, fake = smb_lib
    from app import storage
    storage.backend_for(lib["id"])            # 触发连接（懒构造）
    assert fake.sessions and fake.sessions[0][0] == "100.1.2.3"
    assert lib["smb_host"] == "nas-home" and lib["smb_connect_host"] == "100.1.2.3"
    assert lib["storage_identity"] == "smb:nas-home/video/movies"
    # 公开输出不含密码但含连接地址
    pub = store.public_library(store.get_library(lib["id"]))
    assert pub["smb_connect_host"] == "100.1.2.3" and "smb_password" not in pub


@pytest.fixture()
def smb_lib(tmp_path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    (root / "a.txt").write_bytes(b"x")
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name=f"connhost-{tmp_path.name}", source="smb",
                               smb=_smb(host="nas-home", connect="100.1.2.3"))
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_library(lib["id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def test_diag_uses_connect_host(tmp_path, monkeypatch):
    import contextlib
    import socket
    from app.storage import diag

    root = tmp_path / "share"
    (root / "Movies").mkdir(parents=True)
    (root / "Movies" / "readme.txt").write_bytes(b"x")
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                          ("127.0.0.1", 445))])
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: contextlib.nullcontext())
    out = diag.diagnose_smb(host="nas-home", connect_host="127.0.0.1",
                            share="video", subpath="Movies",
                            username="u", password="pw", check_ffprobe=False)
    assert out["ok"] is True
    assert fake.sessions and fake.sessions[0][0] == "127.0.0.1"
    smb.invalidate()


def test_m15_backfills_identity(tmp_path):
    from app.store import _base
    with store._lock, store._conn() as c:
        c.execute("INSERT INTO libraries(name, kind, source, path, storage_identity,"
                  " created_at, updated_at) VALUES('m15-tmp','movie','smb','/x','',0,0)")
        lid = int(c.execute("SELECT id FROM libraries WHERE name='m15-tmp'").fetchone()[0])
        c.execute("UPDATE libraries SET smb_host='NAS', smb_share='Video',"
                  " smb_subpath='Movies' WHERE id=?", (lid,))
        try:
            _base._m15(c)
            row = c.execute("SELECT storage_identity FROM libraries WHERE id=?",
                            (lid,)).fetchone()
            assert row[0] == "smb:nas/video/movies"
        finally:
            c.execute("DELETE FROM libraries WHERE id=?", (lid,))
