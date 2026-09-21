"""远程文件浏览器：列表（只读时代）与写操作 backend 化（mkdir/rename/move/delete/copy）。"""
import time

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


def test_fs_list_remote_writable(smb_lib):
    lib, _root, _fake = smb_lib
    c = TestClient(app)
    d = c.get(f"/api/fs/list?library={lib['id']}").json()
    # 写操作已 backend 化（mkdir/rename/move/delete/copy），可写库不再 fs_writable=false
    assert d["fs_writable"] is True and d["driver"] == "smb"
    assert [x["name"] for x in d["dirs"]] == ["电影"]
    assert d["dirs"][0]["children"] is None
    d2 = c.get(f"/api/fs/list?path=电影&library={lib['id']}").json()
    assert {x["name"] for x in d2["dirs"]} == {"片 (2020)"}
    assert any(f["name"] == "note.txt" for f in d2["files"])
    # 离线 → 503（不吞成空目录）；清元数据缓存模拟 TTL 过期
    _fake.offline = True
    smb.clear_meta()
    assert c.get(f"/api/fs/list?library={lib['id']}").status_code == 503
    smb.invalidate()


def test_fs_write_remote_ops(smb_lib):
    """直读远程库写操作全链路（backend，无挂载）。"""
    lib, root, _fake = smb_lib
    c = TestClient(app)
    lid = lib["id"]
    # mkdir（含冲突 409）
    r = c.post("/api/fs/mkdir", json={"library_id": lid, "path": "电影",
                                      "name": "新目录"})
    assert r.status_code == 200, r.text
    assert (root / "电影" / "新目录").is_dir()
    assert c.post("/api/fs/mkdir", json={"library_id": lid, "path": "电影",
                                         "name": "新目录"}).status_code == 409
    # rename（dry_run 预览 + 执行）
    d = c.post("/api/fs/rename", json={"library_id": lid, "from": "电影/note.txt",
                                       "name": "n2.txt"}).json()
    assert d["plans"][0]["status"] == "planned"
    r = c.post("/api/fs/rename", json={"library_id": lid, "from": "电影/note.txt",
                                       "name": "n2.txt", "dry_run": False}).json()
    assert r["moved"] == 1 and (root / "电影" / "n2.txt").is_file()
    # move
    r = c.post("/api/fs/move", json={"library_id": lid, "from": "电影/n2.txt",
                                     "to_dir": "电影/新目录", "dry_run": False}).json()
    assert r["moved"] == 1 and (root / "电影" / "新目录" / "n2.txt").is_file()
    # delete：非空目录拒绝、文件删除、空目录可删
    d = c.post("/api/fs/delete", json={"library_id": lid,
                                       "paths": ["电影/新目录"]}).json()
    assert d["plans"][0]["status"] == "dir_not_empty"
    d = c.post("/api/fs/delete", json={"library_id": lid, "dry_run": False,
                                       "paths": ["电影/新目录/n2.txt"]}).json()
    assert d["deleted"] == 1 and not (root / "电影" / "新目录" / "n2.txt").exists()
    # 删最后一个文件后旧目录空 → 收尾自动清掉（与本地 _cleanup_old_dir 同语义）
    assert not (root / "电影" / "新目录").exists()
    # 显式删除空目录
    c.post("/api/fs/mkdir", json={"library_id": lid, "path": "电影", "name": "空目录"})
    d = c.post("/api/fs/delete", json={"library_id": lid, "dry_run": False,
                                       "paths": ["电影/空目录"]}).json()
    assert d["deleted"] == 1 and not (root / "电影" / "空目录").exists()


def test_fs_copy_remote(smb_lib):
    """远程复制：预览统计 + 后台任务分块复制（绝不覆盖，冲突自动副本）。"""
    lib, root, _fake = smb_lib
    c = TestClient(app)
    lid = lib["id"]
    (root / "电影" / "note2.txt").write_bytes(b"x" * 10)
    prev = c.post("/api/fs/copy", json={"library_id": lid,
                                        "from": ["电影/note2.txt"],
                                        "to_dir": "电影/片 (2020)"}).json()
    assert prev["dry_run"] is True and prev["files"] == 1 and prev["bytes"] == 10
    out = c.post("/api/fs/copy", json={"library_id": lid,
                                       "from": ["电影/note2.txt"],
                                       "to_dir": "电影/片 (2020)",
                                       "dry_run": False}).json()
    jid = out["job_id"]
    for _ in range(100):
        st = c.get(f"/api/fs/copy/{jid}").json()
        if st.get("state") in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st["state"] == "done", st
    assert (root / "电影" / "片 (2020)" / "note2.txt").read_bytes() == b"x" * 10
    # 冲突 → 自动「(副本)」，不覆盖
    out2 = c.post("/api/fs/copy", json={"library_id": lid,
                                        "from": ["电影/note2.txt"],
                                        "to_dir": "电影/片 (2020)",
                                        "dry_run": False}).json()
    for _ in range(100):
        st2 = c.get(f"/api/fs/copy/{out2['job_id']}").json()
        if st2.get("state") in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st2["state"] == "done" and st2["renamed"] == 1
    assert (root / "电影" / "片 (2020)" / "note2 (副本).txt").is_file()
    # 目录递归复制到不存在的新目标目录
    d = root / "电影" / "合集目录"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_bytes(b"a")
    (d / "sub" / "b.txt").write_bytes(b"bb")
    out3 = c.post("/api/fs/copy", json={"library_id": lid,
                                        "from": ["电影/合集目录"],
                                        "to_dir": "电影/新目标",
                                        "dry_run": False}).json()
    for _ in range(100):
        st3 = c.get(f"/api/fs/copy/{out3['job_id']}").json()
        if st3.get("state") in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st3["state"] == "done", st3
    assert (root / "电影" / "新目标" / "合集目录" / "a.txt").read_bytes() == b"a"
    assert (root / "电影" / "新目标" / "合集目录" / "sub" / "b.txt").read_bytes() == b"bb"


def test_fs_write_remote_read_only_lib(smb_lib):
    lib, _root, _fake = smb_lib
    store.update_media_library(lib["media_library_id"], read_only=True)
    library_paths.invalidate_cache()
    c = TestClient(app)
    r = c.post("/api/fs/mkdir", json={"library_id": lib["id"], "name": "x"})
    assert r.status_code == 409, r.text
    d = c.get(f"/api/fs/list?library={lib['id']}").json()
    assert d["fs_writable"] is False


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
        # 离线 → 503（不吞成空目录）；清元数据缓存模拟 TTL 过期
        fake.offline = True
        smb.clear_meta()
        assert c.get(f"/api/fs/list?media_library={media['id']}").status_code == 503
        smb.invalidate()
    finally:
        store.delete_media_library(media["id"])
        library_paths.invalidate_cache()
        smb.invalidate()
