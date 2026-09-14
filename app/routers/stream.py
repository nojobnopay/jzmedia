"""在线播放（Plex 式三档）：decide 决策 + 媒体信息 + 断点进度 + HLS 切片。

- 播放单位是版本行 id（每个文件版本即一行 movies，多版本选播即选 id）。
- 媒体信息懒探测 + media_info 缓存（ffprobe 本地派生，不污染 TMDB 镜像，不进 FTS）。
- 伪造文件（0 字节/probe 失败）→ playable=false，decide 422，前端置灰禁用。
- HLS 渐进式会话（P-A，抄 Plex chunked transcode）：ffmpeg 后台转，前 3 分片落盘即回
  playlist（增长型、无 ENDLIST，hls.js 照播）；心跳保活 10min，关播/超时杀进程；
  seek=关旧开新。旧直连 master/seg 口保留（内部走同一会话机制，匿名会话）。
- 最大 2 路并发（超限 429，抄 Plex TranscodeCountLimit），会话目录 24h TTL。
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from .. import media as _media
from .. import store
from ..config import settings
from ..db import TRANSCODE_DIR

router = APIRouter(prefix="/api/stream")

_hls_sem = threading.Semaphore(2)
_SEG_RE = re.compile(r"^seg\d+\.ts$")
_TTL = 24 * 3600
_MIN_SEGS = 3          # 首屏等待分片数（约 18s 内容）
_SESS_IDLE = 600       # 会话无心跳保活期（秒）
_sessions: dict[str, dict] = {}
_sess_lock = threading.RLock()


def _version_abs(version_id: int) -> tuple[dict, str]:
    """版本行 + 磁盘绝对路径。行不存在 404；文件缺失 410（前端按无效文件置灰）。"""
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    m = store.get_movie(vid)
    if not m:
        raise HTTPException(404, "version not found")
    abs_p = os.path.join(settings.media_root, m["file_path"])
    if not os.path.isfile(abs_p):
        raise HTTPException(410, "file missing")
    return m, abs_p


def _media_cached_or_probe(m: dict, abs_p: str) -> dict:
    cached = store.get_media_info(int(m["id"]))
    if cached and int(cached.get("probed_at") or 0) > 0:
        return cached
    info = _media.probe(abs_p)
    return store.upsert_media_info(int(m["id"]), info)


@router.get("/{version_id}/media")
def stream_media(version_id: int, refresh: int = 0):
    """版本媒体信息（徽章行用）：容器/时长/分辨率/编码/音字幕列表/playable。"""
    m, abs_p = _version_abs(version_id)
    if refresh:
        info = store.upsert_media_info(int(m["id"]), _media.probe(abs_p))
    else:
        info = _media_cached_or_probe(m, abs_p)
    return {"version_id": int(m["id"]), "file_path": m["file_path"],
            "title": m.get("title", ""), **info,
            "duration_text": _media.fmt_duration(info.get("duration") or 0)}


@router.get("/{version_id}/decide")
def stream_decide(version_id: int, quality: str = "original",
                  audio: int = 0, sub: int | None = None,
                  client: str = "web"):
    """三档决策：direct（零 CPU 走 blob）/ remux（-c copy）/ transcode / blocked。
    direct_url 直接复用 GET /api/movies/{id}/blob（Range 直发）；hls_url 供切片口。
    client=kodi 时为外部播放器直通：可播即 direct（原盘直链），不做浏览器兼容判定。"""
    m, abs_p = _version_abs(version_id)
    info = _media_cached_or_probe(m, abs_p)
    if not info.get("playable"):
        raise HTTPException(422, f"unplayable: {info.get('probe_error') or 'probe failed'}")
    cli = (client or "web").strip().lower()
    if cli == "kodi":
        blob_url = f"/api/movies/{int(m['id'])}/blob?name={m['file_path']}"
        return {"version_id": int(m["id"]), "method": "direct", "reasons": ["kodi_passthrough"],
                "plan": {"vcopy": True, "acopy": True, "height": 0, "sub": "none",
                         "audio_idx": 0, "sub_idx": None},
                "media": info, "direct_url": blob_url, "hls_url": ""}
    d = _media.decide(info, quality=quality, audio_idx=audio, sub_idx=sub, client=cli)
    method = d["method"]
    blob_url = f"/api/movies/{int(m['id'])}/blob?name={m['file_path']}"
    qs = f"?quality={quality}&audio={audio}" + (f"&sub={sub}" if sub is not None else "")
    hls_url = f"/api/stream/{int(m['id'])}/master.m3u8{qs}"
    return {"version_id": int(m["id"]), "method": method, "reasons": d["reasons"],
            "plan": d["plan"], "media": info,
            "direct_url": blob_url if method == "direct" else "",
            "hls_url": hls_url if method in ("remux", "transcode") else ""}


class ProgressBody(BaseModel):
    position: float = 0
    duration: float = 0


@router.get("/progress")
def progress_get(version_id: int):
    """读单版本断点。无行返回 {position:0,...}（前端视为从头）。"""
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    if not store.get_movie(vid):
        raise HTTPException(404, "version not found")
    p = store.get_progress(vid)
    if not p:
        return {"version_id": vid, "position": 0, "duration": 0, "updated_at": 0,
                "position_text": "0:00"}
    return {**p, "position_text": _media.fmt_duration(p.get("position") or 0)}


@router.post("/progress")
def progress_save(body: ProgressBody, version_id: int):
    """写单版本断点（position/duration 秒）。伪造/缺失版本 404/410，不落脏行。"""
    _version_abs(version_id)
    p = store.save_progress(int(version_id), body.position, body.duration)
    return {**p, "position_text": _media.fmt_duration(p.get("position") or 0)}


@router.delete("/progress")
def progress_clear(version_id: int):
    """清单版本断点（用户选“从头开始”）。"""
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    return {"version_id": vid, "cleared": store.clear_progress(vid)}


@router.get("/versions")
def stream_versions(movie_id: int, quality: str = "original",
                    client: str = "web"):
    """同片全版本一次取齐（选版器用）：每版本 media/method/reasons/score/duration_text。
    best_version_id 为 client 下最优（浏览器永不自动选 DV/4K，抄 Plex 选版）。
    缺失文件记 playable=false，不抛错（前端置灰）。"""
    try:
        mid = int(movie_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad movie_id")
    m = store.get_movie(mid)
    if not m:
        raise HTTPException(404, "movie not found")
    cli = (client or "web").strip().lower()
    items = []
    for v in (m.get("versions") or [{"id": m["id"], "file_path": m["file_path"],
                                     "edition": m.get("edition") or "",
                                     "spec": m.get("spec") or ""}]):
        try:
            vid = int(v["id"])
        except (TypeError, ValueError):
            continue
        vm = store.get_movie(vid)
        if not vm:
            continue
        abs_p = os.path.join(settings.media_root, vm["file_path"])
        if not os.path.isfile(abs_p):
            items.append({"version_id": vid, "file_path": vm["file_path"],
                          "edition": v.get("edition") or "", "spec": v.get("spec") or "",
                          "playable": False, "probe_error": "file missing",
                          "method": "blocked", "reasons": ["unplayable"],
                          "score": [9, 0], "duration_text": ""})
            continue
        info = _media_cached_or_probe(vm, abs_p)
        if not info.get("playable"):
            items.append({"version_id": vid, "file_path": vm["file_path"],
                          "edition": v.get("edition") or "", "spec": v.get("spec") or "",
                          "playable": False,
                          "probe_error": info.get("probe_error") or "probe failed",
                          "method": "blocked", "reasons": ["unplayable"],
                          "score": [9, 0], "duration_text": ""})
            continue
        if cli == "kodi":
            method, reasons = "direct", ["kodi_passthrough"]
            score = (0, 0)
        else:
            d = _media.decide(info, quality=quality, client=cli)
            method, reasons = d["method"], d["reasons"]
            score = _media.score_for_client(info, quality=quality, client=cli)
        items.append({"version_id": vid, "file_path": vm["file_path"],
                      "edition": v.get("edition") or "", "spec": v.get("spec") or "",
                      "playable": True, "probe_error": "",
                      "method": method, "reasons": reasons,
                      "score": list(score),
                      "duration": info.get("duration") or 0,
                      "duration_text": _media.fmt_duration(info.get("duration") or 0),
                      "height": info.get("height") or 0,
                      "vcodec": info.get("vcodec") or "",
                      "dv_profile": info.get("dv_profile") or 0,
                      "audio_count": len(info.get("audio") or []),
                      "sub_count": len(info.get("subs") or [])})
    best = None
    for it in sorted(items, key=lambda x: (x["score"], x["version_id"])):
        if it["method"] != "blocked":
            best = it["version_id"]
            break
    return {"movie_id": mid, "quality": quality, "client": cli,
            "versions": items, "best_version_id": best}


class ProbeMissingBody(BaseModel):
    limit: int = 50
    force: bool = False

@router.post("/probe-missing")
def probe_missing(body: ProbeMissingBody | None = None):
    """后台补探测：给无 media_info 行（或 force 全量）的版本跑 ffprobe。
    离线本地，不调 TMDB；0 字节/失败记 playable=0，不抛错。供设置页/详情页调用。"""
    limit = max(1, min(int((body.limit if body else 50) or 50), 500))
    force = bool(body.force) if body else False
    rows = store.list_movies(grouped=False, limit=100000)
    if not force:
        rows = [r for r in rows if not store.get_media_info(int(r["id"]))]
    rows = rows[:limit]
    done, failed, unplayable = [], [], []
    for r in rows:
        vid = int(r["id"])
        abs_p = os.path.join(settings.media_root, r["file_path"])
        try:
            info = store.upsert_media_info(vid, _media.probe(abs_p))
        except Exception as e:
            failed.append({"id": vid, "error": str(e)[:200]})
            continue
        item = {"id": vid, "playable": bool(info.get("playable")),
                "duration": info.get("duration") or 0}
        (done if info.get("playable") else unplayable).append(item)
    return {"total": len(rows), "ok": len(done), "unplayable": len(unplayable),
            "failed": failed, "results": done, "unplayable_items": unplayable}


def _ffmpeg_ok() -> bool:
    return bool(shutil.which("ffmpeg"))


def _seg_count(sdir: str) -> int:
    try:
        return sum(1 for n in os.listdir(sdir) if _SEG_RE.match(n or ""))
    except OSError:
        return 0


def _kill_proc(proc) -> None:
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _drop_session(sid: str, kill: bool = True) -> None:
    with _sess_lock:
        sess = _sessions.pop(sid, None)
    if sess and kill:
        _kill_proc(sess.get("proc"))


def _sweeper() -> None:
    """后台收尸：无心跳超期 / 进程已退出超期 → 杀进程删会话（分片留 TTL 清理）。"""
    while True:
        time.sleep(60)
        now = time.time()
        dead = []
        with _sess_lock:
            for sid, s in _sessions.items():
                proc = s.get("proc")
                exited = proc is not None and proc.poll() is not None
                idle = now - float(s.get("last_ping") or now)
                if idle > _SESS_IDLE or (exited and idle > 300):
                    dead.append(sid)
        for sid in dead:
            _drop_session(sid, kill=True)
        _purge_old()


_sweeper_thread = threading.Thread(target=_sweeper, daemon=True)
_sweeper_thread.start()


def _session_dir(version_id: int, quality: str, audio: int, start: float) -> str:
    q = re.sub(r"[^a-z0-9]+", "", (quality or "original").strip().lower()) or "original"
    try:
        st = max(0, int(float(start or 0)))
    except (TypeError, ValueError):
        st = 0
    d = os.path.join(TRANSCODE_DIR, str(int(version_id)), f"{q}_a{int(audio or 0)}_s{st}")
    os.makedirs(d, exist_ok=True)
    return d


def _purge_old() -> None:
    """转码会话 TTL 清理（best-effort，失败自吞）。"""
    try:
        now = time.time()
        for vid in os.listdir(TRANSCODE_DIR):
            vd = os.path.join(TRANSCODE_DIR, vid)
            if not os.path.isdir(vd):
                continue
            for sess in os.listdir(vd):
                sd = os.path.join(vd, sess)
                try:
                    if os.path.isdir(sd) and now - os.path.getmtime(sd) > _TTL:
                        shutil.rmtree(sd, ignore_errors=True)
                except OSError:
                    continue
    except OSError:
        pass


def _spawn_session(version_id: int, quality: str, audio: int,
                   sub: int | None, start: float) -> tuple[str, str, dict]:
    """起后台转码会话（渐进式）：校验→decide→Popen→等前 _MIN_SEGS 分片。
    返回 (session_id, session_dir, decide)。direct/burn/无 ffmpeg 等直接抛对应 HTTP 状态。"""
    m, abs_p = _version_abs(version_id)
    info = _media_cached_or_probe(m, abs_p)
    if not info.get("playable"):
        raise HTTPException(422, f"unplayable: {info.get('probe_error') or 'probe failed'}")
    d = _media.decide(info, quality=quality, audio_idx=audio, sub_idx=sub)
    if d["method"] == "blocked":
        raise HTTPException(422, "unplayable")
    if d["method"] == "direct":
        raise HTTPException(400, "use direct_url (Direct Play, no HLS needed)")
    if (d.get("plan") or {}).get("sub") == "burn":
        raise HTTPException(415, "image subtitle needs burn-in: download original and use VLC/Kodi")
    if not _ffmpeg_ok():
        raise HTTPException(501, "ffmpeg not installed in server image")
    if not _hls_sem.acquire(blocking=False):
        raise HTTPException(429, "transcode slots full (max 2), try later")
    sdir = ""
    try:
        _purge_old()
        sdir = _session_dir(int(m["id"]), quality, audio, start)
        for n in os.listdir(sdir):
            try:
                os.remove(os.path.join(sdir, n))
            except OSError:
                pass
        playlist = os.path.join(sdir, "master.m3u8")
        cmd = _media.build_cmd(abs_p, d["plan"], playlist, start=start)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            raise HTTPException(501, "ffmpeg not installed in server image")
        except Exception as e:
            raise HTTPException(500, f"transcode spawn failed: {e}")
        sid = uuid.uuid4().hex[:16]
        with _sess_lock:
            _sessions[sid] = {"proc": proc, "sdir": sdir, "vid": int(m["id"]),
                              "plan": d["plan"], "last_ping": time.time()}
        # 等前 _MIN_SEGS 分片（remux 秒出；转码按实际速度）：首画面不等整片
        deadline = time.time() + 300
        while time.time() < deadline:
            if _seg_count(sdir) >= _MIN_SEGS:
                break
            if proc.poll() is not None:
                break
            time.sleep(1)
        if _seg_count(sdir) == 0 or not os.path.isfile(playlist):
            _drop_session(sid, kill=True)
            raise HTTPException(500, "transcode failed (no segments)")
        try:
            with open(os.path.join(sdir, "plan.json"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"q": quality, "a": audio, "s": sub,
                                     "plan": d["plan"], "sid": sid}, sort_keys=True))
        except OSError:
            pass
        return sid, sdir, d
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"transcode failed: {e}")
    finally:
        _hls_sem.release()


def _playlist_text(sdir: str) -> str:
    playlist = os.path.join(sdir, "master.m3u8")
    try:
        with open(playlist, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        raise HTTPException(500, f"read playlist failed: {e}")
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and s.endswith(".ts"):
            out.append(f"seg/{os.path.basename(s)}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def _get_session(sid: str) -> dict:
    with _sess_lock:
        sess = _sessions.get(sid or "")
    if not sess:
        raise HTTPException(404, "no such transcode session (re-POST sessions)")
    sess["last_ping"] = time.time()
    return sess


class SessionBody(BaseModel):
    quality: str = "720p"
    audio: int = 0
    sub: int | None = None
    start: float = 0


@router.post("/{version_id}/sessions")
def hls_session_create(version_id: int, body: SessionBody | None = None):
    """开渐进式转码会话：后台 ffmpeg，前 3 分片就绪即回（首画面不等整片）。
    seek/换清晰度/换音轨 = 关旧开新。用 ping 保活，DELETE 关播。"""
    body = body or SessionBody()
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    sid, _sdir, d = _spawn_session(vid, body.quality, body.audio, body.sub, body.start)
    return {"session_id": sid,
            "playlist_url": f"/api/stream/sessions/{sid}/master.m3u8",
            "method": d["method"], "reasons": d["reasons"], "plan": d["plan"]}


@router.get("/sessions/{sid}/master.m3u8")
def hls_session_playlist(sid: str):
    """会话播放列表（增长型；转码完成前无 ENDLIST，hls.js 照播）。每次取即心跳。"""
    sess = _get_session(sid)
    return PlainTextResponse(_playlist_text(sess["sdir"]),
                             media_type="application/vnd.apple.mpegurl")


@router.get("/sessions/{sid}/seg/{name}")
def hls_session_segment(sid: str, name: str):
    """会话分片：未就绪等最多 25s（追转码进度），会话死亡则 404。"""
    if not _SEG_RE.match(name or ""):
        raise HTTPException(422, "bad segment name")
    sess = _get_session(sid)
    dest = os.path.join(sess["sdir"], name)
    if os.path.normpath(dest) != dest or not dest.startswith(sess["sdir"]):
        raise HTTPException(422, "bad segment name")
    deadline = time.time() + 25
    while not os.path.isfile(dest) and time.time() < deadline:
        proc = sess.get("proc")
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(0.5)
    if not os.path.isfile(dest):
        raise HTTPException(404, "segment not ready (session may have ended)")
    return FileResponse(dest, media_type="video/MP2T", filename=name)


@router.post("/sessions/{sid}/ping")
def hls_session_ping(sid: str):
    """心跳保活（播放器每 ~10s 调一次；10min 无心跳会话被回收）。"""
    sess = _get_session(sid)
    proc = sess.get("proc")
    running = proc is not None and proc.poll() is None
    return {"session_id": sid, "running": running,
            "segments": _seg_count(sess["sdir"])}


@router.delete("/sessions/{sid}")
def hls_session_close(sid: str):
    """关播：杀转码进程删会话（分片留 24h TTL，供同参数重进复用）。"""
    with _sess_lock:
        existed = sid in _sessions
    _drop_session(sid, kill=True)
    return {"session_id": sid, "closed": existed}


def _ensure_hls(version_id: int, quality: str, audio: int,
                sub: int | None, start: float) -> tuple[str, dict]:
    """旧直连口兼容层：内部走同一会话机制（匿名会话，无需 ping，靠 TTL/清道夫回收）。
    行为变化：只等前 _MIN_SEGS 分片即回，不再整片同步（首画面快，语义见 sessions 口）。"""
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    _sid, sdir, d = _spawn_session(vid, quality, audio, sub, start)
    return sdir, d


@router.get("/{version_id}/master.m3u8")
def hls_master(version_id: int, quality: str = "original",
               audio: int = 0, sub: int | None = None, start: float = 0):
    """HLS 播放列表（旧直连口，渐进式：前分片就绪即回；direct 请走 decide.direct_url）。
    新播放器请用 sessions 口（可 ping/关播）。"""
    sdir, _d = _ensure_hls(version_id, quality, audio, sub, start)
    return PlainTextResponse(_playlist_text(sdir),
                             media_type="application/vnd.apple.mpegurl")


@router.get("/{version_id}/seg/{name}")
def hls_segment(version_id: int, name: str):
    """HLS 分片（旧直连口）。文件名白名单 segNNNNN.ts，约束在会话目录内。
    未就绪等最多 15s（追渐进式转码进度），仍无则 404。"""
    if not _SEG_RE.match(name or ""):
        raise HTTPException(422, "bad segment name")
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    vdir = os.path.join(TRANSCODE_DIR, str(vid))
    if not os.path.isdir(vdir):
        raise HTTPException(404, "no transcode session (GET master.m3u8 first)")
    # 会话目录由 quality/audio/start 派生：取最新 mtime 的会话（同一版本同时只播一路为主）
    deadline = time.time() + 15
    cands: list[tuple[float, str]] = []
    while time.time() < deadline:
        cands = []
        try:
            for sess in os.listdir(vdir):
                sd = os.path.join(vdir, sess)
                cand = os.path.join(sd, name)
                if os.path.isfile(cand):
                    cands.append((os.path.getmtime(sd), cand))
        except OSError:
            pass
        if cands:
            break
        time.sleep(0.5)
    if not cands:
        raise HTTPException(404, "segment not ready (session may have ended)")
    cands.sort(reverse=True)
    return FileResponse(cands[0][1], media_type="video/MP2T",
                        filename=name)


@router.get("/{version_id}/sub/{idx}.vtt")
def hls_subtitle(version_id: int, idx: int):
    """文字字幕抽取 → WebVTT（缓存复用）。图片字幕 415。"""
    m, abs_p = _version_abs(version_id)
    info = _media_cached_or_probe(m, abs_p)
    subs = info.get("subs") or []
    try:
        si = int(idx)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad subtitle index")
    if not (0 <= si < len(subs)):
        raise HTTPException(404, "subtitle not found")
    track = subs[si]
    if int(track.get("image") or 0):
        raise HTTPException(415, "image subtitle (PGS/VobSub): download original and use VLC/Kodi")
    if not _ffmpeg_ok():
        raise HTTPException(501, "ffmpeg not installed in server image")
    sdir = os.path.join(TRANSCODE_DIR, str(int(m["id"])), "subs")
    os.makedirs(sdir, exist_ok=True)
    dest = os.path.join(sdir, f"{si}.vtt")
    try:
        fresh = (os.path.isfile(dest) and os.path.getmtime(dest) > os.path.getmtime(abs_p))
    except OSError:
        fresh = False
    if not fresh:
        ff_idx = track.get("ff_index", si)
        cmd = [shutil.which("ffmpeg") or "ffmpeg", "-y", "-hide_banner",
               "-loglevel", "error", "-i", abs_p, "-map", f"0:{ff_idx}", dest]
        try:
            subprocess.run(cmd, timeout=120, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            raise HTTPException(500, f"subtitle extract failed: {e}")
        if not os.path.isfile(dest):
            raise HTTPException(500, "subtitle extract failed")
    return FileResponse(dest, media_type="text/vtt", filename=f"sub{si}.vtt")
