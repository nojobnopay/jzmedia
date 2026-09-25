"""store.items（F 阶段）：播放/字幕链路统一取行（movie|episode|extra）。

所有 `(kind, item_id)` 复合键（media_info/playback_progress/转码目录）都由本模块
提供一致的行结构，stream 路由据此不再区分电影/剧集/花絮（T4 加 extra）。
"""
import os

from ._base import _conn, _lock
from .extras import get_extra
from .movies import get_movie
from .tv import get_episode, get_show

__all__ = ['get_playable', 'normalize_kind', 'EXTRA_LABELS', 'preview_items']

# 花絮子类型 → 中文标签（前端也可用 /api/tv/shows 返回的 kind）
EXTRA_LABELS = {"movie": "剧场版", "trailer": "预告", "behindthescenes": "幕后",
                "deleted": "删减片段", "featurette": "特辑", "interview": "访谈",
                "scene": "片段", "short": "短片", "other": "其他", "extra": "花絮",
                "sample": "样片"}


def normalize_kind(kind) -> str:
    k = str(kind or "movie").strip().lower()
    if k in ("episode", "tv", "e"):
        return "episode"
    if k in ("extra", "x"):
        return "extra"
    return "movie"


def get_playable(kind, item_id: int) -> dict | None:
    """播放行：电影行原样；剧集/花絮行附加 kind/show_title/display_title。"""
    k = normalize_kind(kind)
    try:
        iid = int(item_id)
    except (TypeError, ValueError):
        return None
    if k == "extra":
        x = get_extra(iid)
        if not x:
            return None
        show = get_show(x.get("show_id")) if x.get("show_id") else None
        stem = os.path.splitext(os.path.basename(x.get("file_path") or ""))[0]
        sub = str(x.get("kind") or "extra")
        label = EXTRA_LABELS.get(sub, "花絮")
        out = dict(x)
        out["kind"] = "extra"
        out["extra_kind"] = sub
        out["show_title"] = (show or {}).get("title", "")
        out["title"] = stem or label
        out["display_title"] = (f"{out['show_title']} · {label}"
                                + (f" · {stem}" if stem else ""))
        return out
    if k == "episode":
        e = get_episode(iid)
        if not e:
            return None
        show = get_show(e.get("show_id")) or {}
        season = int(e.get("season") or 0)
        ep = int(e.get("episode") or 0)
        out = dict(e)
        out["kind"] = "episode"
        out["show_title"] = show.get("title", "")
        out["title"] = e.get("title") or f"S{season:02d}E{ep:02d}"
        out["display_title"] = (f"{show.get('title', '')} S{season:02d}E{ep:02d}"
                                + (f" · {e.get('title')}" if e.get("title") else ""))
        return out
    m = get_movie(iid)
    if not m:
        return None
    out = dict(m)
    out["kind"] = "movie"
    return out


def preview_items(library_id: int) -> list[tuple[str, int]]:
    """仅返回指定视频库的可播放项，预览批任务不跨库。"""
    with _lock, _conn() as c:
        rows = c.execute("SELECT 'movie' AS kind, id FROM movies WHERE library_id=? "
                         "UNION ALL SELECT 'episode', id FROM tv_episodes WHERE library_id=? "
                         "UNION ALL SELECT 'extra', id FROM extras WHERE library_id=?",
                         (int(library_id),) * 3).fetchall()
        return [(r['kind'], int(r['id'])) for r in rows]
