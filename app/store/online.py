"""Persistent online identities and administrator configuration; no upstream I/O."""

from contextlib import closing, contextmanager
import json
import time
import uuid

from ..online import source_migration
from ..online.models import (OnlineEpisode, OnlineItem, OnlineSource,
                             OnlineSourceCreate, OnlineSourcePatch,
                             normalize_external_id)
from ..online.limits import MAX_CATALOG_ENTRIES
from . import _base


class OnlineNotFound(LookupError):
    """The referenced online identity is absent."""


@contextmanager
def _connection(*, write=False):
    # Existing store connections deliberately do not enable foreign keys
    # globally. Enforce them on this module's connections only.
    with _base._lock, closing(_base._conn()) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        if write:
            connection.execute("BEGIN IMMEDIATE")
        yield connection


def _source(row):
    if row is None:
        return None
    values = dict(row)
    values["allowed_hosts"] = json.loads(values["allowed_hosts"])
    values["enabled"] = bool(values["enabled"])
    values["provider_options"] = json.loads(values["provider_options"])
    return OnlineSource.model_validate(values).model_dump(mode="json")


def _item(row):
    return OnlineItem.model_validate(dict(row)).model_dump(mode="json") if row else None


def _episode(row):
    return OnlineEpisode.model_validate(dict(row)).model_dump(mode="json") if row else None


def get_online_settings() -> dict:
    """Opt-in is database-only, independent of environment proxy or other flags."""
    with _connection() as connection:
        row = connection.execute("SELECT value FROM app_settings WHERE key='online_enabled'").fetchone()
    # Missing or damaged settings fail closed.
    return {"enabled": bool(row and row["value"] == "true")}


def update_online_settings(enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError("在线功能启用状态须为布尔值")
    with _connection(write=True) as connection:
        connection.execute(
            "INSERT INTO app_settings(key,value,updated_at) VALUES('online_enabled',?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            ("true" if enabled else "false", int(time.time())),
        )
    return {"enabled": enabled}


def list_online_sources() -> list[dict]:
    with _connection() as connection:
        return [_source(row) for row in connection.execute(
            "SELECT * FROM online_sources ORDER BY priority,created_at,id"
        )]


def get_online_source(source_id: str) -> dict | None:
    with _connection() as connection:
        return _source(connection.execute("SELECT * FROM online_sources WHERE id=?", (source_id,)).fetchone())


def source_revision(connection, source_id):
    row = connection.execute("SELECT value FROM app_settings WHERE key=?",
                             ("online_source_revision:" + source_id,)).fetchone()
    return int(row["value"]) if row else 0


def _advance_source_revision(connection, source_id):
    connection.execute(
        "INSERT INTO app_settings(key,value,updated_at) VALUES(?,'1',?) ON CONFLICT(key) DO UPDATE"
        " SET value=CAST(app_settings.value AS INTEGER)+1,updated_at=excluded.updated_at",
        ("online_source_revision:" + source_id, int(time.time())),
    )


def create_online_source(values: dict) -> dict:
    with _connection(write=True) as connection:
        return insert_online_source(connection, values)


def insert_online_source(connection, values: dict) -> dict:
    """Insert in the caller's write transaction for atomic discovery selection."""
    source = OnlineSourceCreate.model_validate(values).model_dump(mode="json")
    now = int(time.time())
    record = OnlineSource.model_validate({**source, "id": "src_" + uuid.uuid4().hex,
                                         "created_at": now, "updated_at": now}).model_dump(mode="json")
    connection.execute(
        "INSERT INTO online_sources(id,provider,name,api_base_url,allowed_hosts,enabled,priority,created_at,updated_at,provider_options)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (record["id"], record["provider"], record["name"], record["api_base_url"],
         json.dumps(record["allowed_hosts"]), int(record["enabled"]), record["priority"], now, now,
         json.dumps(record["provider_options"])),
    )
    return record


def update_online_source(source_id: str, changes: dict) -> dict:
    return update_online_source_with_change(source_id, changes)[0]


def replace_catalogue(source_id, request):
    """Retain the old source and its offline identities, with a separate identity for the new catalogue."""
    changes = request.model_dump(mode="json", exclude_unset=True)
    digest = source_migration.fingerprint(changes)
    selection = changes.pop("host_migration")
    with _connection(write=True) as connection:
        previous = _source(connection.execute("SELECT * FROM online_sources WHERE id=?", (source_id,)).fetchone())
        if previous is None:
            raise OnlineNotFound("在线来源不存在")
        if previous["provider"] != "maccms":
            raise ValueError("固定开放样例不能更换片库")
        if changes["api_base_url"] == previous["api_base_url"]:
            raise ValueError("新片库需要不同的接口地址")
        receipt_key = "online_catalogue_replacement:" + source_id
        row = connection.execute("SELECT value FROM app_settings WHERE key=?", (receipt_key,)).fetchone()
        if row:
            receipt = json.loads(row["value"])
            if digest == receipt["request"]:
                new_id = receipt["source"]
                existing = _source(connection.execute("SELECT * FROM online_sources WHERE id=?", (new_id,)).fetchone())
                if (existing is None or source_revision(connection, source_id) != receipt["previous_revision"]
                        or source_revision(connection, new_id) != receipt["source_revision"]):
                    raise source_migration.SourceChanged()
                return {"previous": previous, "source": existing}, False
        changes["allowed_hosts"] = source_migration.hosts_for_update(previous, changes, selection)
        configuration = {key: previous[key] for key in OnlineSourceCreate.model_fields}
        source = insert_online_source(connection, {**configuration, **changes})
        connection.execute(
            "INSERT INTO online_network_policies(source_id,rules_json,revision,updated_at)"
            " SELECT ?,rules_json,1,? FROM online_network_policies WHERE source_id=?",
            (source["id"], source["created_at"], source_id),
        )
        suffix = "（旧片库）"
        name = previous["name"].removesuffix(suffix)[:128 - len(suffix)] + suffix
        # Advance the fingerprint even when an already disabled source is replaced in the same second.
        updated = max(int(time.time()), previous["updated_at"] + 1)
        connection.execute("UPDATE online_sources SET name=?,enabled=0,updated_at=? WHERE id=?",
                           (name, updated, source_id))
        _advance_source_revision(connection, source_id)
        previous.update(name=name, enabled=False, updated_at=updated)
        receipt = {"request": digest, "source": source["id"], "previous_revision": source_revision(connection, source_id),
                   "source_revision": source_revision(connection, source["id"])}
        connection.execute("INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES(?,?,?)",
                           (receipt_key, json.dumps(receipt), updated))
        return {"previous": previous, "source": source}, True


def update_online_source_with_change(source_id: str, changes: dict) -> tuple[dict, bool]:
    """Return committed configuration and its material change from one transaction."""
    patch = OnlineSourcePatch.model_validate(changes).model_dump(mode="json", exclude_unset=True)
    with _connection(write=True) as connection:
        previous = _source(connection.execute("SELECT * FROM online_sources WHERE id=?", (source_id,)).fetchone())
        if previous is None:
            raise OnlineNotFound("在线来源不存在")
        return _update_source(connection, previous, patch)


def _update_source(connection, previous, patch):
    source_id = previous["id"]
    if not patch:
        return previous, False
    selection = patch.pop("host_migration", None)
    if selection is not None or "api_base_url" in patch:
        patch["allowed_hosts"] = source_migration.hosts_for_update(previous, patch, selection)
    record = OnlineSource.model_validate({**previous, **patch, "updated_at": int(time.time())}).model_dump(mode="json")
    if all(previous[key] == record[key] for key in record if key != "updated_at"):
        return previous, False
    if record["api_base_url"] != previous["api_base_url"]:
        connection.execute("DELETE FROM app_settings WHERE key=?", ("online_operator:" + source_id,))
    connection.execute(
        "UPDATE online_sources SET name=?,api_base_url=?,allowed_hosts=?,enabled=?,priority=?,updated_at=?,provider_options=? WHERE id=?",
        (record["name"], record["api_base_url"], json.dumps(record["allowed_hosts"]),
         int(record["enabled"]), record["priority"], record["updated_at"],
         json.dumps(record["provider_options"]), source_id),
    )
    changed = any(previous[key] != record[key]
                  for key in ("api_base_url", "allowed_hosts", "provider_options", "enabled"))
    _advance_source_revision(connection, source_id)
    return record, changed


def get_online_item(item_id: str) -> dict | None:
    with _connection() as connection:
        return _item(connection.execute("SELECT * FROM online_items WHERE id=?", (item_id,)).fetchone())


def upsert_online_item(source_id: str, external_id, kind: str, title: str = "") -> dict:
    external_id = normalize_external_id(external_id)
    now = int(time.time())
    candidate = OnlineItem.model_validate({"id": "itm_" + uuid.uuid4().hex, "source_id": source_id,
                                          "external_id": external_id, "kind": kind, "title": title,
                                          "created_at": now, "updated_at": now}).model_dump(mode="json")
    with _connection(write=True) as connection:
        if connection.execute("SELECT 1 FROM online_sources WHERE id=?", (source_id,)).fetchone() is None:
            raise OnlineNotFound("在线来源不存在")
        previous = _item(connection.execute(
            "SELECT * FROM online_items WHERE source_id=? AND kind=? AND external_id=?",
            (source_id, kind, external_id),
        ).fetchone())
        if previous:
            candidate = {**previous, "title": candidate["title"], "updated_at": now}
            connection.execute("UPDATE online_items SET title=?,updated_at=? WHERE id=?",
                               (candidate["title"], now, previous["id"]))
        else:
            connection.execute(
                "INSERT INTO online_items(id,source_id,external_id,kind,title,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                tuple(candidate[key] for key in ("id", "source_id", "external_id", "kind", "title", "created_at", "updated_at")),
            )
    return candidate


def get_online_episode(episode_id: str) -> dict | None:
    with _connection() as connection:
        return _episode(connection.execute("SELECT * FROM online_episodes WHERE id=?", (episode_id,)).fetchone())


def upsert_online_episode(item_id: str, external_id, title: str = "", season: int | None = None,
                          episode: int | None = None, sort_order: int = 0) -> dict:
    external_id = normalize_external_id(external_id)
    now = int(time.time())
    candidate = OnlineEpisode.model_validate({"id": "ep_" + uuid.uuid4().hex, "item_id": item_id,
                                             "external_id": external_id, "title": title,
                                             "season": season, "episode": episode, "sort_order": sort_order,
                                             "created_at": now, "updated_at": now}).model_dump(mode="json")
    with _connection(write=True) as connection:
        parent = connection.execute("SELECT kind FROM online_items WHERE id=?", (item_id,)).fetchone()
        if parent is None:
            raise OnlineNotFound("在线内容不存在")
        if parent["kind"] != "tv":
            raise ValueError("仅剧集条目支持在线分集")
        previous = _episode(connection.execute(
            "SELECT * FROM online_episodes WHERE item_id=? AND external_id=?", (item_id, external_id),
        ).fetchone())
        if previous:
            candidate = {**candidate, "id": previous["id"], "created_at": previous["created_at"]}
            connection.execute(
                "UPDATE online_episodes SET title=?,season=?,episode=?,sort_order=?,updated_at=? WHERE id=?",
                (candidate["title"], season, episode, sort_order, now, previous["id"]),
            )
        else:
            connection.execute(
                "INSERT INTO online_episodes(id,item_id,external_id,title,season,episode,sort_order,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                tuple(candidate[key] for key in ("id", "item_id", "external_id", "title", "season", "episode",
                                                  "sort_order", "created_at", "updated_at")),
            )
    return candidate


def save_online_catalog(source_id: str, external_id, kind: str, title: str, episodes: list[dict]) -> dict:
    """Save one already-validated detail in one bounded identity transaction.

    URLs are never accepted here. Old episode identities remain available for
    history, but a provider must find them in fresh detail before playback.
    """
    if not isinstance(episodes, list) or len(episodes) > MAX_CATALOG_ENTRIES or kind == "movie" and episodes:
        raise ValueError("invalid online catalogue")
    now = int(time.time())
    candidate = OnlineItem.model_validate({"id": "itm_" + uuid.uuid4().hex, "source_id": source_id,
                                          "external_id": external_id, "kind": kind, "title": title,
                                          "created_at": now, "updated_at": now}).model_dump(mode="json")
    prepared, seen = [], set()
    allowed = {"external_id", "title", "season", "episode", "sort_order"}
    for values in episodes:
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError("invalid online catalogue")
        row = OnlineEpisode.model_validate({**values, "id": "ep_" + uuid.uuid4().hex,
                                            "item_id": candidate["id"], "created_at": now,
                                            "updated_at": now}).model_dump(mode="json")
        if row["external_id"] in seen:
            raise ValueError("ambiguous online episode identity")
        seen.add(row["external_id"])
        prepared.append(row)
    with _connection(write=True) as connection:
        source = connection.execute("SELECT enabled FROM online_sources WHERE id=?", (source_id,)).fetchone()
        feature = connection.execute("SELECT value FROM app_settings WHERE key='online_enabled'").fetchone()
        if source is None or not source["enabled"] or feature is None or feature["value"] != "true":
            raise OnlineNotFound("在线来源不可用")
        previous = _item(connection.execute(
            "SELECT * FROM online_items WHERE source_id=? AND kind=? AND external_id=?",
            (source_id, kind, candidate["external_id"]),
        ).fetchone())
        if previous:
            candidate = {**candidate, "id": previous["id"], "created_at": previous["created_at"]}
            connection.execute("UPDATE online_items SET title=?,updated_at=? WHERE id=?",
                               (candidate["title"], now, candidate["id"]))
        else:
            connection.execute(
                "INSERT INTO online_items(id,source_id,external_id,kind,title,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                tuple(candidate[key] for key in ("id", "source_id", "external_id", "kind", "title", "created_at", "updated_at")),
            )
        item_id = candidate["id"]
        # Read only the bounded requested identities. Repeated refreshes may
        # retain more historical rows, which must not turn into an unbounded
        # all-episodes read on each subsequent detail request.
        previous_episodes = {}
        keys = tuple(seen)
        for start in range(0, len(keys), 400):
            batch = keys[start:start + 400]
            previous_episodes.update({row["external_id"]: _episode(row) for row in connection.execute(
                f"SELECT * FROM online_episodes WHERE item_id=? AND external_id IN ({','.join('?' for _ in batch)})",
                (item_id, *batch))})
        for row in prepared:
            previous = previous_episodes.get(row["external_id"])
            row["item_id"] = item_id
            if previous:
                row["id"], row["created_at"] = previous["id"], previous["created_at"]
                connection.execute(
                    "UPDATE online_episodes SET title=?,season=?,episode=?,sort_order=?,updated_at=? WHERE id=?",
                    tuple(row[key] for key in ("title", "season", "episode", "sort_order", "updated_at", "id")),
                )
            else:
                connection.execute(
                    "INSERT INTO online_episodes(id,item_id,external_id,title,season,episode,sort_order,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    tuple(row[key] for key in ("id", "item_id", "external_id", "title", "season", "episode", "sort_order",
                                              "created_at", "updated_at")),
                )
    return {"item": candidate, "episodes": prepared}
