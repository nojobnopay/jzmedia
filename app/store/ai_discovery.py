"""Read-only evidence for natural-language discovery in the current viewing scope."""
from collections import defaultdict, deque
from datetime import date, datetime
import json
import re
import sqlite3
import unicodedata

from ..playback_completion import is_playback_complete
from ._base import _conn, _like_esc, _lock
from .progress import CONTINUE_MIN_POSITION
from .search import _fts_query, _query_terms, _structured_where
from .tv import parse_episode_credits, parse_season_cast, tv_status_bucket


def _strings(value):
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value or [] if v is not None and str(v).strip()]


def _json(raw, default):
    if isinstance(raw, type(default)):
        return raw
    try:
        data = json.loads(raw or "null")
    except (ValueError, TypeError):
        return default
    return data if isinstance(data, type(default)) else default


def _norm(value):
    return "".join(c for c in unicodedata.normalize("NFKC", str(value or "")).casefold() if c.isalnum())


def _names(raw):
    return [s.strip() for s in re.split(r"[,，、|]", raw or "") if s.strip()]


def _text_list(raw):
    return [s.strip() for s in _json(raw, []) if isinstance(s, str) and s.strip()]


# A Chinese audio track without a dialect tag does not establish Mandarin/Cantonese.
_LANGS = {
    "zh": ("zh", "zho", "chi", "中文", "汉语", "chinese"),
    "cmn": ("cmn", "zh-cmn", "mandarin", "普通话", "国语", "国配"),
    "yue": ("yue", "zh-yue", "cantonese", "粤语", "廣東話", "广东话"),
    "en": ("en", "eng", "english", "英语"), "ja": ("ja", "jpn", "japanese", "日语"),
    "ko": ("ko", "kor", "korean", "韩语"), "fr": ("fr", "fra", "fre", "法语", "french"),
    "de": ("de", "deu", "ger", "德语"), "es": ("es", "spa", "西班牙语"),
    "it": ("it", "ita", "意大利语"), "ru": ("ru", "rus", "俄语"),
    "pt": ("pt", "por", "葡萄牙语"), "hi": ("hi", "hin", "印地语"),
}
_LANGUAGE_CODES = {alias.casefold(): code for code, aliases in _LANGS.items() for alias in aliases}


def _language(value):
    raw = str(value or "").strip().lower().replace("_", "-")
    return _LANGUAGE_CODES.get(raw, _LANGUAGE_CODES.get(raw.split("-")[0], raw.split("-")[0]))


def _track_languages(raw):
    languages = set()
    for track in _json(raw, []):
        if not isinstance(track, dict):
            continue
        code = _language(track.get("lang"))
        if code and code not in {"und", "unk", "mul"}:
            languages.add(code)
        label = str(track.get("title") or "").casefold()
        for key in ("cmn", "yue"):
            if any(word in label for word in _LANGS[key][2:]):
                languages.add(key)
    if languages & {"cmn", "yue"}:
        languages.add("zh")
    return sorted(languages)


def _watch(record):
    if record["watched"] or is_playback_complete(record["position"], record["progress_duration"]):
        return "watched"
    return "in_progress" if (record["position"] or 0) >= CONTINUE_MIN_POSITION else "unseen"


def _scope_sql(alias, scope):
    return (f" JOIN libraries l ON l.id={alias}.library_id AND l.enabled=1"
            " JOIN media_libraries ml ON ml.id=l.media_library_id AND ml.enabled=1",
            f"{alias}.library_id IN (" + ",".join("?" for _ in scope) + ")")


def _works(c, scope, kind, criteria):
    table = "movies" if kind == "movie" else "tv_shows"
    joins, where = _scope_sql("w", scope)
    details = ("w.douban_rating,w.watched,t.premiered AS release_date,t.runtime,"
               "p.position,p.duration AS progress_duration,mi.duration AS file_duration,"
               "mi.audio_json,mi.sub_json" if kind == "movie" else
               "w.first_air_date AS release_date,w.episode_run_time AS runtime,"
               "w.status,w.created_by,t.credits AS credits")
    if kind == "movie":
        joins += (" LEFT JOIN playback_progress p ON p.kind='movie' AND p.item_id=w.id"
                  " LEFT JOIN media_info mi ON mi.kind='movie' AND mi.item_id=w.id AND mi.playable=1")
    where += " AND l.kind=?"
    params = [kind, kind, *scope, kind]
    # Follow-ups and public catalogue lookups normally rehydrate a handful of
    # identities. Restrict the SQL reader before materializing long synopses.
    ids = criteria.get("tmdb_ids")
    if ids:
        where += " AND w.tmdb_id IN (" + ",".join("?" for _ in ids) + ")"
        params.extend(ids)
    local_ids = criteria.get("ids")
    if local_ids:
        marks = ",".join("?" for _ in local_ids)
        where += (f" AND (w.id IN ({marks}) OR w.tmdb_id IN (SELECT tmdb_id FROM {table} WHERE id IN ({marks}))"
                  f" OR (w.tmdb_id IS NULL AND w.imdb_id!='' AND w.imdb_id IN"
                  f" (SELECT imdb_id FROM {table} WHERE id IN ({marks}))))")
        params.extend([*local_ids, *local_ids, *local_ids])
    # Require a visible work before touching aliases or the shared metadata cache.
    sql = f"""SELECT w.id,w.library_id,ml.id AS media_library_id,w.tmdb_id,w.imdb_id,
        w.title,w.original_title,w.year,w.poster_path,
        COALESCE(NULLIF(w.overview_override,''),w.overview,'') AS overview,
        w.genres,w.tags,w.origin_countries,w.origin_country,w.region,w.original_language,
        w.tmdb_rating,w.custom_rating,w.person_names,w.added_at,
        json_extract(CASE WHEN json_valid(w.content_evidence) THEN w.content_evidence
                     ELSE '{{}}' END,'$.keywords') AS keywords,
        (SELECT GROUP_CONCAT(a.alt_titles,char(10)) FROM match_index a
         WHERE a.kind=? AND a.tmdb_id=w.tmdb_id AND a.alt_titles!='') AS aliases,
        {details} FROM {table} w {joins}
        LEFT JOIN tmdb_cache t ON t.media_type=? AND t.tmdb_id=w.tmdb_id
        WHERE {where} ORDER BY w.id"""
    rows = [dict(row) for row in c.execute(sql, params)]
    # IMDb-only entries can carry NFO/provider aliases as well. Match just the
    # visible external IDs in one pass; this index has no per-IMDb SQL index.
    imdb_ids = sorted({row["imdb_id"] for row in rows if not row["tmdb_id"] and row["imdb_id"]})
    if imdb_ids:
        marks = ",".join("?" for _ in imdb_ids)
        aliases = dict(c.execute(
            "SELECT imdb_id,GROUP_CONCAT(alt_titles,char(10)) FROM match_index "
            f"WHERE kind=? AND imdb_id IN ({marks}) AND alt_titles!='' GROUP BY imdb_id", (kind, *imdb_ids)))
        for row in rows:
            if not row["tmdb_id"]:
                row["aliases"] = aliases.get(row["imdb_id"], "")
    return rows


def _movie_credits(c, scope, subset=None):
    _, where = _scope_sql("w", scope)
    args = list(scope)
    if subset is not None:
        if not subset:
            return defaultdict(list)
        where += " AND w.id IN (" + ",".join("?" for _ in subset) + ")"
        args.extend(subset)
    sql = f"""SELECT mp.movie_id,mp.role,p.name,mp.character_name
        FROM movies w JOIN movie_person mp ON mp.movie_id=w.id
        JOIN persons p ON p.id=mp.person_id WHERE {where}
        ORDER BY mp.movie_id,mp.cast_order"""
    out = defaultdict(list)
    for mid, role, name, character in c.execute(sql, args):
        out[mid].append((role, name, character))
    return out


def _episodes(c, scope, subset=None):
    joins, where = _scope_sql("e", scope)
    args = list(scope)
    if subset is not None:
        if not subset:
            return defaultdict(list)
        where += " AND e.show_id IN (" + ",".join("?" for _ in subset) + ")"
        args.extend(subset)
    rows = c.execute(f"""SELECT e.id,e.show_id,e.season,e.episode,e.episode_end,e.absolute_number,
        e.tmdb_episode_id,
        e.title,e.overview,e.air_date,e.runtime,e.tmdb_rating,e.watched,e.added_at,
        e.still_path,e.episode_credits,p.position,p.duration AS progress_duration,
        mi.duration AS file_duration,mi.audio_json,mi.sub_json
        FROM tv_episodes e {joins}
        LEFT JOIN playback_progress p ON p.kind='episode' AND p.item_id=e.id
        LEFT JOIN media_info mi ON mi.kind='episode' AND mi.item_id=e.id AND mi.playable=1
        WHERE {where} AND COALESCE(e.missing,0)=0 ORDER BY e.show_id,e.season,e.episode,e.id""", args)
    out = defaultdict(list)
    for row in rows:
        episode = dict(row)
        episode["episode_credits"] = parse_episode_credits(episode["episode_credits"])
        out[row["show_id"]].append(episode)
    return out


def _file(record, kind):
    measured_runtime = (record["file_duration"] or 0) / 60 or None
    return {
        "id": record["id"], "kind": kind, "watch_state": _watch(record), "season": record.get("season"),
        "show_id": record.get("show_id"), "episode": record.get("episode"), "episode_end": record.get("episode_end"),
        "library_id": record.get("library_id"), "media_library_id": record.get("media_library_id"),
        "runtime": measured_runtime or record.get("runtime") or None, "measured_runtime": measured_runtime,
        "audio_languages": _track_languages(record["audio_json"]),
        "subtitle_languages": _track_languages(record["sub_json"]),
    }


def _work(row, kind, credits):
    item = {key: row.get(key) for key in (
        "id", "library_id", "media_library_id", "tmdb_id", "imdb_id", "title", "original_title",
        "year", "poster_path", "overview", "region", "original_language", "tmdb_rating",
        "douban_rating", "custom_rating", "added_at", "release_date", "runtime", "status")}
    item.update(kind=kind, title=row["title"] or row["original_title"] or "", original_title=row["original_title"] or "",
                detail_path=("/m/" if kind == "movie" else "/tv/") + str(row["id"]),
                genres=_text_list(row["genres"]), tags=_text_list(row["tags"]),
                keywords=_text_list(row["keywords"]), aliases=(row["aliases"] or "").splitlines(),
                countries=sorted(set(_text_list(row["origin_countries"]) + _strings(row["origin_country"]))),
                people=_names(row["person_names"]), actors=[], directors=[], characters=[],
                evidence=[], retrieval_score=0, _files=[])
    for role, name, character in credits:
        if name and name not in item["people"]:
            item["people"].append(name)
        if role in ("actor", "director") and name:
            item[role + "s"].append(name)
        if character:
            item["characters"].append(character)
    if kind == "tv":
        for person in _json(row.get("credits"), {}).get("cast") or []:
            if isinstance(person, dict) and person.get("name"):
                item["actors"].append(person["name"])
                if person.get("character"):
                    item["characters"].append(person["character"])
        item["creators"] = [p["name"] for p in _json(row.get("created_by"), [])
                            if isinstance(p, dict) and p.get("name")]
        item["people"] = list(dict.fromkeys(item["people"] + item["actors"] + item["creators"]))
    return item


def _episode_work(show, ep):
    item = {**show, "kind": "episode", "id": ep["id"], "show_id": show["id"],
            "show_title": show["title"], "show_tmdb_id": show["tmdb_id"], "tmdb_id": ep["tmdb_episode_id"],
            "imdb_id": "", "season": ep["season"], "episode": ep["episode"],
            "episode_end": ep["episode_end"], "absolute_number": ep["absolute_number"],
            "detail_path": f"/tv/{show['id']}/s/{ep['season']}/e/{ep['id']}",
            "title": ep["title"] or f"{show['title']} S{ep['season']:02}E{ep['episode']:02}",
            "original_title": "", "overview": ep["overview"] or "",
            "release_date": ep["air_date"], "year": _year(ep["air_date"]),
            "added_at": ep["added_at"], "runtime": ep["runtime"] or None,
            "poster_path": ep["still_path"] or show["poster_path"], "tmdb_rating": ep["tmdb_rating"],
            "custom_rating": None, "directors": [], "actors": [], "characters": [], "people": [],
            "_files": [_file(ep, "episode")], "watch_state": _watch(ep)}
    return item


def _add_tv_credits(item, episodes, seasons=()):
    """Keep series cast, season regulars, episode guests and directors attributable."""
    roles = {"actors": {}, "directors": {}}
    for actor in item["actors"]:
        roles["actors"][actor] = f"{actor}列于剧集演员表"
    characters = list(item["characters"])
    for sn, meta in seasons:
        for person in parse_season_cast(meta.get("cast")):
            if isinstance(person, dict) and person.get("name"):
                name = person["name"]
                roles["actors"].setdefault(name, f"{name}列于第{sn}季常驻演员表")
                if person.get("character"):
                    characters.append(person["character"])
    for ep in episodes:
        place = f"第{ep['season']}季第{ep['episode']}集"
        for source, role, label in (("guests", "actors", "客串"), ("directors", "directors", "担任分集导演")):
            for person in ep["episode_credits"].get(source, []):
                name = person.get("name")
                if name:
                    roles[role].setdefault(name, f"{place}，{name}{label}")
                    if person.get("character"):
                        characters.append(person["character"])
    item["_roles"] = roles
    item["actors"], item["directors"] = list(roles["actors"]), list(roles["directors"])
    item["characters"] = list(dict.fromkeys(characters))
    item["people"] = list(dict.fromkeys(item["people"] + item["directors"] + item["actors"]))


def _year(raw):
    return int(str(raw)[:4]) if raw and str(raw)[:4].isdigit() else None


def _identity(item):
    return (item["kind"], item.get("tmdb_id") or item.get("imdb_id") or ("local", item["id"]))


def _aggregate_watch(files):
    states = {f["watch_state"] for f in files}
    if "watched" in states:
        return "watched"
    return "in_progress" if "in_progress" in states else "unseen"


def _show_watch(episodes):
    # One row may cover multiple episodes; a second version must not cancel the first.
    seen = set()
    all_numbers = set()
    started = False
    for ep in episodes:
        first = ep["episode"]
        last = max(first, ep["episode_end"] or 0)
        # Keep ranges instead of expanding untrusted episode numbers.
        key = (ep["season"], first, last)
        all_numbers.add(key)
        state = ep["watch_state"] if "watch_state" in ep else _watch(ep)
        if state == "watched":
            seen.add(key)
        started |= state != "unseen"
    covered = defaultdict(list)
    for season, start, end in sorted(seen):
        merged = covered[season]
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    if all_numbers and all(any(a <= start and b >= end for a, b in covered[season])
                           for season, start, end in all_numbers):
        return "watched"
    return "in_progress" if started else "unseen"


def _seasons(c, scope):
    _, where = _scope_sql("s", scope)
    return {(row["show_id"], row["season"]): dict(row) for row in c.execute(
        f"SELECT s.show_id,s.season,s.name,s.overview,s.air_date,s.poster_path,"
        f"s.tmdb_season_id,s.cast FROM tv_seasons s WHERE {where}", scope)}


def _season_work(show, sn, episodes, meta):
    # A season also exists when only its local files have been scanned. Anchor its
    # typed result ID to an actual episode and use (show_id, season) for routing.
    title = meta.get("name") or f"第{sn}季"
    premiere = meta.get("air_date")
    item = {**show, "kind": "season", "id": min(e["id"] for e in episodes),
            "show_id": show["id"], "season": sn, "show_title": show["title"],
            "show_tmdb_id": show["tmdb_id"], "tmdb_id": meta.get("tmdb_season_id"), "imdb_id": "",
            "title": f"{show['title']} · {title}", "original_title": "", "overview": meta.get("overview") or "",
            "year": _year(premiere), "release_date": premiere, "tmdb_rating": None, "custom_rating": None,
            "added_at": min(e["added_at"] for e in episodes), "poster_path": meta.get("poster_path") or show["poster_path"],
            "actors": [], "directors": [], "characters": [], "people": [],
            "detail_path": f"/tv/{show['id']}/s/{sn}", "watch_state": _show_watch(episodes),
            "_files": [_file(ep, "episode") for ep in episodes]}
    _add_tv_credits(item, episodes, [(sn, meta)])
    return item


def _load(c, scope, kinds, criteria):
    items = []
    narrow = bool(criteria.get("ids") or criteria.get("tmdb_ids"))
    if "movie" in kinds:
        grouped = defaultdict(list)
        movies = _works(c, scope, "movie", criteria)
        credits = _movie_credits(c, scope, [row["id"] for row in movies] if narrow else None)
        for row in movies:
            item = _work(row, "movie", credits[row["id"]])
            item["_files"] = [_file(row, "movie")]
            grouped[_identity(item)].append(item)
        for versions in grouped.values():
            primary = versions[0]
            files = [f for v in versions for f in v["_files"]]
            primary.update(_files=files, watch_state=_aggregate_watch(files), version_count=len(files))
            if not primary["runtime"]:
                primary["runtime"] = next((f["runtime"] for f in files if f["runtime"]), None)
            for key in ("genres", "tags", "people", "actors", "directors", "characters", "keywords"):
                primary[key] = list(dict.fromkeys(x for v in versions for x in v[key]))
            primary["aliases"] = list(dict.fromkeys(x for v in versions for x in
                                         [v["title"], v["original_title"], *v["aliases"]] if x))
            for key in ("tmdb_rating", "custom_rating", "douban_rating"):
                primary[key] = max((v[key] for v in versions if v[key] is not None), default=None)
            items.append(primary)
    if set(kinds) & {"tv", "episode", "season"}:
        # Season and episode identity namespaces are independent of their parent.
        shows = _works(c, scope, "tv", {} if kinds & {"season", "episode"} else criteria)
        episodes = _episodes(c, scope, [row["id"] for row in shows] if narrow and kinds == {"tv"} else None)
        seasons = _seasons(c, scope) if kinds & {"season", "tv"} else {}
        grouped_shows = defaultdict(list)
        for row in shows:
            show = _work(row, "tv", [])
            eps = episodes[row["id"]]
            if "episode" in kinds:
                by_number = defaultdict(list)
                for ep in eps:
                    by_number[(ep["season"], ep["episode"], max(ep["episode_end"] or 0, ep["episode"]))].append(ep)
                for versions in by_number.values():
                    item = _episode_work(show, versions[0])
                    _add_tv_credits(item, versions)
                    item["_files"] = [_file(ep, "episode") for ep in versions]
                    item["watch_state"] = _aggregate_watch(item["_files"])
                    items.append(item)
            if "season" in kinds:
                for sn in sorted({e["season"] for e in eps}):
                    subset = [e for e in eps if e["season"] == sn]
                    items.append(_season_work(show, sn, subset, seasons.get((show["id"], sn), {})))
            if "tv" in kinds:
                meta = [(sn, seasons.get((show["id"], sn), {})) for sn in sorted({e["season"] for e in eps})]
                _add_tv_credits(show, eps, meta)
                show["_files"] = [_file(ep, "episode") for ep in eps]
                grouped_shows[_identity(show)].append(show)
        for versions in grouped_shows.values():
            primary = versions[0]
            files = [file for v in versions for file in v["_files"]]
            primary.update(_files=files, _ids=[v["id"] for v in versions], watch_state=_show_watch(files))
            if len(versions) > 1:
                for role in ("directors", "actors"):
                    for v in versions[1:]:
                        primary["_roles"][role].update(v["_roles"][role])
                    primary[role] = list(primary["_roles"][role])
                primary["people"] = list(dict.fromkeys(x for v in versions for x in v["people"]))
            items.append(primary)
    return items


def _inside(actual, low, high):
    if low is None and high is None:
        return True
    if actual is None or actual == "":
        return None
    return (low is None or actual >= low) and (high is None or actual <= high)


def _matches(values, wanted, *, every=False, negate=False):
    wanted = {_norm(x) for x in _strings(wanted)}
    if not wanted:
        return True
    present = {_norm(x) for x in _strings(values) if _norm(x)}
    if not present:
        return None
    if negate:
        return not bool(present & wanted)
    return wanted <= present if every else bool(present & wanted)


def _date_from_timestamp(timestamp):
    try:
        return datetime.fromtimestamp(timestamp).date().isoformat() if timestamp else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _valid_date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        return None


def _released_in(item, low, high):
    known = _valid_date(item["release_date"])
    if known or not item["year"] or not (low or high):
        return _inside(known, low, high)
    # If only the year is known, a complete-year interval can still be proven.
    first, last = f"{item['year']:04}-01-01", f"{item['year']:04}-12-31"
    if (low and last < low) or (high and first > high):
        return False
    return True if (not low or first >= low) and (not high or last <= high) else None


def _wall_matches(c, scope, kinds, q, wall):
    """Apply the existing movie FTS/LIKE and TV title/cast word-AND semantics."""
    matches = defaultdict(set)
    for kind, table in (("movie", "movies"), ("tv", "tv_shows")):
        wanted = {"movie"} if kind == "movie" else {"tv", "episode", "season"}
        if not kinds & wanted:
            continue
        joins, where = _scope_sql("w", scope)
        params = list(scope)
        if kind == "movie":
            sql, values = _structured_where("w", genres=wall.get("genres"), regions=wall.get("regions"),
                     countries=wall.get("countries"), years=wall.get("years"), tags=wall.get("tags"),
                     min_rating=wall.get("min_rating"), rating_source=wall.get("rating_source"))
            where += " AND (" + sql + ")"
            params.extend(values)
            fts = _fts_query(q)
            if fts:
                try:
                    matches[kind].update(r[0] for r in c.execute(
                        f"SELECT w.id FROM movies_fts f JOIN movies w ON w.id=f.rowid {joins}"
                        f" WHERE movies_fts MATCH ? AND ({where})", (fts, *params)))
                except sqlite3.OperationalError:
                    pass  # The ordinary movie wall also falls back to substring search.
            if matches[kind]:
                continue
        tokens = _query_terms(q) or ([q.strip()] if kind == "tv" else [])
        if not tokens:
            continue
        for token in tokens:
            where += " AND (" + " OR ".join("w." + col + " LIKE ? ESCAPE '\\'" for col in
                                            ("title", "original_title", "person_names")) + ")"
            params.extend(["%" + _like_esc(token) + "%"] * 3)
        matches[kind].update(r[0] for r in c.execute(f"SELECT w.id FROM {table} w {joins} WHERE {where}", params))
    return matches


def _check(value_list):
    if any(v is False for v in value_list):
        return False
    return None if any(v is None for v in value_list) else True


def _work_checks(item, criteria):
    checks = []
    if criteria.get("ids"):
        ids = {item["id"], *item.get("_ids", [])}
        if item["kind"] == "movie":
            ids.update(f["id"] for f in item["_files"])
        checks.append(bool(ids & set(criteria["ids"])))
    if criteria.get("tmdb_ids"):
        checks.append(item["tmdb_id"] in criteria["tmdb_ids"])
    if item["id"] in (criteria.get("exclude_ids") or {}).get(item["kind"], []):
        return False
    for field, target, all_values, negate in (
        ("genres", "genres", False, False), ("exclude_genres", "genres", False, True),
        ("countries", "countries", False, False), ("regions", "region", False, False),
        ("tags", "tags", True, False), ("exclude_tags", "tags", False, True),
        ("actors", "actors", True, False), ("directors", "directors", True, False),
        ("years", "year", False, False)):
        if criteria.get(field):
            values = item.get(target)
            if target == "year":
                values = [values] if values else []
            checks.append(_matches(values, criteria[field], every=all_values, negate=negate))
    if criteria.get("original_languages"):
        lang = _language(item["original_language"])
        langs = [lang, "zh"] if lang in {"cmn", "yue"} else [lang]
        checks.append(_matches(langs, [_language(x) for x in criteria["original_languages"]]))
    checks.extend((
        _inside(item["year"], criteria.get("year_min"), criteria.get("year_max")),
        _released_in(item, criteria.get("date_from"), criteria.get("date_to")),
        _inside(_date_from_timestamp(item["added_at"]), criteria.get("added_from"), criteria.get("added_to")),
        _inside(item["rating"], criteria.get("min_rating"), criteria.get("max_rating"))))
    if criteria.get("watch_state"):
        desired = criteria["watch_state"]
        state = item["watch_state"]
        checks.append(state != "watched" if desired == "unfinished" else state == desired)
    if criteria.get("status"):
        checks.append(_matches([tv_status_bucket(item["status"])] if item["status"] else [],
                               criteria["status"]))
    for field in ("season", "absolute_number", "episode"):
        desired = criteria.get(field)
        if desired is None:
            continue
        value = item.get(field)
        if field == "episode" and value is not None:
            checks.append(value <= desired <= max(value, item.get("episode_end") or value))
        else:
            checks.append(value == desired)
    return _check(checks)


def _check_files(item, criteria):
    audio = [_language(x) for x in _strings(criteria.get("audio_languages"))]
    subtitles = [_language(x) for x in _strings(criteria.get("subtitle_languages"))]
    low, high = criteria.get("runtime_min"), criteria.get("runtime_max")
    needs_runtime = low is not None or high is not None
    if not (audio or subtitles or needs_runtime):
        return True
    files = item["_files"]
    if item["kind"] in ("tv", "season") and not (audio or subtitles):
        # A show runtime describes a typical single episode, never the entire series.
        return _inside(item["runtime"], low, high)
    unknown = not files
    for file in files:
        runtime = file["measured_runtime"] if needs_runtime and (audio or subtitles) else file["runtime"]
        passed = _check([_inside(runtime, low, high),
                         _matches(file["audio_languages"], audio, every=True),
                         _matches(file["subtitle_languages"], subtitles, every=True)])
        if passed:
            item["runtime"] = runtime or item["runtime"]
            item["audio_languages"] = file["audio_languages"]
            item["subtitle_languages"] = file["subtitle_languages"]
            item["version_id"] = file["id"]
            item["matched_version_id"] = file["id"]
            if item["kind"] == "movie":
                item["detail_path"] = "/m/" + str(file["id"])
                item["library_id"] = file["library_id"]
                item["media_library_id"] = file["media_library_id"]
            elif item["kind"] == "episode":
                item["detail_path"] = f"/tv/{item['show_id']}/s/{file['season']}/e/{file['id']}"
            elif item["kind"] in ("tv", "season"):
                item["matched_episode_id"] = file["id"]
                item["matched_episode_path"] = f"/tv/{file['show_id']}/s/{file['season']}/e/{file['id']}"
            return True
        unknown |= passed is None
    return None if unknown else False


def _terms(clues):
    if isinstance(clues, dict):
        return {key: [_norm(str(x)) for x in _strings(clues.get(key)) if _norm(x)]
                for key in ("titles", "people", "terms")}
    return _terms({"terms": clues})


def _titles(item):
    return {_norm(t) for t in [item["title"], item["original_title"], item.get("show_title"),
                               *item["aliases"]] if t}


def _references(items, references):
    selected = []
    for ref in references or []:
        name = str(ref.get("title") if isinstance(ref, dict) else ref).strip()
        year = ref.get("year") if isinstance(ref, dict) else None
        match = re.fullmatch(r"(.+?)(?:\s*[（(](\d{4})[）)]|\s+(\d{4}))", name)
        if match:
            name, year = match[1].strip(), int(match[2] or match[3])
        title = _norm(name)
        pool = [r for r in items if r["kind"] in ("movie", "tv") and title
                and title in _titles(r) and (not year or year == r["year"])]
        if len(pool) > 1:
            options = [f"{r['title']}（{r['year'] or '年份未知'}，{'电影' if r['kind']=='movie' else '剧集'}）"
                       for r in pool[:6]]
            return [], f"你指的是哪一部《{name.strip('《》')}》？" + "、".join(options)
        if len(pool) == 1:
            selected.append(pool[0])
    return selected, ""


def _score(item, clues, seeds, scene_fragments):
    if not any(clues.values()) and not seeds:
        return
    title_set = _titles(item)
    people = [_norm(x) for x in item["people"] + item["characters"]]
    concepts = [_norm(x) for x in item["genres"] + item["tags"] + item["keywords"]]
    overview = _norm(item["overview"])
    signals = {}
    for term in clues["titles"]:
        if term in title_set:
            signals["片名吻合"] = 160
        elif any(term in name for name in title_set):
            signals["片名相近"] = 55
    for term in clues["people"] + clues["terms"]:
        if any(term in name for name in people):
            signals["演职员或角色吻合"] = 40
        if any(term in name for name in title_set):
            signals["片名线索"] = 25
        if any(term in name for name in concepts):
            signals["类型或主题吻合"] = min(32, signals.get("类型或主题吻合", 0) + 8)
        if term in overview:
            signals["剧情线索吻合"] = min(36, signals.get("剧情线索吻合", 0) + 12)
    if overview and not signals.get("剧情线索吻合"):
        for fragments in scene_fragments:
            # Tolerate intervening words, e.g. "失眠女司机" and "失眠的女司机".
            common = sum(part in overview for part in fragments)
            if common >= 2 and common * 5 >= len(fragments) * 3:
                signals["剧情含相关用词"] = min(12, signals.get("剧情含相关用词", 0) + common)
    for seed in seeds:
        shared_crew = set(seed["directors"]) & set(item["directors"])
        if shared_crew:
            signals["同导演"] = 22
        if set(seed["tags"] + seed["keywords"]) & set(item["tags"] + item["keywords"]):
            signals["与参考作品有共同主题"] = 15
        if set(seed["genres"]) & set(item["genres"]):
            signals["与参考作品类型相近"] = 6
    item["evidence"] = list(signals)
    item["retrieval_score"] = sum(signals.values())


def _date_order(r):
    return r["release_date"] or (str(r["year"]) if r["year"] else "")


def _rank(items, order, limit):
    if order == "relevance":
        items.sort(key=lambda r: (-r["retrieval_score"], -(r["rating"] or 0), r["kind"], r["id"]))
        if len(items) <= limit:
            return items
        # Preserve relevant hits first. Fill the weakly evidenced tail with synopses
        # from different periods and genres, including unscored, older works.
        hits = [item for item in items if item["retrieval_score"] > 0][:limit]
        if len(hits) == limit:
            return hits
        remainder = [r for r in items if r["retrieval_score"] == 0]
        groups = defaultdict(deque)
        for r in sorted(remainder, key=lambda r: not bool(r["overview"])):
            groups[(r["kind"], (r["year"] or 0) // 10, next(iter(r["genres"]), ""))].append(r)
        tail = []
        while groups and len(hits) + len(tail) < limit:
            for key in list(groups):
                rows = groups[key]
                tail.append(rows.popleft())
                if not rows:
                    del groups[key]
                if len(hits) + len(tail) >= limit:
                    break
        return hits + tail
    field = order.rsplit("_", 1)[0]
    key = {"rating": lambda r: r["rating"], "year": lambda r: r["year"],
           "release": _date_order, "added": lambda r: r["added_at"],
           "title": lambda r: r["title"].casefold()}.get(field)
    if key is None:
        raise ValueError("unsupported discovery order")
    # The tie break stays deterministic for both ascending and descending rankings.
    items.sort(key=lambda r: (r["title"].casefold(), r["kind"], r["id"]))
    known = [r for r in items if key(r) is not None and key(r) != ""]
    known.sort(key=key, reverse=order.endswith("_desc"))
    return known[:limit]


def discover_local(library_ids, *, kinds=None, constraints=None, clues=None, references=None,
                   order="relevance", limit=10, today=None, explore=False):
    """Find local evidence. Only visible, enabled library rows may reach the caller.

    The planner resolves relative dates into ISO bounds before calling. No external
    requests, file inspection or model calls occur in this function. All hard
    conditions and grouping run before capping results.
    """
    scope = list(dict.fromkeys(int(i) for i in library_ids or [] if int(i) > 0))
    criteria = {key: val for key, val in (constraints or {}).items() if val is not None}
    kinds = set(_strings(kinds if kinds is not None else ["movie", "tv"]))
    limit = max(0, int(limit))
    empty = {"items": [], "total": 0, "excluded_unknown": 0}
    if not scope or not kinds or not limit:
        return empty
    wall = criteria.get("_wall") or {}
    wall_q = (criteria.get("_wall_q") or "").strip()
    source = criteria.get("rating_source", "tmdb")
    if source not in ("tmdb", "douban", "custom"):
        raise ValueError("unsupported rating source")
    with _lock, _conn() as c:
        c.execute("BEGIN")
        items = _load(c, scope, kinds, criteria)
        seed_items = items
        if references:
            narrow = criteria.get("ids") or criteria.get("tmdb_ids")
            missing = {"movie", "tv"} if narrow else {"movie", "tv"} - kinds
            if missing:
                seed_items = [*([] if narrow else items), *_load(c, scope, missing, {})]
        wall_matches = _wall_matches(c, scope, kinds, wall_q, wall) if wall_q else None
    seeds, clarification = _references(seed_items, references)
    if clarification:
        return {**empty, "clarification": clarification}
    seed_keys = {(r["kind"], r["id"]) for r in seeds}
    tokens = _terms(clues)
    scene_fragments = [{term[i:i + 2] for i in range(len(term) - 1)} for term in tokens["terms"]
                       if 4 <= len(term) <= 30 and explore and re.fullmatch(r"[\u3400-\u9fff]+", term)]
    needs_evidence = bool(seeds or any(tokens.values())) and not (explore and order == "relevance")
    today = today or date.today()
    annual_rating = (order.startswith("rating_") and not criteria.get("date_from") and not criteria.get("date_to")
                     and (today.year in (criteria.get("years") or []) or criteria.get("year_max") == today.year))
    matched, unknown = [], 0
    for row in items:
        if (row["kind"], row["id"]) in seed_keys:
            continue
        if annual_rating and row["year"] == today.year and (_valid_date(row["release_date"]) or "") > today.isoformat():
            continue
        if wall_matches is not None:
            passed = (any(f["id"] in wall_matches["movie"] for f in row["_files"])
                      if row["kind"] == "movie" else row.get("show_id", row["id"]) in wall_matches["tv"])
            if not passed:
                continue
        if wall:
            row["rating"] = row.get(wall.get("rating_source", "tmdb") + "_rating") or None
            wall_ok = _work_checks(row, wall)
        else:
            wall_ok = True
        rating = row.get(source + "_rating")
        row.update(rating=rating if rating and 0 < rating <= 10 else None, rating_source=source)
        ok = _check([wall_ok, _work_checks(row, criteria)])
        sort_values = {"year": row["year"], "rating": row["rating"], "added": row["added_at"]}
        if ok is not False and order.split("_")[0] in sort_values and not sort_values[order.split("_")[0]]:
            ok = None
        if ok:
            ok = _check_files(row, criteria)
        if ok is not False:
            _score(row, tokens, seeds, scene_fragments)
            if needs_evidence and not row["retrieval_score"]:
                continue
            role_proofs = []
            for role, proofs in row.get("_roles", {}).items():
                desired = {_norm(x) for x in [*_strings(criteria.get(role)), *_strings(wall.get(role))]}
                desired.update(tokens["people"])
                role_proofs.extend(label for name, label in proofs.items() if _norm(name) in desired)
            if role_proofs:
                row["evidence"] = list(dict.fromkeys(role_proofs + row["evidence"]))
        if ok is None:
            unknown += 1
        if ok:
            matched.append(row)
    output = _rank(matched, order, limit)
    for row in output:
        row.pop("_files", None)
        row.pop("_ids", None)
        row.pop("_roles", None)
    return {"items": output, "total": len(matched), "excluded_unknown": unknown}
