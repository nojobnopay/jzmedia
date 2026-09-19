"""Batch C：直读库健康检查/状态/看门狗跳过（fake SMB + 网络桩）。"""
import contextlib
import socket

import pytest
from fastapi.testclient import TestClient

from app import library_paths, mounts, store
from app.main import app
from app.storage import smb
from _smb_fake import FakeSmbClient


def _net_ok(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                          ("127.0.0.1", 445))])
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: contextlib.nullcontext())


@pytest.fixture()
def smb_lib(tmp_path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    (root / "readme.txt").write_bytes(b"hello")   # 无视频样本：STREAM/FFPROBE 按跳过处理
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name=f"smbchk-{tmp_path.name}", kind="movie", source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_library(lib["id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def test_list_exposes_smb_driver(smb_lib, monkeypatch):
    client = TestClient(app)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    d = client.get("/api/libraries").json()
    assert d["smb_driver"] == "direct"


def test_check_smb_direct_ok(smb_lib, monkeypatch):
    lib, _root, _fake = smb_lib
    _net_ok(monkeypatch)
    client = TestClient(app)
    r = client.post(f"/api/libraries/{lib['id']}/check")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["readable"] is True and d["driver"] == "smb"
    assert d["last_status"] == "ok"
    assert store.get_library(lib["id"])["last_status"] == "ok"
    assert [s["stage"] for s in d["stages"]][-1] == "FFPROBE"


def test_check_smb_direct_auth_failed(smb_lib, monkeypatch):
    lib, root, fake = smb_lib
    _net_ok(monkeypatch)
    monkeypatch.setattr(smb, "smbclient", FakeSmbClient(root, password_ok=False))
    client = TestClient(app)
    r = client.post(f"/api/libraries/{lib['id']}/check")
    assert r.status_code == 200
    d = r.json()
    assert d["readable"] is False and d["code"] == "AUTH_FAILED"
    assert d["last_status"] == "auth_failed"
    assert store.get_library(lib["id"])["last_status"] == "auth_failed"
    assert "密码" in d["reason"]
    smb.invalidate()


def test_check_smb_read_only_account_is_warning(smb_lib, monkeypatch):
    """只读账号：WRITE 失败不整体失败，readable 真、writable 假、带 warning。"""
    lib, root, _fake = smb_lib
    _net_ok(monkeypatch)
    monkeypatch.setattr(smb, "smbclient", FakeSmbClient(root, write_denied=True))
    client = TestClient(app)
    r = client.post(f"/api/libraries/{lib['id']}/check")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["readable"] is True and d["writable"] is False
    assert d["ok"] is True and d["warning"]
    assert "写入失败" in d["warning"]
    assert store.get_library(lib["id"])["last_status"] == "ok"
    wr = next(s for s in d["stages"] if s["stage"] == "WRITE")
    assert wr["ok"] is False and wr.get("warning") is True
    smb.invalidate()


def test_watchdog_skips_smb_when_direct(smb_lib, monkeypatch):
    lib, _root, _fake = smb_lib
    monkeypatch.setenv("SMB_DRIVER", "direct")
    assert all(int(l["id"]) != int(lib["id"]) for l in mounts._auto_remote_libs())
    monkeypatch.setenv("SMB_DRIVER", "mount")
    with store._lock, store._conn() as c:
        c.execute("UPDATE libraries SET auto_mount=1 WHERE id=?", (lib["id"],))
    assert any(int(l["id"]) == int(lib["id"]) for l in mounts._auto_remote_libs())
