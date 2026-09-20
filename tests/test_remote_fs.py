"""Batch D3：文件浏览器对直读远程库只读（列表可用、写操作 501）。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app
from app.storage import smb
from _smb_fake import FakeSmbClient


@pytest.fixture()
def smb_lib(tmp_path, monkeypatch):
    root = tmp_path / "share"
    (root / "电影" / "片 (2020)").mkdir(parents=True)
    (root / "电影" / "片 (2020)" / "片 (2020).mkv").write_bytes(b"x")
    (root / "电影" / "note.txt").write_bytes(b"n")
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name=f"fs-{tmp_path.name}", source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def test_fs_list_remote_read_only(smb_lib):
    lib, _root, _fake = smb_lib
    c = TestClient(app)
    d = c.get(f"/api/fs/list?library={lib['id']}").json()
    assert d["fs_writable"] is False and d["driver"] == "smb"
    assert [x["name"] for x in d["dirs"]] == ["电影"]
    assert d["dirs"][0]["children"] is None
    d2 = c.get(f"/api/fs/list?path=电影&library={lib['id']}").json()
    assert {x["name"] for x in d2["dirs"]} == {"片 (2020)"}
    assert any(f["name"] == "note.txt" for f in d2["files"])
    # 离线 → 503（不吞成空目录）
    _fake.offline = True
    assert c.get(f"/api/fs/list?library={lib['id']}").status_code == 503
    smb.invalidate()


def test_fs_write_remote_501(smb_lib):
    lib, _root, _fake = smb_lib
    c = TestClient(app)
    lid = lib["id"]
    assert c.post("/api/fs/mkdir", json={"library_id": lid, "name": "x"}).status_code == 501
    assert c.post("/api/fs/rename", json={"library_id": lid, "from": "电影/note.txt",
                                          "name": "n2.txt"}).status_code == 501
    assert c.post("/api/fs/move", json={"library_id": lid, "from": "电影/note.txt",
                                        "to_dir": "电影"}).status_code == 501
    assert c.post("/api/fs/delete", json={"library_id": lid,
                                          "paths": ["电影/note.txt"]}).status_code == 501
    assert c.post("/api/fs/copy", json={"library_id": lid, "from": ["电影/note.txt"],
                                        "to_dir": "电影"}).status_code == 501


def test_fs_list_local_still_writable(media_root):
    c = TestClient(app)
    d = c.get("/api/fs/list").json()
    assert d["fs_writable"] is True


def test_fs_media_root_remote_read_only(tmp_path, monkeypatch):
    """SMB 直读媒体库的媒体根浏览：真实一级目录 + 视频库 subpath 徽章，只读。"""
    root = tmp_path / "share-media"
    (root / "电影" / "片 (2020)").mkdir(parents=True)
    (root / "电影" / "片 (2020)" / "片 (2020).mkv").write_bytes(b"x")
    (root / "剧集").mkdir()
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    media = store.create_media_library(
        name=f"fsmedia-{tmp_path.name}", source="smb",
        smb={"host": "nas", "share": "video", "username": "u", "password": "pw"},
        video_libraries=[{"name": "电影", "subpath": "电影", "kind": "movie"},
                         {"name": "剧集", "subpath": "剧集", "kind": "tv"}])
    library_paths.invalidate_cache()
    smb.invalidate()
    try:
        libs = {v["name"]: v for v in store.video_libraries_of(media["id"])}
        c = TestClient(app)
        d = c.get(f"/api/fs/list?media_library={media['id']}").json()
        assert d["root_kind"] == "media" and d["fs_writable"] is False
        assert d["driver"] == "smb" and d["media_library_id"] == media["id"]
        by = {x["name"]: x for x in d["dirs"]}
        assert by["电影"]["video_library_id"] == libs["电影"]["id"]
        assert by["电影"]["kind"] == "movie"
        assert by["剧集"]["video_library_id"] == libs["剧集"]["id"]
        assert by["剧集"]["kind"] == "tv"
        # 媒体根内继续下钻（远程 list）
        d2 = c.get(f"/api/fs/list?media_library={media['id']}&path=电影").json()
        assert [x["name"] for x in d2["dirs"]] == ["片 (2020)"]
        # 离线 → 503（不吞成空目录）
        fake.offline = True
        assert c.get(f"/api/fs/list?media_library={media['id']}").status_code == 503
        smb.invalidate()
    finally:
        store.delete_media_library(media["id"])
        library_paths.invalidate_cache()
        smb.invalidate()
