"""routers.movies.common（自 app/routers/movies.py 拆分，评审 B9/R05-Q1；经 movies 门面使用）。"""
import os
from fastapi import HTTPException
from ... import library_roots, storage, store
from ...log import get_logger
logger = get_logger("movies.common")
__all__ = ['FilterList', '_PAGE_MAX', '_page',
           '_stream_upload_backend', '_INLINE_EXTS', '_library_scope']

FilterList = list[str] | None

# 媒体库无视频库/参数非法时的哨兵：IN (-1) 必空，绝不退化成全库
_EMPTY_LIB = [-1]


def _library_scope(library, media_library) -> list | None:
    """读接口库范围（v18 媒体库聚合）：media_library 优先 → 其全部视频库 id；
    library（视频库，可重复/逗号）→ 指定 id；都缺省=None（全库不过滤）。"""
    if media_library is not None and str(media_library).strip() != "":
        try:
            mid = int(media_library)
        except (TypeError, ValueError):
            return list(_EMPTY_LIB)
        return store.library_ids_for_media(mid) or list(_EMPTY_LIB)
    return store._split_ints(library)


_PAGE_MAX = 2000


def _page(limit: int, offset: int) -> tuple[int, int]:
    """分页参数钳制（评审 P1-11）：limit 1..2000、offset >= 0。"""
    try:
        lim = max(1, min(int(limit or 500), _PAGE_MAX))
    except (TypeError, ValueError):
        lim = 500
    try:
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        off = 0
    return lim, off


def _stream_upload_backend(file, backend, rel: str) -> int:
    """流式上传，原子提交时拒绝覆盖，即使同名文件并发出现。"""
    rel = backend.norm(rel)
    size = 0
    try:
        with library_roots.upload_root(backend) as verify_root:
            if backend.exists(rel):
                raise HTTPException(409, f"already exists: {os.path.basename(rel)!r}")
            with backend.open_write(rel, overwrite=False) as out:
                while chunk := file.file.read(1024 * 1024):
                    out.write(chunk)
                    size += len(chunk)
                verify_root()
    except storage.StorageExists:
        raise HTTPException(409, f"already exists: {os.path.basename(rel)!r}") from None
    except library_roots.RootReview as e:
        raise HTTPException(409, f"{e} 请在视频库的扫描工具中核对挂载。") from e
    except storage.StorageOffline as e:
        raise HTTPException(503, str(e)) from e
    except storage.StorageError as e:
        raise HTTPException(500, f"upload failed: {e}") from e
    return size


_INLINE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}

