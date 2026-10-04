"""Restoring CI signing secrets must never replace a key before identity validation."""
import base64
import hashlib
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

from scripts import github_signing as signing


KEYSTORE = b"synthetic keystore, never a real signing key"
CERTIFICATE = b"synthetic DER certificate bytes"
FINGERPRINT = hashlib.sha256(CERTIFICATE).hexdigest()


@pytest.fixture
def identity():
    return {signing.KEYSTORE_ENV: base64.b64encode(KEYSTORE).decode("ascii"),
            signing.CERTIFICATE_ENV: FINGERPRINT, "PATH": "/mock/bin"}


def valid_keytool(monkeypatch, destination=None):
    def run(command, **kwargs):
        assert command[:4] == ["keytool", "-exportcert", "-alias", "androiddebugkey"]
        temporary = Path(command[command.index("-keystore") + 1])
        assert temporary.read_bytes() == KEYSTORE
        assert stat.S_IMODE(temporary.stat().st_mode) == 0o600
        assert command[-2:] == ["-storepass", "android"]
        assert "-rfc" not in command
        assert kwargs["capture_output"] and kwargs["timeout"] == 30
        assert signing.KEYSTORE_ENV not in kwargs["env"]
        assert signing.CERTIFICATE_ENV not in kwargs["env"]
        if destination is not None:
            assert destination.read_bytes() == b"previous key"
        return SimpleNamespace(returncode=0, stdout=CERTIFICATE, stderr=b"")
    monkeypatch.setattr(signing.subprocess, "run", run)


def test_valid_identity_atomically_replaces_old_key_with_private_permissions(tmp_path, monkeypatch, identity):
    destination = tmp_path / "debug.keystore"
    destination.write_bytes(b"previous key")
    destination.chmod(0o644)
    valid_keytool(monkeypatch, destination)
    identity[signing.CERTIFICATE_ENV] = FINGERPRINT.upper()
    assert signing.restore_debug_keystore(destination, environ=identity) == FINGERPRINT
    assert destination.read_bytes() == KEYSTORE
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("change", ["missing_key", "missing_fingerprint", "bad_fingerprint",
                                    "invalid_base64", "multiline", "noncanonical", "unicode",
                                    "encoded_too_large", "decoded_too_large"])
def test_invalid_inputs_cannot_replace_key_or_start_keytool(tmp_path, monkeypatch, identity, change):
    destination = tmp_path / "debug.keystore"
    destination.write_bytes(b"previous key")
    if change == "missing_key":
        identity.pop(signing.KEYSTORE_ENV)
    elif change == "missing_fingerprint":
        identity.pop(signing.CERTIFICATE_ENV)
    elif change == "bad_fingerprint":
        identity[signing.CERTIFICATE_ENV] = "not a certificate digest"
    elif change == "invalid_base64":
        identity[signing.KEYSTORE_ENV] = "AA!!"
    elif change == "multiline":
        identity[signing.KEYSTORE_ENV] += "\n"
    elif change == "noncanonical":
        identity[signing.KEYSTORE_ENV] = "Zh=="
    elif change == "unicode":
        identity[signing.KEYSTORE_ENV] = "密钥"
    else:
        size = signing.MAX_KEYSTORE_BYTES + (10 if change == "encoded_too_large" else 1)
        identity[signing.KEYSTORE_ENV] = base64.b64encode(b"x" * size).decode("ascii")
    monkeypatch.setattr(signing.subprocess, "run", lambda *a, **kw: pytest.fail("keytool must not run"))
    with pytest.raises(signing.SigningError):
        signing.restore_debug_keystore(destination, environ=identity)
    assert destination.read_bytes() == b"previous key"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("failure", ["bad_key", "empty_certificate", "different_certificate",
                                     "missing_tool", "timeout"])
def test_keytool_failure_preserves_old_key_cleans_temporary_and_hides_diagnostics(
        tmp_path, monkeypatch, identity, failure, capsys):
    destination = tmp_path / "debug.keystore"
    destination.write_bytes(b"previous key")
    private_diagnostic = b"sensitive validation detail must not escape"

    def run(*args, **kwargs):
        if failure == "missing_tool":
            raise FileNotFoundError(private_diagnostic.decode())
        if failure == "timeout":
            raise subprocess.TimeoutExpired("keytool", 30, output=private_diagnostic)
        return SimpleNamespace(returncode=1 if failure == "bad_key" else 0,
                               stdout=b"" if failure == "empty_certificate" else private_diagnostic,
                               stderr=private_diagnostic)

    monkeypatch.setattr(signing.subprocess, "run", run)
    with pytest.raises(signing.SigningError) as error:
        signing.restore_debug_keystore(destination, environ=identity)
    assert private_diagnostic.decode() not in str(error.value)
    assert destination.read_bytes() == b"previous key"
    assert list(tmp_path.iterdir()) == [destination]
    assert capsys.readouterr() == ("", "")


def test_failed_atomic_replace_leaves_original_and_cleans_temporary(tmp_path, monkeypatch, identity):
    destination = tmp_path / "debug.keystore"
    destination.write_bytes(b"previous key")
    valid_keytool(monkeypatch, destination)

    def denied(*args):
        raise PermissionError("cannot replace")

    monkeypatch.setattr(signing.os, "replace", denied)
    with pytest.raises(signing.SigningError, match="write or replace"):
        signing.restore_debug_keystore(destination, environ=identity)
    assert destination.read_bytes() == b"previous key"
    assert list(tmp_path.iterdir()) == [destination]


def test_output_symlink_is_replaced_without_modifying_its_target(tmp_path, monkeypatch, identity):
    original = tmp_path / "existing.keystore"
    original.write_bytes(b"previous key")
    destination = tmp_path / "debug.keystore"
    destination.symlink_to(original)
    valid_keytool(monkeypatch, destination)
    signing.restore_debug_keystore(destination, environ=identity)
    assert original.read_bytes() == b"previous key"
    assert not destination.is_symlink()
    assert destination.read_bytes() == KEYSTORE


def test_main_uses_explicit_output_and_only_prints_public_fingerprint(tmp_path, monkeypatch, identity, capsys):
    destination = tmp_path / "nested" / "debug.keystore"
    for key, value in identity.items():
        monkeypatch.setenv(key, value)
    valid_keytool(monkeypatch)
    assert signing.main(["--output", str(destination)]) == 0
    output = capsys.readouterr()
    assert FINGERPRINT in output.out
    assert identity[signing.KEYSTORE_ENV] not in output.out + output.err
    assert destination.read_bytes() == KEYSTORE
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_main_default_destination_is_under_home(tmp_path, monkeypatch, identity):
    monkeypatch.setattr(signing.Path, "home", lambda: tmp_path)
    for key, value in identity.items():
        monkeypatch.setenv(key, value)
    valid_keytool(monkeypatch)
    assert signing.main([]) == 0
    assert (tmp_path / ".android/debug.keystore").read_bytes() == KEYSTORE


def test_explicit_runner_destination_ignores_android_and_xdg_home_defaults(tmp_path, monkeypatch, identity):
    home = tmp_path / "runner-home"
    xdg = home / ".config"
    android_home = tmp_path / "android-preferences"
    destination = tmp_path / "runner-temp/jzmedia-signing/debug.keystore"
    monkeypatch.setattr(signing.Path, "home", lambda: home)
    for key, value in {**identity, "XDG_CONFIG_HOME": str(xdg),
                       "ANDROID_USER_HOME": str(android_home),
                       "JZMEDIA_ANDROID_DEBUG_KEYSTORE": str(destination)}.items():
        monkeypatch.setenv(key, value)
    valid_keytool(monkeypatch)
    assert signing.main(["--output", str(destination)]) == 0
    assert destination.read_bytes() == KEYSTORE
    assert not (home / ".android/debug.keystore").exists()
    assert not (xdg / ".android/debug.keystore").exists()
    assert not (android_home / "debug.keystore").exists()


def test_help_needs_no_secret_or_keytool(monkeypatch, capsys):
    monkeypatch.delenv(signing.KEYSTORE_ENV, raising=False)
    monkeypatch.delenv(signing.CERTIFICATE_ENV, raising=False)
    monkeypatch.setattr(signing, "restore_debug_keystore", lambda *a, **kw: pytest.fail("must not restore"))
    with pytest.raises(SystemExit) as error:
        signing.main(["--help"])
    assert error.value.code == 0
    assert "--output" in capsys.readouterr().out


def test_main_missing_secret_fails_without_creating_output(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(signing.KEYSTORE_ENV, raising=False)
    destination = tmp_path / "nested" / "debug.keystore"
    with pytest.raises(SystemExit) as error:
        signing.main(["--output", str(destination)])
    assert error.value.code == 2
    assert not destination.parent.exists()
    assert signing.KEYSTORE_ENV in capsys.readouterr().err
