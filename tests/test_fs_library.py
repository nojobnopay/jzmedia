"""多库文件浏览/归档 scope（B5）：fs 与 organize 的 library 参数限定目标库。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.main import app

client = TestClient(app)


@pytest.fixture()
def second_library(tmp_path):
    root = tmp_path / "libfs"
    root.mkdir()
    lib = store.create_library(name=f"fs-lib-{tmp_path.name}", path=str(root))
    library_paths.invalidate_cache()
    yield lib, root
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_fs_list_scoped(media_root, second_library):
    lib, root = second_library
    (root / "sub").mkdir()
    (root / "sub" / "a.mkv").write_bytes(b"x")
    r = client.get(f"/api/fs/list?library={lib['id']}").json()
    assert "sub" in [d["name"] for d in r["dirs"]]
    default = client.get("/api/fs/list").json()
    assert "sub" not in [d["name"] for d in default["dirs"]]


def test_fs_mkdir_rename_scoped(media_root, second_library):
    lib, root = second_library
    r = client.post("/api/fs/mkdir", json={"path": "", "name": "新目录",
                                           "library_id": lib["id"]})
    assert r.status_code == 200, r.text
    assert (root / "新目录").is_dir()
    _touch(root, "old.mkv")
    r = client.post("/api/fs/rename", json={"from": "old.mkv", "name": "new.mkv",
                                            "dry_run": False,
                                            "library_id": lib["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["moved"] == 1
    assert (root / "new.mkv").is_file()
    assert not (root / "old.mkv").exists()


def test_organize_scoped_to_library(media_root, second_library):
    lib, root = second_library
    _touch(root, "raw/Scoped.Movie.2019.mkv")
    mid = store.upsert_movie_by_path("raw/Scoped.Movie.2019.mkv",
                                     library_id=lib["id"])
    store.update_movie_meta(mid, title="Scoped Movie", year=2019)
    d = client.post("/api/files/organize",
                    json={"mode": "inplace", "library_id": lib["id"],
                          "ids": [mid], "dry_run": False}).json()
    assert [x["status"] for x in d["results"]] == ["moved"], d
    new_rel = (store.get_movie(mid) or {}).get("file_path")
    assert new_rel.startswith("raw/Scoped Movie (2019)/")
    assert (root / new_rel).is_file()
    assert (media_root / new_rel).exists() is False   # 默认库不应出现该文件


def test_fs_copy_scoped(media_root, second_library):
    import time as _t
    lib, root = second_library
    _touch(root, "src/a.mkv")
    r = client.post("/api/fs/copy", json={"from": ["src/a.mkv"], "to_dir": "dst",
                                          "dry_run": False,
                                          "library_id": lib["id"]}).json()
    assert r["total"] == 1
    for _ in range(40):
        st = client.get(f"/api/fs/copy/{r['job_id']}").json()
        if st.get("state") in ("done", "failed"):
            break
        _t.sleep(0.05)
    assert st.get("state") == "done", st
    assert (root / "dst" / "a.mkv").is_file()
