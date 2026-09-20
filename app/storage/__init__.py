"""统一存储层（远程媒体库指导 §6/§37 Phase1–3）。

- `base`：StorageBackend 接口 + 错误/Stat 类型。
- `local`：POSIX 后端（本地库、挂载点）。
- `smb`：用户态 SMB2/3 直读后端（smbprotocol；`SMB_DRIVER=mount` 可回退挂载）。
- `httpproxy`：内网 HTTP Range 服务，供 ffprobe/ffmpeg 读取远程媒体。
- `factory`：按库选择后端的唯一入口。
"""
from .base import (
    MediaSource, StorageBackend, StorageDenied, StorageError, StorageInvalidPath,
    StorageNotFound, StorageOffline, StorageReadOnly, StorageStat,
    StorageUnsupported, WalkEntry,
)
from .factory import (backend_for, backend_for_library, backend_for_media,
                      smb_driver_mode)
from .local import LocalStorageBackend

__all__ = [
    'StorageBackend', 'StorageStat', 'MediaSource', 'WalkEntry', 'LocalStorageBackend',
    'StorageError', 'StorageInvalidPath', 'StorageNotFound', 'StorageDenied',
    'StorageReadOnly', 'StorageOffline', 'StorageUnsupported',
    'backend_for', 'backend_for_library', 'backend_for_media', 'smb_driver_mode',
    'media_source', 'media_write_path',
]


def media_source(library_id, rel: str) -> MediaSource:
    """库内相对路径 → MediaSource（播放/探测统一入口；错误由调用方映射 HTTP）。"""
    backend = backend_for(library_id)
    norm = backend.norm(rel)
    st = backend.stat(norm)
    if st.is_dir:
        raise StorageError(f"不是文件: {rel}")
    return MediaSource(backend=backend, rel=norm, input=backend.get_read_url(norm),
                       local_path=backend.abs_path(norm),
                       size=st.size, mtime=st.mtime)


def media_write_path(library_id, rel: str) -> str:
    """媒体目录写路径（NFO/本地图片落盘用）：直读远程无 POSIX 路径时返回 ''。

    调用方见到 '' 应跳过落盘（NFO/artwork 为可选增强，不影响入库）。
    """
    try:
        backend = backend_for(library_id)
        return backend.abs_path(rel) or ""
    except StorageError:
        return ""
