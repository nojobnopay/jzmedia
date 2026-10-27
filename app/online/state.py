"""Authenticated online state orchestration; never contacts a provider."""

import sqlite3

from ..store import online_state as storage
from . import service
from .sessions import SessionError


OnlineStateError = storage.OnlineStateError


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except sqlite3.Error:
        raise OnlineStateError("online_state_unavailable", 503) from None


def library(access, section="continue", limit=20, offset=0):
    return _call(storage.library, access, section, limit, offset)


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
