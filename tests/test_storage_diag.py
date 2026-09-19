"""SMB 连接诊断管线（Phase 3）：分阶段结果与错误码（离线，fake/桩网络）。"""
import contextlib
import socket

import pytest

from app.storage import diag, httpproxy, smb
from _smb_fake import FakeSmbClient


@pytest.fixture()
def share(tmp_path):
    d = tmp_path / "share"
    (d / "电影").mkdir(parents=True)
    (d / "电影" / "a.mkv").write_bytes(b"0123456789")
    return d


@pytest.fixture()
def fake(share, monkeypatch):
    f = FakeSmbClient(share)
    monkeypatch.setattr(smb, "smbclient", f)
    smb.invalidate()
    yield f
    smb.invalidate()


def _net_ok(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                          ("127.0.0.1", 445))])
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: contextlib.nullcontext())


def _stages(out):
    return [s["stage"] for s in out["stages"]]


def test_diag_address_invalid():
    out = diag.diagnose_smb(host="", share="")
    assert out["status"] == "failed" and out["stage"] == "ADDRESS"
    assert out["code"] == "INVALID_ADDRESS" and out["suggestions"]
    out = diag.diagnose_smb(host="nas", share="")
    assert out["code"] == "INVALID_SHARE"


def test_diag_dns_failure(monkeypatch):
    def _boom(*a, **k):
        raise socket.gaierror("not found")
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    out = diag.diagnose_smb(host="nas-home", share="video", check_ffprobe=False)
    assert out["stage"] == "DNS" and out["code"] == "HOSTNAME_RESOLVE_FAILED"
    assert any("Tailscale" in s for s in out["suggestions"])


def test_diag_tcp_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                          ("127.0.0.1", 445))])

    def _refused(*a, **k):
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(socket, "create_connection", _refused)
    out = diag.diagnose_smb(host="nas", share="video", check_ffprobe=False)
    assert out["stage"] == "TCP" and out["code"] == "CONNECTION_REFUSED"


def test_diag_auth_failed(share, monkeypatch):
    _net_ok(monkeypatch)
    monkeypatch.setattr(smb, "smbclient", FakeSmbClient(share, password_ok=False))
    out = diag.diagnose_smb(host="nas", share="video", username="u",
                            password="bad", check_ffprobe=False)
    assert out["stage"] == "AUTH" and out["code"] == "AUTH_FAILED"
    assert _stages(out)[:4] == ["ADDRESS", "DNS", "TCP", "SMB"]


def test_diag_share_and_path(share, monkeypatch):
    _net_ok(monkeypatch)
    monkeypatch.setattr(smb, "smbclient",
                        FakeSmbClient(share, stat_ntstatus=0xC00000CC))
    out = diag.diagnose_smb(host="nas", share="video", username="u",
                            password="pw", check_ffprobe=False)
    assert out["stage"] == "SHARE" and out["code"] == "SHARE_NOT_FOUND"

    monkeypatch.setattr(smb, "smbclient",
                        FakeSmbClient(share, stat_ntstatus=0xC0000034))
    out = diag.diagnose_smb(host="nas", share="video", subpath="nope",
                            username="u", password="pw", check_ffprobe=False)
    assert out["stage"] == "PATH" and out["code"] == "PATH_NOT_FOUND"


def test_diag_success_with_skips(share, fake, monkeypatch):
    _net_ok(monkeypatch)
    out = diag.diagnose_smb(host="nas", share="video", subpath="", username="u",
                            password="pw", check_ffprobe=False)
    assert out["status"] == "ok" and out["stage"] == "FFPROBE"
    assert _stages(out) == ["ADDRESS", "DNS", "TCP", "SMB", "AUTH", "SHARE",
                            "PATH", "READ", "WRITE", "STREAM", "FFPROBE"]
    skipped = {s["stage"] for s in out["stages"] if s.get("skipped")}
    assert skipped == {"FFPROBE"}               # 有视频样本故 STREAM 实跑，FFPROBE 按开关跳过
    assert all("password" not in str(s) for s in out["stages"])


def test_diag_read_only_skips_write(share, fake, monkeypatch):
    _net_ok(monkeypatch)
    out = diag.diagnose_smb(host="nas", share="video", username="u", password="pw",
                            read_only=True, check_ffprobe=False)
    assert out["status"] == "ok"
    wr = next(s for s in out["stages"] if s["stage"] == "WRITE")
    assert wr.get("skipped") is True


def test_diag_stream_failed_when_offline_midway(share, fake, monkeypatch):
    _net_ok(monkeypatch)
    orig_read = smb.SmbStorageBackend.read

    def _fail_big_read(self, path, offset=0, length=-1):
        if length >= (1 << 20):      # 仅 STREAM 阶段的大块随机读失败
            raise smb.StorageOffline("fake drop")
        return orig_read(self, path, offset, length)
    monkeypatch.setattr(smb.SmbStorageBackend, "read", _fail_big_read)
    out = diag.diagnose_smb(host="nas", share="video", username="u", password="pw",
                            check_ffprobe=False)
    assert out["stage"] == "STREAM" and out["code"] == "STREAM_FAILED"
    httpproxy.shutdown()


def test_url_for_backend_ephemeral(tmp_path):
    """诊断用临时令牌：无库行也能被代理按 backend 直接服务。"""
    import urllib.request
    from app.storage import LocalStorageBackend
    root = tmp_path / "lib"
    root.mkdir()
    (root / "a.bin").write_bytes(b"hello")
    backend = LocalStorageBackend({"id": 0, "name": "诊断", "source": "local",
                                   "path": str(root), "read_only": 0})
    url = httpproxy.url_for_backend(backend, "a.bin")
    assert "/v1/d/" in url
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            assert resp.status == 200 and resp.read() == b"hello"
    finally:
        httpproxy.shutdown()


def test_diag_api_endpoints(tmp_path, monkeypatch):
    """API：预检口不落库；已存库诊断写 last_status。"""
    from fastapi.testclient import TestClient
    from app import library_paths, store
    from app.main import app

    share_dir = tmp_path / "share2"
    share_dir.mkdir()
    (share_dir / "readme.txt").write_bytes(b"hello")
    fake = FakeSmbClient(share_dir)
    monkeypatch.setattr(smb, "smbclient", fake)
    _net_ok(monkeypatch)
    client = TestClient(app)

    r = client.post("/api/libraries/diag/smb", json={
        "host": "nas", "share": "video", "username": "u", "password": "pw"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    lib = store.create_library(name="NAS诊断", kind="movie", source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    try:
        r = client.post(f"/api/libraries/{lib['id']}/diag")
        assert r.status_code == 200 and r.json()["status"] == "ok"
        assert store.get_library(lib["id"])["last_status"] == "ok"
    finally:
        store.delete_library(lib["id"])
        library_paths.invalidate_cache()
        smb.invalidate()
        httpproxy.shutdown()
