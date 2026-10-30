"""Private poster snapshots obtained from successful, authorized catalogue details."""

import time

from ..network.targets import TargetPolicy, TargetPolicyError
from . import online_state
from .online import OnlineNotFound
from .online_network import online_network_fingerprint, online_network_snapshot


def remember(access, source, item_id, poster_url, configuration):
    """Validate access, identity and configuration in the write transaction; no I/O."""
    with online_state._connection(write=True) as connection:
        online_state._scope(connection, access)
        item = connection.execute("SELECT source_id FROM online_items WHERE id=?", (item_id,)).fetchone()
        state = online_network_snapshot(connection, source["id"])
        if (item is None or item["source_id"] != source["id"] or not state["online_enabled"]
                or state["source_config"] != source or not source["enabled"]
                or online_network_fingerprint(state) != configuration):
            return False
        try:
            canonical = TargetPolicy(source["id"], source["allowed_hosts"])._canonical_url(poster_url)[0]
        except (TargetPolicyError, TypeError, ValueError):
            canonical = ""
        connection.execute(
            "INSERT INTO online_item_artwork(item_id,poster_url,configuration_fingerprint,updated_at)"
            " VALUES(?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET poster_url=excluded.poster_url,"
            "configuration_fingerprint=excluded.configuration_fingerprint,updated_at=excluded.updated_at",
            (item_id, canonical, configuration, time.time()))
        return True


def for_library(access, scope_token, item_ids):
    """Batch-read only the authorized page; source checks never contact a provider."""
    if not item_ids:
        return []
    if len(item_ids) > 100:
        raise ValueError("invalid online artwork page")
    with online_state._connection() as connection:
        online_state._scope(connection, access, scope_token)
        rows = connection.execute(
            "SELECT a.item_id,a.poster_url,a.configuration_fingerprint,i.source_id"
            " FROM online_item_artwork a JOIN online_items i ON i.id=a.item_id"
            f" WHERE a.item_id IN ({','.join('?' for _ in item_ids)})", item_ids).fetchall()
        groups = {}
        for source_id in dict.fromkeys(row["source_id"] for row in rows):
            try:
                state = online_network_snapshot(connection, source_id)
                source = state["source_config"]
                if state["online_enabled"] and source and source["enabled"]:
                    groups[source_id] = {"source": source, "configuration": online_network_fingerprint(state),
                                         "items": []}
            except (OnlineNotFound, ValueError):
                continue
        for row in rows:
            group = groups.get(row["source_id"])
            if group and row["configuration_fingerprint"] == group["configuration"] and row["poster_url"]:
                group["items"].append((row["item_id"], row["poster_url"]))
        return [group for group in groups.values() if group["items"]]


def confirm_scope(access, scope_token):
    with online_state._connection() as connection:
        online_state._scope(connection, access, scope_token)
