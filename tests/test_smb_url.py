"""SMB 单输入框解析（app/smburl.py）+ 建库/编辑连接 API 集成。"""
from fastapi.testclient import TestClient

from app import library_paths, store
from app.db import mount_point
from app.main import app
from app.smburl import parse_smb_url

client = TestClient(app)


def test_parse_vectors():
    a = parse_smb_url("\\\\NAS\\video\\Movies")
    assert (a["host"], a["share"], a["subpath"], a["error"]) == (
        "NAS", "video", "Movies", "")
    b = parse_smb_url("//NAS/video")
    assert (b["host"], b["share"], b["subpath"], b["error"]) == (
        "NAS", "video", "", "")
    c = parse_smb_url("NAS\\video\\TV Shows")
    assert (c["host"], c["share"], c["subpath"]) == ("NAS", "video", "TV Shows")
    d = parse_smb_url("//100.101.102.103/video\\\\Movies")
    assert (d["host"], d["share"], d["subpath"]) == ("100.101.102.103", "video", "Movies")
    e = parse_smb_url("smb://user@nas/media/%E7%94%B5%E5%BD%B1")
    assert (e["host"], e["share"], e["subpath"], e["username"], e["error"]) == (
        "nas", "media", "电影", "user", "")


def test_parse_errors():
    assert "共享名" in parse_smb_url("\\\\NAS")["error"]
    assert "映射盘" in parse_smb_url("Z:\\Movies")["error"]
    assert "请输入" in parse_smb_url("")["error"]


def test_create_and_edit_connection_via_url(tmp_path):
    r = client.post("/api/libraries", json={
        "name": f"smb-url-{tmp_path.name}", "source": "smb",
        "smb": {"url": "\\\\NAS\\video\\Movies", "username": "u",
                "password": "p"}})
    assert r.status_code == 200, r.text
    lib = r.json()
    try:
        assert lib["smb_host"] == "NAS"
        assert lib["smb_share"] == "video"
        assert lib["smb_subpath"] == "Movies"
        assert lib["smb_username"] == "u"
        assert lib["path"] == mount_point(lib["media_library_id"])
        assert lib["smb_password_set"] is True

        # 编辑连接：换地址（密码留空=不改）
        r2 = client.patch(f"/api/media-libraries/{lib['media_library_id']}", json={
            "smb_url": "smb://nas2/media/%E7%94%B5%E5%BD%B1",
            "smb": {"username": "u2"}})
        assert r2.status_code == 200, r2.text
        lib2 = r2.json()
        assert (lib2["smb_host"], lib2["smb_share"], lib2["smb_subpath"]) == (
            "nas2", "media", "电影")
        assert lib2["smb_username"] == "u2"
        assert lib2["smb_password_set"] is True   # 留空不清密码
        assert lib2["path"] == mount_point(lib["media_library_id"])   # 路径不受连接编辑影响
        # 视频库视图同步媒体连接
        v2 = client.get("/api/libraries").json()["items"]
        v2 = {v["id"]: v for v in v2}[lib["id"]]
        assert v2["smb_subpath"] == "电影"

        # 非法地址 → 422
        r3 = client.patch(f"/api/media-libraries/{lib['media_library_id']}",
                          json={"smb_url": "Z:\\x"})
        assert r3.status_code == 422
    finally:
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()
