"""TV 匹配（T2）：目录 hint → 本地已匹配 → TMDB 搜索（相似门 + 年份±1 + 别名兜底）。

- `{tmdb-…}` 目录提示直接绑定；`{tvdb-…}`/`{imdb-tt…}` 走 `/find`；
- 无 hint：`tmdb.search_tv` + `short_candidates` 回退；相似门语义与电影
  `scanner.match.pick_match` 一致（不像不绑、短查询/年份不符标待确认）；
- 门不过时对前 3 个候选拉别名表重评（`Legal High`↔胜者即是正义、
  `Hanzawa Naoki`↔半泽直树 这类「英文目录名 vs 本地化标题」的常见缺口）。
"""
from .. import config, store, tmdb
from ..regions import resolve as resolve_region
from ..log import get_logger
from .match import SIM_THRESHOLD, title_similar
from .parse import short_candidates

logger = get_logger("scanner.tv_match")
__all__ = ['resolve_show', 'pick_tv_match', 'tv_meta_from_detail', 'tv_credits',
           'tv_aggregate_credits', 'tv_season_credits', 'tv_episode_credits',
           'tv_display_title']

_ALT_RETRY = 3   # 相似门不过时，对前 N 个候选拉别名表重评


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (s or ""))


def tv_display_title(detail: dict) -> str:
    """剧集中文显示名：`name` 无 CJK 时按地区优先级取别名表（results 形态）。
    与电影 pick_display_title 同策略，字段名不同（name/original_name、results）。"""
    raw = str((detail or {}).get("name") or "").strip()
    if _has_cjk(raw):
        return raw
    lang = str(config.effective_tmdb_language() or "").strip().lower()
    if not lang.startswith("zh"):
        return raw
    region = lang.split("-", 1)[1].upper() if "-" in lang else ""
    prefs: list[str] = []
    for r in ([region] if region else []) + ["CN", "TW", "HK", "SG"]:
        if r and r not in prefs:
            prefs.append(r)
    alts = ((detail or {}).get("alternative_titles") or {}).get("results") or []
    for want in prefs:
        for t in alts:
            t = t or {}
            iso = str(t.get("iso_3166_1") or "").upper()
            cand = str(t.get("title") or "").strip()
            if iso == want and cand and cand != raw and _has_cjk(cand):
                return cand
    return raw


def _tv_year(r: dict):
    fd = (r.get("first_air_date") or "")[:4]
    return int(fd) if fd.isdigit() else None


def _hit_titles(r: dict) -> list[str]:
    return [str(r.get("name") or ""), str(r.get("original_name") or "")]


def _sim(r: dict, query: str) -> float:
    return max((title_similar(query, t) for t in _hit_titles(r)), default=0.0)


def _year_ok(r: dict, year) -> bool:
    if not year:
        return True
    y = _tv_year(r)
    return y is not None and abs(y - int(year)) <= 1


def _popularity(r: dict) -> tuple:
    try:
        pop = float(r.get("popularity") or 0)
    except (TypeError, ValueError):
        pop = 0.0
    try:
        votes = int(r.get("vote_count") or 0)
    except (TypeError, ValueError):
        votes = 0
    return (pop, votes)


def pick_tv_match(results: list[dict], year, query: str = "") -> tuple[dict | None, bool]:
    """挑结果（与电影同门）：像且年份符 → 直接；年份符但不像 / 像但年份不符 → 待确认；
    都不满足 → None（不静默采信 results[0]）。"""
    if not results:
        return None, False
    if not query:
        if year:
            for r in results:
                if _year_ok(r, year):
                    return r, False
        m = results[0]
        return m, bool(year and not _year_ok(m, year))
    scored = [(r, _sim(r, query)) for r in results]
    groups = (
        ([r for r, s in scored if s >= SIM_THRESHOLD and _year_ok(r, year)], False),
        ([r for r, s in scored if _year_ok(r, year)], True),
        ([r for r, s in scored if s >= SIM_THRESHOLD], True),
    )
    for group, review in groups:
        if not group:
            continue
        # 同档多候选按 popularity/vote_count 取最热（本地目录无年份时唯一可用信号，
        # 防 `西部世界` 命中 1980 年 Beyond Westworld），并标待确认。
        best = max(group, key=_popularity)
        ambiguous = len({r.get("id") for r in group}) > 1
        return best, bool(review or ambiguous)
    return None, False


def _library_hit(title: str, year):
    """本地已匹配剧（match_index kind='tv'，零网络）：归一标题精确 + 年份±1。"""
    term = (title or "").strip()
    if not term:
        return None
    try:
        rows = store.search_match_index(term, kind="tv", limit=8, year=year)
    except Exception as e:
        logger.debug("tv library index search failed term=%s: %s", term, e)
        return None
    for r in rows:
        sim = max(title_similar(term, r.get("title") or ""),
                  title_similar(term, r.get("original_title") or ""))
        if sim < 0.9:
            continue
        try:
            if year and r.get("year") is not None and abs(int(r["year"]) - int(year)) > 1:
                continue
        except (TypeError, ValueError):
            pass
        if r.get("tmdb_id"):
            return int(r["tmdb_id"])
    return None


def _hint_detail(hints: dict) -> tuple[dict | None, str]:
    """目录 hint → TMDB detail。返回 (detail, source)。"""
    hints = hints or {}
    try:
        if hints.get("tmdb"):
            return tmdb.tv_detail(int(hints["tmdb"])), "hint"
        if hints.get("tvdb"):
            rows = tmdb.find_by_external_id(str(hints["tvdb"]), "tvdb_id")
            if rows:
                return tmdb.tv_detail(int(rows[0]["id"])), "hint"
        if hints.get("imdb"):
            rows = tmdb.find_by_external_id(str(hints["imdb"]), "imdb_id")
            if rows:
                return tmdb.tv_detail(int(rows[0]["id"])), "hint"
    except Exception as e:
        logger.warning("tv hint resolve failed hints=%s: %s", hints, e)
    return None, ""


def _external_show_candidate(title: str, year, library_id=None):
    """无 key 外部源（wikidata/tvmaze/bgm）→ 候选/门控（P2.4）；失败返回 (None, 0)。"""
    try:
        from ..metadata import auto as meta_auto
        from ..metadata import chain as meta_chain
        hits = meta_chain.search(title, year, "tv", library_id=library_id, limit=5,
                                 exclude=("local", "tmdb"))
        return meta_auto.pick_auto(hits, title, year, "tv")
    except Exception as e:
        logger.debug("tv external chain failed title=%s: %s", title, e)
        return None, 0, ""


def resolve_show(title: str, year, hints: dict | None = None,
                 use_library: bool = True, library_id=None) -> dict:
    """剧名 → 匹配结果：{tmdb_id, detail, source, needs_review, query[, candidate]}。

    顺序：目录 hint → 本地已匹配（离线）→ TMDB 搜索（短查询回退 + 别名重评）
    → 无 key 外部源（仅 TMDB 不可用/无结果时；带 `candidate` 供直接落外部元数据）。
    `use_library=False`（force 重刮）跳过本地索引，强制走真实搜索——本地索引可能
    残留上一条错配（如 `西部世界` 曾绑到 1980 版）。
    TMDB 异常且外部源也无命中时向上抛（调用方标 scan_failed 下次重试）。"""
    detail, source = _hint_detail(hints or {})
    if detail:
        return {"tmdb_id": int(detail["id"]), "detail": detail, "source": source,
                "needs_review": False, "query": title or ""}
    tid = _library_hit(title, year) if use_library else None
    if tid:
        return {"tmdb_id": tid, "detail": None, "source": "library",
                "needs_review": False, "query": title or ""}
    tmdb_error: Exception | None = None
    try:
        for q in short_candidates(title):
            results = tmdb.search_tv(q, year)
            hit, review = pick_tv_match(results, year, query=q)
            if hit is None and results:
                # 别名重评：本地化标题 vs 英文目录名（Legal High / Hanzawa Naoki）
                for cand in results[:_ALT_RETRY]:
                    try:
                        alts = tmdb.tv_alternative_titles(int(cand["id"]))
                    except Exception as e:
                        logger.debug("tv alt titles failed id=%s: %s", cand.get("id"), e)
                        continue
                    names = [str(a.get("title") or "") for a in alts]
                    if any(title_similar(q, n) >= SIM_THRESHOLD for n in names):
                        hit, review = cand, True
                        break
            if hit:
                if q != title:
                    review = True     # 短查询可能过度截断：一律待确认
                return {"tmdb_id": int(hit["id"]), "detail": None, "source": "tmdb",
                        "needs_review": bool(review), "query": q}
    except Exception as e:
        tmdb_error = e
    # 外部源兜底（P2.4）：TMDB 失败/无结果时尝试无 key provider
    cand, review, _why = _external_show_candidate(title, year, library_id)
    if cand is not None:
        return {"tmdb_id": cand.tmdb_id, "detail": None, "source": cand.source,
                "needs_review": bool(review), "query": title or "",
                "candidate": cand}
    if tmdb_error is not None:
        raise tmdb_error
    return {"tmdb_id": None, "detail": None, "source": "", "needs_review": False,
            "query": title or ""}


def tv_meta_from_detail(detail: dict) -> dict:
    """TMDB 剧集详情 → tmdb_cache 字典（movie 同构 + TV 专属字段）。
    TV 专属字段（status/number_of_seasons/seasons…）落在 payload_json，供离线重放；
    显示列由 tv_persist 写入 tv_shows/tv_seasons。"""
    first = str(detail.get("first_air_date") or "")[:10]
    year = int(first[:4]) if first[:4].isdigit() else None
    countries = [c for c in (detail.get("origin_country") or []) if c]
    primary, region = resolve_region(countries, detail.get("original_language"))
    ext = detail.get("external_ids") or {}
    networks = [n["name"] for n in (detail.get("networks") or []) if n.get("name")]
    studios = networks + [c["name"] for c in (detail.get("production_companies") or [])
                          if c.get("name")]
    runtimes = detail.get("episode_run_time") or []
    try:
        runtime = int(runtimes[0]) if runtimes else int(
            (detail.get("last_episode_to_air") or {}).get("runtime") or 0)
    except (TypeError, ValueError):
        runtime = 0
    created_by = [{"id": p.get("id"), "name": p.get("name") or "",
                   "profile_path": p.get("profile_path")}
                  for p in (detail.get("created_by") or []) if p.get("id")]
    raw_name = str(detail.get("name") or "").strip()
    from .match import collect_aliases
    aliases = collect_aliases((detail.get("alternative_titles") or {}).get("results"))
    return {
        "title": tv_display_title(detail),
        "original_title": str(detail.get("original_name") or "").strip() or raw_name,
        "aliases": aliases,
        "year": year,
        "overview": detail.get("overview", "") or "",
        "tmdb_id": detail["id"],
        "imdb_id": ext.get("imdb_id") or "",
        "tvdb_id": ext.get("tvdb_id"),
        "tmdb_rating": detail.get("vote_average"),
        "genres": [g["name"] for g in detail.get("genres", []) if g.get("name")],
        "genre_ids": [g["id"] for g in detail.get("genres", []) if g.get("id")],
        "original_language": detail.get("original_language", "") or "",
        "origin_countries": countries,
        "origin_country": primary,
        "region": region,
        "media_type": "tv",
        "collection_tmdb_id": None,
        "collection_name": "",
        "collection_poster_path": "",
        "premiered": first,
        "tagline": detail.get("tagline", "") or "",
        "runtime": runtime,
        "studios": studios,
        "poster_tmdb_path": detail.get("poster_path") or "",
        "backdrop_tmdb_path": detail.get("backdrop_path") or "",
        "logo_tmdb_path": "",
        "status": detail.get("status", "") or "",
        "first_air_date": first,
        "last_air_date": str(detail.get("last_air_date") or "")[:10],
        "number_of_seasons": int(detail.get("number_of_seasons") or 0),
        "number_of_episodes": int(detail.get("number_of_episodes") or 0),
        "episode_run_time": runtime,
        "networks": networks,
        "created_by": created_by,
        "seasons": detail.get("seasons") or [],
    }


def tv_credits(detail: dict) -> dict:
    """credits 最小集：前 10 演员 + 创作者（created_by，crew 位存 creator）。

    适用于 `tv_detail(append=credits)` 与季详情顶层 `credits`（单季常驻阵容，
    条目为 `{character}` 字符串形）。全剧聚合另见 `tv_aggregate_credits`
   （`roles[]` 数组形）。"""
    cast = []
    for i, c in enumerate((detail.get("credits") or {}).get("cast", [])[:10]):
        if not c.get("id"):
            continue
        cast.append({"id": c["id"], "name": c.get("name", ""),
                     "profile_path": c.get("profile_path"),
                     "character": c.get("character", ""), "order": i})
    crew = []
    for p in detail.get("created_by") or []:
        if p.get("id"):
            crew.append({"id": p["id"], "name": p.get("name", ""),
                         "profile_path": p.get("profile_path"), "job": "Creator"})
    return {"cast": cast, "crew": crew}


def _min_cast(entries, limit: int = 10, character_of=None) -> list[dict]:
    """credits 条目 → 最小集（id/name/profile_path/character/order，兼容
    aggregate 的 `roles[]` 形与季 credits 的 `character` 字符串形）。"""
    out = []
    for i, c in enumerate(entries or []):
        if len(out) >= limit:
            break
        if not c.get("id"):
            continue
        out.append({"id": c["id"], "name": c.get("name", "") or "",
                    "profile_path": c.get("profile_path"),
                    "character": character_of(c) if character_of else "",
                    "order": len(out)})
    return out


def tv_aggregate_credits(agg: dict, limit: int = 10) -> dict:
    """全剧聚合 credits 最小集（`aggregate_credits` → 缓存形态）。

    一人多角时 `character` 取首个角色（展示用），`episode_count` 为各角色
    出场集数之和（供"主演/客串"排序）；crew 位同样存 created_by（调用方在
    `apply_tv_detail` 内合并，与季级行为一致）。"""
    cast = []
    for c in (agg or {}).get("cast", []) or []:
        if len(cast) >= limit:
            break
        if not c.get("id"):
            continue
        roles = c.get("roles") or []
        eps = 0
        for r in roles:
            try:
                eps += int(r.get("episode_count") or 0)
            except (TypeError, ValueError):
                continue
        try:
            total = int(c.get("total_episode_count") or 0)
        except (TypeError, ValueError):
            total = 0
        cast.append({"id": c["id"], "name": c.get("name", "") or "",
                     "profile_path": c.get("profile_path"),
                     "character": str((roles[0].get("character") if roles else "") or ""),
                     "episode_count": eps or total,
                     "total_episode_count": total or eps,
                     "order": len(cast)})
    return {"cast": cast, "crew": []}


def tv_season_credits(season_detail: dict, limit: int = 10) -> list[dict]:
    """本季常驻阵容最小集（季详情顶层 `credits` → `tv_seasons.cast` 落库形态，
    与 `tv_credits` 同形；缺席返回 []，调用方据此跳过覆盖）。"""
    return _min_cast((season_detail or {}).get("credits", {}).get("cast", [])
                     if isinstance((season_detail or {}).get("credits"), dict)
                     else [], limit, lambda c: str(c.get("character") or ""))


def tv_episode_credits(episode_entry: dict) -> dict:
    """单集演职（季详情 `episodes[]` 条目 → `tv_episodes.episode_credits` 落库形态）。

    `{guests: [{id,name,character,profile_path}], directors: [{id,name}]}`；
    条目缺 `guest_stars` 键视为无数据（返回 {}，调用方跳过覆盖——TMDB 正常
    一定带键，缺键只出现在异常 payload）。"""
    if not isinstance(episode_entry, dict) or "guest_stars" not in episode_entry:
        return {}
    guests = _min_cast(episode_entry.get("guest_stars"), 20,
                       lambda c: str(c.get("character") or ""))
    directors = []
    for p in episode_entry.get("crew") or []:
        if str(p.get("job") or "") == "Director" and p.get("id"):
            directors.append({"id": p["id"], "name": p.get("name", "") or ""})
    return {"guests": guests, "directors": directors}
