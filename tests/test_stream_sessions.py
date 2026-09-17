"""B2 批次（一致性/并发）回归网：P1-07 prewarm 与在线会话共存。

宿主无 ffmpeg，这里用假进程 + 假探测验证守卫逻辑（真实链路由 docker e2e 验证）。
"""
import pathlib

import pytest

from app import store
from app.config import settings
from app.routers import stream


class FakeProc:
    def __init__(self, alive=True):
        self.alive = alive
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False

    def kill(self):
        self.killed = True
        self.alive = False

    def wait(self, timeout=None):
        self.alive = False
        return 0

    @property
    def pid(self):
        return 4242


@pytest.fixture(autouse=True)
def _clean_globals():
    with stream._sess_lock:
        saved = dict(stream._sessions)
        stream._sessions.clear()
    saved_jobs = dict(stream._prewarm_jobs)
    stream._prewarm_jobs.clear()
    yield
    with stream._sess_lock:
        stream._sessions.clear()
        stream._sessions.update(saved)
    stream._prewarm_jobs.clear()
    stream._prewarm_jobs.update(saved_jobs)


def _put(sid, vid, plan_key, proc):
    with stream._sess_lock:
        stream._sessions[sid] = {"proc": proc, "sdir": "/tmp/x", "vid": vid,
                                 "plan": {}, "plan_key": plan_key,
                                 "last_ping": 0}


def _row(rel, title="T", year=2020) -> int:
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, title=title, year=year)
    return mid


def test_live_sessions_filters_dead_and_matches_plan():
    live = FakeProc(alive=True)
    dead = FakeProc(alive=False)
    _put("s1", 1, "k", live)
    _put("s2", 1, "k-dead", dead)
    _put("s3", 2, "k", FakeProc(alive=True))

    assert stream._find_live_session(1, "k")["sid"] == "s1"
    assert stream._find_live_session(1, "nope") is None
    assert [x["sid"] for x in stream._live_sessions_for(1)] == ["s1"]


def test_prewarm_session_close_detaches_not_kills():
    proc = FakeProc(alive=True)
    with stream._sess_lock:
        stream._sessions["pw1"] = {"proc": proc, "sdir": "/tmp/x", "vid": 1,
                                   "plan": {}, "plan_key": "k", "prewarm": True,
                                   "last_ping": 0}
    r = stream.hls_session_close("pw1")
    assert r == {"session_id": "pw1", "closed": False, "detached": True}
    assert "pw1" in stream._sessions and not proc.terminated

    normal = FakeProc(alive=True)
    _put("n1", 1, "k", normal)
    r2 = stream.hls_session_close("n1")
    assert r2 == {"session_id": "n1", "closed": True}
    assert "n1" not in stream._sessions and normal.terminated


def test_prewarm_refuses_when_live_other_plan(monkeypatch, media_root):
    rel = "film/a.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = _row(rel)
    info = {"playable": True, "container": "matroska", "duration": 120.0,
            "width": 1920, "height": 1080, "vcodec": "h264", "bit_depth": 8,
            "audio": [{"codec": "aac", "channels": 2, "default": 1}], "subs": []}
    monkeypatch.setattr(stream.prewarm, "_media_cached_or_probe", lambda m, abs_p: info)
    _put("live1", mid, "different-plan", FakeProc(alive=True))
    # 该版本会话目录里放个标记文件，验证拒绝路径不清理目录
    with stream._sess_lock:
        stream._sessions["live1"]["sdir"] = str(media_root / "session-dir")
    sdir = media_root / "session-dir"
    sdir.mkdir()
    (sdir / "keep.m4s").write_bytes(b"x")

    job = {"job_id": "j1", "version_id": mid, "status": "queued"}
    stream._prewarm_jobs["j1"] = job
    stream._prewarm_worker("j1", mid, "auto", 0)

    assert job["status"] == "failed"
    assert "正在播放" in job["error"]
    assert (sdir / "keep.m4s").is_file()   # 未清理在线会话目录
    assert "live1" in stream._sessions


def test_prewarm_plan_matches_online_with_same_caps():
    """B5a-7（R12-D2）：同 caps 下 prewarm 与在线播 plan marker 一致，成品可命中。"""
    from app import playback as pb
    from app.routers.stream import _plan_marker, _prewarm_plan
    info = {"playable": True, "container": "matroska", "duration": 120.0,
            "width": 1920, "height": 1080, "vcodec": "h264", "bit_depth": 8,
            "audio": [{"codec": "aac", "channels": 2, "default": 1}], "subs": []}
    caps = {"video": {"h264": True}, "audio": {"aac": True},
            "native_hls": False, "probes": {}}
    d_pre = _prewarm_plan(info, "auto", 0, caps)
    d_online = pb.plan(info, caps=caps, quality="auto", audio_idx=0)
    assert d_pre["method"] == d_online["method"]
    assert _plan_marker(d_pre["plan"], 0, 0) == _plan_marker(d_online["plan"], 0, 0)
    # 缺省 caps 仍走保守默认，不抛错
    assert _prewarm_plan(info, "auto", 0, None)["method"] == d_pre["method"]


def test_prewarm_attaches_same_plan_without_cleanup(monkeypatch, media_root):
    rel = "film/b.mkv"
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    mid = _row(rel, title="B")
    info = {"playable": True, "container": "matroska", "duration": 120.0,
            "width": 1920, "height": 1080, "vcodec": "h264", "bit_depth": 8,
            "audio": [{"codec": "aac", "channels": 2, "default": 1}], "subs": []}
    monkeypatch.setattr(stream.prewarm, "_media_cached_or_probe", lambda m, abs_p: info)
    monkeypatch.setattr(stream.prewarm, "_plan_marker", lambda plan, audio, start: "K")
    sdir = media_root / "film-session"
    sdir.mkdir()
    (sdir / "keep.m4s").write_bytes(b"x")
    with stream._sess_lock:
        stream._sessions["live2"] = {"proc": FakeProc(alive=True), "sdir": str(sdir),
                                     "vid": mid, "plan": {}, "plan_key": "K",
                                     "last_ping": 0}

    job = {"job_id": "j2", "version_id": mid, "status": "queued"}
    stream._prewarm_jobs["j2"] = job
    stream._prewarm_worker("j2", mid, "auto", 0)

    assert job.get("attached") is True
    assert job["status"] == "failed" and "在线会话中断" in job["error"]
    assert (sdir / "keep.m4s").is_file()   # 附着路径不清理目录
    assert "live2" in stream._sessions


# ---------- R12-Q3：会话元数据落盘 + 孤儿收割 ----------

def test_reap_orphans_kills_and_cleans(tmp_path, monkeypatch):
    import json as _json
    import subprocess as _sp
    from app.routers.stream import common

    monkeypatch.setattr(common, "TRANSCODE_DIR", str(tmp_path))
    sdir = tmp_path / "1" / "f_s0"
    sdir.mkdir(parents=True)
    proc = _sp.Popen(["sleep", "30"], cwd=str(sdir))
    try:
        (sdir / "session.json").write_text(_json.dumps({"pid": proc.pid, "sid": "x"}),
                                           encoding="utf-8")
        killed = common._reap_orphans()
        assert killed == 1
        proc.wait(timeout=5)
        assert proc.poll() is not None
        assert not (sdir / "session.json").exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_reap_orphans_dead_pid_cleanup(tmp_path, monkeypatch):
    import json as _json
    from app.routers.stream import common

    monkeypatch.setattr(common, "TRANSCODE_DIR", str(tmp_path))
    sdir = tmp_path / "2" / "f_s0"
    sdir.mkdir(parents=True)
    (sdir / "session.json").write_text(_json.dumps({"pid": 999999999}), encoding="utf-8")
    assert common._reap_orphans() == 0
    assert not (sdir / "session.json").exists()


# ---------- B11 审计补齐：外挂字幕语言 token 判定（R13-B5） ----------

def test_guess_sidecar_lang_token_match():
    from app.routers.stream.subtitles import _guess_sidecar_lang
    assert _guess_sidecar_lang("chs")[0] == "chi"        # 独立 token 命中
    assert _guess_sidecar_lang("zh-hans")[0] == "chi"    # 分隔符切分
    assert _guess_sidecar_lang("chi")[0] == "chi"
    assert _guess_sidecar_lang("eng")[0] == "eng"
    assert _guess_sidecar_lang("")[0] == ""
    # 单字提示只认独立 token：「中配」以前会被子串「中」误判为中文（评审 R13-B5）
    assert _guess_sidecar_lang("中配")[0] == ""
    assert _guess_sidecar_lang("中")[0] == "chi"
