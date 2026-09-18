"""C 阶段挂载管理器：能力降级/参数白名单/凭据文件权限/挂载指引。"""
import os
import stat

import pytest

from app import library_paths, mounts, secrets, store


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("ALLOW_SMB_MOUNT", "0")
    ok, reason = mounts.mount_supported()
    assert ok is False and "禁用" in reason
    assert mounts.enabled() is False


def test_sanitize_options_whitelist():
    out = mounts._sanitize_options(
        "vers=3.0,unknown=1,sec=ntlmssp,iocharset=utf8", mounts._SMB_KEYS)
    assert "vers=3.0" in out and "sec=ntlmssp" in out
    assert not any(x.startswith("unknown") for x in out)
    assert mounts._sanitize_options("cache=$(rm -rf /)", mounts._SMB_KEYS) == []
    assert mounts._sanitize_options("nosuchkey=1", mounts._SMB_KEYS) == []


def test_cred_file_0600_and_cleanup(tmp_path):
    lib = {"id": 991, "source": "smb", "smb_host": "h", "smb_share": "s",
           "smb_username": "u", "smb_password": secrets.encrypt_str("p@ss"),
           "smb_domain": "d"}
    path = mounts._write_cred_file(lib)
    assert path.endswith(".cred_991")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    content = open(path, encoding="utf-8").read()
    assert "username=u" in content and "password=p@ss" in content
    mounts.cleanup_library(lib)
    assert not os.path.exists(path)


@pytest.fixture()
def smb_library(tmp_path):
    lib = store.create_library(name=f"mnt-{tmp_path.name}", source="smb",
                              path=str(tmp_path / "mnt"),
                              smb={"host": "nas", "share": "media",
                                   "username": "u", "password": "p"})
    library_paths.invalidate_cache()
    yield lib
    store.delete_library(lib["id"])
    library_paths.invalidate_cache()


def test_mount_unsupported_returns_guidance(smb_library, monkeypatch):
    monkeypatch.setattr(mounts, "mount_supported", lambda: (False, "无 CAP_SYS_ADMIN"))
    res = mounts.mount_library(smb_library)
    assert res["ok"] is False and res["mount_supported"] is False
    assert "mount" in res["suggested_cmd"] and "nas/media" in res["suggested_cmd"]
    assert store.get_library(smb_library["id"])["last_status"] == "not_mounted"


def test_local_library_check(media_root, tmp_path):
    path = tmp_path / "local-lib"
    path.mkdir()
    lib = store.create_library(name=f"local-{tmp_path.name}", path=str(path))
    library_paths.invalidate_cache()
    try:
        out = mounts.check_library(lib)
        assert out["readable"] is True and out["last_status"] == "ok"
        assert out["mount_supported"] in (True, False)
    finally:
        store.delete_library(lib["id"])
        library_paths.invalidate_cache()
