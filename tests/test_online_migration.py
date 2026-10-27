"""Schema 32 adds an independent catalogue and preserves the existing library."""

from contextlib import closing
import sqlite3

import pytest

from app import store
from app.store import _base, online
from app.store.online_schema import ONLINE_SCHEMA_STATEMENTS


def _snapshot_local(connection):
    return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("movies", "tv_shows", "tv_episodes", "playback_progress", "app_settings")}


def test_upgrade_from_31_preserves_local_data_and_is_repeatable(tmp_path, monkeypatch):
    db = tmp_path / "v31.db"
    monkeypatch.setattr(_base, "DB_PATH", str(db))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    store.init_db()
    with closing(_base._conn()) as connection, connection:
        connection.execute("INSERT INTO movies(id,file_path,title,library_id) VALUES(101,'existing/movie.mkv','Local movie',1)")
        connection.execute("INSERT INTO tv_shows(id,title,library_id) VALUES(102,'Local show',1)")
        connection.execute("INSERT INTO tv_episodes(id,show_id,library_id,file_path,season,episode,title)"
                           " VALUES(103,102,1,'existing/show/e01.mkv',1,1,'Local episode')")
        connection.execute("INSERT INTO playback_progress(kind,item_id,position,duration,updated_at)"
                           " VALUES('episode',103,123,456,789)")
        connection.execute("INSERT INTO app_settings(key,value,updated_at) VALUES('tmdb_language','en-US',123)")
        for table in ("online_episodes", "online_items", "online_sources"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version=31")
        before = _snapshot_local(connection)
    store.resync_fts(101)

    store.init_db()
    with closing(_base._conn()) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == _base.SCHEMA_VERSION
        assert _snapshot_local(connection) == before
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"online_sources", "online_items", "online_episodes"} <= tables
        for table in ("online_sources", "online_items", "online_episodes"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        for table in ("online_items", "online_episodes"):
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            assert not columns & {"file_path", "url", "media_url", "stream_url"}
    assert online.get_online_settings() == {"enabled": False}

    src = online.create_online_source({"provider": "maccms", "name": "Existing identity",
                                       "api_base_url": "https://catalog.example.invalid/api/"})
    item = online.upsert_online_item(src["id"], "episode-list", "tv")
    episode = online.upsert_online_episode(item["id"], "provider-episode")
    store.init_db()
    with closing(_base._conn()) as connection:
        assert _snapshot_local(connection) == before
        assert connection.execute("PRAGMA user_version").fetchone()[0] == _base.SCHEMA_VERSION
    assert online.get_online_source(src["id"]) == src
    assert online.get_online_item(item["id"]) == item
    assert online.get_online_episode(episode["id"]) == episode


def test_migration_32_participates_in_its_callers_transaction():
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("PRAGMA user_version=31")
        connection.execute("BEGIN IMMEDIATE")
        _base._m32(connection)
        connection.rollback()
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert not names & {"online_sources", "online_items", "online_episodes"}


def test_migration_32_rolls_back_partial_ddl_on_failure():
    class FailingConnection:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == len(ONLINE_SCHEMA_STATEMENTS):
                raise sqlite3.OperationalError("simulated migration failure")
            return self.wrapped.execute(statement)

    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="simulated migration failure"):
            _base._m32(FailingConnection(connection))
        connection.rollback()
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert not names & {"online_sources", "online_items", "online_episodes"}
