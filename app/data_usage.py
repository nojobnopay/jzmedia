"""Application data usage, excluding media mounted below DATA_DIR/mounts.

The warning is advisory. Database, artwork and active playback files are never
deleted to satisfy it; cleanup remains an explicit user action.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time
from urllib.parse import quote

from .config import settings

WARNING_BYTES = 20 * 1024 ** 3
_lock = threading.Lock()
_cached = None
_cached_at = 0.0


def invalidate() -> None:
    global _cached_at
    with _lock:
        _cached_at = 0.0


def _category(name: str) -> str:
    if name in ("posters", "previews", "transcode"):
        return name
    if name.startswith("jzmedia.db"):
        return "database"
    return "other"


def _media_paths(data_root: str) -> set[str]:
    paths = [settings.media_root]
    database = os.path.join(data_root, "jzmedia.db")
    if os.path.isfile(database):
        try:
            with open(database, "rb") as source:
                if source.read(16) != b"SQLite format 3\x00":
                    raise sqlite3.DatabaseError("invalid database header")
            uri = "file:" + quote(database, safe="/") + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as connection:
                paths.extend(row[0] for row in connection.execute(
                    "SELECT path FROM media_libraries WHERE source='local'"))
        except (OSError, sqlite3.Error):
            pass  # Older or temporarily unavailable databases still use MEDIA_ROOT.
    excluded = set()
    for path in paths:
        if not path:
            continue
        absolute = os.path.abspath(path)
        try:
            if absolute != data_root and os.path.commonpath((data_root, absolute)) == data_root:
                excluded.add(absolute)
        except ValueError:
            continue
    return excluded


def _scan() -> dict:
    root = Path(settings.data_dir)
    data_root = os.path.abspath(root)
    excluded_media = _media_paths(data_root)
    parts = {"database": 0, "posters": 0, "previews": 0,
             "transcode": 0, "other": 0}
    errors = 0
    try:
        if not root.is_dir():
            raise FileNotFoundError()
        pending = [(root, None)]
        while pending:
            directory, category = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if directory == root and entry.name == "mounts":
                            continue
                        if os.path.abspath(entry.path) in excluded_media:
                            continue
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            errors += 1
                            continue
                        bucket = category or _category(entry.name)
                        if stat.S_ISDIR(info.st_mode):
                            pending.append((Path(entry.path), bucket))
                        elif stat.S_ISREG(info.st_mode):
                            parts[bucket] += info.st_size
            except OSError:
                errors += 1
    except OSError:
        errors += 1
    total = sum(parts.values())
    return {"ok": errors == 0, "total_bytes": total, "warning_bytes": WARNING_BYTES,
            "over_limit": errors == 0 and total >= WARNING_BYTES,
            "categories": parts,
            "error": "部分应用数据暂时无法统计" if errors else ""}


def usage(*, refresh: bool = False) -> dict:
    global _cached, _cached_at
    with _lock:
        now = time.monotonic()
        if refresh or _cached is None or now - _cached_at >= 60:
            _cached = _scan()
            _cached_at = now
        return dict(_cached, categories=dict(_cached["categories"]))
