"""库管理 API（MULTI_LIBRARY_PLAN §11）：CRUD / 检查 / SMB·NFS 挂载（C 阶段）。

- 所有库变更后调用 library_paths.invalidate_cache()。
- 凭据只写不读（store.public_library 脱敏）。
- 删库只清 DB 记录（store.delete_library），绝不触碰磁盘媒体文件。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import library_paths, mounts, secrets, storage, store
from ..log import get_logger
from ..storage import diag as storage_diag
from ..storage import smb as storage_smb

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
    return {"items": items, "default_id": d["id"] if d else None,
            "smb_driver": storage.smb_driver_mode()}


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
    smb_url: str | None = None   # 单输入框：\\主机\共享\目录（也兼容 smb://）


@router.post("")
def create_library(body: LibraryCreate):
    data = body.model_dump()
    smb_url = (data.pop("smb_url", None) or "").strip()
    if smb_url:
        data["smb"] = {**(data.get("smb") or {}), "url": smb_url}
    try:
        lib = store.create_library(**data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    library_paths.invalidate_cache()
    return _payload(lib)


class LibraryUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = None
    kind: str | None = None
    path: str | None = None   # 仅本地库且影片数为 0 时可改（D 修复：复用默认库）
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
    smb_url: str | None = None   # 编辑连接：完整地址（与 smb 二选一，url 优先）
    smb_password: str | None = None
    nfs_password: str | None = None


@router.patch("/{library_id}")
def update_library(library_id: int, body: LibraryUpdate):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(422, "nothing to update")
    smb_url = (data.pop("smb_url", None) or "").strip()
    if smb_url:
        data["smb"] = {**(data.get("smb") or {}), "url": smb_url}
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
    if str(lib.get("source") or "local") in ("smb", "nfs"):
        try:
            mounts.unmount_library(lib)
        except Exception as e:
            logger.debug("unmount before delete failed: %s", e)
    stats = store.delete_library(library_id)
    mounts.cleanup_library(lib)
    library_paths.invalidate_cache()
    return {"deleted": True, **(stats or {})}


@router.post("/{library_id}/check")
def check_library(library_id: int):
    """路径/挂载/可写性 + 影片数；SMB 直读模式走诊断管线（不触碰挂载），
    挂载模式/NFS/本地走挂载与路径检查。"""
    lib = store.get_library(library_id)
    if not lib:
        raise HTTPException(404, "library not found")
    if str(lib.get("source")) == "smb" and storage.smb_driver_mode() != "mount":
        return _check_smb_direct(lib)
    out = mounts.check_library(lib)
    out["movie_count"] = store.library_movie_count(library_id)
    try:
        out["driver"] = storage.backend_for(library_id).driver
    except storage.StorageError:
        out["driver"] = ""
    return out


class SmbDiagBody(BaseModel):
    host: str = ""
    share: str = ""
    subpath: str = ""
    username: str = ""
    password: str = ""
    domain: str = ""
    read_only: bool = False


@router.post("/diag/smb")
def diag_smb(body: SmbDiagBody):
    """网页填地址/账号后先测：分阶段诊断（指导 §13/§14），不落库。"""
    return storage_diag.diagnose_smb(**body.model_dump())


def _diag_body(lib: dict) -> dict:
    """库行 → diagnose_smb 参数（凭据解密只在内存；失败转 409）。"""
    try:
        password = secrets.decrypt_str(lib.get("smb_password") or "")
    except secrets.SecretError as e:
        raise HTTPException(409, f"凭据不可用: {e}") from e
    return {"host": lib.get("smb_host") or "", "share": lib.get("smb_share") or "",
            "subpath": lib.get("smb_subpath") or "",
            "username": lib.get("smb_username") or "", "password": password,
            "domain": lib.get("smb_domain") or "",
            "connect_host": lib.get("smb_connect_host") or "",
            "read_only": bool(int(lib.get("read_only") or 0))}


@router.post("/{library_id}/diag")
def diag_library(library_id: int):
    """对已存库运行诊断（凭据从库解密），结果落 last_status 供 UI 展示。"""
    lib = store.get_library(library_id)
    if not lib:
        raise HTTPException(404, "library not found")
    if str(lib.get("source")) != "smb":
        raise HTTPException(422, "诊断目前仅支持 SMB 库")
    out = storage_diag.diagnose_smb(**_diag_body(lib))
    status = "ok" if out.get("ok") else str(out.get("code") or "error").lower()
    store.set_library_status(library_id, status, "" if out.get("ok")
                             else f"{out.get('stage')}: {out.get('message')}")
    if out.get("ok"):
        storage_smb.invalidate(library_id)
    return out


def _check_smb_direct(lib: dict) -> dict:
    """SMB 直读库的「检查」：走诊断管线（读/写/流/ffprobe），不触碰挂载。
    只读账号写失败记为 warning：readable 仍为真（扫描/播放可用）。"""
    out = storage_diag.diagnose_smb(**_diag_body(lib))
    warnings = out.get("warnings") or []
    write_failed = any(w.get("code") == "WRITE_FAILED" for w in warnings)
    status = "ok" if out.get("ok") else str(out.get("code") or "error").lower()
    err = "" if out.get("ok") else f"{out.get('stage')}: {out.get('message')}"
    store.set_library_status(int(lib["id"]), status, err)
    if out.get("ok"):
        storage_smb.invalidate(int(lib["id"]))
    try:
        driver = storage.backend_for(int(lib["id"])).driver
    except storage.StorageError:
        driver = "smb"
    suggestions = out.get("suggestions") or []
    warn_text = warnings[0].get("message", "") if warnings else ""
    warn_sug = (warnings[0].get("suggestions") or []) if warnings else []
    reason = "；".join(suggestions[:2]) or out.get("message") or ""
    if warn_text:
        reason = (reason + "；" if reason else "") + warn_text
    return {**out,
            "id": int(lib["id"]), "source": "smb", "driver": driver,
            "readable": bool(out.get("ok")),
            "writable": bool(out.get("ok")) and not write_failed
            and not bool(int(lib.get("read_only") or 0)),
            "read_only": bool(int(lib.get("read_only") or 0)),
            "reason": reason, "error": err, "last_status": status,
            "warning": warn_text, "warning_suggestions": warn_sug,
            "movie_count": store.library_movie_count(int(lib["id"]))}


@router.post("/{library_id}/mount")
def mount_library(library_id: int):
    lib = store.get_library(library_id)
    if not lib:
        raise HTTPException(404, "library not found")
    return mounts.mount_library(lib)


@router.post("/{library_id}/unmount")
def unmount_library(library_id: int):
    lib = store.get_library(library_id)
    if not lib:
        raise HTTPException(404, "library not found")
    return mounts.unmount_library(lib)
