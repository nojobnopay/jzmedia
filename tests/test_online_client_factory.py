"""Online readers use one enabled-source snapshot without opening connections."""

from contextlib import closing
import json
import socket
import sqlite3

import pytest

from app import secrets, store
from app.config import settings
from app.network import client as clients
from app.network.policy import NetworkPolicyError
from app.network.targets import TargetPolicyError
from app.store import _base, online, online_network as network


@pytest.fixture(autouse=True)
def isolated_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(_base, "DB_PATH", str(tmp_path / "online-client.db"))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "keys"))
    monkeypatch.setattr(secrets, "_key_cache", None)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    store.init_db()

    def no_network(*_args, **_kwargs):
        pytest.fail("building an online client must not resolve or connect upstream")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)


def source(**changes):
    # Numeric public addresses only authorize test targets; no requests run.
    return online.create_online_source({
        "name": "Isolated source", "provider": "maccms",
        "api_base_url": "https://8.8.8.8/api/", "allowed_hosts": ["8.8.4.4"],
        "enabled": True, **changes,
    })


def test_feature_is_disabled_by_default_before_a_transport_is_built(monkeypatch):
    src = source()

    def forbidden(*_args, **_kwargs):
        pytest.fail("disabled online sources must not construct a transport")

    monkeypatch.setattr(clients, "create_client", forbidden)
    with pytest.raises(NetworkPolicyError, match="^online_source_disabled$"):
        clients.create_online_client(src["id"], "api")


def test_disabled_source_is_rejected_when_the_feature_is_enabled(monkeypatch):
    src = source(enabled=False)
    online.update_online_settings(True)

    def forbidden(*_args, **_kwargs):
        pytest.fail("disabled source must not construct a transport")

    monkeypatch.setattr(clients, "create_client", forbidden)
    with pytest.raises(NetworkPolicyError, match="^online_source_disabled$"):
        clients.create_online_client(src["id"], "media")


def test_existing_client_keeps_its_exact_host_allowlist_after_source_changes():
    src = source()
    online.update_online_settings(True)
    with clients.create_online_client(src["id"], "media") as original:
        first = original.targets.resolve("https://8.8.4.4/segment.ts")
        online.update_online_source(src["id"], {"allowed_hosts": ["1.1.1.1"]})

        assert original.targets.resolve(first.url).addresses == first.addresses
        with pytest.raises(TargetPolicyError, match="^target_host_not_allowed$"):
            original.targets.resolve("https://1.1.1.1/segment.ts")

        with clients.create_online_client(src["id"], "media") as replacement:
            assert replacement.targets.resolve("https://1.1.1.1/segment.ts").host == "1.1.1.1"
            with pytest.raises(TargetPolicyError, match="^target_host_not_allowed$"):
                replacement.targets.resolve(first.url)
        assert original.snapshot.source_id == src["id"]


def test_factory_reads_enable_flags_hosts_and_network_rule_from_one_database_snapshot(monkeypatch):
    src = source()
    online.update_online_settings(True)
    first = network.create_network_profile({"name": "Original", "mode": "direct",
                                            "connect_timeout_seconds": 2.0})
    replacement = network.create_network_profile({"name": "Replacement", "mode": "direct",
                                                  "connect_timeout_seconds": 7.0})
    network.update_online_network_default({"mode": "direct", "profile_id": first["id"]})
    original_connection = _base._conn
    opened = []
    changed = False

    class Connection:
        def __init__(self):
            self.wrapped = original_connection()
            opened.append(self)

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def close(self):
            self.wrapped.close()

        def execute(self, statement, parameters=()):
            nonlocal changed
            result = self.wrapped.execute(statement, parameters)
            if (statement == "SELECT value FROM app_settings WHERE key=?"
                    and parameters == (network.DEFAULT_KEY,) and not changed):
                changed = True
                # A separate WAL writer commits after the reader starts its
                # snapshot, before later source/feature/profile reads occur.
                with closing(sqlite3.connect(_base.DB_PATH, timeout=2)) as writer, writer:
                    writer.execute("UPDATE app_settings SET value=? WHERE key=?", (
                        json.dumps({"rule": {"mode": "direct", "profile_id": replacement["id"]},
                                    "revision": 2}), network.DEFAULT_KEY))
                    writer.execute("UPDATE app_settings SET value='false' WHERE key='online_enabled'")
                    writer.execute("UPDATE online_sources SET enabled=0,allowed_hosts=? WHERE id=?", (
                        json.dumps(["8.8.8.8", "1.1.1.1"]), src["id"]))
            return result

    monkeypatch.setattr(_base, "_conn", Connection)
    with clients.create_online_client(src["id"], "api") as current:
        assert changed
        assert len(opened) == 1
        assert current.snapshot.profile_id == first["id"]
        assert current.snapshot.connect_timeout_seconds == 2.0
        assert current.targets.resolve("https://8.8.4.4/api/").host == "8.8.4.4"
        with pytest.raises(TargetPolicyError, match="^target_host_not_allowed$"):
            current.targets.resolve("https://1.1.1.1/api/")

    with pytest.raises(NetworkPolicyError, match="^online_source_disabled$"):
        clients.create_online_client(src["id"], "api")
