"""scanner.persist（自 app/scanner.py 拆分，评审 B9/R03-Q1；对外经 app.scanner 门面使用）。"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from ..config import settings
from .. import library_paths
from .. import store
from .. import tmdb
from ..db import POSTER_DIR
from ..log import get_logger
logger = get_logger("scanner.persist")
from .match import extract_credits, jobs_from_credits, meta_from_detail
from .nfo_link import _write_nfo_for
__all__ = ['save_person_avatar', '_sync_jobs', 'sync_persons', 'sync_persons_from_cache', 'ensure_movie_poster', '_media_needs', 'finish_tmdb_media', 'apply_tmdb_detail', 'apply_tmdb_detail_fast', 'apply_cached_to_movie', 'finish_refresh_media', 'refresh_tmdb_id', 'refresh_tmdb_id_fast']

def save_person_avatar(person_tmdb_id: int, profile_path: str | None) -> str:
    """人物头像落盘（w185），文件已存在则跳过。返回相对 DATA_DIR 的路径，失败返回 ''。"""
    if not profile_path:
        return ""
    dest = os.path.join(POSTER_DIR, f"person_{person_tmdb_id}.jpg")
    if os.path.exists(dest):
        return os.path.relpath(dest, settings.data_dir)
    if tmdb.download_poster(profile_path, dest, size="w185"):
        return os.path.relpath(dest, settings.data_dir)
    return ""


def _sync_jobs(mid: int, jobs: list[tuple], max_workers: int = 8) -> int:
    """jobs 落库（幂等）：头像按 profile 变化判断重下，关联一致则跳过 link。返回新下载头像数。"""
    now = int(time.time())
    existing = {(l["person_tmdb_id"], l["role"], l["character_name"] or "", l["cast_order"])
                for l in store.get_movie_person_links(mid)}
    uniq: dict = {}
    for job in jobs:
        uniq.setdefault(job[0], job)

    def _fetch(item: tuple) -> tuple:
        pid_tmdb, _, profile_path, _, _, _ = item
        if not profile_path:
            return pid_tmdb, "-", False, ""
        raw = store.get_person_raw(pid_tmdb) or {}
        stored_profile = raw.get("profile_tmdb_path") or ""
        old_avatar = raw.get("avatar") or ""
        dest = os.path.join(POSTER_DIR, f"person_{pid_tmdb}.jpg")
        if (stored_profile == (profile_path or "") and os.path.exists(dest)
                and old_avatar):
            return pid_tmdb, old_avatar, False, profile_path or ""
        # 远端换图/首下：download_poster 自带 tmp+replace 原子写；失败绝不删旧图
        # （评审 B7/R03-B2：此前先删旧文件再下载，失败会把头像清空）
        existed = os.path.exists(dest)
        avatar = save_person_avatar(pid_tmdb, profile_path)
        if avatar:
            return pid_tmdb, avatar, (not existed), profile_path or ""
        if old_avatar and os.path.exists(os.path.join(settings.data_dir, old_avatar)):
            logger.debug("avatar download failed, keep old person=%s old=%s",
                         pid_tmdb, old_avatar)
            return pid_tmdb, old_avatar, False, stored_profile
        logger.debug("avatar download failed person=%s profile=%s", pid_tmdb,
                     profile_path)
        return pid_tmdb, "", False, profile_path or ""

    avatars: dict = {}
    profiles: dict = {}
    downloaded = 0
    if uniq:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for pid_tmdb, avatar, is_new, profile in ex.map(_fetch, uniq.values()):
                avatars[pid_tmdb] = avatar
                profiles[pid_tmdb] = profile
                downloaded += 1 if is_new else 0
    for pid_tmdb, name, _, role, character, order in jobs:
        key = (pid_tmdb, role, character or "", order)
        if key in existing:
            # 关联已一致，仍需确保人物基础行存在（首建时可能缺行）
            if not store.get_person_raw(pid_tmdb):
                store.upsert_person(pid_tmdb, name, avatars.get(pid_tmdb),
                                    profiles.get(pid_tmdb, ""), now)
            continue
        pid = store.upsert_person(pid_tmdb, name, avatars.get(pid_tmdb),
                                  profiles.get(pid_tmdb, ""), now)
        store.link_person(mid, pid, role, character, order)
    return downloaded


def sync_persons(mid: int, detail: dict, max_workers: int = 8) -> int:
    """按TMDB credits 落库导演+前10演员（含头像），返回新下载头像数。供扫描/手动匹配/刷新共用。

    无照片的记 avatar='-'（确认无，避免反复重试）；远端换头像（profile_path 变化）才重下；
    关联一致跳过 link。落库串行；同一人多角色去重下载但保留全部关联。
    """
    jobs = jobs_from_credits((detail.get("credits") or {}))
    # 兼容 cache 形态（cast/crew 已为最小集）与原始 detail 形态
    if not jobs and isinstance(detail.get("credits"), dict):
        jobs = jobs_from_credits(detail["credits"])
    return _sync_jobs(mid, jobs, max_workers)


def sync_persons_from_cache(mid: int, tmdb_id: int) -> int:
    """免网络复用人物：cache.credits 有则直接落库；种子行 credits 为空时从兄弟版本复制关联。"""
    cached = store.get_tmdb_cached(tmdb_id)
    credits = (cached or {}).get("credits") or {}
    jobs = jobs_from_credits(credits)
    if jobs:
        return _sync_jobs(mid, jobs)
    sib = store.find_sibling_with_persons(tmdb_id, mid)
    if sib:
        n = store.copy_person_links(sib, mid)
        if n:
            store.resync_fts(mid)
        return 0
    return 0


def ensure_movie_poster(tmdb_id: int, poster_tmdb_path: str | None,
                        old_poster_tmdb_path: str | None = None) -> str:
    """海报本地路径保障：远端 path 未变且文件存在则复用，否则下载（覆盖）。
    返回相对 DATA_DIR 的路径，失败时有文件则复用旧文件，否则 ''。"""
    dest = os.path.join(POSTER_DIR, f"{tmdb_id}.jpg")
    remote = poster_tmdb_path or ""
    if not remote:
        return os.path.relpath(dest, settings.data_dir) if os.path.exists(dest) else ""
    if os.path.exists(dest) and (old_poster_tmdb_path or "") == remote:
        return os.path.relpath(dest, settings.data_dir)
    if tmdb.download_poster(remote, dest):
        return os.path.relpath(dest, settings.data_dir)
    return os.path.relpath(dest, settings.data_dir) if os.path.exists(dest) else ""


def _media_needs(tmdb_id: int, mid: int, poster_tmdb: str,
                 old_poster_tmdb: str, detail: dict) -> dict:
    """后台重活是否真有事做（供回包 flags，前端展示“补齐中”）。纯本地判断，不调网。"""
    dest = os.path.join(POSTER_DIR, f"{tmdb_id}.jpg")
    poster = bool(poster_tmdb) and not (
        os.path.exists(dest) and (old_poster_tmdb or "") == poster_tmdb)
    try:
        avatars = store.persons_missing_avatar(mid)
    except Exception:
        avatars = False
    if not avatars:
        try:
            jobs = jobs_from_credits(extract_credits(detail))
            avatars = bool(jobs and store.get_movie_person_links(mid) == [])
        except Exception:
            avatars = False
    return {"poster": bool(poster), "avatars": bool(avatars)}


def finish_tmdb_media(mid: int, detail: dict, abs_path: str,
                      poster_tmdb: str, old_poster_tmdb: str) -> dict:
    """后台重活：海报下载 + 头像同步 + FTS + NFO。幂等，失败自吞（下次刷新/重绑自愈）。"""
    tmdb_id = detail["id"]
    try:
        poster_local = ensure_movie_poster(tmdb_id, poster_tmdb, old_poster_tmdb)
        store.update_movie_meta(mid, poster_path=poster_local)
        sync_persons(mid, detail)
        store.resync_fts(mid)
        nfo = _write_nfo_for(mid, abs_path)
        return {"poster_path": poster_local, "nfo": nfo}
    except Exception as e:
        logger.warning("finish_tmdb_media failed mid=%s tmdb=%s: %s", mid,
                       tmdb_id, e)
        return {"poster_path": "", "nfo": False}


def apply_tmdb_detail(mid: int, detail: dict, abs_path: str,
                      force_title: bool = False) -> dict:
    """把TMDB详情写入镜像并复制到本片（人物/海报/NFO/FTS，全同步版；扫描链路用）。
    force_title=True（手动换绑）时无条件覆盖标题；否则保留手工改过的标题。"""
    out, media = apply_tmdb_detail_fast(mid, detail, abs_path, force_title)
    finish_tmdb_media(mid, detail, abs_path, media["poster_tmdb"],
                      media["old_poster_tmdb"])
    movie = store.get_movie(mid)
    out["nfo"] = _write_nfo_for(mid, abs_path)
    if movie:
        out["title"], out["year"] = movie["title"], movie["year"]
    return out


def apply_tmdb_detail_fast(mid: int, detail: dict, abs_path: str,
                           force_title: bool = False) -> tuple[dict, dict]:
    """快路径（绑定/刷新接口用）：只落镜像 + 复制文字元数据，立即回包；
    海报/头像/NFO 由调用方经 finish_tmdb_media() 放后台。返回 (result, media_args)。"""
    tmdb_id = detail["id"]
    cur = store.get_movie(mid)
    cur_tmdb = (cur or {}).get("tmdb_id")
    cur_title = (cur or {}).get("title") or ""
    if cur_tmdb and cur_tmdb != tmdb_id:
        store.clear_movie_persons(mid)
    if not cur_tmdb or cur_tmdb != tmdb_id:
        store.update_movie_meta(mid, tmdb_id=tmdb_id)
    old_cache = store.get_tmdb_cached(tmdb_id)
    old_title = (old_cache or {}).get("title") or ""
    old_poster_tmdb = (old_cache or {}).get("poster_tmdb_path") or ""
    meta = meta_from_detail(detail)
    poster_tmdb = detail.get("poster_path") or ""
    credits = extract_credits(detail)
    store.upsert_tmdb_cache(tmdb_id, meta, credits, poster_tmdb)
    if force_title:
        store.update_movie_meta(mid, tmdb_id=tmdb_id)
        store.copy_tmdb_to_movie(mid, old_title=cur_title)
        # copy 在 current==old_title 时才会跟随标题；换绑需强制对齐远端标题
        if (store.get_movie(mid) or {}).get("title") != (meta.get("title") or ""):
            store.update_movie_meta(mid, title=meta.get("title") or "", title_auto=0)
        # 其余 TMDB 列经 copy 已同步（copy 内含除 poster 外全量）
    else:
        store.copy_tmdb_to_movie(mid, old_title=old_title if old_cache else None)
    # copy_tmdb_to_movie/update_movie_meta 内部已 resync_fts（评审 B7/R05-B1：不再重复）
    movie = store.get_movie(mid) or {}
    needs = _media_needs(tmdb_id, mid, poster_tmdb, old_poster_tmdb, detail)
    out = {"title": movie.get("title", ""), "year": movie.get("year"),
           "tmdb_id": tmdb_id, "nfo": False, "background": needs}
    media = {"poster_tmdb": poster_tmdb, "old_poster_tmdb": old_poster_tmdb}
    return out, media


def apply_cached_to_movie(mid: int, tmdb_id: int, abs_path: str) -> dict:
    """零网络复用：从 tmdb_cache 向新行复制元数据+海报复用+人物复用，供同 tmdb_id 多版本使用。"""
    cur = store.get_movie(mid)
    cur_tmdb = (cur or {}).get("tmdb_id")
    if cur_tmdb and cur_tmdb != tmdb_id:
        store.clear_movie_persons(mid)
    if not cur_tmdb or cur_tmdb != tmdb_id:
        store.update_movie_meta(mid, tmdb_id=tmdb_id)
    store.copy_tmdb_to_movie(mid, old_title=None)
    cached = store.get_tmdb_cached(tmdb_id) or {}
    poster_local = ensure_movie_poster(tmdb_id, cached.get("poster_tmdb_path") or "",
                                       cached.get("poster_tmdb_path") or "")
    store.update_movie_meta(mid, poster_path=poster_local)
    sync_persons_from_cache(mid, tmdb_id)
    store.resync_fts(mid)
    movie = store.get_movie(mid) or {}
    nfo = _write_nfo_for(mid, abs_path)
    return {"title": movie.get("title", ""), "year": movie.get("year"),
            "tmdb_id": tmdb_id, "nfo": nfo}


def finish_refresh_media(jobs: list[dict]) -> int:
    """后台重活：各版本的海报/头像/NFO。幂等，失败自吞。返回处理数。"""
    n = 0
    for j in jobs:
        try:
            mid = j["mid"]
            if not store.get_movie(mid):
                continue
            poster_local = ensure_movie_poster(j["tmdb_id"], j["poster_tmdb"],
                                               j["old_poster_tmdb"])
            store.update_movie_meta(mid, poster_path=poster_local)
            sync_persons(mid, j["detail"])
            store.resync_fts(mid)
            _write_nfo_for(mid, j["abs_path"])
            n += 1
        except Exception:
            continue
    return n


def refresh_tmdb_id(tmdb_id: int) -> dict:
    """手动刷新唯一入口：抓远端 → 写镜像 → 有变化才扇出到所有同 tmdb_id 版本。
    返回 {changed, affected_ids, title, year}。无变化时不碰任何 movies 行。"""
    out, jobs = refresh_tmdb_id_fast(int(tmdb_id))
    if jobs:
        finish_refresh_media(jobs)
    return out


def refresh_tmdb_id_fast(tmdb_id: int) -> tuple[dict, list[dict]]:
    """快路径（刷新接口用）：抓远端 + 写镜像 + 文字扇出，立即回包；
    海报/头像/NFO 由调用方经 finish_refresh_media() 放后台。返回 (result, jobs)。"""
    tmdb_id = int(tmdb_id)
    detail = tmdb.movie_detail(tmdb_id)
    new_meta = meta_from_detail(detail)
    new_poster_tmdb = detail.get("poster_path") or ""
    new_credits = extract_credits(detail)
    old_cache = store.get_tmdb_cached(tmdb_id)
    old_title = (old_cache or {}).get("title") or ""
    old_poster_tmdb = (old_cache or {}).get("poster_tmdb_path") or ""
    changed = store.upsert_tmdb_cache(tmdb_id, new_meta, new_credits, new_poster_tmdb)
    if not changed:
        jobs = []
        for mid in store.list_movie_ids_by_tmdb(tmdb_id):
            # 缺头像补齐放后台；回包 flags 诚实告知
            try:
                if store.persons_missing_avatar(mid):
                    m = store.get_movie(mid)
                    if m:
                        jobs.append({"mid": mid, "tmdb_id": tmdb_id,
                                     "detail": detail, "poster_tmdb": new_poster_tmdb,
                                     "old_poster_tmdb": old_poster_tmdb,
                                     "abs_path": library_paths.resolve(
                                         m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID,
                                         m["file_path"])})
            except Exception:
                continue
        cur = store.get_tmdb_cached(tmdb_id) or {}
        return ({"changed": False, "affected_ids": [],
                 "title": cur.get("title", ""), "year": cur.get("year"),
                 "tmdb_id": tmdb_id,
                 "background": {"poster": False, "avatars": bool(jobs)}}, jobs)
    affected: list[int] = []
    jobs = []
    for mid in store.list_movie_ids_by_tmdb(tmdb_id):
        m = store.get_movie(mid)
        if not m:
            continue
        store.copy_tmdb_to_movie(mid, old_title=old_title if old_cache else None)
        abs_path = library_paths.resolve(
            m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID, m["file_path"])
        affected.append(mid)
        jobs.append({"mid": mid, "tmdb_id": tmdb_id, "detail": detail,
                     "poster_tmdb": new_poster_tmdb,
                     "old_poster_tmdb": old_poster_tmdb, "abs_path": abs_path})
    return ({"changed": True, "affected_ids": affected,
             "title": new_meta.get("title", ""), "year": new_meta.get("year"),
             "tmdb_id": tmdb_id,
             "background": {"poster": bool(new_poster_tmdb),
                            "avatars": bool(jobs)}}, jobs)

