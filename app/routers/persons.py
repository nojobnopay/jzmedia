import threading
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, store, tmdb
from ..log import get_logger

from .movies.common import _library_scope

logger = get_logger("persons")

router = APIRouter(prefix="/api/persons")

# tmdb_id 值域守卫（评审 B6/R07-B2）：超大 int 会在 SQLite 绑定时抛 OverflowError→500
_ID_MAX = 2 ** 31 - 1


def _check_id(tmdb_id: int) -> int:
    if not (0 < int(tmdb_id) <= _ID_MAX):
        raise HTTPException(404, "person not found")
    return int(tmdb_id)


_bio_lock = threading.Lock()
_bio_recent: dict[int, float] = {}


def _rate_ok(tmdb_id: int) -> bool:
    """10s 内重复刷新直接跳过（评审 B8/R07-D3：防连点/多标签页打爆 TMDB）。"""
    now = time.time()
    with _bio_lock:
        if now - _bio_recent.get(tmdb_id, 0) < 10:
            return False
        if len(_bio_recent) > 256:
            for k in sorted(_bio_recent, key=_bio_recent.get)[:128]:
                _bio_recent.pop(k, None)
        _bio_recent[tmdb_id] = now
    return True


def _fetch_and_cache_bio(tmdb_id: int) -> None:
    """抓人物详情并写入镜像（含空结果的负缓存，只供首次访问/手动刷新调用）。
    主语言跟随设置页 TMDB 语言（评审 B8/R07-D1），空简介回退 en-US。"""
    primary = config.effective_tmdb_language() or "zh-CN"
    detail = tmdb.person_detail(int(tmdb_id), language=primary)
    lang = primary
    if not (detail.get("biography") or "").strip() and primary.lower() != "en-us":
        detail = tmdb.person_detail(int(tmdb_id), language="en-US")
        lang = "en-US"
    store.update_person_bio(
        tmdb_id, detail.get("biography", "") or "",
        detail.get("birthday", "") or "",
        detail.get("place_of_birth", "") or "", lang=lang)


@router.get("/{tmdb_id}")
def get_person(tmdb_id: int, library: str | None = None,
               media_library: int | None = None):
    """人物详情＋库内作品（纯本地，瞬时返回，不阻塞等 TMDB）。

    作品列表支持库范围（v18 读聚合）：`media_library=<id>` 聚合其全部视频库、
    `library=<视频库id[,id]>` 指定；都缺省=全库。简介首次访问由前端在后台调
    POST /refresh 补齐（渐进加载）；空简介有负缓存，不再每次访问重试。"""
    scope = _library_scope(library, media_library)
    p = store.get_person(_check_id(tmdb_id), library_ids=scope)
    if not p:
        raise HTTPException(404, "person not found")
    return p


@router.post("/{tmdb_id}/refresh")
def refresh_person(tmdb_id: int, library: str | None = None,
                   media_library: int | None = None):
    """手动刷新人物简介/生日/出生地（唯一的远端写入口）；返回仍按当前库范围。"""
    tmdb_id = _check_id(tmdb_id)
    scope = _library_scope(library, media_library)
    if not store.person_exists(tmdb_id):
        raise HTTPException(404, "person not found")
    if _rate_ok(tmdb_id):
        try:
            _fetch_and_cache_bio(int(tmdb_id))
        except Exception as e:
            raise HTTPException(502, f"tmdb fetch failed: {e}")
    return store.get_person(tmdb_id, library_ids=scope)


class EnsureBody(BaseModel):
    tmdb_id: int
    name: str = ""
    profile_path: str = ""


@router.post("/ensure")
def ensure_person(body: EnsureBody, library: str | None = None,
                  media_library: int | None = None):
    """剧集点击建档：用 TV 侧已有的 {tmdb_id, name, profile_path} 建人物镜像行
    （头像复用电影 `save_person_avatar` 管线，简介走人物页渐进 refresh 补齐）。

    已有行只补名字/profile，不重下头像；无 profile_path 记 '-'（确认无照片，
    防回填重试）；下载失败记 ''（下次点击重试）。返回仍按当前库范围（含 tv_works）。"""
    tid = _check_id(int(body.tmdb_id or 0))
    scope = _library_scope(library, media_library)
    name = " ".join(str(body.name or "").split()) or f"TMDB {tid}"
    profile = str(body.profile_path or "").strip()
    raw = store.get_person_raw(tid) or {}
    if (raw.get("avatar") or "") not in ("", None):
        store.upsert_person(tid, name, avatar=None,
                            profile_tmdb_path=profile or None,
                            fetched_at=raw.get("fetched_at") or None)
        return store.get_person(tid, library_ids=scope)
    if profile:
        from ..scanner import persist as _persist
        try:
            avatar = _persist.save_person_avatar(tid, profile)
        except Exception as e:
            logger.warning("ensure avatar failed person=%s: %s", tid, e)
            avatar = ""
        avatar = avatar or ""
    else:
        avatar = "-"
    store.upsert_person(tid, name, avatar=avatar,
                        profile_tmdb_path=profile or None, fetched_at=None)
    p = store.get_person(tid, library_ids=scope)
    if not p:
        raise HTTPException(500, "ensure failed")
    return p
