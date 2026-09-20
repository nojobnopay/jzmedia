"""媒体库 API（v17 两层）：CRUD / 检查 / 挂载 / 诊断 / 子目录浏览。

- 媒体库 = 存储连接与根（凭据只写不读，store.public_media_library 脱敏）。
- 视频库（`/api/libraries`）挂在媒体库下；建媒体库时至少带一个视频库（含类型）。
- 删媒体库级联清掉其全部视频库记录（store.delete_media_library），绝不触碰磁盘媒体文件。
"""
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import library_paths, mounts, secrets, storage, store
from ..log import get_logger
from ..storage import diag as storage_diag
from ..storage import smb as storage_smb

router = APIRouter(prefix="/api/media-libraries")
logger = get_logger("media_libraries")


def driver_hint(lib: dict) -> str:
    """媒体库当前实际访问方式（仅展示用，不建立连接）：
    local=本地路径；smb=用户态直读；mount=容器/宿主挂载。"""
    source = str(lib.get("source") or "local")
    if source == "local":
        return "local"
    if source == "nfs":
        return "mount"
    mode = storage.smb_driver_mode()
    if mode == "mount":
        return "mount"
    if mode == "auto":
        path = str(lib.get("path") or "")
        try:
            if path and os.path.ismount(path):
                return "mount"
        except OSError:
            pass
    return "smb"


def video_payload(v: dict) -> dict:
    return {
        "id": v.get("id"), "media_library_id": v.get("media_library_id"),
        "name": v.get("name"), "kind": v.get("kind"), "subpath": v.get("subpath") or "",
        "path": v.get("path") or "", "enabled": bool(v.get("enabled")),
        "effective_enabled": bool(v.get("effective_enabled")),
        "sort_order": int(v.get("sort_order") or 0),
        "naming_profile": v.get("naming_profile"), "artwork_mode": v.get("artwork_mode"),
        "organize_target": v.get("organize_target"), "inbox_dir": v.get("inbox_dir"),
        "movie_count": store.library_movie_count(v.get("id")),
        "episode_count": store.library_tv_count(v.get("id")),
        "last_status": v.get("last_status") or "",
    }


def _payload(m: dict | None) -> dict | None:
    out = store.public_media_library(m)
    if out is None:
        return None
    counts = store.media_counts(m["id"])
    out["movie_count"] = counts["movies"]
    out["episode_count"] = counts["episodes"]
    out["show_count"] = counts["shows"]
    out["driver"] = driver_hint(m)
    out["video_libraries"] = [video_payload(v)
                              for v in store.video_libraries_of(m["id"])]
    return out


@router.get("")
def list_media_libraries():
    items = [_payload(m) for m in store.list_media_libraries()]
    d = store.default_library()
    return {"items": items, "default_id": d["id"] if d else None,
            "smb_driver": storage.smb_driver_mode()}


class VideoLibraryIn(BaseModel):
    name: str = ""
    kind: str = "movie"
    subpath: str = ""
    naming_profile: str | None = None
    artwork_mode: str | None = None
    organize_target: str | None = None
    inbox_dir: str | None = None
    metadata_providers: str | None = None
    enabled: bool = True
    sort_order: int = 0


class MediaLibraryCreate(BaseModel):
    name: str
    source: str = "local"
    path: str = ""
    read_only: bool = False
    auto_mount: bool = True
    enabled: bool = True
    sort_order: int = 0
    smb: dict | None = None
    nfs: dict | None = None
    smb_url: str | None = None   # 单输入框：\\主机\共享\目录（也兼容 smb://）
    video_libraries: list[VideoLibraryIn]


@router.post("")
def create_media_library(body: MediaLibraryCreate):
    data = body.model_dump()
    smb_url = (data.pop("smb_url", None) or "").strip()
    if smb_url:
        data["smb"] = {**(data.get("smb") or {}), "url": smb_url}
    try:
        lib = store.create_media_library(**data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    library_paths.invalidate_cache()
    return _payload(lib)


class MediaLibraryUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = None
    path: str | None = None   # 仅本地媒体库且无记录时可改
    read_only: bool | None = None
    auto_mount: bool | None = None
    enabled: bool | None = None
    sort_order: int | None = None
    smb: dict | None = None
    nfs: dict | None = None
    smb_url: str | None = None   # 编辑连接：完整地址（与 smb 二选一，url 优先）
    smb_password: str | None = None
    nfs_password: str | None = None


@router.patch("/{media_library_id}")
def update_media_library(media_library_id: int, body: MediaLibraryUpdate):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(422, "nothing to update")
    smb_url = (data.pop("smb_url", None) or "").strip()
    if smb_url:
        data["smb"] = {**(data.get("smb") or {}), "url": smb_url}
    try:
        lib = store.update_media_library(media_library_id, **data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if lib is None:
        raise HTTPException(404, "media library not found")
    if str(lib.get("source") or "") == "smb" and any(
            k in data for k in ("smb", "smb_password")):
        storage_smb.invalidate()   # 连接参数变了：失效全部直读缓存
    library_paths.invalidate_cache()
    return _payload(lib)


def _drop_media_sessions(media_id: int) -> None:
    """关闭该媒体库下所有视频库的播放/预转码会话（防孤儿 ffmpeg 写分片）。"""
    try:
        from .stream import drop_sessions_for_version
        for v in store.video_libraries_of(media_id):
            for vid in store.list_movie_ids_by_library(v["id"]):
                drop_sessions_for_version(vid)
    except Exception as e:
        logger.debug("drop sessions before media delete failed: %s", e)


@router.delete("/{media_library_id}")
def delete_media_library(media_library_id: int):
    lib = store.get_media_library(media_library_id)
    if not lib:
        raise HTTPException(404, "media library not found")
    try:
        from . import jobs as jobs_router
        running = jobs_router._SCAN_JOBS.running()
        if running:
            jobs_router._SCAN_JOBS.cancel(running["job_id"])
    except Exception as e:
        logger.debug("cancel scan before media delete failed: %s", e)
    _drop_media_sessions(media_library_id)
    if str(lib.get("source") or "local") in ("smb", "nfs"):
        try:
            mounts.unmount_library(lib)
        except Exception as e:
            logger.debug("unmount before media delete failed: %s", e)
    stats = store.delete_media_library(media_library_id)
    mounts.cleanup_library(lib)
    if str(lib.get("source") or "") == "smb":
        storage_smb.invalidate()
    library_paths.invalidate_cache()
    return {"deleted": True, **(stats or {})}


def _video_status(media_id: int) -> list[dict]:
    """各视频库子目录可达性（检查结果里附带，便于定位 subpath 写错）。"""
    out = []
    for v in store.video_libraries_of(media_id):
        ok, err = True, ""
        try:
            storage.backend_for(v["id"]).stat("")
        except storage.StorageError as e:
            ok, err = False, str(e)[:200]
        out.append({"id": v["id"], "name": v["name"], "kind": v["kind"],
                    "subpath": v["subpath"], "path": v["path"], "ok": ok,
                    "error": err})
    return out


@router.post("/{media_library_id}/check")
def check_media_library(media_library_id: int):
    """连接/挂载/可写性 + 记录数与各视频库子目录可达性。SMB 直读走诊断管线。"""
    lib = store.get_media_library(media_library_id)
    if not lib:
        raise HTTPException(404, "media library not found")
    if str(lib.get("source")) == "smb" and storage.smb_driver_mode() != "mount":
        out = _check_smb_direct(lib)
    else:
        out = mounts.check_library(lib)
        counts = store.media_counts(media_library_id)
        out["movie_count"] = counts["movies"]
        try:
            out["driver"] = storage.backend_for_library(lib).driver
        except storage.StorageError:
            out["driver"] = ""
    out["video_libraries"] = _video_status(media_library_id)
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
    """媒体库行 → diagnose_smb 参数（凭据解密只在内存；失败转 409）。"""
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


@router.post("/{media_library_id}/diag")
def diag_media_library(media_library_id: int):
    """对已存媒体库运行诊断（凭据从库解密），结果落 last_status 供 UI 展示。"""
    lib = store.get_media_library(media_library_id)
    if not lib:
        raise HTTPException(404, "media library not found")
    if str(lib.get("source")) != "smb":
        raise HTTPException(422, "诊断目前仅支持 SMB 媒体库")
    out = storage_diag.diagnose_smb(**_diag_body(lib))
    status = "ok" if out.get("ok") else str(out.get("code") or "error").lower()
    store.set_media_status(media_library_id, status, "" if out.get("ok")
                           else f"{out.get('stage')}: {out.get('message')}")
    if out.get("ok"):
        storage_smb.invalidate()
    return out


def _check_smb_direct(lib: dict) -> dict:
    """SMB 直读媒体库的「检查」：走诊断管线（读/写/流/ffprobe），不触碰挂载。
    只读账号写失败记为 warning：readable 仍为真（扫描/播放可用）。"""
    out = storage_diag.diagnose_smb(**_diag_body(lib))
    warnings = out.get("warnings") or []
    write_failed = any(w.get("code") == "WRITE_FAILED" for w in warnings)
    status = "ok" if out.get("ok") else str(out.get("code") or "error").lower()
    err = "" if out.get("ok") else f"{out.get('stage')}: {out.get('message')}"
    store.set_media_status(int(lib["id"]), status, err)
    if out.get("ok"):
        storage_smb.invalidate()
    driver = "smb"
    try:
        driver = storage.backend_for_library(lib).driver
    except storage.StorageError:
        pass
    suggestions = out.get("suggestions") or []
    warn_text = warnings[0].get("message", "") if warnings else ""
    warn_sug = (warnings[0].get("suggestions") or []) if warnings else []
    reason = "；".join(suggestions[:2]) or out.get("message") or ""
    if warn_text:
        reason = (reason + "；" if reason else "") + warn_text
    counts = store.media_counts(int(lib["id"]))
    return {**out,
            "id": int(lib["id"]), "source": "smb", "driver": driver,
            "readable": bool(out.get("ok")),
            "writable": bool(out.get("ok")) and not write_failed
            and not bool(int(lib.get("read_only") or 0)),
            "read_only": bool(int(lib.get("read_only") or 0)),
            "reason": reason, "error": err, "last_status": status,
            "warning": warn_text, "warning_suggestions": warn_sug,
            "movie_count": counts["movies"]}


@router.post("/{media_library_id}/mount")
def mount_media_library(media_library_id: int):
    lib = store.get_media_library(media_library_id)
    if not lib:
        raise HTTPException(404, "media library not found")
    return mounts.mount_library(lib)


@router.post("/{media_library_id}/unmount")
def unmount_media_library(media_library_id: int):
    lib = store.get_media_library(media_library_id)
    if not lib:
        raise HTTPException(404, "media library not found")
    return mounts.unmount_library(lib)


@router.get("/{media_library_id}/subdirs")
def list_subdirs(media_library_id: int, path: str = ""):
    """列出媒体库根（或指定子路径）下的目录：添加视频库时选题材目录用。"""
    lib = store.get_media_library(media_library_id)
    if not lib:
        raise HTTPException(404, "media library not found")
    try:
        backend = storage.backend_for_library(lib)
        rel = backend.norm(path)
        entries = backend.list(rel)
    except storage.StorageError as e:
        raise HTTPException(502, f"读取目录失败: {e}") from e
    dirs = []
    for e in entries:
        if not e.get("is_dir") or str(e.get("name") or "").startswith("."):
            continue
        name = e["name"]
        dirs.append({"name": name, "rel": f"{rel}/{name}" if rel else name})
    dirs.sort(key=lambda d: d["name"].lower())
    return {"media_library_id": media_library_id, "path": rel, "dirs": dirs}
