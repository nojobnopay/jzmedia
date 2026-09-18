"""库凭据加密（MULTI_LIBRARY_PLAN D4）：Fernet + data_dir/secret.key（0600）。

- 密钥首次使用时自动生成；换机携带 data/secret.key 即可解密既有凭据，丢 key 需重输。
- 只用于远程库凭据（SMB/NFS 密码）；API 只写不读，日志/错误一律脱敏。
- 未安装 cryptography 时给出明确错误，不影响其余功能启动（只在写入/挂载时触发）。
"""
import os

from .config import settings
from .log import get_logger

logger = get_logger("secrets")

_KEY_NAME = "secret.key"
_key_cache: bytes | None = None


class SecretError(RuntimeError):
    """加解密失败（密钥丢失/密文损坏/依赖缺失）。"""


def key_path() -> str:
    return os.path.join(settings.data_dir, _KEY_NAME)


def _load_or_create_key() -> bytes:
    global _key_cache
    if _key_cache:
        return _key_cache
    path = key_path()
    try:
        with open(path, "rb") as fh:
            key = fh.read().strip()
        if key:
            _key_cache = key
            return key
    except FileNotFoundError:
        pass
    except OSError as e:
        raise SecretError(f"读取密钥失败 {path}: {e}") from e
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:  # pragma: no cover - 依赖缺失时明确报错
        raise SecretError("缺少 cryptography 依赖，无法加密库凭据") from e
    key = Fernet.generate_key()
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        logger.info("已生成凭据密钥 %s（0600）", path)
    except FileExistsError:
        # 并发首启：另一个进程已生成，读它的
        try:
            with open(path, "rb") as fh:
                key = fh.read().strip()
        except OSError as e:
            raise SecretError(f"读取密钥失败 {path}: {e}") from e
    except OSError as e:
        raise SecretError(f"写入密钥失败 {path}: {e}") from e
    if not key:
        raise SecretError(f"密钥为空: {path}")
    _key_cache = key
    return key


def encrypt_str(value: str) -> str:
    """明文 → Fernet 密文（base64 字符串）；空串原样返回。"""
    s = value or ""
    if not s:
        return ""
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:  # pragma: no cover
        raise SecretError("缺少 cryptography 依赖，无法加密库凭据") from e
    return Fernet(_load_or_create_key()).encrypt(s.encode("utf-8")).decode("ascii")


def decrypt_str(token: str) -> str:
    """Fernet 密文 → 明文；空串原样返回。密钥错误/密文损坏抛 SecretError。"""
    s = token or ""
    if not s:
        return ""
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as e:  # pragma: no cover
        raise SecretError("缺少 cryptography 依赖，无法解密库凭据") from e
    try:
        return Fernet(_load_or_create_key()).decrypt(s.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        raise SecretError("凭据解密失败（secret.key 不匹配或密文损坏）") from e


def is_encrypted(token: str) -> bool:
    """粗判是否已是 Fernet 密文（gAAAAA 前缀），供迁移/幂等写入用。"""
    return (token or "").startswith("gAAAAA")


def reset_cache() -> None:
    """测试/密钥轮换用：清进程内缓存。"""
    global _key_cache
    _key_cache = None
