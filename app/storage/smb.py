"""用户态 SMB2/3 直读后端（指导 §8/§11/§12；spike 见 docs/plans/SMB_DIRECT_SPIKE.md）。

- 不依赖容器内 mount.cifs / CAP_SYS_ADMIN：进程内 `smbclient`(smbprotocol) + 内网
  HTTP Range 代理（`storage.httpproxy`）供 ffprobe/ffmpeg 读取。
- 凭据只从库行 Fernet 解密到内存；绝不进日志、命令行、API 响应（约束 7）。
- 错误统一映射：认证→StorageDenied、离线/超时→StorageOffline、不存在→
  StorageNotFound，供播放错误分层与连接诊断（§14/§35）。
- 进程内按库缓存后端实例；`invalidate()` 在库配置变更后调用。直连失败有 30s
  冷却，防止断网时每请求重连（§20 退避的轻量版，完整状态机在 Phase 6）。
"""
from __future__ import annotations

import datetime
import os
import socket
import stat as _st
import threading
import time
from contextlib import contextmanager
from typing import Iterator, BinaryIO

from .. import secrets
from ..log import get_logger
from . import httpproxy
from .base import (
    StorageBackend, StorageDenied, StorageError, StorageNotFound,
    StorageOffline, StorageStat, StorageUnsupported,
)

try:
    import smbclient
    import smbprotocol.exceptions as smb_exc
    _IMPORT_ERROR = ""
except ImportError as e:   # 未安装 smbprotocol：工厂回退挂载兼容模式
    smbclient = None
    smb_exc = None
    _IMPORT_ERROR = str(e)

logger = get_logger("storage.smb")

__all__ = ['SmbStorageBackend', 'backend_if_available', 'invalidate', 'map_smb_error']

_ATTR_DIR = 0x10                       # FILE_ATTRIBUTE_DIRECTORY
_NT_NOT_FOUND = {0xC0000034, 0xC000003A, 0xC00000CC}   # 对象/路径/共享不存在
_NT_DENIED = {0xC0000022}
_NT_NOT_EMPTY = {0xC0000101}
_CONNECT_TIMEOUT = int(os.getenv("SMB_CONNECT_TIMEOUT", "15") or 15)
_FAIL_TTL = 30.0

_CACHE_LOCK = threading.Lock()
_CACHE: dict[int, tuple[tuple, "SmbStorageBackend"]] = {}
_FAILED_AT: dict[int, float] = {}


def _short(v, limit: int = 200) -> str:
    return " ".join(str(v).split())[:limit]


def map_smb_error(e: BaseException, what: str) -> StorageError:
    """SMB/网络异常 → 存储层异常（消息脱敏、单行、截断）。"""
    if isinstance(e, StorageError):
        return e
    if smbclient is None:
        return StorageUnsupported(f"缺少 smbprotocol 依赖: {_IMPORT_ERROR}")
    if isinstance(e, smb_exc.SMBAuthenticationError):
        return StorageDenied(f"SMB 认证失败: {what}")
    if isinstance(e, smb_exc.SMBOSError):
        status = 0
        try:
            status = int(getattr(e, "ntstatus", 0) or 0)
        except (TypeError, ValueError):
            status = 0
        if status in _NT_NOT_FOUND:
            return StorageNotFound(f"SMB 对象不存在: {_short(e)}")
        if status in _NT_DENIED:
            return StorageDenied(f"SMB 拒绝访问: {_short(e)}")
        if status in _NT_NOT_EMPTY:
            return StorageError(f"目录非空，需 recursive=True: {_short(e)}")
        return StorageError(f"SMB 操作失败: {_short(e)}")
    if isinstance(e, socket.gaierror):
        return StorageOffline(f"主机名无法解析: {e}")
    if isinstance(e, (socket.timeout, TimeoutError)):
        return StorageOffline(f"SMB 连接超时: {what}")
    if isinstance(e, (ConnectionError, smb_exc.SMBConnectionClosed)):
        return StorageOffline(f"SMB 连接中断: {what}")
    if isinstance(e, smb_exc.SMBResponseException):
        return StorageError(f"SMB 响应错误: {_short(e)}")
    if isinstance(e, smb_exc.SMBException):
        return StorageOffline(f"SMB 通信失败: {_short(e)}")
    if isinstance(e, OSError):
        if isinstance(e, FileNotFoundError) or getattr(e, "errno", None) in (2, 20):
            return StorageNotFound(f"不存在: {what}")
        if isinstance(e, PermissionError) or getattr(e, "errno", None) == 13:
            return StorageDenied(f"权限不足: {what}")
        return StorageOffline(f"网络/IO 错误: {_short(e)}")
    return StorageError(f"SMB 未知错误 {type(e).__name__}: {_short(e)}")


def _dt_epoch(dt) -> float:
    if isinstance(dt, datetime.datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        try:
            return float(dt.timestamp())
        except (OSError, ValueError):
            return 0.0
    try:
        return float(dt or 0)
    except (TypeError, ValueError):
        return 0.0


def _smb_shutil():
    """smbclient(真实) 不自动导入子模块 shutil；测试替身自带该属性时优先用它。"""
    sh = getattr(smbclient, "shutil", None)
    if sh is not None:
        return sh
    from smbclient import shutil as sh  # noqa: PLC0415
    return sh


class SmbStorageBackend(StorageBackend):
    driver = "smb"

    def __init__(self, library: dict):
        super().__init__(library, driver="smb")
        if smbclient is None:
            raise StorageUnsupported(f"缺少 smbprotocol 依赖: {_IMPORT_ERROR}")
        self.host = str(library.get("smb_host") or "").strip()
        # Tailscale/内网可达地址：连接与 UNC 用它；展示用 smb_host（指导 §15）
        self.connect_host = str(library.get("smb_connect_host")
                                or library.get("smb_host") or "").strip()
        self.share = str(library.get("smb_share") or "").strip().strip("/")
        self.subpath = str(library.get("smb_subpath") or "").strip().strip("/")
        self.username = str(library.get("smb_username") or "").strip()
        self.domain = str(library.get("smb_domain") or "").strip()
        if not self.connect_host or not self.share:
            raise StorageUnsupported("SMB 库缺少主机/共享名")
        try:
            self.password = secrets.decrypt_str(library.get("smb_password") or "")
        except secrets.SecretError as e:
            raise StorageDenied(f"凭据不可用（需重新输入密码）: {e}") from e
        self._register()

    # ---------------- 连接 ----------------

    def _register(self) -> None:
        user = self.username
        if self.domain and user and "\\" not in user and "@" not in user:
            user = f"{self.domain}\\{user}"
        kwargs: dict = {"connection_timeout": _CONNECT_TIMEOUT}
        if user:
            kwargs["username"] = user
        if user or self.password:
            kwargs["password"] = self.password
        try:
            smbclient.register_session(self.connect_host, **kwargs)
        except Exception as e:
            raise map_smb_error(e, f"连接 {self.connect_host}") from e

    def _unc(self, path: str = "") -> str:
        rel = self.norm(path)
        parts = [self.connect_host, self.share, self.subpath, rel]
        return "//" + "/".join(p.strip("/") for p in parts if p)

    def _op(self, what: str, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            raise map_smb_error(e, what) from e

    # ---------------- 读 ----------------

    def abs_path(self, path: str = "") -> str | None:
        return None

    def list(self, path: str = "") -> list[dict]:
        unc = self._unc(path)
        out: list[dict] = []
        try:
            with smbclient.scandir(unc) as it:
                for e in it:
                    info = e.smb_info
                    attrs = int(getattr(info, "file_attributes", 0) or 0)
                    out.append({"name": e.name,
                                "is_dir": bool(attrs & _ATTR_DIR),
                                "size": int(getattr(info, "end_of_file", 0) or 0),
                                "mtime": _dt_epoch(getattr(info, "last_write_time", None))})
        except Exception as e:
            raise map_smb_error(e, f"列目录 {path or '/'}") from e
        return out

    def stat(self, path: str = "") -> StorageStat:
        unc = self._unc(path)
        try:
            st = smbclient.stat(unc)
        except Exception as e:
            raise map_smb_error(e, path or "/") from e
        return StorageStat(size=int(st.st_size), mtime=float(st.st_mtime),
                           is_dir=_st.S_ISDIR(int(st.st_mode)))

    def read(self, path: str, offset: int = 0, length: int = -1) -> bytes:
        try:
            with smbclient.open_file(self._unc(path), mode="rb",
                                     share_access="r") as fh:
                if offset:
                    fh.seek(offset)
                return fh.read(length)
        except Exception as e:
            raise map_smb_error(e, path) from e

    @contextmanager
    def open_read(self, path: str) -> Iterator[BinaryIO]:
        try:
            fh = smbclient.open_file(self._unc(path), mode="rb", share_access="r")
        except Exception as e:
            raise map_smb_error(e, path) from e
        try:
            yield fh
        finally:
            try:
                fh.close()
            except Exception as e:   # 关闭失败不覆盖业务异常
                logger.debug("SMB 句柄关闭失败 %s: %s", path, e)

    def get_read_url(self, path: str) -> str:
        return httpproxy.url_for(self.library_id, self.norm(path))

    # ---------------- 写 ----------------

    def mkdir(self, path: str = "", parents: bool = True) -> None:
        self._require_writable()
        unc = self._unc(path)
        try:
            if parents:
                smbclient.makedirs(unc, exist_ok=True)
            else:
                smbclient.mkdir(unc)
        except Exception as e:
            raise map_smb_error(e, path or "/") from e

    def write(self, path: str, data: bytes) -> None:
        self._require_writable()
        unc = self._unc(path)
        tmp = unc + f".tmp{os.getpid()}"
        try:
            parent = unc.rsplit("/", 1)[0]
            smbclient.makedirs(parent, exist_ok=True)
            with smbclient.open_file(tmp, mode="wb", share_access="r") as fh:
                fh.write(data)
            smbclient.replace(tmp, unc)
        except Exception as e:
            try:
                smbclient.remove(tmp)
            except Exception as cleanup_err:
                logger.debug("SMB 写临时文件清理失败 %s: %s", tmp, cleanup_err)
            raise map_smb_error(e, path) from e

    def rename(self, src: str, dst: str) -> None:
        self._require_writable()
        try:
            smbclient.replace(self._unc(src), self._unc(dst))
        except Exception as e:
            raise map_smb_error(e, src) from e

    def delete(self, path: str, recursive: bool = False) -> None:
        self._require_writable()
        unc = self._unc(path)
        try:
            if smbclient.path.isdir(unc):
                if recursive:
                    _smb_shutil().rmtree(unc)
                else:
                    smbclient.rmdir(unc)
            else:
                smbclient.remove(unc)
        except Exception as e:
            raise map_smb_error(e, path) from e


# ---------------- 工厂钩子（进程内缓存 + 失败冷却） ----------------

def _fingerprint(lib: dict) -> tuple:
    return (lib.get("smb_host"), lib.get("smb_connect_host"), lib.get("smb_share"),
            lib.get("smb_subpath"), lib.get("smb_domain"), lib.get("smb_username"),
            lib.get("smb_password"))


def backend_if_available(lib: dict) -> SmbStorageBackend:
    """factory 用：优先复用缓存实例；冷却期内直接抛 StorageOffline。"""
    lid = int(lib.get("id") or 0)
    now = time.time()
    with _CACHE_LOCK:
        failed = _FAILED_AT.get(lid, 0.0)
        if failed and now - failed < _FAIL_TTL:
            left = int(_FAIL_TTL - (now - failed)) + 1
            raise StorageOffline(f"直读连接冷却中（{left}s 后重试）")
        fp = _fingerprint(lib)
        hit = _CACHE.get(lid)
        if hit and hit[0] == fp:
            return hit[1]
    try:
        backend = SmbStorageBackend(lib)
    except StorageError as e:
        with _CACHE_LOCK:
            _FAILED_AT[lid] = time.time()
            _CACHE.pop(lid, None)
        logger.info("SMB 直读不可用 lib=%s: %s", lid, e)
        raise
    with _CACHE_LOCK:
        _CACHE[lid] = (_fingerprint(lib), backend)
        _FAILED_AT.pop(lid, None)
    logger.info("SMB 直读就绪 lib=%s host=%s share=%s", lid,
                backend.connect_host, backend.share)
    return backend


def invalidate(library_id=None) -> None:
    """库配置/凭据变更或删除后调用；None=全部。"""
    with _CACHE_LOCK:
        if library_id is None:
            _CACHE.clear()
            _FAILED_AT.clear()
            return
        try:
            lid = int(library_id)
        except (TypeError, ValueError):
            return
        _CACHE.pop(lid, None)
        _FAILED_AT.pop(lid, None)
