"""Authenticated online state orchestration; never contacts a provider."""

import sqlite3

from ..store import online_state as storage
from ..store import online_artwork
from . import artwork, service
from .sessions import SessionError


OnlineStateError = storage.OnlineStateError


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except sqlite3.Error:
        raise OnlineStateError("online_state_unavailable", 503) from None


def library(access, section="continue", limit=20, offset=0):
    try:
        return _library(access, section, limit, offset)
    except OnlineStateError as error:
        if error.category != "online_state_scope_changed":
            raise
        # Clearing history can advance the epoch between the row snapshot and
        # poster enrichment. Retry this read once under the original access;
        # revoked viewing identities must never inherit the refreshed scope.
        service._current_access(access)
        return _library(access, section, limit, offset)


def _library(access, section, limit, offset):
    result = _call(storage.library, access, section, limit, offset)
    entries = {entry["item"]["id"]: entry for entry in result["items"] if entry["available"]}
    groups = _call(online_artwork.for_library, access, result["scope_token"], tuple(entries))
    for group in groups:
        paths = artwork.register_many(group["source"], [url for _item_id, url in group["items"]],
                                      expected_configuration=group["configuration"])
        for (item_id, _url), path in zip(group["items"], paths):
            entries[item_id]["poster_path"] = path
    _call(online_artwork.confirm_scope, access, result["scope_token"])
    return result


def item_state(access, item_id, *, episode_id=None, line_id=None):
    return _call(storage.item_state, access, item_id, episode_id=episode_id, line_id=line_id)


def favorite(access, scope_token, item_id, enabled):
    return _call(storage.favorite, access, scope_token, item_id, enabled)


def delete_history(access, scope_token, item_id):
    return _call(storage.delete_history, access, scope_token, item_id)


def claim(access, scope_token, body):
    try:
        session = service.authorize(body.session_id, access)
    except SessionError:
        raise OnlineStateError("online_progress_session_invalid", 404) from None
    return _call(storage.claim, access, scope_token, session, body.expected_revision)


def save_progress(access, scope_token, body):
    return _call(storage.save_progress, access, scope_token, body)
