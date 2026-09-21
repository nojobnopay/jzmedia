"""Batch B：远程直读库字幕（外挂枚举/转换输入/直发/VobSub 烧录第二输入）。"""
import pytest
from fastapi.testclient import TestClient

from app import library_paths, storage, store
from app.main import app
from app.routers.stream import subtitles as subs
from app.storage import smb
from _smb_fake import FakeSmbClient


@pytest.fixture()
def smb_lib(tmp_path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    fake = FakeSmbClient(root)
    monkeypatch.setattr(smb, "smbclient", fake)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    lib = store.create_library(name=f"smbsub-{tmp_path.name}", kind="movie", source="smb",
                               smb={"host": "nas", "share": "video",
                                    "username": "u", "password": "pw"})
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()
    smb.invalidate()


def _movie(lib, rel):
    return {"id": store.upsert_movie_by_path(rel, library_id=lib["id"]),
            "library_id": lib["id"], "file_path": rel, "kind": "movie"}


def test_sub_list_sidecar_remote(smb_lib):
    lib, root, _fake = smb_lib
    (root / "subs").mkdir()
    (root / "Movie (2000).mkv").write_bytes(b"x")
    (root / "Movie (2000).chs.srt").write_bytes(b"1\n")
    (root / "subs" / "Movie (2000).sup").write_bytes(b"PGS")
    m = _movie(lib, "Movie (2000).mkv")
    items = subs._sub_list(m, {"subs": []})
    assert len(items) == 2
    srt = next(i for i in items if i["codec"] == "srt")
    assert srt["source"] == "sidecar" and srt["default"] == 1   # 无内嵌 → 中文外挂自动默认
    assert srt["sidecar"] == "Movie (2000).chs.srt"
    sup = next(i for i in items if i["codec"] == "pgs")
    assert sup["image"] == 1 and sup["sidecar"] == "subs/Movie (2000).sup"


def test_decide_payload_sidecar_list_once(smb_lib, monkeypatch):
    """P2：decide 只枚举一次外挂字幕目录（此前 _media_payload 被调两次）。"""
    from app.routers.stream.media import PlaybackQuery, _decide_payload
    monkeypatch.setenv("SMB_META_TTL", "0")   # 关缓存，纯看调用次数
    lib, root, fake = smb_lib
    (root / "Movie (2000).mkv").write_bytes(b"x")
    m = _movie(lib, "Movie (2000).mkv")
    info = {"playable": True, "container": "mkv", "vcodec": "h264",
            "audio": [{"index": 1, "codec": "aac", "channels": 2}],
            "subs": [], "duration": 60, "width": 1920, "height": 1080}
    fake.counts.clear()
    out = _decide_payload(m, info, PlaybackQuery())
    assert fake.counts.get("scandir", 0) == 4   # src + subs/Subs/字幕 各一次
    assert "media" in out and out["media"]["subs"] == []


def test_sidecar_src_url_and_missing(smb_lib):
    lib, root, _fake = smb_lib
    (root / "A (2001).ass").write_bytes(b"[Script Info]")
    m = _movie(lib, "A (2001).mkv")
    src = subs._sidecar_src(m, "A (2001).ass")
    assert src.input.startswith("http://127.0.0.1:")
    assert src.local_path is None
    with pytest.raises(Exception) as ei:
        subs._sidecar_src(m, "missing.ass")
    assert getattr(ei.value, "status_code", 0) == 404


def test_convert_sidecar_uses_remote_input(smb_lib, monkeypatch):
    lib, root, _fake = smb_lib
    (root / "C (2002).srt").write_bytes(b"1\n")
    m = _movie(lib, "C (2002).mkv")
    captured = {}

    def _fake_convert(dest, argv_of_tmp, timeout, err_msg):
        argv = argv_of_tmp(dest + ".tmp")
        captured["argv"] = argv
        with open(dest, "wb") as fh:
            fh.write(b"WEBVTT")
    monkeypatch.setattr(subs, "_run_ffmpeg_to_temp", _fake_convert)
    src = subs._sidecar_src(m, "C (2002).srt")
    out = subs._convert_sidecar(src, m["id"], "vtt")
    assert out.endswith(".vtt")
    assert captured["argv"][captured["argv"].index("-i") + 1] == src.input


def test_extract_embedded_uses_remote_input(smb_lib, monkeypatch):
    lib, root, _fake = smb_lib
    (root / "D (2003).mkv").write_bytes(b"x")
    m = _movie(lib, "D (2003).mkv")
    captured = {}

    def _fake_convert(dest, argv_of_tmp, timeout, err_msg):
        argv = argv_of_tmp(dest + ".tmp")
        captured["argv"] = argv
        with open(dest, "wb") as fh:
            fh.write(b"WEBVTT")
    monkeypatch.setattr(subs, "_run_ffmpeg_to_temp", _fake_convert)
    src = storage.media_source(lib["id"], "D (2003).mkv")
    subs._extract_embedded(src, m["id"], {"ff_index": 2}, 0, "vtt")
    assert captured["argv"][captured["argv"].index("-i") + 1] == src.input


def test_serve_sidecar_sup_and_ass_via_api(smb_lib, monkeypatch):
    lib, root, _fake = smb_lib
    (root / "E (2004).mkv").write_bytes(b"x")
    (root / "E (2004).sup").write_bytes(b"PGS-BYTES")
    (root / "E (2004).ass").write_bytes(b"[Script Info]\nTitle: t")
    m = _movie(lib, "E (2004).mkv")
    monkeypatch.setattr(subs, "_media_cached_or_probe",
                        lambda mm, src: {"playable": True, "subs": []})
    client = TestClient(app)
    sup = next(i for i, x in enumerate(subs._sub_list(m, {"subs": []}))
               if x["codec"] == "pgs")
    r = client.get(f"/api/stream/{m['id']}/sub/{sup}.sup")
    assert r.status_code == 200 and r.content == b"PGS-BYTES"
    ass = next(i for i, x in enumerate(subs._sub_list(m, {"subs": []}))
               if x["codec"] == "ass")
    r2 = client.get(f"/api/stream/{m['id']}/sub/{ass}.ass")
    assert r2.status_code == 200 and b"Script Info" in r2.content
    # 远程 Range 直发（浏览器/播放器拖取）
    r3 = client.get(f"/api/stream/{m['id']}/sub/{sup}.sup",
                    headers={"Range": "bytes=0-2"})
    assert r3.status_code == 206 and r3.content == b"PGS"
