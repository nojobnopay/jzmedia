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
                # 直读不可用且挂载点未就绪：明确上抛，绝不落到未挂载的空目录——
                # 否则读操作看到空目录，会把“库离线”误判成“文件缺失”（§19）。
                if not _mount_ready(lib):
                    raise
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


def backend_for_media(media_library_id) -> StorageBackend:
    """媒体库根后端（文件浏览的媒体根只读列表用）：把媒体库行当伪库传入同一分流点。

    - 路径 = 媒体库根（本地路径或远程挂载点），SMB 直读用媒体级 host/share/subpath。
    - 伪库 id 取 `1_000_000_000 + media_id`，避免与视频库实例/缓存冲突；
      read_only=1（媒体根不属于任何视频库，写操作一律拒绝）。
    """
    from .. import store
    try:
        mid = int(media_library_id)
    except (TypeError, ValueError):
        raise StorageError("invalid media_library_id")
    m = store.get_media_library(mid)
    if not m:
        raise StorageError("media library not found")
    pseudo_id = 1_000_000_000 + mid
    lib = {
        "id": pseudo_id,
        "library_id": pseudo_id,
        "name": str(m.get("name") or f"媒体库{mid}"),
        "source": str(m.get("source") or "local"),
        "path": str(m.get("path") or ""),
        "media_path": str(m.get("path") or ""),
        "media_mount_point": str(m.get("path") or ""),
        "read_only": 1,
        "smb_host": str(m.get("smb_host") or ""),
        "smb_share": str(m.get("smb_share") or ""),
        "smb_subpath": str(m.get("smb_subpath") or ""),
        "smb_domain": str(m.get("smb_domain") or ""),
        "smb_username": str(m.get("smb_username") or ""),
        "smb_password": str(m.get("smb_password") or ""),
        "smb_options": str(m.get("smb_options") or ""),
        "smb_connect_host": str(m.get("smb_connect_host") or ""),
        "nfs_export": str(m.get("nfs_export") or ""),
        "nfs_password": str(m.get("nfs_password") or ""),
        "nfs_options": str(m.get("nfs_options") or ""),
    }
    return backend_for_library(lib)
