"""Network choices and credentials persist atomically without contacting providers."""

from contextlib import closing
import json
import sqlite3

import pytest

from app import secrets, store
from app.config import settings
from app.store import _base, online, online_network as network


@pytest.fixture(autouse=True)
def isolated_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(_base, "DB_PATH", str(tmp_path / "network.db"))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "keys"))
    monkeypatch.setattr(secrets, "_key_cache", None)
    store.init_db()


def source():
    return online.create_online_source({"name": "Offline source", "provider": "maccms",
                                        "api_base_url": "https://catalog.example.invalid/api/"})


def proxy(**changes):
    return network.create_network_profile({"name": "Proxy", "mode": "proxy",
                                           "proxy_url": "http://127.0.0.1:17890", **changes})


def raw_profile(profile_id):
    with closing(_base._conn()) as connection:
        return dict(connection.execute("SELECT * FROM network_profiles WHERE id=?", (profile_id,)).fetchone())


def rule(profile):
    return {"mode": profile["mode"], "profile_id": profile["id"]}


def test_default_is_explicit_direct_and_sources_start_with_inheritance(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ONLINE_NETWORK_DEFAULT", "proxy")
    current = network.get_online_network_default()
    assert current == {"rule": {"mode": "direct", "profile_id": None}, "revision": 0}
    policy = network.get_source_network_policy(source()["id"])
    assert policy["revision"] == 0
    assert set(policy["policy"]) == {"default", "api", "media", "image", "subtitle", "subscription"}
    assert all(value == {"mode": "inherit", "profile_id": None} for value in policy["policy"].values())
    assert online.get_online_settings() == {"enabled": False}


def test_credentials_are_encrypted_and_never_in_public_profile_payloads():
    created = proxy(username="private-user", password="private-password")
    assert created["has_credentials"] is True and created["revision"] == 1
    assert created["id"].startswith("np_")
    public = [created, network.get_network_profile(created["id"]), network.list_network_profiles()]
    assert "private-user" not in str(public) and "private-password" not in str(public)
    assert not {"username", "password", "credentials_encrypted"} & set(created)
    saved = raw_profile(created["id"])
    cipher = saved["credentials_encrypted"]
    assert secrets.is_encrypted(cipher)
    assert "private-user" not in str(saved) and "private-password" not in str(saved)
    assert json.loads(secrets.decrypt_str(cipher)) == {"username": "private-user", "password": "private-password"}


def test_partial_secret_changes_preserve_the_other_field_and_can_explicitly_clear():
    created = proxy(username="first-user", password="first-password")
    changed = network.update_network_profile(created["id"], {"password": "next-password"})
    assert changed["id"] == created["id"] and changed["revision"] == 2
    assert changed["created_at"] == created["created_at"]
    assert json.loads(secrets.decrypt_str(raw_profile(created["id"])["credentials_encrypted"])) == {
        "username": "first-user", "password": "next-password"}
    network.update_network_profile(created["id"], {"username": ""})
    assert json.loads(secrets.decrypt_str(raw_profile(created["id"])["credentials_encrypted"])) == {
        "username": "", "password": "next-password"}
    cleared = network.update_network_profile(created["id"], {"clear_credentials": True})
    assert cleared["has_credentials"] is False
    assert raw_profile(created["id"])["credentials_encrypted"] == ""


def test_non_secret_update_does_not_decrypt_or_reencrypt_saved_credentials(monkeypatch):
    created = proxy(username="user", password="password")
    cipher = raw_profile(created["id"])["credentials_encrypted"]
    def forbidden(*_args):
        pytest.fail("unchanged credentials should not be decoded")
    monkeypatch.setattr(secrets, "decrypt_str", forbidden)
    changed = network.update_network_profile(created["id"], {"name": "Renamed"})
    assert changed["name"] == "Renamed" and changed["revision"] == 2
    assert raw_profile(created["id"])["credentials_encrypted"] == cipher


@pytest.mark.parametrize("changes", [
    {"mode": "direct"}, {"name": "Unsaved", "max_connections": 0},
    {"name": None}, {"clear_credentials": True, "username": "ambiguous"},
])
def test_rejected_profile_change_leaves_public_configuration_and_cipher_unchanged(changes):
    original = proxy(username="user", password="secret")
    before = raw_profile(original["id"])
    with pytest.raises(ValueError):
        network.update_network_profile(original["id"], changes)
    assert raw_profile(original["id"]) == before


def test_clear_can_repair_an_unreadable_cipher_without_disclosing_it():
    created = proxy(username="user", password="secret")
    with closing(_base._conn()) as connection, connection:
        connection.execute("UPDATE network_profiles SET credentials_encrypted='damaged-cipher' WHERE id=?", (created["id"],))
    with pytest.raises(ValueError, match="网络代理凭据无法读取"):
        network.update_network_profile(created["id"], {"password": "replacement"})
    assert network.update_network_profile(created["id"], {"clear_credentials": True})["has_credentials"] is False


def test_failed_database_update_rolls_back_credentials_and_revision():
    original = proxy(username="user", password="secret")
    before = raw_profile(original["id"])
    with closing(_base._conn()) as connection, connection:
        connection.execute("""CREATE TRIGGER reject_network_change BEFORE UPDATE ON network_profiles
            BEGIN SELECT RAISE(ABORT,'simulated network save failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated network"):
        network.update_network_profile(original["id"], {"password": "new-secret", "name": "Unsaved"})
    assert raw_profile(original["id"]) == before


def test_profile_references_must_exist_and_match_the_rule_mode():
    direct = network.create_network_profile({"name": "Direct", "mode": "direct"})
    proxied = proxy()
    src = source()
    for invalid in ({"mode": "proxy", "profile_id": direct["id"]},
                    {"mode": "direct", "profile_id": proxied["id"]}):
        with pytest.raises(ValueError):
            network.update_source_network_policy(src["id"], {"api": invalid})
        with pytest.raises(ValueError):
            network.update_online_network_default(invalid)
    missing = {"mode": "proxy", "profile_id": "np_" + "0" * 32}
    with pytest.raises(online.OnlineNotFound):
        network.update_source_network_policy(src["id"], {"api": missing})
    with pytest.raises(online.OnlineNotFound):
        network.update_online_network_default(missing)
    assert network.get_source_network_policy(src["id"])["revision"] == 0
    assert network.get_online_network_default()["revision"] == 0


def test_global_default_and_partial_source_rules_keep_explicit_inheritance():
    src = source()
    proxied = proxy()
    default = network.update_online_network_default(rule(proxied))
    assert default == {"rule": rule(proxied), "revision": 1}
    first = network.update_source_network_policy(src["id"], {"api": rule(proxied), "media": {"mode": "direct"}})
    second = network.update_source_network_policy(src["id"], {"image": {"mode": "direct"}})
    assert second["revision"] == 2
    assert second["policy"]["api"] == first["policy"]["api"]
    assert second["policy"]["media"] == {"mode": "direct", "profile_id": None}
    assert second["policy"]["default"] == {"mode": "inherit", "profile_id": None}
    assert network.update_source_network_policy(src["id"], {}) == second
    assert network.update_network_profile(proxied["id"], {}) == proxied
    assert network.update_network_profile(proxied["id"], {"clear_credentials": False}) == proxied


def test_invalid_policy_update_is_atomic_and_global_cannot_inherit():
    src = source()
    before = network.update_source_network_policy(src["id"], {"media": {"mode": "direct"}})
    for changes in ({"api": None}, {"unknown": {"mode": "direct"}},
                    {"api": {"mode": "direct"}, "image": {"mode": "proxy", "profile_id": "np_" + "0" * 32}}):
        with pytest.raises((ValueError, online.OnlineNotFound)):
            network.update_source_network_policy(src["id"], changes)
        assert network.get_source_network_policy(src["id"]) == before
    with pytest.raises(ValueError):
        network.update_online_network_default({"mode": "inherit"})
    assert network.get_online_network_default()["revision"] == 0


def test_corrupt_global_default_fails_closed_but_explicit_save_can_repair_it():
    store.set_setting("online_network_default", "malformed")
    with pytest.raises(ValueError, match="默认网络配置无效"):
        network.get_online_network_default()
    assert network.update_online_network_default({"mode": "direct"}) == {
        "rule": {"mode": "direct", "profile_id": None}, "revision": 1}


def test_referenced_profiles_cannot_be_deleted_until_all_rules_are_changed():
    src = source()
    created = proxy()
    network.update_online_network_default(rule(created))
    with pytest.raises(network.NetworkConflict):
        network.delete_network_profile(created["id"])
    network.update_online_network_default({"mode": "direct"})
    network.update_source_network_policy(src["id"], {"subscription": rule(created)})
    with pytest.raises(network.NetworkConflict):
        network.delete_network_profile(created["id"])
    network.update_source_network_policy(src["id"], {"subscription": {"mode": "inherit"}})
    network.delete_network_profile(created["id"])
    assert network.get_network_profile(created["id"]) is None


def test_server_only_state_contains_only_referenced_ciphertexts_and_is_a_copy():
    src = source()
    selected = proxy(username="private-user", password="private-password")
    proxy(name="Unselected", username="other-user", password="other-password")
    network.update_source_network_policy(src["id"], {"api": rule(selected)})
    saved = network.load_online_network_state(src["id"])
    assert set(saved["profiles"]) == {selected["id"]}
    assert secrets.is_encrypted(saved["profiles"][selected["id"]]["credentials_encrypted"])
    assert "private-password" not in str(saved)
    network.update_network_profile(selected["id"], {"name": "Changed"})
    network.update_source_network_policy(src["id"], {"api": {"mode": "direct"}})
    assert saved["profiles"][selected["id"]]["name"] == "Proxy"
    assert saved["source"]["revision"] == 1
    assert network.load_online_network_state(src["id"])["source"]["revision"] == 2


def test_state_load_uses_one_database_snapshot_during_a_concurrent_writer(monkeypatch):
    src = source()
    first, replacement = proxy(name="First"), proxy(name="Replacement")
    network.update_online_network_default(rule(first))
    network.update_source_network_policy(src["id"], {"api": rule(first)})
    original = _base._conn
    changed = [False]

    class Connection:
        def __init__(self):
            self.wrapped = original()

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def close(self):
            self.wrapped.close()

        def execute(self, statement, parameters=()):
            result = self.wrapped.execute(statement, parameters)
            if statement.startswith("SELECT value FROM app_settings") and not changed[0]:
                changed[0] = True
                with closing(sqlite3.connect(_base.DB_PATH, timeout=2)) as writer, writer:
                    new_default = {"rule": rule(replacement), "revision": 2}
                    writer.execute("UPDATE app_settings SET value=? WHERE key=?",
                                   (json.dumps(new_default), network.DEFAULT_KEY))
                    policy = json.loads(writer.execute("SELECT rules_json FROM online_network_policies").fetchone()[0])
                    policy["api"] = rule(replacement)
                    writer.execute("UPDATE online_network_policies SET rules_json=?,revision=2", (json.dumps(policy),))
            return result

    monkeypatch.setattr(_base, "_conn", Connection)
    state = network.load_online_network_state(src["id"])
    assert changed[0]
    assert state["application"]["revision"] == state["source"]["revision"] == 1
    assert set(state["profiles"]) == {first["id"]}


def test_missing_entities_have_explicit_not_found_results():
    missing_profile = "np_" + "0" * 32
    missing_source = "src_" + "0" * 32
    assert network.get_network_profile(missing_profile) is None
    for operation in (lambda: network.update_network_profile(missing_profile, {"name": "Missing"}),
                      lambda: network.delete_network_profile(missing_profile),
                      lambda: network.get_source_network_policy(missing_source),
                      lambda: network.update_source_network_policy(missing_source, {}),
                      lambda: network.load_online_network_state(missing_source)):
        with pytest.raises(online.OnlineNotFound):
            operation()


def test_connections_are_closed_and_fk_enforcement_is_local(monkeypatch):
    original = _base._conn
    opened = []
    def tracked():
        connection = original()
        opened.append(connection)
        return connection
    monkeypatch.setattr(_base, "_conn", tracked)
    network.get_online_network_default()
    assert opened
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    with closing(sqlite3.connect(_base.DB_PATH)) as unrelated:
        assert unrelated.execute("PRAGMA foreign_keys").fetchone()[0] == 0
