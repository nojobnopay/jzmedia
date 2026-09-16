"""routers.movies.scope（自 app/routers/movies.py 拆分，评审 B9/R05-Q1；经 movies 门面使用）。"""
import os
from fastapi import HTTPException
from ... import store
from ...config import settings
from ...log import get_logger
logger = get_logger("movies.scope")
from ...scanner import same_stem
__all__ = ['_movie_delete_scope']

def _movie_delete_scope(movie_id: int) -> dict:
    """整片删除范围：返回 {movie, version_ids, files[{rel,size,kind}], total_size}。

    - 独占目录：整棵目录树全部文件。
    - 共享目录：本片版本文件 + 同茎跟随（不含 movie.nfo）+ 已归属花絮文件。
    海报/tmdb_cache 不在此列（delete_movie 语义保留）。"""
    from ...scanner import (SUBTITLE_EXTS, VIDEO_EXTS, is_extra, is_sample)
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
    movie_dir = os.path.join(settings.media_root, rel_dir) if rel_dir \
        else settings.media_root

    def _same_stem(stem: str) -> bool:
        return same_stem(stem, own_stems)   # 单源（评审 B9/R05-B4）

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
                and not is_extra(rel) and rel not in own_paths:
            foreign = True
            break
    try:
        extra_rows = store.list_extras_by_movie(movie_id)
    except Exception:
        extra_rows = []
    extra_set = {e["file_path"] for e in extra_rows}
    rels: dict[str, str] = {}  # rel -> kind
    if not foreign and os.path.isdir(movie_dir):
        for root, _, files in os.walk(movie_dir):
            for fn in sorted(files):
                if fn.startswith("."):
                    continue
                full = os.path.join(root, fn)
                try:
                    rel = os.path.relpath(full, settings.media_root)
                except ValueError:
                    continue
                if rel in own_paths:
                    rels[rel] = "feature"
                elif rel in extra_set:
                    rels[rel] = "sidecar"
                else:
                    _, ex = os.path.splitext(fn)
                    ex = ex.lower()
                    if ex in SUBTITLE_EXTS:
                        rels[rel] = "subtitle"
                    elif ex == ".nfo":
                        rels[rel] = "nfo"
                    else:
                        rels[rel] = "other"
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
                    _, ex = os.path.splitext(n)
                    ex = ex.lower()
                    if ex in SUBTITLE_EXTS:
                        rels[rel] = "subtitle"
                    elif ex == ".nfo":
                        rels[rel] = "nfo"
                    else:
                        rels[rel] = "sidecar"
    for e in extra_set:
        if e not in rels and os.path.isfile(os.path.join(settings.media_root, e)):
            rels[e] = "sidecar"
    files, total = [], 0
    for rel in sorted(rels):
        try:
            size = os.path.getsize(os.path.join(settings.media_root, rel))
        except OSError:
            size = 0
        total += size
        files.append({"rel": rel, "size": size, "kind": rels[rel]})
    return {"movie": m, "version_ids": sorted(set(version_ids)),
            "exclusive": not foreign, "files": files, "total_size": total}

