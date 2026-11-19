"""Read a bounded set of public TMDB records; only the caller can assign local IDs."""
from datetime import date
from itertools import chain, product
import re
import time
import unicodedata

import httpx

from .. import config, tmdb
from .client import AiUnavailable


_UNAVAILABLE = {
    "added_from": "入库日期", "added_to": "入库日期", "tags": "片库标签", "exclude_tags": "片库标签",
    "audio_languages": "文件音轨", "subtitle_languages": "文件字幕", "season": "具体季",
    "episode": "具体分集", "absolute_number": "绝对集号", "regions": "片库产地大区",
    "status": "本库连载状态",
}
_DATES = {"movie": "primary_release_date", "tv": "first_air_date"}


def _result(items=(), warnings=()):
    return {"items": list(items), "total": len(items), "warnings": list(warnings), "excluded_unknown": 0}


def _genre_filters(read, kind, constraints):
    names = constraints.get("genres") or []
    excluded = constraints.get("exclude_genres") or []
    if not names and not excluded:
        return {}
    genre_rows = read(f"/genre/{kind}/list", {}).get("genres", [])
    ids = {g["name"]: str(g["id"]) for g in genre_rows if isinstance(g, dict) and g.get("name") and g.get("id")}
    if any(name not in ids for name in [*names, *excluded]):
        return None
    return {"with_genres": "|".join(ids[n] for n in names), "without_genres": ",".join(ids[n] for n in excluded)}


def _date_range(c, year, today):
    starts = [str(c["date_from"])] if c.get("date_from") else []
    ends = [str(c["date_to"])] if c.get("date_to") else []
    if c.get("year_min") or year:
        starts.append(f"{max(c.get('year_min') or 1, year or 1):04d}-01-01")
    if c.get("year_max") or year:
        ends.append(f"{min(c.get('year_max') or 9999, year or 9999):04d}-12-31")
    if year == today.year or c.get("year_max") == today.year:
        ends.append(today.isoformat())
    return max(starts, default=""), min(ends, default="")


def _item(row, kind, image_base):
    if not isinstance(row, dict) or row.get("adult") or type(row.get("id")) is not int or row["id"] <= 0:
        return None
    title = row.get("title" if kind == "movie" else "name")
    if not isinstance(title, str) or not title.strip():
        return None
    released = row.get("release_date" if kind == "movie" else "first_air_date") or ""
    if not isinstance(released, str):
        return None
    score = row.get("vote_average")
    poster = row.get("poster_path")
    poster_url = (image_base + "/t/p/w342" + poster
                  if isinstance(poster, str) and re.fullmatch(r"/[a-zA-Z0-9_-]+\.(?:jpg|png|webp)", poster) else None)
    original = row.get("original_title" if kind == "movie" else "original_name")
    votes = row.get("vote_count")
    return {"kind": kind, "id": None, "tmdb_id": row["id"], "title": title.strip()[:300],
            "external_url": f"https://www.themoviedb.org/{kind}/{row['id']}", "detail_path": None,
            "original_title": original[:300] if isinstance(original, str) else "",
            "overview": str(row.get("overview") or "")[:1500], "poster_path": None, "poster_url": poster_url,
            "year": int(released[:4]) if released[:4].isascii() and released[:4].isdigit() else None,
            "rating": score if type(score) in (float, int) and 0 < score <= 10 else None,
            "rating_source": "tmdb", "in_library": False, "watch_state": "unknown",
            "evidence": [], "release_date": released, "vote_count": votes if type(votes) is int and votes > 0 else 0}


def _bounds(value, low, high):
    if low is None and high is None:
        return True
    return value is not None and (low is None or value >= low) and (high is None or value <= high)


def _directory_params(kind, constraints, genre_params, order, dates):
    field = _DATES[kind]
    sort_key, _, direction = order.partition("_")
    sort_by = {"rating": "vote_average", "year": field}.get(sort_key)
    params = {**genre_params, "sort_by": f"{sort_by}.{direction}" if sort_by else "popularity.desc",
              field + ".gte": dates[0], field + ".lte": dates[1],
              "vote_count.gte": 100 if sort_key == "rating" else None,
              "vote_average.gte": constraints.get("min_rating"), "vote_average.lte": constraints.get("max_rating"),
              "with_runtime.gte": constraints.get("runtime_min"), "with_runtime.lte": constraints.get("runtime_max"),
              "with_origin_country": "|".join(constraints.get("countries") or []),
              "with_original_language": "|".join(constraints.get("original_languages") or [])}
    return {k: v for k, v in params.items() if v is not None and v != ""}


def _pages(read, path, params, count, truncation_warnings=None):
    for page in range(1, count + 1):
        response = read(path, {**params, "page": page})
        if not isinstance(response.get("results"), list):
            raise ValueError("无效片目列表")
        yield from response["results"]
        if page >= (response.get("total_pages") or 1):
            break
        if page == count and truncation_warnings is not None:
            truncation_warnings.append("片名检索命中较多，仅在已读取的候选中排序。")


def _filmography(read, kind, constraints, warnings, people):
    matched = None
    for field, role in (("actors", "cast"), ("directors", "crew"), ("people", "both")):
        for name in (people if field == "people" else constraints.get(field) or []):
            candidates = _pages(read, "/search/person", {"query": name, "include_adult": "false"}, 1)
            ids = {p["id"] for p in candidates if p.get("id") and name.casefold() in {
                str(p.get("name") or "").casefold(), str(p.get("original_name") or "").casefold()}}
            if len(ids) != 1:
                warnings.append(f"公开片目无法唯一确认人物“{name}”，可补充原名或作品名。")
                return {}
            data = read(f"/person/{ids.pop()}/{kind}_credits", {})
            credits = chain(data.get("cast", []), data.get("crew", [])) if role == "both" else data.get(role, [])
            works = {w["id"]: w for w in credits if w.get("id") and (role != "crew" or w.get("job") == "Director")}
            matched = works if matched is None else {i: row for i, row in matched.items() if i in works}
    return matched


def _keywords(read, terms):
    ids = []
    for term in terms[:3]:
        rows = _pages(read, "/search/keyword", {"query": term}, 1)
        found = next((r["id"] for r in rows if r.get("id") and str(r.get("name", "")).casefold() == term.casefold()), None)
        if found:
            ids.append(str(found))
    return "|".join(ids)


def _matches_record(read, item, raw, params, c, from_directory, keyword_ids=""):
    when, score = item["release_date"], item["rating"]
    date_field = _DATES[item["kind"]]
    if not _bounds(when or None, params.get(date_field + ".gte"), params.get(date_field + ".lte")):
        return False
    if not _bounds(score, c.get("min_rating"), c.get("max_rating")):
        return False
    if params.get("vote_count.gte") and (item["vote_count"] < params["vote_count.gte"] or score is None):
        return False
    if c.get("original_languages") and raw.get("original_language") not in c["original_languages"]:
        return False
    if from_directory:
        return True  # Remaining constraints were enforced by discover, before its sort and pagination.
    genres = {str(g) for g in raw.get("genre_ids") or []}
    for field, sep in (("with_genres", "|"), ("without_genres", ",")):
        if params.get(field):
            overlap = genres.intersection(params[field].split(sep))
            if not genres or bool(overlap) != (field == "with_genres"):
                return False
    if not keyword_ids and not any(c.get(k) for k in ("runtime_min", "runtime_max", "countries")):
        return True
    detail = read(f"/{item['kind']}/{item['tmdb_id']}", {"append_to_response": "keywords"} if keyword_ids else {})
    if keyword_ids:
        labels = detail.get("keywords") or {}
        values = labels.get("keywords") or labels.get("results") or []
        if not set(keyword_ids.split("|")).intersection(str(k.get("id")) for k in values):
            return False
    runtime = detail.get("runtime") if item["kind"] == "movie" else next(iter(detail.get("episode_run_time") or []), None)
    origins = detail.get("origin_country") or [x.get("iso_3166_1") for x in detail.get("production_countries") or []]
    return (_bounds(runtime or None, c.get("runtime_min"), c.get("runtime_max"))
            and (not c.get("countries") or bool(set(c["countries"]).intersection(origins))))


def _reference_name(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def _public_references(read, references, image_base):
    seeds = {}
    for value in dict.fromkeys(references or []):
        text = unicodedata.normalize("NFKC", value).strip()
        explicit_kind = None
        qualifier = re.match(r"^(电影|剧集|电视剧)(?:\s*:\s*|\s+|(?=《))", text)
        if qualifier:
            explicit_kind = "movie" if qualifier[1] == "电影" else "tv"
            text = text[qualifier.end():]
        kind_suffix = re.search(r"\((?:(\d{4})\s*[,/]\s*)?(电影|剧集|电视剧)\)$", text)
        if kind_suffix:
            explicit_kind = "movie" if kind_suffix[2] == "电影" else "tv"
            text = text[:kind_suffix.start()] + (" " + kind_suffix[1] if kind_suffix[1] else "")
        version = re.fullmatch(r"(.+?)(?:\s*\((\d{4})\)|\s+(\d{4}))", text)
        year = int(version[2] or version[3]) if version else None
        name = (version[1] if version else text).strip().strip("《》").strip()
        exact, options = {}, {}
        # Multi-search resolves the reference's own kind even when the requested
        # results have another kind. Never guess from a popularity-sorted first hit.
        for page in range(1, 4):
            data = read("/search/multi", {"query": name, "include_adult": "false", "page": page})
            if not isinstance(data.get("results"), list):
                raise ValueError("无效参考作品列表")
            for raw in data["results"]:
                if (not isinstance(raw, dict) or raw.get("media_type") not in _DATES
                        or explicit_kind and raw["media_type"] != explicit_kind):
                    continue
                candidate = _item(raw, raw["media_type"], image_base)
                if not candidate or year and candidate["year"] != year:
                    continue
                key = candidate["kind"], candidate["tmdb_id"]
                options[key] = candidate
                names = (candidate["title"], candidate["original_title"])
                if _reference_name(name) in {_reference_name(n) for n in names if n}:
                    exact[key] = candidate
            last = page >= int(data.get("total_pages") or 1)
            if last or len(exact) > 1:
                break
        if len(exact) != 1 or not last:
            choices = list((exact or options).values())[:5]
            labels = [f"{r['title']}（{r['year'] or '年份未知'}，{'电影' if r['kind']=='movie' else '剧集'}）"
                      for r in choices]
            question = f"请补充参考作品《{name}》的原名、年份或电影／剧集类型，以便在 TMDB 确认版本。"
            if labels:
                question += " 查到的作品有" + "、".join(labels) + "。"
            return {}, question
        seeds.update(exact)
    return seeds, ""


def _related_candidates(read, kind, seeds, c, genre_params, filmography, order, keyword_ids,
                        limit, today, entries, image_base, warnings):
    feeds = [(f"/{kind}/{seed_id}/{endpoint}", f"列入 TMDB《{seed['title']}》的{label}作品")
             for (seed_kind, seed_id), seed in seeds.items() if kind == seed_kind
             for endpoint, label in (("recommendations", "推荐"), ("similar", "相似"))]
    for page in range(1, 5):
        remaining = []
        for path, proof in feeds:
            data = read(path, {"page": page})
            if not isinstance(data.get("results"), list):
                raise ValueError("无效参考片推荐列表")
            if page < (data.get("total_pages") or 1):
                remaining.append((path, proof))
            for raw in data["results"]:
                item = _item(raw, kind, image_base)
                if not item or raw.get("media_type", kind) != kind:
                    continue
                key = kind, item["tmdb_id"]
                if key in seeds or filmography is not None and item["tmdb_id"] not in filmography:
                    continue
                if key in entries:
                    if proof not in entries[key]["evidence"]:
                        entries[key]["evidence"].append(proof)
                    continue
                years = c.get("years")
                if years and item["year"] not in years:
                    continue
                dates = _date_range(c, item["year"] if years else None, today)
                params = _directory_params(kind, c, genre_params, order, dates)
                # These relationship feeds accept no hard filters. Verify their
                # data as for a title-search hit, including any required detail.
                if _matches_record(read, item, raw, params, c, False, keyword_ids):
                    item["evidence"] = [proof]
                    item["people"] = list(dict.fromkeys([*(c.get("actors") or []), *(c.get("directors") or [])]))
                    entries[key] = item
        if not remaining or (order == "relevance" and len(entries) >= limit):
            return
        feeds = remaining
    warnings.append("相关片目较多，本次仅核实已读取的推荐和相似作品。")


def _find(read, kind, c, clues, order, limit, today, entries, warnings, image_base, allow_exploration,
          reference_seeds=None):
    genre_params = _genre_filters(read, kind, c)
    if genre_params is None:
        warnings.append("TMDB 的分类无法核实所选类型。")
        return
    ranked = order != "relevance"
    people = [p for p in clues.get("people") or [] if p not in (c.get("actors") or []) + (c.get("directors") or [])]
    titles = list(dict.fromkeys(clues.get("titles") or []))[:3]
    filmography = _filmography(read, kind, c, warnings, people if ranked or not titles and not reference_seeds else [])
    if ranked and titles and not reference_seeds:
        warnings.append("榜单范围为 TMDB 片名和别名检索命中的作品。")
    terms = clues.get("terms") or []
    keyword_ids = _keywords(read, terms) if terms and (ranked or filmography is None and not titles and not reference_seeds) else ""
    if ranked and terms and not keyword_ids:
        warnings.append("TMDB 未能核实榜单的主题关键词，可调整描述或改查本库。")
        return
    if reference_seeds:
        if filmography != {}:
            _related_candidates(read, kind, reference_seeds, c, genre_params, filmography, order, keyword_ids,
                                limit, today, entries, image_base, warnings)
        return
    if terms and not keyword_ids and filmography is None and not titles and not allow_exploration:
        warnings.append("TMDB 未找到对应的主题关键词，可提供片名、人物或改查本库。")
        return
    for year in c.get("years") or [None]:
        dates = _date_range(c, year, today)
        if dates[0] and dates[1] and dates[0] > dates[1]:
            continue
        params = _directory_params(kind, c, genre_params, order, dates)
        if titles:
            batches = [_pages(read, f"/search/{kind}", {"query": title, "include_adult": "false"},
                              16 if ranked else 1, warnings if ranked else None) for title in titles]
        elif filmography is not None:
            batches = [filmography.values()]
        else:
            path = f"/discover/{kind}"
            batches = []
            for language, country in product(c.get("original_languages") or [""], c.get("countries") or [""]):
                params_for_scope = dict(params)
                if language:
                    params_for_scope["with_original_language"] = language
                if country:
                    params_for_scope["with_origin_country"] = country
                if keyword_ids:
                    batches.append(_pages(read, path, {**params_for_scope, "with_keywords": keyword_ids}, (limit + 19) // 20))
                if not keyword_ids or allow_exploration and not ranked:
                    batches.append(_pages(read, path, params_for_scope, (limit + 19) // 20))
        for batch in batches:
            for raw in batch:
                item = _item(raw, kind, image_base)
                if not item or filmography is not None and item["tmdb_id"] not in filmography:
                    continue
                key = kind, item["tmdb_id"]
                if key not in entries and _matches_record(read, item, raw, params, c,
                                                          not titles and filmography is None, keyword_ids if ranked else ""):
                    item["people"] = list(dict.fromkeys([*(c.get("actors") or []), *(c.get("directors") or [])]))
                    entries[key] = item


def discover_public(kinds, constraints, clues, order, limit, today=None, allow_exploration=False, references=None):
    today = today or date.today()
    limit = min(100, max(1, limit))
    c = {k: v for k, v in constraints.items() if v is not None and v != [] and v != ""}
    unsupported = [_UNAVAILABLE[key] for key in c if key in _UNAVAILABLE]
    if any(kind not in _DATES for kind in kinds):
        unsupported.append("季集片目")
    if c.get("rating_source", "tmdb") != "tmdb":
        unsupported.append("指定评分来源")
    if order.startswith("added"):
        unsupported.append("入库排序")
    if unsupported:
        return _result(warnings=["公开片目无法核实" + "、".join(dict.fromkeys(unsupported)) + "。可改查当前片库或调整要求。"])
    if not (config.effective_tmdb_read_token() or config.effective_tmdb_api_key()):
        raise AiUnavailable("tmdb_not_configured", "查看公开片目需要先在设置的在线资料服务中配置 TMDB。")

    deadline = time.monotonic() + 12
    image_base = config.effective_tmdb_image_base().rstrip("/")
    entries, warnings = {}, []
    calls = 0
    try:
        with tmdb._client() as client:
            def read(path, parameters):
                nonlocal calls
                calls += 1
                if calls > 16:
                    raise TimeoutError
                return tmdb.catalog_page(client, path, parameters, deadline)

            seeds, question = _public_references(read, references, image_base)
            if question:
                return {**_result(), "clarification": question}
            seed_kinds = {kind for kind, _ in seeds}
            if seeds and seed_kinds - set(kinds):
                return _result(warnings=["TMDB 的参考片推荐只覆盖同类作品，无法核实电影和剧集之间的相似关系。"])
            if seeds and set(kinds) - seed_kinds:
                warnings.append("TMDB 参考片推荐只覆盖与参考作品相同的电影或剧集类型。")
            if seeds and order != "relevance":
                warnings.append("本次仅对 TMDB 返回的参考片推荐和相似作品排序。")
            for kind in kinds:
                if not seeds or kind in seed_kinds:
                    _find(read, kind, c, clues, order, limit, today, entries, warnings, image_base,
                          allow_exploration, seeds)
    except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError):
        if not entries:
            raise AiUnavailable("tmdb_unavailable", "TMDB 公开片目暂时不可用，可稍后重试或搜索当前片库。") from None
        warnings.append("公开资料服务只返回了部分候选。")
    items = list(entries.values())
    if order.startswith("rating_"):
        items.sort(key=lambda i: (i["rating"] if order == "rating_asc" else -i["rating"], -i["vote_count"], i["tmdb_id"]))
        warnings.append("TMDB 评分榜仅计入至少 100 票的作品。")
    elif order.startswith("year_"):
        items = sorted((i for i in items if i["release_date"]), key=lambda i: i["release_date"], reverse=order == "year_desc")
    return _result(items[:limit], warnings)
