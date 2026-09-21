"""SMB 直读后端（Phase 2）：用假 smbclient（本地目录镜像）离线验证。

- 不触网：`app.storage.smb.smbclient` 被替换为基于 tmp 目录的 fake；
- 覆盖 list/stat/read/write/rename/delete/只读守卫/错误映射/工厂+内网 Range 代理。
"""
import urllib.error
import urllib.request

import pytest

from app import store
from app.storage import StorageDenied, StorageOffline, StorageReadOnly, httpproxy, smb
from _smb_fake import FakeSmbClient


def _lib(root, read_only=0, subpath="", password="pw"):
    from app import secrets
    return {"id": 901, "name": "NAS", "source": "smb", "path": "",
            "read_only": read_only, "smb_host": "nas", "smb_share": "video",
            "smb_subpath": subpath, "smb_domain": "", "smb_username": "u",
            "smb_password": secrets.encrypt_str(password)}


@pytest.fixture()
def share(tmp_path):
    d = tmp_path / "share"
    (d / "电影").mkdir(parents=True)
    (d / "电影" / "a.mkv").write_bytes(b"0123456789")
    (d / "b.txt").write_bytes(b"hello")
    return d


@pytest.fixture()
def fake(share, monkeypatch):
    f = FakeSmbClient(share)
    monkeypatch.setattr(smb, "smbclient", f)
    smb.invalidate()
    yield f
    smb.invalidate()


def test_list_stat_read(share, fake):
    b = smb.SmbStorageBackend(_lib(share))
    names = {e["name"]: e for e in b.list("")}
    assert set(names) == {"电影", "b.txt"}
    assert names["电影"]["is_dir"] is True
    assert names["b.txt"]["size"] == 5
    st = b.stat("电影/a.mkv")
    assert st.size == 10 and st.is_dir is False
    assert b.read("电影/a.mkv", 2, 3) == b"234"
    with b.open_read("b.txt") as fh:
        assert fh.read() == b"hello"
    assert b.abs_path("b.txt") is None


def test_subpath_and_windows_paths(share, fake):
    (share / "Movies").mkdir()
    (share / "Movies" / "x.mkv").write_bytes(b"x")
    b = smb.SmbStorageBackend(_lib(share, subpath="Movies"))
    assert [e["name"] for e in b.list("")] == ["x.mkv"]
    assert b.read("x.mkv") == b"x"


def test_write_rename_delete(share, fake):
    b = smb.SmbStorageBackend(_lib(share))
    b.write("电影/new/n.bin", b"abc")
    assert (share / "电影" / "new" / "n.bin").read_bytes() == b"abc"
    b.rename("电影/new/n.bin", "电影/new/m.bin")
    assert (share / "电影" / "new" / "m.bin").exists()
    b.delete("电影/new/m.bin")
    assert not (share / "电影" / "new" / "m.bin").exists()
    b.delete("电影/new", recursive=True)
    assert not (share / "电影" / "new").exists()


def test_read_only_and_credentials(share, fake):
    ro = smb.SmbStorageBackend(_lib(share, read_only=1))
    with pytest.raises(StorageReadOnly):
        ro.write("x.bin", b"x")
    bad = FakeSmbClient(share, password_ok=False)
    smb.smbclient = bad
    with pytest.raises(StorageDenied):
        smb.SmbStorageBackend(_lib(share))
    off = FakeSmbClient(share, offline=True)
    smb.smbclient = off
    with pytest.raises(StorageOffline):
        smb.SmbStorageBackend(_lib(share)).list("")


def test_error_mapping_offline_after_init(share, fake):
    b = smb.SmbStorageBackend(_lib(share))
    fake.offline = True
    with pytest.raises(StorageOffline):
        b.list("")
    assert smb.map_smb_error(ConnectionResetError("x"), "t").code == "OFFLINE"
    assert smb.map_smb_error(FileNotFoundError("x"), "t").code == "NOT_FOUND"


def test_factory_and_range_proxy(share, fake, monkeypatch):
    from app import library_paths

    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name="NAS代理", kind="movie", source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    try:
        from app import storage
        b = storage.backend_for_library(store.get_library(lib["id"]))
        assert b.driver == "smb"
        url = b.get_read_url("电影/a.mkv")
        assert url.startswith("http://127.0.0.1:")
        try:
            # 全量 Range
            req = urllib.request.Request(url, headers={"Range": "bytes=0-3"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                assert resp.status == 206
                assert resp.headers["Content-Range"] == "bytes 0-3/10"
                assert resp.read() == b"0123"
            # 后缀 Range + HEAD
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as resp:
                assert resp.status == 200
                assert resp.headers["Content-Length"] == "10"
            # 越界 → 416
            req = urllib.request.Request(url, headers={"Range": "bytes=99-"})
            with pytest.raises(urllib.error.HTTPError) as ei:
                urllib.request.urlopen(req, timeout=10)
            assert ei.value.code == 416
        finally:
            httpproxy.shutdown()
    finally:
        httpproxy.shutdown()
        store.delete_media_library(lib["media_library_id"])
        smb.invalidate()


def test_offline_not_treated_as_missing(share, fake, monkeypatch):
    """§19 回归：直读库离线/冷却期不得判为“文件已删”，/files/clean 绝不删行。"""
    from app import library_paths
    from app.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)

    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name="NAS离线", kind="movie", source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    mid = None
    try:
        mid = store.upsert_movie_by_path("电影/a.mkv", library_id=lib["id"])
        store.update_movie_meta(mid, title="A", year=2020, tmdb_id=1)
        # 在线：文件存在 → 不是缺失
        assert client.get(f"/api/files/missing?library={lib['id']}").json()["total"] == 0
        # 离线（stat 抛 StorageOffline）→ 同样不判缺失
        fake.offline = True
        assert client.get(f"/api/files/missing?library={lib['id']}").json()["total"] == 0
        d = client.post("/api/files/clean",
                        json={"dry_run": False, "library_id": lib["id"]}).json()
        assert d["total"] == 0 and store.get_movie(mid) is not None
        # 后端初始化失败（认证错误→30s 冷却，backend_for 直接抛）也不得判缺失
        fake.offline = False
        fake.password_ok = False
        lib2 = store.create_library(name="NAS坏凭据", kind="movie", source="smb",
                                    smb={"host": "nas2", "share": "video",
                                         "username": "u", "password": "pw"})
        library_paths.invalidate_cache()
        mid2 = store.upsert_movie_by_path("电影/a.mkv", library_id=lib2["id"])
        try:
            assert client.get(
                f"/api/files/missing?library={lib2['id']}").json()["total"] == 0
            d2 = client.post("/api/files/clean",
                             json={"dry_run": False, "library_id": lib2["id"]}).json()
            assert d2["total"] == 0 and store.get_movie(mid2) is not None
        finally:
            store.delete_movie(mid2)
            store.delete_media_library(lib2["media_library_id"])
    finally:
        if mid is not None:
            store.delete_movie(mid)
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()
        smb.invalidate()


def test_range_parse_edges():
    assert httpproxy._parse_range(None, 10) == (0, 9, 200)
    assert httpproxy._parse_range("bytes=2-5", 10) == (2, 5, 206)
    assert httpproxy._parse_range("bytes=2-", 10) == (2, 9, 206)
    assert httpproxy._parse_range("bytes=-4", 10) == (6, 9, 206)
    assert httpproxy._parse_range("bytes=99-", 10)[2] == 416
    assert httpproxy._parse_range("bytes=abc", 10) == (0, 9, 200)
