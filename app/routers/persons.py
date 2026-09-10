from fastapi import APIRouter, HTTPException

from .. import store, tmdb

router = APIRouter(prefix="/api/persons")


@router.get("/{tmdb_id}")
def get_person(tmdb_id: int):
    """人物详情＋库内作品。简介为空时现调 TMDB 回填并永久缓存（懒加载，不做全量回填）。"""
    p = store.get_person(tmdb_id)
    if not p:
        raise HTTPException(404, "person not found")
    if not (p.get("biography") or "").strip():
        try:
            detail = tmdb.person_detail(int(tmdb_id))
            if not (detail.get("biography") or "").strip():
                detail = tmdb.person_detail(int(tmdb_id), language="en-US")
            store.update_person_bio(
                tmdb_id, detail.get("biography", ""),
                detail.get("birthday", "") or "",
                detail.get("place_of_birth", "") or "")
            p = store.get_person(tmdb_id)
        except Exception:
            pass  # TMDB 不可达时返回本地数据，简介保持为空
    return p
