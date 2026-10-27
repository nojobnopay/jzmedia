"""Schema 33 adds policy references without rewriting catalogue identities."""

from contextlib import closing
import sqlite3

import pytest

from app import store
from app.store import _base, online, online_network
from app.store.network_schema import NETWORK_SCHEMA_STATEMENTS


def _preserved(connection):
    tables = ("movies", "tv_shows", "tv_episodes", "playback_progress", "app_settings",
              "online_sources", "online_items", "online_episodes")
    return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in tables}


def test_upgrade_from_32_preserves_local_and_online_data_and_is_repeatable(tmp_path, monkeypatch):
    db = tmp_path / "v32.db"
    monkeypatch.setattr(_base, "DB_PATH", str(db))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    store.init_db()
    src = online.create_online_source({"name": "Saved source", "provider": "maccms",
                                       "api_base_url": "https://catalog.example.invalid/api/"})
    show = online.upsert_online_item(src["id"], "show", "tv", "Online show")
    ep = online.upsert_online_episode(show["id"], "chapter", "Online episode", 1, 1, 4)
    online.update_online_settings(True)
    with closing(_base._conn()) as connection, connection:
        connection.execute("INSERT INTO movies(id,file_path,title,library_id) VALUES(101,'existing/movie.mkv','Local movie',1)")
        connection.execute("INSERT INTO playback_progress(kind,item_id,position,duration,updated_at) VALUES('movie',101,12,34,56)")
        connection.execute("DROP TABLE online_network_policies")
        connection.execute("DROP TABLE network_profiles")
        connection.execute("PRAGMA user_version=32")
        before = _preserved(connection)
    store.resync_fts(101)

    store.init_db()
    with closing(_base._conn()) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == _base.SCHEMA_VERSION
        assert _preserved(connection) == before
        assert connection.execute("SELECT COUNT(*) FROM network_profiles").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM online_network_policies").fetchone()[0] == 0
    assert online.get_online_source(src["id"]) == src
    assert online.get_online_item(show["id"]) == show
    assert online.get_online_episode(ep["id"]) == ep
    assert online_network.get_online_network_default() == {"rule": {"mode": "direct", "profile_id": None}, "revision": 0}
    assert online_network.get_source_network_policy(src["id"])["revision"] == 0

    profile = online_network.create_network_profile({"name": "Direct options", "mode": "direct", "max_connections": 2})
    policy = online_network.update_source_network_policy(src["id"], {"api": {"mode": "direct", "profile_id": profile["id"]}})
    store.init_db()
    assert online_network.get_network_profile(profile["id"]) == profile
    assert online_network.get_source_network_policy(src["id"]) == policy
    with closing(_base._conn()) as connection:
        assert _preserved(connection) == before


def test_migration_33_uses_the_callers_transaction():
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("PRAGMA user_version=32")
        connection.execute("BEGIN IMMEDIATE")
        _base._m33(connection)
        connection.rollback()
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert not names & {"network_profiles", "online_network_policies"}


def test_migration_33_rolls_back_partial_ddl_on_failure():
    class FailingConnection:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.calls = 0

        def execute(self, statement):
            self.calls += 1
            if self.calls == len(NETWORK_SCHEMA_STATEMENTS):
                raise sqlite3.OperationalError("simulated network migration failure")
            return self.wrapped.execute(statement)

    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="simulated network"):
            _base._m33(FailingConnection(connection))
        connection.rollback()
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert not names & {"network_profiles", "online_network_policies"}
