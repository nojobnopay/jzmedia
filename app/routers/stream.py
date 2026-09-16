"""在线播放（四档决策 + 渐进式 HLS 会话）。

- 决策在 app/playback.py（direct/remux/audio_transcode/video_transcode），输入含
  ClientCapabilities（前端实测上报，见 app/caps.py）；本模块只做会话与传输。
- 播放单位是版本行 id（每个文件版本即一行 movies，多版本选播即选 id）。
- 媒体信息懒探测 + media_info 缓存（ffprobe 本地派生，不污染 TMDB 镜像，不进 FTS）；
  probe_ver 低的老行播放时自动重探（新字段自愈，免手动 backfill）。
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
from collections import deque
from urllib.parse import quote, unquote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from .. import caps as _caps
from .. import media as _media
from .. import playback as _playback
from .. import store
from ..config import settings
from ..db import TRANSCODE_DIR

router = APIRouter(prefix="/api/stream")

_hls_sem = threading.Semaphore(2)
# fMP4 扁平产物：<name>_init.mp4 / <name>_segNNNNN.m4s；TS 回滚：segNNNNN.ts
_SEG_RE = re.compile(r"^(?:[A-Za-z0-9]+_seg\d+\.m4s|seg\d+\.ts)$")
# 会话目录内可直接取的播放文件（master 为 P2 自产；变体列表/初始化段/分片）
_SESS_FILE_RE = re.compile(
    r"^(?:out_[A-Za-z0-9]+\.m3u8|[A-Za-z0-9]+_init\.mp4|[A-Za-z0-9]+_seg\d+\.m4s|seg\d+\.ts)$")
_TTL = 24 * 3600
_MIN_SEGS = 3          # 首屏等待分片数（fMP4 4s → 约 12s 内容；TS 6s → 18s）
_SESS_IDLE = 600       # 会话无心跳保活期（秒）
_sessions: dict[str, dict] = {}
_sess_lock = threading.RLock()
# 请求命中环形日志（卡死定位用）：{t, sid, kind, name, status}，只增不查库
_hits: deque = deque(maxlen=200)


def _log_hit(sid: str, kind: str, name: str, status: int) -> None:
    try:
        with _sess_lock:
            _hits.append({"t": int(time.time()), "sid": sid or "",
                          "kind": kind, "name": name or "", "status": int(status)})
    except Exception:
        pass


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
    """缓存优先；环境错误缓存/探测结构过期（probe_ver 低）→ 自动重探（自愈，免 backfill）。"""
    cached = store.get_media_info(int(m["id"]))
    if cached and int(cached.get("probed_at") or 0) > 0:
        stale = int(cached.get("probe_ver") or 0) < int(getattr(_media, "PROBE_VERSION", 0))
        # 脏行自愈：环境错误缓存视为未探测（旧版本落库的，一次即洗掉）
        if stale or (not cached.get("playable") and _media.is_retryable_error(
                str(cached.get("probe_error") or ""))):
            cached = None
        else:
            return cached
    info = _media.probe(abs_p)
    return store.upsert_media_info(int(m["id"]), info)


@router.get("/{version_id}/media")
def stream_media(version_id: int, refresh: int = 0):
    """版本媒体信息（徽章行用）：容器/时长/分辨率/编码/HDR/位深/音字幕列表/playable，
    并附客户端实测候选码串（vcaps + 每音轨 caps）。"""
    m, abs_p = _version_abs(version_id)
    if refresh:
        info = store.upsert_media_info(int(m["id"]), _media.probe(abs_p))
    else:
        info = _media_cached_or_probe(m, abs_p)
    return {"version_id": int(m["id"]), "file_path": m["file_path"],
            "title": m.get("title", ""), **_media.decorate(info),
            "duration_text": _media.fmt_duration(info.get("duration") or 0)}


class PlaybackQuery(BaseModel):
    """播放请求（用户选择 + 客户端能力）。caps 缺省走服务端保守默认（旧客户端/调试）。"""
    quality: str = "auto"
    audio: int = 0
    sub: int | None = None
    client: str = "web"
    caps: dict | None = None


def _decide_payload(m: dict, info: dict, q: PlaybackQuery) -> dict:
    caps = _caps.default_caps() if q.caps is None else q.caps
    d = _playback.plan(info, caps=caps, quality=q.quality, audio_idx=q.audio,
                       sub_idx=q.sub, client=q.client)
    method = d["method"]
    blob_url = f"/api/movies/{int(m['id'])}/blob?name={m['file_path']}"
    sub_q = f"&sub={q.sub}" if q.sub is not None else ""
    hls_url = (f"/api/stream/{int(m['id'])}/master.m3u8"
               f"?quality={q.quality}&audio={q.audio}{sub_q}")
    return {"version_id": int(m["id"]), "method": method, "reasons": d["reasons"],
            "plan": d["plan"], "subtitle_mode": d.get("subtitle_mode") or "none",
            "caps_hash": _caps.caps_hash(caps) if q.caps is not None else "",
            "media": _media.decorate(info),
            "direct_url": blob_url if method == "direct" else "",
            "hls_url": hls_url if method in ("remux", "audio_transcode",
                                             "transcode", "video_transcode") else ""}


@router.get("/{version_id}/decide")
def stream_decide(version_id: int, quality: str = "auto",
                  audio: int = 0, sub: int | None = None,
                  client: str = "web"):
    """三档决策（GET 兼容口：无 caps，走服务端保守默认）。新播放器用 POST 带 caps。"""
    m, abs_p = _version_abs(version_id)
    info = _media_cached_or_probe(m, abs_p)
    if not info.get("playable"):
        raise HTTPException(422, f"unplayable: {info.get('probe_error') or 'probe failed'}")
    return _decide_payload(m, info, PlaybackQuery(quality=quality, audio=audio,
                                                  sub=sub, client=client, caps=None))


@router.post("/{version_id}/decide")
def stream_decide_post(version_id: int, body: PlaybackQuery | None = None):
    """四档决策（目标文档 §5）：direct / remux / audio_transcode / video_transcode。
    caps 由前端 caps.js 实测上报；direct_url 复用 blob（Range 直发），hls_url 供切片口。"""
    m, abs_p = _version_abs(version_id)
    info = _media_cached_or_probe(m, abs_p)
    if not info.get("playable"):
        raise HTTPException(422, f"unplayable: {info.get('probe_error') or 'probe failed'}")
    return _decide_payload(m, info, body or PlaybackQuery())


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


class VersionsQuery(BaseModel):
    movie_id: int
    quality: str = "auto"
    client: str = "web"
    caps: dict | None = None


def _versions_payload(movie_id: int, q: VersionsQuery) -> dict:
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
    cli = (q.client or "web").strip().lower()
    caps = _caps.default_caps() if q.caps is None else q.caps
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
        base = {"version_id": vid, "file_path": vm["file_path"],
                "edition": v.get("edition") or "", "spec": v.get("spec") or ""}
        if not os.path.isfile(abs_p):
            items.append({**base, "playable": False, "probe_error": "file missing",
                          "method": "blocked", "reasons": ["unplayable"],
                          "score": [9, 0], "duration_text": ""})
            continue
        info = _media_cached_or_probe(vm, abs_p)
        if not info.get("playable"):
            items.append({**base, "playable": False,
                          "probe_error": info.get("probe_error") or "probe failed",
                          "method": "blocked", "reasons": ["unplayable"],
                          "score": [9, 0], "duration_text": ""})
            continue
        d = _playback.plan(info, caps=caps, quality=q.quality, client=cli)
        method, reasons = d["method"], d["reasons"]
        score = _playback.score(info, caps=caps, quality=q.quality, client=cli)
        items.append({**base, "playable": True, "probe_error": "",
                      "method": method, "reasons": reasons,
                      "score": list(score),
                      "duration": info.get("duration") or 0,
                      "duration_text": _media.fmt_duration(info.get("duration") or 0),
                      "height": info.get("height") or 0,
                      "vcodec": info.get("vcodec") or "",
                      "hdr": info.get("hdr") or "",
                      "dv_profile": info.get("dv_profile") or 0,
                      "bit_depth": info.get("bit_depth") or 0,
                      "audio_count": len(info.get("audio") or []),
                      "sub_count": len(info.get("subs") or [])})
    best = None
    for it in sorted(items, key=lambda x: (x["score"], x["version_id"])):
        if it["method"] != "blocked":
            best = it["version_id"]
            break
    return {"movie_id": mid, "quality": q.quality, "client": cli,
            "versions": items, "best_version_id": best}


@router.get("/versions")
def stream_versions(movie_id: int, quality: str = "auto", client: str = "web"):
    """版本聚合（GET 兼容口：无 caps，走服务端保守默认）。"""
    return _versions_payload(movie_id, VersionsQuery(movie_id=movie_id, quality=quality,
                                                     client=client, caps=None))


@router.post("/versions")
def stream_versions_post(q: VersionsQuery):
    """版本聚合（POST：带客户端实测 caps，打分/最优版随能力变化）。"""
    return _versions_payload(q.movie_id, q)


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
        # 无行、旧行是环境错误（换环境后可重试）、或探测结构过期（probe_ver 低）都要补探
        kept = []
        for r in rows:
            mi = store.get_media_info(int(r["id"]))
            if not mi:
                kept.append(r)
            elif int(mi.get("probe_ver") or 0) < int(getattr(_media, "PROBE_VERSION", 0)):
                kept.append(r)
            elif not mi.get("playable") and _media.is_retryable_error(
                    str(mi.get("probe_error") or "")):
                kept.append(r)
        rows = kept
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
    try:
        return bool(_media.bin_status().get("ffmpeg"))
    except Exception:
        return bool(shutil.which("ffmpeg"))


def _seg_count(sdir: str, prefix: str = "") -> int:
    """分片数（可限定前缀）。fMP4 视频分片 video_seg*，音频 rendition 是 audioN_seg*：
    首屏等待/预转码进度必须只数视频分片——音频转码快得多，混数会在视频就绪前放行。"""
    try:
        return sum(1 for n in os.listdir(sdir)
                   if _SEG_RE.match(n or "") and (not prefix or (n or "").startswith(prefix)))
    except OSError:
        return 0


def _video_seg_prefix(seg: str) -> str:
    return "seg" if seg == "ts" else "video_"


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


def shutdown_sessions() -> None:
    """应用退出（uvicorn 优雅关闭 / docker stop）：杀掉全部转码进程，避免孤儿 ffmpeg
    继续占 CPU 写分片（SIGKILL 场景兜不住，分片靠 TTL 清理）。"""
    with _sess_lock:
        sids = list(_sessions.keys())
    for sid in sids:
        _drop_session(sid, kill=True)


def _sweeper() -> None:
    """后台收尸：无心跳超期 / 进程已退出超期 → 杀进程删会话（分片留 TTL 清理）。"""
    while True:
        time.sleep(60)
        now = time.time()
        dead = []
        with _sess_lock:
            for sid, s in list(_sessions.items()):
                proc = s.get("proc")
                exited = proc is not None and proc.poll() is not None
                idle = now - float(s.get("last_ping") or now)
                # 完工静态会话（proc=None）：只按文件 TTL 收记录，不按 idle 杀
                if s.get("complete"):
                    if idle > _TTL:
                        dead.append(sid)
                elif idle > _SESS_IDLE or (exited and idle > 300):
                    dead.append(sid)
        for sid in dead:
            _drop_session(sid, kill=True)
        _purge_old()


_sweeper_thread = threading.Thread(target=_sweeper, daemon=True)
_sweeper_thread.start()


def _quality_key(plan: dict) -> str:
    """会话目录的档位键：由实际产物（plan）决定，而非用户请求字符串。
    这样 auto/原画/1080p/720p 落到同一 plan 时共用目录与静态成品（预转码命中在线播）：
    - copy（vcopy 且不封顶）→ "copy"；重编封顶 → "h720"/"h1080"；重编不封顶 → "src"。"""
    plan = plan or {}
    if plan.get("vcopy") and not int(plan.get("height") or 0):
        return "copy"
    h = int(plan.get("height") or 0)
    return f"h{h}" if h else "src"


def _session_key(plan: dict, audio: int) -> str:
    """会话目录键（不含 start，start 由 _session_dir 拼），由产物（plan）决定。
    - fMP4：`f` 前缀（copy/h720/h1080/src）；全部音轨 rendition 都在，与所选音轨无关；
    - TS 回滚：`<档位>_a<音轨>`（沿用 P1 命名，按所选音轨单轨产出）。"""
    p = plan or {}
    if (p.get("seg") or "fmp4") == "ts":
        return f"{_quality_key(p)}_a{int(audio or 0)}"
    return "f" + _quality_key(p)


def _plan_marker(plan: dict, audio: int, start: float) -> str:
    """静态/在线复用键：只保留决定转码产物的字段。
    - 去掉档位字符串：目录键（`_session_key`）已含产物档位；
    - `seg`（封装类型）由目录键前缀区分，不进键（TS 回滚可继续命中 P1 旧成品）；
    - 非烧录字幕归一（文本/ASS 字幕走独立接口，不影响 ffmpeg 输出），
      否则换个字幕就把整片成品作废；烧录保留（sub_ff_index 在 plan 内）。"""
    p = dict(plan or {})
    p.pop("seg", None)
    a_key = int(audio or 0)
    if (plan or {}).get("seg") != "ts":
        # fMP4 全部音轨都产成 rendition，产物与所选音轨无关 → 选择不进键（秒开复用）
        p["audio_idx"] = None
        a_key = 0
    if p.get("sub") != "burn":
        p["sub"] = "none"
        p["sub_idx"] = None
    try:
        st = max(0, int(float(start or 0)))
    except (TypeError, ValueError):
        st = 0
    return json.dumps({"a": a_key, "start": st, "plan": p}, sort_keys=True)


def _session_dir(version_id: int, key: str, start: float) -> str:
    k = re.sub(r"[^a-z0-9_]+", "", (key or "src").strip().lower()) or "src"
    try:
        st = max(0, int(float(start or 0)))
    except (TypeError, ValueError):
        st = 0
    d = os.path.join(TRANSCODE_DIR, str(int(version_id)), f"{k}_s{st}")
    os.makedirs(d, exist_ok=True)
    return d


def _write_master(sdir: str, info: dict, plan: dict, seg_time: int) -> None:
    """自产 fMP4 master.m3u8（目标文档 §6 布局）：ffmpeg 的 -master_pl_name 对
    HEVC copy 不产 CODECS（Safari 原生 HLS 需要），故自己写、字段可控。"""
    plan = plan or {}
    audios = list(plan.get("audios") or [])
    src_w = int(info.get("width") or 0)
    src_h = int(info.get("height") or 0)
    tgt_h = int(plan.get("height") or 0)
    vcodec = _media.norm_codec(str(info.get("vcodec") or ""))
    if plan.get("vcopy"):
        cands = _media.video_codec_strings(
            vcodec, str(info.get("video_profile") or ""),
            int(info.get("video_level") or 0), int(info.get("bit_depth") or 0))
        vc = cands[0] if cands else ""
        w, h = src_w, src_h
    else:
        vc = _playback.transcoded_video_codec(tgt_h or src_h)
        if tgt_h and src_h and src_w:
            h = min(tgt_h, src_h)
            w = max(2, round(src_w * h / src_h / 2) * 2)
        else:
            w, h = src_w, src_h
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS"]
    default_acodec = ""
    audio_bits = 0
    tracks = info.get("audio") or []
    for n, a in enumerate(audios):
        a = a or {}
        try:
            track = tracks[int(a.get("i") or 0)] or {}
        except (IndexError, ValueError):
            track = {}
        if a.get("copy"):
            ac = _media.audio_codec_string(str(track.get("codec") or "")) or "mp4a.40.2"
        else:
            ac = "mp4a.40.2"
        if int(a.get("default") or 0) and not default_acodec:
            default_acodec = ac
        try:
            abr = int(track.get("bitrate") or 0) or (192000 if not a.get("copy") else 256000)
        except (TypeError, ValueError):
            abr = 192000
        audio_bits += abr
        attrs = ["TYPE=AUDIO", 'GROUP-ID="aud"', f'NAME="audio{n + 1}"',
                 f'DEFAULT={"YES" if a.get("default") else "NO"}',
                 "AUTOSELECT=YES",
                 f'CHANNELS="{int(a.get("channels") or 2)}"']
        if a.get("lang"):
            attrs.append(f'LANGUAGE="{a["lang"]}"')
        attrs.append(f'URI="out_audio{n}.m3u8"')
        lines.append("#EXT-X-MEDIA:" + ",".join(attrs))
    if plan.get("vcopy"):
        try:
            vbits = int(info.get("vbitrate") or 0)
        except (TypeError, ValueError):
            vbits = 0
    else:
        # 重编档用分辨率估算（~3bit/px/s），别照抄源码率（4K→720p 后虚高 10 倍）
        vbits = (w * h * 3) if (w and h) else 0
    bandwidth = vbits + audio_bits
    if bandwidth <= 0:
        bandwidth = 16000000 if src_h > 1080 else 6000000
    si = [f"BANDWIDTH={bandwidth}"]
    if w and h:
        si.append(f"RESOLUTION={w}x{h}")
    codecs = [x for x in (vc, default_acodec or ("mp4a.40.2" if audios else "")) if x]
    if codecs:
        si.append('CODECS="' + ",".join(codecs) + '"')
    if audios:
        si.append('AUDIO="aud"')
    lines.append("#EXT-X-STREAM-INF:" + ",".join(si))
    lines.append("out_video.m3u8")
    try:
        with open(os.path.join(sdir, "master.m3u8"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


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
                   sub: int | None, start: float,
                   caps: dict | None = None) -> tuple[str, str, dict]:
    """起后台转码会话（渐进式）：校验→plan→Popen→等前 _MIN_SEGS 分片。
    返回 (session_id, session_dir, plan_result)。direct/无 ffmpeg 等直接抛对应 HTTP 状态。
    caps 参与 plan 与 plan_key（不同客户端能力不复用同一转码档）。"""
    m, abs_p = _version_abs(version_id)
    info = _media_cached_or_probe(m, abs_p)
    if not info.get("playable"):
        raise HTTPException(422, f"unplayable: {info.get('probe_error') or 'probe failed'}")
    caps_n = _caps.default_caps() if caps is None else _caps.normalize_caps(caps)
    d = _playback.plan(info, caps=caps_n, quality=quality, audio_idx=audio, sub_idx=sub)
    if d["method"] == "blocked":
        raise HTTPException(422, "unplayable")
    if d["method"] == "direct":
        raise HTTPException(400, "use direct_url (Direct Play, no HLS needed)")
    # 图片字幕（PGS/VobSub）：浏览器无法当外挂字幕后，改为烧录进画面（重开转码会话）。
    # 取所选字幕在文件里的真实流号（ff_index），塞进 plan 供 build_cmd 组 overlay 滤镜。
    if (d.get("plan") or {}).get("sub") == "burn":
        try:
            si = int(sub if sub is not None else -1)
        except (TypeError, ValueError):
            si = -1
        subs = info.get("subs") or []
        if not (0 <= si < len(subs)):
            raise HTTPException(422, "subtitle not found")
        track = subs[si] or {}
        if not int(track.get("image") or 0):
            raise HTTPException(422, "not an image subtitle")
        d["plan"]["sub_ff_index"] = int(track.get("ff_index", si))
    if not _ffmpeg_ok():
        raise HTTPException(501, "ffmpeg not installed in server image")
    # 单人场景：同版本同 plan（含 start）且进程活着 → 直接复用（秒开，不重转）；
    # 同版本不同 plan → 先杀旧的再开新的（防多路 ffmpeg 抢 CPU 越跑越慢）。
    # 复用键/目录键只放产物字段（见 _plan_marker/_quality_key），不同 caps/档位字符串
    # 只要落到同一 plan 就可安全复用（caps 摘要仅 decide 返回供观测）。
    try:
        st_key = max(0, int(float(start or 0)))
    except (TypeError, ValueError):
        st_key = 0
    plan_key = _plan_marker(d["plan"], audio, st_key)
    skey = _session_key(d["plan"], audio)
    seg = d["plan"].get("seg") or "fmp4"
    stime = _playback.seg_time(seg)
    # 整片已转完（预转码/之前播完）：当静态 VOD 直接播，不起进程——hls.js 最稳形态
    sdir0 = _session_dir(int(m["id"]), skey, start)
    if _session_complete(sdir0, plan_key):
        sid0 = uuid.uuid4().hex[:16]
        with _sess_lock:
            _sessions[sid0] = {"proc": None, "sdir": sdir0, "vid": int(m["id"]),
                               "plan": d["plan"], "plan_key": plan_key,
                               "complete": True, "last_ping": time.time()}
        return sid0, sdir0, d
    with _sess_lock:
        for sid, s in list(_sessions.items()):
            if int(s.get("vid") or -1) != int(m["id"]):
                continue
            proc = s.get("proc")
            if proc is not None and proc.poll() is not None:
                continue
            if s.get("plan_key") == plan_key:
                s["last_ping"] = time.time()
                return sid, s["sdir"], d
            _drop_session(sid, kill=True)
    if not _hls_sem.acquire(blocking=False):
        raise HTTPException(429, "transcode slots full (max 2), try later")
    sdir = ""
    try:
        _purge_old()
        sdir = _session_dir(int(m["id"]), skey, start)
        for n in os.listdir(sdir):
            try:
                os.remove(os.path.join(sdir, n))
            except OSError:
                pass
        if seg != "ts":
            # fMP4 的 master 自己写（ffmpeg 对 HEVC copy 不产 CODECS）；变体列表由 ffmpeg 产
            _write_master(sdir, info, d["plan"], stime)
        cmd = _playback.build_cmd(abs_p, d["plan"], start=start, seg_time=stime)
        log_path = os.path.join(sdir, "ffmpeg.log")
        try:
            log_fh = open(log_path, "wb")
        except OSError:
            log_fh = subprocess.DEVNULL  # type: ignore[assignment]
        try:
            # cwd=sdir：ffmpeg 相对分片名按 CWD 落盘，播放列表 URI 按列表位置解析
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=log_fh, cwd=sdir)
        except FileNotFoundError:
            try:
                if log_fh is not subprocess.DEVNULL:
                    log_fh.close()
            except Exception:
                pass
            raise HTTPException(501, "ffmpeg not installed in server image")
        except Exception as e:
            try:
                if log_fh is not subprocess.DEVNULL:
                    log_fh.close()
            except Exception:
                pass
            raise HTTPException(500, f"transcode spawn failed: {e}")
        sid = uuid.uuid4().hex[:16]
        with _sess_lock:
            _sessions[sid] = {"proc": proc, "sdir": sdir, "vid": int(m["id"]),
                              "plan": d["plan"], "plan_key": plan_key,
                              "last_ping": time.time()}
        # 等前 _MIN_SEGS 个视频分片（remux 秒出；转码按实际速度）：首画面不等整片
        vprefix = _video_seg_prefix(seg)
        deadline = time.time() + 300
        while time.time() < deadline:
            if _seg_count(sdir, vprefix) >= _MIN_SEGS:
                break
            if proc.poll() is not None:
                break
            time.sleep(1)
        if _seg_count(sdir, vprefix) == 0 or not _variant_playlists(sdir):
            _drop_session(sid, kill=True)
            tail = ""
            try:
                with open(log_path, "rb") as fh:
                    fh.seek(max(0, os.path.getsize(log_path) - 2000))
                    tail = fh.read().decode("utf-8", errors="replace").strip()[-500:]
            except OSError:
                pass
            raise HTTPException(500, "transcode failed (no segments)" +
                                (f": {tail}" if tail else ""))
        try:
            with open(os.path.join(sdir, "plan.json"), "w", encoding="utf-8") as fh:
                mk = json.loads(plan_key)
                mk["sid"] = sid
                fh.write(json.dumps(mk, sort_keys=True))
        except (OSError, ValueError):
            pass
        # 完工监视：仅自然退出(code 0)写 complete.json，供“静态 VOD 复用”用
        threading.Thread(target=_watch_completion,
                         args=(sid, proc, sdir, plan_key), daemon=True).start()
        return sid, sdir, d
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"transcode failed: {e}")
    finally:
        _hls_sem.release()


def _marker_matches(sdir: str, plan_key: str, filename: str = "plan.json") -> bool:
    """标记文件与本次 plan_key 比对（忽略 sid；复用/完工判定用）。"""
    try:
        with open(os.path.join(sdir, filename), encoding="utf-8") as fh:
            mk = json.load(fh)
        mk.pop("sid", None)
        ref = json.loads(plan_key)
        ref.pop("sid", None)
        return mk == ref
    except Exception:
        return False


def _variant_playlists(sdir: str) -> list[str]:
    """会话目录里的变体播放列表：fMP4 为 out_*.m3u8（video+各音轨）；TS 为 master.m3u8。
    注意 fMP4 的 master.m3u8 是自产索引，不带 ENDLIST，不能参与完工判定。"""
    try:
        names = os.listdir(sdir)
    except OSError:
        return []
    outs = sorted(n for n in names if re.match(r"^out_[A-Za-z0-9]+\.m3u8$", n or ""))
    if outs:
        return outs
    return ["master.m3u8"] if "master.m3u8" in names else []


def _has_endlist(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip() == "#EXT-X-ENDLIST":
                    return True
    except OSError:
        pass
    return False


def _playlist_endlist(sdir: str) -> bool:
    """整片完工：所有变体列表都带 ENDLIST（fMP4 video/audio 各自写 ENDLIST）。"""
    pls = _variant_playlists(sdir)
    if not pls:
        return False
    for name in pls:
        if not _has_endlist(os.path.join(sdir, name)):
            return False
    return True


def _write_complete_marker(sdir: str, plan_key: str) -> None:
    try:
        with open(os.path.join(sdir, "complete.json"), "w", encoding="utf-8") as fh:
            fh.write(plan_key)
    except OSError:
        pass


def _watch_completion(sid: str, proc, sdir: str, plan_key: str) -> None:
    """完工监视线程：仅当 ffmpeg 自然退出（返回码 0）才写 complete.json。
    被 SIGTERM 杀掉（负返回码）的残缺会话也会写出 ENDLIST，绝不能冒充静态 VOD。"""
    try:
        rc = proc.wait()
    except Exception:
        return
    if rc == 0 and _seg_count(sdir) > 0 and _playlist_endlist(sdir):
        _write_complete_marker(sdir, plan_key)
        with _sess_lock:
            s = _sessions.get(sid)
            if s is not None:
                s["complete"] = True


def _session_complete(sdir: str, plan_key: str) -> bool:
    """整片已自然转完（静态 VOD）：complete.json 存在 + 列表带 ENDLIST + 有分片 + plan 一致。
    仅凭 ENDLIST 不够——被杀的残缺会话也会写 ENDLIST（SIGTERM 时 ffmpeg 会写 trailer）。"""
    if not os.path.isfile(os.path.join(sdir, "complete.json")):
        return False
    return (_playlist_endlist(sdir) and _seg_count(sdir) > 0
            and _marker_matches(sdir, plan_key, "complete.json"))


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
    quality: str = "auto"
    audio: int = 0
    sub: int | None = None
    start: float = 0
    caps: dict | None = None


_prewarm_jobs: dict[str, dict] = {}


def _prewarm_worker(job_id: str, vid: int, quality: str, audio: int) -> None:
    """后台整片转完（夜间用）：与在线播同一 build_cmd/目录 scheme，完工即静态 VOD。
    进度=已产分片/预估总数；失败记 error 尾。"""
    job = _prewarm_jobs.get(job_id)
    if not job:
        return
    if not _hls_sem.acquire(blocking=False):
        job.update({"status": "failed", "error": "transcode slots full, retry later"})
        return
    try:
        m, abs_p = _version_abs(vid)
        info = _media_cached_or_probe(m, abs_p)
        if not info.get("playable"):
            raise RuntimeError(f"unplayable: {info.get('probe_error') or 'probe failed'}")
        d = _playback.plan(info, caps=_caps.default_caps(), quality=quality,
                           audio_idx=audio)
        if d["method"] == "direct":
            raise RuntimeError("already direct-playable, no prewarm needed")
        try:
            dur = max(0.0, float(info.get("duration") or 0))
        except (TypeError, ValueError):
            dur = 0.0
        seg = d["plan"].get("seg") or "fmp4"
        stime = _playback.seg_time(seg)
        expected = max(1, int(dur / stime) + 1) if dur > 0 else 0
        job.update({"expected": expected,
                    "total_text": _media.fmt_duration(dur)})
        sdir = _session_dir(int(m["id"]), _session_key(d["plan"], audio), 0)
        job["sdir"] = sdir
        job["vprefix"] = _video_seg_prefix(seg)
        for n in os.listdir(sdir):
            try:
                os.remove(os.path.join(sdir, n))
            except OSError:
                pass
        if seg != "ts":
            _write_master(sdir, info, d["plan"], stime)
        cmd = _playback.build_cmd(abs_p, d["plan"], start=0, seg_time=stime)
        log_path = os.path.join(sdir, "ffmpeg.log")
        try:
            log_fh = open(log_path, "wb")
        except OSError:
            log_fh = subprocess.DEVNULL  # type: ignore[assignment]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log_fh,
                                cwd=sdir)
        job.update({"status": "running", "pid": proc.pid})
        timeout = max(1800.0, dur * 4 + 600) if dur > 0 else 7200.0
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_proc(proc)
            raise RuntimeError("prewarm timeout")
        if proc.returncode != 0 or not _playlist_endlist(sdir):
            tail = ""
            try:
                with open(log_path, "rb") as fh:
                    fh.seek(max(0, os.path.getsize(log_path) - 2000))
                    tail = fh.read().decode("utf-8", errors="replace").strip()[-500:]
            except OSError:
                pass
            raise RuntimeError("transcode failed" + (f": {tail}" if tail else ""))
        marker = _plan_marker(d["plan"], audio, 0)
        mk = json.loads(marker)
        mk["sid"] = "prewarm:" + job_id
        marker_with_sid = json.dumps(mk, sort_keys=True)
        with open(os.path.join(sdir, "plan.json"), "w", encoding="utf-8") as fh:
            fh.write(marker_with_sid)
        # 自然转完（returncode 0 且 ENDLIST）：写完工标记，点播当静态 VOD 秒开
        _write_complete_marker(sdir, marker)
        job.update({"status": "done",
                    "segments": _seg_count(sdir, str(job.get("vprefix") or "video_"))})
    except Exception as e:
        job.update({"status": "failed", "error": str(e)[:500]})
    finally:
        try:
            sdir = str(job.get("sdir") or "")
            if sdir:
                job["segments"] = _seg_count(sdir, str(job.get("vprefix") or "video_"))
        except Exception:
            pass
        _hls_sem.release()


class PrewarmBody(BaseModel):
    version_id: int = 0
    quality: str = "auto"     # auto=按后端能力封顶（无 HW 720p / 有 HW 1080p）
    audio: int = 0


@router.post("/prewarm")
def prewarm_start(body: PrewarmBody | None = None):
    """夜间预转码：后台把整片转完（与在线播同一管线），完工后点播即静态秒播。
    只对 remux/transcode 生效；direct 直接 400（本来就零 CPU）。
    quality 支持 auto/1080p/720p/source；产物键与在线播一致（同 plan 即命中复用）。"""
    body = body or PrewarmBody()
    try:
        vid = int(body.version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    if not store.get_movie(vid):
        raise HTTPException(404, "version not found")
    quality = body.quality or "auto"
    job_id = uuid.uuid4().hex[:12]
    _prewarm_jobs[job_id] = {"job_id": job_id, "version_id": vid,
                             "quality": quality,
                             "audio": int(body.audio or 0),
                             "status": "queued", "segments": 0, "expected": 0,
                             "started_at": int(time.time())}
    th = threading.Thread(target=_prewarm_worker,
                          args=(job_id, vid, quality,
                                int(body.audio or 0)),
                          daemon=True)
    th.start()
    return {"job_id": job_id, "status": "queued"}


@router.get("/prewarm/{job_id}")
def prewarm_status(job_id: str):
    """预转码进度：{status queued/running/done/failed, segments/expected}。"""
    job = _prewarm_jobs.get(job_id or "")
    if not job:
        raise HTTPException(404, "no such prewarm job")
    if job.get("status") == "running" and job.get("sdir"):
        try:
            job["segments"] = _seg_count(str(job.get("sdir")),
                                         str(job.get("vprefix") or "video_"))
        except Exception:
            pass
    return dict(job)


@router.post("/{version_id}/sessions")
def hls_session_create(version_id: int, body: SessionBody | None = None):
    """开渐进式转码会话：后台 ffmpeg，前 3 分片就绪即回（首画面不等整片）。
    seek/换清晰度 = 关旧开新；fMP4 下切音轨只切 rendition（客户端完成，不重开会话）。
    caps 参与 plan（HEVC 直通/音频单转等四档），缺省保守默认。"""
    body = body or SessionBody()
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    sid, _sdir, d = _spawn_session(vid, body.quality, body.audio, body.sub, body.start,
                                   caps=body.caps)
    return {"session_id": sid,
            "playlist_url": f"/api/stream/sessions/{sid}/master.m3u8",
            "method": d["method"], "reasons": d["reasons"], "plan": d["plan"],
            "subtitle_mode": d.get("subtitle_mode") or "none"}


@router.get("/sessions/{sid}/master.m3u8")
def hls_session_playlist(sid: str):
    """会话主播放列表（增长型；转码完成前无 ENDLIST，hls.js 照播）。每次取即心跳。
    - fMP4：自产 master（EXT-X-MEDIA 多音轨）原样吐出；
    - TS 回滚：旧 media 列表，分片路径重写为 `seg/`（旧前端兼容）。
    永不缓存：hls.js 靠反复重取发现新分片，缓存即断流。"""
    sess = _get_session(sid)
    if (sess.get("plan") or {}).get("seg") == "ts":
        body = _playlist_text(sess["sdir"])
    else:
        path = os.path.join(sess["sdir"], "master.m3u8")
        try:
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
        except OSError:
            raise HTTPException(404, "playlist not ready (session may have ended)")
    _log_hit(sid, "playlist", "master.m3u8", 200)
    return PlainTextResponse(body, media_type="application/vnd.apple.mpegurl",
                             headers={"Cache-Control": "no-store"})


def _wait_file(sess: dict, dest: str, timeout: float = 25.0) -> bool:
    """等文件就绪（追渐进式转码进度）；会话已死则提前放弃。"""
    deadline = time.time() + timeout
    while not os.path.isfile(dest) and time.time() < deadline:
        proc = sess.get("proc")
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(0.5)
    return os.path.isfile(dest)


@router.get("/sessions/{sid}/seg/{name}")
def hls_session_segment(sid: str, name: str):
    """会话分片：未就绪等最多 25s（追转码进度），会话死亡则 404。"""
    if not _SEG_RE.match(name or ""):
        raise HTTPException(422, "bad segment name")
    sess = _get_session(sid)
    # 文件名已白名单限定为扁平 segNNNNN.ts（无目录成分），此处再以 dirname 双保险；
    # 注意两侧都要 normpath：DATA_DIR 可能是相对路径（./data），单边归一会恒假。
    base = os.path.normpath(sess["sdir"])
    dest = os.path.normpath(os.path.join(base, os.path.basename(name or "")))
    if os.path.dirname(dest) != base:
        raise HTTPException(422, "bad segment name")
    deadline = time.time() + 25
    while not os.path.isfile(dest) and time.time() < deadline:
        proc = sess.get("proc")
        if proc is not None and proc.poll() is not None:
            break
        time.sleep(0.5)
    if not os.path.isfile(dest):
        _log_hit(sid, "seg", name, 404)
        raise HTTPException(404, "segment not ready (session may have ended)")
    _log_hit(sid, "seg", name, 200)
    return FileResponse(dest, media_type="video/MP2T", filename=name)


@router.get("/sessions/{sid}/debug")
def hls_session_debug(sid: str):
    """卡死自证口：进程活/死/退出码、已产分片、列表行数/是否完工、ffmpeg尾日志、近期命中。
    前端状态行 + 此口 JSON，足够定位“没产出/没进来/播不出”三选一。"""
    with _sess_lock:
        sess = _sessions.get(sid or "")
        hits = [h for h in list(_hits) if h.get("sid") == sid][-20:]
    if not sess:
        return {"session_id": sid, "exists": False, "hits": hits}
    proc = sess.get("proc")
    alive = proc is not None and proc.poll() is None
    exit_code = None if proc is None or alive else proc.poll()
    sdir = sess.get("sdir") or ""
    segs = _seg_count(sdir)
    extinf = 0
    for name in _variant_playlists(sdir):
        try:
            with open(os.path.join(sdir, name), encoding="utf-8") as fh:
                for line in fh:
                    if line.strip().startswith("#EXTINF"):
                        extinf += 1
        except OSError:
            continue
    endlist = _playlist_endlist(sdir)
    flog = ""
    try:
        lp = os.path.join(sdir, "ffmpeg.log")
        with open(lp, "rb") as fh:
            fh.seek(max(0, os.path.getsize(lp) - 2000))
            flog = fh.read().decode("utf-8", errors="replace")[-800:]
    except OSError:
        pass
    return {"session_id": sid, "exists": True, "running": alive,
            "complete": bool(sess.get("complete")),
            "exit_code": exit_code, "segments": segs,
            "playlist_items": extinf, "finished": endlist,
            "last_ping_ago": round(time.time() - float(sess.get("last_ping") or 0), 1),
            "ffmpeg_log_tail": flog, "hits": hits}


@router.post("/sessions/{sid}/ping")
def hls_session_ping(sid: str):
    """心跳保活（播放器每 ~10s 调一次；10min 无心跳会话被回收）。"""
    sess = _get_session(sid)
    proc = sess.get("proc")
    running = proc is not None and proc.poll() is None
    return {"session_id": sid, "running": running,
            "segments": _seg_count(sess["sdir"])}


# 注意：扁平产物路由必须注册在 debug/ping 之后——`{name}` 会吞掉同段静态路径
@router.get("/sessions/{sid}/{name}")
def hls_session_file(sid: str, name: str):
    """fMP4 扁平产物：变体列表 out_<name>.m3u8 / init <name>_init.mp4 / 分片 <name>_segNNNNN.m4s。
    TS 回滚时的 segNNNNN.ts 也走这里（旧 seg/ 路由保留兼容）。"""
    if not _SESS_FILE_RE.match(name or ""):
        raise HTTPException(422, "bad file name")
    sess = _get_session(sid)
    base = os.path.normpath(sess["sdir"])
    dest = os.path.normpath(os.path.join(base, os.path.basename(name or "")))
    if os.path.dirname(dest) != base:
        raise HTTPException(422, "bad file name")
    wait = 25.0 if name.endswith((".m4s", ".ts", ".mp4")) else 10.0
    if not _wait_file(sess, dest, timeout=wait):
        _log_hit(sid, "file", name, 404)
        raise HTTPException(404, "file not ready (session may have ended)")
    _log_hit(sid, "file", name, 200)
    if name.endswith(".m3u8"):
        media = "application/vnd.apple.mpegurl"
    elif name.endswith(".mp4"):
        media = "video/mp4"
    else:
        media = "video/iso.segment"
    return FileResponse(dest, media_type=media, filename=name,
                        headers={"Cache-Control": "no-store"})


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
    新播放器请用 sessions 口（可 ping/关播）。fMP4 下此口只返回 master 原文，
    变体/分片请走 sessions 口（旧 seg/ 口仅 TS 命名）。列表永不缓存。"""
    sdir, d = _ensure_hls(version_id, quality, audio, sub, start)
    _log_hit("", "playlist-legacy", str(version_id), 200)
    if (d.get("plan") or {}).get("seg") == "ts":
        body = _playlist_text(sdir)
    else:
        try:
            with open(os.path.join(sdir, "master.m3u8"), encoding="utf-8") as fh:
                body = fh.read()
        except OSError:
            raise HTTPException(404, "playlist not ready (session may have ended)")
    return PlainTextResponse(body, media_type="application/vnd.apple.mpegurl",
                             headers={"Cache-Control": "no-store"})


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
        _log_hit("", "seg-legacy", f"{vid}/{name}", 404)
        raise HTTPException(404, "segment not ready (session may have ended)")
    cands.sort(reverse=True)
    _log_hit("", "seg-legacy", f"{vid}/{name}", 200)
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
        cmd = [_media.ffmpeg_bin(), "-y", "-hide_banner",
               "-loglevel", "error", "-i", abs_p, "-map", f"0:{ff_idx}", dest]
        try:
            subprocess.run(cmd, timeout=120, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            raise HTTPException(500, f"subtitle extract failed: {e}")
        if not os.path.isfile(dest):
            raise HTTPException(500, "subtitle extract failed")
    return FileResponse(dest, media_type="text/vtt", filename=f"sub{si}.vtt")


# ===== ASS/SSA 客户端渲染（JASSUB）：原始 .ass + 内嵌/内置字体 =====
_FONT_RE = re.compile(r"^[^/\\]{1,120}\.(?:ttf|otf|ttc|woff2?)$", re.IGNORECASE)


def _sub_track(m: dict, abs_p: str, idx: int) -> dict:
    """定位指定字幕轨（不存在 404）；返回 track dict。"""
    info = _media_cached_or_probe(m, abs_p)
    subs = info.get("subs") or []
    try:
        si = int(idx)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad subtitle index")
    if not (0 <= si < len(subs)):
        raise HTTPException(404, "subtitle not found")
    return subs[si]


@router.get("/{version_id}/sub/{idx}.ass")
def hls_subtitle_ass(version_id: int, idx: int):
    """ASS/SSA 抽取为原始 .ass（JASSUB WASM/libass 客户端渲染，保留字体/位置/动画）。
    文本类非 ASS 字幕 415（走 .vtt）；图片字幕 415（只能烧录）。"""
    m, abs_p = _version_abs(version_id)
    track = _sub_track(m, abs_p, idx)
    if int(track.get("image") or 0):
        raise HTTPException(415, "image subtitle (PGS/VobSub): burn-in only")
    codec = _media.norm_codec(str(track.get("codec") or ""))
    if codec not in _media.ASS_SUBS:
        raise HTTPException(415, f"not an ass/ssa subtitle: {codec or 'unknown'}")
    if not _ffmpeg_ok():
        raise HTTPException(501, "ffmpeg not installed in server image")
    si = int(idx)
    sdir = os.path.join(TRANSCODE_DIR, str(int(m["id"])), "subs")
    os.makedirs(sdir, exist_ok=True)
    dest = os.path.join(sdir, f"{si}.ass")
    try:
        fresh = os.path.isfile(dest) and os.path.getmtime(dest) > os.path.getmtime(abs_p)
    except OSError:
        fresh = False
    if not fresh:
        ff_idx = track.get("ff_index", si)
        # 统一转 ASS：SSA 老格式也归一（libass 只吃 ASS 语法）
        cmd = [_media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
               "-i", abs_p, "-map", f"0:{ff_idx}", "-c:s", "ass", dest]
        try:
            subprocess.run(cmd, timeout=120, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            raise HTTPException(500, f"subtitle extract failed: {e}")
        if not os.path.isfile(dest):
            raise HTTPException(500, "subtitle extract failed")
    return FileResponse(dest, media_type="text/x-ssa", filename=f"sub{si}.ass")


def _dump_attachments(abs_p: str, fdir: str) -> None:
    """一次性 dump 全部附件到 fdir（MKV 字体给 JASSUB 用）。
    ffmpeg 以附件元数据文件名落盘且相对 CWD：在独立 .dump 子目录执行，只回收
    合法字体名（basename + 字体扩展），其余（路径逃逸/非字体）丢弃。"""
    marker = os.path.join(fdir, ".dumped")
    if os.path.isfile(marker):
        return
    tmp = os.path.join(fdir, ".dump")
    os.makedirs(tmp, exist_ok=True)
    rc = 1
    try:
        proc = subprocess.run(
            [_media.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
             "-dump_attachment:t", "", "-i", abs_p],
            cwd=tmp, timeout=120, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = proc.returncode
    except Exception:
        rc = 1
    for n in os.listdir(tmp):
        src = os.path.join(tmp, n)
        if os.path.isfile(src) and os.path.basename(n) == n and _FONT_RE.match(n):
            try:
                os.replace(src, os.path.join(fdir, n))
            except OSError:
                pass
    shutil.rmtree(tmp, ignore_errors=True)
    if rc == 0:
        try:
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write("1")
        except OSError:
            pass


@router.get("/{version_id}/fonts")
def stream_fonts(version_id: int):
    """ASS 渲染可用字体清单：MKV 内嵌 attachment + data/fonts 内置（运行时可投放）。
    附件字体在首次请求字体文件时懒抽取（见 /{id}/fonts/{name}）。"""
    m, abs_p = _version_abs(version_id)
    info = _media_cached_or_probe(m, abs_p)
    out = []
    for a in (info.get("attachments") or []):
        name = os.path.basename(str(a.get("name") or ""))
        if _FONT_RE.match(name):
            out.append({"name": name, "source": "attachment",
                        "url": f"/api/stream/{int(m['id'])}/fonts/{quote(name)}"})
    bdir = os.path.join(settings.data_dir, "fonts")
    if os.path.isdir(bdir):
        for name in sorted(os.listdir(bdir)):
            if _FONT_RE.match(name):
                out.append({"name": name, "source": "builtin",
                            "url": f"/api/stream/fonts/builtin/{quote(name)}"})
    return {"version_id": int(m["id"]), "fonts": out}


@router.get("/fonts/builtin/{name}")
def stream_builtin_font(name: str):
    """内置字体（data/fonts/*，运行时可投放；不在仓库里塞大字体）。"""
    fn = os.path.basename(unquote(name or ""))
    if not _FONT_RE.match(fn):
        raise HTTPException(422, "bad font name")
    path = os.path.join(settings.data_dir, "fonts", fn)
    if not os.path.isfile(path):
        raise HTTPException(404, "font not found")
    return FileResponse(path, media_type="font/woff2", filename=fn)


@router.get("/{version_id}/fonts/{name}")
def stream_attachment_font(version_id: int, name: str):
    """MKV 内嵌字体（attachment）：首次请求时从片源 dump 到 transcode/{id}/fonts/。"""
    m, abs_p = _version_abs(version_id)
    info = _media_cached_or_probe(m, abs_p)
    wanted = os.path.basename(unquote(name or ""))
    if not _FONT_RE.match(wanted):
        raise HTTPException(422, "bad font name")
    att = {os.path.basename(str(a.get("name") or "")) for a in (info.get("attachments") or [])}
    if wanted not in att:
        raise HTTPException(404, "font not found in this file")
    fdir = os.path.join(TRANSCODE_DIR, str(int(m["id"])), "fonts")
    os.makedirs(fdir, exist_ok=True)
    dest = os.path.join(fdir, wanted)
    if not os.path.isfile(dest):
        _dump_attachments(abs_p, fdir)
    if not os.path.isfile(dest):
        raise HTTPException(404, "font extract failed")
    return FileResponse(dest, media_type="font/ttf", filename=wanted)
