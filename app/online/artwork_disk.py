"""Private, bounded original artwork bytes; no URLs, decoders or I/O workers."""

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

try:
    import fcntl
except ImportError:  # Unsupported hosts keep the normal upstream image path.
    fcntl = None

from .. import data_usage
from ..config import settings


MAX_CACHE_BYTES = 128 * 1024**2
MAX_CACHE_RECORDS = 2048
MAX_CACHE_SECONDS = 86400
MAX_IMAGE_BYTES = 512 * 1024
SWEEP_SECONDS = 60
_MAGIC = b"JZART1\n"
_HEADER_BYTES = 1024
_SCAN_LIMIT = MAX_CACHE_RECORDS * 2 + 64
_FILE = re.compile(r"[0-9a-f]{64}\.art\Z")
_TEMP = re.compile(r"\.[0-9a-f]{32}\.tmp\Z")
_SOURCE = re.compile(r"src_[0-9a-f]{32}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True)
class DiskHit:
    result: tuple[bytes, str]
    remaining: float


@dataclass
class _Entry:
    expires: float
    size: int
    used: float


def _name(key):
    if (not isinstance(key, tuple) or len(key) != 3
            or not isinstance(key[0], str) or not _SOURCE.fullmatch(key[0])
            or not isinstance(key[1], str) or not _HEX.fullmatch(key[1])
            or type(key[2]) is not bytes or len(key[2]) != 32):
        return None
    return hashlib.sha256(b"online-artwork-v1\0" + key[0].encode()
                          + key[1].encode() + key[2]).hexdigest() + ".art"


def _mime(body):
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(body) >= 33 and body[:8] == b"\x89PNG\r\n\x1a\n" and body[12:16] == b"IHDR":
        return "image/png"
    if (len(body) >= 20 and body[:4] == b"RIFF" and body[8:12] == b"WEBP"
            and body[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
            and int.from_bytes(body[4:8], "little") + 8 == len(body)):
        return "image/webp"
    return None


def _close(fd):
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _open(path, flags, *, dir_fd=None):
    try:
        return os.open(path, flags | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0), 0o600, dir_fd=dir_fd)
    except OSError:
        return None


def _info(fd):
    try:
        return os.fstat(fd)
    except OSError:
        return None


def _read(fd, length, check):
    body = bytearray()
    while len(body) < length:
        check()
        try:
            chunk = os.read(fd, min(32 * 1024, length - len(body)))
        except OSError:
            return None
        if not chunk:
            return None
        body.extend(chunk)
    return bytes(body)


def _names(directory, check):
    try:
        iterator = os.scandir(directory)
    except OSError:
        return None
    try:
        names = []
        while True:
            check()
            try:
                item = next(iterator)
            except StopIteration:
                return names
            except OSError:
                return None
            if len(names) >= _SCAN_LIMIT:
                return None
            names.append(item.name)
    finally:
        try:
            iterator.close()
        except OSError:
            pass


class ArtworkDiskCache:
    def __init__(self, root=None, *, capacity=MAX_CACHE_RECORDS,
                 byte_budget=MAX_CACHE_BYTES, ttl=MAX_CACHE_SECONDS, clock=time.time):
        if (type(capacity) is not int or not 1 <= capacity <= MAX_CACHE_RECORDS
                or type(byte_budget) is not int or not 1 <= byte_budget <= MAX_CACHE_BYTES
                or type(ttl) not in {int, float} or not math.isfinite(ttl)
                or not 0 < ttl <= MAX_CACHE_SECONDS):
            raise ValueError("invalid artwork disk limits")
        self._root = root
        self._capacity, self._budget, self._ttl = capacity, byte_budget, ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._identity = None
        self._directory_stamp = None
        self._next_sweep = 0
        self._entries = OrderedDict()
        self._last_stats = (0, 0)

    @contextmanager
    def _locked(self, check, *, create=False):
        maintenance = check is None
        check = check or (lambda: None)
        check()
        if maintenance:
            if not self._lock.acquire(blocking=False):
                yield None
                return
        else:
            while not self._lock.acquire(timeout=0.05):
                check()
        directory = lock = None
        try:
            check()
            if fcntl is None:
                yield None
                return
            path = self._root() if callable(self._root) else self._root
            path = Path(path) if path is not None else Path(settings.data_dir) / "online-artwork"
            if create:
                try:
                    path.mkdir(mode=0o700, parents=True, exist_ok=True)
                except OSError:
                    yield None
                    return
            directory = _open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            info = _info(directory) if directory is not None else None
            if info is None or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
                self._entries.clear()
                self._identity = None
                yield None
                return
            identity = (os.path.abspath(path), info.st_dev, info.st_ino)
            if identity != self._identity:
                self._identity = identity
                self._directory_stamp = None
                self._next_sweep = 0
                self._entries.clear()
            lock = _open(".lock", os.O_RDWR | os.O_CREAT, dir_fd=directory)
            info = _info(lock) if lock is not None else None
            if (info is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_size != 0 or info.st_mode & 0o077):
                yield None
                return
            while True:
                check()
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if maintenance:
                        yield None
                        return
                    time.sleep(0.01)
                except OSError:
                    yield None
                    return
            check()
            yield directory
        finally:
            _close(lock)
            _close(directory)
            self._last_stats = (sum(entry.size for entry in self._entries.values()), len(self._entries))
            self._lock.release()

    def _stamp(self, directory):
        info = _info(directory)
        return (info.st_mtime_ns, info.st_ctime_ns) if info is not None else None

    def _remove(self, directory, name):
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            self._entries.pop(name, None)
            return True
        except OSError:
            return False
        self._entries.pop(name, None)
        data_usage.invalidate()
        return True

    def _header(self, directory, name, now, check, *, body=False):
        descriptor = _open(name, os.O_RDONLY, dir_fd=directory)
        if descriptor is None:
            return None
        try:
            info = _info(descriptor)
            if (info is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_mode & 0o077 or not 0 < info.st_size <= MAX_IMAGE_BYTES + _HEADER_BYTES + 11):
                return None
            prefix = _read(descriptor, len(_MAGIC) + 4, check)
            if prefix is None or prefix[:len(_MAGIC)] != _MAGIC:
                return None
            length = int.from_bytes(prefix[-4:], "big")
            if not 0 < length <= _HEADER_BYTES:
                return None
            encoded = _read(descriptor, length, check)
            if encoded is None:
                return None
            try:
                pairs = json.loads(encoded, object_pairs_hook=list)
                metadata = dict(pairs)
                if (len(pairs) != 7 or len(metadata) != 7
                        or set(metadata) != {"v", "mime", "length", "sha256", "created", "expires", "format"}
                        or metadata["v"] != 1 or type(metadata["v"]) is not int
                        or metadata["format"] != "original"
                        or metadata["mime"] not in {"image/jpeg", "image/png", "image/webp"}
                        or type(metadata["length"]) is not int or not 0 < metadata["length"] <= MAX_IMAGE_BYTES
                        or not isinstance(metadata["sha256"], str) or not _HEX.fullmatch(metadata["sha256"])
                        or any(type(metadata[field]) not in {float, int}
                               or not math.isfinite(metadata[field]) for field in ("created", "expires"))
                        or not 0 <= metadata["created"] <= now
                        or not 0 < metadata["expires"] - metadata["created"] <= MAX_CACHE_SECONDS
                        or info.st_size != len(prefix) + length + metadata["length"]):
                    return None
            except (ValueError, TypeError, UnicodeDecodeError):
                return None
            expiry = min(metadata["expires"], metadata["created"] + self._ttl)
            if expiry <= now:
                return None
            if not body:
                return _Entry(expiry, info.st_size, info.st_mtime)
            value = _read(descriptor, metadata["length"], check)
            if (value is None or hashlib.sha256(value).hexdigest() != metadata["sha256"]
                    or _mime(value) != metadata["mime"]):
                return None
            return _Entry(expiry, info.st_size, info.st_mtime), (value, metadata["mime"])
        finally:
            _close(descriptor)

    def _trim(self, directory, *, reserve=0, check=lambda: None):
        while self._entries and (len(self._entries) > self._capacity - bool(reserve)
                                 or sum(entry.size for entry in self._entries.values()) + reserve > self._budget):
            check()
            if not self._remove(directory, next(iter(self._entries))):
                return False
        return True

    def _sync(self, directory, now, check, *, force=False):
        stamp = self._stamp(directory)
        if not force and stamp == self._directory_stamp and now < self._next_sweep:
            return True
        # A cancelled or failed scan must never make a partial index eligible
        # for reuse on a later writer's capacity check.
        self._directory_stamp = None
        self._next_sweep = 0
        self._entries.clear()
        names = _names(directory, check)
        if names is None:
            return False
        for name in names:
            check()
            if _TEMP.fullmatch(name):
                if not self._remove(directory, name):
                    return False
            elif _FILE.fullmatch(name):
                entry = self._header(directory, name, now, check)
                if entry is None:
                    if not self._remove(directory, name):
                        return False
                else:
                    self._entries[name] = entry
            elif name != ".lock":
                # Foreign data is never deleted or adopted as our cache.
                self._entries.clear()
                return False
        self._entries = OrderedDict(sorted(self._entries.items(), key=lambda pair: pair[1].used))
        if not self._trim(directory, check=check):
            return False
        self._directory_stamp = self._stamp(directory)
        self._next_sweep = now + SWEEP_SECONDS
        return True

    def get(self, key, *, check):
        check()
        name = _name(key)
        if name is None:
            return None
        with self._locked(check) as directory:
            if directory is None:
                check()
                return None
            now = self._clock()
            if not self._sync(directory, now, check):
                return None
            value = self._header(directory, name, now, check, body=True)
            if value is None:
                self._remove(directory, name)
                return None
            entry, result = value
            check()
            remaining = entry.expires - self._clock()
            if remaining <= 0:
                self._remove(directory, name)
                return None
            try:
                os.utime(name, (now, now), dir_fd=directory, follow_symlinks=False)
            except OSError:
                pass
            self._entries[name] = entry
            self._entries.move_to_end(name)
            check()
            remaining = entry.expires - self._clock()
            if remaining <= 0:
                self._remove(directory, name)
                return None
            return DiskHit(result, remaining)

    def put(self, key, result, *, check):
        check()
        name = _name(key)
        if (name is None or not isinstance(result, tuple) or len(result) != 2
                or type(result[0]) is not bytes or not 0 < len(result[0]) <= MAX_IMAGE_BYTES
                or type(result[1]) is not str or result[1] not in {"image/jpeg", "image/png", "image/webp"}
                or _mime(result[0]) != result[1]):
            return False
        now = self._clock()
        if not math.isfinite(now) or now < 0:
            return False
        metadata = json.dumps({"v": 1, "mime": result[1], "length": len(result[0]),
                               "sha256": hashlib.sha256(result[0]).hexdigest(), "created": now,
                               "expires": now + self._ttl, "format": "original"},
                              sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        value = _MAGIC + len(metadata).to_bytes(4, "big") + metadata + result[0]
        if len(value) > self._budget:
            return False
        with self._locked(check, create=True) as directory:
            if directory is None or not self._sync(directory, now, check):
                return False
            if (not self._remove(directory, name)
                    or not self._trim(directory, reserve=len(value), check=check)):
                return False
            temporary = "." + uuid.uuid4().hex + ".tmp"
            descriptor = _open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, dir_fd=directory)
            if descriptor is None:
                return False
            published = False
            try:
                offset = 0
                while offset < len(value):
                    check()
                    try:
                        written = os.write(descriptor, value[offset:offset + 32 * 1024])
                    except OSError:
                        return False
                    if written <= 0:
                        return False
                    offset += written
                check()
                try:
                    os.utime(descriptor, (now, now))
                except OSError:
                    return False
                check()
                try:
                    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                except OSError:
                    return False
                published = True
                self._entries[name] = _Entry(now + self._ttl, len(value), now)
                self._entries.move_to_end(name)
                self._directory_stamp = self._stamp(directory)
                self._next_sweep = now + SWEEP_SECONDS
                data_usage.invalidate()
                check()
                return True
            except BaseException:
                if published:
                    self._remove(directory, name)
                raise
            finally:
                _close(descriptor)
                if not published:
                    self._remove(directory, temporary)

    def remove(self, key):
        name = _name(key)
        if name is None:
            return
        with self._locked(None) as directory:
            if directory is not None:
                self._remove(directory, name)

    def clear(self):
        with self._locked(None) as directory:
            if directory is None:
                return
            names = _names(directory, lambda: None)
            if names is None:
                return
            for name in names:
                if _FILE.fullmatch(name) or _TEMP.fullmatch(name):
                    self._remove(directory, name)
            self._entries.clear()
            self._directory_stamp = None

    def sweep(self, force=False):
        with self._locked(None) as directory:
            if directory is not None:
                self._sync(directory, self._clock(), lambda: None, force=force)

    def stats(self):
        self.sweep()
        size, entries = self._last_stats
        return {"bytes": size, "entries": entries}
