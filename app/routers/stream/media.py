"""routers.stream.media（自 app/routers/stream.py 拆分，评审 B9/R12-Q1；经 stream 门面使用）。"""
from fastapi import HTTPException
from pydantic import BaseModel
from ... import caps as _caps
from ... import media as _media
from ... import storage
from ... import store
from ... import library_paths
from ... import playback as _playback
from ...log import get_logger
logger = get_logger("stream.media")
from .common import _media_cached_or_probe, _version_source, router
from .subtitles import _sub_list
__all__ = ['stream_media', '_media_payload', 'stream_backends', 'PlaybackQuery', '_decide_payload', 'stream_decide', 'stream_decide_post', 'VersionsQuery', '_versions_payload', 'stream_versions', 'stream_versions_post', 'ProbeMissingBody', 'probe_missing', 'ProgressBody', 'progress_get', 'progress_save', 'progress_clear']


def _source_or_none(vm: dict):
    """库内相对路径 → MediaSource；缺失/离线等存储错误返回 None（版本列表置灰用）。"""
    try:
        return storage.media_source(
            vm.get("library_id") or library_paths.DEFAULT_LIBRARY_ID, vm["file_path"])
    except storage.StorageError:
        return None


@router.get("/{version_id}/media")
def stream_media(version_id: int, refresh: int = 0, kind: str = "movie"):
    """版本媒体信息（徽章行用）：容器/时长/分辨率/编码/HDR/位深/音字幕列表/playable，
    并附客户端实测候选码串（vcaps + 每音轨 caps）。kind=movie|episode（F）。"""
    m, src = _version_source(version_id, kind)
    k = m.get("kind") or "movie"
    if refresh:
        info = store.upsert_media_info(int(m["id"]),
                                       _media.probe(src.input, size=src.size), k)
    else:
        info = _media_cached_or_probe(m, src)
    return {"version_id": int(m["id"]), "kind": k, "file_path": m["file_path"],
            "title": m.get("title", ""), **_media_payload(m, info),
            "duration_text": _media.fmt_duration(info.get("duration") or 0)}


def _media_payload(m: dict, info: dict) -> dict:
    """播放/媒体信息响应体：ffprobe 字段 + 候选码串 + 内嵌/外挂合并字幕轨。"""
    from ...playback import MAX_AUDIO_RENDITIONS
    payload = _media.decorate(info)
    # 音轨与 HLS 产物一致（R11-D3）：产物只含前 8 条，列表也截断，避免选到不存在的轨
    payload["audio"] = list(payload.get("audio") or [])[:MAX_AUDIO_RENDITIONS]
    payload["subs"] = _sub_list(m, info)
    return payload


@router.get("/backends")
def stream_backends(refresh: int = 0):
    """转码后端探测（目标文档 §11）：{name: software|vaapi|qsv|nvenc, hw, device, reason}。
    冒烟编码结果进程内缓存；?refresh=1 强制重探（NAS 上验证 /dev/dri 权限时用）。"""
    try:
        from ... import transcode as _tr
    except Exception as e:
        return {"name": "software", "hw": False, "device": "", "reason": str(e)[:120], "env": ""}
    return _tr.backend_info(refresh=bool(refresh))


class PlaybackQuery(BaseModel):
    """播放请求（用户选择 + 客户端能力）。caps 缺省走服务端保守默认（旧客户端/调试）。
    force_burn：客户端图片字幕解码失败时的显式降级（仅图片字幕生效）。"""
    quality: str = "auto"
    audio: int = 0
    sub: int | None = None
    client: str = "web"
    caps: dict | None = None
    force_burn: bool = False
    kind: str = "movie"   # F：movie|episode


def _decide_payload(m: dict, info: dict, q: PlaybackQuery) -> dict:
    caps = _caps.default_caps() if q.caps is None else q.caps
    # 只算一次：subs = 内嵌+外挂合并清单（决策与响应同源；此前重复调用会再枚举
    # 一遍外挂字幕目录，直读库 = 多 4 次 SMB list）
    merged = _media_payload(m, info)
    d = _playback.plan(merged, caps=caps, quality=q.quality, audio_idx=q.audio,
                       sub_idx=q.sub, client=q.client, force_burn=q.force_burn)
    method = d["method"]
    k = m.get("kind") or "movie"
    if k == "episode":
        blob_url = f"/api/tv/episodes/{int(m['id'])}/blob"
    else:
        blob_url = f"/api/movies/{int(m['id'])}/blob?name={m['file_path']}"
    sub_q = f"&sub={q.sub}" if q.sub is not None else ""
    kind_q = "" if k == "movie" else "&kind=episode"
    hls_url = (f"/api/stream/{int(m['id'])}/master.m3u8"
               f"?quality={q.quality}&audio={q.audio}{sub_q}{kind_q}")
    return {"version_id": int(m["id"]), "kind": k, "method": method, "reasons": d["reasons"],
            "plan": d["plan"], "subtitle_mode": d.get("subtitle_mode") or "none",
            "caps_hash": _caps.caps_hash(caps) if q.caps is not None else "",
            "media": merged,
            # 直链始终返回：HDR/DV/图片字幕等复杂片源可复制给 VLC/Kodi（目标文档 §12）
            "direct_url": blob_url,
            "hls_url": hls_url if method in ("remux", "audio_transcode",
                                             "video_transcode") else ""}


@router.get("/{version_id}/decide")
def stream_decide(version_id: int, quality: str = "auto",
                  audio: int = 0, sub: int | None = None,
                  client: str = "web", kind: str = "movie"):
    """三档决策（GET 兼容口：无 caps，走服务端保守默认）。新播放器用 POST 带 caps。"""
    m, src = _version_source(version_id, kind)
    info = _media_cached_or_probe(m, src)
    if not info.get("playable"):
        raise HTTPException(422, f"unplayable: {info.get('probe_error') or 'probe failed'}")
    return _decide_payload(m, info, PlaybackQuery(quality=quality, audio=audio,
                                                  sub=sub, client=client, caps=None))


@router.post("/{version_id}/decide")
def stream_decide_post(version_id: int, body: PlaybackQuery | None = None):
    """四档决策（目标文档 §5）：direct / remux / audio_transcode / video_transcode。
    caps 由前端 caps.js 实测上报；direct_url 复用 blob（Range 直发），hls_url 供切片口。"""
    m, src = _version_source(version_id, body.kind if body else "movie")
    info = _media_cached_or_probe(m, src)
    if not info.get("playable"):
        raise HTTPException(422, f"unplayable: {info.get('probe_error') or 'probe failed'}")
    return _decide_payload(m, info, body or PlaybackQuery())


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
    versions = list(m.get("versions") or [{"id": m["id"], "file_path": m["file_path"],
                                            "edition": m.get("edition") or "",
                                            "spec": m.get("spec") or ""}])

    # 每版本只解析一次：并行补探测并把 (vm, src, info) 缓存，报告循环复用——
    # 此前报告循环再次 stat 源文件、再次走探测，直读库 = 成倍往返。
    prepared: dict[int, tuple] = {}

    def _prepare_one(v: dict) -> None:
        """无缓存版本并行补探测（评审 B8/R12-D7：多版本片不再串行等 ffprobe）。"""
        try:
            vid = int(v["id"])
        except (TypeError, ValueError):
            return
        if vid in prepared:
            return
        try:
            vm = store.get_movie(vid)
        except Exception as e:
            logger.debug("get version failed vid=%s: %s", vid, e)
            return
        if not vm:
            return
        src = _source_or_none(vm)
        info = None
        if src is not None:
            try:
                info = _media_cached_or_probe(vm, src)
            except Exception as e:
                logger.debug("pre-probe failed vid=%s: %s", vid, e)
        prepared[vid] = (vm, src, info)

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(_prepare_one, versions))
    except Exception as e:
        logger.debug("parallel probe skipped: %s", e)
        for v in versions:
            _prepare_one(v)

    items = []
    for v in versions:
        try:
            vid = int(v["id"])
        except (TypeError, ValueError):
            continue
        if vid not in prepared:
            _prepare_one(v)
        hit = prepared.get(vid)
        if hit is None:
            continue
        vm, src, info = hit
        base = {"version_id": vid, "file_path": vm["file_path"],
                "edition": v.get("edition") or "", "spec": v.get("spec") or ""}
        if src is None:
            items.append({**base, "playable": False, "probe_error": "file missing",
                          "method": "blocked", "reasons": ["unplayable"],
                          "score": [9, 0], "duration_text": ""})
            continue
        if info is None or not info.get("playable"):
            items.append({**base, "playable": False,
                          "probe_error": (info or {}).get("probe_error") or "probe failed",
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
                      "sub_count": len(_sub_list(vm, info))})
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
        # 无行、探测结构过期、或环境错误（换环境后可重试）都要补探。
        # 先一次轻量视图预筛（评审 B8/R12-D6），避免逐行 get_media_info 的 N+1
        mi_by_id = {int(mi["item_id"]): mi for mi in store.list_media_info_brief()
                    if str(mi.get("kind") or "movie") == "movie"}
        pv_now = int(getattr(_media, "PROBE_VERSION", 0))
        kept = []
        for r in rows:
            mi = mi_by_id.get(int(r["id"]))
            if not mi:
                kept.append(r)
            elif int(mi.get("probe_ver") or 0) < pv_now:
                kept.append(r)
            elif not mi.get("playable") and _media.is_retryable_error(
                    str(mi.get("probe_error") or "")):
                kept.append(r)
        rows = kept
    rows = rows[:limit]
    done, failed, unplayable = [], [], []
    for r in rows:
        vid = int(r["id"])
        src = _source_or_none(r)
        if src is None:
            failed.append({"id": vid, "error": "file missing"})
            continue
        try:
            info = store.upsert_media_info(
                vid, _media.probe(src.input, size=src.size))
        except Exception as e:
            failed.append({"id": vid, "error": str(e)[:200]})
            continue
        item = {"id": vid, "playable": bool(info.get("playable")),
                "duration": info.get("duration") or 0}
        (done if info.get("playable") else unplayable).append(item)
    return {"total": len(rows), "ok": len(done), "unplayable": len(unplayable),
            "failed": failed, "results": done, "unplayable_items": unplayable}


class ProgressBody(BaseModel):
    position: float = 0
    duration: float = 0


@router.get("/progress")
def progress_get(version_id: int, kind: str = "movie"):
    """读单版本断点。无行返回 {position:0,...}（前端视为从头）。"""
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    if not store.get_playable(kind, vid):
        raise HTTPException(404, "version not found")
    p = store.get_progress(vid, kind)
    if not p:
        return {"version_id": vid, "position": 0, "duration": 0, "updated_at": 0,
                "position_text": "0:00"}
    return {**p, "position_text": _media.fmt_duration(p.get("position") or 0)}


@router.post("/progress")
def progress_save(body: ProgressBody, version_id: int, kind: str = "movie"):
    """写单版本断点（position/duration 秒）。伪造/缺失版本 404/410，不落脏行。"""
    _version_source(version_id, kind)
    p = store.save_progress(int(version_id), body.position, body.duration, kind)
    return {**p, "position_text": _media.fmt_duration(p.get("position") or 0)}


@router.delete("/progress")
def progress_clear(version_id: int, kind: str = "movie"):
    """清单版本断点（用户选“从头开始”）。"""
    try:
        vid = int(version_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "bad version_id")
    return {"version_id": vid, "cleared": store.clear_progress(vid, kind)}

