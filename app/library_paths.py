"""库路径解析层（MULTI_LIBRARY_PLAN §6）：媒体根路径的唯一来源。

- `libraries` 表是唯一事实源；`settings.media_root` 仅保留 v12 自举与无库兜底。
- A 阶段所有既有调用点默认指向「默认库」；B 阶段逐步显式传 `library_id`。
- 进程内快照 + TTL + 显式失效（库 CRUD/挂载状态变化后调用 `invalidate_cache()`）。
"""
import os
import threading
import time

from .config import settings
from .log import get_logger

logger = get_logger("library_paths")

DEFAULT_LIBRARY_ID = 1
_CACHE_TTL = 5.0

_cache: list[dict] = []
_cache_at = 0.0
_lock = threading.RLock()


def invalidate_cache() -> None:
    """库表变化（建/删/改/挂载状态）后调用。"""
    global _cache, _cache_at
    with _lock:
        _cache = []
        _cache_at = 0.0


def _fetch() -> list[dict]:
    try:
        from . import store
        return store.list_libraries()
    except Exception as e:   # 导入期/DB 未初始化：回退 settings.media_root
        logger.debug("list libraries failed, fallback to media_root: %s", e)
        return []


def _snapshot() -> list[dict]:
    global _cache, _cache_at
    now = time.time()
    with _lock:
        if _cache and now - _cache_at < _CACHE_TTL:
            return list(_cache)
    rows = _fetch()
    with _lock:
        _cache = rows
        _cache_at = now
        return list(rows)


def _fallback_library() -> dict:
    """无库时的兜底（v12 自举前/测试导入期），路径即 settings.media_root。"""
    return {"id": DEFAULT_LIBRARY_ID, "name": "默认库", "kind": "movie",
            "source": "local", "path": settings.media_root,
            "naming_profile": "kodi", "artwork_mode": "nfo",
            "read_only": 0, "enabled": 1}


def list_libraries(only_enabled: bool = False) -> list[dict]:
    rows = _snapshot()
    if not rows:
        return [_fallback_library()]
    return [r for r in rows if (not only_enabled or int(r.get("enabled") or 0))]


def get_library(library_id) -> dict | None:
    try:
        lid = int(library_id)
    except (TypeError, ValueError):
        return None
    for r in _snapshot():
        if int(r.get("id") or 0) == lid:
            return r
    return None


def default_library() -> dict:
    """默认库：优先 DEFAULT_LIBRARY_ID，其次第一个启用库，最后第一行。"""
    rows = _snapshot()
    if not rows:
        return _fallback_library()
    for r in rows:
        if int(r.get("id") or 0) == DEFAULT_LIBRARY_ID:
            return r
    enabled = [r for r in rows if int(r.get("enabled") or 0)]
    return enabled[0] if enabled else rows[0]


def default_id() -> int:
    return int(default_library().get("id") or DEFAULT_LIBRARY_ID)


def default_root() -> str:
    return str(default_library().get("path") or settings.media_root)


def library_root(library_id) -> str:
    lib = get_library(library_id)
    return str((lib or default_library()).get("path") or settings.media_root)


def resolve(library_id, rel: str) -> str:
    """库内相对路径 → 绝对路径（rel 为空返回库根）。"""
    root = library_root(library_id)
    return os.path.join(root, rel) if rel else root


def abs_path(rel: str, library_id=None) -> str:
    """默认库（或指定库）内相对路径 → 绝对路径。"""
    return resolve(default_id() if library_id is None else library_id, rel)


def locate(abs_path_str: str) -> tuple[dict | None, str | None]:
    """绝对路径 → (库, 相对路径)；多库重叠取最长前缀。未命中回退默认库/None。"""
    p = os.path.normpath(str(abs_path_str or ""))
    best: tuple[dict, str] | None = None
    for lib in _snapshot():
        root = os.path.normpath(str(lib.get("path") or ""))
        if not root:
            continue
        if p == root or p.startswith(root + os.sep):
            if best is None or len(root) > len(best[1]):
                best = (lib, root)
    if best is not None:
        return best[0], os.path.relpath(p, best[1])
    if not _snapshot():
        lib = _fallback_library()
        root = os.path.normpath(lib["path"])
        if p == root or p.startswith(root + os.sep):
            return lib, os.path.relpath(p, root)
    return None, None


def locate_default(abs_path_str: str) -> str | None:
    """绝对路径 → 默认库内相对路径；不在任何库内返回 None。"""
    lib, rel = locate(abs_path_str)
    if lib is None or int(lib.get("id") or 0) != default_id():
        return None
    return rel


def check_inside(library_id, rel: str) -> str:
    """归一并约束在库根内，返回归一相对路径；非法抛 ValueError（调用方转 422）。
    除字符串边界外还做 realpath 校验（评审 B6/R09-B1）：根内符号链接指向外部时，
    仅 normpath 检查会放行，实际写入/读取会落到根外。"""
    norm = os.path.normpath((rel or "").strip().strip("/"))
    if not norm or norm == "." or norm.startswith("..") or os.path.isabs(rel or ""):
        raise ValueError(f"illegal path: {rel!r}")
    root = library_root(library_id)
    try:
        root_real = os.path.realpath(root)
        abs_real = os.path.realpath(os.path.join(root, norm))
    except (OSError, ValueError):
        raise ValueError(f"illegal path: {rel!r}")
    if abs_real != root_real and not abs_real.startswith(root_real + os.sep):
        raise ValueError(f"path escapes library root: {rel!r}")
    return norm


def is_read_only(library_id) -> bool:
    lib = get_library(library_id) or default_library()
    return bool(int(lib.get("read_only") or 0))


def naming_profile(library_id) -> str:
    """库级命名档：plex|kodi|off（缺省 kodi）。"""
    lib = get_library(library_id) or default_library()
    v = str(lib.get("naming_profile") or "kodi").strip().lower()
    return v if v in ("plex", "kodi", "off") else "kodi"


def per_version_meta(library_id, versions) -> bool:
    """多版本是否写 per-version 元数据（`<stem>.nfo` / `<stem>-poster.jpg`）：
    默认仅 plex 档且版本间 edition 不同才写（Plex 拆独立条目）；env `PER_VERSION_META=1`
    可强制回退旧行为（所有多版本都写）。"""
    v = (os.getenv("PER_VERSION_META") or "").strip().lower()
    if v and v not in ("0", "false", "no", "off"):
        return True
    if naming_profile(library_id) != "plex":
        return False
    eds = {str((x or {}).get("edition") or "") for x in (versions or [])}
    return len(eds) > 1


def artwork_mode(library_id) -> str:
    """库级落盘策略：none|nfo|nfo_art（缺省 nfo）。"""
    lib = get_library(library_id) or default_library()
    v = str(lib.get("artwork_mode") or "nfo").strip().lower()
    return v if v in ("none", "nfo", "nfo_art") else "nfo"


class LibraryReadOnlyError(RuntimeError):
    """只读库写入被拒（路由层转 409）。"""


def require_writable(library_id) -> None:
    """只读库的写操作守卫：归档/移动/改名/删除/上传/NFO/图片写入前调用。"""
    if is_read_only(library_id):
        lib = get_library(library_id) or default_library()
        raise LibraryReadOnlyError(
            f"库「{lib.get('name') or library_id}」为只读，写操作被拒")


def ensure_roots() -> None:
    """为本地库创建根目录（smb/nfs 挂载点由 mounts 管理）。失败只告警。"""
    for lib in list_libraries(only_enabled=True):
        if str(lib.get("source") or "local") != "local":
            continue
        path = str(lib.get("path") or "")
        if not path:
            continue
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            logger.warning("create library root failed id=%s path=%s: %s",
                           lib.get("id"), path, e)
