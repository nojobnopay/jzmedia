"""Online playback ownership, source gates and revocable viewing permissions."""

from contextlib import closing
from dataclasses import dataclass, field
import sqlite3
import time

from .. import viewing
from ..network.client import create_online_client
from ..network.policy import NetworkPolicyError
from ..store import _base
from ..store.online import OnlineNotFound
from . import providers
from .models import OnlinePlaybackIdentity
from .sessions import SessionError, SessionRegistry


registry = SessionRegistry()


class PlaybackError(RuntimeError):
    def __init__(self, category, status=403):
        self.category, self.status = category, status
        super().__init__(category)


@dataclass(frozen=True)
class PlaybackAccess:
    owner: str = field(repr=False)
    generation: int
    protection_revision: int


def _state(source_ids=(), owners=()):
    """Read bounded feature/source/viewing state from a single SQLite snapshot."""
    try:
        with viewing.unrestricted(), closing(_base._conn()) as connection:
            connection.execute("BEGIN")
            feature = connection.execute("SELECT value FROM app_settings WHERE key='online_enabled'").fetchone()
            protection = connection.execute(
                "SELECT revision,password_hash<>'' AS active FROM protection_settings WHERE id=1").fetchone()
            sources, sessions = {}, {}
            if source_ids:
                keys = tuple(set(source_ids))
                sources = {row["id"]: bool(row["enabled"]) for row in connection.execute(
                    f"SELECT id,enabled FROM online_sources WHERE id IN ({','.join('?' for _ in keys)})", keys)}
            if owners:
                keys = tuple(set(owners))
                sessions = {row["token_hash"]: dict(row) for row in connection.execute(
                    f"SELECT * FROM viewing_sessions WHERE token_hash IN ({','.join('?' for _ in keys)})", keys)}
            if protection is None:
                raise PlaybackError("online_access_unavailable", 503)
            return {"enabled": bool(feature and feature["value"] == "true"), "sources": sources,
                    "sessions": sessions, "revision": protection["revision"], "protected": bool(protection["active"])}
    except sqlite3.Error:
        raise PlaybackError("online_access_unavailable", 503) from None


def _access_valid(access, state, now):
    if not isinstance(access, PlaybackAccess) or state["revision"] != access.protection_revision:
        return False
    session = state["sessions"].get(access.owner)
    return bool(session and session["generation"] == access.generation
                and not (state["protected"] and session["mode"] == "children")
                and (session["mode"] != "private" or max(session["last_activity"] + viewing.IDLE_SECONDS,
                                                            session["playback_until"]) > now))


def capture_access(request):
    """Bind even unprotected installations to an existing device session."""
    supplied = request.headers.get(viewing.HEADER) or request.cookies.get(viewing.COOKIE, "")
    if not isinstance(supplied, str) or not supplied or len(supplied) > 1024:
        raise PlaybackError("viewing_session_required", 401)
    try:
        session, _secret, expired = viewing.resolve(supplied)
    except sqlite3.Error:
        raise PlaybackError("online_access_unavailable", 503) from None
    if session is None or expired:
        raise PlaybackError("viewing_session_invalid", 401)
    state = _state(owners=(session["token_hash"],))
    current = state["sessions"].get(session["token_hash"])
    if current is None:
        raise PlaybackError("viewing_session_invalid", 401)
    if state["protected"] and current["mode"] == "children":
        raise PlaybackError("online_children_unavailable", 403)
    access = PlaybackAccess(current["token_hash"], current["generation"], state["revision"])
    if not _access_valid(access, state, time.time()):
        raise PlaybackError("viewing_session_invalid", 401)
    return access


def _identity_ready(identity):
    try:
        with viewing.unrestricted(), closing(_base._conn()) as connection:
            connection.execute("BEGIN")
            item = connection.execute("SELECT source_id,kind FROM online_items WHERE id=?", (identity.item_id,)).fetchone()
            source = connection.execute("SELECT enabled FROM online_sources WHERE id=?", (identity.source_id,)).fetchone()
            episode = (connection.execute("SELECT item_id FROM online_episodes WHERE id=?", (identity.episode_id,)).fetchone()
                       if identity.kind == "episode" else None)
            if (item is None or source is None or item["source_id"] != identity.source_id
                    or identity.kind == "movie" and item["kind"] != "movie"
                    or identity.kind == "episode" and (item["kind"] != "tv" or episode is None
                                                       or episode["item_id"] != identity.item_id)):
                raise PlaybackError("online_identity_not_found", 404)
            feature = connection.execute("SELECT value FROM app_settings WHERE key='online_enabled'").fetchone()
            if not feature or feature["value"] != "true" or not source["enabled"]:
                raise PlaybackError("online_source_disabled", 503)
    except sqlite3.Error:
        raise PlaybackError("online_access_unavailable", 503) from None


def _current_access(access):
    state = _state(owners=(access.owner,))
    if not _access_valid(access, state, time.time()):
        raise PlaybackError("online_access_revoked", 401)


def _resolved_source_current(identity, resolved):
    from . import catalog
    try:
        catalog.assert_source_version(identity.source_id, resolved.source_fingerprint)
    except catalog.CatalogError as error:
        raise PlaybackError(error.category, error.status) from None


def create(identity, access):
    try:
        identity = OnlinePlaybackIdentity.model_validate(identity)
    except (ValueError, TypeError):
        raise PlaybackError("invalid_online_identity", 422) from None
    if not isinstance(access, PlaybackAccess):
        raise PlaybackError("viewing_session_required", 401)
    _current_access(access)
    _identity_ready(identity)
    try:
        from . import catalog
        with catalog.resolution_owner(access.owner):
            resolved = providers.resolve(identity)
    except catalog.CatalogError as error:
        raise PlaybackError(error.category, error.status) from None
    except providers.ProviderError:
        raise
    except Exception:
        # Providers are a separate boundary. No third-party exception text,
        # signed URL or headers may become an API error body.
        raise providers.ProviderError("provider_resolution_failed") from None
    if not isinstance(resolved, providers.ResolvedPlayback):
        raise providers.ProviderError("invalid_provider_result")
    _resolved_source_current(identity, resolved)
    try:
        client = create_online_client(identity.source_id, "media", header_policy=resolved.header_policy)
    except (NetworkPolicyError, OnlineNotFound, sqlite3.Error, ValueError):
        raise PlaybackError("online_source_unavailable", 503) from None
    session = registry.create(identity, access.owner, client, resolved.url, resolved.transport,
                              seekable=resolved.seekable, duration_seconds=resolved.duration_seconds, context=access)
    try:
        # Source or permission changes racing provider resolution/DNS must not
        # publish a newly active session after the gate has been revoked.
        _identity_ready(identity)
        _resolved_source_current(identity, resolved)
        authorize(session.id, access)
    except Exception:
        try:
            registry.close(session.id, access.owner)
        except SessionError:
            pass
        raise
    return session


def authorize(session_id, access):
    if not isinstance(access, PlaybackAccess):
        raise PlaybackError("viewing_session_required", 401)
    # Ownership comes before all source/permission explanations, preventing
    # another device from learning why a guessed session is unavailable.
    session = registry.get(session_id, access.owner)
    try:
        state = _state((session.source_id,), (access.owner,))
        if session.context != access or not _access_valid(access, state, time.time()):
            raise PlaybackError("online_access_revoked", 401)
        if not state["enabled"] or not state["sources"].get(session.source_id, False):
            raise PlaybackError("online_source_disabled", 503)
    except Exception:
        try:
            registry.close(session.id, access.owner)
        except SessionError:
            pass
        raise
    return session


def delete(session_id, access):
    session = authorize(session_id, access)
    registry.close(session.id, access.owner)


def sweep():
    """Called by the application maintenance loop; no thread starts at import."""
    registry.sweep()
    sessions = registry.sessions()
    if not sessions:
        return
    try:
        state = _state(tuple(s.source_id for s in sessions), tuple(s.owner for s in sessions))
    except PlaybackError:
        registry.close_all()
        raise
    now = time.time()
    for session in sessions:
        if (not state["enabled"] or not state["sources"].get(session.source_id, False)
                or not _access_valid(session.context, state, now)):
            try:
                registry.close(session.id, session.owner)
            except SessionError:
                pass
