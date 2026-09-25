"""外部元数据应用（Phase 1+2）：NFO / Wikidata / TVmaze / Bangumi → 业务行落库。

- 标准化 detail（`detail_from_*` 产物）：标题/年份/简介/类型/产地/评分/图片 URL/
  别名/演职员/季集；`apply_external_movie` / `apply_external_show` 幂等写入
  `movies` / `tv_shows` / `tv_seasons` / `tv_episodes`，并同步 `external_meta`。
- `tmdb_id` 只在显式绑定（用户操作）或 TMDB 缓存已存在时写入 movies：无缓存的
  离线外源结果保持 tmdb_id=NULL，避免“有 id 就跳过重扫”卡死后续 TMDB 升级；
  待联网后强制重扫/手动匹配即可升级。
- 图片：远程 URL 经 `tmdb.download_url`（走 TMDB_PROXY）；NFO 同目录本地图片经
  StorageBackend 读字节落 `posters/ext/`。
"""
import os
import time

from .. import config, posters, store, tmdb
from ..log import get_logger
from .base import Candidate

logger = get_logger("metadata.external")

__all__ = ['EXTERNAL_SOURCES', 'detail_from_candidate', 'detail_from_nfo_movie',
           'detail_from_nfo_tvshow', 'apply_external_movie', 'apply_external_show',
           'apply_nfo_movie', 'apply_nfo_show', 'import_episode_nfos']

EXTERNAL_SOURCES = {"wikidata", "tvmaze", "bgm", "nfo", "douban", "imdb"}


def detail_from_candidate(cand: Candidate) -> dict:
    """Candidate.payload['detail']（或 payload 自身）→ 标准化 detail。"""
    payload = cand.payload if isinstance(cand.payload, dict) else {}
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else payload
    d = dict(detail or {})
    d.setdefault("title", cand.title)
    d.setdefault("original_title", cand.original_title)
    d.setdefault("year", cand.year)
    if cand.tmdb_id and not d.get("tmdb_id"):
        d["tmdb_id"] = cand.tmdb_id
    if cand.imdb_id and not d.get("imdb_id"):
        d["imdb_id"] = cand.imdb_id
    d["_source"] = cand.source
    d["_source_id"] = cand.source_id
    return d


def detail_from_nfo_movie(parsed: dict) -> dict:
    """NFO 解析（`nfo_import.parse_movie_bytes`）→ 标准化 detail。"""
    people = {"directors": [{"name": n} for n in parsed.get("directors") or []],
              "cast": [{"name": a.get("name") or "", "character": a.get("character") or "",
                        "order": a.get("order", 99)} for a in parsed.get("actors") or []]}
    return {
        "title": parsed.get("title") or "",
        "original_title": parsed.get("original_title") or "",
        "year": parsed.get("year"),
        "overview": parsed.get("overview") or "",
        "rating": parsed.get("rating"),
        "genres": parsed.get("genres") or [],
        "tags": parsed.get("tags") or [],
        "countries": parsed.get("countries") or [],
        "studios": parsed.get("studios") or [],
        "premiered": parsed.get("premiered") or "",
        "tagline": parsed.get("tagline") or "",
        "runtime": parsed.get("runtime") or 0,
        "collection_name": parsed.get("collection_name") or "",
        "aliases": [],
        "people": people,
        "poster_url": "",
        "backdrop_url": "",
        "tmdb_id": parsed.get("tmdb_id"),
        "imdb_id": parsed.get("imdb_id") or "",
        "douban_rating": parsed.get("douban_rating"),
        "custom_rating": parsed.get("custom_rating"),
        "_source": "nfo",
        "_source_id": parsed.get("_nfo_name") or "nfo",
        "_local_art": True,
    }


def detail_from_nfo_tvshow(parsed: dict) -> dict:
    """TV `tvshow.nfo` 解析 → 标准化 detail（无季集明细）。"""
    d = detail_from_nfo_movie(parsed)
    d["_source"] = "nfo"
    d["tvdb_id"] = parsed.get("tvdb_id")
    d["status"] = parsed.get("status") or ""
    d["seasons"] = []
    d["episodes"] = []
    return d


def _backend_for(library_id):
    """库 id → StorageBackend（失败/未知返回 None，调用方自行跳过写盘）。"""
    try:
        from .. import storage
        lid = int(library_id or 0)
        if not lid:
            return None
        return storage.backend_for(lid)
    except Exception:
        return None


def _show_dir_of(show_id: int) -> str:
    """剧根目录（库内相对）：取首集路径上跳季目录（复用 tv_nfo_link 规则）。"""
    try:
        from ..scanner import tv_nfo_link
        for ep in store.list_episodes(int(show_id)) or []:
            rel = str(ep.get("file_path") or "")
            if rel:
                return tv_nfo_link.show_dir_of(rel) or ""
    except Exception as e:
        logger.debug("show dir resolve failed show=%s: %s", show_id, e)
    return ""


def _people_names(detail: dict) -> str:
    names: list[str] = []
    people = detail.get("people") if isinstance(detail.get("people"), dict) else {}
    for p in (people.get("directors") or []):
        n = str((p or {}).get("name") or "").strip()
        if n:
            names.append(n)
    for p in (people.get("cast") or []):
        n = str((p or {}).get("name") or "").strip()
        if n:
            names.append(n)
    return ", ".join(dict.fromkeys(names))


def _download_to_rel(source: str, source_id, url: str, kind: str = "poster") -> str:
    """远程图片 → DATA_DIR 相对路径；url 空/失败返回 ''（保留旧文件）。"""
    rel = (posters.external_poster_rel(source, source_id) if kind == "poster"
           else posters.external_backdrop_rel(source, source_id))
    dest = os.path.join(config.settings.data_dir, rel)
    if not str(url or "").strip():
        return rel if os.path.isfile(dest) and os.path.getsize(dest) > 0 else ""
    if tmdb.download_url(url, dest):
        return rel
    return rel if os.path.isfile(dest) and os.path.getsize(dest) > 0 else ""


def _import_local_art(source: str, source_id, backend, dir_rel: str,
                      names: list[str], kind: str = "poster") -> str:
    """同目录本地图片（poster.jpg/fanart.jpg…）经 backend 读字节 → posters/ext。"""
    if backend is None or not dir_rel:
        return ""
    d = backend.norm(dir_rel or "")
    for name in names or []:
        rel = f"{d}/{name}" if d else name
        try:
            data = backend.read(rel)
        except Exception:
            continue
        if not data or len(data) < 128:
            continue
        rel_out = (posters.external_poster_rel(source, source_id) if kind == "poster"
                   else posters.external_backdrop_rel(source, source_id))
        dest = os.path.join(config.settings.data_dir, rel_out)
        try:
            from ..fsutil import atomic_write_bytes
            parent = os.path.dirname(dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            atomic_write_bytes(dest, data)
            return rel_out
        except OSError as e:
            logger.warning("import local art failed rel=%s: %s", rel, e)
            return ""
    return ""


def _write_movie_nfo(movie_id: int, abs_path: str, backend, rel: str) -> bool:
    from ..scanner import nfo_link
    return nfo_link._write_nfo_for(movie_id, abs_path or "", backend=backend,
                                   rel=rel or "")


def apply_external_movie(movie_id: int, detail: dict, *, source: str = "",
                         source_id: str = "", backend=None, rel: str = "",
                         abs_path: str = "", needs_review: int = 0,
                         set_tmdb_id: bool = False, write_nfo: bool = True,
                         fetch_images: bool = True) -> dict:
    """外部 detail → movies 行（幂等）。`fetch_images=False` 时跳过图片写盘（测试）。

    返回 {title, year, tmdb_id, poster_path, nfo, source}。"""
    mid = int(movie_id)
    src = str(source or detail.get("_source") or "").strip() or "external"
    sid = str(source_id or detail.get("_source_id") or "").strip() or str(mid)
    cur = store.get_movie(mid) or {}
    title = str(detail.get("title") or "").strip()
    keep_title = bool(cur.get("title")) and not int(cur.get("title_auto") or 0)
    if not title and cur.get("title"):
        title = str(cur["title"])
    fields: dict = {
        "match_source": src,
        "needs_review": int(needs_review or 0),
    }
    if title and not keep_title:
        fields["title"] = title
        fields["title_auto"] = 1   # 外源标题可被后续 TMDB 匹配覆盖
    for key in ("original_title", "year", "overview"):
        v = detail.get(key)
        if v not in (None, ""):
            fields[key] = v
    countries = [str(c) for c in (detail.get("countries") or []) if c]
    lang = str(detail.get("original_language") or "")
    if countries or lang:
        from ..regions import resolve as resolve_region
        primary, region = resolve_region(countries, lang)
        fields["origin_countries"] = countries
        fields["origin_country"] = primary
        fields["region"] = region
        if lang:
            fields["original_language"] = lang
    if detail.get("genres"):
        fields["genres"] = [str(g) for g in detail["genres"] if g]
    names = _people_names(detail)
    if names:
        fields["person_names"] = names
    imdb_id = str(detail.get("imdb_id") or "").strip()
    if imdb_id:
        fields["imdb_id"] = imdb_id
    tmdb_id = detail.get("tmdb_id")
    try:
        tmdb_id_i = int(tmdb_id) if tmdb_id else 0
    except (TypeError, ValueError):
        tmdb_id_i = 0
    if set_tmdb_id and tmdb_id_i:
        fields["tmdb_id"] = tmdb_id_i
    # 图片：远程 URL 或同目录本地图
    if fetch_images:
        if detail.get("_local_art") and backend is not None:
            from . import nfo_import as _nfo
            media_rel = rel or cur.get("file_path") or ""
            art = _import_local_art(
                src, sid, backend, os.path.dirname(backend.norm(media_rel)),
                (_nfo.art_names_for(os.path.basename(media_rel)).get("poster") or []),
                "poster")
        else:
            art = _download_to_rel(src, sid, str(detail.get("poster_url") or ""), "poster")
        if art:
            fields["poster_path"] = art
    store.update_movie_meta(mid, **fields)
    try:
        store.resync_fts(mid)
    except Exception:
        pass
    # external_meta 幂等（payload 完整存 detail）
    try:
        store.upsert_external(src, sid, "movie",
                              title=str(store.get_movie(mid).get("title") or ""),
                              original_title=str(detail.get("original_title") or ""),
                              year=detail.get("year"),
                              tmdb_id=tmdb_id_i or None,
                              imdb_id=imdb_id, poster_url=detail.get("poster_url") or "",
                              backdrop_url=detail.get("backdrop_url") or "",
                              payload={k: v for k, v in detail.items()
                                       if not str(k).startswith("_")}
                              | {"aliases": detail.get("aliases") or []})
    except Exception as e:
        logger.debug("upsert external failed %s:%s: %s", src, sid, e)
    nfo_ok = False
    if write_nfo:
        nfo_ok = _write_movie_nfo(mid, abs_path, backend, rel)
    row = store.get_movie(mid) or {}
    return {"title": row.get("title") or "", "year": row.get("year"),
            "tmdb_id": row.get("tmdb_id"), "poster_path": row.get("poster_path") or "",
            "nfo": nfo_ok, "source": src}


def apply_nfo_movie(movie_id: int, parsed: dict, *, backend=None, rel: str = "",
                    abs_path: str = "", needs_review: int = 0) -> dict:
    """NFO 完整回放（离线首见片）：不写 tmdb_id（防跳过后续 TMDB 升级）。

    source_id 用库内相对路径（P1.2：`movie.nfo` 同名会跨片互相覆盖索引）。"""
    detail = detail_from_nfo_movie(parsed)
    sid = str(rel or abs_path or parsed.get("_nfo_name") or "nfo")
    return apply_external_movie(movie_id, detail, source="nfo", source_id=sid,
                                backend=backend, rel=rel, abs_path=abs_path,
                                needs_review=needs_review, set_tmdb_id=False)


def _fill_episode(ep: dict, item: dict, src: str) -> bool:
    """只补空字段（不覆盖已有 TMDB 刮削结果）。返回是否写入。"""
    fields: dict = {}
    for key, src_key in (("title", "title"), ("overview", "overview"),
                         ("air_date", "air_date")):
        v = str(item.get(src_key) or "").strip()
        if v and not str(ep.get(key) or "").strip():
            fields[key] = v
    if not fields:
        return False
    try:
        return bool(store.update_episode_meta(int(ep["id"]), **fields))
    except Exception as e:
        logger.debug("fill episode failed ep=%s src=%s: %s", ep.get("id"), src, e)
        return False


def import_episode_nfos(show_id: int, library_id=None) -> int:
    """离线补集：逐集读同茎 `<stem>.nfo`（episodedetails）回填空字段。返回回填数。"""
    from .. import storage
    from . import nfo_import as _nfo
    lid = int(library_id or 0) or None
    try:
        backend = storage.backend_for(lid) if lid else None
    except Exception:
        backend = None
    if backend is None:
        return 0
    n = 0
    for ep in store.list_episodes(int(show_id)):
        rel = str(ep.get("file_path") or "")
        if not rel:
            continue
        parsed = _nfo.read_any_for_backend(backend, rel)
        if not parsed or parsed.get("_kind") != "episode":
            continue
        if _fill_episode(ep, parsed, "nfo"):
            n += 1
    return n


def apply_external_show(show_id: int, detail: dict, *, source: str = "",
                        source_id: str = "", library_id=None, needs_review: int = 0,
                        set_tmdb_id: bool = False, write_nfo: bool = True,
                        fill_episodes: bool = True) -> dict:
    """外部 detail → tv_shows/tv_seasons/tv_episodes（幂等，只补空）。"""
    sid_show = int(show_id)
    src = str(source or detail.get("_source") or "").strip() or "external"
    sid = str(source_id or detail.get("_source_id") or "").strip() or str(sid_show)
    cur = store.get_show_meta(sid_show) or {}
    title = str(detail.get("title") or "").strip()
    keep_title = bool(cur.get("title")) and not int(cur.get("title_auto") or 0)
    if not title and cur.get("title"):
        title = str(cur["title"])
    fields: dict = {"match_source": src, "needs_review": int(needs_review or 0),
                    "fetched_at": int(time.time())}
    if title and not keep_title:
        from ..scanner.parse import normalize_title
        fields["title"] = title
        fields["sort_title"] = normalize_title(title)
        fields["title_auto"] = 1
    for key in ("original_title", "year", "overview", "status", "first_air_date"):
        v = detail.get(key)
        if v not in (None, ""):
            fields[key] = v
    countries = [str(c) for c in (detail.get("countries") or []) if c]
    lang = str(detail.get("original_language") or "")
    if countries or lang:
        from ..regions import resolve as resolve_region
        primary, region = resolve_region(countries, lang)
        fields["origin_countries"] = countries
        fields["origin_country"] = primary
        fields["region"] = region
        if lang:
            fields["original_language"] = lang
    if detail.get("genres"):
        fields["genres"] = [str(g) for g in detail["genres"] if g]
    names = _people_names(detail)
    if names:
        fields["person_names"] = names
    imdb_id = str(detail.get("imdb_id") or "").strip()
    if imdb_id:
        fields["imdb_id"] = imdb_id
    tvdb_id = detail.get("tvdb_id")
    if tvdb_id:
        fields["tvdb_id"] = tvdb_id
    tmdb_id = detail.get("tmdb_id")
    try:
        tmdb_id_i = int(tmdb_id) if tmdb_id else 0
    except (TypeError, ValueError):
        tmdb_id_i = 0
    if set_tmdb_id and tmdb_id_i:
        fields["tmdb_id"] = tmdb_id_i
    seasons = detail.get("seasons") or []
    if detail.get("number_of_seasons"):
        try:
            fields["number_of_seasons"] = int(detail["number_of_seasons"])
        except (TypeError, ValueError):
            pass
    elif seasons:
        try:
            fields["number_of_seasons"] = max(int(s.get("season_number") or 0)
                                              for s in seasons)
        except (TypeError, ValueError):
            pass
    if detail.get("number_of_episodes"):
        try:
            fields["number_of_episodes"] = int(detail["number_of_episodes"])
        except (TypeError, ValueError):
            pass
    store.update_show_meta(sid_show, **fields)
    # 图片
    if detail.get("_local_art"):
        media_dir = _show_dir_of(sid_show)
        backend = _backend_for(library_id or cur.get("library_id"))
        if backend is not None and media_dir:
            from . import nfo_import as _nfo
            poster_rel = _import_local_art(
                "nfo", sid, backend, media_dir,
                (_nfo.art_names_for("show.mkv").get("poster") or []), "poster")
            if poster_rel:
                store.update_show_meta(sid_show, poster_path=poster_rel)
            back = _import_local_art("nfo", sid, backend, media_dir,
                                     ["fanart.jpg", "backdrop.jpg"], "backdrop")
            if back:
                store.update_show_meta(sid_show, backdrop_path=back)
    else:
        poster_rel = _download_to_rel(src, sid, str(detail.get("poster_url") or ""),
                                      "poster")
        if poster_rel:
            store.update_show_meta(sid_show, poster_path=poster_rel)
        backdrop_rel = _download_to_rel(src, sid, str(detail.get("backdrop_url") or ""),
                                        "backdrop")
        if backdrop_rel:
            store.update_show_meta(sid_show, backdrop_path=backdrop_rel)
    # 季与集
    if seasons:
        for s in seasons:
            try:
                sn = int(s.get("season_number"))
            except (TypeError, ValueError):
                continue
            try:
                store.upsert_season(sid_show, library_id or cur.get("library_id") or 1, sn,
                                    name=str(s.get("name") or ""),
                                    overview=str(s.get("overview") or ""),
                                    air_date=str(s.get("air_date") or "")[:10],
                                    episode_count=int(s.get("episode_count") or 0))
            except Exception as e:
                logger.debug("upsert external season failed show=%s sn=%s: %s",
                             sid_show, sn, e)
    eps = detail.get("episodes") or []
    filled = 0
    if eps and fill_episodes:
        local = store.list_episodes(sid_show) or []
        by_key: dict = {}
        for e in local:
            try:
                by_key.setdefault((int(e.get("season") or 0), int(e.get("episode") or 0)), e)
            except (TypeError, ValueError):
                continue
        for item in eps:
            try:
                key = (int(item.get("season") or 0), int(item.get("episode") or 0))
            except (TypeError, ValueError):
                continue
            ep = by_key.get(key)
            if ep and _fill_episode(ep, item, src):
                filled += 1
    try:
        store.upsert_external(src, sid, "tv",
                              title=str((store.get_show_meta(sid_show) or {}).get("title") or ""),
                              original_title=str(detail.get("original_title") or ""),
                              year=detail.get("year"), tmdb_id=tmdb_id_i or None,
                              imdb_id=imdb_id, tvdb_id=tvdb_id,
                              poster_url=detail.get("poster_url") or "",
                              backdrop_url=detail.get("backdrop_url") or "",
                              payload={k: v for k, v in detail.items()
                                       if not str(k).startswith("_")}
                              | {"aliases": detail.get("aliases") or []})
    except Exception as e:
        logger.debug("upsert external show failed %s:%s: %s", src, sid, e)
    nfo = False
    if write_nfo:
        try:
            from ..scanner import tv_persist
            r = tv_persist.write_media_files(sid_show, library_id or cur.get("library_id"))
            nfo = bool(r.get("nfo"))
        except Exception as e:
            logger.debug("external show nfo write failed show=%s: %s", sid_show, e)
    return {"title": (store.get_show_meta(sid_show) or {}).get("title") or "",
            "tmdb_id": (store.get_show_meta(sid_show) or {}).get("tmdb_id"),
            "nfo": nfo, "source": src, "episodes_filled": filled}


def apply_nfo_show(show_id: int, parsed: dict, *, library_id=None,
                   needs_review: int = 0) -> dict:
    """`tvshow.nfo` 离线回放：不写 tmdb_id（防跳过后续 TMDB 升级）。"""
    detail = detail_from_nfo_tvshow(parsed)
    out = apply_external_show(show_id, detail, source="nfo",
                              source_id=parsed.get("_nfo_name") or "tvshow.nfo",
                              library_id=library_id, needs_review=needs_review,
                              set_tmdb_id=False)
    try:
        out["episodes_filled"] = import_episode_nfos(show_id, library_id)
    except Exception as e:
        logger.debug("import episode nfos failed show=%s: %s", show_id, e)
    return out
