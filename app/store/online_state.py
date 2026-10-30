"""Bounded device/mode online state, validated in the same SQLite transaction."""

from contextlib import closing, contextmanager
import hashlib
import hmac
import json
import math
import re
import secrets
import time

from .. import viewing
from ..online.models import OnlinePlaybackIdentity
from . import _base


MAX_FAVORITES = 500
MAX_PROGRESS = 1000
WRITER_TTL_SECONDS = 7200
MAX_INTEGER = 2**53 - 1
_PUBLIC_PROGRESS = ("source_id,item_id,kind,episode_id,line_id,position,duration,"
                    "paused,completed,updated_at,writer_revision")


class OnlineStateError(RuntimeError):
    def __init__(self, category, status=409):
        self.category, self.status = category, status
        super().__init__(category)


@contextmanager
def _connection(*, write=False):
    with viewing.unrestricted(), _base._lock, closing(_base._conn()) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield connection


def _scope(connection, access, intent=None):
    """Never use a caller's owner/mode, and never validate outside the transaction."""
    owner = getattr(access, "owner", None)
    if not isinstance(owner, str) or not re.fullmatch(r"[0-9a-f]{64}", owner):
        raise OnlineStateError("viewing_session_required", 401)
    protection = connection.execute("SELECT revision,password_hash<>'' AS active FROM protection_settings WHERE id=1").fetchone()
    session = connection.execute("SELECT * FROM viewing_sessions WHERE token_hash=?", (owner,)).fetchone()
    now = time.time()
    if session is None or protection is None:
        raise OnlineStateError("viewing_session_invalid", 401)
    mode = session["mode"]
    if mode == "children" and protection["active"]:
        raise OnlineStateError("online_children_unavailable", 403)
    if (session["generation"] != getattr(access, "generation", None)
            or protection["revision"] != getattr(access, "protection_revision", None)
            or mode not in {"normal", "private", "children"}
            or mode == "private" and max(session["last_activity"] + viewing.IDLE_SECONDS,
                                          session["playback_until"]) <= now):
        raise OnlineStateError("online_access_revoked", 401)
    # A protection-disabled legacy children row is an ordinary viewing scope;
    # it cannot inherit any private records.
    mode = "private" if mode == "private" else "normal"
    value = json.dumps([owner, mode, session["generation"], protection["revision"], _epoch(connection, owner, mode)],
                       separators=(",", ":"))
    token = hashlib.sha256(value.encode()).hexdigest()
    if intent is not None and (not isinstance(intent, str) or not re.fullmatch(r"[0-9a-f]{64}", intent)
                               or not hmac.compare_digest(intent, token)):
        raise OnlineStateError("online_state_scope_changed")
    return owner, mode, {"generation": session["generation"], "scope_token": token}


def _epoch(connection, owner, mode):
    row = connection.execute("SELECT revision FROM online_state_scopes WHERE owner_hash=? AND scope=?",
                             (owner, mode)).fetchone()
    return row["revision"] if row is not None else 0


def _required_intent(value):
    # Empty is deliberately different from a read's no-intent argument.
    return value if value is not None else ""


def _item(connection, item_id):
    row = connection.execute(
        "SELECT i.id,i.source_id,i.external_id,i.kind,i.title,s.name AS source_name,"
        "s.provider,s.enabled AS source_enabled FROM online_items i"
        " JOIN online_sources s ON s.id=i.source_id WHERE i.id=?", (item_id,)).fetchone()
    if row is None:
        raise OnlineStateError("online_identity_not_found", 404)
    return dict(row)


def _availability(connection, item):
    feature = connection.execute("SELECT value FROM app_settings WHERE key='online_enabled'").fetchone()
    available = bool(feature and feature["value"] == "true" and item["source_enabled"])
    return {"available": available, "reason": "" if available else "online_source_disabled"}


def _enabled(connection, item):
    if not _availability(connection, item)["available"]:
        raise OnlineStateError("online_source_disabled", 503)


def _identity(connection, item, episode_id=None, line_id=None):
    if item["kind"] == "movie" and episode_id is not None:
        raise OnlineStateError("invalid_online_state_request", 422)
    if item["kind"] == "tv" and episode_id is None:
        return None
    if episode_id is not None:
        row = connection.execute("SELECT item_id FROM online_episodes WHERE id=?", (episode_id,)).fetchone()
        if row is None or row["item_id"] != item["id"]:
            raise OnlineStateError("online_identity_not_found", 404)
    try:
        return OnlinePlaybackIdentity.model_validate({"source_id": item["source_id"], "item_id": item["id"],
            "kind": "movie" if item["kind"] == "movie" else "episode", "episode_id": episode_id,
            "line_id": line_id})
    except (ValueError, TypeError):
        raise OnlineStateError("invalid_online_state_request", 422) from None


def _key(identity):
    return json.dumps(identity.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _progress(row):
    if row is None:
        return None
    identity = OnlinePlaybackIdentity.model_validate({key: row[key] for key in
        ("source_id", "item_id", "kind", "episode_id", "line_id")}).model_dump(mode="json")
    return {"identity": identity, "position": float(row["position"]),
            "duration": float(row["duration"]) if row["duration"] is not None else None,
            "paused": bool(row["paused"]), "completed": bool(row["completed"]),
            "updated_at": float(row["updated_at"]), "writer_revision": row["writer_revision"]}


def item_state(access, item_id, *, episode_id=None, line_id=None):
    with _connection() as connection:
        owner, mode, public_scope = _scope(connection, access)
        item = _item(connection, item_id)
        identity = _identity(connection, item, episode_id, line_id)
        favorite = connection.execute("SELECT 1 FROM online_favorites WHERE owner_hash=? AND scope=? AND item_id=?",
                                      (owner, mode, item_id)).fetchone() is not None
        latest = connection.execute(f"SELECT {_PUBLIC_PROGRESS} FROM online_progress"
            " WHERE owner_hash=? AND scope=? AND item_id=? AND updated_at>0"
            " ORDER BY updated_at DESC,identity_key LIMIT 1", (owner, mode, item_id)).fetchone()
        selected = (connection.execute(f"SELECT {_PUBLIC_PROGRESS} FROM online_progress"
            " WHERE owner_hash=? AND scope=? AND identity_key=?", (owner, mode, _key(identity))).fetchone()
            if identity is not None else None)
        return {"item_id": item_id, "favorite": favorite, "last_progress": _progress(latest),
                "selected_progress": _progress(selected), **_availability(connection, item), **public_scope}


def favorite(access, intent, item_id, enabled):
    if type(enabled) is not bool:
        raise OnlineStateError("invalid_online_state_request", 422)
    with _connection(write=True) as connection:
        owner, mode, public_scope = _scope(connection, access, _required_intent(intent))
        item = _item(connection, item_id)
        exists = connection.execute("SELECT 1 FROM online_favorites WHERE owner_hash=? AND scope=? AND item_id=?",
                                    (owner, mode, item_id)).fetchone() is not None
        if enabled:
            _enabled(connection, item)
            if not exists:
                count = connection.execute("SELECT COUNT(*) FROM online_favorites WHERE owner_hash=? AND scope=?",
                                           (owner, mode)).fetchone()[0]
                if count >= MAX_FAVORITES:
                    raise OnlineStateError("online_favorite_limit", 429)
                connection.execute("INSERT INTO online_favorites(owner_hash,scope,item_id,created_at) VALUES(?,?,?,?)",
                                   (owner, mode, item_id, time.time()))
        elif exists:
            connection.execute("DELETE FROM online_favorites WHERE owner_hash=? AND scope=? AND item_id=?",
                               (owner, mode, item_id))
        return {"item_id": item_id, "favorite": enabled, **public_scope}


def delete_history(access, intent, item_id):
    with _connection(write=True) as connection:
        owner, mode, public_scope = _scope(connection, access, _required_intent(intent))
        _item(connection, item_id)
        result = connection.execute("DELETE FROM online_progress WHERE owner_hash=? AND scope=? AND item_id=?",
                                    (owner, mode, item_id))
        deleted = result.rowcount
        # Bump even when no row existed: an older first claim with revision=0
        # must not resurrect history after the user explicitly cleared it.
        epoch = connection.execute("INSERT INTO online_state_scopes(owner_hash,scope,revision) VALUES(?,?,1)"
            " ON CONFLICT(owner_hash,scope) DO UPDATE SET revision=revision+1 WHERE revision<?",
            (owner, mode, MAX_INTEGER))
        if epoch.rowcount != 1:
            raise OnlineStateError("online_state_scope_limit", 429)
        _owner, _mode, refreshed_scope = _scope(connection, access)
        return {"deleted": deleted, **refreshed_scope}


def claim(access, intent, session, expected_revision):
    if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_INTEGER:
        raise OnlineStateError("invalid_online_state_request", 422)
    with _connection(write=True) as connection:
        owner, mode, public_scope = _scope(connection, access, _required_intent(intent))
        if session.owner != owner or session.context != access or session.cancelled.is_set():
            raise OnlineStateError("online_progress_session_invalid", 404)
        item = _item(connection, session.identity.item_id)
        _enabled(connection, item)
        identity = _identity(connection, item, session.identity.episode_id, session.identity.line_id)
        if identity is None or identity != session.identity or item["source_id"] != session.identity.source_id:
            raise OnlineStateError("online_identity_not_found", 404)
        key, now, scope_revision = _key(identity), time.time(), _epoch(connection, owner, mode)
        row = connection.execute("SELECT * FROM online_progress WHERE owner_hash=? AND scope=? AND identity_key=?",
                                 (owner, mode, key)).fetchone()
        # Same media-session retries are idempotent, not another takeover.
        if (row is not None and row["writer_session_id"] == session.id
                and row["writer_generation"] == access.generation
                and row["writer_protection_revision"] == access.protection_revision
                and row["writer_scope_revision"] == scope_revision
                and row["writer_expires_at"] > now):
            return _claim_result(row, public_scope)
        current_revision = row["writer_revision"] if row is not None else 0
        if current_revision != expected_revision or current_revision >= MAX_INTEGER:
            raise OnlineStateError("online_progress_writer_conflict")
        if row is None:
            # Only expired claims that never observed playback are disposable.
            # Saved history and live claims are never silently evicted.
            connection.execute("DELETE FROM online_progress WHERE owner_hash=? AND scope=?"
                               " AND updated_at=0 AND writer_expires_at<=?", (owner, mode, now))
            count = connection.execute("SELECT COUNT(*) FROM online_progress WHERE owner_hash=? AND scope=?",
                                       (owner, mode)).fetchone()[0]
            if count >= MAX_PROGRESS:
                raise OnlineStateError("online_progress_limit", 429)
        writer = secrets.token_urlsafe(32)
        revision, expires = current_revision + 1, now + WRITER_TTL_SECONDS
        if row is None:
            connection.execute("INSERT INTO online_progress(owner_hash,scope,identity_key,source_id,item_id,kind,"
                "episode_id,line_id,writer_id,writer_session_id,writer_revision,writer_generation,"
                "writer_protection_revision,writer_scope_revision,writer_expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (owner, mode, key, identity.source_id, identity.item_id, identity.kind, identity.episode_id,
                 identity.line_id, writer, session.id, revision, access.generation, access.protection_revision, scope_revision, expires))
        else:
            connection.execute("UPDATE online_progress SET writer_id=?,writer_session_id=?,writer_revision=?,"
                "writer_generation=?,writer_protection_revision=?,writer_scope_revision=?,writer_seq=0,writer_expires_at=?,writer_digest=''"
                " WHERE owner_hash=? AND scope=? AND identity_key=?",
                (writer, session.id, revision, access.generation, access.protection_revision, scope_revision, expires, owner, mode, key))
        row = connection.execute("SELECT * FROM online_progress WHERE owner_hash=? AND scope=? AND identity_key=?",
                                 (owner, mode, key)).fetchone()
        return _claim_result(row, public_scope)


def _claim_result(row, public_scope):
    progress = _progress(row)
    return {"identity": progress["identity"], "writer_id": row["writer_id"],
            "writer_revision": row["writer_revision"], "expires_at": row["writer_expires_at"],
            "progress": progress if row["updated_at"] > 0 else None, **public_scope}


def save_progress(access, intent, values):
    # Typed route/service validation also covers direct store callers.
    from ..online.state_models import ProgressBody
    try:
        body = ProgressBody.model_validate(values)
    except (ValueError, TypeError):
        raise OnlineStateError("invalid_online_state_request", 422) from None
    payload = body.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with _connection(write=True) as connection:
        owner, mode, public_scope = _scope(connection, access, _required_intent(intent))
        row = connection.execute("SELECT * FROM online_progress WHERE owner_hash=? AND scope=? AND writer_id=?",
                                 (owner, mode, body.writer_id)).fetchone()
        now = time.time()
        if (row is None or row["writer_revision"] != body.writer_revision
                or row["writer_generation"] != access.generation
                or row["writer_protection_revision"] != access.protection_revision
                or row["writer_scope_revision"] != _epoch(connection, owner, mode)
                or row["writer_expires_at"] <= now):
            raise OnlineStateError("online_progress_writer_conflict")
        if body.seq == row["writer_seq"] and hmac.compare_digest(row["writer_digest"], digest):
            return {"progress": _progress(row), **public_scope}
        if body.seq <= row["writer_seq"] or row["completed"] and row["writer_seq"] > 0 and not body.completed:
            raise OnlineStateError("online_progress_writer_conflict")
        position = min(body.position, body.duration) if body.duration is not None else body.position
        if not math.isfinite(position):
            raise OnlineStateError("invalid_online_state_request", 422)
        # Completion describes an actual ended observation; replay needs a new
        # claim. A zero-position completion packet cannot erase the endpoint.
        if body.completed and (body.duration is None or abs(position - body.duration) > 1):
            raise OnlineStateError("invalid_online_state_completion", 422)
        # The writer expires after inactivity, rather than after a fixed movie
        # length. Only a fully validated new sequence extends the lease; an
        # expired or replaced writer cannot revive itself with a late packet.
        connection.execute("UPDATE online_progress SET position=?,duration=?,paused=?,completed=?,updated_at=?,"
            "writer_seq=?,writer_digest=?,writer_expires_at=? WHERE owner_hash=? AND scope=? AND identity_key=?",
            (position, body.duration, int(body.paused), int(body.completed), now,
             body.seq, digest, now + WRITER_TTL_SECONDS, owner, mode, row["identity_key"]))
        updated = connection.execute(f"SELECT {_PUBLIC_PROGRESS} FROM online_progress"
            " WHERE owner_hash=? AND scope=? AND identity_key=?", (owner, mode, row["identity_key"])).fetchone()
        return {"progress": _progress(updated), **public_scope}


def library(access, section="continue", limit=20, offset=0):
    if (section not in {"favorites", "continue", "history"} or type(limit) is not int or not 1 <= limit <= 100
            or type(offset) is not int or not 0 <= offset <= MAX_PROGRESS):
        raise OnlineStateError("invalid_online_state_request", 422)
    with _connection() as connection:
        owner, mode, public_scope = _scope(connection, access)
        favorites = {row["item_id"]: row["created_at"] for row in connection.execute(
            "SELECT item_id,created_at FROM online_favorites WHERE owner_hash=? AND scope=?"
            " ORDER BY created_at DESC,item_id LIMIT ?", (owner, mode, MAX_FAVORITES))}
        latest = {}
        for row in connection.execute(f"SELECT {_PUBLIC_PROGRESS} FROM online_progress"
                " WHERE owner_hash=? AND scope=? AND updated_at>0 ORDER BY updated_at DESC,identity_key LIMIT ?",
                (owner, mode, MAX_PROGRESS)):
            latest.setdefault(row["item_id"], _progress(row))
        if section == "favorites":
            keys = sorted(favorites, key=lambda key: (-favorites[key], key))
        else:
            keys = [key for key, progress in latest.items() if section == "history"
                    or not progress["completed"] and progress["position"] > 0]
        selected, entries = keys[offset:offset + limit], []
        episode_ids = tuple({latest[key]["identity"]["episode_id"] for key in selected
                             if latest.get(key) and latest[key]["identity"].get("episode_id")})
        episode_labels = {row["id"]: (row["item_id"], row["title"]) for row in connection.execute(
            f"SELECT id,item_id,title FROM online_episodes WHERE id IN ({','.join('?' for _ in episode_ids)})",
            episode_ids)} if episode_ids else {}
        for item_id in selected:
            item = _item(connection, item_id)
            progress = latest.get(item_id)
            episode = episode_labels.get(progress["identity"].get("episode_id")) if progress else None
            entries.append({"item": {key: item[key] for key in ("id", "source_id", "external_id", "kind", "title")},
                "source": {"id": item["source_id"], "name": item["source_name"], "provider": item["provider"],
                           "enabled": bool(item["source_enabled"])},
                "favorite": item_id in favorites, "progress": progress, "poster_path": "",
                "episode_label": episode[1] if episode and episode[0] == item_id else "",
                **_availability(connection, item)})
        return {"items": entries, "total": len(keys), "limit": limit, "offset": offset, **public_scope}
