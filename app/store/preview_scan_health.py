"""Conservative, persistent evidence that a library scan completed.

Missing evidence, interrupted scans and storage errors all mean unknown. This
record is independent of the per-file scan_state cache.
"""
import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager

from ._base import _conn, _lock
from .libraries import get_library
from .fs_changes import fs_change_summary

_guard = threading.RLock()
_active = {}
_unsafe = set()
_FIELDS = ("id", "kind", "path", "source", "media_library_id", "storage_identity",
           "effective_enabled", "smb_host", "smb_connect_host", "smb_share", "smb_subpath",
           "smb_domain", "smb_username", "smb_password", "smb_options", "nfs_export",
           "nfs_options", "nfs_password", "media_mount_point")


@contextmanager
def preview_scan_guard():
    """Prevent scans starting across a guarded cleanup decision and deletion."""
    with _guard:
        yield


def _identity(lib):
    if not lib:
        return None
    raw = json.dumps([lib.get(k) for k in _FIELDS], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def invalidate_preview_scan_evidence(c, *, library_id=None, media_library_id=None):
    """Invalidate in the same transaction as a library configuration change."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='preview_scan_health'").fetchone():
        return
    if library_id is not None:
        c.execute("DELETE FROM preview_scan_health WHERE library_id=?", (int(library_id),))
    elif media_library_id is not None:
        c.execute("DELETE FROM preview_scan_health WHERE library_id IN "
                  "(SELECT id FROM libraries WHERE media_library_id=?)", (int(media_library_id),))


def begin_preview_scan(library_id):
    lid = int(library_id)
    token = uuid.uuid4().hex
    with _guard:
        running = _active.setdefault(lid, {})
        if running:
            for other in running.values():
                other["overlap"] = True
        entry = {"overlap": bool(running), "identity": None}
        running[token] = entry
        try:
            entry["identity"] = _identity(get_library(lid))
            with _lock, _conn() as c:
                c.execute("CREATE TABLE IF NOT EXISTS preview_scan_health "
                          "(library_id INTEGER PRIMARY KEY, token TEXT NOT NULL, "
                          "identity TEXT, healthy INTEGER NOT NULL)")
                c.execute("INSERT OR REPLACE INTO preview_scan_health VALUES (?, ?, ?, 0)",
                          (lid, token, entry["identity"]))
            _unsafe.discard(lid)
        except (sqlite3.Error, OSError, ValueError, TypeError):
            _unsafe.add(lid)
    return token


def finish_preview_scan(library_id, token, success):
    lid = int(library_id)
    with _guard:
        running = _active.get(lid, {})
        entry = running.pop(token, None)
        if not running:
            _active.pop(lid, None)
        if not entry or entry["overlap"] or running or not success:
            return
        try:
            identity = _identity(get_library(lid))
            if not identity or identity != entry["identity"] or lid in _unsafe:
                return
            with _lock, _conn() as c:
                c.execute("UPDATE preview_scan_health SET healthy=1 "
                          "WHERE library_id=? AND token=? AND identity=?",
                          (lid, token, identity))
        except (sqlite3.Error, OSError, ValueError, TypeError):
            _unsafe.add(lid)


def preview_scan_health(library_id):
    """Return healthy only while the last scan evidence remains applicable."""
    lid = int(library_id)
    with _guard:
        if lid in _unsafe or _active.get(lid):
            return "unknown"
        try:
            lib = get_library(lid)
            if not lib or not lib.get("effective_enabled") or fs_change_summary(lid)["pending"]:
                return "unknown"
            with _lock, _conn() as c:
                row = c.execute("SELECT identity, healthy FROM preview_scan_health WHERE library_id=?",
                                (lid,)).fetchone()
            return "healthy" if row and row["healthy"] and row["identity"] == _identity(lib) else "unknown"
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return "unknown"
