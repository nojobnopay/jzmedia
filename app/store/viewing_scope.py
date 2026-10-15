"""Request-scoped SQLite views apply visibility before joins, counts and LIMIT.

Connections are never pooled. TEMP views shadow the catalogue tables only on
this request's connection; background work and authorized management use the
original tables. Writes explicitly target main after API authorization.
"""
import re
import sqlite3

TABLES = {"movies", "tv_shows", "tv_seasons", "tv_episodes", "extras", "collections",
          "collection_members", "movie_person", "persons", "playback_progress"}
_WRITE = re.compile(r"^(\s*(?:UPDATE(?:\s+OR\s+\w+)?|INSERT(?:\s+OR\s+\w+)?\s+INTO|DELETE\s+FROM)\s+)(\w+)\b", re.I)


class ScopedConnection(sqlite3.Connection):
    def execute(self, sql, parameters=(), /):
        match = _WRITE.match(sql)
        if match and match[2].lower() in TABLES:
            sql = sql[:match.start(2)] + "main." + sql[match.start(2):]
        return super().execute(sql, parameters)


def install(c):
    from ..viewing import current
    ctx = current.get()
    if ctx is None:
        return
    condition = "is_private=1" if ctx.mode == "private" else "is_private=0"
    if ctx.mode == "children":
        condition += " AND (children_override='allow' OR (children_override='auto' AND children_auto_status='allow'))"
    views = {
        "movies": f"SELECT * FROM main.movies WHERE {condition}",
        "tv_shows": f"SELECT * FROM main.tv_shows WHERE {condition}",
        "tv_seasons": "SELECT * FROM main.tv_seasons WHERE show_id IN (SELECT id FROM temp.tv_shows)",
        "tv_episodes": "SELECT * FROM main.tv_episodes WHERE show_id IN (SELECT id FROM temp.tv_shows)",
        # An orphan may have lost a private parent during cleanup. Unassigned
        # files belong to authorized file management, not ordinary playback.
        "extras": """SELECT * FROM main.extras WHERE (movie_id IS NOT NULL OR show_id IS NOT NULL)
            AND (movie_id IS NULL OR movie_id IN (SELECT id FROM temp.movies))
            AND (show_id IS NULL OR show_id IN (SELECT id FROM temp.tv_shows))""",
        "movie_person": "SELECT * FROM main.movie_person WHERE movie_id IN (SELECT id FROM temp.movies)",
        "persons": """SELECT p.* FROM main.persons p WHERE p.id IN (SELECT person_id FROM temp.movie_person)
            OR p.tmdb_id IN (
              SELECT CAST(COALESCE(json_extract(j.value,'$.id'),json_extract(j.value,'$.tmdb_id')) AS INTEGER)
              FROM main.tmdb_cache cache, json_each(cache.credits,'$.cast') j
              WHERE cache.media_type='tv' AND cache.tmdb_id IN (SELECT tmdb_id FROM temp.tv_shows)
              UNION SELECT CAST(COALESCE(json_extract(j.value,'$.id'),json_extract(j.value,'$.tmdb_id')) AS INTEGER)
              FROM temp.tv_seasons season, json_each(season.cast) j
              UNION SELECT CAST(COALESCE(json_extract(j.value,'$.id'),json_extract(j.value,'$.tmdb_id')) AS INTEGER)
              FROM temp.tv_episodes episode, json_each(episode.episode_credits,'$.guests') j
              UNION SELECT CAST(COALESCE(json_extract(j.value,'$.id'),json_extract(j.value,'$.tmdb_id')) AS INTEGER)
              FROM temp.tv_episodes episode, json_each(episode.episode_credits,'$.directors') j)""",
        "collection_members": """SELECT cm.rowid AS rowid, cm.* FROM main.collection_members cm
            JOIN main.collections col ON col.id=cm.collection_id WHERE EXISTS (
                SELECT 1 FROM temp.movies m JOIN main.libraries l ON l.id=m.library_id
                WHERE l.media_library_id=col.media_library_id AND
                ((cm.movie_tmdb_id IS NOT NULL AND m.tmdb_id=cm.movie_tmdb_id) OR m.id=cm.movie_id))""",
        "collections": "SELECT * FROM main.collections WHERE id IN (SELECT collection_id FROM temp.collection_members)",
        "playback_progress": """SELECT * FROM main.playback_progress WHERE
            (kind='movie' AND item_id IN (SELECT id FROM temp.movies)) OR
            (kind='episode' AND item_id IN (SELECT id FROM temp.tv_episodes)) OR
            (kind='extra' AND item_id IN (SELECT id FROM temp.extras))""",
    }
    for name, query in views.items():
        c.execute(f"CREATE TEMP VIEW {name} AS {query}")
