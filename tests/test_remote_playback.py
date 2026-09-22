"""Phase 4：播放链路去 POSIX 假设（URL 输入 / MediaSource / 远程 Range 直发）。"""
import json
import subprocess

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app import playback, store, storage
from app.routers import stream
from app.storage import LocalStorageBackend
from app.storage.base import StorageNotFound, StorageOffline


def test_build_cmd_keeps_url_input():
    plan = {"vcopy": True, "audios": [], "vcodec": "h264", "seg": "fmp4"}
    cmd = playback.build_cmd("http://127.0.0.1:1234/v1/l1/a.mkv", plan, start=0)
    assert cmd[cmd.index("-i") + 1] == "http://127.0.0.1:1234/v1/l1/a.mkv"
    # 本地路径仍绝对化（cwd=会话目录执行）
    cmd2 = playback.build_cmd("rel/x.mkv", plan, start=0)
    assert cmd2[cmd2.index("-i") + 1].startswith("/")


def test_build_cmd_remote_input_gets_probe_limits(monkeypatch):
    """远程输入限探测读取 + HTTP 读超时（慢链路首帧前别白读 5MB）；本地不加。"""
    plan = {"vcopy": True, "audios": [], "vcodec": "h264", "seg": "fmp4"}
    for k in ("FFMPEG_PROBESIZE", "FFMPEG_ANALYZEDURATION", "FFMPEG_RW_TIMEOUT_US"):
        monkeypatch.delenv(k, raising=False)
    remote = playback.build_cmd("http://127.0.0.1:1234/v1/l1/a.mkv", plan, start=0)
    assert "-probesize" in remote and "-analyzeduration" in remote and "-rw_timeout" in remote
    assert remote.index("-probesize") < remote.index("-i")   # 必须在 -i 前生效
    local = playback.build_cmd("rel/x.mkv", plan, start=0)
    assert "-probesize" not in local and "-rw_timeout" not in local
    # env 置 0 = 关闭对应限制
    monkeypatch.setenv("FFMPEG_PROBESIZE", "0")
    monkeypatch.setenv("FFMPEG_ANALYZEDURATION", "0")
    monkeypatch.setenv("FFMPEG_RW_TIMEOUT_US", "0")
    off = playback.build_cmd("http://127.0.0.1:1/a.mkv", plan, start=0)
    assert "-probesize" not in off and "-rw_timeout" not in off


def test_build_cmd_sidecar_input_override():
    plan = {"vcopy": False, "audios": [], "vcodec": "h264", "seg": "fmp4",
            "sub": "burn", "sub_sidecar": "subs/a.idx",
            "sub_sidecar_input": "http://127.0.0.1:9/subs/a.idx"}
    cmd = playback.build_cmd("http://127.0.0.1:9/main.mkv", plan, start=0)
    assert "http://127.0.0.1:9/subs/a.idx" in cmd


def test_probe_accepts_url(monkeypatch):
    from app import media
    captured = {}

    def _run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps({
                "format": {"duration": "2.5", "format_name": "matroska"},
                "streams": [{"codec_type": "video", "codec_name": "h264",
                             "width": 1920, "height": 1080}]}).encode(), b"")

    monkeypatch.setattr(media.subprocess, "run", _run)
    info = media.probe("http://127.0.0.1:9/movie.mkv", size=1234)
    assert info["playable"] is True and info["duration"] == 2.5
    assert captured["cmd"][-1] == "http://127.0.0.1:9/movie.mkv"
    # 0 字节/缺失仍按老语义拦截
    assert media.probe("http://x/y.mkv", size=0)["probe_error"] == "empty file (0 bytes)"


def test_version_source_maps_errors(monkeypatch):
    monkeypatch.setattr(store, "get_playable", lambda kind, vid: {"id": vid, "file_path": "a.mkv"})

    def _missing(library_id, rel):
        raise StorageNotFound("no file")
    monkeypatch.setattr(storage, "media_source", _missing)
    with pytest.raises(HTTPException) as ei:
        stream._version_source(5)
    assert ei.value.status_code == 410

    def _offline(library_id, rel):
        raise StorageOffline("nas down")
    monkeypatch.setattr(storage, "media_source", _offline)
    with pytest.raises(HTTPException) as ei:
        stream._version_source(5)
    assert ei.value.status_code == 503

    monkeypatch.setattr(store, "get_playable", lambda kind, vid: None)
    with pytest.raises(HTTPException) as ei:
        stream._version_source(5)
    assert ei.value.status_code == 404


class _RemoteBackend(LocalStorageBackend):
    """本地实现 + 伪装远程（无 POSIX 路径），验证 blob 的流式 Range 分支。"""

    def abs_path(self, path=""):
        return None


@pytest.fixture()
def remote_blob_app(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    (root / "a.bin").write_bytes(bytes(range(256)) * 40)   # 10240 B
    (root / "c.txt").write_bytes("你好，jzmedia".encode("utf-8"))
    backend = _RemoteBackend({"id": 1, "name": "R", "source": "smb",
                              "path": str(root), "read_only": 0})
    app = FastAPI()

    @app.get("/b")
    def _blob(request: Request):
        from app.routers.blob import media_response
        return media_response(request, backend, "a.bin", filename="a.bin")

    @app.get("/t")
    def _text():
        from app.routers.blob import text_response
        return text_response(backend, "c.txt")

    return TestClient(app), backend


def test_remote_blob_range(remote_blob_app):
    client, _ = remote_blob_app
    r = client.get("/b")
    assert r.status_code == 200 and len(r.content) == 10240
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-length"] == "10240"

    r = client.get("/b", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and len(r.content) == 100
    assert r.headers["content-range"] == "bytes 100-199/10240"
    assert r.content == bytes(range(100, 200))

    r = client.get("/b", headers={"Range": "bytes=999999-"})
    assert r.status_code == 416 and r.headers["content-range"] == "bytes */10240"

    r = client.get("/t")
    assert r.status_code == 200 and r.text == "你好，jzmedia"


def test_remote_blob_missing_and_offline(tmp_path):
    from app.routers.blob import media_response
    app = FastAPI()

    @app.get("/missing")
    def _missing(request: Request):
        backend = _RemoteBackend({"id": 1, "name": "R", "source": "smb",
                                  "path": str(tmp_path), "read_only": 0})
        return media_response(request, backend, "nope.bin")

    class _OfflineBackend(_RemoteBackend):
        def stat(self, path=""):
            raise StorageOffline("nas down")

    backend = _OfflineBackend({"id": 1, "name": "R", "source": "smb",
                               "path": str(tmp_path), "read_only": 0})

    @app.get("/offline")
    def _offline(request: Request):
        return media_response(request, backend, "a.bin")

    client = TestClient(app)
    assert client.get("/missing").status_code == 404
    assert client.get("/offline").status_code == 503
