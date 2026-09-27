"""routers.movies.common（自 app/routers/movies.py 拆分，评审 B9/R05-Q1；经 movies 门面使用）。"""
import os
from fastapi import HTTPException
from ... import storage, store
from ...log import get_logger
logger = get_logger("movies.common")
__all__ = ['FilterList', '_PAGE_MAX', '_page', '_stream_upload',
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


def _stream_upload(file, dst: str) -> int:
    """流式落盘（1MB 分块，先写 .part 再原子替换）。并发同名上传用 O_EXCL 占位：
    第二个请求在占位阶段即 409，绝不覆盖（评审 B5a-5/R05-D2）。返回落盘字节数。"""
    part = dst + ".part"
    try:
        fd = os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        raise HTTPException(409, f"already exists: {os.path.basename(dst)!r}")
    except OSError as e:
        raise HTTPException(500, f"create target failed: {e}")
    size = 0
    replaced = False
    try:
        with open(part, "wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                size += len(chunk)
        os.replace(part, dst)
        replaced = True
    except Exception as e:
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError:
            pass
        if not replaced:
            try:
                os.remove(dst)      # 清掉本次创建的占位
            except OSError:
                pass
        raise HTTPException(500, f"upload failed: {e}")
    return size


def _stream_upload_backend(file, backend, rel: str) -> int:
    """远程流式上传，原子提交时拒绝覆盖，即使同名文件并发出现。"""
    rel = backend.norm(rel)
    try:
        if backend.exists(rel):
            raise HTTPException(409, f"already exists: {os.path.basename(rel)!r}")
    except storage.StorageError as e:
        raise HTTPException(503, f"check target failed: {e}")
    size = 0
    try:
        with backend.open_write(rel, overwrite=False) as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                size += len(chunk)
    except storage.StorageExists:
        raise HTTPException(409, f"already exists: {os.path.basename(rel)!r}")
    except storage.StorageError as e:
        raise HTTPException(500, f"upload failed: {e}")
    return size


_INLINE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}

