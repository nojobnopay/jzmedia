"""Local TV actor lookup from explicit acting credits, never mixed person_names.

The request builds a view of current, enabled libraries. Only spelling/pinyin
derivatives use the title-search cache; actor membership and counts are not cached.
"""
from functools import lru_cache
import json
import unicodedata

from pypinyin import Style, pinyin

from ..log import get_logger
from ._base import _conn, _lock
from .tv_search import _compact, _title_keys, _title_rank

logger = get_logger("store.tv_actors")


@lru_cache(maxsize=32768)
def _surname_spellings(name: str) -> tuple[str, ...]:
    """Bounded first-character alternatives catch surnames such as 曾/单/解/仇.

    Keep the normal phrase spelling as well, and never change pypinyin's global
    dictionary (which would also affect film titles). Only the first character is
    expanded, not the Cartesian product of every ambiguous character in a name.
    """
    if not name or name[0].isascii():
        return ()
    readings = pinyin(name[0], style=Style.NORMAL, heteronym=True, errors="ignore")
    if not readings:
        return ()
    _, full, initials = _title_keys(name[1:])
    result = set()
    for reading in readings[0][:8]:
        if reading.isascii() and reading.isalpha():
            result.add(reading + full)
            result.add(reading[0] + initials)
    return tuple(sorted(result))


def _actor_rank(query, name):
    rank = _title_rank(query, name, "")
    rank = rank if rank >= 0 else 100
    for spelling in _surname_spellings(name):
        if query == spelling:
            rank = min(rank, 2)
        elif spelling.startswith(query):
            rank = min(rank, 3)
        elif query in spelling:
            rank = min(rank, 5)
    return rank if rank < 100 else -1


def _name(value) -> str:
    if not isinstance(value, str):
        return ""
    name = " ".join(unicodedata.normalize("NFKC", value).split())
    # Actor keys must remain navigable within the endpoint's bounded query field.
    return name if len(name) <= 300 else ""


def _person_id(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        identifier = int(value)
    except ValueError:
        return None
    return identifier if 0 < identifier <= 2 ** 63 - 1 else None


def _json(value, context):
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        logger.debug("Invalid actor credits JSON: %s", context)
        return {}


def _cast(value, field, context) -> list[dict]:
    parsed = _json(value, context)
    if field and not isinstance(parsed, dict):
        logger.debug("Invalid actor credits object: %s", context)
        return []
    rows = parsed.get(field, []) if field else parsed
    if not isinstance(rows, list):
        logger.debug("Invalid actor credits list: %s", context)
        return []
    return [row for row in rows if isinstance(row, dict)]


def _avatar(value) -> str:
    """Only stored local avatar paths; neither remote URLs nor profile lookups."""
    value = str(value or "").strip().lstrip("/")
    return "" if value == "-" or ":" in value or ".." in value.split("/") else value


def _scope(media_library):
    sql = "l.enabled=1 AND ml.enabled=1"
    params = []
    if media_library is not None:
        sql += " AND ml.id=?"
        params.append(media_library)
    return sql, params


def _actor_catalogue(media_library):
    actors, works = {}, {}
    movie_keys, movie_tmdb, show_tmdb = {}, {}, {}
    scope, params = _scope(media_library)

    def add(entry, work_key, local_avatar=False):
        name = _name(entry.get("name"))
        if not name:
            return
        identifier = _person_id(entry.get("id") or entry.get("tmdb_id"))
        key = f"tmdb:{identifier}" if identifier else "name:" + name.casefold()
        if len(key) > 512:
            logger.debug("Skipping actor key exceeding navigation limit")
            return
        actor = actors.setdefault(key, {"key": key, "name": name, "avatar_path": "",
                                        "aliases": set(), "works": set(),
                                        "tmdb_id": identifier})
        actor["aliases"].add(name)
        original_name = _name(entry.get("original_name"))
        if original_name:
            actor["aliases"].add(original_name)
        actor["works"].add(work_key)
        if local_avatar and entry.get("avatar"):
            actor["avatar_path"] = actor["avatar_path"] or _avatar(entry["avatar"])

    with _lock, _conn() as connection:
        for table, kind in (("movies", "movie"), ("tv_shows", "show")):
            rows = connection.execute(
                "SELECT i.id, i.title, i.original_title, i.year, i.poster_path,"
                " i.library_id, i.tmdb_id, i.updated_at, ml.id AS media_library_id"
                f" FROM {table} i JOIN libraries l ON l.id=i.library_id"
                " JOIN media_libraries ml ON ml.id=l.media_library_id"
                " WHERE " + scope + " ORDER BY i.id", params).fetchall()
            for row in rows:
                item = dict(row)
                tid = item.pop("tmdb_id")
                updated = item.pop("updated_at") or 0
                item["kind"] = kind
                item["title"] = item["title"] or ""
                item["poster_path"] = item["poster_path"] or ""
                if kind == "movie":
                    key = (kind, item["library_id"], tid if tid is not None else -item["id"])
                    movie_keys[item["id"]] = key
                    if tid:
                        movie_tmdb.setdefault(tid, set()).add(key)
                else:
                    key = (kind, item["id"])
                    if tid:
                        show_tmdb.setdefault(tid, set()).add(key)
                previous = works.get(key)
                versions = (previous or {}).get("version_count", 0) + 1
                if previous is None or (updated, item["id"]) > previous["_representative"]:
                    item["_representative"] = (updated, item["id"])
                    works[key] = item
                if kind == "movie":
                    works[key]["version_count"] = versions

        links = connection.execute(
            "SELECT mp.movie_id, p.tmdb_id, p.name, p.avatar"
            " FROM movie_person mp JOIN persons p ON p.id=mp.person_id"
            " JOIN movies i ON i.id=mp.movie_id"
            " JOIN libraries l ON l.id=i.library_id"
            " JOIN media_libraries ml ON ml.id=l.media_library_id"
            " WHERE mp.role='actor' AND " + scope + " ORDER BY p.id, mp.movie_id",
            params).fetchall()
        for row in links:
            add(dict(row), movie_keys[row["movie_id"]], local_avatar=True)

        for table, media_type, mapping in (("movies", "movie", movie_tmdb),
                                            ("tv_shows", "tv", show_tmdb)):
            cached = connection.execute(
                "SELECT DISTINCT tc.tmdb_id, tc.credits FROM tmdb_cache tc"
                f" JOIN {table} i ON i.tmdb_id=tc.tmdb_id"
                " JOIN libraries l ON l.id=i.library_id"
                " JOIN media_libraries ml ON ml.id=l.media_library_id"
                " WHERE tc.media_type=? AND " + scope + " ORDER BY tc.tmdb_id",
                (media_type, *params)).fetchall()
            for row in cached:
                for entry in _cast(row["credits"], "cast", f"{media_type}:{row['tmdb_id']}"):
                    for key in mapping.get(row["tmdb_id"], ()):
                        add(entry, key)

        for table, column, field in (("tv_seasons", '"cast"', None),
                                      ("tv_episodes", "episode_credits", "guests")):
            credits = connection.execute(
                f"SELECT child.id, child.show_id, child.{column} AS credits"
                f" FROM {table} child JOIN tv_shows i ON i.id=child.show_id"
                " JOIN libraries l ON l.id=i.library_id"
                " JOIN media_libraries ml ON ml.id=l.media_library_id"
                " WHERE child.library_id=i.library_id AND " + scope + " ORDER BY child.id",
                params).fetchall()
            for row in credits:
                for entry in _cast(row["credits"], field, f"{table}:{row['id']}"):
                    add(entry, ("show", row["show_id"]))

        # TV actors need not exist in persons. Reuse an existing local portrait/name
        # by exact TMDB identity without creating a person or fetching a biography.
        ids = [actor["tmdb_id"] for actor in actors.values() if actor["tmdb_id"]]
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            people = connection.execute(
                "SELECT tmdb_id,name,avatar FROM persons WHERE tmdb_id IN ("
                + ",".join("?" for _ in chunk) + ")", chunk).fetchall()
            for person in people:
                actor = actors[f"tmdb:{person['tmdb_id']}"]
                name = _name(person["name"])
                if name:
                    actor["name"] = name
                    actor["aliases"].add(name)
                actor["avatar_path"] = _avatar(person["avatar"]) or actor["avatar_path"]

    for item in works.values():
        item.pop("_representative")
    return actors, works


def _actor_payload(actor):
    movies = sum(key[0] == "movie" for key in actor["works"])
    shows = len(actor["works"]) - movies
    return {"key": actor["key"], "name": actor["name"], "avatar_path": actor["avatar_path"],
            "movie_count": movies, "show_count": shows, "work_count": movies + shows}


def _page(items, limit, offset):
    return {"items": items[offset:offset + limit], "total": len(items),
            "has_more": offset + limit < len(items), "limit": limit, "offset": offset}


def search_tv_actors(query: str = "", media_library: int | None = None,
                     limit: int = 24, offset: int = 0) -> dict:
    query = str(query or "").strip()[:80]
    compact = _compact(query)
    if query and not compact:
        return {**_page([], limit, offset), "query": query}
    actors, _ = _actor_catalogue(media_library)
    matched = []
    for actor in actors.values():
        ranks = [_actor_rank(compact, name) for name in actor["aliases"]] if compact else [0]
        ranks = [rank for rank in ranks if rank >= 0]
        if ranks:
            matched.append((min(ranks), _actor_payload(actor)))
    matched.sort(key=lambda pair: (pair[0], -pair[1]["work_count"],
                                   pair[1]["name"].casefold(), pair[1]["key"]))
    return {**_page([item for _, item in matched], limit, offset), "query": query}


def tv_actor_works(actor_key: str, kind: str = "all", media_library: int | None = None,
                   limit: int = 24, offset: int = 0) -> dict | None:
    actors, works = _actor_catalogue(media_library)
    actor = actors.get(actor_key)
    if actor is None:
        return None
    items = [works[key] for key in actor["works"] if kind == "all" or key[0] == kind]
    items.sort(key=lambda item: (item["year"] is None, -(item["year"] or 0),
                                item["title"].casefold(), item["kind"], item["id"]))
    return {**_page(items, limit, offset), "actor": _actor_payload(actor)}
