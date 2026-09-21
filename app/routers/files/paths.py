"""routers.files.paths（自 app/routers/files.py 拆分，评审 B9/R09-Q1；经 files 门面使用）。"""
import os
import errno
import shutil
from ... import library_paths
from ... import storage
from ...editions import _ILLEGAL
from fastapi import HTTPException
from ...log import get_logger
logger = get_logger("files.paths")
__all__ = ['_MAX_ONLY_IDS', '_safe_component', '_check_inside_root', '_only_ids',
           '_rename_or_move', '_require_writable', '_backend_for', '_exists',
           '_is_file', '_exists_map']


def _backend_for(library_id):
    """取后端；不可用（含直读库离线冷却期）返回 None，由调用方保守处理。
    离线是 debug 级：批量端点会逐行调用，warning 会刷屏。"""
    try:
        return storage.backend_for(library_id)
    except storage.StorageError as e:
        logger.debug("backend unavailable lib=%s: %s", library_id, e)
        return None


def _exists(library_id, rel: str) -> bool:
    """存在性（本地/远程统一）。后端不可用（离线/冷却期）保守返回 True——
    绝不能把 StorageOffline 当成“文件已删”，否则 /files/clean 会误删 DB 行（§19）。"""
    backend = _backend_for(library_id)
    if backend is None:
        return True
    local = backend.abs_path(rel)
    if local is not None:
        return os.path.exists(local)
    try:
        return backend.exists(rel)
    except storage.StorageError as e:
        logger.warning("exists check failed lib=%s rel=%s: %s", library_id, rel, e)
        return True


def _is_file(library_id, rel: str) -> bool:
    """是否文件（本地/远程统一）；后端不可用/错误同样保守返回 True。"""
    backend = _backend_for(library_id)
    if backend is None:
        return True
    local = backend.abs_path(rel)
    if local is not None:
        return os.path.isfile(local)
    try:
        return not backend.stat(rel).is_dir
    except storage.StorageError as e:
        logger.warning("is_file check failed lib=%s rel=%s: %s", library_id, rel, e)
        return True


def _exists_map(library_id, rels) -> dict[str, bool]:
    """批量文件存在性：远程按父目录分组，一次 backend.list 建集合（N 次 stat → D 次 list）。
    本地仍走 os.path.isfile（系统调用，便宜）。
    语义与 `_exists` 对齐：StorageNotFound → False；离线/其它存储错误保守 True（§19）。"""
    rels = {str(r) for r in (rels or ()) if r}
    if not rels:
        return {}
    backend = _backend_for(library_id)
    if backend is None:
        return {rel: True for rel in rels}
    local_root = backend.abs_path("")
    out: dict[str, bool] = {}
    by_dir: dict[str, list[str]] = {}
    for rel in rels:
        by_dir.setdefault(os.path.dirname(rel), []).append(rel)
    for d, items in by_dir.items():
        if local_root is not None:
            for rel in items:
                out[rel] = os.path.isfile(backend.abs_path(rel) or "")
            continue
        try:
            entries = backend.list(d)
        except storage.StorageNotFound:
            for rel in items:
                out[rel] = False
            continue
        except storage.StorageError as e:
            logger.debug("exists batch list failed lib=%s dir=%s: %s", library_id, d, e)
            for rel in items:
                out[rel] = True
            continue
        names = {str(e.get("name") or "") for e in entries if not e.get("is_dir")}
        for rel in items:
            out[rel] = os.path.basename(rel) in names
    return out


def _require_writable(library_id) -> None:
    """只读库写操作守卫：转 HTTP 409（D5/§10.3）。"""
    try:
        library_paths.require_writable(library_id)
    except library_paths.LibraryReadOnlyError as e:
        raise HTTPException(409, str(e))


_MAX_ONLY_IDS = 5000


def _safe_component(s: str) -> str:
    """文件名安全清洗。非法字符表与 editions 单源（评审 B11/R09-B3）；与
    editions.sanitize_tag 的差异：本函数不限长、不剥首尾点划线（用于路径段）；
    sanitize_tag 面向标签/后缀（限 20、剥边界）。"""
    s = str(s or "").strip()
    s = _ILLEGAL.sub("", s)
    return " ".join(s.split())


def _check_inside_root(rel: str, library_id=None) -> str:
    """归一并约束在指定库（缺省=默认库）根内，返回归一相对路径；非法抛 422。
    realpath 校验见 library_paths.check_inside（评审 B6/R09-B1）。"""
    lid = library_paths.default_id() if library_id is None else library_id
    try:
        return library_paths.check_inside(lid, rel)
    except ValueError as e:
        raise HTTPException(422, str(e))


def _only_ids(body: dict | None) -> set[int] | None:
    """body.ids → 选择集合（评审 B6/R09-B2）：强转 int；空=全量。
    此前 set(str) 会让字符串 id 静默不匹配，把“只操作选中项”放大成全量操作。"""
    raw = (body or {}).get("ids") or []
    if not raw:
        return None
    if len(raw) > _MAX_ONLY_IDS:
        raise HTTPException(422, f"too many ids (max {_MAX_ONLY_IDS})")
    try:
        return {int(x) for x in raw}
    except (TypeError, ValueError):
        raise HTTPException(422, "ids must be int list")


def _rename_or_move(src: str, dst: str) -> None:
    """同盘 rename；跨盘（EXDEV）退化为 shutil.move（评审 B8/R09-D3）。"""
    try:
        os.rename(src, dst)
    except OSError as e:
        if e.errno == errno.EXDEV:
            shutil.move(src, dst)
        else:
            raise

