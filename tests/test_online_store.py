"""Online identities survive metadata changes without touching the local library."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3

import pytest

from app import store
from app.store import _base, online


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(_base, "DB_PATH", str(tmp_path / "online.db"))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    store.init_db()


def source(**changes):
    return online.create_online_source({"name": "Test source", "provider": "maccms",
                                        "api_base_url": "https://catalog.example.invalid/api/", **changes})


def test_online_starts_disabled_and_does_not_inherit_an_environment_flag(monkeypatch):
    monkeypatch.setenv("ONLINE_ENABLED", "true")
    assert online.get_online_settings() == {"enabled": False}
    assert online.update_online_settings(True) == {"enabled": True}
    assert online.get_online_settings() == {"enabled": True}
    assert online.update_online_settings(False) == {"enabled": False}


@pytest.mark.parametrize("invalid", [1, "true", None])
def test_settings_reject_coercion_without_changing_saved_value(invalid):
    online.update_online_settings(True)
    with pytest.raises(ValueError):
        online.update_online_settings(invalid)
    assert online.get_online_settings() == {"enabled": True}


def test_corrupt_enable_setting_fails_closed():
    store.set_setting("online_enabled", "corrupt")
    assert online.get_online_settings() == {"enabled": False}


def test_sources_get_random_stable_identities_and_are_disabled_by_default():
    first, second = source(), source()
    assert first["id"] != second["id"]
    assert first["id"].startswith("src_")
    assert first["enabled"] is False and first["priority"] == 100
    assert first["allowed_hosts"] == ["catalog.example.invalid"]
    assert online.get_online_source(first["id"]) == first
    assert {row["id"] for row in online.list_online_sources()} == {first["id"], second["id"]}


def test_source_rename_address_rotation_and_disabling_preserve_content_ids():
    original = source(enabled=True)
    item = online.upsert_online_item(original["id"], 123, "tv", "Original show")
    ep = online.upsert_online_episode(item["id"], "chapter-1", "Original episode", 1, 1, 1)
    changed = online.update_online_source(original["id"], {
        "name": "Renamed", "api_base_url": "https://mirror.example.invalid/provide/vod/", "enabled": False,
    })
    assert changed["id"] == original["id"] and changed["created_at"] == original["created_at"]
    assert changed["enabled"] is False and changed["name"] == "Renamed"
    assert "mirror.example.invalid" in changed["allowed_hosts"]
    assert online.get_online_item(item["id"]) == item
    assert online.get_online_episode(ep["id"]) == ep


def test_invalid_source_patch_is_atomic_and_provider_is_immutable():
    original = source()
    for changes in ({"name": "Not saved", "priority": -1}, {"provider": "open_catalog"}, {"name": None}):
        with pytest.raises(ValueError):
            online.update_online_source(original["id"], changes)
        assert online.get_online_source(original["id"]) == original
    assert online.update_online_source(original["id"], {}) == original


def test_source_database_write_failure_rolls_back_every_field():
    original = source()
    with closing(_base._conn()) as connection, connection:
        connection.execute("""CREATE TRIGGER reject_source_change BEFORE UPDATE ON online_sources
            BEGIN SELECT RAISE(ABORT,'simulated source failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated source failure"):
        online.update_online_source(original["id"], {"name": "Not saved", "enabled": True})
    assert online.get_online_source(original["id"]) == original


def test_item_external_id_normalizes_but_source_and_kind_stay_separate(monkeypatch):
    first, second = source(), source()
    monkeypatch.setattr(online.time, "time", lambda: 1000)
    item = online.upsert_online_item(first["id"], 123, "movie", "Before")
    monkeypatch.setattr(online.time, "time", lambda: 2000)
    revised = online.upsert_online_item(first["id"], " 123 ", "movie", "After")
    assert revised["id"] == item["id"] and revised["external_id"] == "123"
    assert revised["created_at"] == 1000 and revised["updated_at"] == 2000
    assert revised["title"] == "After" and online.get_online_item(item["id"]) == revised
    other_source = online.upsert_online_item(second["id"], 123, "movie", "After")
    other_kind = online.upsert_online_item(first["id"], 123, "tv", "After")
    assert len({revised["id"], other_source["id"], other_kind["id"]}) == 3


def test_invalid_or_missing_parent_cannot_create_orphan_content():
    with pytest.raises(online.OnlineNotFound):
        online.upsert_online_item("src_" + "0" * 32, "missing", "movie")
    with pytest.raises(online.OnlineNotFound):
        online.upsert_online_episode("itm_" + "0" * 32, "missing")
    src = source()
    with pytest.raises(ValueError):
        online.upsert_online_item(src["id"], True, "movie")
    with closing(_base._conn()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM online_items").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM online_episodes").fetchone()[0] == 0


def test_episodes_have_stable_provider_keys_across_order_and_number_changes():
    src = source()
    show = online.upsert_online_item(src["id"], "show", "tv")
    first = online.upsert_online_episode(show["id"], 7, "Before", 1, 1, 1)
    second = online.upsert_online_episode(show["id"], "chapter-b", "Other", 1, 2, 2)
    revised = online.upsert_online_episode(show["id"], "7", "After", 2, 4, 8)
    assert revised["id"] == first["id"] and revised["created_at"] == first["created_at"]
    assert (revised["title"], revised["season"], revised["episode"], revised["sort_order"]) == ("After", 2, 4, 8)
    assert online.get_online_episode(second["id"]) == second
    another = online.upsert_online_item(src["id"], "another-show", "tv")
    assert online.upsert_online_episode(another["id"], 7)["id"] != first["id"]


def test_movies_cannot_receive_episodes_and_bad_episode_updates_are_atomic():
    src = source()
    movie = online.upsert_online_item(src["id"], "movie", "movie")
    with pytest.raises(ValueError, match="剧集"):
        online.upsert_online_episode(movie["id"], "chapter")
    show = online.upsert_online_item(src["id"], "show", "tv")
    original = online.upsert_online_episode(show["id"], "chapter", "Original", 1, 1, 1)
    with pytest.raises(ValueError):
        online.upsert_online_episode(show["id"], "chapter", "Not saved", 1, 1, -1)
    assert online.get_online_episode(original["id"]) == original


def test_concurrent_upserts_do_not_duplicate_the_identity():
    src = source()
    with ThreadPoolExecutor(max_workers=2) as pool:
        items = list(pool.map(lambda _index: online.upsert_online_item(src["id"], "same", "tv"), range(8)))
    assert len({item["id"] for item in items}) == 1
    with closing(_base._conn()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM online_items").fetchone()[0] == 1


def test_online_connections_close_and_do_not_change_other_connections(monkeypatch):
    original = _base._conn
    opened = []
    def tracked():
        connection = original()
        opened.append(connection)
        return connection
    monkeypatch.setattr(_base, "_conn", tracked)
    online.get_online_settings()
    assert opened
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    with closing(sqlite3.connect(_base.DB_PATH)) as unrelated:
        assert unrelated.execute("PRAGMA foreign_keys").fetchone()[0] == 0


def test_parent_relations_are_enforced_on_online_connections():
    src = source()
    item = online.upsert_online_item(src["id"], "parent", "tv")
    online.upsert_online_episode(item["id"], "chapter")
    with pytest.raises(sqlite3.IntegrityError):
        with online._connection(write=True) as connection:
            connection.execute("DELETE FROM online_items WHERE id=?", (item["id"],))
    assert online.get_online_item(item["id"]) == item


def test_unknown_id_reads_and_source_updates_have_explicit_results():
    assert online.get_online_source("src_" + "0" * 32) is None
    assert online.get_online_item("itm_" + "0" * 32) is None
    assert online.get_online_episode("ep_" + "0" * 32) is None
    with pytest.raises(online.OnlineNotFound):
        online.update_online_source("src_" + "0" * 32, {"name": "Missing"})
