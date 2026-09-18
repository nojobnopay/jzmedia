"""store.items（F 阶段）：播放/字幕链路统一取行（movie|episode）。

所有 `(kind, item_id)` 复合键（media_info/playback_progress/转码目录）都由本模块
提供一致的行结构，stream 路由据此不再区分电影/剧集。
"""
from ._base import _conn, _lock
from .movies import get_movie
from .tv import get_episode, get_show

__all__ = ['get_playable', 'normalize_kind']


def normalize_kind(kind) -> str:
    return "episode" if str(kind or "movie").strip().lower() in ("episode", "tv", "e") \
        else "movie"


def get_playable(kind, item_id: int) -> dict | None:
    """播放行：电影行原样；剧集行附加 kind/show_title/display_title。"""
    k = normalize_kind(kind)
    try:
        iid = int(item_id)
    except (TypeError, ValueError):
        return None
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
