import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from .. import scanner, store, tmdb
from ..config import settings
from ..regions import normalize_tags

router = APIRouter(prefix="/api")

FilterList = list[str] | None


@router.get("/search")
def search(q: str = "", limit: int = 500, grouped: bool = True,
           genre: FilterList = Query(default=None),
           region: FilterList = Query(default=None),
           country: FilterList = Query(default=None),
           year: FilterList = Query(default=None),
           decade: FilterList = Query(default=None),
           tag: FilterList = Query(default=None),
           min_rating: float | None = None,
           rating_source: str = "tmdb"):
    return {"q": q, "items": store.search_fts(
        q, limit, grouped, genres=store._split_multi(genre),
        regions=store._split_multi(region), countries=store._split_multi(country),
        years=store._split_ints(year), decades=store._split_ints(decade),
        tags=store._split_multi(tag), min_rating=min_rating,
        rating_source=rating_source)}


@router.get("/movies")
def list_movies(grouped: bool = True, limit: int = 500,
                genre: FilterList = Query(default=None),
                region: FilterList = Query(default=None),
                country: FilterList = Query(default=None),
                year: FilterList = Query(default=None),
                decade: FilterList = Query(default=None),
                tag: FilterList = Query(default=None),
                min_rating: float | None = None,
                rating_source: str = "tmdb"):
    return {"items": store.list_movies(
        grouped, genres=store._split_multi(genre),
        regions=store._split_multi(region), countries=store._split_multi(country),
        years=store._split_ints(year), decades=store._split_ints(decade),
        tags=store._split_multi(tag), limit=max(1, min(limit, 2000)),
        min_rating=min_rating, rating_source=rating_source)}


@router.get("/facets")
def facets(grouped: bool = True):
    """动态分类计数：类型/大区/国家/年/年代/标签，只返回有片的项。"""
    return store.get_facets(grouped=grouped)


@router.get("/movies/{movie_id}")
def get_movie(movie_id: int):
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    return m


@router.patch("/movies/{movie_id}")
def patch_movie(movie_id: int, body: dict):
    if not store.get_movie(movie_id):
        raise HTTPException(404, "movie not found")
    allowed = {"title", "overview_override", "douban_rating", "custom_rating",
               "tags", "edition", "spec"}
    data = {k: v for k, v in body.items() if k in allowed}
    for k in ("douban_rating", "custom_rating"):
        if k in data and data[k] is not None:
            try:
                v = float(data[k])
            except (TypeError, ValueError):
                raise HTTPException(422, f"{k} must be 0-10")
            if not 0 <= v <= 10:
                raise HTTPException(422, f"{k} must be 0-10")
            data[k] = v
    if "tags" in data and not isinstance(data["tags"], list):
        raise HTTPException(422, "tags must be a list")
    if "tags" in data and isinstance(data["tags"], list):
        data["tags"] = normalize_tags(data["tags"])
    if "edition" in data:
        from ..editions import sanitize_tag
        if data["edition"] is None:
            data["edition"] = ""
        elif not isinstance(data["edition"], str):
            raise HTTPException(422, "edition must be a string")
        else:
            data["edition"] = sanitize_tag(data["edition"])
    if "spec" in data:
        from ..editions import sanitize_tag
        if data["spec"] is None:
            data["spec"] = ""
        elif not isinstance(data["spec"], str):
            raise HTTPException(422, "spec must be a string")
        else:
            data["spec"] = sanitize_tag(data["spec"])
    # 本地写专用：TMDB 镜像列会被静默丢弃，保证标签/评分小改动不污染镜像
    store.update_movie_local(movie_id, **data)
    store.resync_fts(movie_id)
    return store.get_movie(movie_id)


@router.get("/movies/{movie_id}/files")
def movie_files(movie_id: int):
    """同目录文件清单（只读）：独占目录全量展示；共享目录（如未整理的 batch/）
    只返回本片相关（自身+同 tmdb 版本+同 stem 前缀的花絮/字幕/NFO），并标 scoped=related。"""
    from ..scanner import (SUBTITLE_EXTS, VIDEO_EXTS, is_extra, is_sample)
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    rel_dir = os.path.dirname(m["file_path"])
    movie_dir = os.path.join(settings.media_root, rel_dir)
    empty = {"dir": rel_dir, "scoped": "dir", "hint": "",
             "feature": [], "extras": [], "samples": [],
             "subtitles": [], "nfos": [], "others": []}
    if not os.path.isdir(movie_dir):
        return empty
    own_paths = {v.get("file_path", "") for v in (m.get("versions") or [])}
    own_paths.add(m["file_path"])
    own_stems = {os.path.splitext(os.path.basename(p))[0] for p in own_paths}

    def _same_stem(name_stem: str) -> bool:
        for s in own_stems:
            if name_stem == s or name_stem.startswith(
                    (s + "-", s + ".", s + "_", s + " ")):
                return True
        return False

    names = sorted(os.listdir(movie_dir))
    # 共享目录判定：存在不属于本片的正片视频
    foreign = False
    for n in names:
        full = os.path.join(movie_dir, n)
        if not os.path.isfile(full):
            continue
        rel = os.path.join(rel_dir, n) if rel_dir else n
        _, ex = os.path.splitext(n)
        if ex.lower() in VIDEO_EXTS and not is_sample(n) \
                and not is_extra(rel) and rel not in own_paths:
            foreign = True
            break
    out = {"dir": rel_dir,
           "scoped": "related" if foreign else "dir", "hint": "",
           "feature": [], "extras": [], "samples": [],
           "subtitles": [], "nfos": [], "others": []}
    if foreign:
        out["hint"] = "该片尚未归档，同目录为共享目录，仅显示同名相关文件"
    for n in names:
        full = os.path.join(movie_dir, n)
        if not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        item = {"name": n, "size": size}
        rel = os.path.join(rel_dir, n) if rel_dir else n
        _, ex = os.path.splitext(n)
        ex = ex.lower()
        stem = os.path.splitext(n)[0]
        if foreign and not (rel in own_paths or _same_stem(stem)
                            or n == "movie.nfo"):
            continue
        if ex in SUBTITLE_EXTS:
            out["subtitles"].append(item)
        elif n.endswith(".nfo"):
            out["nfos"].append(item)
        elif ex in VIDEO_EXTS:
            if is_sample(n):
                out["samples"].append(item)
            elif is_extra(rel):
                out["extras"].append(item)
            else:
                out["feature"].append(item)
        else:
            out["others"].append(item)
    # 归属花絮子目录（仅独占目录：共享目录的 extras/ 归属不明，不混入各片）
    if foreign:
        return out
    extras_dir = os.path.join(movie_dir, "extras")
    try:
        sub = sorted(os.listdir(extras_dir)) if os.path.isdir(extras_dir) else []
    except OSError:
        sub = []
    seen = {x["name"] for lst in
            (out["extras"], out["samples"], out["feature"]) for x in lst}
    for n in sub:
        full = os.path.join(extras_dir, n)
        if not os.path.isfile(full):
            continue
        if os.path.splitext(n)[1].lower() not in VIDEO_EXTS:
            continue
        disp = f"extras/{n}"
        if disp in seen:
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        out["extras"].append({"name": disp, "size": size})
    # 已归属但尚散落在外的花絮（待 collect 归位）：带相对路径展示
    try:
        attached = store.list_extras_by_movie(movie_id)
    except Exception:
        attached = []
    known = {x["name"] for lst in
             (out["extras"], out["samples"], out["feature"]) for x in lst}
    known_basenames = {os.path.basename(x) for x in known}
    for e in attached:
        full = os.path.join(settings.media_root, e["file_path"])
        if not os.path.isfile(full):
            continue
        if e["file_path"] in known or os.path.basename(e["file_path"]) in known_basenames:
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        out["extras"].append({"name": e["file_path"], "size": size,
                              "attached": True})
        known.add(e["file_path"])
    return out


@router.post("/scan")
def run_scan():
    return {"results": scanner.scan_all()}


@router.get("/tmdb/search")
def tmdb_search(q: str, year: int | None = None):
    """手动匹配第一步：按关键词查TMDB候选。"""
    out = []
    for r in tmdb.search_movie(q, year)[:10]:
        out.append({"tmdb_id": r.get("id"), "title": r.get("title"),
                    "original_title": r.get("original_title"),
                    "release_date": r.get("release_date"),
                    "vote_average": r.get("vote_average")})
    return {"items": out}


@router.post("/movies/{movie_id}/match")
def manual_match(movie_id: int, body: dict, background_tasks: BackgroundTasks):
    """手动匹配第二步：用TMDB ID强制绑定（显式换绑，覆盖标题并重建人物关联）。
    快回包：文字元数据同步落库；海报/头像/NFO 放后台，前端轮询补齐。"""
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    tmdb_id = body.get("tmdb_id")
    if not tmdb_id:
        raise HTTPException(422, "tmdb_id required")
    try:
        detail = tmdb.movie_detail(int(tmdb_id))
    except Exception as e:
        raise HTTPException(502, f"tmdb fetch failed: {e}")
    abs_path = os.path.join(settings.media_root, m["file_path"])
    out, media = scanner.apply_tmdb_detail_fast(
        movie_id, detail, abs_path, force_title=True)
    store.update_movie_local(movie_id, needs_review=0)
    background_tasks.add_task(scanner.finish_tmdb_media, movie_id, detail,
                              abs_path, media["poster_tmdb"],
                              media["old_poster_tmdb"])
    return {"id": movie_id, **out}


@router.post("/movies/{movie_id}/refresh")
def refresh_movie(movie_id: int, background_tasks: BackgroundTasks):
    """手动刷新：按本片 tmdb_id 抓远端 → 写镜像 → 有变化才扇出到同 tmdb_id 全版本。
    无变化时不碰任何 movies 行（含 updated_at）。海报/头像/NFO 放后台补齐。"""
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    if not m.get("tmdb_id"):
        raise HTTPException(422, "movie has no tmdb_id, use /match first")
    try:
        out, jobs = scanner.refresh_tmdb_id_fast(int(m["tmdb_id"]))
    except Exception as e:
        raise HTTPException(502, f"tmdb fetch failed: {e}")
    if jobs:
        background_tasks.add_task(scanner.finish_refresh_media, jobs)
    return {"id": movie_id, **out}
