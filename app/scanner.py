"""扫描器：遍历MEDIA_ROOT → guessit解析 → TMDB匹配 → 入库+NFO+海报。V1只做电影。

TMDB数据经 tmdb_cache 镜像：新文件同 tmdb_id 直接复用缓存零请求；
默认永不自动刷新，只经手动匹配/手动刷新入口写入远端数据。
"""
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from guessit import guessit

from . import store, tmdb
from .config import settings
from .db import POSTER_DIR, ensure_dirs
from .nfo import write_movie_nfo
from .regions import resolve as resolve_region

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".webm"}


_ROMAN = {"II": "2", "III": "3", "IV": "4", "VI": "6",
           "VII": "7", "VIII": "8", "IX": "9"}


def normalize_title(s: str) -> str:
    """NFKC：全角→半角、Ⅱ→II等兼容字符归一；独立多字母罗马数字→阿拉伯数字；压空白。
    注：单字母V/X歧义大（如V字仇杀队/Project X），故意不转。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"\b(II|III|IV|VI|VII|VIII|IX)\b",
               lambda m: _ROMAN[m.group(1)], s)
    return re.sub(r"\s+", " ", s).strip()


def short_candidates(title: str) -> list[str]:
    """长标题fallback：冒号后段、去剧场版前缀、去加长版后缀，逐个重试。"""
    cands = [title]
    for sep in ("：", ":"):
        if sep in title:
            cands.append(title.split(sep)[-1].strip())
    m = re.search(r"剧场版\s*\d*\s*[:：]?\s*(.+)$", title)
    if m:
        cands.append(m.group(1).strip())
    tail = re.sub(r"\s*(加长版|导演剪辑版|加长收藏版)\s*$", "", title).strip()
    if tail != title:
        cands.append(tail)
    seen, out = set(), []
    for x in cands:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def parse_filename(name: str) -> dict:
    try:
        g = guessit(name)
    except Exception:
        return {"title": os.path.splitext(name)[0], "year": None, "type": "movie"}
    title = g.get("title") or os.path.splitext(name)[0]
    if isinstance(title, list):
        title = title[0]
    return {"title": str(title), "year": g.get("year"),
            "type": g.get("type", "movie")}


def pick_match(results: list[dict], year: int | None) -> dict | None:
    if not results:
        return None
    if year:
        for r in results:
            rd = (r.get("release_date") or "")[:4]
            if rd.isdigit() and abs(int(rd) - year) <= 1:
                return r
    return results[0]


def search_with_fallback(title: str, year: int | None) -> tuple[dict | None, str]:
    """依次试短查询，返回(命中, 实际生效的查询词)。"""
    for q in short_candidates(title):
        results = tmdb.search_movie(q, year)
        m = pick_match(results, year)
        if m:
            return m, q
    return None, title


def meta_from_detail(detail: dict) -> dict:
    """TMDB详情 → 可写入 tmdb_cache 的字典（产地/类型/语言，不含海报/NFO）。"""
    year = None
    if detail.get("release_date", "")[:4].isdigit():
        year = int(detail["release_date"][:4])
    countries = [c.get("iso_3166_1", "") for c in detail.get("production_countries", [])
                 if c.get("iso_3166_1")]
    primary, region = resolve_region(countries, detail.get("original_language"))
    return {
        "title": detail.get("title", ""),
        "original_title": detail.get("original_title", ""),
        "year": year,
        "overview": detail.get("overview", ""),
        "tmdb_id": detail["id"],
        "imdb_id": (detail.get("external_ids") or {}).get("imdb_id", ""),
        "tmdb_rating": detail.get("vote_average"),
        "genres": [g["name"] for g in detail.get("genres", []) if g.get("name")],
        "genre_ids": [g["id"] for g in detail.get("genres", []) if g.get("id")],
        "original_language": detail.get("original_language", "") or "",
        "origin_countries": countries,
        "origin_country": primary,
        "region": region,
        "media_type": "movie",
    }


def extract_credits(detail: dict) -> dict:
    """从 movie_detail.credits 提取最小可用集（导演+前10演员），存入 tmdb_cache 供复用。"""
    cast = []
    for i, c in enumerate((detail.get("credits") or {}).get("cast", [])[:10]):
        if not c.get("id"):
            continue
        cast.append({"id": c["id"], "name": c.get("name", ""),
                     "profile_path": c.get("profile_path"),
                     "character": c.get("character", ""), "order": i})
    crew = []
    for d in (detail.get("credits") or {}).get("crew", []):
        if d.get("job") == "Director" and d.get("id"):
            crew.append({"id": d["id"], "name": d.get("name", ""),
                         "profile_path": d.get("profile_path")})
    return {"cast": cast, "crew": crew}


def jobs_from_credits(credits: dict) -> list[tuple]:
    """credits(原始detail或cache) → jobs[(tmdb_id, name, profile_path, role, character, order)]。"""
    credits = credits or {}
    jobs: list[tuple] = []
    for d in credits.get("crew", []):
        if d.get("id"):
            jobs.append((d["id"], d.get("name", ""), d.get("profile_path"),
                         "director", "", 99))
    for c in (credits.get("cast", []) or [])[:10]:
        if not c.get("id"):
            continue
        jobs.append((c["id"], c.get("name", ""), c.get("profile_path"),
                     "actor", c.get("character", ""), c.get("order", 99)))
    return jobs


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
        raw = store.get_person_raw(pid_tmdb)
        stored_profile = (raw or {}).get("profile_tmdb_path") or ""
        dest = os.path.join(POSTER_DIR, f"person_{pid_tmdb}.jpg")
        if raw and stored_profile == (profile_path or "") and os.path.exists(dest) and (raw.get("avatar") or "") not in ("", None):
            return pid_tmdb, raw.get("avatar") or "", False, profile_path or ""
        if stored_profile != (profile_path or "") and os.path.exists(dest):
            try:
                os.remove(dest)
            except Exception:
                pass
            existed = False
        else:
            existed = os.path.exists(dest)
        avatar = save_person_avatar(pid_tmdb, profile_path)
        if not avatar and raw and os.path.exists(dest):
            avatar = os.path.relpath(dest, settings.data_dir)
        return pid_tmdb, avatar, bool(avatar and avatar != "-" and not existed), profile_path or ""

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


def _write_nfo_for(mid: int, abs_path: str) -> bool:
    try:
        movie = store.get_movie(mid)
        write_movie_nfo(movie, os.path.join(os.path.dirname(abs_path), "movie.nfo"))
        return True
    except Exception:
        return False


def apply_tmdb_detail(mid: int, detail: dict, abs_path: str,
                      force_title: bool = False) -> dict:
    """把TMDB详情写入镜像并复制到本片（人物/海报/NFO/FTS）。
    force_title=True（手动换绑）时无条件覆盖标题；否则保留手工改过的标题。"""
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
            store.update_movie_meta(mid, title=meta.get("title") or "")
        # 其余 TMDB 列经 copy 已同步（copy 内含除 poster 外全量）
    else:
        store.copy_tmdb_to_movie(mid, old_title=old_title if old_cache else None)
    poster_local = ensure_movie_poster(tmdb_id, poster_tmdb, old_poster_tmdb)
    store.update_movie_meta(mid, poster_path=poster_local)
    sync_persons(mid, detail)
    store.resync_fts(mid)
    movie = store.get_movie(mid)
    nfo = _write_nfo_for(mid, abs_path)
    return {"title": movie["title"], "year": movie["year"],
            "tmdb_id": tmdb_id, "nfo": nfo}


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


def refresh_tmdb_id(tmdb_id: int) -> dict:
    """手动刷新唯一入口：抓远端 → 写镜像 → 有变化才扇出到所有同 tmdb_id 版本。
    返回 {changed, affected_ids, title, year}。无变化时不碰任何 movies 行。"""
    detail = tmdb.movie_detail(int(tmdb_id))
    new_meta = meta_from_detail(detail)
    new_poster_tmdb = detail.get("poster_path") or ""
    new_credits = extract_credits(detail)
    old_cache = store.get_tmdb_cached(int(tmdb_id))
    old_title = (old_cache or {}).get("title") or ""
    old_poster_tmdb = (old_cache or {}).get("poster_tmdb_path") or ""
    changed = store.upsert_tmdb_cache(int(tmdb_id), new_meta, new_credits, new_poster_tmdb)
    if not changed:
        mids = store.list_movie_ids_by_tmdb(int(tmdb_id))
        # 仍需补齐人物缺失（如头像缺失）：只做本地修复，不改 updated_at 语义之外的字段
        for mid in mids:
            if store.persons_missing_avatar(mid):
                sync_persons(mid, detail)
                store.resync_fts(mid)
        cur = store.get_tmdb_cached(int(tmdb_id)) or {}
        return {"changed": False, "affected_ids": [],
                "title": cur.get("title", ""), "year": cur.get("year"),
                "tmdb_id": int(tmdb_id)}
    affected: list[int] = []
    for mid in store.list_movie_ids_by_tmdb(int(tmdb_id)):
        m = store.get_movie(mid)
        if not m:
            continue
        store.copy_tmdb_to_movie(mid, old_title=old_title if old_cache else None)
        poster_local = ensure_movie_poster(int(tmdb_id), new_poster_tmdb, old_poster_tmdb)
        store.update_movie_meta(mid, poster_path=poster_local)
        sync_persons(mid, detail)
        store.resync_fts(mid)
        abs_path = os.path.join(settings.media_root, m["file_path"])
        _write_nfo_for(mid, abs_path)
        affected.append(mid)
    return {"changed": True, "affected_ids": affected,
            "title": new_meta.get("title", ""), "year": new_meta.get("year"),
            "tmdb_id": int(tmdb_id)}


def scan_one(abs_path: str) -> dict:
    rel = os.path.relpath(abs_path, settings.media_root)
    cached = store.get_by_path(rel)
    if cached and cached.get("tmdb_id"):
        return {"file": rel, "status": "skipped_cached", "title": cached.get("title")}
    parsed = parse_filename(os.path.basename(abs_path))
    parsed["title"] = normalize_title(parsed["title"])
    if parsed["type"] == "episode":
        mid = store.upsert_movie_by_path(rel)
        store.update_movie_meta(mid, title=parsed["title"])
        return {"file": rel, "status": "skipped_episode_v1"}
    m, used_q = search_with_fallback(parsed["title"], parsed["year"])
    if not m:
        mid = store.upsert_movie_by_path(rel)
        store.update_movie_meta(mid, title=parsed["title"], year=parsed["year"])
        return {"file": rel, "status": "no_match", "parsed": parsed}
    tmdb_id = int(m["id"])
    mid = store.upsert_movie_by_path(rel)
    if store.get_tmdb_cached(tmdb_id):
        out = apply_cached_to_movie(mid, tmdb_id, abs_path)
    else:
        detail = tmdb.movie_detail(tmdb_id)
        out = apply_tmdb_detail(mid, detail, abs_path)
    needs_review = 1 if used_q != parsed["title"] else 0
    store.update_movie_local(mid, needs_review=needs_review)
    status = "ok" if not needs_review else "ok_needs_review"
    return {"file": rel, "status": status, "query": used_q, **out}


def scan_all() -> list[dict]:
    ensure_dirs()
    store.init_db()
    out = []
    for root, _, files in os.walk(settings.media_root):
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                try:
                    out.append(scan_one(os.path.join(root, f)))
                except Exception as e:
                    out.append({"file": os.path.relpath(os.path.join(root, f),
                                                        settings.media_root),
                                "status": f"error: {e}"})
    return out
