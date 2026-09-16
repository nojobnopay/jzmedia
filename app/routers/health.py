import os
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config, store
from ..config import settings

router = APIRouter(prefix="/api")


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
    media_ok = os.path.isdir(settings.media_root) and os.access(settings.media_root, os.R_OK)
    return {"status": "ok" if (dbh.get("ok") and media_ok) else "degraded",
            "db": dbh,
            "media": {"root": settings.media_root, "ok": bool(media_ok)},
            "ffmpeg": bool(bins.get("ffmpeg")), "ffprobe": bool(bins.get("ffprobe")),
            "transcoder": tw,
            "build": _build_commit()}


def _build_commit() -> str:
    """构建版本脚标：git 短 hash，无仓库（如镜像内）则 unknown。只读，不抛错。"""
    try:
        import subprocess as _sp
        out = _sp.run(["git", "rev-parse", "--short", "HEAD"],
                      capture_output=True, timeout=5, check=False)
        s = (out.stdout or b"").decode("utf-8", errors="replace").strip()
        return s[:12] if out.returncode == 0 and s else "unknown"
    except Exception:
        return "unknown"


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
        "tmdb_proxy": proxy,
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
    for k, v in data.items():
        if v is None:
            continue
        store.set_setting(k, _validate(k, v))
    return _settings_view()
