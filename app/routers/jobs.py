from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import scanner, store, tmdb

router = APIRouter(prefix="/api/jobs")


class BackfillBody(BaseModel):
    limit: int = 500
    force: bool = False


@router.post("/douban-fetch")
def douban_fetch():
    # 按确认结论：默认不爬豆瓣，V1手动填，口子先留好
    raise HTTPException(status_code=501, detail="douban fetch disabled by default (phase0 placeholder)")


@router.post("/backfill-meta")
def backfill_meta(body: BackfillBody | None = None):
    """存量回填：region 为空的行补元数据；所有有 tmdb_id 的行同步人物+头像。

    不重下海报、不写 NFO；已存在的头像文件不重下。scan 的 skipped_cached
    补不上这些，所以单设此口。
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
    for m in cands:
        try:
            detail = tmdb.movie_detail(int(m["tmdb_id"]))
            if force or not (m.get("region") or ""):
                meta = scanner.meta_from_detail(detail)
                # 不覆盖手动改过的标题（PATCH allowlist 含 title；overview/tags/评分不在 meta 内，天然保留）
                if (m.get("title") or "").strip():
                    meta.pop("title", None)
                store.update_movie_meta(m["id"], **meta)
            avatars += scanner.sync_persons(m["id"], detail)
            done.append({"id": m["id"], "tmdb_id": m["tmdb_id"],
                         "region": store.get_movie(m["id"]).get("region", "")})
        except Exception as e:
            failed.append({"id": m["id"], "tmdb_id": m.get("tmdb_id"), "error": str(e)})
    return {"total": len(cands), "ok": len(done), "failed": failed,
            "avatars_downloaded": avatars,
            "results": done, "facets": store.get_facets()}
