#!/usr/bin/env python3
"""Restore the existing Android Debug signing identity in a GitHub Actions runner."""
from __future__ import annotations

import argparse
import base64
import binascii
from collections.abc import Mapping
import hashlib
import hmac
import os
from pathlib import Path
import re
import subprocess
import tempfile


KEYSTORE_ENV = "JZMEDIA_ANDROID_DEBUG_KEYSTORE_BASE64"
CERTIFICATE_ENV = "JZMEDIA_ANDROID_DEBUG_CERT_SHA256"
MAX_KEYSTORE_BYTES = 1024 * 1024


class SigningError(ValueError):
    """The configured signing identity could not be restored safely."""


def _identity(environ: Mapping[str, str]) -> tuple[bytes, str]:
    encoded = environ.get(KEYSTORE_ENV, "")
    expected = environ.get(CERTIFICATE_ENV, "")
    if not encoded:
        raise SigningError(f"Missing {KEYSTORE_ENV}")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise SigningError(f"{CERTIFICATE_ENV} must contain a 64-character SHA-256 hex digest")
    if len(encoded) > 4 * ((MAX_KEYSTORE_BYTES + 2) // 3):
        raise SigningError("Encoded Debug keystore exceeds the 1 MiB limit")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise SigningError(f"{KEYSTORE_ENV} must contain strict, single-line base64") from None
    if not content or len(content) > MAX_KEYSTORE_BYTES:
        raise SigningError("Decoded Debug keystore must contain between 1 byte and 1 MiB")
    if base64.b64encode(content).decode("ascii") != encoded:
        raise SigningError(f"{KEYSTORE_ENV} must contain canonical base64")
    return content, expected.lower()


def restore_debug_keystore(output: Path | str, *, environ: Mapping[str, str] | None = None,
                           keytool: str = "keytool") -> str:
    """Validate a temporary keystore before atomically replacing the destination.

    The source environment must contain the existing Debug key and its independently
    configured certificate fingerprint. No key or keytool diagnostics are logged.
    """
    source = os.environ if environ is None else environ
    content, expected = _identity(source)
    destination = Path(output).expanduser()
    temporary = None
    try:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp",
                                    dir=destination.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Do not forward the base64 key to the validation subprocess environment.
        child_env = {key: value for key, value in source.items()
                     if key not in (KEYSTORE_ENV, CERTIFICATE_ENV)}
        try:
            result = subprocess.run(
                [keytool, "-exportcert", "-alias", "androiddebugkey",
                 "-keystore", str(temporary), "-storepass", "android"],
                capture_output=True, timeout=30, check=False, env=child_env)
        except FileNotFoundError:
            raise SigningError("keytool is required to validate the Debug keystore") from None
        except subprocess.TimeoutExpired:
            raise SigningError("Debug keystore certificate validation timed out") from None
        if result.returncode or not result.stdout:
            raise SigningError("keytool could not export the Debug signing certificate")
        # Without -rfc, keytool exports DER bytes directly to stdout.
        actual = hashlib.sha256(result.stdout).hexdigest()
        if not hmac.compare_digest(actual, expected):
            raise SigningError("Debug signing certificate SHA-256 does not match the configured fingerprint")
        os.replace(temporary, destination)
        temporary = None
        return actual
    except OSError:
        raise SigningError("Could not write or replace the Debug keystore") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path.home() / ".android/debug.keystore",
                        help="Keystore destination (default: ~/.android/debug.keystore)")
    args = parser.parse_args(argv)
    try:
        fingerprint = restore_debug_keystore(args.output)
    except SigningError as error:
        parser.error(str(error))
    print(f"Android Debug signing identity verified: SHA-256 {fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
