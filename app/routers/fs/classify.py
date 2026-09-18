"""routers.fs.classify（自 app/routers/fs.py 拆分，评审 R01-Q4；经 fs 门面使用）。"""
import os

from ... import library_paths
from ... import store
from ...scanner import SUBTITLE_EXTS, is_feature_video, is_sidecar
__all__ = ['_classify', '_impact_for_delete']


def _classify(rel: str, extras_map: dict | None = None) -> dict:
    """单文件定性：feature / sidecar / subtitle / nfo / other，附 DB 行提示。
    extras_map（{path: extra}）由列表口一次性传入（评审 B8/R08-B1：避免每文件全表扫 extras）。"""
    abs_p = library_paths.abs_path(rel)
    size, mtime = 0, 0
    try:
        st = os.stat(abs_p)
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        pass
    base = {"rel": rel, "name": os.path.basename(rel),
            "size": size, "mtime": mtime}
    m = store.get_by_path(rel)
    if m:
        vers = []
        try:
            full = store.get_movie(m["id"]) or {}
            vers = full.get("versions") or []
        except Exception:
            vers = []
        return {**base, "kind": "feature", "movie_id": m["id"],
                "title": m.get("title", ""), "year": m.get("year"),
                "tmdb_id": m.get("tmdb_id"),
                "version_count": len(vers) or 1}
    if extras_map is not None:
        e = extras_map.get(rel)
        if e:
            return {**base, "kind": "sidecar",
                    "extra_id": e["id"], "movie_id": e.get("movie_id")}
    else:
        for e in store.list_all_extras():
            if e["file_path"] == rel:
                return {**base, "kind": "sidecar",
                        "extra_id": e["id"], "movie_id": e.get("movie_id")}
    _, ex = os.path.splitext(rel)
    ex = ex.lower()
    if is_sidecar(rel):
        return {**base, "kind": "sidecar"}
    if ex in SUBTITLE_EXTS:
        return {**base, "kind": "subtitle"}
    if ex == ".nfo":
        return {**base, "kind": "nfo"}
    if is_feature_video(rel):
        # 库无行但形态是正片（多为未扫描）：按 feature 对待，删时提醒
        return {**base, "kind": "feature", "movie_id": None,
                "version_count": 1}
    return {**base, "kind": "other"}


def _impact_for_delete(rel: str) -> dict:
    """删除预览：正片 requires_confirm=True 并带影响面，其余直接可删。"""
    info = _classify(rel)
    if info["kind"] == "feature":
        attached = 0
        if info.get("movie_id"):
            try:
                attached = len(store.list_extras_by_movie(info["movie_id"]))
            except Exception:
                attached = 0
        return {**info, "requires_confirm": True,
                "attached_extras": attached,
                "warn": "正片文件：删除后将从海报墙移除，关联与索引一并清理，海报/镜像缓存保留"}
    return {**info, "requires_confirm": False}


