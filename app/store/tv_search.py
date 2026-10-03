"""Read-only television title search, independent of the web client's FTS semantics.

Only derived title strings are cached. Every request reads current rows and library
visibility, so scans, renames and deletions need no extra index maintenance.
"""
from functools import lru_cache
import unicodedata

from pypinyin import Style, lazy_pinyin

from ._base import _conn, _lock


def _compact(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold()
                   if c.isalnum())


@lru_cache(maxsize=32768)
def _title_keys(title: str) -> tuple[str, str, str]:
    """Phrase-aware pronunciation; do not expand ambiguous characters combinatorially."""
    literal = _compact(title)
    if title.isascii():
        return literal, literal, literal
    full = _compact("".join(lazy_pinyin(title)))
    initials = _compact("".join(lazy_pinyin(title, style=Style.FIRST_LETTER)))
    return literal, full, initials


def _title_rank(query: str, title: str, original_title: str) -> int:
    best = 100
    for name in (title, original_title):
        if not name:
            continue
        literal, full, initials = _title_keys(name)
        if query == literal:
            best = min(best, 0)
        elif literal.startswith(query):
            best = min(best, 1)
        elif query in literal:
            best = min(best, 4)
        for derived in (full, initials):
            if query == derived:
                best = min(best, 2)
            elif derived.startswith(query):
                best = min(best, 3)
            elif query in derived:
                best = min(best, 5)
    return -1 if best == 100 else best


def _search_sql(kind: str, media_library: int | None) -> tuple[str, list]:
    """All identifiers come from fixed code; scope and query values are bound."""
    scope = " AND ml.id=?" if media_library is not None else ""
    params = []
    definitions = []
    sources = []
    if kind in ("all", "movie"):
        definitions.append(
            "movie_scores AS ("
            " SELECT m.id, 'movie' AS kind, m.title, m.original_title, m.year,"
            " m.poster_path, m.library_id, ml.id AS media_library_id,"
            " m.tmdb_id, m.updated_at,"
            " tv_title_rank(m.title, m.original_title) AS score,"
            " COUNT(*) OVER (PARTITION BY m.library_id, COALESCE(m.tmdb_id,-m.id))"
            " AS version_count"
            " FROM movies m JOIN libraries l ON l.id=m.library_id"
            " JOIN media_libraries ml ON ml.id=l.media_library_id"
            " WHERE l.enabled=1 AND ml.enabled=1" + scope + "),"
            " movie_ranked AS (SELECT *, ROW_NUMBER() OVER ("
            " PARTITION BY library_id, COALESCE(tmdb_id,-id)"
            " ORDER BY CASE WHEN score<0 THEN 100 ELSE score END,"
            " updated_at DESC, id DESC) AS rn FROM movie_scores)"
        )
        sources.append(
            "SELECT id,kind,title,original_title,year,poster_path,library_id,"
            " media_library_id,version_count,score FROM movie_ranked"
            " WHERE rn=1 AND score>=0")
        if media_library is not None:
            params.append(media_library)
    if kind in ("all", "show"):
        sources.append(
            "SELECT s.id, 'show' AS kind, s.title, s.original_title, s.year,"
            " s.poster_path, s.library_id, ml.id AS media_library_id,"
            " 0 AS version_count, tv_title_rank(s.title,s.original_title) AS score"
            " FROM tv_shows s JOIN libraries l ON l.id=s.library_id"
            " JOIN media_libraries ml ON ml.id=l.media_library_id"
            " WHERE l.enabled=1 AND ml.enabled=1 AND score>=0" + scope)
        if media_library is not None:
            params.append(media_library)
    if kind in ("all", "collection"):
        sources.append(
            "SELECT col.id, 'collection' AS kind, col.name AS title,"
            " '' AS original_title, NULL AS year, col.poster_path,"
            " NULL AS library_id, ml.id AS media_library_id,"
            " 0 AS version_count, tv_title_rank(col.name,'') AS score"
            " FROM collections col"
            " JOIN media_libraries ml ON ml.id=col.media_library_id"
            " WHERE ml.enabled=1 AND score>=0" + scope)
        if media_library is not None:
            params.append(media_library)
    definitions.append("matches AS (" + " UNION ALL ".join(sources) + ")")
    return "WITH " + ", ".join(definitions), params


def search_tv_titles(query: str, kind: str = "all", media_library: int | None = None,
                     limit: int = 24, offset: int = 0) -> dict:
    if kind not in ("all", "movie", "show", "collection"):
        raise ValueError("unsupported search kind")
    limit = max(1, min(int(limit), 60))
    offset = max(0, int(offset))
    query = str(query or "").strip()[:80]
    compact = _compact(query)
    result = {"items": [], "total": 0, "has_more": False,
              "limit": limit, "offset": offset, "query": query}
    if not compact:
        return result

    sql, params = _search_sql(kind, media_library)
    with _lock, _conn() as connection:
        connection.create_function(
            "tv_title_rank", 2,
            lambda title, original: _title_rank(compact, title or "", original or ""),
            deterministic=True)
        result["total"] = connection.execute(
            sql + " SELECT COUNT(*) FROM matches", params).fetchone()[0]
        rows = connection.execute(
            sql + " SELECT * FROM matches ORDER BY score, title COLLATE NOCASE,"
            " year DESC, kind, id LIMIT ? OFFSET ?",
            (*params, limit, offset)).fetchall()
        for row in rows:
            item = dict(row)
            item.pop("score")
            if item["kind"] != "movie":
                item.pop("version_count")
            if item["kind"] == "collection":
                item["name"] = item["title"]
                item["member_count"] = connection.execute(
                    "SELECT COUNT(*) FROM collection_members WHERE collection_id=?",
                    (item["id"],)).fetchone()[0]
                if not item["poster_path"]:
                    cover = connection.execute(
                        "SELECT m.poster_path FROM collection_members cm"
                        " JOIN movies m ON (cm.movie_tmdb_id=m.tmdb_id OR cm.movie_id=m.id)"
                        " JOIN libraries l ON l.id=m.library_id"
                        " WHERE cm.collection_id=? AND l.media_library_id=? AND l.enabled=1"
                        " AND m.poster_path IS NOT NULL AND m.poster_path!=''"
                        " ORDER BY m.year IS NULL, m.year, m.id LIMIT 1",
                        (item["id"], item["media_library_id"])).fetchone()
                    item["poster_path"] = cover[0] if cover else ""
                item["cover"] = item["poster_path"]
            result["items"].append(item)
    result["has_more"] = offset + len(result["items"]) < result["total"]
    return result
