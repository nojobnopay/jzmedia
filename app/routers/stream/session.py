"""routers.stream.session（自 app/routers/stream.py 拆分，评审 B9/R12-Q1；经 stream 门面使用）。"""
import os
import time
import json
import subprocess
import threading
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from ... import caps as _caps
from ... import playback as _playback
import uuid
from ...log import get_logger
logger = get_logger("stream.session")
from .common import (_MIN_SEGS, _SEG_RE, _SESS_FILE_RE, _has_endlist, _drop_session, _ffmpeg_ok, _get_session, _hls_sem, _hits,
                     _log_hit, _media_cached_or_probe, _media_start_for, _plan_marker,
                     _playlist_endlist, _playlist_text, _purge_old, _seg_count,
                     _sess_lock, _write_session_meta,
                     _sessions, _session_complete, _session_dir, _session_key, _variant_playlists,
                     _version_abs, _video_seg_prefix, _watch_completion, _write_master, router)
from .media import _media_payload
from .subtitles import _sub_list
__all__ = ['SessionBody', '_spawn_session', 'hls_session_create', 'hls_session_playlist', '_wait_file', 'hls_session_segment', 'hls_session_debug', 'hls_session_ping', 'hls_session_close', 'hls_session_file']

class SessionBody(BaseModel):
    quality: str = "auto"
    audio: int = 0
    sub: int | None = None
    start: float = 0
    caps: dict | None = None
    force_burn: bool = False
    kind: str = "movie"   # F：movie|episode


def _spawn_session(version_id: int, quality: str, audio: int,
                   sub: int | None, start: float,
                   caps: dict | None = None,
                   force_burn: bool = False,
                   kind: str = "movie") -> tuple[str, str, dict]:
    """起后台转码会话（渐进式）：校验→plan→Popen→等前 _MIN_SEGS 分片。
    返回 (session_id, session_dir, plan_result)。direct/无 ffmpeg 等直接抛对应 HTTP 状态。
    caps 参与 plan 与 plan_key（不同客户端能力不复用同一转码档）；force_burn 为
    客户端图片字幕解码失败时的烧录降级（见 playback._subtitle_mode）。"""
    m, abs_p = _version_abs(version_id, kind)
    k = m.get("kind") or "movie"
    info = _media_cached_or_probe(m, abs_p)
    if not info.get("playable"):
        raise HTTPException(422, f"unplayable: {info.get('probe_error') or 'probe failed'}")
    caps_n = _caps.default_caps() if caps is None else _caps.normalize_caps(caps)
    d = _playback.plan(_media_payload(m, info), caps=caps_n, quality=quality,
                       audio_idx=audio, sub_idx=sub, force_burn=force_burn)
    if d["method"] == "blocked":
        raise HTTPException(422, "unplayable")
    if d["method"] == "direct":
        raise HTTPException(400, "use direct_url (Direct Play, no HLS needed)")
    # 图片字幕烧录（仅 VobSub/降级）：内嵌取真实流号（ff_index）；外挂（.idx/.sub）
    # 记 sub_sidecar，build_cmd 以第二输入 + [1:s:0] overlay（时间轴对齐同主输入）。
    if (d.get("plan") or {}).get("sub") == "burn":
        subs = _sub_list(m, info)
        try:
            si = int(sub if sub is not None else -1)
        except (TypeError, ValueError):
            si = -1
        if not (0 <= si < len(subs)):
            raise HTTPException(422, "subtitle not found")
        track = subs[si] or {}
        if not int(track.get("image") or 0):
            raise HTTPException(422, "not an image subtitle")
        if track.get("source") == "sidecar":
            d["plan"]["sub_sidecar"] = str(track.get("sidecar") or "")
        else:
            ff = track.get("ff_index")
            if ff is None:
                # 评审 B7/R13-D6：探测缓存缺流号时不再猜（猜错会烧错轨）
                raise HTTPException(422, "subtitle stream index missing; re-probe required")
            d["plan"]["sub_ff_index"] = int(ff)
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
    # 真实媒体起点：客户端字幕（VTT/ASS/PGS）按此平移对齐播放进度
    d["media_start"] = _media_start_for(int(m["id"]), abs_p, start, d["plan"])
    seg = d["plan"].get("seg") or "fmp4"
    stime = _playback.seg_time(seg)
    # 整片已转完（预转码/之前播完）：当静态 VOD 直接播，不起进程——hls.js 最稳形态
    sdir0 = _session_dir(int(m["id"]), skey, start, k)
    if _session_complete(sdir0, plan_key):
        sid0 = uuid.uuid4().hex[:16]
        with _sess_lock:
            _sessions[sid0] = {"proc": None, "sdir": sdir0, "vid": int(m["id"]),
                               "kind": k, "plan": d["plan"], "plan_key": plan_key,
                               "caps_hash": _caps.caps_hash(caps_n), "backend": "static",
                               "complete": True, "last_ping": time.time()}
        return sid0, sdir0, d
    with _sess_lock:
        for sid, s in list(_sessions.items()):
            if int(s.get("vid") or -1) != int(m["id"]):
                continue
            if str(s.get("kind") or "movie") != k:
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
        sdir = _session_dir(int(m["id"]), skey, start, k)
        for n in os.listdir(sdir):
            try:
                os.remove(os.path.join(sdir, n))
            except OSError:
                pass
        # 等前 _MIN_SEGS 个视频分片（remux 秒出；转码按实际速度）：首画面不等整片。
        # 硬件后端（VAAPI/QSV/NVENC）若不出片，自动用软件编码重试一次（驱动/编码器组合
        # 不匹配时的兜底；日志留两次尝试的 ffmpeg 尾）。
        log_path = os.path.join(sdir, "ffmpeg.log")
        vprefix = _video_seg_prefix(seg)
        force_sw = False
        use_hw = bool(_playback.hw_backend()) and not d["plan"].get("vcopy")
        proc = None
        sid = ""
        for attempt in (0, 1):
            if seg != "ts":
                # fMP4 的 master 自己写（ffmpeg 对 HEVC copy 不产 CODECS）；变体列表由 ffmpeg 产
                _write_master(sdir, info, d["plan"], stime)
            cmd = _playback.build_cmd(abs_p, d["plan"], start=start, seg_time=stime,
                                      force_sw=force_sw)
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
            if log_fh is not subprocess.DEVNULL:
                try:
                    log_fh.close()
                except Exception:
                    pass
            sid = uuid.uuid4().hex[:16]
            _write_session_meta(sdir, sid, int(m["id"]), proc,
                                ("copy" if d["plan"].get("vcopy") else
                                 ("software" if force_sw or not use_hw
                                  else (_playback.hw_backend() or "software"))), attempt + 1)
            with _sess_lock:
                _sessions[sid] = {"proc": proc, "sdir": sdir, "vid": int(m["id"]),
                                  "plan": d["plan"], "plan_key": plan_key,
                                  "caps_hash": _caps.caps_hash(caps_n),
                                  "backend": ("copy" if d["plan"].get("vcopy")
                                              else ("software" if force_sw or not use_hw
                                                    else (_playback.hw_backend() or "software"))),
                                  "attempt": attempt + 1,
                                  "last_ping": time.time()}
            deadline = time.time() + 300
            while time.time() < deadline:
                if _seg_count(sdir, vprefix) >= _MIN_SEGS:
                    break
                if proc.poll() is not None:
                    break
                time.sleep(1)
            if _seg_count(sdir, vprefix) > 0 and _variant_playlists(sdir):
                break  # 成功
            tail = ""
            try:
                with open(log_path, "rb") as fh:
                    fh.seek(max(0, os.path.getsize(log_path) - 2000))
                    tail = fh.read().decode("utf-8", errors="replace").strip()[-500:]
            except OSError:
                pass
            _drop_session(sid, kill=True)
            if attempt == 0 and use_hw:
                force_sw = True
                for n2 in os.listdir(sdir):   # 清残片，重来
                    try:
                        os.remove(os.path.join(sdir, n2))
                    except OSError:
                        pass
                continue
            logger.warning("transcode failed vid=%s attempt=%s backend=%s tail=%s",
                           m["id"], attempt + 1,
                           "sw" if force_sw else ("hw" if use_hw else "copy"), tail[-200:])
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
                                   caps=body.caps, force_burn=body.force_burn,
                                   kind=body.kind)
    caps_n = _caps.default_caps() if body.caps is None else _caps.normalize_caps(body.caps)
    return {"session_id": sid,
            "playlist_url": f"/api/stream/sessions/{sid}/master.m3u8",
            "method": d["method"], "reasons": d["reasons"], "plan": d["plan"],
            "media_start": float(d.get("media_start") or 0),
            "caps_hash": _caps.caps_hash(caps_n),
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
    """卡死自证口：进程活/死/退出码、分片/列表/ENDLIST（含各 rendition）、
    实际转码后端/重试次数/caps 摘要、ffmpeg 尾日志、近期命中。"""
    with _sess_lock:
        sess = _sessions.get(sid or "")
        hits = [h for h in list(_hits) if h.get("sid") == sid][-20:]
    if not sess:
        return {"session_id": sid, "exists": False, "hits": hits}
    proc = sess.get("proc")
    alive = proc is not None and proc.poll() is None
    exit_code = None if proc is None or alive else proc.poll()
    sdir = sess.get("sdir") or ""
    plan = sess.get("plan") or {}
    segs = _seg_count(sdir)
    vsegs = _seg_count(sdir, _video_seg_prefix(plan.get("seg") or "fmp4"))
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
    variants = []
    for name in _variant_playlists(sdir):
        variants.append({"name": name,
                         "endlist": _has_endlist(os.path.join(sdir, name))})
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
            "exit_code": exit_code, "segments": segs, "video_segments": vsegs,
            "playlist_items": extinf, "finished": endlist,
            "backend": sess.get("backend") or "", "attempt": sess.get("attempt") or 1,
            "caps_hash": sess.get("caps_hash") or "", "plan": plan,
            "variants": variants,
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


@router.delete("/sessions/{sid}")
def hls_session_close(sid: str):
    """关播：杀转码进程删会话（分片留 24h TTL，供同参数重进复用）。
    预转码会话为共享产物：播放器关闭只解除绑定，不杀后台任务（评审 P1-07）。"""
    with _sess_lock:
        sess = _sessions.get(sid)
        shared = bool(sess and sess.get("prewarm") and not sess.get("complete"))
    if shared:
        return {"session_id": sid, "closed": False, "detached": True}
    with _sess_lock:
        existed = sid in _sessions
    _drop_session(sid, kill=True)
    return {"session_id": sid, "closed": existed}


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

