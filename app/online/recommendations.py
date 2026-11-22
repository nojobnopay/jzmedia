"""Short-lived, viewer-bound proofs for verified alternate sessions."""

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
import secrets
from threading import Lock
import time

from ..store import online, online_network
from . import service
from .models import OnlinePlaybackIdentity
from .providers import PlaybackMatch


_TTL = 300
_lock = Lock()
_offers = OrderedDict()
_origins = OrderedDict()


def path_digest(url):
    return sha256(url.encode()).digest()


def _put(table, key, value, limit):
    now = time.monotonic()
    with _lock:
        table.pop(key, None)
        while table and (len(table) >= limit or next(iter(table.values()))[0] <= now):
            table.popitem(last=False)
        table[key] = (now + _TTL, value)


def _get(table, key):
    with _lock:
        expiry, value = table.get(key, (0, None))
        return value if expiry > time.monotonic() else None


def sweep():
    now = time.monotonic()
    with _lock:
        for table in (_offers, _origins):
            while table and next(iter(table.values()))[0] <= now:
                table.popitem(last=False)


@dataclass
class Offer:
    access: service.PlaybackAccess
    identity: OnlinePlaybackIdentity
    match: PlaybackMatch
    configuration: str

    prepared_id: str | None = None
    _preparation: Lock = field(default_factory=Lock, repr=False)

    @contextmanager
    def reserve(self):
        # A retry after a player error may have both device slots available.
        # Do not publish two media sessions for one concurrent selection.
        if not self._preparation.acquire(blocking=False):
            raise service.PlaybackError("session_limit", 429)
        try:
            if self.prepared_id and any(session.id == self.prepared_id for session in service.registry.sessions()):
                raise service.PlaybackError("session_limit", 429)
            yield
        finally:
            self._preparation.release()

    def check_configuration(self):
        try:
            state = online_network.load_online_network_state(self.identity.source_id)
        except online.OnlineNotFound:
            raise service.PlaybackError("online_recommendation_changed", 409) from None
        if online_network.online_network_fingerprint(state) != self.configuration:
            raise service.PlaybackError("online_recommendation_changed", 409)

    def check_match(self, current):
        if not self.match.same_edition(current):
            raise service.PlaybackError("online_recommendation_changed", 409)

    def check_cache(self, cached):
        detail = cached.get("detail") or {}
        item = detail.get("item") or {}
        identity = self.identity
        kind = "movie" if identity.kind == "movie" else "tv"
        if (item.get("id") != identity.item_id or item.get("source_id") != identity.source_id
                or item.get("kind") != kind or not isinstance(item.get("title"), str)):
            raise service.PlaybackError("online_recommendation_changed", 409)
        for line in detail.get("lines", ()):
            if line.get("id") != identity.line_id:
                continue
            for entry in line.get("entries", ()):
                selected = entry.get("identity") or {}
                if (entry.get("supported") and isinstance(entry.get("label"), str) and
                        all(selected.get(key) == value for key, value in identity.model_dump().items())):
                    self.check_match(PlaybackMatch.from_metadata(item["title"], detail.get("year", ""), entry["label"], detail))
                    return
        raise service.PlaybackError("online_recommendation_changed", 409)


def issue(access, identity, match, configuration):
    token = secrets.token_urlsafe(24)
    _put(_offers, token, Offer(access, OnlinePlaybackIdentity.model_validate(identity), match, configuration), 512)
    return token


def accept(token, access, identity):
    offer = _get(_offers, token)
    if offer is None or offer.access != access or offer.identity != identity:
        raise service.PlaybackError("online_recommendation_expired", 409)
    offer.check_configuration()
    return offer


def remember_origin(session):
    # The player may release its media session on a fatal network error before
    # the user tries another line. Retain no media URL, client or socket.
    if session.playback_match and session.playback_configuration:
        _put(_origins, (session.context, session.identity), (
            session.playback_configuration, session.playback_match, path_digest(session.entry.url)), 128)


def last_origin(access, identity, configuration):
    saved = _get(_origins, (access, identity))
    if saved:
        if saved[0] != configuration:
            raise service.PlaybackError("online_recommendation_changed", 409)
        return saved[1:]
    return None, None
