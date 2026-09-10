from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import scanner, store

router = APIRouter(prefix="/api/jobs")


class BackfillBody(BaseModel):
    limit: int = 500
    force: bool = False


class RefreshBody(BaseModel):
    ids: list[int] | None = None
    tmdb_ids: list[int] | None = None
    limit: int = 500


@router.post("/douban-fetch")
def douban_fetch():
    # 按确认结论：默认不爬豆瓣，V1手动填，口子先留好
    raise HTTPException(status_code=501, detail="douban fetch disabled by default (phase0 placeholder)")


@router.post("/backfill-meta")
def backfill_meta(body: BackfillBody | None = None):
    """离线修复：从 tmdb_cache 向 movies 补元数据 + 从缓存/兄弟行补人物关联。

    不调任何 TMDB 网络（默认永不自动刷新）；不重下海报、不写 NFO；
    手工标题永不覆盖；已存在的头像文件不重下。
    远端更新请走 POST /api/movies/{id}/refresh 或 POST /api/jobs/tmdb-refresh。
    """
    limit = (body.limit if body else 500) or 500
    force = bool(body.force) if body else False
    all_tmdb = [m for m in store.list_movies(grouped=False, limit=100000)
                if m.get("tmdb_id")]
    if force:
        cands = all_tmdb
    else:
        cands = [m for m in all_tmdb
                 if not (m.get("region") or "") or store.persons_missing_avatar(m["id"])]
    cands = cands[:max(1, min(limit, 5000))]
    done, failed = [], []
    avatars = 0
    skipped_no_cache = 0
    for m in cands:
        try:
            cached = store.get_tmdb_cached(int(m["tmdb_id"]))
            if not cached:
                skipped_no_cache += 1
                failed.append({"id": m["id"], "tmdb_id": m.get("tmdb_id"),
                               "error": "no tmdb_cache, use refresh first"})
                continue
            if force or not (m.get("region") or ""):
                # old_title=None：空标题才写入，非空（手工或已同步）一律保留
                store.copy_tmdb_to_movie(m["id"], old_title=None)
            avatars += scanner.sync_persons_from_cache(m["id"], int(m["tmdb_id"]))
            store.resync_fts(m["id"])
            done.append({"id": m["id"], "tmdb_id": m["tmdb_id"],
                         "region": (store.get_movie(m["id"]) or {}).get("region", "")})
        except Exception as e:
            failed.append({"id": m["id"], "tmdb_id": m.get("tmdb_id"), "error": str(e)})
    return {"total": len(cands), "ok": len(done), "failed": failed,
            "avatars_downloaded": avatars,
            "skipped_no_cache": skipped_no_cache,
            "results": done, "facets": store.get_facets()}


@router.post("/tmdb-refresh")
def tmdb_refresh(body: RefreshBody | None = None):
    """手动批量刷新：显式给出的 ids/tmdb_ids 才抓远端，无变化的行不碰。
    默认永不自动触发，供前端多选/设置页调用。"""
    body = body or RefreshBody()
    limit = max(1, min(body.limit or 500, 5000))
    tmdb_ids: list[int] = []
    seen: set[int] = set()
    for mid in body.ids or []:
        try:
            m = store.get_movie(int(mid))
        except Exception:
            m = None
        if m and m.get("tmdb_id") and int(m["tmdb_id"]) not in seen:
            seen.add(int(m["tmdb_id"]))
            tmdb_ids.append(int(m["tmdb_id"]))
    for tid in body.tmdb_ids or []:
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        if tid not in seen:
            seen.add(tid)
            tmdb_ids.append(tid)
    if not tmdb_ids:
        raise HTTPException(422, "ids or tmdb_ids required")
    tmdb_ids = tmdb_ids[:limit]
    done, failed = [], []
    for tid in tmdb_ids:
        try:
            out = scanner.refresh_tmdb_id(tid)
            done.append({"tmdb_id": tid, **out})
        except Exception as e:
            failed.append({"tmdb_id": tid, "error": str(e)})
    return {"total": len(tmdb_ids),
            "ok": len(done), "failed": failed,
            "results": done, "facets": store.get_facets()}
