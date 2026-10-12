"""MP4 短片使用独立租约/完整缓存；不能误当作 start=0 全片缓存。"""
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.stream import common, session


@pytest.fixture
def clips(tmp_path, monkeypatch):
    saved = dict(common._sessions)
    common._sessions.clear()
    created = []
    monkeypatch.setattr(common, "TRANSCODE_DIR", str(tmp_path))
    monkeypatch.setattr(session, "_hls_sem", threading.BoundedSemaphore(1))
    monkeypatch.setattr(session, "_version_source", lambda vid, kind: (
        {"id": vid, "kind": kind, "library_id": 1, "file_path": "synthetic.mkv"},
        SimpleNamespace(input="unused", size=100, mtime=1)))
    monkeypatch.setattr(session, "_media_cached_or_probe", lambda *a: {"playable": True, "duration": 90})
    monkeypatch.setattr(session, "_media_payload", lambda *a: {})
    monkeypatch.setattr(session, "_media_start_for", lambda vid, src, start, plan, kind: start)
    monkeypatch.setattr(session, "_ffmpeg_ok", lambda: True)
    monkeypatch.setattr(session, "_mp4_duration", lambda _dir: 30.1)
    monkeypatch.setattr(session._playback, "hw_backend", lambda: "")
    monkeypatch.setattr(session._playback, "build_cmd", lambda *a, **kw: ["fake-mp4"])
    monkeypatch.setattr(session._playback, "plan", lambda *a, **kw: {
        "method": "video_transcode", "reasons": [], "plan": {"seg": "mp4", "vcopy": False,
        "acopy": False, "sub": "none", "audio_idx": kw["audio_idx"], "clip_seconds": 30}})

    class Finished:
        pid = 999999999
        def poll(self): return 0

    def produce(cmd, **kw):
        directory = Path(kw["cwd"])
        (directory / "media.tmp.mp4").write_bytes(bytes(range(256)) * 20)
        created.append(directory)
        return Finished()
    monkeypatch.setattr(session.subprocess, "Popen", produce)
    yield created
    common.shutdown_sessions()
    assert not common._tasks
    common._sessions.update(saved)


def test_mp4_clips_have_immutable_range_output_and_exact_cache(clips):
    first = session.hls_session_create(1, session.SessionBody())
    assert first["complete"] and first["media_url"].endswith("media.mp4")
    assert first["playlist_url"] == ""
    assert first["clip_duration"] == 30.1
    diagnostic = session.hls_session_debug(first["session_id"])
    assert diagnostic["segments"] == diagnostic["video_segments"] == 1
    assert diagnostic["finished"] and diagnostic["complete"]
    assert not (clips[0] / "media.tmp.mp4").exists()
    assert common._sessions[first["session_id"]]["task"]["done"].is_set()
    app = FastAPI()
    app.include_router(session.router)
    client = TestClient(app)
    response = client.get(first["media_url"], headers={"Range": "bytes=100-199"})
    assert response.status_code == 206
    assert response.content == bytes(range(100, 200))
    assert response.headers["content-range"] == "bytes 100-199/5120"
    assert response.headers["content-disposition"].startswith("inline")
    assert client.get(first["media_url"].replace("media.mp4", "media.tmp.mp4")).status_code == 422
    again = session.hls_session_create(1, session.SessionBody())
    assert again["session_id"] != first["session_id"] and len(clips) == 1
    # 完工短片释放并发槽；非零起点不命中第一个 30 秒的缓存。
    second = session.hls_session_create(1, session.SessionBody(start=60))
    assert len(clips) == 2 and second["media_start"] == 60 and second["initial_time"] == 0
    assert second["complete"]
    other = session.hls_session_create(1, session.SessionBody(start=60, audio=1))
    assert len(clips) == 3 and other["session_id"] != second["session_id"]


def test_aborted_or_unmarked_mp4_is_never_a_complete_cache(tmp_path):
    marker = common._plan_marker({"seg": "mp4", "clip_seconds": 30}, 0, 0)
    (tmp_path / "media.tmp.mp4").write_bytes(b"partial")
    (tmp_path / "complete.json").write_text(marker)
    assert not common._session_complete(str(tmp_path), marker)
    (tmp_path / "media.mp4").write_bytes(b"")
    assert not common._session_complete(str(tmp_path), marker)
    (tmp_path / "media.mp4").write_bytes(b"completed")
    assert common._session_complete(str(tmp_path), marker)
    assert not common._session_complete(str(tmp_path), marker.replace('30', '60'))


def test_mp4_duration_cache_uses_actual_probe_and_rejects_nonfinite(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        return CompletedProcess(cmd, 0, b'{"format":{"duration":"30.123"}}', b'')
    monkeypatch.setattr(session.subprocess, 'run', run)
    monkeypatch.setattr(session._media, 'ffprobe_bin', lambda: 'fake-ffprobe')
    assert session._mp4_duration(str(tmp_path)) == 30.123
    assert session._mp4_duration(str(tmp_path)) == 30.123
    assert len(calls) == 1
    (tmp_path / 'media-info.json').write_text('{"duration":"NaN"}')
    assert session._mp4_duration(str(tmp_path)) == 30.123
    assert len(calls) == 2
