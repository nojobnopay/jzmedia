"""库管理 API（v12）：CRUD / 嵌套校验 / 只读与策略 / 删库事务（只清 DB 不动文件）。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app

client = TestClient(app)

_created: list[int] = []


@pytest.fixture(autouse=True)
def _cleanup_created():
    yield
    for lid in list(_created):
        store.delete_library(lid)
    _created.clear()
    library_paths.invalidate_cache()


def _mk(tmp_path, name, sub="libA", **extra):
    p = tmp_path / sub
    p.mkdir(parents=True, exist_ok=True)
    body = {"name": name, "kind": "movie", "source": "local", "path": str(p)}
    body.update(extra)
    r = client.post("/api/libraries", json=body)
    assert r.status_code == 200, r.text
    lib = r.json()
    _created.append(lib["id"])
    return lib, p


def test_create_list_patch_check(tmp_path, media_root):
    lib, p = _mk(tmp_path, "test-lib-a")
    assert lib["kind"] == "movie" and lib["source"] == "local"
    assert lib["smb_password_set"] is False
    assert lib["naming_profile"] == "kodi" and lib["artwork_mode"] == "nfo"

    data = client.get("/api/libraries").json()
    ids = {l["id"]: l for l in data["items"]}
    assert lib["id"] in ids
    assert store.DEFAULT_LIBRARY_ID in ids
    assert data["default_id"] == store.DEFAULT_LIBRARY_ID

    # 重名 / 非法 kind / 非法命名档
    r = client.post("/api/libraries", json={"name": "test-lib-a",
                                            "path": str(tmp_path / "other")})
    assert r.status_code == 422
    r = client.post("/api/libraries", json={"name": "test-kind", "kind": "mixed",
                                            "path": str(tmp_path / "other")})
    assert r.status_code == 422
    r = client.post("/api/libraries", json={"name": "test-np", "path": str(tmp_path / "other"),
                                            "naming_profile": "xbmc"})
    assert r.status_code == 422

    r = client.patch(f"/api/libraries/{lib['id']}",
                     json={"read_only": True, "naming_profile": "plex",
                           "artwork_mode": "nfo_art"})
    assert r.status_code == 200, r.text
    assert r.json()["read_only"] is True
    assert r.json()["naming_profile"] == "plex"
    assert library_paths.is_read_only(lib["id"]) is True

    r = client.post(f"/api/libraries/{lib['id']}/check")
    assert r.status_code == 200
    j = r.json()
    assert j["readable"] is True and j["writable"] is True and j["exists"] is True
    assert j["last_status"] == "ok"


def test_nested_root_rejected(tmp_path, media_root):
    lib, p = _mk(tmp_path, "test-lib-nest", "outer")
    child = p / "child"
    child.mkdir()
    r = client.post("/api/libraries", json={"name": "test-lib-nest-child",
                                            "path": str(child)})
    assert r.status_code == 422
    assert "重叠" in r.json()["detail"]


def test_delete_library_only_clears_db(tmp_path, media_root):
    lib, p = _mk(tmp_path, "test-lib-del", "del")
    (p / "a.mkv").write_bytes(b"video")
    mid = store.upsert_movie_by_path("a.mkv", library_id=lib["id"])
    store.upsert_extra("a.srt", mid, "extra", library_id=lib["id"])
    store.set_scan_state("a.mkv", 1, 2, "no_match", library_id=lib["id"])
    store.upsert_media_info(mid, {"playable": True, "duration": 1.0,
                                  "audio": [], "subs": [], "attachments": [],
                                  "probe_ver": 1, "probed_at": 1})
    store.save_progress(mid, 3.0, 10.0)

    r = client.delete(f"/api/libraries/{lib['id']}")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["deleted"] is True and j["movies"] == 1 and j["extras"] == 1
    _created.remove(lib["id"])

    # 磁盘文件必须原样保留
    assert (p / "a.mkv").is_file()
    # DB 关联全清
    assert store.get_by_path("a.mkv", library_id=lib["id"]) is None
    assert store.get_media_info(mid) is None
    assert store.get_progress(mid) is None
    assert store.get_scan_state("a.mkv", library_id=lib["id"]) is None
    assert store.get_library(lib["id"]) is None


def test_read_only_library_blocks_writes(tmp_path, media_root):
    lib, p = _mk(tmp_path, "test-lib-ro", "ro", read_only=True)
    (p / "x").mkdir()
    (p / "x" / "movie.mkv").write_bytes(b"x")
    mid = store.upsert_movie_by_path("x/movie.mkv", library_id=lib["id"])
    store.update_movie_meta(mid, title="只读片", year=2000)
    try:
        # 执行归档 → 409；预览（只读操作）放行
        r = client.post("/api/files/organize",
                        json={"mode": "inplace", "ids": [mid], "dry_run": False})
        assert r.status_code == 409
        r = client.post("/api/files/organize",
                        json={"mode": "inplace", "ids": [mid], "dry_run": True})
        assert r.status_code == 200
        # 删除单文件 → 409
        r = client.request("DELETE", f"/api/movies/{mid}/files",
                           json={"name": "movie.mkv", "dry_run": False,
                                 "confirm": True})
        assert r.status_code == 409
        assert (p / "x" / "movie.mkv").is_file()
    finally:
        store.delete_movie(mid)


def test_smb_library_path_auto_derived(tmp_path, media_root):
    from app.db import mount_point
    r = client.post("/api/libraries", json={
        "name": "test-lib-smb-auto", "source": "smb",
        "smb": {"host": "nas", "share": "video", "subpath": "Movies"}})
    assert r.status_code == 200, r.text
    lib = r.json()
    _created.append(lib["id"])
    assert lib["path"] == mount_point(lib["id"])
    # 远程库路径不可修改
    rp = client.patch(f"/api/libraries/{lib['id']}",
                      json={"path": str(tmp_path / "whatever")})
    assert rp.status_code == 422


def test_local_empty_library_path_can_change(tmp_path, media_root):
    lib, p = _mk(tmp_path, "test-lib-relocate", "old")
    newdir = tmp_path / "newhome"
    newdir.mkdir()
    r = client.patch(f"/api/libraries/{lib['id']}", json={"path": str(newdir)})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == str(newdir)
    # 有片后拒绝改路径
    (newdir / "a.mkv").write_bytes(b"x")
    mid = store.upsert_movie_by_path("a.mkv", library_id=lib["id"])
    try:
        r2 = client.patch(f"/api/libraries/{lib['id']}", json={"path": str(p)})
        assert r2.status_code == 422
        assert "影片记录" in r2.json()["detail"]
    finally:
        store.delete_movie(mid)


def test_mount_endpoints_local_and_smb_guidance(tmp_path, media_root):
    lib, p = _mk(tmp_path, "test-lib-mount")
    # 本地库：mount/unmount 为无操作成功
    r = client.post(f"/api/libraries/{lib['id']}/mount")
    assert r.status_code == 200 and r.json()["ok"] is True
    r = client.post(f"/api/libraries/{lib['id']}/unmount")
    assert r.status_code == 200 and r.json()["ok"] is True

    # SMB 库：无论环境是否支持挂载，都必须返回可读的失败/指引，不抛 500
    rs = client.post("/api/libraries", json={
        "name": "test-lib-smb", "source": "smb", "path": str(tmp_path / "smb"),
        "smb": {"host": "nas", "share": "media", "username": "u", "password": "p"}})
    assert rs.status_code == 200, rs.text
    smb = rs.json()
    _created.append(smb["id"])
    m = client.post(f"/api/libraries/{smb['id']}/mount").json()
    assert m["ok"] is False
    assert m["suggested_cmd"] and "mount" in m["suggested_cmd"]
