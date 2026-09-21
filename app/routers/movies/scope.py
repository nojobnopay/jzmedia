"""routers.movies.scope（自 app/routers/movies.py 拆分，评审 B9/R05-Q1；经 movies 门面使用）。"""
import os
from fastapi import HTTPException
from ... import library_paths, storage, store
from ...log import get_logger
logger = get_logger("movies.scope")
from ...scanner import same_stem
__all__ = ['_movie_delete_scope']


def _classify(rel: str, name: str, own_paths: set, extra_set: set) -> str:
    if rel in own_paths:
        return "feature"
    if rel in extra_set:
        return "sidecar"
    from ...scanner import SUBTITLE_EXTS
    ex = os.path.splitext(name)[1].lower()
    if ex in SUBTITLE_EXTS:
        return "subtitle"
    if ex == ".nfo":
        return "nfo"
    return "other"


def _movie_delete_scope(movie_id: int, backend=None) -> dict:
    """整片删除范围：返回 {movie, version_ids, files[{rel,size,kind}], total_size}。

    - 独占目录：整棵目录树全部文件。
    - 共享目录：本片版本文件 + 同茎跟随（不含 movie.nfo）+ 已归属花絮文件。
    海报/tmdb_cache 不在此列（delete_movie 语义保留）。
    远程直读库经 StorageBackend 遍历（无挂载依赖）；backend 缺省按库解析。"""
    from ...scanner import VIDEO_EXTS, is_extra, is_sample
    m = store.get_movie(movie_id)
    if not m:
        raise HTTPException(404, f"movie {movie_id} not found")
    versions = [v.get("file_path", "") for v in (m.get("versions") or [])]
    if m["file_path"] not in versions:
        versions.append(m["file_path"])
    version_ids = [v.get("id") for v in (m.get("versions") or []) if v.get("id")]
    if m["id"] not in version_ids:
        version_ids.append(m["id"])
    own_paths = set(versions)
    own_stems = {os.path.splitext(os.path.basename(p))[0] for p in versions}
    rel_dir = os.path.dirname(m["file_path"])
    lib_id = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID

    def _same_stem(stem: str) -> bool:
        return same_stem(stem, own_stems)   # 单源（评审 B9/R05-B4）

    try:
        extra_rows = store.list_extras_by_movie(movie_id)
    except Exception:
        extra_rows = []
    extra_set = {e["file_path"] for e in extra_rows}

    if backend is None:
        try:
            backend = storage.backend_for(lib_id)
        except storage.StorageError as e:
            logger.debug("delete scope backend unavailable lib=%s: %s", lib_id, e)
            backend = None
    local_dir = backend.abs_path(rel_dir) if backend is not None else None
    if backend is not None and local_dir is None:
        return _remote_scope(m, backend, rel_dir, versions, version_ids,
                             own_paths, extra_set, _same_stem, is_extra,
                             is_sample, VIDEO_EXTS)

    lib_root = library_paths.library_root(lib_id)
    movie_dir = local_dir if local_dir is not None else (
        os.path.join(lib_root, rel_dir) if rel_dir else lib_root)

    # 共享判定：目录顶层存在不属于本片的正片视频
    foreign = False
    try:
        top = sorted(os.listdir(movie_dir)) if os.path.isdir(movie_dir) else []
    except OSError:
        top = []
    for n in top:
        full = os.path.join(movie_dir, n)
        if not os.path.isfile(full):
            continue
        rel = os.path.join(rel_dir, n) if rel_dir else n
        _, ex = os.path.splitext(n)
        if ex.lower() in VIDEO_EXTS and not is_sample(n) \
                and not is_extra(rel, library_id=lib_id) and rel not in own_paths:
            foreign = True
            break
    rels: dict[str, str] = {}  # rel -> kind
    if not foreign and os.path.isdir(movie_dir):
        for root, _, files in os.walk(movie_dir):
            for fn in sorted(files):
                if fn.startswith("."):
                    continue
                full = os.path.join(root, fn)
                try:
                    rel = os.path.relpath(full, lib_root)
                except ValueError:
                    continue
                rels[rel] = _classify(rel, fn, own_paths, extra_set)
    else:
        for p in versions:
            rels[p] = "feature"
        if os.path.isdir(movie_dir):
            for n in top:
                full = os.path.join(movie_dir, n)
                if not os.path.isfile(full):
                    continue
                rel = os.path.join(rel_dir, n) if rel_dir else n
                if rel in rels or n == "movie.nfo":
                    continue
                if _same_stem(os.path.splitext(n)[0]):
                    kind = _classify(rel, n, own_paths, extra_set)
                    rels[rel] = "sidecar" if kind == "other" else kind
    for e in extra_set:
        if e not in rels and os.path.isfile(library_paths.resolve(lib_id, e)):
            rels[e] = "sidecar"
    files, total = [], 0
    for rel in sorted(rels):
        try:
            size = os.path.getsize(library_paths.resolve(lib_id, rel))
        except OSError:
            size = 0
        total += size
        files.append({"rel": rel, "size": size, "kind": rels[rel]})
    return {"movie": m, "version_ids": sorted(set(version_ids)),
            "exclusive": not foreign, "files": files, "total_size": total}


def _remote_scope(m, backend, rel_dir, versions, version_ids, own_paths,
                  extra_set, _same_stem, is_extra, is_sample, video_exts) -> dict:
    """远程直读分支：一次 list 判共享 + 一次 iter_tree 收整棵子树（不逐文件 stat）。"""
    lib_id = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    empty = {"movie": m, "version_ids": sorted(set(version_ids)),
             "exclusive": False, "files": [], "total_size": 0}
    try:
        top_entries = backend.list(rel_dir)
    except storage.StorageNotFound:
        top_entries = []
    except storage.StorageError as e:
        # 列目录失败（离线/权限）：不猜范围，返回空计划（调用方 skipped_empty，不删行）
        logger.debug("delete scope list failed lib=%s rel=%s: %s", lib_id, rel_dir, e)
        return empty
    foreign = False
    for e in top_entries:
        if e.get("is_dir"):
            continue
        n = e["name"]
        rel = f"{rel_dir}/{n}" if rel_dir else n
        _, ex = os.path.splitext(n)
        if ex.lower() in video_exts and not is_sample(n) \
                and not is_extra(rel, library_id=lib_id, backend=backend) \
                and rel not in own_paths:
            foreign = True
            break
    rels: dict[str, str] = {}
    sizes: dict[str, int] = {}
    if not foreign:
        try:
            for we in backend.iter_tree(rel_dir):
                if we.is_dir or we.name.startswith("."):
                    continue
                rels[we.rel] = _classify(we.rel, we.name, own_paths, extra_set)
                sizes[we.rel] = int(we.size)
        except storage.StorageError as e:
            # 遍历中途失败：丢弃部分结果，绝不按“半个目录”删
            logger.debug("delete scope tree failed lib=%s rel=%s: %s", lib_id, rel_dir, e)
            rels, sizes = {}, {}
    else:
        for p in versions:
            rels[p] = "feature"
        for e in top_entries:
            if e.get("is_dir"):
                continue
            n = e["name"]
            rel = f"{rel_dir}/{n}" if rel_dir else n
            if rel in rels or n == "movie.nfo":
                continue
            if _same_stem(os.path.splitext(n)[0]):
                kind = _classify(rel, n, own_paths, extra_set)
                rels[rel] = "sidecar" if kind == "other" else kind
                sizes[rel] = int(e.get("size") or 0)
    for e in extra_set:
        if e in rels:
            continue
        try:
            st = backend.stat(e)
        except storage.StorageError:
            continue
        if not st.is_dir:
            rels[e] = "sidecar"
            sizes[e] = int(st.size)
    files, total = [], 0
    for rel in sorted(rels):
        size = int(sizes.get(rel) or 0)
        total += size
        files.append({"rel": rel, "size": size, "kind": rels[rel]})
    return {"movie": m, "version_ids": sorted(set(version_ids)),
            "exclusive": not foreign, "files": files, "total_size": total}
