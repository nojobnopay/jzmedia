"""库凭据加密（D4）：Fernet 轮转/空值/错误密钥。"""
import os
import stat

import pytest

from app import secrets


@pytest.fixture()
def keyed_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets.settings, "data_dir", str(tmp_path))
    secrets.reset_cache()
    yield tmp_path
    secrets.reset_cache()


def test_encrypt_decrypt_roundtrip(keyed_dir):
    tok = secrets.encrypt_str("p@ssw0rd-中文")
    assert tok and tok != "p@ssw0rd-中文"
    assert secrets.is_encrypted(tok)
    assert secrets.decrypt_str(tok) == "p@ssw0rd-中文"
    assert secrets.encrypt_str("") == ""
    assert secrets.decrypt_str("") == ""


def test_key_file_created_0600(keyed_dir):
    secrets.encrypt_str("x")
    p = keyed_dir / "secret.key"
    assert p.is_file()
    mode = stat.S_IMODE(os.stat(p).st_mode)
    assert mode == 0o600


def test_wrong_key_raises(keyed_dir):
    tok = secrets.encrypt_str("secret")
    # 模拟换机丢 key：换掉密钥文件后旧密文不可解
    secrets.reset_cache()
    (keyed_dir / "secret.key").write_bytes(
        b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")
    with pytest.raises(secrets.SecretError):
        secrets.decrypt_str(tok)
