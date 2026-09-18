"""库管理 API（MULTI_LIBRARY_PLAN §11）：CRUD / 检查 / 挂载占位（C 阶段落地）。

- 所有库变更后调用 library_paths.invalidate_cache()。
- 凭据只写不读（store.public_library 脱敏）。
- 删库只清 DB 记录（store.delete_library），绝不触碰磁盘媒体文件。
"""
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import library_paths, store
from ..log import get_logger

router = APIRouter(prefix="/api/libraries")
logger = get_logger("libraries")


def _payload(lib: dict | None) -> dict | None:
    out = store.public_library(lib)
    if out is None:
        return None
    out["movie_count"] = store.library_movie_count(out.get("id"))
    return out


@router.get("")
def list_libraries():
    items = [_payload(l) for l in store.list_libraries()]
    d = store.default_library()
    return {"items": items, "default_id": d["id"] if d else None}


class LibraryCreate(BaseModel):
    name: str
    kind: str = "movie"
    source: str = "local"
    path: str = ""
    read_only: bool = False
    auto_mount: bool = True
    enabled: bool = True
    sort_order: int = 0
    naming_profile: str = "kodi"
    artwork_mode: str = "nfo"
    organize_target: str = "电影"
    inbox_dir: str = "待整理"
    metadata_providers: str = ""
    smb: dict | None = None
    nfs: dict | None = None


@router.post("")
def create_library(body: LibraryCreate):
    try:
        lib = store.create_library(**body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))
    library_paths.invalidate_cache()
    return _payload(lib)


class LibraryUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = None
    kind: str | None = None
    read_only: bool | None = None
    auto_mount: bool | None = None
    enabled: bool | None = None
    sort_order: int | None = None
    naming_profile: str | None = None
    artwork_mode: str | None = None
    organize_target: str | None = None
    inbox_dir: str | None = None
    metadata_providers: str | None = None
    smb: dict | None = None
    nfs: dict | None = None
    smb_password: str | None = None
    nfs_password: str | None = None


@router.patch("/{library_id}")
def update_library(library_id: int, body: LibraryUpdate):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(422, "nothing to update")
    try:
        lib = store.update_library(library_id, **data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if lib is None:
        raise HTTPException(404, "library not found")
    library_paths.invalidate_cache()
    return _payload(lib)


@router.delete("/{library_id}")
def delete_library(library_id: int):
    lib = store.get_library(library_id)
    if not lib:
        raise HTTPException(404, "library not found")
    # 运行中的扫描任务保守取消；该库播放/预转码会话先关（防孤儿 ffmpeg 写分片）
    try:
        from . import jobs as jobs_router
        running = jobs_router._SCAN_JOBS.running()
        if running:
            jobs_router._SCAN_JOBS.cancel(running["job_id"])
    except Exception as e:
        logger.debug("cancel scan before delete failed: %s", e)
    try:
        from .stream import drop_sessions_for_version
        for vid in store.list_movie_ids_by_library(library_id):
            drop_sessions_for_version(vid)
    except Exception as e:
        logger.debug("drop sessions before delete failed: %s", e)
    stats = store.delete_library(library_id)
    library_paths.invalidate_cache()
    return {"deleted": True, **(stats or {})}


@router.post("/{library_id}/check")
def check_library(library_id: int):
    """路径/挂载/可写性 + 影片数；远程挂载能力由 C 阶段落地（当前返回宿主挂载指引）。"""
    lib = store.get_library(library_id)
    if not lib:
        raise HTTPException(404, "library not found")
    source = str(lib.get("source") or "local")
    path = str(lib.get("path") or "")
    exists = bool(path) and os.path.isdir(path)
    readable = exists and os.access(path, os.R_OK)
    writable = exists and os.access(path, os.W_OK)
    mount_supported = False
    reason = ""
    suggested_cmd = ""
    if source in ("smb", "nfs"):
        reason = "应用内挂载将在 C 阶段（app/mounts.py）落地；当前请宿主挂载后登记为 local"
        if source == "smb":
            suggested_cmd = (
                f"sudo mount -t cifs //{lib.get('smb_host')}/{lib.get('smb_share')}"
                f"{('/' + lib.get('smb_subpath')) if lib.get('smb_subpath') else ''}"
                f" {path} -o credentials=/etc/jzmedia-smb.cred,"
                "uid=$(id -u),gid=$(id -g),iocharset=utf8,soft,retrans=3,vers=3.0,nobrl")
        else:
            suggested_cmd = (f"sudo mount -t nfs {lib.get('nfs_export')} {path}"
                             " -o vers=4.1,soft,timeo=100")
    status = ("ok" if readable else ("not_mounted" if source != "local" else "error"))
    store.set_library_status(library_id, status,
                             "" if readable or source != "local" else "路径不可读")
    library_paths.invalidate_cache()
    return {"id": library_id, "source": source, "path": path,
            "exists": exists, "readable": readable, "writable": writable,
            "read_only": bool(lib.get("read_only")),
            "mount_supported": mount_supported, "reason": reason,
            "suggested_cmd": suggested_cmd,
            "movie_count": store.library_movie_count(library_id),
            "last_status": status}


@router.post("/{library_id}/mount")
def mount_library(library_id: int):
    if not store.get_library(library_id):
        raise HTTPException(404, "library not found")
    raise HTTPException(501, "应用内挂载将在 C 阶段提供；当前请宿主挂载后登记为 local")


@router.post("/{library_id}/unmount")
def unmount_library(library_id: int):
    if not store.get_library(library_id):
        raise HTTPException(404, "library not found")
    raise HTTPException(501, "应用内挂载将在 C 阶段提供")
