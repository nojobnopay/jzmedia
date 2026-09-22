"""B2 批次（一致性/并发）回归网：P1-07 prewarm 与在线会话共存。

宿主无 ffmpeg，这里用假进程 + 假探测验证守卫逻辑（真实链路由 docker e2e 验证）。
"""
import pathlib
import time

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


# ---------- B11 审计补齐：媒体负载音轨与产物一致（R11-D3） ----------

def test_media_payload_caps_audio_renditions():
    from app.routers.stream.media import _media_payload
    from app.playback import MAX_AUDIO_RENDITIONS
    info = {"audio": [{"index": i, "codec": "aac", "channels": 2} for i in range(12)],
            "subs": []}
    payload = _media_payload({"id": 1, "file_path": "x.mkv"}, info)
    assert len(payload["audio"]) == MAX_AUDIO_RENDITIONS


# ---------- 2026-09 播放排障：变体 m3u8 随转码增长，不能按 stat 定长直发 ----------

def test_variant_playlist_served_as_content_snapshot(tmp_path, monkeypatch):
    """out_*.m3u8 由 ffmpeg 持续改写：必须按内容快照返回。
    用 FileResponse（Content-Length 来自 stat）时文件随后增长会让 ASGI 抛
    RuntimeError: Response content longer than Content-Length（实测 out_audio0.m3u8）。"""
    from fastapi.responses import FileResponse
    from app.routers.stream import session as session_mod

    sdir = tmp_path / "sess"
    sdir.mkdir()
    body = "#EXTM3U\n#EXT-X-VERSION:7\n#EXTINF:4.0,\nout_audio0_seg00000.m4s\n"
    (sdir / "out_audio0.m3u8").write_text(body, encoding="utf-8")
    monkeypatch.setattr(session_mod, "_get_session", lambda sid: {"sdir": str(sdir)})

    resp = session_mod.hls_session_file("sid", "out_audio0.m3u8")
    assert not isinstance(resp, FileResponse)
    data = resp.body
    assert data == body.encode()
    assert resp.headers["content-length"] == str(len(data))
    assert resp.headers["cache-control"] == "no-store"


def test_read_playlist_stable_retries_when_file_grows(tmp_path, monkeypatch):
    """读取期间文件变化（stat 长度与读入内容不一致）→ 重读快照，不返回半截内容。"""
    import os
    from app.routers.stream import session as session_mod

    f = tmp_path / "out_video.m3u8"
    f.write_text("#EXTM3U\n#EXTINF:4.0,\nout_video_seg00000.m4s\n", encoding="utf-8")
    real = os.path.getsize
    calls = {"n": 0}

    def fake_getsize(p, *a, **k):
        if str(p) == str(f):
            n = calls["n"]
            calls["n"] += 1
            return real(p, *a, **k) - (1 if n == 0 else 0)   # 首次模拟“读到一半又长了”
        return real(p, *a, **k)

    monkeypatch.setattr(os.path, "getsize", fake_getsize)
    monkeypatch.setattr(session_mod.time, "sleep", lambda *_: None)
    data = session_mod._read_playlist_stable(str(f))
    assert data == f.read_bytes()
    assert calls["n"] >= 2


# ---------- 2026-09 关窗音频泄漏：建会话期间 abort → 未认领会话快速收割 ----------

def test_unclaimed_session_reaped_after_grace():
    """客户端从未取流（关窗 abort 拿不到 sid）：宽限期后必须回收孤儿 ffmpeg。"""
    from app.routers.stream import common

    now = 1_000_000.0
    with stream._sess_lock:
        stream._sessions["u1"] = {"proc": FakeProc(alive=True), "sdir": "/tmp/x",
                                  "vid": 1, "plan": {}, "plan_key": "k",
                                  "last_ping": now, "created": now, "unclaimed": True}
    assert common._dead_sessions(now + common._UNCLAIMED_TTL - 1) == []
    assert common._dead_sessions(now + common._UNCLAIMED_TTL + 1) == ["u1"]


def test_get_session_claims_and_resets_grace():
    """首次 playlist/分片/心跳请求即认领：之后按普通 idle 规则，不再宽限回收。"""
    from app.routers.stream import common

    now = time.time()
    with stream._sess_lock:
        stream._sessions["u2"] = {"proc": FakeProc(alive=True), "sdir": "/tmp/x",
                                  "vid": 1, "plan": {}, "plan_key": "k",
                                  "last_ping": now - 100, "created": now - 100,
                                  "unclaimed": True}
    sess = common._get_session("u2")
    assert sess["unclaimed"] is False
    assert common._dead_sessions(time.time() + common._UNCLAIMED_TTL + 1) == []
    assert common._dead_sessions(time.time() + common._SESS_IDLE + 1) == ["u2"]


# ---------- P1：首屏等待分片数按 plan 自适应 ----------

def test_min_segs_copy_fast_transcode_cautious(monkeypatch):
    """copy/remux 1 片即回（原固定 3 片=27s 内容）；视频重编/烧录留 2 片。"""
    from app.routers.stream import common

    monkeypatch.delenv("MIN_SEGS_COPY", raising=False)
    monkeypatch.delenv("MIN_SEGS_TRANSCODE", raising=False)
    assert common._min_segs({"vcopy": True}) == 1
    assert common._min_segs({"vcopy": False}) == 2
    assert common._min_segs({"vcopy": True, "sub": "burn"}) == 2   # 烧录=重编码
    assert common._min_segs(None) == 2                              # 缺省按保守（转码）
    monkeypatch.setenv("MIN_SEGS_COPY", "3")
    monkeypatch.setenv("MIN_SEGS_TRANSCODE", "1")
    assert common._min_segs({"vcopy": True}) == 3
    assert common._min_segs({"vcopy": False}) == 1
    monkeypatch.setenv("MIN_SEGS_COPY", "abc")   # 坏值回落默认
    assert common._min_segs({"vcopy": True}) == 1


# ---------- P1：转码缓存总量限额（TRANSCODE_CACHE_GB）----------

def _mk_cache_dir(root: pathlib.Path, vid: str, name: str, size: int, mtime: float):
    d = root / vid / name
    d.mkdir(parents=True)
    (d / "video_seg00000.m4s").write_bytes(b"x" * size)
    import os as _os
    _os.utime(d, (mtime, mtime))
    return d


def test_transcode_cache_cap_evicts_oldest_skips_active_and_fresh(tmp_path, monkeypatch):
    from app.routers.stream import common

    monkeypatch.setattr(common, "TRANSCODE_DIR", str(tmp_path))
    now = time.time()
    old = _mk_cache_dir(tmp_path, "1", "fcopy_s0", 4 << 20, now - 3600)
    mid = _mk_cache_dir(tmp_path, "1", "fcopy_s100", 4 << 20, now - 1800)
    fresh = _mk_cache_dir(tmp_path, "2", "fh720_s0", 4 << 20, now - 10)   # <5min 保护期
    # 活动会话目录（进程在跑）即使很旧也不许删
    with stream._sess_lock:
        stream._sessions["act"] = {"proc": FakeProc(alive=True), "sdir": str(mid),
                                   "vid": 1, "plan": {}, "plan_key": "k",
                                   "last_ping": now}
    r = common._evict_transcode_cache(now=now, cap=8 << 20)
    assert r["total"] == 12 << 20 and r["removed"] == 1 and r["freed"] == 4 << 20
    assert not old.exists() and mid.exists() and fresh.exists()
    # 会话退出且超过活跃窗口后，mid 变成可淘汰
    with stream._sess_lock:
        stream._sessions["act"]["proc"] = FakeProc(alive=False)
        stream._sessions["act"]["last_ping"] = now - common._SESS_IDLE - 1
    r2 = common._evict_transcode_cache(now=now, cap=1 << 20)
    assert not mid.exists() and r2["removed"] == 1
    assert fresh.exists()   # 新目录始终受保护


def test_cache_clean_endpoint_dry_run_then_execute(tmp_path, monkeypatch):
    from app.routers.stream import common

    monkeypatch.setattr(common, "TRANSCODE_DIR", str(tmp_path))
    now = time.time()
    d1 = _mk_cache_dir(tmp_path, "1", "fcopy_s0", 2 << 20, now - 3600)
    dry = common.clean_stream_cache(common.CacheCleanBody(dry_run=True))
    assert dry["dry_run"] is True and dry["removed"] == 1 and d1.exists()
    run = common.clean_stream_cache(common.CacheCleanBody(dry_run=False))
    assert run["removed"] == 1 and not d1.exists()
