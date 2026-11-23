"""文件原子写小工具（评审 B5a-6）。

临时文件与目标同目录 + os.replace：目标要么保持旧内容、要么替换为新内容，
绝不出现半成品（崩溃/磁盘满/中断都不会污染缓存与 NFO）。
"""
import ctypes
import errno
import hashlib
import os
import shutil
import stat
import struct
import sys
import tempfile


def file_birthtime_ns(path, info):
    """Read Linux statx birth time where Python's os.stat_result does not expose it."""
    birth = getattr(info, "st_birthtime_ns", None)
    if birth is not None or not sys.platform.startswith("linux"):
        return birth
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "statx"):
        return None
    # linux/stat.h fixes struct statx at 256 bytes. Validate its mask and inode
    # before combining this second no-follow lookup with the caller's lstat.
    result = ctypes.create_string_buffer(256)
    fields = 0x100 | 0x800  # STATX_INO | STATX_BTIME
    if libc.statx(-100, os.fsencode(path), 0x100, fields, ctypes.byref(result)) != 0:
        return None
    if struct.unpack_from("=I", result, 0)[0] & fields != fields or struct.unpack_from("=Q", result, 32)[0] != info.st_ino:
        return None
    seconds, nanos = struct.unpack_from("=qI", result, 80)
    return seconds * 1_000_000_000 + nanos


def stream_sample(stream, size):
    """Sample at most three blocks from an already opened file."""
    digest = hashlib.sha256()
    block = 4096
    for offset in sorted({0, max(0, (size - block) // 2), max(0, size - block)}):
        stream.seek(offset)
        digest.update(stream.read(block))
    return digest.digest()


def file_sample(path, info):
    """Bound the extra read on local filesystems without a creation timestamp."""
    def revision(s):
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        if revision(os.fstat(fd)) != revision(info):
            return None
        sample = stream_sample(stream, info.st_size)
        return sample if revision(os.fstat(fd)) == revision(info) else None


def rename_no_replace(src: str, dst: str) -> None:
    """Publish on the same filesystem without replacing a competing file or directory."""
    if os.name == "nt":
        os.rename(src, dst)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        # AT_FDCWD = -100, RENAME_NOREPLACE = 1. This also works on mounts without hard links.
        result = libc.renameat2(-100, os.fsencode(src), -100, os.fsencode(dst), 1)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        result = libc.renamex_np(os.fsencode(src), os.fsencode(dst), 4)  # RENAME_EXCL
    else:
        result = None
    if result == 0:
        return
    if result is not None:
        err = ctypes.get_errno()
        if err not in (errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL):
            raise OSError(err, os.strerror(err), dst)
    os.link(src, dst)
    os.unlink(src)


def _move_inventory(root):
    """Inventory exactly the entries a cross-device move may copy and remove."""
    result, pending = {}, [""]
    device = os.lstat(root).st_dev
    while pending:
        rel = pending.pop()
        path = os.path.join(root, rel) if rel else root
        info = os.lstat(path)
        mode = stat.S_IFMT(info.st_mode)
        if info.st_dev != device or mode == stat.S_IFDIR and os.path.ismount(path):
            raise OSError(errno.EXDEV, "整理目录包含其他挂载点", path)
        if mode not in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
            raise OSError(errno.ENOTSUP, "整理目录包含特殊文件", path)
        result[rel] = (mode, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if mode == stat.S_IFDIR:
            pending.extend(os.path.join(rel, name) for name in os.listdir(path))
    return result


def move_no_replace(src: str, dst: str, capture_copy=None) -> None:
    """Use an exclusive rename, or publish a complete cross-device copy before removing its source."""
    try:
        rename_no_replace(src, dst)
        return
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
    if dst.startswith(src.rstrip(os.sep) + os.sep):
        raise OSError(errno.EINVAL, "目标位于原目录内", dst)
    entries = _move_inventory(src)
    with tempfile.TemporaryDirectory(prefix=".jzmedia-move-", dir=os.path.dirname(dst)) as staging:
        copy = os.path.join(staging, "item")
        if entries[""][0] == stat.S_IFDIR:
            shutil.copytree(src, copy, symlinks=True)
        else:
            shutil.copy2(src, copy, follow_symlinks=False)
        copied = _move_inventory(copy)
        if (entries != _move_inventory(src) or entries.keys() != copied.keys()
                or any(s[0] != copied[p][0] or s[0] != stat.S_IFDIR and s[3] != copied[p][3]
                       for p, s in entries.items())):
            raise OSError(errno.ESTALE, "复制期间源文件或目录已变化", src)
        if capture_copy is not None:
            capture_copy(src, copy, entries)
        rename_no_replace(copy, dst)
    if entries != _move_inventory(src):
        raise OSError(errno.ESTALE, "已保留目标副本，原目录或文件已变化", src)
    # A late new file must survive. Never recursively delete the source tree.
    unlinked = set()
    for rel in sorted(entries, key=lambda name: (name.count(os.sep), len(name)), reverse=True):
        path = os.path.join(src, rel) if rel else src
        info = os.lstat(path)
        mode = stat.S_IFMT(info.st_mode)
        expected = entries[rel]
        stamp = (mode, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        # Removing another hard link changes ctime. Removing children changes directory timestamps.
        fields = 3 if mode == stat.S_IFDIR else 5 if (info.st_dev, info.st_ino) in unlinked else 6
        if stamp[:fields] != expected[:fields]:
            raise OSError(errno.ESTALE, "已保留目标副本；原路径发生变化", path)
        if mode == stat.S_IFDIR:
            os.rmdir(path)
        else:
            os.unlink(path)
            if info.st_nlink > 1:
                unlinked.add((info.st_dev, info.st_ino))


def atomic_write_bytes(dest: str, data: bytes) -> None:
    tmp = dest + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(dest: str, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(dest, text.encode(encoding))
