"""Restricted cache paths and atomic files, never paths supplied by viewers."""

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import threading
import uuid

from ..config import settings


class CacheError(RuntimeError):
    def __init__(self, category, status=409):
        self.category, self.status = category, status
        super().__init__(category)


_ID = re.compile(r"oc_[0-9a-f]{32}\Z")
_NAME = re.compile(r"[A-Za-z0-9_-]{1,100}\.[a-z0-9]{1,8}(?:\.part)?\Z")
_roots = {}
_root_lock = threading.RLock()


def root(*, create=True):
    result = Path(os.path.abspath(os.getenv("ONLINE_CACHE_DIR") or str(Path(settings.data_dir) / "online-cache")))
    if result == Path(result.anchor):
        raise CacheError("online_cache_path_unsafe")
    current = Path(result.anchor)
    try:
        with _root_lock:
            pinned = _roots.get(str(result))
            if pinned is not None:
                info = result.lstat()
                if (info.st_dev, info.st_ino) != pinned:
                    raise CacheError("online_cache_storage_unavailable", 503)
            for component in result.parts[1:]:
                current /= component
                try:
                    info = current.lstat()
                except FileNotFoundError:
                    if not create:
                        raise CacheError("online_cache_storage_unavailable", 503) from None
                    current.mkdir(mode=0o700)
                    info = current.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise CacheError("online_cache_path_unsafe")
            _roots[str(result)] = info.st_dev, info.st_ino
            return result
    except OSError:
        raise CacheError("online_cache_storage_unavailable", 503) from None


def _marker(directory, *, create=False):
    target = directory / ".cache-identity"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except FileNotFoundError:
        if not create:
            raise CacheError("online_cache_storage_unavailable", 503) from None
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(uuid.uuid4().hex.encode())
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass
        except OSError:
            raise CacheError("online_cache_storage_unavailable", 503) from None
        return _marker(directory)
    except OSError:
        raise CacheError("online_cache_storage_unavailable", 503) from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise CacheError("online_cache_storage_unavailable", 503)
            value = stream.read(65)
        if not re.fullmatch(rb"[0-9a-f]{32}", value):
            raise CacheError("online_cache_storage_unavailable", 503)
        return value.decode("ascii")
    except OSError:
        raise CacheError("online_cache_storage_unavailable", 503) from None


def identity():
    with _root_lock:
        return _marker(root(), create=True)


def verify_identity(expected):
    if expected is None:
        return
    directory = root(create=False)
    if expected != _marker(directory):
        raise CacheError("online_cache_storage_unavailable", 503)


def directory(cache_id, *, create=False):
    if not isinstance(cache_id, str) or not _ID.fullmatch(cache_id):
        raise CacheError("online_cache_not_found", 404)
    result = root() / cache_id
    try:
        if create:
            result.mkdir(mode=0o700, exist_ok=True)
        if not stat.S_ISDIR(result.lstat().st_mode):
            raise CacheError("online_cache_path_unsafe")
        return result
    except FileNotFoundError:
        raise CacheError("online_cache_corrupt") from None
    except OSError:
        raise CacheError("online_cache_storage_unavailable", 503) from None


def path(cache_id, name, *, missing=False):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise CacheError("online_cache_path_unsafe")
    result = directory(cache_id) / name
    try:
        if not stat.S_ISREG(result.lstat().st_mode):
            raise CacheError("online_cache_path_unsafe")
    except FileNotFoundError:
        if not missing:
            raise CacheError("online_cache_corrupt") from None
    return result


def open_file(cache_id, name, mode):
    result = path(cache_id, name, missing=mode != "rb")
    flags = os.O_RDONLY if mode == "rb" else os.O_WRONLY | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if mode == "wb":
        flags |= os.O_TRUNC
    elif mode == "ab":
        flags |= os.O_APPEND
    try:
        descriptor = os.open(result, flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise CacheError("online_cache_path_unsafe")
        return os.fdopen(descriptor, mode)
    except OSError:
        raise CacheError("online_cache_storage_unavailable", 503) from None


def atomic_bytes(cache_id, name, body):
    with open_file(cache_id, name + ".part", "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(path(cache_id, name + ".part"), path(cache_id, name, missing=True))


def signature(cache_id, name):
    digest = hashlib.sha256()
    with open_file(cache_id, name, "rb") as stream:
        before = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        info = os.fstat(stream.fileno())
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
        raise CacheError("online_cache_corrupt")
    return {"size": info.st_size, "sha256": digest.hexdigest(), "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns, "dev": info.st_dev, "ino": info.st_ino}


def remove(cache_id):
    target = root() / cache_id
    if not isinstance(cache_id, str) or not _ID.fullmatch(cache_id):
        raise CacheError("online_cache_not_found", 404)
    if not target.exists() and not target.is_symlink():
        return
    directory(cache_id)
    shutil.rmtree(target)
