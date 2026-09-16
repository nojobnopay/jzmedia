import os
from pydantic import BaseModel

from .log import get_logger

logger = get_logger("config")


class Settings(BaseModel):
    app_port: int = int(os.getenv("APP_PORT", "8080"))
    env: str = os.getenv("ENV", "dev")
    media_root: str = os.getenv("MEDIA_ROOT", "./sample_media")
    data_dir: str = os.getenv("DATA_DIR", "./data")
    tmdb_api_key: str = os.getenv("TMDB_API_KEY", "")
    tmdb_read_token: str = os.getenv("TMDB_READ_TOKEN", "")
    tmdb_proxy: str = os.getenv("TMDB_PROXY", "")
    tmdb_language: str = os.getenv("TMDB_LANGUAGE", "zh-CN")
    tmdb_image_base: str = os.getenv("TMDB_IMAGE_BASE", "https://image.tmdb.org")


settings = Settings()

# 设置页可写键 → (Settings 属性名, 原始环境变量名, 代码默认值)
# DB 非空值优先于环境变量；缺 key/空串一律回落 env（.env 只做首次启动兜底）。
TMDB_SETTING_MAP = {
    "tmdb_read_token": ("tmdb_read_token", "TMDB_READ_TOKEN", ""),
    "tmdb_api_key": ("tmdb_api_key", "TMDB_API_KEY", ""),
    "tmdb_proxy": ("tmdb_proxy", "TMDB_PROXY", ""),
    "tmdb_language": ("tmdb_language", "TMDB_LANGUAGE", "zh-CN"),
    "tmdb_image_base": ("tmdb_image_base", "TMDB_IMAGE_BASE", "https://image.tmdb.org"),
}


def _db_value(key: str) -> str:
    """读库里的用户设置。延迟 import store，避免 config↔store 循环引用；异常回空。"""
    try:
        from . import store
        v = store.get_setting(key)
        return (v or "").strip()
    except Exception as e:
        logger.warning("read setting %s from db failed, fallback to env: %s", key, e)
        return ""


def effective_with_source(key: str) -> tuple[str, str]:
    """有效值 + 来源(db|env|default|unset)。DB 非空优先，否则看原始 env 是否显式设置过。”
    """
    spec = TMDB_SETTING_MAP.get(key)
    if not spec:
        raise ValueError(f"unknown setting: {key}")
    attr, env_name, default = spec
    dbv = _db_value(key)
    if dbv:
        return dbv, "db"
    raw = os.environ.get(env_name)
    if raw is not None and raw != "":
        return getattr(settings, attr, "") or "", "env"
    val = getattr(settings, attr, "") or ""
    if val:
        # 有值但 env 未显式设置 → 代码默认值（如 language/image_base）
        return val, "default"
    return "", "unset"


def effective(key: str) -> str:
    """有效值（DB 优先，env 兜底）。每次调用实时读取，设置页保存后免重启生效。"""
    return effective_with_source(key)[0]


def effective_tmdb_read_token() -> str:
    return effective("tmdb_read_token")


def effective_tmdb_api_key() -> str:
    return effective("tmdb_api_key")


def effective_tmdb_proxy() -> str:
    return effective("tmdb_proxy")


def effective_tmdb_language() -> str:
    return effective("tmdb_language")


def effective_tmdb_image_base() -> str:
    return effective("tmdb_image_base")


def mask_secret(v: str) -> str:
    """脱敏：空→空；长度>8 显示 ****+后4位，否则统一 ****（绝不返明文）。"""
    s = (v or "").strip()
    if not s:
        return ""
    if len(s) > 8:
        return "****" + s[-4:]
    return "****"
