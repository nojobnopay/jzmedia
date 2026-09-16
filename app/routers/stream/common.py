"""routers.stream.common（自 app/routers/stream.py 拆分，评审 B9/R12-Q1；经 stream 门面使用）。"""
import os
import re
import time
import json
import shutil
import subprocess
import threading
from collections import deque
from fastapi import APIRouter, HTTPException
from ... import store
from ...config import settings
from ...db import TRANSCODE_DIR
from ... import caps as _caps
from ... import media as _media
from ... import playback as _playback
import uuid
from ...log import get_logger
logger = get_logger("stream.common")
__all__ = ['router', '_SEG_RE', '_SESS_FILE_RE', '_TTL', '_MIN_SEGS', 'PLAN_VERSION', '_SESS_IDLE', '_sessions', '_sess_lock', '_MEDIA_START_CACHE', '_hits', '_max_transcodes', '_hls_sem', '_log_hit', '_version_abs', '_media_cached_or_probe', '_quality_key', '_session_key', '_plan_marker', '_session_dir', '_media_start_for', '_write_master', '_purge_old', '_kill_proc', '_drop_session', 'shutdown_sessions', '_sweeper', '_sweeper_thread', '_marker_matches', '_variant_playlists', '_has_endlist', '_playlist_endlist', '_write_complete_marker', '_watch_completion', '_session_complete', '_ffmpeg_ok', '_seg_count', '_video_seg_prefix', '_live_sessions_for', '_find_live_session', '_register_prewarm_session', '_rm_tmp', '_run_ffmpeg_to_temp', '_get_session', '_playlist_text']

router = APIRouter(prefix="/api/stream")


_SEG_RE = re.compile(r"^(?:[A-Za-z0-9]+_seg\d+\.m4s|seg\d+\.ts)$")


_SESS_FILE_RE = re.compile(
    r"^(?:out_[A-Za-z0-9]+\.m3u8|[A-Za-z0-9]+_init\.mp4|[A-Za-z0-9]+_seg\d+\.m4s|seg\d+\.ts)$")


_TTL = 24 * 3600


_MIN_SEGS = 3          # 首屏等待分片数（fMP4 4s → 约 12s 内容；TS 6s → 18s）


PLAN_VERSION = 2


_SESS_IDLE = 600       # 会话无心跳保活期（秒）


_sessions: dict[str, dict] = {}


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
    - 带 `PLAN_VERSION`：编码参数/像素格式等产物规则变化时旧成品自动失效重转；
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
    return json.dumps({"v": PLAN_VERSION, "a": a_key, "start": st, "plan": p},
                      sort_keys=True)


def _session_dir(version_id: int, key: str, start: float) -> str:
    k = re.sub(r"[^a-z0-9_]+", "", (key or "src").strip().lower()) or "src"
    try:
        st = max(0, int(float(start or 0)))
    except (TypeError, ValueError):
        st = 0
    d = os.path.join(TRANSCODE_DIR, str(int(version_id)), f"{k}_s{st}")
    os.makedirs(d, exist_ok=True)
    return d


def _media_start_for(version_id: int, abs_p: str, start: float, plan: dict) -> float:
    """会话片内 0 对应的源时间（copy=目标前关键帧，转码/烧录=start）。
    按 (version, int(start)) 缓存探测结果，重开会话/复用同一 start 时不重复 ffprobe。"""
    try:
        st_key = max(0, int(float(start or 0)))
    except (TypeError, ValueError):
        st_key = 0
    key = (int(version_id), st_key)
    with _sess_lock:
        hit = _MEDIA_START_CACHE.get(key)
    if hit is not None:
        return hit
    vcopy_seek = bool((plan or {}).get("vcopy")) and (plan or {}).get("sub") != "burn"
    ms = _playback.actual_media_start(abs_p, start, vcopy_seek)
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
    except OSError as e:
        logger.warning("write master failed dir=%s: %s", sdir, e)


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
            proc.wait(timeout=3)   # 评审 B7/R12-B7：kill 后等待回收
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
                # 完工静态会话（proc=None）与预转码任务：只按文件 TTL 收记录，不按 idle 杀
                # （预转码无客户端心跳，但有自己的 job 超时与生命周期，评审 P1-07）
                if s.get("complete") or s.get("prewarm"):
                    if idle > _TTL:
                        dead.append(sid)
                elif idle > _SESS_IDLE or (exited and idle > 300):
                    dead.append(sid)
        for sid in dead:
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


def _live_sessions_for(vid: int) -> list[dict]:
    """该版本当前活着的会话（含预转码注册的），供 prewarm 复用/避让（评审 P1-07）。"""
    out = []
    with _sess_lock:
        for sid, s in list(_sessions.items()):
            if int(s.get("vid") or -1) != int(vid):
                continue
            proc = s.get("proc")
            if proc is not None and proc.poll() is not None:
                continue
            out.append({"sid": sid, "sdir": s.get("sdir") or "", "proc": proc,
                        "plan_key": s.get("plan_key") or ""})
    return out


def _find_live_session(vid: int, plan_key: str) -> dict | None:
    """同 plan 的活会话（在线播或另一 prewarm），可附着复用。"""
    return next((x for x in _live_sessions_for(vid) if x["plan_key"] == plan_key), None)


def _register_prewarm_session(vid: int, sdir: str, plan: dict, proc,
                              plan_key: str, backend: str, attempt: int) -> str:
    """把 prewarm 转码进程注册进 `_sessions`：在线播可复用（同 plan 秒开）、
    换档会按既有逻辑杀掉旧会话、关播 DELETE 会被识别为共享任务而不误杀。"""
    sid = uuid.uuid4().hex[:16]
    with _sess_lock:
        _sessions[sid] = {"proc": proc, "sdir": sdir, "vid": int(vid),
                          "plan": plan, "plan_key": plan_key,
                          "caps_hash": _caps.caps_hash(_caps.default_caps()),
                          "backend": backend, "attempt": attempt,
                          "prewarm": True, "last_ping": time.time()}
    return sid


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

