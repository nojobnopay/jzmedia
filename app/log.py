"""统一日志配置（评审 P1-10）。

- `setup_logging()` 在 app.main 导入时调用一次；级别由 env `LOG_LEVEL`（默认 INFO）控制。
- 业务 logger 统一走 `get_logger(__name__ 后缀)`，挂在 `jzmedia.*` 命名空间下。
- 约定：失败路径要留痕（warning/debug + 关键上下文），不要再写 `except Exception: pass`；
  历史静默点按批次逐步改造，改造时优先补“用户可感知但被吞掉”的路径。
"""
import logging
import os

_configured = False


def setup_logging(force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return
    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper() or "INFO"
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if force or not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # pytest/宿主已有 handler 时 basicConfig 不生效，这里兜底保证级别
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # smbprotocol 对每次线上操作打 INFO（Create/Close/Read...），直读库会刷屏；
    # 默认压到 WARNING，排障时用 env SMB_LOG_LEVEL=DEBUG/INFO 单独放开。
    smb_level_name = (os.getenv("SMB_LOG_LEVEL") or "WARNING").strip().upper() or "WARNING"
    smb_level = getattr(logging, smb_level_name, logging.WARNING)
    for noisy in ("smbprotocol", "smbclient", "spnego"):
        logging.getLogger(noisy).setLevel(smb_level)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"jzmedia.{name}")
