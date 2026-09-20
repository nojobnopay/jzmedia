"""视频库 API（v17 两层）：CRUD / 检查；连接/挂载/诊断见 /api/media-libraries。

- `GET /api/libraries` 返回全部视频库（增强视图：带 media_library_id/media_name/subpath/
  来源/连接字段），前端海报墙、剧集页、工具页沿用该接口与 `?library=<视频库id>`。
- POST 两种：带 `media_library_id` 在既有媒体库下建视频库；否则按旧扁平参数自动
  建同名媒体库 + 根视频库（兼容旧客户端/脚本与测试）。
- 存储连接、根路径、只读、挂载属于媒体库；视频库只改名称/类型/子路径/命名档等。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import library_paths, mounts, storage, store
from ..log import get_logger
from .media_libraries import (_check_smb_direct, SmbDiagBody, diag_media_library,
                              diag_smb as diag_smb_media, driver_hint,
                              mount_media_library, unmount_media_library)

router = APIRouter(prefix="/api/libraries")
logger = get_logger("libraries")

_VIDEO_KEYS = ("name", "kind", "subpath", "enabled", "sort_order", "naming_profile",
               "artwork_mode", "organize_target", "inbox_dir", "metadata_providers")


def _payload(lib: dict | None) -> dict | None:
    out = store.public_library(lib)
    if out is None:
        return None
    out["movie_count"] = store.library_movie_count(out.get("id"))
    out["episode_count"] = store.library_tv_count(out.get("id"))
    out["driver"] = driver_hint(lib or {})
    return out


@router.get("")
def list_libraries():
    items = [_payload(l) for l in store.list_libraries()]
    d = store.default_library()
    return {"items": items, "default_id": d["id"] if d else None,
            "smb_driver": storage.smb_driver_mode()}


class LibraryCreate(BaseModel):
    name: str = ""
    kind: str = "movie"
    media_library_id: int | None = None
    subpath: str = ""
    # 兼容旧扁平建库（未传 media_library_id 时走 media+root-video 自动包装）
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
    smb_url: str | None = None


@router.post("")
def create_library(body: LibraryCreate):
    data = body.model_dump()
    smb_url = (data.pop("smb_url", None) or "").strip()
    if smb_url:
        data["smb"] = {**(data.get("smb") or {}), "url": smb_url}
    mid = data.pop("media_library_id", None)
    try:
        if mid is not None:
            lib = store.create_video_library(mid, **{k: data[k] for k in _VIDEO_KEYS})
        else:
            data.pop("subpath", None)
            if not str(data.get("name") or "").strip():
                raise ValueError("库名不能为空")
            lib = store.create_library(**data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    library_paths.invalidate_cache()
    return _payload(lib)


class LibraryUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = None
    kind: str | None = None   # 仅空库可改（有影片/剧集记录时 422）
    subpath: str | None = None
    enabled: bool | None = None
    sort_order: int | None = None
    naming_profile: str | None = None
    artwork_mode: str | None = None
    organize_target: str | None = None
    inbox_dir: str | None = None
    metadata_providers: str | None = None


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
    """媒体库连接 + 本视频库子目录可达性 + 记录数；SMB 直读走诊断管线。"""
    lib = store.get_library(library_id)
    if not lib:
        raise HTTPException(404, "library not found")
    media = store.get_media_library(lib.get("media_library_id"))
    if not media:
        raise HTTPException(404, "media library not found")
    if str(media.get("source")) == "smb" and storage.smb_driver_mode() != "mount":
        out = _check_smb_direct(media)
    else:
        out = mounts.check_library(media)
    ok, err = True, ""
    try:
        storage.backend_for(library_id).stat("")
    except storage.StorageError as e:
        ok, err = False, str(e)[:200]
    out["video"] = {"id": library_id, "name": lib.get("name"),
                    "subpath": lib.get("subpath") or "", "ok": ok, "error": err}
    out["movie_count"] = store.library_movie_count(library_id)
    out["episode_count"] = store.library_tv_count(library_id)
    return out


@router.post("/diag/smb")
def diag_smb(body: SmbDiagBody):
    """兼容别名：SMB 预检不落库。"""
    return diag_smb_media(body)


@router.post("/{library_id}/diag")
def diag_library(library_id: int):
    """兼容别名：解析到所属媒体库走 SMB 诊断。"""
    lib = store.get_library(library_id)
    if not lib:
        raise HTTPException(404, "library not found")
    return diag_media_library(int(lib["media_library_id"]))


@router.post("/{library_id}/mount")
def mount_library(library_id: int):
    lib = store.get_library(library_id)
    if not lib:
        raise HTTPException(404, "library not found")
    return mount_media_library(int(lib["media_library_id"]))


@router.post("/{library_id}/unmount")
def unmount_library(library_id: int):
    lib = store.get_library(library_id)
    if not lib:
        raise HTTPException(404, "library not found")
    return unmount_media_library(int(lib["media_library_id"]))
