"""扫描器：遍历MEDIA_ROOT → guessit解析 → TMDB匹配 → 入库+NFO+海报。V1只做电影。"""
import os
import re
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
    """TMDB详情 → 可直接 update_movie_meta 的字典（产地/类型/语言，不含海报/NFO）。"""
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


def sync_persons(mid: int, detail: dict, max_workers: int = 8) -> int:
    """按TMDB credits 落库导演+前10演员（含头像），返回新下载头像数。供扫描/手动匹配/回填共用。

    无照片的记 avatar='-'（确认无，避免回填反复重试）；已有文件不重下。
    头像下载并行（I/O密集），落库串行；同一人多角色去重下载但保留全部关联。
    """
    jobs: list[tuple] = []  # (tmdb_id, name, profile_path, role, character, order)
    for d in (detail.get("credits") or {}).get("crew", []):
        if d.get("job") == "Director" and d.get("id"):
            jobs.append((d["id"], d.get("name", ""), d.get("profile_path"),
                         "director", "", 99))
    for i, c in enumerate((detail.get("credits") or {}).get("cast", [])[:10]):
        if not c.get("id"):
            continue
        jobs.append((c["id"], c.get("name", ""), c.get("profile_path"),
                     "actor", c.get("character", ""), i))

    def _fetch(item: tuple) -> tuple:
        pid_tmdb, _, profile_path, _, _, _ = item
        if not profile_path:
            return pid_tmdb, "-", False
        dest = os.path.join(POSTER_DIR, f"person_{pid_tmdb}.jpg")
        existed = os.path.exists(dest)
        avatar = save_person_avatar(pid_tmdb, profile_path)
        return pid_tmdb, avatar, bool(avatar and not existed)

    uniq: dict = {}
    for job in jobs:
        uniq.setdefault(job[0], job)
    avatars: dict = {}
    downloaded = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for pid_tmdb, avatar, is_new in ex.map(_fetch, uniq.values()):
            avatars[pid_tmdb] = avatar
            downloaded += 1 if is_new else 0
    for pid_tmdb, name, _, role, character, order in jobs:
        pid = store.upsert_person(pid_tmdb, name, avatars.get(pid_tmdb))
        store.link_person(mid, pid, role, character, order)
    return downloaded


def apply_tmdb_detail(mid: int, detail: dict, abs_path: str) -> dict:
    """把TMDB详情落库（人物/海报/NFO/FTS），供自动扫描与手动匹配共用。"""
    poster_local = ""
    if detail.get("poster_path"):
        poster_local = os.path.join(POSTER_DIR, f"{detail['id']}.jpg")
        if tmdb.download_poster(detail["poster_path"], poster_local):
            poster_local = os.path.relpath(poster_local, settings.data_dir)
        else:
            poster_local = ""
    meta = meta_from_detail(detail)
    meta["poster_path"] = poster_local
    store.update_movie_meta(mid, **meta)
    sync_persons(mid, detail)
    store.resync_fts(mid)
    movie = store.get_movie(mid)
    try:
        write_movie_nfo(movie, os.path.join(os.path.dirname(abs_path), "movie.nfo"))
        nfo = True
    except Exception:
        nfo = False
    return {"title": movie["title"], "year": movie["year"],
            "tmdb_id": detail["id"], "nfo": nfo}


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
    detail = tmdb.movie_detail(m["id"])
    mid = store.upsert_movie_by_path(rel)
    out = apply_tmdb_detail(mid, detail, abs_path)
    needs_review = 1 if used_q != parsed["title"] else 0
    store.update_movie_meta(mid, needs_review=needs_review)
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
