"""统一存储抽象（远程媒体库指导 §6）：媒体文件读写不再直连 POSIX。

- 传入路径一律为「库内相对路径」（POSIX 分隔符；空串表示库根）。
- 业务层不得按来源分支（不得写 `if lib["source"] == "smb"`），后端选择集中在
  `factory`；Local 后端已落地，SMB 直读后端接入时上层调用点不变。
- 只读库写入在基类统一拦截（`StorageReadOnly`）；错误带机器可读 `code`，
  供 API 映射与连接诊断使用。
- 远程库离线时读操作抛 `StorageOffline`，`exists()` 不得把离线当删除
  （指导 §19：Offline ≠ Deleted），只把 `StorageNotFound` 映射为 False。
"""
from __future__ import annotations

import abc
import hashlib
import os
import stat as _stat
import time
from contextlib import contextmanager
from typing import BinaryIO, Iterator, NamedTuple

__all__ = [
    'StorageBackend', 'StorageStat', 'StorageEntry', 'WalkEntry', 'MediaSource',
    'StorageExists', 'StorageError', 'StorageInvalidPath', 'StorageNotFound', 'StorageDenied',
    'StorageReadOnly', 'StorageOffline', 'StorageUnsupported', 'StorageSourceChanged',
]


class StorageError(RuntimeError):
    """存储层错误基类；code 供 API/诊断映射（永不包含凭据）。"""
    code = "STORAGE_ERROR"


class StorageExists(StorageError):
    code = "ALREADY_EXISTS"


class StorageInvalidPath(StorageError):
    code = "INVALID_PATH"


class StorageNotFound(StorageError):
    code = "NOT_FOUND"


class StorageDenied(StorageError):
    code = "PERMISSION_DENIED"


class StorageReadOnly(StorageError):
    code = "READ_ONLY"


class StorageOffline(StorageError):
    code = "OFFLINE"


class StorageUnsupported(StorageError):
    code = "UNSUPPORTED"


class StorageSourceChanged(StorageError):
    code = "SOURCE_CHANGED"


class StorageStat(NamedTuple):
    size: int
    mtime: float
    is_dir: bool


class StorageEntry(NamedTuple):
    name: str
    size: int
    mtime: float
    is_dir: bool


class WalkEntry(NamedTuple):
    """iter_tree 产出：库内相对路径 + 基础元数据（远程后端 list 顺带返回，省一次 stat）。"""
    rel: str
    name: str
    is_dir: bool
    size: int
    mtime: float


class StorageBackend(abc.ABC):
    """库级存储后端：路径 = 库内相对路径，后端负责边界与只读拦截。"""

    driver = "base"
    read_cache_revision = 0
    UNVERIFIED_DIRECTORY = "unverified-directory"

    def __init__(self, library: dict, driver: str = ""):
        self.library = dict(library or {})
        self.library_id = int(self.library.get("id") or 0)
        self.source = str(self.library.get("source") or "local")
        self.root = str(self.library.get("path") or "")
        self.read_only = bool(int(self.library.get("read_only") or 0))
        self.name = str(self.library.get("name") or f"库{self.library_id}")
        if driver:
            self.driver = driver

    # ---------------- 路径 ----------------

    def norm(self, path: str) -> str:
        """语法归一 + 边界检查；返回库内相对路径（'' 表示库根）。
        真实路径校验（符号链接逃逸）由各后端 `_abs` 完成。"""
        raw = str(path if path is not None else "").strip().replace("\\", "/")
        if "\x00" in raw:
            raise StorageInvalidPath(f"illegal path: {path!r}")
        if raw in ("", ".", "/"):
            return ""
        if raw.startswith("/") or os.path.isabs(raw):
            raise StorageInvalidPath(f"path must be library-relative: {path!r}")
        parts = [p for p in raw.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise StorageInvalidPath(f"path escapes library root: {path!r}")
        return "/".join(parts)

    @abc.abstractmethod
    def abs_path(self, path: str = "") -> str | None:
        """后端有 POSIX 路径时返回绝对路径（本地/挂载后端），否则 None。"""

    def _require_writable(self) -> None:
        if self.read_only:
            raise StorageReadOnly(f"库「{self.name}」为只读，写操作被拒")

    # ---------------- 读 ----------------

    @abc.abstractmethod
    def list(self, path: str = "") -> list[dict]:
        """目录项：{name, size, mtime, is_dir}；目录不可达抛 StorageError。"""

    @abc.abstractmethod
    def stat(self, path: str) -> StorageStat:
        """stat；不存在抛 StorageNotFound，权限/离线等抛对应子类。"""

    def exists(self, path: str = "") -> bool:
        """存在性：仅 NotFound 视为 False；Offline/权限错误照抛（防误删）。"""
        try:
            self.stat(path)
            return True
        except StorageNotFound:
            return False

    def is_dir(self, path: str = "") -> bool:
        return self.stat(path).is_dir

    def iter_tree(self, path: str = "", skip_dirs=(), max_entries: int = 0):
        """广度优先遍历库内子树，产出 WalkEntry（跳过隐藏目录/文件与 skip_dirs）。

        - 泛型实现基于 `list()`：SMB 每目录一次网络往返（避免逐文件 stat）；
        - Local 覆写为 os.scandir 递归（大库更快）；
        - 目录不可达（离线/权限）抛对应 StorageError，由调用方决定跳过与状态。
        """
        start = self.norm(path)
        queue = [start]
        n = 0
        skipped = set(skip_dirs or ())
        while queue:
            cur = queue.pop(0)
            for e in self.list(cur):
                name = e["name"]
                if name.startswith("."):
                    continue
                rel = f"{cur}/{name}" if cur else name
                if e["is_dir"]:
                    if name in skipped:
                        continue
                    queue.append(rel)
                yield WalkEntry(rel=rel, name=name, is_dir=bool(e["is_dir"]),
                                size=int(e.get("size") or 0),
                                mtime=float(e.get("mtime") or 0))
                n += 1
                if max_entries and n >= max_entries:
                    return

    @abc.abstractmethod
    def read(self, path: str, offset: int = 0, length: int = -1) -> bytes:
        """随机读：length < 0 表示读到文件尾（Range 代理/SMB 复用同一接口）。"""

    @abc.abstractmethod
    @contextmanager
    def open_read(self, path: str) -> Iterator[BinaryIO]:
        """返回上下文管理器式文件对象（扫描/ffprobe 小读用）。"""
        raise NotImplementedError

    @abc.abstractmethod
    def get_read_url(self, path: str) -> str:
        """可供 ffprobe/ffmpeg 读取的地址：本地=绝对路径，远程=内网 HTTP URL。"""

    def read_revision(self, path: str, info: StorageStat) -> str:
        """Opaque identity shared by input URLs and revision-bound readers."""
        from ..store.media_info import media_source_revision
        value = media_source_revision(self.library_id, self.norm(path), info.size, info.mtime)
        return hashlib.sha256(value.encode()).hexdigest()

    def get_media_read_url(self, path: str, info: StorageStat) -> str:
        return self.get_read_url(path)

    @contextmanager
    def open_media_read(self, path: str, revision: str | None = None) -> Iterator[BinaryIO]:
        """Open before publishing HTTP headers and reject an intervening replacement."""
        fresh_stat = getattr(self, 'stat_uncached', self.stat)
        before = fresh_stat(path)
        expected = revision or self.read_revision(path, before)
        if before.is_dir or self.read_revision(path, before) != expected:
            raise StorageSourceChanged('source changed before reading')
        with self.open_read(path) as fh:
            after = fresh_stat(path)
            if after.is_dir or self.read_revision(path, after) != expected:
                raise StorageSourceChanged('source changed while opening')
            yield fh

    # ---------------- 写 ----------------

    @abc.abstractmethod
    def mkdir(self, path: str = "", parents: bool = True) -> None: ...

    @abc.abstractmethod
    def write(self, path: str, data: bytes) -> None:
        """原子写（临时文件 + rename），父目录自动创建。"""

    @contextmanager
    def open_write(self, path: str, *, overwrite: bool = True) -> Iterator[BinaryIO]:
        """流式原子写（大文件上传/复制）：临时文件 + 关闭时 rename，父目录自动建。

        默认不支持，由后端覆写；失败/中断必须清理临时文件。
        """
        self._require_writable()
        raise StorageUnsupported(f"{self.driver} 后端不支持流式写: {path}")
        yield  # pragma: no cover（生成器语法占位）

    @abc.abstractmethod
    def rename(self, src: str, dst: str, *, overwrite: bool = True) -> None:
        """同库 rename；跨设备时后端自行降级（复制+删除）。"""

    def _entry_lstat(self, path: str):
        raise StorageUnsupported("此存储无法核对待删除文件的身份")

    def delete_entry_identity(self, path: str):
        """Return the current entry type, size and revision, without following its final link."""
        info = self._entry_lstat(path)
        return self._delete_identity(info) if info is not None else None

    def organize_identity(self, path: str, obj: str, *, recorded_at=None) -> str:
        """Identify a file or directory across renames. Directory contents can change independently."""
        info = self._entry_lstat(path)
        return self._organize_stamp(info, obj, self.abs_path(path) if info is not None else None,
                                    recorded_at=recorded_at, remote_sample=lambda: self._organize_file_sample(path, info))

    def _organize_file_sample(self, path, info):
        return None

    @staticmethod
    def _organize_stamp(info, obj, absolute, *, recorded_at=None, remote_sample=None):
        mode_check = _stat.S_ISDIR if obj == "dir" else _stat.S_ISREG
        if info is None or not mode_check(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            return ""
        birth = getattr(info, "st_birthtime_ns", None)
        if hasattr(info, "st_chgtime_ns"):
            birth = info.st_ctime_ns  # smbclient distinguishes CreationTime from ChangeTime.
        elif birth is None and absolute is not None:
            from ..fsutil import file_birthtime_ns
            birth = file_birthtime_ns(absolute, info)
        if birth is not None and birth <= 0:
            birth = None
        # A replacement created after a legacy audit cannot be the object from that move.
        if birth and recorded_at is not None and birth // 1_000_000_000 > recorded_at:
            return ""
        if not info.st_ino and not birth:
            return ""
        if obj == "dir" and not birth:
            # Allow normal organization. Inode reuse makes automatic reversal unsafe.
            return StorageBackend.UNVERIFIED_DIRECTORY
        stamp = (obj, info.st_dev, info.st_ino, birth)
        if obj == "file":
            stamp += (info.st_size, info.st_mtime_ns)
            if birth is None:
                from ..fsutil import file_sample
                if absolute is not None:
                    sample = file_sample(absolute, info)
                else:
                    sample = remote_sample() if remote_sample else None
                if sample is None:
                    return ""
                stamp += (sample,)
        return hashlib.sha256(str(stamp).encode()).hexdigest()

    def organize_move(self, src: str, dst: str, obj: str, *, expected: str | None = None, copied=None) -> str:
        identity = self.organize_identity(src, obj)
        if expected is not None and identity == self.UNVERIFIED_DIRECTORY:
            raise StorageSourceChanged("存储未提供目录创建时间，请人工核对目录，分集文件可单独撤销")
        if not identity or expected is not None and identity != expected:
            raise StorageSourceChanged("源文件或目录已变化，请重新预览")
        copies, destination_identity = self._organize_rename(src, dst, restoring=expected is not None)
        if copied is not None:
            copied.update(copies)
        return destination_identity or identity

    def _organize_rename(self, src, dst, *, restoring):
        self.rename(src, dst, overwrite=False)
        return {}, None

    def file_delete_identity(self, path: str) -> tuple[int, str] | None:
        identity = self.delete_entry_identity(path)
        return identity[1:] if identity and identity[0] == "file" else None

    def delete_empty_dir(self, path: str) -> None:
        """Remove only an empty directory, never a file that replaced it."""
        raise StorageUnsupported("此存储不支持清理空目录")

    @staticmethod
    def _delete_identity(info):
        if _stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            kind = "link"
        elif _stat.S_ISDIR(info.st_mode):
            kind = "dir"
        elif _stat.S_ISREG(info.st_mode):
            kind = "file"
        else:
            return None
        stamp = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                 getattr(info, "st_chgtime_ns", None))
        return kind, info.st_size, hashlib.sha256(str(stamp).encode()).hexdigest()

    @abc.abstractmethod
    def delete(self, path: str, recursive: bool = False, *, file_only: bool = False) -> None:
        """删除文件/空目录；目录需 recursive=True，缺失抛 StorageNotFound。"""

    # ---------------- 健康 ----------------

    def health_check(self) -> dict:
        """读探测 + 延迟；status 为机器可读短码（online/offline/not_found/...）。"""
        t0 = time.perf_counter()
        ok, status, err = True, "online", ""
        try:
            self.test_read()
        except StorageError as e:
            ok, status, err = False, str(getattr(e, "code", "ERROR")).lower(), str(e)
        return {"ok": ok, "status": status, "error": err,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "driver": self.driver, "read_only": self.read_only}

    def test_read(self) -> dict:
        """诊断用：列举 + 抽样小读（所有后端共用；失败抛 StorageError 子类）。"""
        entries = self.list("")
        sample = next((e for e in entries if not e["is_dir"] and e["size"] > 0), None)
        if sample is not None:
            data = self.read(sample["name"], 0, min(65536, sample["size"]))
            if not data:
                raise StorageError(f"读取样本为空: {sample['name']}")
        return {"ok": True, "entries": len(entries)}

    def test_write(self) -> dict:
        """诊断用：建临时文件 → 写入 → rename → 删除；只读库返回 skipped。"""
        if self.read_only:
            return {"ok": True, "skipped": True, "reason": "read_only"}
        name = f".jzmedia-write-test-{os.getpid()}"
        tmp, final = name + ".tmp", name + ".ok"
        try:
            self.write(tmp, b"jzmedia")
            self.rename(tmp, final)
            self.delete(final)
        except StorageError as e:
            for leftover in (tmp, final):
                try:
                    self.delete(leftover)
                except StorageError:
                    pass
            return {"ok": False, "error": str(e), "code": e.code}
        return {"ok": True, "skipped": False}


class MediaSource(NamedTuple):
    """统一播放输入（指导 §10/约束 3）：决策层不关心 Local/SMB/NFS。

    - `input`：ffprobe/ffmpeg 的输入（本地=绝对路径；远程=内网 Range 代理 URL）；
    - `local_path`：后端有 POSIX 路径时可用（FileResponse/字幕旁路），远程 None；
    - size/mtime：后端 stat 结果（远程无 inode 语义，扫描按 size+mtime）。
    """
    backend: StorageBackend
    rel: str
    input: str
    local_path: str | None
    size: int
    mtime: float


def _is_dir_mode(mode: int) -> bool:
    return _stat.S_ISDIR(mode)
