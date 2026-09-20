"""存储后端选择（唯一按来源分流的位置）——指导 §21–27 / §40。

- 本地库 → `LocalStorageBackend(driver=local)`（POSIX）。
- smb 库：`SMB_DRIVER` 控制访问方式（auto|direct|mount，默认 auto）；
  auto = 挂载点可用则用挂载（快、存量行为不变），否则用户态直读（免 mount）。
  nfs 库第一阶段固定挂载兼容模式（指导 §27）。
- 调用方不得自行判断来源/驱动（约束 2），只允许 `backend_for*`。
"""
from __future__ import annotations

import os

from ..log import get_logger
from .base import StorageBackend, StorageError
from .local import LocalStorageBackend

logger = get_logger("storage.factory")

__all__ = ['backend_for', 'backend_for_library', 'smb_driver_mode']

_SMB_DRIVERS = ("auto", "direct", "mount")


def smb_driver_mode() -> str:
    """env `SMB_DRIVER`：direct=强制直读；mount=强制挂载兼容；auto=挂载可用优先挂载。"""
    v = os.getenv("SMB_DRIVER", "auto").strip().lower()
    return v if v in _SMB_DRIVERS else "auto"


def _mount_ready(lib: dict) -> bool:
    """媒体库挂载点已挂载且可读（视频库 path 是挂载点子目录，不能用 ismount(path)）。"""
    mp = str(lib.get("media_mount_point") or lib.get("media_path")
             or lib.get("path") or "")
    if not mp:
        return False
    try:
        return os.path.ismount(mp) and os.access(mp, os.R_OK)
    except OSError:
        return False


def backend_for_library(lib: dict | None) -> StorageBackend:
    if not lib:
        raise StorageError("library not found")
    source = str(lib.get("source") or "local")
    if source == "smb":
        mode = smb_driver_mode()
        if mode == "direct" or (mode == "auto" and not _mount_ready(lib)):
            try:
                from . import smb
                return smb.backend_if_available(lib)
            except Exception as e:
                logger.warning("SMB 直读不可用，回退挂载点 lib=%s: %s",
                               lib.get("id"), e)
    driver = "mount" if source in ("smb", "nfs") else "local"
    return LocalStorageBackend(lib, driver=driver)


def backend_for(library_id=None) -> StorageBackend:
    """按库 id 取后端；缺省/未知回落默认库（与 library_paths 语义一致）。"""
    from .. import library_paths
    lib = None
    if library_id is not None:
        try:
            lib = library_paths.get_library(int(library_id))
        except (TypeError, ValueError):
            lib = None
    return backend_for_library(lib or library_paths.default_library())
