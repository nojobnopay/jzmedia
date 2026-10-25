"""store.items（F 阶段）：播放/字幕链路统一取行（movie|episode|extra）。

所有 `(kind, item_id)` 复合键（media_info/playback_progress/转码目录）都由本模块
提供一致的行结构，stream 路由据此不再区分电影/剧集/花絮（T4 加 extra）。
"""
import os

from ._base import _conn, _lock
from .extras import get_extra
from .movies import get_movie
from .tv import get_episode, get_show

__all__ = ['get_playable', 'get_preview_playables', 'normalize_kind', 'EXTRA_LABELS', 'preview_items']

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
        return _preview_playable(k, x, (show or {}).get("title", ""))
    if k == "episode":
        e = get_episode(iid)
        if not e:
            return None
        show = get_show(e.get("show_id")) or {}
        return _preview_playable(k, e, show.get("title", ""))
    m = get_movie(iid)
    if not m:
        return None
    out = dict(m)
    out["kind"] = "movie"
    return out


def _preview_playable(kind, row, show_title=""):
    out = dict(row)
    out["kind"] = kind
    if kind == "extra":
        stem = os.path.splitext(os.path.basename(row.get("file_path") or ""))[0]
        sub = str(row.get("kind") or "extra")
        label = EXTRA_LABELS.get(sub, "花絮")
        out.update(extra_kind=sub, show_title=show_title, title=stem or label,
                   display_title=f"{show_title} · {label}" + (f" · {stem}" if stem else ""))
    elif kind == "episode":
        season, ep = int(row.get("season") or 0), int(row.get("episode") or 0)
        out.update(show_title=show_title, title=row.get("title") or f"S{season:02d}E{ep:02d}",
                   display_title=f"{show_title} S{season:02d}E{ep:02d}"
                   + (f" · {row['title']}" if row.get("title") else ""))
    return out


def get_preview_playables(refs, library_id=None) -> list[dict]:
    """批量读取缩略图候选行及展示名；不加载演职员、版本或整剧分集详情。"""
    grouped = {kind: set() for kind in ("movie", "episode", "extra")}
    for kind, item_id in refs:
        try:
            item_id = int(item_id)
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 < item_id < 2 ** 63:
            grouped[normalize_kind(kind)].add(item_id)
    if not any(grouped.values()):
        return []
    out = []
    with _lock, _conn() as c:
        for kind, table in (("movie", "movies"), ("episode", "tv_episodes"), ("extra", "extras")):
            ids = sorted(grouped[kind])
            for start in range(0, len(ids), 400):
                chunk = ids[start:start + 400]
                query = f"SELECT item.* FROM {table} AS item"
                if kind != "movie":
                    query = ("SELECT item.*, CASE WHEN series.id IS NULL THEN '' ELSE series.title END "
                             "AS preview_show_title "
                             f"FROM {table} AS item LEFT JOIN tv_shows AS series ON series.id=item.show_id")
                query += " WHERE item.id IN (" + ",".join("?" for _ in chunk) + ")"
                params = chunk
                if library_id is not None:
                    query += " AND item.library_id=?"
                    params = [*chunk, int(library_id)]
                for raw in c.execute(query, params):
                    row = dict(raw)
                    show_title = row.pop("preview_show_title", "")
                    out.append(_preview_playable(kind, row, show_title))
    return out


def preview_items(library_id: int) -> list[tuple[str, int]]:
    """仅返回指定视频库的可播放项，预览批任务不跨库。"""
    with _lock, _conn() as c:
        rows = c.execute("SELECT 'movie' AS kind, id FROM movies WHERE library_id=? "
                         "UNION ALL SELECT 'episode', id FROM tv_episodes WHERE library_id=? "
                         "UNION ALL SELECT 'extra', id FROM extras WHERE library_id=?",
                         (int(library_id),) * 3).fetchall()
        return [(r['kind'], int(r['id'])) for r in rows]
