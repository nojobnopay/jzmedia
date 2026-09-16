from fastapi import APIRouter, HTTPException

from .. import store, tmdb

router = APIRouter(prefix="/api/persons")

# tmdb_id 值域守卫（评审 B6/R07-B2）：超大 int 会在 SQLite 绑定时抛 OverflowError→500
_ID_MAX = 2 ** 31 - 1


def _check_id(tmdb_id: int) -> int:
    if not (0 < int(tmdb_id) <= _ID_MAX):
        raise HTTPException(404, "person not found")
    return int(tmdb_id)


def _fetch_and_cache_bio(tmdb_id: int) -> None:
    """抓人物详情并写入镜像（含空结果的负缓存，只供首次访问/手动刷新调用）。"""
    detail = tmdb.person_detail(int(tmdb_id))
    lang = "zh-CN"
    if not (detail.get("biography") or "").strip():
        detail = tmdb.person_detail(int(tmdb_id), language="en-US")
        lang = "en-US"
    store.update_person_bio(
        tmdb_id, detail.get("biography", "") or "",
        detail.get("birthday", "") or "",
        detail.get("place_of_birth", "") or "", lang=lang)


@router.get("/{tmdb_id}")
def get_person(tmdb_id: int):
    """人物详情＋库内作品（纯本地，瞬时返回，不阻塞等 TMDB）。
    简介首次访问由前端在后台调 POST /refresh 补齐（渐进加载）；空简介有负缓存，
    不再每次访问重试（与电影镜像策略一致）。"""
    p = store.get_person(_check_id(tmdb_id))
    if not p:
        raise HTTPException(404, "person not found")
    return p


@router.post("/{tmdb_id}/refresh")
def refresh_person(tmdb_id: int):
    """手动刷新人物简介/生日/出生地（唯一的远端写入口）。"""
    tmdb_id = _check_id(tmdb_id)
    if not store.get_person_raw(tmdb_id):
        raise HTTPException(404, "person not found")
    try:
        _fetch_and_cache_bio(int(tmdb_id))
    except Exception as e:
        raise HTTPException(502, f"tmdb fetch failed: {e}")
    return store.get_person(int(tmdb_id))
