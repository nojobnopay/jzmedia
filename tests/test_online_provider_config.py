"""P2 source actions migrate atomically without changing existing identities."""

from contextlib import closing
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app import store, viewing
from app.online.models import OnlinePlaybackIdentity, OnlineSourcePatch
from app.routers import online as admin
from app.store import _base, online


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(_base, "DB_PATH", str(tmp_path / "p2-config.db"))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    store.init_db()


def source(**changes):
    return online.create_online_source({"name": "Administrator source", "provider": "maccms",
                                        "api_base_url": "https://api.example.invalid/provide/vod/", **changes})


def test_source_options_round_trip_without_network_or_identity_changes():
    original = source(enabled=True)
    assert original["provider_options"] == {"search_action": "list", "detail_action": "detail"}
    item = online.upsert_online_item(original["id"], "1", "tv", "Show")
    ep = online.upsert_online_episode(item["id"], "stable-episode", "Episode")
    changed = online.update_online_source(original["id"], {
        "provider_options": {"search_action": "videolist", "detail_action": "videolist"}})
    assert changed["id"] == original["id"]
    assert changed["provider_options"] == {"search_action": "videolist", "detail_action": "videolist"}
    assert online.get_online_source(changed["id"]) == changed
    assert online.get_online_item(item["id"]) == item
    assert online.get_online_episode(ep["id"]) == ep


@pytest.mark.parametrize("options", [None, [], "detail", {"detail_action": "list"},
                                    {"search_action": "url"}, {"search_action": 1},
                                    {"token": "must-not-be-stored"}])
def test_invalid_options_do_not_partially_update_source(options):
    original = source()
    with pytest.raises(ValidationError):
        online.update_online_source(original["id"], {"name": "Not saved", "provider_options": options})
    assert online.get_online_source(original["id"]) == original


def test_upgrade_from_33_preserves_local_and_online_data():
    original = source(enabled=True)
    item = online.upsert_online_item(original["id"], "1", "tv", "Show")
    ep = online.upsert_online_episode(item["id"], "stable-episode", "Episode")
    with closing(_base._conn()) as connection, connection:
        connection.execute("INSERT INTO movies(id,file_path,title,library_id) VALUES(901,'local.mkv','Local',1)")
        connection.execute("INSERT INTO playback_progress(kind,item_id,position,duration,updated_at)"
                           " VALUES('movie',901,12,123,456)")
        connection.execute("ALTER TABLE online_sources DROP COLUMN provider_options")
        connection.execute("PRAGMA user_version=33")
        before = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                  for table in ("movies", "playback_progress", "online_items", "online_episodes")}
    store.init_db()
    store.init_db()
    with closing(_base._conn()) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == _base.SCHEMA_VERSION
        assert {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                for table in before} == before
    assert online.get_online_source(original["id"]) == original
    assert online.get_online_item(item["id"]) == item
    assert online.get_online_episode(ep["id"]) == ep


def test_migration_34_uses_callers_transaction_and_is_repeatable():
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE online_sources(id TEXT PRIMARY KEY)")
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        _base._m34(connection)
        _base._m34(connection)
        assert "provider_options" in {row["name"] for row in connection.execute("PRAGMA table_info(online_sources)")}
        connection.rollback()
        assert "provider_options" not in {row["name"] for row in connection.execute("PRAGMA table_info(online_sources)")}


def test_line_selection_keeps_legacy_identity_shape_and_rejects_urls():
    identity = {"source_id": "src_" + "1" * 32, "item_id": "itm_" + "2" * 32, "kind": "movie"}
    assert OnlinePlaybackIdentity.model_validate(identity).model_dump() == {**identity, "episode_id": None}
    selected = {**identity, "line_id": "ln_" + "3" * 32}
    assert OnlinePlaybackIdentity.model_validate(selected).model_dump()["line_id"] == selected["line_id"]
    for line in ("https://media.example.invalid/movie.mp4", "3" * 32, "ln_" + "3" * 32 + "\n", True):
        with pytest.raises(ValidationError):
            OnlinePlaybackIdentity.model_validate({**identity, "line_id": line})


def test_material_source_changes_revoke_sessions_but_rename_does_not(monkeypatch):
    from app.online import service
    revoked = []
    monkeypatch.setattr(service, "registry", SimpleNamespace(revoke_source=revoked.append))
    original = source(enabled=True)
    sid = original["id"]
    admin.update_source(sid, OnlineSourcePatch(name="Renamed"))
    assert not revoked
    admin.update_source(sid, OnlineSourcePatch(provider_options={"detail_action": "videolist"}))
    assert revoked == [sid]
    admin.update_source(sid, OnlineSourcePatch(allowed_hosts=["cdn.example.invalid"]))
    assert revoked == [sid, sid]
    admin.update_source(sid, OnlineSourcePatch(enabled=False))
    assert revoked == [sid, sid, sid]


def test_change_decision_uses_the_actual_previous_transaction_state():
    original = source(enabled=True)
    sid = original["id"]
    values, changed = online.update_online_source_with_change(sid, {
        "api_base_url": "https://api.example.invalid/other/"})
    assert changed and values["api_base_url"].endswith("/other/")
    values, changed = online.update_online_source_with_change(sid, {
        "api_base_url": original["api_base_url"]})
    assert changed and values["api_base_url"] == original["api_base_url"]
    assert online.update_online_source_with_change(sid, {}) == (values, False)
    assert online.update_online_source_with_change(sid, {"name": "Renamed"})[1] is False


def test_catalog_management_exception_is_get_only_and_exact():
    for suffix in ("sources", "search", "detail"):
        path = "/api/online/catalog/" + suffix
        assert not viewing.management_path(path, "GET")
        for method in ("HEAD", "POST", "PATCH", "DELETE"):
            assert viewing.management_path(path, method)
        assert viewing.management_path(path + "/unknown", "GET")
    assert viewing.management_path("/api/online/sources", "GET")
