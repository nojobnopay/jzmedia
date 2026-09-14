"""在线播放（Plex 式三档）：decide 决策 + 媒体信息 + 断点进度 + HLS 切片。

- 播放单位是版本行 id（每个文件版本即一行 movies，多版本选播即选 id）。
- 媒体信息懒探测 + media_info 缓存（ffprobe 本地派生，不污染 TMDB 镜像，不进 FTS）。
- 伪造文件（0 字节/probe 失败）→ playable=false，decide 422，前端置灰禁用。
- HLS：remux（-c copy，P1）/ transcode（P2）按 (version,quality,audio,start) 会话目录整片切片，
  24h TTL 清理，最大 2 路并发（超限 429，抄 Plex TranscodeCountLimit）。
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

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
                  audio: int = 0, sub: int | None = None):
    """三档决策：direct（零 CPU 走 blob）/ remux（-c copy）/ transcode / blocked。
    direct_url 直接复用 GET /api/movies/{id}/blob（Range 直发）；hls_url 供 P1/P2 切片口。"""
    m, abs_p = _version_abs(version_id)
    info = _media_cached_or_probe(m, abs_p)
    if not info.get("playable"):
        raise HTTPException(422, f"unplayable: {info.get('probe_error') or 'probe failed'}")
    d = _media.decide(info, quality=quality, audio_idx=audio, sub_idx=sub)
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


def _ensure_hls(version_id: int, quality: str, audio: int,
                sub: int | None, start: float) -> tuple[str, dict]:
    """保证会话切片存在（有则复用），返回 (session_dir, decide)。整片同步切片。"""
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
    sdir = _session_dir(int(m["id"]), quality, audio, start)
    playlist = os.path.join(sdir, "master.m3u8")
    marker = os.path.join(sdir, "plan.json")
    plan_key = json.dumps({"q": quality, "a": audio, "s": sub,
                           "plan": d["plan"]}, sort_keys=True)
    try:
        fresh = (os.path.isfile(playlist) and os.path.isfile(marker)
                 and open(marker, encoding="utf-8").read() == plan_key
                 and os.path.getmtime(playlist) > os.path.getmtime(abs_p))
    except OSError:
        fresh = False
    if fresh:
        return sdir, d
    if not _hls_sem.acquire(blocking=False):
        raise HTTPException(429, "transcode slots full (max 2), try later")
    try:
        _purge_old()
        for n in os.listdir(sdir):
            try:
                os.remove(os.path.join(sdir, n))
            except OSError:
                pass
        cmd = _media.build_cmd(abs_p, d["plan"], playlist, start=start)
        try:
            subprocess.run(cmd, timeout=1800, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            raise HTTPException(501, "ffmpeg not installed in server image")
        except subprocess.TimeoutExpired:
            raise HTTPException(504, "transcode timeout")
        if not os.path.isfile(playlist):
            raise HTTPException(500, "transcode failed (no playlist)")
        try:
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write(plan_key)
        except OSError:
            pass
        return sdir, d
    finally:
        _hls_sem.release()


@router.get("/{version_id}/master.m3u8")
def hls_master(version_id: int, quality: str = "original",
               audio: int = 0, sub: int | None = None, start: float = 0):
    """HLS 播放列表（remux/transcode 共用；direct 请走 decide.direct_url）。"""
    sdir, _d = _ensure_hls(version_id, quality, audio, sub, start)
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
    return PlainTextResponse("\n".join(out) + "\n",
                             media_type="application/vnd.apple.mpegurl")


@router.get("/{version_id}/seg/{name}")
def hls_segment(version_id: int, name: str):
    """HLS 分片。文件名白名单 segNNNNN.ts，约束在会话目录内。"""
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
    cands = []
    try:
        for sess in os.listdir(vdir):
            sd = os.path.join(vdir, sess)
            cand = os.path.join(sd, name)
            if os.path.isfile(cand):
                cands.append((os.path.getmtime(sd), cand))
    except OSError:
        pass
    if not cands:
        raise HTTPException(404, "segment not found")
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
