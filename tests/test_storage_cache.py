"""P2：存储元数据短 TTL 缓存（TTLCache 纯逻辑 + SMB stat/list 接入与写失效）。"""
import time

import pytest

from app import secrets
from app.storage import StorageNotFound, smb
from app.storage.cache import TTLCache
from _smb_fake import FakeSmbClient


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_ttl_cache_basic_and_expiry():
    clk = FakeClock()
    c = TTLCache(ttl=3.0, clock=clk)
    assert c.get(("stat", "a")) is TTLCache.MISS
    c.put(("stat", "a"), 1)
    assert c.get(("stat", "a")) == 1
    clk.t += 2.9
    assert c.get(("stat", "a")) == 1
    clk.t += 0.2
    assert c.get(("stat", "a")) is TTLCache.MISS


def test_ttl_cache_negative_shorter_and_drop_path():
    clk = FakeClock()
    c = TTLCache(ttl=10.0, clock=clk)
    assert c.neg_ttl == 2.0
    c.put(("list", "d/sub"), None, negative=True)
    clk.t += 2.1
    assert c.get(("list", "d/sub")) is TTLCache.MISS
    c.put(("stat", "d/a"), 1)
    c.put(("stat", "d/sub/b"), 2)
    c.put(("stat", "dx"), 3)
    c.drop_path("d")
    assert c.get(("stat", "d/a")) is TTLCache.MISS
    assert c.get(("stat", "d/sub/b")) is TTLCache.MISS
    assert c.get(("stat", "dx")) == 3


def test_ttl_cache_disabled():
    c = TTLCache(ttl=0)
    c.put(("stat", "a"), 1)
    assert c.get(("stat", "a")) is TTLCache.MISS


def test_ttl_cache_bounded():
    c = TTLCache(ttl=100.0, max_entries=16)
    for i in range(40):
        c.put(("stat", f"p{i}"), i)
    assert len(c) <= 16


@pytest.fixture()
def share(tmp_path, monkeypatch):
    root = tmp_path / "share"
    (root / "d").mkdir(parents=True)
    (root / "d" / "a.mkv").write_bytes(b"x")
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    smb.invalidate()
    yield root, fake
    smb.invalidate()


def _lib(root, lid=903):
    return {"id": lid, "name": "NAS", "source": "smb", "path": "",
            "read_only": 0, "smb_host": "nas", "smb_share": "video",
            "smb_subpath": "", "smb_domain": "", "smb_username": "u",
            "smb_password": secrets.encrypt_str("pw")}


def test_smb_meta_cache_hit_and_write_invalidation(share, monkeypatch):
    root, fake = share
    monkeypatch.setenv("SMB_META_TTL", "30")
    b = smb.SmbStorageBackend(_lib(root))
    b.stat("d/a.mkv")
    b.stat("d/a.mkv")
    assert fake.counts.get("stat") == 1
    b.list("d")
    b.list("d")
    assert fake.counts.get("scandir") == 1
    # 写路径精确失效：写后 stat/list 必须重新走网络
    b.write("d/a.mkv", b"yy")
    b.stat("d/a.mkv")
    assert fake.counts["stat"] == 2
    b.list("d")
    assert fake.counts["scandir"] == 2


def test_smb_negative_cache(share, monkeypatch):
    root, fake = share
    monkeypatch.setenv("SMB_META_TTL", "30")
    b = smb.SmbStorageBackend(_lib(root))
    for _ in range(3):
        with pytest.raises(StorageNotFound):
            b.stat("d/none.mkv")
        with pytest.raises(StorageNotFound):
            b.list("none")
    assert fake.counts.get("stat") == 1
    assert fake.counts.get("scandir") == 1


def test_smb_cache_disabled_by_env(share, monkeypatch):
    root, fake = share
    monkeypatch.setenv("SMB_META_TTL", "0")
    b = smb.SmbStorageBackend(_lib(root))
    b.stat("d/a.mkv")
    b.stat("d/a.mkv")
    assert fake.counts.get("stat") == 2


def test_smb_open_write_atomic(share, monkeypatch):
    root, fake = share
    b = smb.SmbStorageBackend(_lib(root))
    with b.open_write("d/new.bin") as fh:
        fh.write(b"abc")
    assert (root / "d" / "new.bin").read_bytes() == b"abc"
    assert not list((root / "d").glob("*.part-*"))
    with pytest.raises(RuntimeError):
        with b.open_write("d/fail.bin") as fh:
            fh.write(b"x")
            raise RuntimeError("boom")
    assert not (root / "d" / "fail.bin").exists()
    assert not list((root / "d").glob("*.part-*"))
    with b.open_write("d/new.bin") as fh:   # 覆盖（replace）
        fh.write(b"zz")
    assert (root / "d" / "new.bin").read_bytes() == b"zz"


def test_open_read_pool_reuses_handle(share, monkeypatch):
    root, fake = share
    monkeypatch.setenv("SMB_HANDLE_POOL", "4")
    monkeypatch.setenv("SMB_HANDLE_TTL", "30")
    b = smb.SmbStorageBackend(_lib(root))
    with b.open_read("d/a.mkv") as fh:
        assert fh.read() == b"x"
    with b.open_read("d/a.mkv") as fh:
        assert fh.read() == b"x"   # 复用句柄已 seek(0)
    assert fake.counts.get("open_file") == 1


def test_open_read_pool_disabled(share, monkeypatch):
    root, fake = share
    monkeypatch.setenv("SMB_HANDLE_POOL", "0")
    b = smb.SmbStorageBackend(_lib(root))
    with b.open_read("d/a.mkv") as fh:
        fh.read()
    with b.open_read("d/a.mkv") as fh:
        fh.read()
    assert fake.counts.get("open_file") == 2


def test_open_read_pool_ttl_expiry(share, monkeypatch):
    root, fake = share
    monkeypatch.setenv("SMB_HANDLE_POOL", "4")
    monkeypatch.setenv("SMB_HANDLE_TTL", "0.05")
    b = smb.SmbStorageBackend(_lib(root))
    with b.open_read("d/a.mkv") as fh:
        fh.read()
    time.sleep(0.07)
    with b.open_read("d/a.mkv") as fh:
        fh.read()
    assert fake.counts.get("open_file") == 2


def test_open_read_pool_evicted_on_delete(share, monkeypatch):
    root, fake = share
    monkeypatch.setenv("SMB_HANDLE_POOL", "4")
    b = smb.SmbStorageBackend(_lib(root))
    with b.open_read("d/a.mkv") as fh:
        fh.read()
    assert b._handles._idle            # 已入池
    b.delete("d/a.mkv")                # 删除前必须先驱逐，否则共享冲突
    assert not b._handles._idle
    assert not (root / "d" / "a.mkv").exists()


def test_smb_rename_delete_invalidate(share, monkeypatch):
    root, fake = share
    monkeypatch.setenv("SMB_META_TTL", "30")
    b = smb.SmbStorageBackend(_lib(root))
    b.list("d")
    b.rename("d/a.mkv", "d/b.mkv")
    b.list("d")            # 改名后目录列表必须重取
    assert fake.counts.get("scandir") == 2
    with pytest.raises(StorageNotFound):
        b.stat("d/a.mkv")
    assert fake.counts.get("stat") == 1   # 改名已失效旧路径缓存，走网络得到 NotFound
