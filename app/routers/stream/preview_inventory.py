"""Rebuildable manifest inventory; immutable sprites are checked lazily by library.

Keep the database outside previews: SQLite journals must not change the discovery
directory's mtime. No media database, storage backend or worker locks are used here.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading


_KEY = re.compile(r"^[a-f0-9]{32}$")


def _signature(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise OSError("symlink in preview inventory")
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns]


def _metadata(directory):
    try:
        _signature(directory)
        _signature(directory / "manifest.json")
        data = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


class PreviewInventory:
    def __init__(self, root: Path):
        self.root = root
        self.path = root.parent / f".{root.name}-inventory.sqlite3"
        self.lock = threading.RLock()
        self.entries = {}
        self.pending = {}

    def _connect(self):
        if self.path.is_symlink():
            raise OSError("symlink preview inventory database")
        connection = sqlite3.connect(self.path, timeout=0.25)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS entries "
                               "(key TEXT PRIMARY KEY, data TEXT, signature TEXT, valid INTEGER)")
            connection.execute("CREATE TABLE IF NOT EXISTS discovery "
                               "(id INTEGER PRIMARY KEY CHECK(id=1), signature TEXT)")
        except BaseException:
            connection.close()
            raise
        return connection

    @staticmethod
    def _put(connection, key, data, signature=None, valid=False):
        connection.execute("INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?)",
                           (key, json.dumps(data, ensure_ascii=False), signature, int(valid)))

    def changed(self, directory, data=None):
        """Record writes/deletions, even before the first inventory request."""
        if directory.parent != self.root or not _KEY.fullmatch(directory.name):
            return
        with self.lock:
            self.entries.pop(directory.name, None)
            self.pending.pop(directory.name, None)
            with closing(self._connect()) as connection, connection:
                if data is None and not directory.exists():
                    connection.execute("DELETE FROM entries WHERE key=?", (directory.name,))
                else:
                    self._put(connection, directory.name, data)

    def flush(self):
        """Persist validation results together, avoiding one fsync per video."""
        with self.lock:
            if not self.pending:
                return
            with closing(self._connect()) as connection, connection:
                for key, (data, signature, valid) in self.pending.items():
                    self._put(connection, key, data, signature, valid)
            self.pending.clear()

    def snapshot(self):
        """Discover directories once, then only when the root itself changes."""
        self.flush()
        try:
            before = json.dumps(_signature(self.root))
        except FileNotFoundError:
            return {}
        with self.lock, closing(self._connect()) as connection, connection:
            saved = connection.execute("SELECT signature FROM discovery WHERE id=1").fetchone()
            if not saved or saved[0] != before:
                known = {row[0] for row in connection.execute("SELECT key FROM entries")}
                found = set()
                with os.scandir(self.root) as directories:
                    for entry in directories:
                        if _KEY.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False):
                            found.add(entry.name)
                            if entry.name not in known:
                                self._put(connection, entry.name, _metadata(Path(entry.path)))
                connection.executemany("DELETE FROM entries WHERE key=?",
                                       [(key,) for key in known - found])
                after = json.dumps(_signature(self.root))
                # A concurrent mkdir must be discovered by the next request.
                connection.execute("INSERT OR REPLACE INTO discovery VALUES (1, ?)",
                                   (before if before == after else None,))
            rows = connection.execute("SELECT key, data, signature, valid FROM entries").fetchall()
            if any(not _KEY.fullmatch(key) for key, *_ in rows):
                raise ValueError("invalid preview inventory key")
            self.entries = {key: (json.loads(raw), signature, valid)
                            for key, raw, signature, valid in rows}
            if any(data is not None and not isinstance(data, dict)
                   for data, _signature_text, _valid in self.entries.values()):
                raise ValueError("invalid preview inventory metadata")
        result = {}
        for key, raw, _signature_text, valid in rows:
            data = json.loads(raw)
            if not valid or data is None or data.get("item_kind") is None or data.get("state") != "ready":
                # Empty/partial/old ownerless directories can become ready without
                # changing root.mtime, including a crash before changed() commits.
                data = _metadata(self.root / key)
            if isinstance(data, dict):
                result[key] = data
        return result

    def checked(self, key, reader, *, verify=False):
        """Recheck changed sets and every returned page, including in-place damage."""
        directory = self.root / key
        try:
            _signature(self.root)
            signature = json.dumps([_signature(directory), _signature(directory / "manifest.json")])
        except OSError:
            signature = None
        with self.lock:
            saved = self.entries.get(key)
            if not verify and signature is not None and saved and saved[1] == signature:
                return saved[0] if saved[2] else None
            data = reader(directory) if signature is not None else None
            if saved and saved == (data, signature, 1):
                return data
            # Keep ownership even for partial/invalid entries so later repairs
            # remain discoverable. The complete reader decides usability.
            metadata = data or _metadata(directory)
            try:
                after = json.dumps([_signature(directory), _signature(directory / "manifest.json")])
            except OSError:
                after = None
            if signature != after:
                signature = None
            self.entries[key] = (metadata, signature, int(data is not None))
            self.pending[key] = self.entries[key]
            return data
