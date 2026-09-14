"""在线播放（Plex 式三档）：decide 决策 + 媒体信息 + 断点进度。

- 播放单位是版本行 id（每个文件版本即一行 movies，多版本选播即选 id）。
- 媒体信息懒探测 + media_info 缓存（ffprobe 本地派生，不污染 TMDB 镜像，不进 FTS）。
- 伪造文件（0 字节/probe 失败）→ playable=false，decide 422，前端置灰禁用。
- HLS 切片（remux/transcode）在 P1/P2 落到 master.m3u8/seg-*.ts/sub；
  本文件 P0 先给 decide/media/progress，后续同文件追加切片口。
"""
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import media as _media
from .. import store
from ..config import settings

router = APIRouter(prefix="/api/stream")


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
