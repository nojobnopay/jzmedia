"""Additive Web inventory APIs; existing television playback APIs are unchanged."""
import os
import re
import threading
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import posters, store, tmdb, tv_airing, tv_collection
from ..config import settings
from ..db import POSTER_DIR

router = APIRouter(prefix="/api/tv")
_IMAGE_LOCKS = [threading.Lock() for _ in range(16)]
_IMAGE_FAILURES = {}
_IMAGE_PATH = re.compile(r"^/[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp)$", re.I)


def _collection(show_id):
    try:
        return tv_collection.Collection(show_id)
    except LookupError:
        raise HTTPException(404, "剧集不存在")


@router.get("/airing/status")
def airing_status():
    return tv_airing.status()


class CheckBody(BaseModel):
    show_id: int | None = Field(default=None, ge=1)


@router.post("/airing/check")
def check_airing(body: CheckBody | None = None):
    tids = None
    if body and body.show_id:
        ctx = _collection(body.show_id)
        if not ctx.confirmed:
            raise HTTPException(422, "请先确认剧集的 TMDB 匹配")
        tids = [ctx.tid]
    return tv_airing.request_check(tids)


@router.get("/updates")
def tv_updates(media_library: int = Query(..., ge=1)):
    return tv_collection.updates(media_library)


@router.get("/shows/{show_id}/collection")
def show_collection(show_id: int):
    return _collection(show_id).summary()


@router.get("/shows/{show_id}/seasons/{season}/catalog")
def season_catalog(show_id: int, season: int):
    ctx = _collection(show_id)
    if not ctx.confirmed:
        raise HTTPException(422, "请先确认剧集的 TMDB 匹配")
    if season < 0 or season not in ctx.official:
        raise HTTPException(404, "该季暂无对应的官方分集目录")
    cached = tv_airing.get_catalog(ctx.tid, season)
    current = store.get_show_meta(show_id)
    if not current or current.get("tmdb_id") != ctx.tid or current.get("needs_review"):
        raise HTTPException(409, "剧集匹配已变化，请重新打开季详情")
    return tv_collection.catalog_result(show_id, season, cached)


@router.get("/shows/{show_id}/seasons/{season}/poster")
def season_poster(show_id: int, season: int, tmdb_id: int = Query(..., ge=1)):
    ctx = _collection(show_id)
    if not ctx.confirmed or ctx.tid != tmdb_id:
        raise HTTPException(404, "季海报不可用")
    season_meta = ctx.official.get(season) or {}
    image_path = str(season_meta.get("poster_path") or "")
    if not _IMAGE_PATH.fullmatch(image_path):
        raise HTTPException(404, "暂无季海报")
    rel = posters.tv_season_poster_rel(ctx.tid, season)
    dest = os.path.join(settings.data_dir, rel)
    key = (ctx.tid, season, image_path)
    with _IMAGE_LOCKS[hash(key) % len(_IMAGE_LOCKS)]:
        existing = posters.resolve(POSTER_DIR, rel, settings.data_dir)
        if existing:
            return FileResponse(existing, media_type="image/jpeg")
        if _IMAGE_FAILURES.get(key, 0) > time.monotonic():
            raise HTTPException(503, "季海报暂不可用，请稍后重试")
        if not tmdb.download_image(image_path, dest, size="w300"):
            if len(_IMAGE_FAILURES) >= 512:
                _IMAGE_FAILURES.clear()
            _IMAGE_FAILURES[key] = time.monotonic() + 600
            raise HTTPException(503, "季海报暂不可用，请稍后重试")
        _IMAGE_FAILURES.pop(key, None)
    return FileResponse(dest, media_type="image/jpeg")
