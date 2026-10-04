import os
import re
import stat

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, storage, store
from ..config import settings
from ..db import TRANSCODE_DIR
from ..log import get_logger
from ..version import VERSION, build_commit

router = APIRouter(prefix="/api")
logger = get_logger("health")


def _disk_error(exc: Exception) -> dict:
    """Keep filesystem errors useful without returning exception strings or paths."""
    if isinstance(exc, FileNotFoundError):
        code, message = "not_found", "目录尚未创建或已不可达"
    elif isinstance(exc, PermissionError):
        code, message = "permission_denied", "没有读取目录或文件系统容量的权限"
    elif isinstance(exc, NotADirectoryError):
        code, message = "not_directory", "配置的存储位置不是目录"
    elif isinstance(exc, ValueError):
        code, message = "invalid_path", "配置的存储位置无效"
    elif isinstance(exc, NotImplementedError):
        code, message = "unsupported", "当前平台不支持文件系统容量查询"
    else:
        code, message = "unavailable", "暂时无法读取文件系统容量"
    return {"code": code, "message": message}


def _disk_space() -> dict:
    """Read only the configured data/cache directories; never walk media or probe NAS.

    st_dev identifies a filesystem in this server's mount namespace, not a physical
    disk. Bind mounts/symlinks on one filesystem share one capacity snapshot. Used
    excludes free blocks; available excludes blocks reserved from ordinary users.
    A missing directory is an error, rather than a guess based on its parent.
    """
    targets = {}
    filesystems = {}
    for name, path in (("data", settings.data_dir), ("transcode", TRANSCODE_DIR)):
        target = {"ok": False, "filesystem_id": None}
        try:
            info = os.stat(path)
            if not stat.S_ISDIR(info.st_mode):
                raise NotADirectoryError()
            filesystem_id = f"dev:{info.st_dev}"
            target["filesystem_id"] = filesystem_id
            if filesystem_id not in filesystems:
                if not hasattr(os, "statvfs"):
                    raise NotImplementedError()
                usage = os.statvfs(path)
                block_size = usage.f_frsize or usage.f_bsize
                filesystems[filesystem_id] = {
                    "id": filesystem_id,
                    "total_bytes": max(0, usage.f_blocks * block_size),
                    "used_bytes": max(0, (usage.f_blocks - usage.f_bfree) * block_size),
                    "available_bytes": max(0, usage.f_bavail * block_size),
                }
            target["ok"] = True
        except (OSError, ValueError, NotImplementedError) as exc:
            target["error"] = _disk_error(exc)
            logger.warning("disk capacity unavailable target=%s code=%s", name, target["error"]["code"])
        targets[name] = target
    data_id, transcode_id = (targets[name]["filesystem_id"] for name in ("data", "transcode"))
    return {"ok": all(target["ok"] for target in targets.values()),
            "same_filesystem": data_id == transcode_id if data_id and transcode_id else None,
            **targets, "filesystems": list(filesystems.values())}


@router.get("/health")
def health():
    try:
        from .. import media as _media
        bins = _media.bin_status()
    except Exception:
        bins = {"ffmpeg": False, "ffprobe": False}
    try:
        from .. import transcode as _tr
        tw = _tr.backend_info()
    except Exception:
        tw = {"name": "software", "hw": False, "reason": "detect failed"}
    # DB/媒体根自检（评审 B6/R01-D4）：失败时 status=degraded，编排/前端可据此告警
    try:
        dbh = store.health_check()
    except Exception as e:
        dbh = {"ok": False, "readable": False, "writable": False,
               "error": str(e)[:200], "bytes": 0}
    # 逐媒体库可读性自检（v17 两层）：任一启用媒体库不可读 → degraded
    libs = []
    all_ok = True
    try:
        for lib in store.list_media_libraries(only_enabled=True):
            p = str(lib.get("path") or "")
            source = str(lib.get("source") or "local")
            mounted_ok = bool(p) and os.path.isdir(p) and os.access(p, os.R_OK)
            # SMB 直读不依赖本地挂载点：连接状态由 /check 诊断管线按需验证
            ok = mounted_ok or (source == "smb"
                                and storage.smb_driver_mode() != "mount")
            all_ok = all_ok and ok
            libs.append({"id": lib.get("id"), "name": lib.get("name"),
                         "source": source,
                         "path": p, "ok": ok, "read_only": bool(lib.get("read_only")),
                         "last_status": lib.get("last_status") or "",
                         "video_libraries": [
                             {"id": v.get("id"), "name": v.get("name"),
                              "kind": v.get("kind"), "subpath": v.get("subpath") or "",
                              "path": v.get("path") or ""}
                             for v in store.video_libraries_of(lib.get("id"))]})
    except Exception as e:
        all_ok = False
        libs = [{"id": None, "name": "", "kind": "", "source": "",
                 "path": settings.media_root, "ok": False, "read_only": False,
                 "last_status": "", "error": str(e)[:200]}]
    commit = _build_commit()
    return {"status": "ok" if (dbh.get("ok") and all_ok) else "degraded",
            "version": VERSION.name, "version_code": VERSION.code,
            "db": dbh,
            "media": {"root": settings.media_root, "ok": bool(all_ok),
                      "libraries": libs},
            "ffmpeg": bool(bins.get("ffmpeg")), "ffprobe": bool(bins.get("ffprobe")),
            "transcoder": tw,
            # Capacity collection is independent of the existing DB/media status.
            "disks": _disk_space(),
            "build": commit[:12], "build_commit": commit}


def _build_commit() -> str:
    """镜像使用构建时注入的完整提交；源码运行时读取当前仓库，无 Git 时 unknown。"""
    return build_commit()


def _settings_view() -> dict:
    """配置摘要：密钥只给脱敏值+来源（绝不返明文）；代理/语言/图片源给有效值+来源。”
    """
    token, token_src = config.effective_with_source("tmdb_read_token")
    api_key, key_src = config.effective_with_source("tmdb_api_key")
    proxy, proxy_src = config.effective_with_source("tmdb_proxy")
    lang, lang_src = config.effective_with_source("tmdb_language")
    img, img_src = config.effective_with_source("tmdb_image_base")
    auth_token, auth_src = config.effective_with_source("jzmedia_token")
    # 凭证来源：优先展示实际生效的那一路
    cred_src = token_src if token else (key_src if api_key else "unset")
    return {
        "media_root": settings.media_root,
        "libraries": [{"id": m.get("id"), "name": m.get("name"),
                       "source": m.get("source"),
                       "path": m.get("path"), "enabled": bool(m.get("enabled")),
                       "read_only": bool(m.get("read_only")),
                       "last_status": m.get("last_status") or "",
                       "video_libraries": [
                           {"id": v.get("id"), "name": v.get("name"),
                            "kind": v.get("kind"), "subpath": v.get("subpath") or "",
                            "enabled": bool(v.get("enabled"))}
                           for v in store.video_libraries_of(m.get("id"))]}
                      for m in store.list_media_libraries()],
        # 兼容老字段
        "tmdb_language": lang,
        "tmdb_configured": bool(token or api_key),
        "tmdb_proxy_set": bool(proxy),
        "tmdb_image_base": img,
        # 新增：脱敏 + 来源
        "tmdb_read_token_masked": config.mask_secret(token),
        "tmdb_read_token_source": token_src,
        "tmdb_api_key_masked": config.mask_secret(api_key),
        "tmdb_api_key_source": key_src,
        "tmdb_token_source": cred_src,
        # 脱敏回显（评审 R01-B6）：代理 URL 常含 user:pass；前端保存时按值比对，回显值不落库
        "tmdb_proxy": config.mask_proxy(proxy),
        "tmdb_proxy_source": proxy_src,
        "tmdb_language_source": lang_src,
        "tmdb_image_base_source": img_src,
        # 写操作访问控制（P1-01）：只回脱敏与来源，绝不回明文
        "jzmedia_auth_enabled": bool(auth_token),
        "jzmedia_token_masked": config.mask_secret(auth_token),
        "jzmedia_token_source": auth_src,
    }


@router.get("/settings")
def get_settings():
    return _settings_view()


class SettingsUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    tmdb_read_token: str | None = None
    tmdb_api_key: str | None = None
    tmdb_proxy: str | None = None
    tmdb_language: str | None = None
    tmdb_image_base: str | None = None
    jzmedia_token: str | None = None


_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z]{2,4})?$")


def _validate(key: str, value: str) -> str:
    v = (value or "").strip()
    if key in ("tmdb_read_token", "tmdb_api_key", "jzmedia_token"):
        # 空串 = 清空（恢复跟随 env）；非空做最小长度拦截，防手误粘贴半截
        minimum = 8 if key == "jzmedia_token" else 10
        if v and len(v) < minimum:
            raise HTTPException(422, f"{key} too short, check paste")
        return v
    if key in ("tmdb_proxy", "tmdb_image_base"):
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise HTTPException(422, f"{key} must start with http:// or https://")
        return v.rstrip("/") if key == "tmdb_image_base" and v else v
    if key == "tmdb_language":
        if v and not _LANG_RE.match(v):
            raise HTTPException(422, "tmdb_language like zh-CN / en-US")
        return v
    raise HTTPException(422, f"unknown setting: {key}")


@router.put("/settings")
def update_settings(body: SettingsUpdate):
    """保存配置（TMDB + 写操作访问令牌）到库（DB 非空值优先于 env，免重启生效）。
    字段缺席=不动它；显式空串=清空该项、恢复跟随 env。返回脱敏视图。"""
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(422, "nothing to update")
    # 回显的脱敏代理值视为「不修改」（评审 R01-B6）；空串仍然=清空
    if data.get("tmdb_proxy"):
        cur, _ = config.effective_with_source("tmdb_proxy")
        if data["tmdb_proxy"] == config.mask_proxy(cur):
            data.pop("tmdb_proxy")
    for k, v in data.items():
        if v is None:
            continue
        store.set_setting(k, _validate(k, v))
    return _settings_view()
