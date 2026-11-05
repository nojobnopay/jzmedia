"""Small transactional ledger; signed playback URLs are never persisted."""

from contextlib import closing, contextmanager
import hashlib
import json
import re
import time

from .. import viewing
from . import _base


@contextmanager
def _connection():
    with viewing.unrestricted(), _base._lock, closing(_base._conn()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        yield connection


def _row(row, connection, snapshots=None):
    if row is None:
        return None
    value = {**json.loads(row["payload"]), "id": row["id"], "state": row["state"],
             "created_at": row["created_at"], "updated_at": row["updated_at"]}
    if value.get("detail_id"):
        if snapshots is not None:
            value["detail"] = snapshots[value["detail_id"]]
        else:
            snapshot = connection.execute("SELECT payload FROM online_cache_snapshots WHERE id=?",
                                          (value["detail_id"],)).fetchone()
            value["detail"] = json.loads(snapshot["payload"])
    return value


def _snapshot(connection, detail):
    payload = json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    key = hashlib.sha256(payload.encode()).hexdigest()
    connection.execute("INSERT OR IGNORE INTO online_cache_snapshots(id,payload) VALUES(?,?)", (key, payload))
    return key


def save_snapshot(detail):
    with _connection() as connection:
        return _snapshot(connection, detail)


def root_identity(initial=None):
    """Keep mount identity even after the final download has been deleted."""
    with _connection() as connection:
        row = connection.execute("SELECT value FROM app_settings WHERE key='online_cache_root_identity'").fetchone()
        if row is not None:
            if not re.fullmatch(r"[0-9a-f]{32}", row["value"]):
                raise ValueError("invalid online cache storage identity")
            return row["value"]
        if initial is not None:
            if not isinstance(initial, str) or not re.fullmatch(r"[0-9a-f]{32}", initial):
                raise ValueError("invalid online cache storage identity")
            connection.execute("INSERT INTO app_settings(key,value) VALUES('online_cache_root_identity',?)", (initial,))
            return initial
        return None


def _dump(connection, value):
    value = dict(value)
    detail = value.pop("detail", None)
    if detail is not None and not value.get("detail_id"):
        value["detail_id"] = _snapshot(connection, detail)
    return json.dumps(value, ensure_ascii=False)


def rows():
    with _connection() as connection:
        snapshots = {row["id"]: json.loads(row["payload"]) for row in connection.execute(
            "SELECT * FROM online_cache_snapshots")}
        return [_row(row, connection, snapshots) for row in connection.execute(
            "SELECT * FROM online_cache_tasks ORDER BY created_at,rowid")]


def get(task_id):
    with _connection() as connection:
        return _row(connection.execute("SELECT * FROM online_cache_tasks WHERE id=?", (task_id,)).fetchone(), connection)


def insert(value, identity_key):
    with _connection() as connection:
        old = connection.execute("SELECT * FROM online_cache_tasks WHERE identity_key=?", (identity_key,)).fetchone()
        if old is not None:
            return _row(old, connection)
        now = int(time.time())
        connection.execute(
            "INSERT INTO online_cache_tasks(id,identity_key,item_id,source_id,state,payload,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)", (value["id"], identity_key, value["identity"]["item_id"],
            value["identity"]["source_id"], value["state"], _dump(connection, value), now, now))
        return {**value, "created_at": now, "updated_at": now}


def update(task_id, *, expected_state=None, **changes):
    with _connection() as connection:
        row = connection.execute("SELECT * FROM online_cache_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        if expected_state is not None and row["state"] != expected_state:
            return _row(row, connection)
        value = {**_row(row, connection), **changes}
        connection.execute("UPDATE online_cache_tasks SET state=?,payload=?,updated_at=? WHERE id=?",
                           (value["state"], _dump(connection, value), int(time.time()), task_id))
        return value


def remove(task_id):
    with _connection() as connection:
        connection.execute("DELETE FROM online_cache_tasks WHERE id=?", (task_id,))
        connection.execute("DELETE FROM online_cache_snapshots WHERE NOT EXISTS (SELECT 1 FROM online_cache_tasks"
                           " WHERE json_extract(online_cache_tasks.payload,'$.detail_id')=online_cache_snapshots.id)")


def recover():
    with _connection() as connection:
        connection.execute("UPDATE online_cache_tasks SET state='queued' WHERE state='downloading'")
