"""Validated, server-only AI configuration and persistent daily usage accounting."""

from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import os
import re
import time
from urllib.parse import urlsplit

from ..config import mask_secret
from ..store import _base


DEFAULTS = {
    "enabled": False,
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "api_key": "",
    "timeout_seconds": 12,
    "daily_limit": 100,
}

OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"


@dataclass(frozen=True)
class AiConfig:
    enabled: bool = False
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    api_key: str = field(default="", repr=False)
    timeout_seconds: int = 12
    daily_limit: int = 100


def validate_base_url(value: str) -> str:
    """The administrator chooses the endpoint; model output never participates."""
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (parsed.scheme in ("https", "http") and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment and "?" not in value and "#" not in value
                 and port != 0 and len(value) <= 1024
                 and not re.search(r"[\s\\\x00-\x1f\x7f]", value)
                 and "@" not in parsed.netloc)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("API 地址须为 HTTP/HTTPS 基础地址，不能含账号、密码、查询参数或片段")
    return value


def _validate(values: dict) -> AiConfig:
    result = dict(values)
    if type(result["enabled"]) is not bool:
        raise ValueError("启用状态须为布尔值")
    if result["provider"] not in ("deepseek", "opencode_go", "compatible"):
        raise ValueError("请选择 DeepSeek、OpenCode Go 或兼容接口")
    for name in ("base_url", "model", "api_key"):
        if not isinstance(result[name], str):
            raise ValueError("地址、模型和密钥须为文本")
        result[name] = result[name].strip()
    result["base_url"] = validate_base_url(result["base_url"])
    if result["provider"] == "deepseek" and result["base_url"] not in (
        "https://api.deepseek.com", "https://api.deepseek.com/v1",
    ):
        raise ValueError("自定义 API 地址请使用兼容接口")
    if result["provider"] == "opencode_go" and result["base_url"] != OPENCODE_GO_BASE_URL:
        raise ValueError("OpenCode Go 请使用官方 Go 基础地址；自定义地址请使用兼容接口")
    if not result["model"] or len(result["model"]) > 128 or re.search(
        r"[\x00-\x1f\x7f]", result["model"],
    ):
        raise ValueError("模型名称须为 1 至 128 字符的单行文本")
    if len(result["api_key"]) > 4096 or re.search(r"[\s\x00-\x1f\x7f]", result["api_key"]):
        raise ValueError("API Key 格式无效")
    for name, minimum, maximum, label in (
        ("timeout_seconds", 2, 60, "超时秒数"),
        ("daily_limit", 1, 10000, "每日调用上限"),
    ):
        value = result[name]
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{label}须为 {minimum} 至 {maximum} 的整数")
    return AiConfig(**result)


def _setting_rows(conn) -> dict:
    conn.execute("CREATE TABLE IF NOT EXISTS app_settings ("
                 "key TEXT PRIMARY KEY, value TEXT DEFAULT '', updated_at INTEGER DEFAULT 0)")
    return {row["key"]: row["value"] for row in conn.execute(
        "SELECT key, value FROM app_settings WHERE key LIKE 'ai_%'"
    )}


def _resolve(rows: dict) -> tuple[AiConfig, dict]:
    values, sources = {}, {}
    for name, default in DEFAULTS.items():
        stored = rows.get("ai_" + name, "")
        env = os.getenv("AI_" + name.upper(), "").strip()
        raw = stored if stored else env
        sources[name] = "db" if stored else ("env" if env else ("default" if default != "" else "unset"))
        if raw == "":
            values[name] = default
        elif isinstance(default, bool):
            if raw.lower() not in ("true", "false", "1", "0"):
                raise ValueError("AI_ENABLED 须为 true 或 false")
            values[name] = raw.lower() in ("true", "1")
        elif isinstance(default, int):
            try:
                values[name] = int(raw)
            except ValueError:
                raise ValueError("AI 超时与限额设置须为整数") from None
        else:
            values[name] = raw
    return _validate(values), sources


def effective() -> AiConfig:
    with _base._lock, closing(_base._conn()) as conn, conn:
        return _resolve(_setting_rows(conn))[0]


def update(changes: dict) -> dict:
    """Validate the entire snapshot before committing any field. Empty keys keep secrets."""
    changes = dict(changes)
    if isinstance(changes.get("api_key"), str) and not changes["api_key"].strip():
        changes.pop("api_key")
    if set(changes) - set(DEFAULTS) - {"clear_api_key"}:
        raise ValueError("包含不支持的智能辅助设置")
    if "clear_api_key" in changes and type(changes["clear_api_key"]) is not bool:
        raise ValueError("清除密钥状态须为布尔值")
    if changes.get("clear_api_key") and changes.get("api_key"):
        raise ValueError("不能同时填写和清除 API Key")
    with _base._lock, closing(_base._conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = _setting_rows(conn)
        # Start from raw settings so an invalid environment value can be repaired.
        for name, value in changes.items():
            if name == "clear_api_key":
                continue
            if name == "api_key" and value == "":
                continue
            if name == "enabled":
                if type(value) is not bool:
                    raise ValueError("启用状态须为布尔值")
                value = "true" if value else "false"
            elif name in ("timeout_seconds", "daily_limit"):
                if type(value) is not int:
                    raise ValueError("超时与每日限额须为整数")
                value = str(value)
            elif not isinstance(value, str):
                raise ValueError("地址、模型和密钥须为文本")
            elif name != "api_key" and not value.strip():
                raise ValueError("地址、模型和服务商不能为空")
            if name == "api_key" and value.startswith("****"):
                raise ValueError("请输入新的 API Key；保留已保存的密钥时请留空")
            rows["ai_" + name] = value.strip()
        if changes.get("clear_api_key"):
            rows["ai_api_key"] = ""
        current, _ = _resolve(rows)
        # No mutation until every value is valid. Normalized endpoint/model are saved.
        for name in changes.keys() & DEFAULTS.keys():
            if name == "api_key" and changes[name] == "":
                continue
            value = getattr(current, name)
            encoded = ("true" if value else "false") if isinstance(value, bool) else str(value)
            conn.execute("INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                         ("ai_" + name, encoded, int(time.time())))
        if changes.get("clear_api_key"):
            conn.execute("DELETE FROM app_settings WHERE key='ai_api_key'")
    return public_settings()


_USAGE_DDL = """CREATE TABLE IF NOT EXISTS ai_usage (
    date TEXT PRIMARY KEY, requests INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0
)"""


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def usage() -> dict:
    today = _today()
    with _base._lock, closing(_base._conn()) as conn, conn:
        conn.execute(_USAGE_DDL)
        row = conn.execute("SELECT * FROM ai_usage WHERE date=?", (today,)).fetchone()
    return dict(row) if row else {"date": today, "requests": 0, "input_tokens": 0, "output_tokens": 0}


def reserve_request(limit: int) -> str | None:
    """Count attempts before network I/O; SQLite serializes reservations across workers."""
    today = _today()
    with _base._lock, closing(_base._conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(_USAGE_DDL)
        conn.execute("INSERT OR IGNORE INTO ai_usage(date) VALUES(?)", (today,))
        result = conn.execute("UPDATE ai_usage SET requests=requests+1 WHERE date=? AND requests < ?",
                              (today, limit))
        return today if result.rowcount else None


def record_tokens(day: str, input_tokens: int, output_tokens: int) -> None:
    with _base._lock, closing(_base._conn()) as conn, conn:
        conn.execute("UPDATE ai_usage SET input_tokens=input_tokens+?, output_tokens=output_tokens+? "
                     "WHERE date=?", (input_tokens, output_tokens, day))


def public_settings() -> dict:
    with _base._lock, closing(_base._conn()) as conn, conn:
        current, sources = _resolve(_setting_rows(conn))
    result = asdict(current)
    result.pop("api_key")
    result.update(api_key_set=bool(current.api_key), api_key_masked=mask_secret(current.api_key),
                  api_key_source=sources["api_key"], usage=usage())
    return result
