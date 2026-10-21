"""Provider 健康状态与冷却（E 阶段补全，MULTI_LIBRARY_PLAN §9.1）。

连续失败达 `FAIL_THRESHOLD` 次后暂停该 provider `COOLDOWN_SEC`，冷却期内的
搜索链直接跳过它（不影响离线主链）；成功后失败计数清零，冷却失效自愈。
状态落 `app_settings(key=metadata_provider_state)`，重启后仍生效；只记
最近一次错误摘要（截断 200 字符），不落任何凭据。
"""
import json
import re
import time

import httpx

from .. import store
from ..log import get_logger

logger = get_logger("metadata.state")

SETTING_KEY = "metadata_provider_state"
FAIL_THRESHOLD = 3
COOLDOWN_SEC = 600
_MAX_ENTRIES = 16

# 展示名（设置页链路状态用；未知 provider 回退到 name）
LABELS = {
    "local": "本地离线索引",
    "tmdb": "TMDB",
    "wikidata": "Wikidata（无 key）",
    "tvmaze": "TVmaze（无 key，剧集）",
    "bgm": "Bangumi（无 key，中文/动漫）",
    "douban": "豆瓣（默认关）",
    "nfo": "NFO 导入",
}


def safe_error(error) -> str:
    """Keep useful failure categories without persisting upstream URLs or credentials."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}: 资料服务请求失败"
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return "资料服务请求超时"
    if isinstance(error, httpx.HTTPError):
        return "资料服务连接失败"
    value = str(error or "")
    # This also scrubs old HTTPX exception strings, including already-truncated URLs.
    value = re.sub(r"https?://[^\s\"'<>]+", "[地址已隐藏]", value, flags=re.IGNORECASE)
    value = re.sub(r"\bBearer\s+[^\s\"'<>]+", "Bearer [已隐藏]", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret)"
        r"\s*[:=]\s*[\"']?[^\s&\"',;<>]+",
        r"\1=[已隐藏]", value, flags=re.IGNORECASE)
    return value[:200]


def _load() -> dict:
    try:
        raw = store.get_setting(SETTING_KEY)
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            return {}
        changed = False
        for entry in data.values():
            if isinstance(entry, dict) and "last_error" in entry:
                sanitized = safe_error(entry["last_error"])
                if sanitized != entry["last_error"]:
                    entry["last_error"] = sanitized
                    changed = True
        if changed:
            _save(data)
        return data
    except Exception as e:
        logger.debug("load provider state failed: %s", safe_error(e))
        return {}


def _save(data: dict) -> None:
    try:
        store.set_setting(SETTING_KEY, json.dumps(data, ensure_ascii=False))
    except Exception as e:
        logger.warning("save provider state failed: %s", safe_error(e))


def available(name: str) -> bool:
    """是否可调用（冷却未到 / 无记录）。"""
    ent = _load().get(name) or {}
    try:
        return float(ent.get("cooldown_until") or 0) <= time.time()
    except (TypeError, ValueError):
        return True


def note_ok(name: str) -> None:
    """一次成功：失败计数清零、退出冷却；无历史记录时不写库（省 IO）。"""
    data = _load()
    ent = data.get(name) or {}
    if ent.get("fails") or ent.get("cooldown_until"):
        data[name] = {**ent, "fails": 0, "cooldown_until": 0,
                      "last_ok_at": int(time.time())}
        _save(data)


def note_fail(name: str, error: str = "") -> bool:
    """记一次失败；达到阈值进入冷却并返回 True（调用方据此打 warning）。"""
    now = int(time.time())
    data = _load()
    ent = data.get(name) or {}
    fails = int(ent.get("fails") or 0) + 1
    tripped = fails >= FAIL_THRESHOLD
    data[name] = {
        "fails": fails,
        "cooldown_until": now + COOLDOWN_SEC if tripped else 0,
        "last_error": safe_error(error),
        "last_fail_at": now,
        "last_ok_at": int(ent.get("last_ok_at") or 0),
    }
    if len(data) > _MAX_ENTRIES:
        oldest = sorted(data, key=lambda k: int((data[k] or {}).get("last_fail_at") or 0))
        for k in oldest[:len(data) - _MAX_ENTRIES]:
            data.pop(k, None)
    _save(data)
    return tripped


def snapshot(names=None) -> list[dict]:
    """链路状态快照（设置页展示；不触发任何网络）。"""
    now = time.time()
    data = _load()
    order = list(names) if names else list(data.keys())
    out = []
    for name in order:
        ent = data.get(name) or {}
        try:
            cd = float(ent.get("cooldown_until") or 0)
        except (TypeError, ValueError):
            cd = 0.0
        out.append({
            "name": name,
            "label": LABELS.get(name, name),
            "available": cd <= now,
            "fails": int(ent.get("fails") or 0),
            "cooldown_until": int(cd),
            "cooldown_remaining": max(0, int(cd - now)),
            "last_error": str(ent.get("last_error") or ""),
            "last_fail_at": int(ent.get("last_fail_at") or 0),
            "last_ok_at": int(ent.get("last_ok_at") or 0),
        })
    return out


def reset(name: str | None = None) -> None:
    """清除冷却（单个或全部）；设置页「重置冷却」与排障用。"""
    if name is None:
        _save({})
        return
    data = _load()
    if name in data:
        data.pop(name, None)
        _save(data)


__all__ = ['SETTING_KEY', 'FAIL_THRESHOLD', 'COOLDOWN_SEC', 'LABELS',
           'available', 'note_ok', 'note_fail', 'snapshot', 'reset', 'safe_error']
