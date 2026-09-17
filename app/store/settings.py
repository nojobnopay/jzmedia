"""store.settings（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。"""
import time
from ._base import APP_SETTING_KEYS, _conn, _lock
__all__ = ['get_setting', 'set_setting']

def get_setting(key: str) -> str:
    """读单项应用配置（设置页写入的值）。缺 key/空串一律返回 ''，调用方回落 env。"""
    with _lock, _conn() as c:
        try:
            c.execute("CREATE TABLE IF NOT EXISTS app_settings ("
                      "key TEXT PRIMARY KEY, value TEXT DEFAULT '',"
                      " updated_at INTEGER DEFAULT 0)")
            row = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        except Exception:
            return ""
        if not row:
            return ""
        try:
            return row["value"] or ""
        except Exception:
            return ""



def set_setting(key: str, value: str) -> str:
    """写单项应用配置。空串表示清空（恢复跟随 env）。返回落库后的 strip 值。"""
    if key not in APP_SETTING_KEYS:
        raise ValueError(f"unknown setting: {key}")
    v = (value or "").strip()
    now = int(time.time())
    with _lock, _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS app_settings ("
                  "key TEXT PRIMARY KEY, value TEXT DEFAULT '',"
                  " updated_at INTEGER DEFAULT 0)")
        c.execute("INSERT INTO app_settings(key, value, updated_at) VALUES(?, ?, ?)"
                  " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                  " updated_at=excluded.updated_at",
                  (key, v, now))
    return v

