"""routers.stream.common（自 app/routers/stream.py 拆分，评审 B9/R12-Q1；经 stream 门面使用）。"""
import os
import re
import signal
import time
import json
import hashlib
import shutil
import subprocess
import threading
from collections import deque
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ... import library_paths
from ... import store
from ... import storage
from ...db import TRANSCODE_DIR
from ... import media as _media
from ... import playback as _playback
from ...log import get_logger
logger = get_logger("stream.common")
__all__ = ['_reap_orphans', '_write_session_meta', '_clear_session_meta', 'router', '_SEG_RE', '_SESS_FILE_RE', '_TTL', '_min_segs', 'PLAN_VERSION', '_SESS_IDLE', '_UNCLAIMED_TTL', '_CACHE_MIN_AGE', '_sessions', '_sess_lock', '_MEDIA_START_CACHE', '_hits', '_max_transcodes', '_hls_sem', '_log_hit', '_version_source', '_media_cached_or_probe', 'version_cache_dir', '_quality_key', '_session_key', '_plan_marker', '_session_dir', '_media_start_for', '_write_master', '_tree_size', '_cache_cap_bytes', '_transcode_cache_scan', '_evict_transcode_cache', '_purge_old', 'CacheCleanBody', 'clean_stream_cache', '_kill_proc', '_drop_session', 'shutdown_sessions', 'drop_sessions_for_version', '_dead_sessions', '_sweeper', '_sweeper_thread', '_marker_matches', '_variant_playlists', '_has_endlist', '_playlist_endlist', '_write_complete_marker', '_session_complete', '_ffmpeg_ok', '_seg_count', '_video_seg_prefix', '_live_sessions_for', '_find_live_session', '_rm_tmp', '_run_ffmpeg_to_temp', '_get_session', '_playlist_text']

router = APIRouter(prefix="/api/stream")


_SEG_RE = re.compile(r"^(?:[A-Za-z0-9]+_seg\d+\.m4s|seg\d+\.ts)$")


_SESS_FILE_RE = re.compile(
    r"^(?:out_[A-Za-z0-9]+\.m3u8|[A-Za-z0-9]+_init\.mp4|[A-Za-z0-9]+_seg\d+\.m4s|seg\d+\.ts)$")


_TTL = 24 * 3600


def _min_segs(plan: dict | None = None) -> int:
    """首屏等待的视频分片数：copy/remux 出片快，1 片即回；视频重编/烧录慢，留 2 片缓冲。
    copy 档分片按源码 GOP 切（可能 9s/片），原固定等 3 片=27s 内容，慢链路上起播黑屏很久
    （用户 2026-09 反馈）。env `MIN_SEGS_COPY`/`MIN_SEGS_TRANSCODE` 可调（1~10）。"""
    p = plan or {}
    transcode = (not p.get("vcopy")) or p.get("sub") == "burn"
    env = "MIN_SEGS_TRANSCODE" if transcode else "MIN_SEGS_COPY"
    default = 2 if transcode else 1
    try:
        return max(1, min(int(os.getenv(env, str(default)) or default), 10))
    except (TypeError, ValueError):
        return default


PLAN_VERSION = 2


_SESS_IDLE = 600       # 会话无心跳保活期（秒）


# 会话从未被客户端认领（没有任何 playlist/分片/心跳请求）就断开时，快速收割的宽限期。
# 场景：建会话 POST 期间用户关窗 → 前端 abort 拿不到 sid → 无人 DELETE，ffmpeg 变孤儿。
_UNCLAIMED_TTL = 90


# 转码缓存目录保护期：新建/刚写过的目录不参与限额淘汰（防与 ffmpeg 写入竞态）。
_CACHE_MIN_AGE = 300.0


_sessions: dict[str, dict] = {}

# Client leases are separate from shared FFmpeg producers. All mutations, including
# first registration, are serialized by _sess_lock; initializing tasks reserve a slot.
_tasks: dict[tuple, dict] = {}


_sess_lock = threading.RLock()


_MEDIA_START_CACHE: dict = {}


_hits: deque = deque(maxlen=200)


def _max_transcodes() -> int:
    """并发转码上限（评审 B8/R12-D4）：env MAX_TRANSCODES，默认 2（弱 NAS 可调 1）。"""
    try:
        return max(1, min(int(os.getenv("MAX_TRANSCODES", "2") or 2), 8))
    except (TypeError, ValueError):
        return 2


_hls_sem = threading.Semaphore(_max_transcodes())


_SESSION_META = "session.json"


def _write_session_meta(sdir: str, sid: str, vid: int, proc, backend: str,
                        attempt: int) -> None:
    """会话运行元数据落盘（评审 R12-Q3）：重启后据此清理孤儿 ffmpeg。"""
    try:
        with open(os.path.join(sdir, _SESSION_META), "w", encoding="utf-8") as fh:
            json.dump({"sid": sid, "vid": int(vid), "pid": int(proc.pid),
                       "backend": backend, "attempt": attempt,
                       "started_at": int(time.time())}, fh, sort_keys=True)
    except OSError as e:
        logger.debug("write session meta failed dir=%s: %s", sdir, e)


def _clear_session_meta(sdir: str) -> None:
    try:
        p = os.path.join(sdir, _SESSION_META)
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass


def _reap_orphans() -> int:
    """启动清道夫（评审 R12-Q3）：上次 SIGKILL/崩溃留下的孤儿 ffmpeg（session.json
    记录 pid）若仍存活则杀掉；用 /proc/<pid>/cwd 精确比对会话目录防误杀。"""
    killed = 0
    try:
        vids = os.listdir(TRANSCODE_DIR)
    except OSError:
        return 0
    for vid in vids:
        vd = os.path.join(TRANSCODE_DIR, vid)
        try:
            names = os.listdir(vd)
        except OSError:
            continue
        for name in names:
            sdir = os.path.join(vd, name)
            meta = os.path.join(sdir, _SESSION_META)
            if not os.path.isfile(meta):
                continue
            try:
                with open(meta, encoding="utf-8") as fh:
                    m = json.load(fh)
                pid = int(m.get("pid") or 0)
            except (OSError, ValueError):
                _clear_session_meta(sdir)
                continue
            if pid > 0:
                try:
                    cwd = os.readlink(f"/proc/{pid}/cwd")
                except OSError:
                    cwd = ""
                if cwd and os.path.realpath(cwd) == os.path.realpath(sdir):
                    try:
                        os.kill(pid, signal.SIGKILL)
                        killed += 1
                        logger.warning("killed orphan ffmpeg pid=%s dir=%s", pid, sdir)
                    except OSError as e:
                        logger.debug("kill orphan failed pid=%s: %s", pid, e)
            _clear_session_meta(sdir)
    return killed


def _log_hit(sid: str, kind: str, name: str, status: int) -> None:
    try:
        with _sess_lock:
            _hits.append({"t": int(time.time()), "sid": sid or "",
                          "kind": kind, "name": name or "", "status": int(status)})
    except Exception:
        pass


def _kind_prefix(kind) -> str:
    """会话/缓存目录前缀：电影 m / 剧集 e / 花絮-剧场版 x（id 空间独立防串）。"""
    k = str(kind or "movie")
    if k == "episode":
        return "e"
    if k == "extra":
        return "x"
    return "m"


def version_cache_dir(kind, item_id: int) -> str:
    """按 (kind, id) 隔离的字幕/字体缓存目录（movie=m<id>，episode=e<id>，extra=x<id>）。"""
    d = os.path.join(TRANSCODE_DIR, f"{_kind_prefix(kind)}{int(item_id)}")
    os.makedirs(d, exist_ok=True)
    return d


def _version_source(version_id: int, kind: str = "movie") -> tuple[dict, "storage.MediaSource"]:
    """播放行 + MediaSource（本地路径或远程内网 URL）。行不存在 404；文件缺失 410
    （前端按无效文件置灰）；远程离线 503（指导 §19/§35：Offline ≠ Deleted）。
    `kind=movie|episode`（F 阶段）：剧集与电影共用同一套流接口。"""
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    m = store.get_playable(kind, vid)
    if not m:
        raise HTTPException(404, "version not found")
    try:
        src = storage.media_source(
            m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID, m["file_path"])
    except storage.StorageNotFound:
        raise HTTPException(410, "file missing")
    except storage.StorageOffline as e:
        raise HTTPException(503, f"source offline: {e}")
    except storage.StorageError as e:
        raise HTTPException(500, f"storage error: {e}")
    return m, src


def _media_cached_or_probe(m: dict, src) -> dict:
    """缓存优先；环境错误缓存/探测结构过期（probe_ver 低）→ 自动重探（自愈，免 backfill）。
    `src` 为 MediaSource（本地/远程统一；str 兼容旧调用 = 本地路径）。"""
    kind = m.get("kind") or "movie"
    cached = store.get_media_info(int(m["id"]), kind)
    if cached and int(cached.get("probed_at") or 0) > 0:
        stale = int(cached.get("probe_ver") or 0) < int(getattr(_media, "PROBE_VERSION", 0))
        # 脏行自愈：环境错误缓存视为未探测（旧版本落库的，一次即洗掉）
        if stale or (not cached.get("playable") and _media.is_retryable_error(
                str(cached.get("probe_error") or ""))):
            cached = None
        else:
            return cached
    if isinstance(src, str):
        info = _media.probe(src)
    else:
        info = _media.probe(src.input, size=src.size)
    return store.upsert_media_info(int(m["id"]), info, kind)


def _quality_key(plan: dict) -> str:
    """会话目录的档位键：由实际产物（plan）决定，而非用户请求字符串。
    这样 auto/原画/1080p/720p 落到同一 plan 时共用目录与静态成品（预转码命中在线播）：
    - copy（vcopy 且不封顶）→ "copy"；重编封顶 → "h720"/"h1080"；重编不封顶 → "src"。
    - 烧录（sub=burn）追加 "_burn"：与非烧录成品的产物不同，分目录避免互踩清空（评审 R12-D3）。"""
    plan = plan or {}
    burn = "_burn" if str(plan.get("sub") or "") == "burn" else ""
    if plan.get("vcopy") and not int(plan.get("height") or 0):
        return "copy" + burn
    h = int(plan.get("height") or 0)
    return (f"h{h}" if h else "src") + burn


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
    - 带 `PLAN_VERSION`：编码参数/像素格式等产物规则变化时旧成品自动失效重转；
    - 封装、精确起点与所有实际产物字段进入键，由完整键哈希隔离输出目录；
    - 非烧录字幕归一（文本/ASS 字幕走独立接口，不影响 ffmpeg 输出），
      否则换个字幕就把整片成品作废；烧录保留（sub_ff_index 在 plan 内）。"""
    p = dict(plan or {})
    p["seg"] = p.get("seg") or "fmp4"
    a_key = int(audio or 0)
    if (plan or {}).get("seg") != "ts":
        # fMP4 全部音轨都产成 rendition，产物与所选音轨无关 → 选择不进键（秒开复用）
        p["audio_idx"] = None
        p["acopy"] = None  # fMP4 uses each audios[].copy, never the selected track's flag.
        a_key = 0
    if p.get("sub") != "burn":
        p["sub"] = "none"
        p["sub_idx"] = None
    try:
        st = max(0.0, float(start or 0))
    except (TypeError, ValueError):
        st = 0
    return json.dumps({"v": PLAN_VERSION, "a": a_key, "start": st, "plan": p},
                      sort_keys=True)


def _artifact_key(plan: dict, audio: int, marker: str) -> str:
    """Every output-changing field participates, including audio policy and burn track."""
    return _session_key(plan, audio) + "_" + hashlib.sha256(marker.encode()).hexdigest()


def _source_plan(plan: dict, row: dict, src) -> dict:
    """Invalidate an artifact when the source is replaced, without keying transient URLs."""
    return {**plan, "source": {"library": row.get("library_id"),
                              "path": row.get("file_path"),
                              "size": getattr(src, "size", None),
                              "mtime": getattr(src, "mtime", None)}}


def _session_dir(version_id: int, key: str, start: float, kind: str = "movie") -> str:
    k = re.sub(r"[^a-z0-9_]+", "", (key or "src").strip().lower()) or "src"
    try:
        st = max(0, int(float(start or 0)))
    except (TypeError, ValueError):
        st = 0
    pre = _kind_prefix(kind)
    d = os.path.join(TRANSCODE_DIR, f"{pre}{int(version_id)}", f"{k}_s{st}")
    os.makedirs(d, exist_ok=True)
    return d


def _media_start_for(version_id: int, input_url: str, start: float, plan: dict,
                     kind: str = "movie") -> float:
    """会话片内 0 对应的源时间（copy=目标前关键帧，转码/烧录=start）。
    按 kind/version/输入/精确起点/copy 状态缓存，避免 copy 关键帧偏移污染重编时间轴。
    `input_url` 可为本地路径或内网 URL（远程直读）。"""
    vcopy_seek = bool((plan or {}).get("vcopy")) and (plan or {}).get("sub") != "burn"
    key = (str(kind or "movie"), int(version_id), str(input_url), float(start or 0),
           vcopy_seek, json.dumps((plan or {}).get("source"), sort_keys=True))
    with _sess_lock:
        hit = _MEDIA_START_CACHE.get(key)
    if hit is not None:
        return hit
    ms = _playback.actual_media_start(input_url, start, vcopy_seek)
    with _sess_lock:
        if len(_MEDIA_START_CACHE) >= 64:
            _MEDIA_START_CACHE.pop(next(iter(_MEDIA_START_CACHE)))  # FIFO（评审 B7/R12-B6）
        _MEDIA_START_CACHE[key] = ms
    return ms


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
        if tgt_h and src_h and src_w:
            h = min(tgt_h, src_h)
            w = max(2, round(src_w * h / src_h / 2) * 2)
        else:
            w, h = src_w, src_h
        vc = _playback.transcoded_video_codec(w, h)
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS"]
    audio_codecs = []
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
        # CODECS covers every rendition in AUDIO="aud", not only its default.
        # Native HLS clients use this to prepare/identify the audio tracks.
        if ac not in audio_codecs:
            audio_codecs.append(ac)
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
    codecs = ([vc] if vc else []) + audio_codecs
    if codecs:
        si.append('CODECS="' + ",".join(codecs) + '"')
    if audios:
        si.append('AUDIO="aud"')
    lines.append("#EXT-X-STREAM-INF:" + ",".join(si))
    lines.append("out_video.m3u8")
    try:
        with open(os.path.join(sdir, "master.m3u8"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as e:
        logger.warning("write master failed dir=%s: %s", sdir, e)


def _tree_size(path: str) -> int:
    """目录树字节数（best-effort；用于缓存限额统计）。"""
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _cache_cap_bytes() -> int:
    """转码缓存总量上限（env `TRANSCODE_CACHE_GB`，默认 10；0=只按 TTL 回收）。"""
    try:
        gb = float(os.getenv("TRANSCODE_CACHE_GB", "10") or 0)
    except (TypeError, ValueError):
        gb = 10.0
    return int(max(0.0, gb) * (1 << 30))


def _transcode_cache_scan(now: float) -> tuple[int, list[tuple[float, int, str]]]:
    """扫 TRANSCODE_DIR：返回 (总字节, 可淘汰候选 [(mtime, size, dir)]，最旧在前)。
    活动会话目录（进程在跑，或 10min 内被取过流）与创建 <5min 的新目录不进候选：
    防删掉正在播/正在写的分片，也防与「建目录→登记会话」的毫秒级窗口竞态。"""
    with _sess_lock:
        live = _protected_dirs(now)
    total = 0
    cands: list[tuple[float, int, str]] = []
    try:
        vids = os.listdir(TRANSCODE_DIR)
    except OSError:
        return 0, []
    for vid in vids:
        vd = os.path.join(TRANSCODE_DIR, vid)
        if not os.path.isdir(vd):
            continue
        try:
            sess_list = os.listdir(vd)
        except OSError:
            continue
        for sess in sess_list:
            sd = os.path.join(vd, sess)
            try:
                if not os.path.isdir(sd):
                    continue
                mtime = os.path.getmtime(sd)
            except OSError:
                continue
            size = _tree_size(sd)
            total += size
            if os.path.normpath(sd) in live or now - mtime < _CACHE_MIN_AGE:
                continue
            cands.append((mtime, size, sd))
    cands.sort(key=lambda x: x[0])
    return total, cands


def _protected_dirs(now: float) -> set[str]:
    """Caller holds _sess_lock; include reservations before Popen or the first lease."""
    live = {os.path.normpath(t["sdir"]) for t in _tasks.values()}
    for s in _sessions.values():
        proc = s.get("proc")
        if ((proc is not None and proc.poll() is None)
                or now - float(s.get("last_ping") or 0) < _SESS_IDLE):
            live.add(os.path.normpath(str(s.get("sdir") or "")))
    return live


def _evict_transcode_cache(now: float | None = None, cap: int | None = None,
                           dry_run: bool = False, delete_all: bool = False) -> dict:
    """转码缓存回收：超 cap（默认 TRANSCODE_CACHE_GB）按最旧淘汰到 90% 水位（滞回）；
    delete_all=True（手动清理）删掉全部可淘汰目录；dry_run 只统计。跳过活动目录。"""
    now = time.time() if now is None else now
    cap = _cache_cap_bytes() if cap is None else max(0, int(cap))
    total, cands = _transcode_cache_scan(now)
    cand_bytes = sum(size for _m, size, _p in cands)
    stat = {"total": total, "cap": cap, "candidates": len(cands),
            "candidate_bytes": cand_bytes, "freed": 0, "removed": 0}
    if not delete_all and (cap <= 0 or total <= cap):
        return stat
    target = int(cap * 0.9) if cap > 0 else 0
    freed = removed = 0
    for _mtime, size, sd in cands:
        if not delete_all and total - freed <= target:
            break
        with _sess_lock:
            # Recheck atomically: a client may have attached since the directory scan.
            if os.path.normpath(sd) in _protected_dirs(time.time()):
                continue
            if not dry_run:
                shutil.rmtree(sd, ignore_errors=True)
            freed += size
            removed += 1
    stat.update({"freed": freed, "removed": removed})
    return stat


_cap_lock = threading.Lock()
_cap_last = 0.0
_CAP_INTERVAL = 300.0   # 限额扫描节流：避免每次建会话都全目录 walk


class CacheCleanBody(BaseModel):
    dry_run: bool = True


@router.post("/cache/clean")
def clean_stream_cache(body: CacheCleanBody | None = None) -> dict:
    """转码缓存手动清理（设置页维护面板，全局不限于媒体库）：dry_run 预览可回收量；
    执行删掉全部可淘汰目录（跳过正在转码/10min 内播放过的会话与 5min 内新目录）。"""
    b = body or CacheCleanBody()
    r = _evict_transcode_cache(dry_run=bool(b.dry_run), delete_all=True)
    return {"dry_run": bool(b.dry_run), **r}


def _purge_old() -> None:
    """转码缓存回收（best-effort，失败自吞）：① 超 24h TTL 删除会话目录；
    ② 总量超 TRANSCODE_CACHE_GB 时按最旧淘汰到 90% 水位（节流 5min 一次）。"""
    try:
        now = time.time()
        for vid in os.listdir(TRANSCODE_DIR):
            vd = os.path.join(TRANSCODE_DIR, vid)
            if not os.path.isdir(vd):
                continue
            for sess in os.listdir(vd):
                sd = os.path.join(vd, sess)
                try:
                    with _sess_lock:
                        if (os.path.isdir(sd) and now - os.path.getmtime(sd) > _TTL
                                and os.path.normpath(sd) not in _protected_dirs(now)):
                            shutil.rmtree(sd, ignore_errors=True)
                except OSError:
                    continue
    except OSError:
        pass
    global _cap_last
    with _cap_lock:
        if time.time() - _cap_last < _CAP_INTERVAL:
            return
        _cap_last = time.time()
    try:
        _evict_transcode_cache()
    except Exception as e:   # 缓存回收绝不打断播放/扫描
        logger.debug("transcode cache cap pass failed: %s", e)


def _kill_proc(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception as e:
        logger.debug("terminate transcode failed: %s", e)
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)   # 评审 B7/R12-B7：kill 后等待回收
        except Exception as e:
            logger.warning("kill transcode failed: %s", e)


def _drop_session(sid: str, kill: bool = True) -> None:
    """Remove one lease atomically; slow process termination never holds the global lock."""
    with _sess_lock:
        sess = _sessions.pop(sid, None)
        if not sess:
            return
        task = sess.get("task")
        if task is not None:
            task["owners"].discard(sid)
            if task["owners"] or task.get("stopping"):
                return
            # Keep the cancelled task registered while stopping. New requests cannot
            # attach or launch another producer into its directory during this gap.
            task["cancelled"] = True
            task["stopping"] = True
            proc = task.get("proc")
        else:
            proc = sess.get("proc")
    if task is not None:
        _kill_proc(proc)
        with _sess_lock:
            _release_task_slot(task)
            if proc is None or proc.poll() is not None:
                if _tasks.get(task["key"]) is task:
                    _tasks.pop(task["key"])
                    _clear_session_meta(task["sdir"])
                task["done"].set()
            elif proc is not None and not task.get("watched"):
                task["watched"] = True
                threading.Thread(target=_watch_task, args=(task, proc), daemon=True).start()
            task["stopping"] = False
    elif kill:
        _kill_proc(proc)
        with _sess_lock:
            # Legacy/static leases do not own a producer; avoid clearing newer metadata.
            if not any(t["sdir"] == sess.get("sdir") for t in _tasks.values()):
                _clear_session_meta(sess.get("sdir") or "")


def _release_task_slot(task: dict) -> None:
    """Exactly once, only after its process has exited (caller holds _sess_lock)."""
    proc = task.get("proc")
    if proc is not None and proc.poll() is None:
        return
    sem = task.pop("slot", None)
    if sem is not None:
        sem.release()


def _watch_task(task: dict, proc) -> None:
    """One completion observer owns slot release and publishes the finished artifact."""
    try:
        rc = proc.wait()
        with _sess_lock:
            if (not task.get("cancelled") and rc == 0
                    and _seg_count(task["sdir"]) > 0 and _playlist_endlist(task["sdir"])):
                _write_complete_marker(task["sdir"], task["plan_key"])
                task["complete"] = True
                for sid in task["owners"]:
                    if sid in _sessions:
                        _sessions[sid]["complete"] = True
            elif not task.get("cancelled"):
                task["error"] = HTTPException(500, "transcode ended before completion")
            # A cancelled producer can have been replaced; never clear its successor's meta.
            if _tasks.get(task["key"]) is task:
                _clear_session_meta(task["sdir"])
                if not task["owners"]:
                    _tasks.pop(task["key"])
    except Exception as e:
        logger.warning("transcode completion observer failed: %s", e)
        with _sess_lock:
            task["error"] = HTTPException(500, "transcode completion failed")
            task["cancelled"] = True
        _kill_proc(proc)
    finally:
        with _sess_lock:
            _release_task_slot(task)
            if proc.poll() is not None:
                if _tasks.get(task["key"]) is task and not task["owners"]:
                    _tasks.pop(task["key"])
                    _clear_session_meta(task["sdir"])
                task["done"].set()


def shutdown_sessions() -> None:
    """应用退出（uvicorn 优雅关闭 / docker stop）：杀掉全部转码进程，避免孤儿 ffmpeg
    继续占 CPU 写分片（SIGKILL 场景兜不住，分片靠 TTL 清理）。"""
    with _sess_lock:
        sids = list(_sessions.keys())
    for sid in sids:
        _drop_session(sid, kill=True)


def drop_sessions_for_version(version_id: int, kind: str = "movie") -> int:
    """关掉某版本的在线/预转码会话（删库/删片用，防 ffmpeg 继续写分片）。"""
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        return 0
    with _sess_lock:
        sids = [sid for sid, s in _sessions.items()
                if int(s.get("vid") or -1) == vid
                and str(s.get("kind") or "movie") == kind]
    for sid in sids:
        _drop_session(sid, kill=True)
    return len(sids)


def _dead_sessions(now: float) -> list[str]:
    """应收割的会话 id（无心跳超期 / 未认领超宽限 / 进程已退出超期）；纯计算便于回归测试。"""
    dead = []
    with _sess_lock:
        for sid, s in list(_sessions.items()):
            task = s.get("task")
            if task is not None and not task["ready"].is_set():
                continue  # Startup has its own bounded timeout; no client can claim it yet.
            proc = s.get("proc")
            exited = proc is not None and proc.poll() is not None
            idle = now - float(s.get("last_ping") or now)
            # Prewarm has its own bounded worker lifetime; all client leases expire.
            if s.get("prewarm"):
                continue  # The prewarm worker owns a bounded lifetime, without heartbeats.
            elif s.get("unclaimed"):
                # 客户端从未取过流（关窗 abort 拿不到 sid / 会话是给别人的）：快速回收
                age = now - float(s.get("created") or s.get("last_ping") or now)
                if age > _UNCLAIMED_TTL:
                    dead.append(sid)
            elif idle > _SESS_IDLE or (exited and idle > 300):
                dead.append(sid)
    return dead


def _sweeper() -> None:
    """后台收尸：无心跳超期 / 进程已退出超期 → 杀进程删会话（分片留 TTL 清理）。"""
    while True:
        time.sleep(60)
        for sid in _dead_sessions(time.time()):
            _drop_session(sid, kill=True)
        _purge_old()


_sweeper_thread = threading.Thread(target=_sweeper, daemon=True)


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


def _session_complete(sdir: str, plan_key: str) -> bool:
    """整片已自然转完（静态 VOD）：complete.json 存在 + 列表带 ENDLIST + 有分片 + plan 一致。
    仅凭 ENDLIST 不够——被杀的残缺会话也会写 ENDLIST（SIGTERM 时 ffmpeg 会写 trailer）。"""
    if not os.path.isfile(os.path.join(sdir, "complete.json")):
        return False
    return (_playlist_endlist(sdir) and _seg_count(sdir) > 0
            and _marker_matches(sdir, plan_key, "complete.json"))


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


def _live_sessions_for(vid: int, kind: str | None = None) -> list[dict]:
    """该 item 当前活着的会话（含预转码注册的），供 prewarm 复用/避让（评审 P1-07）。
    `kind` 提供时同时匹配类型（电影/剧集 id 空间独立，防串）。"""
    out = []
    with _sess_lock:
        for sid, s in list(_sessions.items()):
            if int(s.get("vid") or -1) != int(vid):
                continue
            if kind is not None and str(s.get("kind") or "movie") != str(kind):
                continue
            proc = s.get("proc")
            if proc is not None and proc.poll() is not None:
                continue
            out.append({"sid": sid, "sdir": s.get("sdir") or "", "proc": proc,
                        "plan_key": s.get("plan_key") or ""})
    return out


def _find_live_session(vid: int, plan_key: str,
                       kind: str | None = None) -> dict | None:
    """同 plan 的活会话（在线播或另一 prewarm），可附着复用。"""
    return next((x for x in _live_sessions_for(vid, kind)
                 if x["plan_key"] == plan_key), None)


def _rm_tmp(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _run_ffmpeg_to_temp(dest: str, argv_of_tmp, timeout: int, err_msg: str) -> None:
    """ffmpeg 产物先写 dest 同扩展名 .tmp，再 os.replace 原子替换（评审 B5a-6/R13-D4）：
    并发请求/中途失败都不会留下可被读到的半成品字幕。"""
    ext = os.path.splitext(dest)[1]
    tmp = (dest[:-len(ext)] + ".tmp" + ext) if ext else (dest + ".tmp")
    try:
        subprocess.run(argv_of_tmp(tmp), timeout=timeout, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        _rm_tmp(tmp)
        raise HTTPException(500, f"{err_msg}: {e}")
    if not os.path.isfile(tmp):
        raise HTTPException(500, err_msg)
    try:
        os.replace(tmp, dest)
    except OSError as e:
        _rm_tmp(tmp)
        raise HTTPException(500, f"{err_msg}: {e}")


def _get_session(sid: str) -> dict:
    with _sess_lock:
        sess = _sessions.get(sid or "")
    if not sess:
        raise HTTPException(404, "no such transcode session (re-POST sessions)")
    sess["last_ping"] = time.time()
    # 任何一次真实取流请求（playlist/分片/心跳/调试）= 客户端已拿到 sid，撤销快速收割
    sess["unclaimed"] = False
    return sess


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
