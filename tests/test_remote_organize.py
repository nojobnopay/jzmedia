"""Batch D2：远程直读库整理/恢复/缺失检测（fake SMB，无挂载）。"""
import pytest

from app import library_paths, storage, store
from app.routers.files.routes import _organize, _restore_one, clean, missing
from app.storage import smb
from _smb_fake import FakeSmbClient


@pytest.fixture()
def smb_lib(tmp_path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name=f"smborg-{tmp_path.name}", kind="movie", source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_library(lib["id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def _movie(lib, rel, title="新标题", year=2020):
    mid = store.upsert_movie_by_path(rel, library_id=lib["id"])
    store.update_movie_meta(mid, title=title, year=year)
    return mid


def test_organize_remote_inplace(smb_lib):
    lib, root, _fake = smb_lib
    (root / "Messy.Name.2020.1080p.mkv").write_bytes(b"x")
    mid = _movie(lib, "Messy.Name.2020.1080p.mkv")
    # 预览：远程也不误报 source_missing
    prev = _organize("inplace", only={mid}, dry_run=True, library_id=lib["id"])
    assert len(prev["plans"]) == 1
    p = prev["plans"][0]
    assert p["to"] == "新标题 (2020)/新标题 (2020).mkv"
    assert p.get("status") != "source_missing"
    # 执行
    out = _organize("inplace", only={mid}, dry_run=False, library_id=lib["id"])
    assert out["results"][0]["status"] == "moved"
    assert not (root / "Messy.Name.2020.1080p.mkv").exists()
    assert (root / "新标题 (2020)" / "新标题 (2020).mkv").read_bytes() == b"x"
    assert store.get_movie(mid)["file_path"] == "新标题 (2020)/新标题 (2020).mkv"
    # 远程 NFO 同步（D1）随整理落盘
    assert (root / "新标题 (2020)" / "movie.nfo").is_file()


def test_organize_remote_followers_and_extras(smb_lib):
    lib, root, _fake = smb_lib
    d0 = root / "olddir"
    d0.mkdir()
    (d0 / "Old (2020).mkv").write_bytes(b"m")
    (d0 / "Old (2020).chs.srt").write_bytes(b"1\n")
    (d0 / "Old (2020)-trailer.mkv").write_bytes(b"t")
    mid = _movie(lib, "olddir/Old (2020).mkv")
    store.upsert_extra("olddir/Old (2020)-trailer.mkv", mid, "trailer",
                       library_id=lib["id"])
    out = _organize("inplace", only={mid}, dry_run=False, library_id=lib["id"])
    assert out["results"][0]["status"] == "moved"
    # 就地整理保留原父目录（olddir/）；同茎字幕跟随改名，花絮归入 extras/ 子目录
    new_dir = d0 / "新标题 (2020)"
    assert (new_dir / "新标题 (2020).mkv").is_file()
    assert (new_dir / "新标题 (2020).chs.srt").is_file()
    assert (new_dir / "extras" / "新标题 (2020)-trailer.mkv").is_file()
    extras = store.list_extras_by_movie(mid)
    assert len(extras) == 1
    assert extras[0]["file_path"] == "olddir/新标题 (2020)/extras/新标题 (2020)-trailer.mkv"
    # 旧目录内仍有正片（新目录），不整体删除
    assert d0.is_dir() and root.is_dir()


def test_missing_clean_remote_no_false_positive(smb_lib):
    lib, root, _fake = smb_lib
    (root / "Present (2021).mkv").write_bytes(b"x")
    mid = _movie(lib, "Present (2021).mkv", title="Present", year=2021)
    out = missing(library=str(lib["id"]))
    assert all(it["id"] != mid for it in out["items"])
    cl = clean({"dry_run": True, "library_id": lib["id"]})
    assert cl["total"] == 0
    # 文件真的没了才报缺失（离线保护另测）
    (root / "Present (2021).mkv").unlink()
    out2 = missing(library=str(lib["id"]))
    assert any(it["id"] == mid for it in out2["items"])
    cl2 = clean({"dry_run": True, "library_id": lib["id"]})
    assert cl2["total"] == 1


def test_offline_never_reports_deleted(smb_lib):
    lib, root, fake = smb_lib
    (root / "Offline (2022).mkv").write_bytes(b"x")
    mid = _movie(lib, "Offline (2022).mkv", title="Offline", year=2022)
    fake.offline = True
    out = missing(library=str(lib["id"]))
    assert all(it["id"] != mid for it in out["items"])   # 离线 ≠ 已删除
    cl = clean({"dry_run": True, "library_id": lib["id"]})
    assert cl["total"] == 0
    smb.invalidate()


def test_restore_remote(smb_lib):
    lib, root, _fake = smb_lib
    (root / "Restore (2019).mkv").write_bytes(b"x")
    mid = _movie(lib, "Restore (2019).mkv", title="Restore", year=2019)
    # 先搬迁到规范目录
    _organize("inplace", only={mid}, dry_run=False, library_id=lib["id"])
    m = store.get_movie(mid)
    assert m["file_path"] == "Restore (2019)/Restore (2019).mkv"
    # 恢复原始位置
    r = _restore_one(m, dry_run=False)
    assert r["status"] == "restored"
    assert (root / "Restore (2019).mkv").is_file()
    assert store.get_movie(mid)["file_path"] == "Restore (2019).mkv"
