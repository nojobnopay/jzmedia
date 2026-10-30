"""Schema 36 adds private poster snapshots without rewriting existing state."""

from contextlib import closing
import sqlite3

from app import store
from app.store import _base, online


def test_upgrade_from_35_preserves_identities_and_records_and_repeats(tmp_path, monkeypatch):
    monkeypatch.setattr(_base, "DB_PATH", str(tmp_path / "migration.db"))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    store.init_db()
    source = online.create_online_source({"name": "Saved", "provider": "maccms",
        "api_base_url": "https://api.example/provide/vod/", "enabled": True})
    item = online.upsert_online_item(source["id"], "movie", "movie", "Existing movie")
    with closing(_base._conn()) as connection, connection:
        connection.execute("INSERT INTO online_favorites(owner_hash,scope,item_id,created_at) VALUES(?,?,?,?)",
                           ("a" * 64, "normal", item["id"], 123))
        connection.execute("INSERT INTO movies(id,file_path,title,library_id) VALUES(801,'existing.mkv','Local',1)")
        connection.execute("INSERT INTO playback_progress(kind,item_id,position,duration,updated_at)"
                           " VALUES('movie',801,12,24,123)")
        connection.execute("DROP TABLE online_item_artwork")
        connection.execute("PRAGMA user_version=35")
        tables = ("movies", "playback_progress", "online_sources", "online_items", "online_episodes",
                  "online_favorites", "online_progress", "online_state_scopes")
        before = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in tables}
    store.init_db()
    store.init_db()
    with closing(_base._conn()) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 36
        assert {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in tables} == before
        assert connection.execute("SELECT COUNT(*) FROM online_item_artwork").fetchone()[0] == 0


def test_schema_36_ddl_rolls_back_without_own_commit_and_is_idempotent():
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _base._m36(connection)
        _base._m36(connection)
        connection.rollback()
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='online_item_artwork'").fetchone()
