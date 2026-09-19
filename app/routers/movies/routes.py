"""routers.movies.routes（自 app/routers/movies.py 拆分，评审 B9/R05-Q1；经 movies 门面使用）。"""
import os
from fastapi import (APIRouter, BackgroundTasks, File, HTTPException, Query,
                     Request, UploadFile)
from ... import config
from ... import store
from ... import scanner
from ... import tmdb
from ... import library_paths
from ...regions import normalize_tags
from ...log import get_logger
logger = get_logger("movies.routes")
from ...scanner import same_stem
from .common import (_INLINE_EXTS, FilterList, _page, _stream_upload)
from .scope import _movie_delete_scope
__all__ = ['router', 'search', 'search_suggest', 'list_movies', 'facets', 'get_movie', 'patch_movie', 'batch_update', 'movie_collections', 'movie_collection_hint', 'movie_similar', 'movie_files', '_movie_blob_rel', 'movie_blob', 'movie_upload', 'library_upload', 'movie_file_delete', 'run_scan', 'tmdb_search', 'manual_match', 'rescan_movie', 'organize_hint', 'refresh_movie', 'batch_delete_movies', 'movie_poster_orig']

router = APIRouter(prefix="/api")


@router.get("/search")
def search(q: str = "", limit: int = 500, offset: int = 0, grouped: bool = True,
           genre: FilterList = Query(default=None),
           region: FilterList = Query(default=None),
           country: FilterList = Query(default=None),
           year: FilterList = Query(default=None),
           decade: FilterList = Query(default=None),
           tag: FilterList = Query(default=None),
           min_rating: float | None = None,
           rating_source: str = "tmdb",
           watched: int | None = None,
           collection: FilterList = Query(default=None),
           library: FilterList = Query(default=None)):
    lim, off = _page(limit, offset)
    items = store.search_fts(
        q, lim + 1, grouped, genres=store._split_multi(genre),
        regions=store._split_multi(region), countries=store._split_multi(country),
        years=store._split_ints(year), decades=store._split_ints(decade),
        tags=store._split_multi(tag), min_rating=min_rating,
        rating_source=rating_source, watched=watched,
        collection_ids=store._split_ints(collection),
        library_ids=store._split_ints(library), offset=off)
    has_more = len(items) > lim
    return {"q": q, "items": items[:lim], "has_more": has_more,
            "limit": lim, "offset": off}


@router.get("/search/suggest")
def search_suggest(q: str = "", limit: int = 8,
                   library: FilterList = Query(default=None)):
    """搜索框联想：本地库标题/原名（中文/部分词可用）+ 演员名（含参演数），轻量返回。"""
    libs = store._split_ints(library)
    return {"q": q, "items": store.suggest_titles(q, limit, library_ids=libs),
            "persons": store.suggest_people(q, 5, library_ids=libs)}


@router.get("/movies")
def list_movies(grouped: bool = True, limit: int = 500, offset: int = 0,
                genre: FilterList = Query(default=None),
                region: FilterList = Query(default=None),
                country: FilterList = Query(default=None),
                year: FilterList = Query(default=None),
                decade: FilterList = Query(default=None),
                tag: FilterList = Query(default=None),
                min_rating: float | None = None,
                rating_source: str = "tmdb",
                watched: int | None = None,
                collection: FilterList = Query(default=None),
                library: FilterList = Query(default=None)):
    lim, off = _page(limit, offset)
    items = store.list_movies(
        grouped, genres=store._split_multi(genre),
        regions=store._split_multi(region), countries=store._split_multi(country),
        years=store._split_ints(year), decades=store._split_ints(decade),
        tags=store._split_multi(tag), limit=lim + 1,
        min_rating=min_rating, rating_source=rating_source, watched=watched,
        collection_ids=store._split_ints(collection),
        library_ids=store._split_ints(library), offset=off)
    has_more = len(items) > lim
    return {"items": items[:lim], "has_more": has_more, "limit": lim, "offset": off}


@router.get("/facets")
def facets(grouped: bool = True, library: FilterList = Query(default=None)):
    """动态分类计数：类型/大区/国家/年/年代/标签，只返回有片的项。"""
    return store.get_facets(grouped=grouped,
                            library_ids=store._split_ints(library))


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
               "tags", "edition", "spec", "watched"}
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
    if "title" in data:
        t = (data["title"] or "").strip()
        if not t:
            raise HTTPException(422, "title required")
        if len(t) > 200:
            raise HTTPException(422, "title too long (max 200)")
        data["title"] = t
        data["title_auto"] = 0   # 手工标题：此后刷新/重扫不覆盖（评审 title_auto）
    if "overview_override" in data:
        if data["overview_override"] is None:
            data["overview_override"] = ""
        if not isinstance(data["overview_override"], str):
            raise HTTPException(422, "overview_override must be a string")
        if len(data["overview_override"]) > 20000:
            raise HTTPException(422, "overview_override too long (max 20000)")
    if "tags" in data and not isinstance(data["tags"], list):
        raise HTTPException(422, "tags must be a list")
    if "tags" in data and isinstance(data["tags"], list):
        data["tags"] = normalize_tags(data["tags"])
    if "watched" in data:
        import time as _time
        w = data["watched"]
        if isinstance(w, bool):
            w = 1 if w else 0
        try:
            w = int(w)
        except (TypeError, ValueError):
            raise HTTPException(422, "watched must be 0/1")
        data["watched"] = 1 if w else 0
        data["watched_at"] = int(_time.time()) if data["watched"] else 0
    if "edition" in data:
        from ...editions import sanitize_tag
        if data["edition"] is None:
            data["edition"] = ""
        elif not isinstance(data["edition"], str):
            raise HTTPException(422, "edition must be a string")
        else:
            data["edition"] = sanitize_tag(data["edition"])
    if "spec" in data:
        from ...editions import sanitize_tag
        if data["spec"] is None:
            data["spec"] = ""
        elif not isinstance(data["spec"], str):
            raise HTTPException(422, "spec must be a string")
        else:
            data["spec"] = sanitize_tag(data["spec"])
    # 本地写专用：TMDB 镜像列会被静默丢弃，保证标签/评分小改动不污染镜像
    # （update_movie_local → update_movie_meta 内已 resync_fts，不再重复刷）
    store.update_movie_local(movie_id, **data)
    return store.get_movie(movie_id)


@router.post("/movies/batch")
def batch_update(body: dict):
    """海报墙多选批量编辑（海报粒度）：ids 为代表行 id，有 tmdb_id 则展开到同 tmdb 全版本。
    ops: watched(bool) / add_tags[] / remove_tags[] / set_tags[]（与add/remove互斥）
         / douban_rating / custom_rating（null=清空）/ confirm_review(bool，清待确认标记）。"""
    import time as _time
    body = body or {}
    raw_ids = body.get("ids") or []
    ops = body.get("ops") or {}
    try:
        rep_ids = sorted({int(x) for x in raw_ids})
    except (TypeError, ValueError):
        raise HTTPException(422, "ids must be int list")
    if not rep_ids:
        raise HTTPException(422, "ids required")
    if len(rep_ids) > 500:
        raise HTTPException(422, "too many ids (max 500)")
    add_tags = ops.get("add_tags")
    remove_tags = ops.get("remove_tags")
    set_tags = ops.get("set_tags")
    if set_tags is not None and (add_tags is not None or remove_tags is not None):
        raise HTTPException(422, "set_tags is mutually exclusive with add/remove_tags")
    for k in ("add_tags", "remove_tags", "set_tags"):
        v = ops.get(k)
        if v is not None and not isinstance(v, list):
            raise HTTPException(422, f"{k} must be a list")
    norm_add = normalize_tags(add_tags) if add_tags is not None else None
    norm_remove = set(normalize_tags(remove_tags)) if remove_tags is not None else None
    norm_set = normalize_tags(set_tags) if set_tags is not None else None
    confirm_review = ops.get("confirm_review", None)
    if confirm_review is not None:
        confirm_review = bool(confirm_review)
    watched = ops.get("watched", None)
    if watched is not None:
        if isinstance(watched, bool):
            watched = 1 if watched else 0
        try:
            watched = 1 if int(watched) else 0
        except (TypeError, ValueError):
            raise HTTPException(422, "watched must be 0/1")
    ratings: dict = {}
    for k in ("douban_rating", "custom_rating"):
        if k in ops:
            v = ops[k]
            if v is None:
                ratings[k] = None
            else:
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    raise HTTPException(422, f"{k} must be 0-10")
                if not 0 <= f <= 10:
                    raise HTTPException(422, f"{k} must be 0-10")
                ratings[k] = f
    if watched is None and norm_add is None and norm_remove is None \
            and norm_set is None and not ratings and confirm_review is None:
        raise HTTPException(422, "empty ops")
    expanded = store.expand_ids_to_versions(rep_ids)
    now = int(_time.time())
    results = []
    affected_versions = 0
    for rid in rep_ids:
        vers = expanded.get(rid, [])
        if not vers:
            results.append({"id": rid, "expanded_ids": [], "status": "not_found"})
            continue
        ok = 0
        for vid in vers:
            cur_tags = store.get_movie_tags(vid)     # 轻量（评审 B8/R05-B2）
            if cur_tags is None:
                continue
            patch: dict = {}
            if norm_set is not None:
                patch["tags"] = norm_set
            elif norm_add is not None or norm_remove is not None:
                cur = list(cur_tags)
                if norm_add:
                    cur = normalize_tags(cur + norm_add)
                if norm_remove:
                    cur = [t for t in cur if t not in norm_remove]
                patch["tags"] = cur
            if watched is not None:
                patch["watched"] = watched
                patch["watched_at"] = now if watched else 0
            if confirm_review:
                patch["needs_review"] = 0
            patch.update(ratings)
            try:
                store.update_movie_local(vid, **patch)
                ok += 1
            except Exception as e:
                results.append({"id": rid, "expanded_ids": vers,
                                "status": f"error: {e}"})
                break
        else:
            affected_versions += ok
            results.append({"id": rid, "expanded_ids": vers,
                            "status": "ok" if ok else "not_found"})
    return {"total": len(rep_ids), "affected_versions": affected_versions,
            "results": results}


@router.post("/movies/{movie_id}/confirm-match")
def confirm_match(movie_id: int):
    """确认当前匹配无误：清除「待确认」标记（本地写，不重刮、不触网、不改标题）。
    与批量 ops.confirm_review 共用语义（2026-09 用户反馈：待确认应可一键确认）。"""
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    if not m.get("tmdb_id"):
        raise HTTPException(422, "unmatched: use /match to bind a tmdb_id first")
    store.update_movie_local(movie_id, needs_review=0)
    return {"id": movie_id, "needs_review": 0, "tmdb_id": m.get("tmdb_id")}


@router.get("/movies/{movie_id}/collections")
def movie_collections(movie_id: int):
    if not store.get_movie(movie_id):
        raise HTTPException(404, "movie not found")
    return {"id": movie_id, "collections": store.list_collections_for_movie(movie_id)}


@router.get("/movies/{movie_id}/collection-hint")
def movie_collection_hint(movie_id: int):
    """TMDB 系列提示：本片所属远端系列 + 库内同系列兄弟，供一键建合集。"""
    if not store.get_movie(movie_id):
        raise HTTPException(404, "movie not found")
    hint = store.collection_hint_for_movie(movie_id)
    return hint or {"collection_tmdb_id": None, "collection_name": "",
                    "in_library": [], "in_library_count": 0}


@router.get("/movies/{movie_id}/similar")
def movie_similar(movie_id: int, limit: int = 12):
    """库中类似（Plex 式推荐）：纯本地相似度（系列/合集/导演/主演/类型/标签），不调网。"""
    if not store.get_movie(movie_id):
        raise HTTPException(404, "movie not found")
    return {"id": movie_id, "items": store.similar_movies(movie_id, limit)}


@router.get("/movies/{movie_id}/files")
def movie_files(movie_id: int):
    """同目录文件清单（只读）：独占目录全量展示；共享目录（如未整理的 待整理/）
    只返回本片相关（自身+同 tmdb 版本+同 stem 前缀的花絮/字幕/NFO），并标 scoped=related。"""
    from ...scanner import (SUBTITLE_EXTS, VIDEO_EXTS, is_extra, is_sample)
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    rel_dir = os.path.dirname(m["file_path"])
    lib_id = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    root = library_paths.library_root(lib_id)
    movie_dir = os.path.join(root, rel_dir) if rel_dir else root
    empty = {"dir": rel_dir, "scoped": "dir", "hint": "",
             "feature": [], "extras": [], "samples": [],
             "subtitles": [], "nfos": [], "others": []}
    if not os.path.isdir(movie_dir):
        return empty
    own_paths = {v.get("file_path", "") for v in (m.get("versions") or [])}
    own_paths.add(m["file_path"])
    own_stems = {os.path.splitext(os.path.basename(p))[0] for p in own_paths}

    def _same_stem(name_stem: str) -> bool:
        return same_stem(name_stem, own_stems)   # 单源（评审 B9/R05-B4）

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
            _st = os.stat(full)
            size, mtime = _st.st_size, int(_st.st_mtime)
        except OSError:
            size, mtime = 0, 0
        rel = os.path.join(rel_dir, n) if rel_dir else n
        item = {"name": n, "rel": rel, "size": size, "mtime": mtime}
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
        disp = f"extras/{n}"
        if disp in seen:
            continue
        try:
            _st = os.stat(full)
            size, mtime = _st.st_size, int(_st.st_mtime)
        except OSError:
            size, mtime = 0, 0
        rel = os.path.join(rel_dir, disp) if rel_dir else disp
        item = {"name": disp, "size": size, "mtime": mtime, "rel": rel}
        # 花絮子目录全文件列出：视频进 extras 组，其余进 others 组（不再隐身）
        if os.path.splitext(n)[1].lower() in VIDEO_EXTS:
            out["extras"].append(item)
        else:
            out["others"].append(item)
    # 已归属但尚散落在外的花絮（待 collect 归位）：带相对路径展示
    try:
        attached = store.list_extras_by_movie(movie_id)
    except Exception:
        attached = []
    known = {x["name"] for lst in
             (out["extras"], out["samples"], out["feature"],
              out["subtitles"], out["nfos"], out["others"]) for x in lst}
    known_basenames = {os.path.basename(x) for x in known}
    for e in attached:
        full = library_paths.resolve(lib_id, e["file_path"])
        if not os.path.isfile(full):
            continue
        if e["file_path"] in known or os.path.basename(e["file_path"]) in known_basenames:
            continue
        try:
            _st = os.stat(full)
            size, mtime = _st.st_size, int(_st.st_mtime)
        except OSError:
            size, mtime = 0, 0
        out["extras"].append({"name": e["file_path"], "rel": e["file_path"],
                              "size": size, "mtime": mtime,
                              "attached": True})
        known.add(e["file_path"])
    return out


def _movie_blob_rel(movie_id: int, name: str) -> tuple[dict, str]:
    """详情页文件定位：name 可为 files 清单的 name 或 rel；返回 (movie, rel)。"""
    from ..fs import _check_inside_root
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    lib_id = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    raw = (name or "").strip()
    if not raw:
        raise HTTPException(422, "name required")
    rel_dir = os.path.dirname(m["file_path"])
    cands = [raw]
    # 清单 name 形态：顶层名 / extras/名 / 散落花絮全路径
    if rel_dir and "/" not in raw.replace("\\", "/"):
        cands.append(os.path.join(rel_dir, raw))
    elif rel_dir and not raw.startswith(rel_dir.rstrip("/") + "/") \
            and os.path.basename(raw) == raw:
        cands.append(os.path.join(rel_dir, raw))
    if rel_dir:
        cands.append(os.path.join(rel_dir, os.path.basename(raw)))
        cands.append(os.path.join(rel_dir, "extras", os.path.basename(raw)))
    rel = ""
    for c in cands:
        try:
            norm = _check_inside_root(c)
        except HTTPException:
            continue
        if os.path.isfile(library_paths.resolve(lib_id, norm)):
            rel = norm
            break
    if not rel:
        # 最后按原样归一一次，给出明确 404 而非 422
        try:
            rel = _check_inside_root(raw)
        except HTTPException:
            raise HTTPException(422, f"illegal path: {raw!r}")
        raise HTTPException(404, "file not found")
    # 越界校验：必须在片目录（含 extras/）内，或是本片已归属花絮
    allowed = False
    try:
        rel_dir_norm = os.path.normpath(rel_dir) if rel_dir else ""
        parent = os.path.normpath(os.path.dirname(rel))
        if parent == rel_dir_norm or parent == os.path.normpath(
                os.path.join(rel_dir_norm, "extras") if rel_dir_norm else "extras"):
            allowed = True
    except Exception:
        pass
    if not allowed:
        try:
            attached = {e["file_path"] for e in store.list_extras_by_movie(movie_id)}
            if rel in attached:
                allowed = True
        except Exception:
            pass
    if not allowed:
        own = {v.get("file_path", "") for v in (m.get("versions") or [])}
        own.add(m["file_path"])
        if rel in own:
            allowed = True
    if not allowed:
        raise HTTPException(403, "not in this movie dir")
    return m, rel


@router.get("/movies/{movie_id}/blob")
def movie_blob(movie_id: int, request: Request, name: str = "", mode: str = "",
               inline: int = 0):
    """详情页下载/预览：name 取 /files 清单的 name 或 rel。

    默认原样发送（本地 FileResponse / 远程 Range 流式，视频可拖进度、图片可直显）；
    mode=text 返回前 64KB 文本（srt/nfo/剧本预览用）。
    inline=1 且扩展名在白名单（图片/PDF）时不带 attachment，供 <iframe>/<img> 内嵌预览
    （评审 B6/R05-D3：带 attachment 的 PDF 会触发下载而不是渲染）。"""
    from ... import storage
    from ..blob import media_response, text_response, guess_media_type
    m, rel = _movie_blob_rel(movie_id, name)
    backend = storage.backend_for(
        m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
    if (mode or "").strip().lower() == "text":
        return text_response(backend, rel)
    if inline and os.path.splitext(rel)[1].lower() in _INLINE_EXTS:
        return media_response(request, backend, rel,
                              media_type=guess_media_type(rel), inline=True)
    return media_response(request, backend, rel,
                          filename=os.path.basename(backend.norm(rel)))


@router.post("/movies/{movie_id}/upload")
def movie_upload(movie_id: int, file: UploadFile = File(...),
                 subdir: str = Query(default="")):
    """详情页上传：multipart file 字段；周边放片目录，花絮类进 extras/。

    流式落盘（1MB 分块，不占内存，>2GB 可用；局域网场景），先写 .part 再原子
    改名；中断残留 .part 下次同名上传覆盖。落盘后按类型入库：
    正片→scan_one，花絮→attribute_extra，字幕/周边→仅文件。
    subdir 仅允许空或 extras（显式放花絮子目录）。
    """
    from ...scanner import is_feature_video as _is_feat, is_sidecar as _is_side
    from ..files import _require_writable, _safe_component
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    raw_name = os.path.basename((file.filename or "").strip())
    safe = _safe_component(raw_name)
    if not safe or safe in (".", ".."):
        raise HTTPException(422, "illegal file name")
    sub = (subdir or "").strip().strip("/")
    if sub not in ("", "extras"):
        raise HTTPException(422, "subdir must be ''|extras")
    rel_dir = os.path.dirname(m["file_path"])
    lib_id = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    _require_writable(lib_id)
    root = library_paths.library_root(lib_id)
    movie_dir = os.path.join(root, rel_dir) if rel_dir else root
    if not os.path.isdir(movie_dir):
        raise HTTPException(404, "movie dir missing")
    target_dir = os.path.join(movie_dir, "extras") if (
        sub == "extras" or _is_side(safe)) else movie_dir
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as e:
        raise HTTPException(500, f"mkdir failed: {e}")
    dst = os.path.join(target_dir, safe)
    try:
        size = _stream_upload(file, dst)
    finally:
        try:
            file.file.close()
        except Exception:
            pass
    rel = os.path.relpath(dst, root)
    status = "stored"
    try:
        if _is_feat(rel):
            r = scanner.scan_one(dst, library_id=lib_id)
            status = r.get("status", "stored")
        elif _is_side(rel):
            r = scanner.attribute_extra(dst, library_id=lib_id)
            status = r.get("status", "stored")
    except Exception as e:
        status = f"stored_scan_warn: {e}"
    return {"name": safe, "rel": rel, "size": size, "status": status}


@router.post("/uploads")
def library_upload(file: UploadFile = File(...),
                   relpath: str = Query(default=""),
                   target_dir: str = Query(default="待整理"),
                   library_id: int | None = Query(default=None)):
    """库页上传：multipart file 字段；relpath 透传浏览器相对路径
    （文件夹模式为 webkitRelativePath，单文件模式为文件名）。

    目标= target_dir/relpath（逐段清洗并约束在库根内，默认落到
    待整理/，本地结构原样保留）。流式落盘（1MB 分块，先写 .part 再原子
    改名；已存在 409 跳过，绝不覆盖）。落盘后按类型入库：
    正片→scan_one，花絮→attribute_extra，字幕/周边→仅文件。
    `library_id` 缺省=默认库（多库 v12）。
    """
    from ...scanner import is_feature_video as _is_feat, is_sidecar as _is_side
    from ..files import _check_inside_root, _require_writable, _safe_component
    lid = library_paths.default_id() if library_id is None else int(library_id)
    raw = (relpath or "").strip().strip("/") or (file.filename or "").strip()
    raw_segs = [s for s in raw.replace("\\", "/").split("/")]
    if any(s == ".." for s in raw_segs):
        raise HTTPException(422, "illegal path")
    segs = [s for s in raw_segs if s not in ("", ".", "..")]
    safe_segs = [_safe_component(s) for s in segs]
    safe_segs = [s for s in safe_segs if s and s not in (".", "..")]
    if not safe_segs or safe_segs[-1].startswith("."):
        raise HTTPException(422, "illegal file name")
    tmods = [_safe_component(s) for s in
             (target_dir or "").strip().strip("/").replace("\\", "/").split("/")]
    tmods = [s for s in tmods if s and s not in (".", "..")]
    rel = _check_inside_root("/".join([*(tmods or ["待整理"]), *safe_segs]), lid)
    dst = library_paths.resolve(lid, rel)
    _require_writable(lid)
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
    except OSError as e:
        raise HTTPException(500, f"mkdir failed: {e}")
    try:
        size = _stream_upload(file, dst)
    finally:
        try:
            file.file.close()
        except Exception:
            pass
    status = "stored"
    try:
        if _is_feat(rel):
            r = scanner.scan_one(dst, library_id=lid)
            status = r.get("status", "stored")
        elif _is_side(rel):
            r = scanner.attribute_extra(dst, library_id=lid)
            status = r.get("status", "stored")
    except Exception as e:
        status = f"stored_scan_warn: {e}"
    movie_id = None
    try:
        m = store.get_by_path(rel, library_id=lid)
        if m:
            movie_id = m["id"]
    except Exception:
        pass
    return {"name": safe_segs[-1], "rel": rel, "size": size,
            "status": status, "movie_id": movie_id, "library_id": lid}


@router.delete("/movies/{movie_id}/files")
def movie_file_delete(movie_id: int, body: dict | None = None):
    """详情页删单文件：{name, dry_run, confirm}。

    周边/花絮 dry_run:false 即删；正片必须 confirm:true（影响海报墙，二次警告）。"""
    from ..fs import _exec_delete_one, _impact_for_delete
    body = body or {}
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    _, rel = _movie_blob_rel(movie_id, str(body.get("name") or ""))
    plan = _impact_for_delete(rel)
    dry_run = body.get("dry_run", True)
    confirm = bool(body.get("confirm", False))
    if dry_run or (plan.get("requires_confirm") and not confirm):
        return {"dry_run": True, "plans": [plan],
                "needs_confirm": 1 if plan.get("requires_confirm") else 0,
                "hint": ("正片文件：删除后将从海报墙移除，需 confirm:true 二次确认"
                         if plan.get("requires_confirm") else "")}
    from ..files import _require_writable as _rw
    _rw(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
    r = _exec_delete_one(plan)
    return {"dry_run": False, "total": 1,
            "deleted": 1 if r.get("status") == "deleted" else 0,
            "results": [r]}


@router.post("/scan")
def run_scan():
    """全量扫描：回包给聚合摘要（评审 B7/R03-B5：万级文件时不再把全部结果塞进响应）。"""
    res = scanner.scan_all()
    counts: dict = {}
    errors: list = []
    for r in res:
        st = str(r.get("status") or "")
        counts[st] = counts.get(st, 0) + 1
        if st.startswith("error"):
            errors.append({"file": r.get("file", ""), "status": st[:200]})
    return {"total": len(res), "counts": counts, "errors": errors[:100],
            "results": res[:200]}


@router.get("/tmdb/search")
def tmdb_search(q: str, year: int | None = None,
                library: FilterList = Query(default=None)):
    """手动匹配第一步：按关键词查候选（E 阶段支持降级链）。

    - `provider=auto`（默认）：TMDB 优先（有凭据且可达），失败回退本地离线索引，
      再按库 `metadata_providers` 链尝试 wikidata/（可选）douban。
    - 响应 `source` 标注来源；`tmdb_id` 为空的是提示候选（不可直接绑定）。
    """
    from ... import metadata
    libs = store._split_ints(library)
    lib_id = libs[0] if len(libs) == 1 else None
    items: list[dict] = []
    source = ""
    if (config.effective_tmdb_read_token() or config.effective_tmdb_api_key()):
        try:
            for r in tmdb.search_movie(q, year)[:10]:
                items.append({"tmdb_id": r.get("id"), "title": r.get("title"),
                              "original_title": r.get("original_title"),
                              "release_date": r.get("release_date"),
                              "vote_average": r.get("vote_average"),
                              "source": "tmdb"})
            source = "tmdb"
        except Exception as e:
            logger.warning("tmdb search failed q=%s: %s", q, e)
    if not items:
        try:
            for c in metadata.chain.search(q, year, "movie", library_id=lib_id,
                                           limit=10):
                items.append({**c.to_dict(),
                              "release_date": str(c.year or ""),
                              "vote_average": None})
            source = "offline" if items else "none"
        except Exception as e:
            logger.warning("offline search failed q=%s: %s", q, e)
    return {"items": items, "source": source}


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
        tmdb_id = int(tmdb_id)
    except (TypeError, ValueError):
        raise HTTPException(422, "tmdb_id must be int")
    if not (0 < tmdb_id <= 2 ** 31 - 1):     # 值域守卫（评审 B6/R07-B2）
        raise HTTPException(422, "tmdb_id out of range")
    # 写目标：本地=绝对路径；远程直读=backend+rel（此前写挂载点导致 NAS 不更新）
    target = scanner.write_target(m)
    try:
        detail = tmdb.movie_detail(int(tmdb_id))
    except Exception as e:
        # TMDB 不可用（无 token/断网/401）时用本地缓存离线绑定（本地库数据支持远程库纠正）
        if not store.get_tmdb_cached(int(tmdb_id)):
            raise HTTPException(502, f"tmdb fetch failed: {e}") from e
        logger.info("TMDB 不可用，手动匹配走本地缓存 movie_id=%s tmdb_id=%s",
                    movie_id, tmdb_id)
        cached = store.get_tmdb_cached(int(tmdb_id)) or {}
        # 显式换绑：先强制对齐缓存标题（copy_tmdb_to_movie 不覆盖手工标题），
        # 再落缓存元数据与 NFO/海报（标题/studios 等一次写对）
        if cached.get("title"):
            try:
                store.update_movie_meta(movie_id, title=str(cached["title"]),
                                        title_auto=0)
            except Exception as e2:
                logger.debug("force title from cache failed movie_id=%s: %s",
                             movie_id, e2)
        try:
            store.update_movie_meta(movie_id, nfo_hash="")
        except Exception as e2:
            logger.debug("clear nfo_hash failed movie_id=%s: %s", movie_id, e2)
        try:
            out = scanner.apply_cached_to_movie(
                movie_id, int(tmdb_id), target.get("abs_path") or "",
                backend=target.get("backend"), rel=target.get("rel") or "",
                force_title=True)
        except Exception as e2:
            logger.warning("offline match apply failed movie_id=%s: %s", movie_id, e2)
            raise HTTPException(500, f"apply cached failed: {e2}") from e2
        store.update_movie_local(movie_id, needs_review=0)
        try:
            store.update_movie_meta(movie_id, match_source="local")
        except Exception as e2:
            logger.debug("persist match_source failed movie_id=%s: %s", movie_id, e2)
        return {"id": movie_id, "offline": True, **out}
    out, media = scanner.apply_tmdb_detail_fast(movie_id, detail, "", force_title=True)
    store.update_movie_local(movie_id, needs_review=0)
    # 显式换绑：清 NFO 所有权哈希，允许覆盖 NAS 上旧的（错配）NFO
    try:
        store.update_movie_meta(movie_id, nfo_hash="")
    except Exception as e:
        logger.debug("clear nfo_hash failed movie_id=%s: %s", movie_id, e)
    background_tasks.add_task(
        scanner.finish_tmdb_media, movie_id, detail,
        target.get("abs_path") or "", media["poster_tmdb"], media["old_poster_tmdb"],
        backend=target.get("backend"), rel=target.get("rel") or "")
    return {"id": movie_id, **out}


@router.get("/movies/{movie_id}/organize-hint")
def organize_hint(movie_id: int):
    """重新匹配后的归档推荐（评审 B9 后续）：按行内路径自动选 就地/搬迁，dry_run 预览。

    返回 {needs, total, plans, conflicts, params}；params 可直接回传 /api/files/organize 执行。
    未匹配行返回 needs=false（先匹配再归档）。"""
    from ..files import _organize
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    if not m.get("tmdb_id"):
        return {"needs": False, "total": 0, "plans": [], "conflicts": [],
                "params": {}, "reason": "unmatched"}
    rel = (m.get("file_path") or "").strip()
    if not rel:
        raise HTTPException(422, "movie has no file_path")
    top = rel.split("/")[0] if "/" in rel else ""
    lib_id = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    if top and top != "电影":
        # 不在正式库（待整理/下载等）→ 推荐搬到 电影/标题 (年份)/（扁平，D5）
        d = _organize("relocate", from_prefix=top, to_dir="电影",
                      only={movie_id}, dry_run=True, library_id=lib_id)
        params = {"mode": "relocate", "from_prefix": top, "to_dir": "电影",
                  "library_id": lib_id}
    else:
        # 已在电影分区/根目录平铺 → 就地规范化（建片目录、改规范名）
        d = _organize("inplace", only={movie_id}, dry_run=True, library_id=lib_id)
        params = {"mode": "inplace", "library_id": lib_id}
    plans = d.get("plans") or []
    return {"needs": bool(plans), "total": len(plans), "plans": plans[:20],
            "conflicts": (d.get("conflicts") or [])[:20], "params": params}


@router.post("/movies/{movie_id}/rescan")
def rescan_movie(movie_id: int):
    """手动重试刮削（评审 B9 后续）：读行内 file_path，force 跳过增量跳过与缓存短路。
    走 StorageBackend：直读远程库（无挂载）同样可重扫。刮削失败的行（scan_failed/
    no_match）重试后仍在，绝不因失败消失。"""
    from ... import storage

    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    rel = (m.get("file_path") or "").strip()
    if not rel:
        raise HTTPException(422, "movie has no file_path")
    try:
        backend = storage.backend_for(
            m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        backend.stat(rel)
    except storage.StorageNotFound:
        raise HTTPException(410, f"file missing: {rel}")
    except storage.StorageOffline as e:
        raise HTTPException(503, f"source offline: {e}")
    except storage.StorageError as e:
        raise HTTPException(500, f"storage error: {e}")
    try:
        r = scanner.scan_file(backend, rel, force=True)
    except Exception as e:
        logger.warning("rescan failed movie_id=%s rel=%s: %s", movie_id, rel, e)
        raise HTTPException(502, f"rescan failed: {e}")
    cur = store.get_movie(movie_id) or {}
    return {"id": movie_id, **r,
            "tmdb_id": cur.get("tmdb_id"), "title": cur.get("title"),
            "year": cur.get("year")}


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


@router.post("/movies/batch-delete")
def batch_delete_movies(body: dict | None = None):
    """整片删除（海报墙多选）：{ids[], dry_run, confirm}。

    dry_run 默认 true：返回每部片标题/年份/版本文件/附属文件/字节数，
    须 confirm:true 才执行。执行删磁盘文件 + 版本库行 + extras 库行
    （海报/tmdb_cache 保留）；独占目录整树删，共享目录仅删本片相关。"""
    from ..files import _cleanup_old_dir, _resync_old_dir
    body = body or {}
    raw = body.get("ids") or []
    try:
        ids = sorted({int(x) for x in raw})
    except (TypeError, ValueError):
        raise HTTPException(422, "ids must be int list")
    if not ids:
        raise HTTPException(422, "ids required")
    if len(ids) > 100:
        raise HTTPException(422, "too many ids (max 100)")
    dry_run = body.get("dry_run", True)
    confirm = bool(body.get("confirm", False))
    # 海报粒度展开：有 tmdb_id 的代表行展开到同 tmdb 全版本所在影片
    try:
        expanded = store.expand_ids_to_versions(ids)
    except Exception:
        expanded = {i: [i] for i in ids}
    cover: dict[int, int] = {}  # version_id -> rep_id
    for rep, vers in (expanded or {}).items():
        for v in vers or []:
            cover.setdefault(int(v), int(rep))
    for i in ids:
        cover.setdefault(i, i)
    rep_ids = sorted(set(cover.values()))
    plans = []
    for rep in rep_ids:
        m = store.get_movie(rep)
        if not m:
            plans.append({"id": rep, "status": "not_found", "files": [],
                          "file_count": 0, "total_size": 0})
            continue
        try:
            scope = _movie_delete_scope(rep)
        except HTTPException:
            plans.append({"id": rep, "status": "not_found", "files": [],
                          "file_count": 0, "total_size": 0})
            continue
        # 同 tmdb 其他版本若在别处，一并纳入（以版本行为准逐个 scope）
        extra_vids = [v for v in cover if v not in scope["version_ids"]
                      and (store.get_movie(v) or {}).get("tmdb_id")
                      and m.get("tmdb_id")
                      and (store.get_movie(v) or {}).get("tmdb_id") == m.get("tmdb_id")]
        seen = {f["rel"] for f in scope["files"]}
        for v in extra_vids:
            try:
                s2 = _movie_delete_scope(v)
            except HTTPException:
                continue
            scope["version_ids"] += [x for x in s2["version_ids"]
                                     if x not in scope["version_ids"]]
            for f in s2["files"]:
                if f["rel"] not in seen:
                    seen.add(f["rel"])
                    scope["files"].append(f)
                    scope["total_size"] += f["size"]
        scope["files"].sort(key=lambda x: x["rel"])
        n_feat = sum(1 for f in scope["files"] if f["kind"] == "feature")
        plans.append({"id": rep, "title": m.get("title", ""),
                      "year": m.get("year"), "tmdb_id": m.get("tmdb_id"),
                      "library_id": m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID,
                      "exclusive": scope["exclusive"],
                      "version_ids": sorted(set(scope["version_ids"])),
                      "files": scope["files"],
                      "file_count": len(scope["files"]),
                      "feature_count": n_feat,
                      "extra_count": len(scope["files"]) - n_feat,
                      "total_size": scope["total_size"],
                      "status": "planned"})
    tv = sum(p.get("feature_count", 0) for p in plans)
    tf = sum(p.get("file_count", 0) for p in plans)
    tb = sum(p.get("total_size", 0) for p in plans)
    base = {"total_movies": len(plans), "total_versions": tv,
            "total_files": tf, "total_bytes": tb,
            "hint": "将永久删除磁盘文件与库记录（海报/镜像缓存保留），不可恢复"}
    if dry_run or not confirm:
        return {**base, "dry_run": True, "plans": plans}
    from ..files import _require_writable as _rw
    for lid in sorted({int(p.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
                       for p in plans if p.get("files")}):
        _rw(lid)
    results = []
    for p in plans:
        if p.get("status") == "not_found" or not p.get("files"):
            results.append({**p, "status": p.get("status") or "skipped_empty"})
            continue
        # 先删库、后删盘（评审 B7/R05-B3）：盘删失败最坏留孤儿文件（重扫可再认领），
        # 反之先删盘会让海报墙挂死行；失败逐条上报而不是静默
        db_errors = []
        for vid in p.get("version_ids", []):
            try:
                for e in store.list_extras_by_movie(vid):
                    try:
                        store.delete_extra_by_path(e["file_path"])
                    except Exception as ex:
                        db_errors.append(f"extra {e.get('id')}: {ex}")
                store.delete_movie(vid)
            except Exception as ex:
                db_errors.append(f"version {vid}: {ex}")
        touched_dirs: set[str] = set()
        ok, missing, failed = 0, 0, []
        for f in p["files"]:
            abs_p = library_paths.resolve(
                p.get("library_id") or library_paths.DEFAULT_LIBRARY_ID, f["rel"])
            touched_dirs.add(os.path.dirname(abs_p))
            if not os.path.lexists(abs_p):
                missing += 1
                continue
            try:
                if os.path.isfile(abs_p):
                    os.remove(abs_p)
                    ok += 1
            except OSError as ex:
                failed.append({"rel": f["rel"], "error": str(ex)})
        for d in touched_dirs:
            _cleanup_old_dir(d)
            _resync_old_dir(d)
        status = "deleted" if (not db_errors and not failed) else "deleted_with_errors"
        results.append({**p, "status": status,
                        "deleted_files": ok, "missing_files": missing,
                        "failed_files": failed, "db_errors": db_errors})
    ok_m = sum(1 for r in results if str(r.get("status", "")).startswith("deleted"))
    return {**base, "dry_run": False, "deleted_movies": ok_m,
            "results": results}


@router.get("/movies/{movie_id}/poster-orig")
def movie_poster_orig(movie_id: int):
    """海报原图（按需缓存）：tmdb_cache.poster_tmdb_path → original 尺寸落盘
    posters/<tmdb_id>_orig.jpg（走 TMDB_PROXY，已存在直接复用），FileResponse 返回。
    无 tmdb_id/远端路径/下载失败时 4xx/5xx，前端回退本地 w500 图。删 *_orig.jpg
    即清缓存，下次点击自动重下。"""
    from fastapi.responses import FileResponse
    from .. import tmdb as _tmdb
    from ...db import POSTER_DIR
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    tid = m.get("tmdb_id")
    if not tid:
        raise HTTPException(404, "movie has no tmdb_id")
    try:
        cached = store.get_tmdb_cached(int(tid))
    except Exception:
        cached = None
    remote = ((cached or {}).get("poster_tmdb_path") or "").strip()
    if not remote:
        raise HTTPException(404, "no poster path in cache")
    dest = os.path.join(POSTER_DIR, f"{int(tid)}_orig.jpg")
    if not os.path.isfile(dest):
        try:
            ok = _tmdb.download_poster(remote, dest, size="original")
        except Exception:
            ok = False
        if not ok or not os.path.isfile(dest):
            raise HTTPException(502, "original poster download failed")
    return FileResponse(dest, filename=os.path.basename(dest))

