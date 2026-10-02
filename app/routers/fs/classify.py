"""routers.fs.classify（自 app/routers/fs.py 拆分，评审 R01-Q4；经 fs 门面使用）。"""
import os

from ... import library_paths
from ... import store
from ...scanner import SUBTITLE_EXTS, is_feature_video, is_sidecar
__all__ = ['_classify', '_impact_for_delete']


def _classify(rel: str, extras_map: dict | None = None,
              library_id=None, entry: dict | None = None,
              backend=None) -> dict:
    """单文件定性：feature / sidecar / subtitle / nfo / other，附 DB 行提示。
    extras_map（{path: extra}）由列表口一次性传入（评审 B8/R08-B1：避免每文件全表扫 extras）。
    `library_id` 缺省=默认库（多库 v12：行/花絮/路径均限定在该库）。
    `entry`（目录列举项，带 size/mtime）与 `backend` 由远程直读列表口传入：
    避免再走 POSIX stat（直读库 size 不再为 0），通用目录花絮判定也不再退化。"""
    lid = library_paths.default_id() if library_id is None else int(library_id)
    if entry is not None:
        size, mtime = int(entry.get("size") or 0), int(entry.get("mtime") or 0)
    else:
        abs_p = library_paths.resolve(lid, rel)
        size, mtime = 0, 0
        try:
            st = os.stat(abs_p)
            size, mtime = st.st_size, int(st.st_mtime)
        except OSError:
            pass
    base = {"rel": rel, "name": os.path.basename(rel),
            "size": size, "mtime": mtime, "registered": False, "match_status": None}
    m = store.get_by_path(rel, library_id=lid)
    if m:
        vers = []
        try:
            full = store.get_movie(m["id"]) or {}
            vers = full.get("versions") or []
        except Exception:
            vers = []
        return {**base, "kind": "feature", "movie_id": m["id"],
                "registered": True, "match_status": "matched" if m.get('tmdb_id') or m.get('match_source') in ('nfo', 'wikidata', 'tvmaze', 'bgm', 'douban', 'imdb') else "unmatched",
                "title": m.get("title", ""), "year": m.get("year"),
                "tmdb_id": m.get("tmdb_id"),
                "version_count": len(vers) or 1}
    # TV 集行：双击进剧详情（fs 浏览器剧库内正片跳转，T3）
    try:
        ep = store.get_episode_by_path(rel, library_id=lid)
    except Exception:
        ep = None
    if ep:
        show = store.get_show_meta(ep.get("show_id")) or {}
        return {**base, "kind": "feature", "episode_id": ep["id"],
                "registered": True, "match_status": "matched" if show.get('tmdb_id') or show.get('match_source') in ('nfo', 'wikidata', 'tvmaze', 'bgm', 'douban', 'imdb') else "unmatched",
                "show_id": ep["show_id"], "movie_id": None,
                "title": show.get("title") or "", "year": show.get("year"),
                "season": ep.get("season"), "episode": ep.get("episode"),
                "tmdb_id": show.get("tmdb_id"), "version_count": 1}
    if extras_map is not None:
        e = extras_map.get(rel)
        if e:
            return {**base, "kind": "sidecar",
                    "registered": True, "extra_id": e["id"], "movie_id": e.get("movie_id"), "show_id": e.get('show_id')}
    else:
        for e in store.list_all_extras():
            if e["file_path"] == rel and int(e.get('library_id') or 0) == lid:
                return {**base, "kind": "sidecar",
                        "registered": True, "extra_id": e["id"], "movie_id": e.get("movie_id"), "show_id": e.get('show_id')}
    _, ex = os.path.splitext(rel)
    ex = ex.lower()
    if is_sidecar(rel, library_id=lid, backend=backend):
        return {**base, "kind": "sidecar"}
    if ex in SUBTITLE_EXTS:
        return {**base, "kind": "subtitle"}
    if ex == ".nfo":
        return {**base, "kind": "nfo"}
    if is_feature_video(rel, library_id=lid, backend=backend):
        # 库无行但形态是正片（多为未扫描）：按 feature 对待，删时提醒
        return {**base, "kind": "feature", "movie_id": None,
                "match_status": "unregistered",
                "version_count": 1}
    return {**base, "kind": "other"}


def _impact_for_delete(rel: str, library_id=None, backend=None) -> dict:
    """删除预览：正片 requires_confirm=True 并带影响面，其余直接可删。"""
    info = _classify(rel, library_id=library_id, backend=backend)
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

