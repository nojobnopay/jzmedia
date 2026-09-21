"""LocalStorageBackend：POSIX 路径后端（本地库 + 宿主/应用内挂载点）。

- 边界语义与 `library_paths.check_inside` 一致：normpath + realpath 双重校验，
  库内符号链接指向外部时拒绝（评审 B6/R09-B1）。
- 写操作沿用 `fsutil` 原子写；rename 跨设备（EXDEV）退化为 shutil.move，
  与 `routers.files.paths._rename_or_move` 行为一致。
"""
from __future__ import annotations

import errno
import os
import shutil
import time
from contextlib import contextmanager
from typing import Iterator, BinaryIO

from .. import fsutil
from ..log import get_logger
from .base import (
    StorageBackend, StorageDenied, StorageError, StorageInvalidPath,
    StorageNotFound, StorageStat, StorageUnsupported, WalkEntry, _is_dir_mode,
)

logger = get_logger("storage.local")

__all__ = ['LocalStorageBackend']


def _map_os_error(e: OSError, path: str) -> StorageError:
    if isinstance(e, (FileNotFoundError, NotADirectoryError)):
        return StorageNotFound(f"不存在: {path}")
    if isinstance(e, PermissionError):
        return StorageDenied(f"权限不足: {path}")
    return StorageError(f"IO 错误 {path}: {e}")


class LocalStorageBackend(StorageBackend):
    """POSIX 后端；`driver` 可为 local（本地库）或 mount（远程库挂载点）。"""

    def __init__(self, library: dict, driver: str = "local"):
        super().__init__(library, driver=driver)
        if not self.root:
            raise StorageUnsupported(f"库「{self.name}」没有可用存储路径")

    def _abs(self, path: str = "") -> str:
        """严格模式：realpath 校验，含符号链接逃逸（内容访问一律走这里）。"""
        rel = self.norm(path)
        try:
            root = os.path.realpath(self.root)
            abs_p = os.path.realpath(os.path.join(root, rel)) if rel else root
        except (OSError, ValueError) as e:
            raise StorageInvalidPath(f"非法路径 {path!r}: {e}") from e
        if abs_p != root and not abs_p.startswith(root + os.sep):
            raise StorageInvalidPath(f"path escapes library root: {path!r}")
        return abs_p

    def _abs_lax(self, path: str = "") -> str:
        """宽松模式：只做语法归一（stat/exists 等元数据，不跟随逃逸判定）。"""
        rel = self.norm(path)
        return os.path.join(self.root, rel) if rel else self.root

    def abs_path(self, path: str = "") -> str | None:
        return self._abs(path)

    # ---------------- 读 ----------------

    def list(self, path: str = "") -> list[dict]:
        p = self._abs(path)
        try:
            out: list[dict] = []
            with os.scandir(p) as it:
                for e in it:
                    try:
                        st = e.stat()
                        out.append({"name": e.name, "is_dir": e.is_dir(),
                                    "size": int(st.st_size),
                                    "mtime": float(st.st_mtime)})
                    except OSError:
                        out.append({"name": e.name, "is_dir": e.is_dir(),
                                    "size": 0, "mtime": 0.0})
            return out
        except OSError as e:
            raise _map_os_error(e, path) from e

    def stat(self, path: str = "") -> StorageStat:
        # 元数据用宽松路径（与 os.stat 一致：跟随符号链接，不因指向库外而报错）；
        # 真正读取/写入内容时 `_abs` 仍会拒绝逃逸。
        try:
            st = os.stat(self._abs_lax(path))
        except OSError as e:
            raise _map_os_error(e, path) from e
        return StorageStat(size=int(st.st_size), mtime=float(st.st_mtime),
                           is_dir=_is_dir_mode(st.st_mode))

    def read(self, path: str, offset: int = 0, length: int = -1) -> bytes:
        try:
            with open(self._abs(path), "rb") as f:
                if offset:
                    f.seek(offset)
                return f.read(length)
        except OSError as e:
            raise _map_os_error(e, path) from e

    @contextmanager
    def open_read(self, path: str) -> Iterator[BinaryIO]:
        try:
            fh = open(self._abs(path), "rb")
        except OSError as e:
            raise _map_os_error(e, path) from e
        try:
            yield fh
        finally:
            fh.close()

    def get_read_url(self, path: str) -> str:
        return self._abs(path)

    def iter_tree(self, path: str = "", skip_dirs=(), max_entries: int = 0):
        """os.scandir 递归（本地大库比逐层 list 快），跳过隐藏目录/文件与 skip_dirs。"""
        start = self._abs(path)
        skipped = set(skip_dirs or ())
        n = 0
        stack = [("", start)]
        while stack:
            rel_dir, abs_dir = stack.pop()
            try:
                with os.scandir(abs_dir) as it:
                    entries = list(it)
            except OSError as e:
                raise _map_os_error(e, rel_dir or "/") from e
            for e in sorted(entries, key=lambda x: x.name):
                name = e.name
                if name.startswith("."):
                    continue
                rel = f"{rel_dir}/{name}" if rel_dir else name
                try:
                    is_dir = e.is_dir()
                    st = e.stat()
                    size, mtime = int(st.st_size), float(st.st_mtime)
                except OSError:
                    is_dir, size, mtime = False, 0, 0.0
                if is_dir and name in skipped:
                    continue
                yield WalkEntry(rel=rel, name=name, is_dir=is_dir,
                                size=size, mtime=mtime)
                n += 1
                if max_entries and n >= max_entries:
                    return
                if is_dir:
                    stack.append((rel, os.path.join(abs_dir, name)))

    # ---------------- 写 ----------------

    def mkdir(self, path: str = "", parents: bool = True) -> None:
        self._require_writable()
        p = self._abs(path)
        try:
            (os.makedirs if parents else os.mkdir)(p, exist_ok=True)
        except OSError as e:
            raise _map_os_error(e, path) from e

    def write(self, path: str, data: bytes) -> None:
        self._require_writable()
        p = self._abs(path)
        try:
            parent = os.path.dirname(p)
            if parent:
                os.makedirs(parent, exist_ok=True)
            fsutil.atomic_write_bytes(p, data)
        except OSError as e:
            raise _map_os_error(e, path) from e

    @contextmanager
    def open_write(self, path: str) -> Iterator[BinaryIO]:
        """流式原子写：`.part-<pid>-<ns>` 临时文件，关闭成功后 os.replace 落位；
        异常/中断清理临时文件，绝不留下半成品。"""
        self._require_writable()
        dest = self._abs(path)
        tmp = f"{dest}.part-{os.getpid()}-{time.monotonic_ns()}"
        try:
            parent = os.path.dirname(dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            fh = open(tmp, "wb")
        except OSError as e:
            raise _map_os_error(e, path) from e
        ok = False
        try:
            yield fh
            fh.close()
            os.replace(tmp, dest)
            ok = True
        except OSError as e:
            raise _map_os_error(e, path) from e
        finally:
            if not ok:
                try:
                    fh.close()
                except Exception:
                    pass
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def rename(self, src: str, dst: str) -> None:
        self._require_writable()
        s, d = self._abs(src), self._abs(dst)
        try:
            parent = os.path.dirname(d)
            if parent:
                os.makedirs(parent, exist_ok=True)
            os.rename(s, d)
        except OSError as e:
            if e.errno == errno.EXDEV:
                shutil.move(s, d)
                return
            raise _map_os_error(e, src) from e

    def delete(self, path: str, recursive: bool = False) -> None:
        self._require_writable()
        p = self._abs(path)
        if not os.path.lexists(p):
            raise StorageNotFound(f"不存在: {path}")
        try:
            if os.path.islink(p):
                os.remove(p)
            elif os.path.isdir(p):
                if recursive:
                    shutil.rmtree(p)
                else:
                    os.rmdir(p)     # 仅允许空目录（rm 语义）
            else:
                os.remove(p)
        except OSError as e:
            if isinstance(e, OSError) and e.errno in (errno.ENOTEMPTY, errno.EEXIST):
                raise StorageError(f"目录非空，需 recursive=True: {path}") from e
            raise _map_os_error(e, path) from e

    # ---------------- 健康 ----------------
    # test_read/test_write 由基类用 list/read/write/rename/delete 实现（各后端共用）。
