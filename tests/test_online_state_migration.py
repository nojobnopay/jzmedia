"""Schema 35 preserves catalogue/local data and keeps DDL transactional."""

from contextlib import closing
import sqlite3

import pytest

from app import store
from app.store import _base, online


def test_upgrade_from_34_preserves_existing_data_and_is_repeatable(tmp_path, monkeypatch):
    monkeypatch.setattr(_base, "DB_PATH", str(tmp_path / "migration.db"))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    store.init_db()
    source = online.create_online_source({"name": "Saved", "provider": "maccms",
        "api_base_url": "https://api.example/provide/vod/", "enabled": True})
    item = online.upsert_online_item(source["id"], "show", "tv", "Online show")
    episode = online.upsert_online_episode(item["id"], "episode", "Episode")
    with closing(_base._conn()) as connection, connection:
        connection.execute("INSERT INTO movies(id,file_path,title,library_id) VALUES(801,'existing.mkv','Local',1)")
        connection.execute("INSERT INTO playback_progress(kind,item_id,position,duration,updated_at) VALUES('movie',801,12,24,123)")
        connection.execute("DROP TABLE online_favorites")
        connection.execute("DROP TABLE online_progress")
        connection.execute("DROP TABLE online_state_scopes")
        connection.execute("PRAGMA user_version=34")
        tables = ("movies", "playback_progress", "online_sources", "online_items", "online_episodes")
        before = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in tables}
    store.init_db()
    store.init_db()
    with closing(_base._conn()) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == _base.SCHEMA_VERSION
        assert {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in tables} == before
        for table in ("online_favorites", "online_progress", "online_state_scopes"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            names = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            assert not names & {"url", "media_url", "upstream_url", "file_path", "session_token"}
    assert online.get_online_item(item["id"]) == item and online.get_online_episode(episode["id"]) == episode


def test_schema_35_ddl_rolls_back_and_repeats_without_own_commit():
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _base._m35(connection)
        _base._m35(connection)
        connection.rollback()
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert not names & {"online_favorites", "online_progress", "online_state_scopes"}


def test_schema_35_partial_failure_is_transactional():
    with closing(sqlite3.connect(":memory:")) as connection:
        class Broken:
            count = 0

            def execute(self, sql):
                self.count += 1
                if self.count == 3:
                    raise sqlite3.OperationalError("isolated failure")
                return connection.execute(sql)

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError):
            _base._m35(Broken())
        connection.rollback()
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='online_progress'").fetchone()
