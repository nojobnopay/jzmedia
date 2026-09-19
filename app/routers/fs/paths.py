"""routers.fs.paths（自 app/routers/fs.py 拆分，评审 R01-Q4；经 fs 门面使用）。"""
import os

from fastapi import HTTPException

from ... import library_paths
from ... import storage
from ..files import _check_inside_root
__all__ = ['_resolve_dir', '_check_inside_root']


def _resolve_dir(rel: str | None, library_id=None) -> str:
    """目录相对路径归一：空串=根；其余必须在库内且对应目录（缺省=默认库）。
    直读远程库经 StorageBackend.is_dir 判定（不依赖挂载点）。"""
    raw = (rel or "").strip().strip("/")
    if not raw:
        return ""
    lid = library_paths.default_id() if library_id is None else int(library_id)
    norm = _check_inside_root(raw, lid)
    try:
        backend = storage.backend_for(lid)
    except storage.StorageError as e:
        raise HTTPException(503, f"library unavailable: {e}")
    if backend.abs_path(norm) is None:
        try:
            if not backend.is_dir(norm):
                raise HTTPException(404, f"not a directory: {rel!r}")
        except HTTPException:
            raise
        except storage.StorageOffline as e:
            raise HTTPException(503, f"source offline: {e}")
        except storage.StorageError:
            raise HTTPException(404, f"not a directory: {rel!r}")
        return norm
    abs_p = library_paths.resolve(lid, norm)
    if not os.path.isdir(abs_p):
        raise HTTPException(404, f"not a directory: {rel!r}")
    return norm
