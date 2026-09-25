"""同目录 NFO 导入（离线元数据来源之一）：Kodi `<movie>` / `<tvshow>` / `<episodedetails>`。

扫描时若 TMDB 不可用，可先用 NFO 匹配；进一步地（Phase 1）离线时用 NFO 里的
完整字段直接建行（标题/年份/简介/评分/类型/产地/演职员/`<uniqueid>`），保证
断网/无 Token 场景仍能恢复元数据与匹配。
本地走 POSIX；远程直读库经 StorageBackend 读 NFO 字节（`*_for_backend`）。
"""
import os
import xml.etree.ElementTree as ET

from .base import Candidate

__all__ = ['candidates_for', 'candidates_for_backend', 'read_nfo', 'read_nfo_any',
           'parse_movie_bytes', 'parse_tvshow_bytes', 'parse_episode_bytes',
           'nfo_names_for', 'art_names_for']

_NFO_NAMES = ("movie.nfo",)


def _text(root, tag: str) -> str:
    return (root.findtext(tag) or "").strip()


def _float(v):
    try:
        f = float(str(v or "").strip())
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(str(v or "").strip()[:4])
    except (TypeError, ValueError):
        return None


def _unique_ids(root) -> tuple[int | None, str, int | None]:
    tmdb_id, imdb_id, tvdb_id = None, "", None
    for u in root.findall("uniqueid"):
        t = (u.get("type") or "").strip().lower()
        v = (u.text or "").strip()
        if t == "tmdb" and v.isdigit():
            tmdb_id = int(v)
        elif t == "imdb":
            imdb_id = v
        elif t == "tvdb" and v.isdigit():
            tvdb_id = int(v)
    return tmdb_id, imdb_id, tvdb_id


def _rating_of(root) -> float | None:
    r = _float(_text(root, "rating"))
    if r:
        return r
    for node in root.findall("./ratings/rating"):
        v = _float(node.findtext("value"))
        if v:
            return v
    return None


def _people_of(root) -> tuple[list[str], list[dict]]:
    directors = [str(d.text or "").strip() for d in root.findall("director")
                 if str(d.text or "").strip()]
    actors: list[dict] = []
    for a in root.findall("actor"):
        name = (a.findtext("name") or "").strip()
        if not name:
            continue
        actors.append({"name": name, "character": (a.findtext("role") or "").strip(),
                       "order": _int_order(a.findtext("order"))})
    return directors, actors


def _int_order(v):
    try:
        return int(str(v or "").strip() or 99)
    except (TypeError, ValueError):
        return 99


def parse_movie_bytes(data: bytes) -> dict | None:
    """`<movie>` NFO → 完整元数据 dict；不是 movie 根/无有效内容返回 None。

    返回键：title/original_title/year/overview/rating/douban_rating/custom_rating/
    genres/countries(ISO)/tags/studios/premiered/tagline/runtime/collection_name/
    directors/actors/tmdb_id/imdb_id。"""
    try:
        root = ET.fromstring(data)
    except Exception:
        return None
    if str(root.tag or "").lower() != "movie":
        return None
    title = _text(root, "title")
    tmdb_id, imdb_id, _tv = _unique_ids(root)
    if not title and not tmdb_id:
        return None
    from ..regions import name_to_country
    countries = []
    for c in root.findall("country"):
        code = name_to_country(c.text or "")
        if code and code not in countries:
            countries.append(code)
    genres = [str(g.text or "").strip() for g in root.findall("genre")
              if str(g.text or "").strip()]
    tags = [str(t.text or "").strip() for t in root.findall("tag")
            if str(t.text or "").strip()]
    studios = [str(s.text or "").strip() for s in root.findall("studio")
               if str(s.text or "").strip()]
    directors, actors = _people_of(root)
    return {
        "title": title,
        "original_title": _text(root, "originaltitle") or title,
        "year": _int(_text(root, "year") or _text(root, "premiered")),
        "overview": _text(root, "plot") or _text(root, "outline"),
        "rating": _rating_of(root),
        "douban_rating": _float(_text(root, "douban_rating")),
        "custom_rating": _float(_text(root, "customrating")),
        "genres": genres,
        "countries": countries,
        "tags": tags,
        "studios": studios,
        "premiered": _text(root, "premiered"),
        "tagline": _text(root, "tagline"),
        "runtime": _int_full(_text(root, "runtime")),
        "collection_name": _text(root.find("set"), "name") if root.find("set") is not None else "",
        "directors": directors,
        "actors": actors,
        "tmdb_id": tmdb_id,
        "imdb_id": imdb_id,
    }


def _int_full(v) -> int:
    try:
        return max(0, int(str(v or "").strip()))
    except (TypeError, ValueError):
        return 0


def parse_tvshow_bytes(data: bytes) -> dict | None:
    """`<tvshow>` NFO → 剧级元数据（字段形态与 movie 解析一致）。"""
    try:
        root = ET.fromstring(data)
    except Exception:
        return None
    if str(root.tag or "").lower() != "tvshow":
        return None
    title = _text(root, "title")
    tmdb_id, imdb_id, tvdb_id = _unique_ids(root)
    if not title and not tmdb_id:
        return None
    from ..regions import name_to_country
    countries = []
    for c in root.findall("country"):
        code = name_to_country(c.text or "")
        if code and code not in countries:
            countries.append(code)
    genres = [str(g.text or "").strip() for g in root.findall("genre")
              if str(g.text or "").strip()]
    studios = [str(s.text or "").strip() for s in root.findall("studio")
               if str(s.text or "").strip()]
    directors, actors = _people_of(root)
    return {
        "title": title,
        "original_title": _text(root, "originaltitle") or title,
        "year": _int(_text(root, "year") or _text(root, "premiered")),
        "overview": _text(root, "plot"),
        "rating": _rating_of(root),
        "genres": genres,
        "countries": countries,
        "studios": studios,
        "status": _text(root, "status"),
        "premiered": _text(root, "premiered"),
        "runtime": _int_full(_text(root, "runtime")),
        "directors": directors,
        "actors": actors,
        "tmdb_id": tmdb_id,
        "imdb_id": imdb_id,
        "tvdb_id": tvdb_id,
    }


def parse_episode_bytes(data: bytes) -> dict | None:
    """`<episodedetails>` NFO → 集级元数据（用于离线补集名/简介/播出日）。"""
    try:
        root = ET.fromstring(data)
    except Exception:
        return None
    if str(root.tag or "").lower() != "episodedetails":
        return None
    title = _text(root, "title")
    season = _int_full(_text(root, "season"))
    episode = _int_full(_text(root, "episode"))
    if not title and not episode:
        return None
    return {"title": title, "overview": _text(root, "plot"),
            "season": season, "episode": episode,
            "air_date": _text(root, "aired"), "rating": _rating_of(root)}


def candidate_from_movie(parsed: dict, source_id: str = "nfo") -> Candidate:
    """movie 解析 dict → 匹配候选（payload 带完整 detail，供离线回放复用）。"""
    return Candidate(title=parsed.get("title") or "",
                     original_title=parsed.get("original_title") or "",
                     year=parsed.get("year"),
                     tmdb_id=parsed.get("tmdb_id"), imdb_id=parsed.get("imdb_id") or "",
                     source="nfo", source_id=source_id or "nfo", score=60.0,
                     payload={"detail": parsed})


def _parse_bytes(data: bytes) -> Candidate | None:
    """兼容旧接口：`<movie>` 字节 → 匹配候选（payload 带完整解析）。"""
    parsed = parse_movie_bytes(data)
    if parsed is None:
        return None
    return candidate_from_movie(parsed)


def _read(path: str) -> Candidate | None:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    cand = _parse_bytes(data)
    if cand is not None:
        cand.source_id = os.path.basename(path)
    return cand


def read_nfo(path: str) -> Candidate | None:
    return _read(path) if path and os.path.isfile(path) else None


def read_nfo_any(path: str) -> dict | None:
    """路径 → 解析结果 dict（movie/tvshow/episode 自动判别，非 NFO 返回 None）。"""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    for kind, fn in (("movie", parse_movie_bytes), ("tv", parse_tvshow_bytes),
                     ("episode", parse_episode_bytes)):
        parsed = fn(data)
        if parsed is not None:
            parsed["_kind"] = kind
            return parsed
    return None


def nfo_names_for(abs_path: str) -> list[str]:
    """候选 NFO 文件名（优先 `<stem>.nfo`，其次 `movie.nfo`/`tvshow.nfo`）。"""
    base = os.path.basename(abs_path or "")
    stem = os.path.splitext(base)[0]
    names: list[str] = []
    if stem:
        names.append(stem + ".nfo")
    for n in _NFO_NAMES + ("tvshow.nfo",):
        if n not in names:
            names.append(n)
    return names


def art_names_for(abs_path: str) -> dict:
    """同目录本地图片候选：{poster: [...], fanart: [...]}（Plex/Kodi 常见命名）。"""
    base = os.path.basename(abs_path or "")
    stem = os.path.splitext(base)[0]
    return {
        "poster": [f for f in (f"{stem}-poster.jpg", "poster.jpg", "folder.jpg",
                               "cover.jpg") if f],
        "fanart": [f for f in (f"{stem}-fanart.jpg", "fanart.jpg",
                               "backdrop.jpg") if f],
    }


def _names_for(abs_path: str) -> list[str]:
    return nfo_names_for(abs_path)


def candidates_for(abs_path: str) -> Candidate | None:
    """优先 `<stem>.nfo`，其次 `movie.nfo`（与写盘收敛规则一致）。"""
    if not abs_path:
        return None
    d = os.path.dirname(abs_path)
    for name in _names_for(abs_path):
        cand = _read(os.path.join(d, name))
        if cand is not None:
            return cand
    return None


def _read_any_local(d: str, abs_path: str) -> dict | None:
    for name in _names_for(abs_path):
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        parsed = read_nfo_any(p)
        if parsed is not None:
            parsed["_nfo_name"] = name
            return parsed
    return None


def candidates_for_backend(backend, rel: str) -> Candidate | None:
    """远程直读库版：经 StorageBackend 读同名 NFO 字节解析（离线匹配不再跳过）。"""
    from .. import storage
    rel = backend.norm(rel)
    if not rel:
        return None
    d = os.path.dirname(rel)
    for name in _names_for(rel):
        p = f"{d}/{name}" if d else name
        try:
            data = backend.read(p)
        except storage.StorageError:
            continue
        cand = _parse_bytes(data)
        if cand is not None:
            cand.source_id = name
            return cand
    return None


def read_any_for_backend(backend, rel: str) -> dict | None:
    """远程直读库版完整解析（movie/tvshow/episode 自动判别）。"""
    from .. import storage
    rel = backend.norm(rel)
    if not rel:
        return None
    d = os.path.dirname(rel)
    for name in _names_for(rel):
        p = f"{d}/{name}" if d else name
        try:
            data = backend.read(p)
        except storage.StorageError:
            continue
        parsed = None
        for kind, fn in (("movie", parse_movie_bytes), ("tv", parse_tvshow_bytes),
                         ("episode", parse_episode_bytes)):
            parsed = fn(data)
            if parsed is not None:
                parsed["_kind"] = kind
                break
        if parsed is not None:
            parsed["_nfo_name"] = name
            return parsed
    return None


def read_any_for(abs_path: str) -> dict | None:
    """本地路径版完整解析。"""
    if not abs_path:
        return None
    return _read_any_local(os.path.dirname(abs_path), abs_path)
