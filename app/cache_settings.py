"""Live cache limits. One settings row keeps the three values atomic."""
import json
import math
import os

from pydantic import BaseModel, Field, ValidationError

from . import store

GIB = 1024 ** 3


class CacheLimits(BaseModel):
    model_config = {"extra": "forbid", "allow_inf_nan": False}

    data_warning_gb: float = Field(ge=1, le=102400, multiple_of=0.1)
    transcode_cache_gb: float = Field(ge=0, le=102400, multiple_of=0.1)
    preview_cache_gb: float = Field(ge=0.1, le=102400, multiple_of=0.1)


def reserved_tenths(base_bytes: int) -> int:
    """Reserve base data plus 10% or 1 GiB, rounded up to 0.1 GiB.

    Keeping the calculation in integers also accounts for growth margins smaller
    than one byte, without rounding down at an exact tenth boundary.
    """
    numerator = 10 * base_bytes + max(base_bytes, 10 * GIB)
    return (numerator + GIB - 1) // GIB


def validate_budget(body: CacheLimits, base_bytes: int) -> None:
    """Validate new allocations only; legacy saved limits remain readable."""
    if body.transcode_cache_gb == 0:
        raise ValueError("播放缓存当前未限制容量，请设置大于 0 的播放缓存额度后保存总预算")
    reserved = reserved_tenths(base_bytes)
    allocated = (round(body.transcode_cache_gb * 10)
                 + round(body.preview_cache_gb * 10) + reserved)
    excess = allocated - round(body.data_warning_gb * 10)
    if excess > 0:
        raise ValueError(
            f"缓存分配超出总预算 {excess / 10:.1f} GB（基础数据需预留 {reserved / 10:.1f} GB）。"
            "请增大总预算或调低播放缓存、进度缩略图额度")


def _env_gb(name: str, minimum: float) -> float:
    try:
        value = float(os.getenv(name, "10") or 0)
        if math.isfinite(value):
            return max(minimum, value)
    except (TypeError, ValueError):
        pass
    return 10.0


def limits() -> dict[str, float]:
    saved = store.get_setting("cache_limits")
    if saved:
        try:
            return CacheLimits.model_validate_json(saved).model_dump()
        except ValidationError:
            pass
    return {"data_warning_gb": 20.0,
            "transcode_cache_gb": _env_gb("TRANSCODE_CACHE_GB", 0),
            "preview_cache_gb": _env_gb("PREVIEW_CACHE_GB", 0.1)}


def save(body: CacheLimits) -> dict[str, float]:
    values = body.model_dump()
    store.set_setting("cache_limits", json.dumps(values))
    return values


def warning_bytes() -> int:
    return int(limits()["data_warning_gb"] * GIB)


def transcode_bytes() -> int:
    return int(limits()["transcode_cache_gb"] * GIB)


def preview_bytes() -> int:
    return int(limits()["preview_cache_gb"] * GIB)
