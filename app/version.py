"""Read the release identity without importing application state or requiring Git."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parent.parent
_NAME_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_COMMIT_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")


@dataclass(frozen=True)
class Version:
    name: str
    code: int

    def __post_init__(self):
        if not isinstance(self.name, str) or not _NAME_RE.fullmatch(self.name):
            raise ValueError("versionName must be a release version X.Y.Z without leading zeroes")
        if type(self.code) is not int or not 1 <= self.code <= 2_100_000_000:
            raise ValueError("versionCode must be an integer from 1 to 2100000000")

    @property
    def parts(self) -> tuple[int, int, int]:
        return tuple(int(part) for part in self.name.split("."))


def read_version(root: Path | str = ROOT) -> Version:
    """The checked-in properties file also ships in images and source archives."""
    path = Path(root) / "version.properties"
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or key not in {"versionName", "versionCode"} or key in values:
            raise ValueError(f"{path}: malformed, unknown, or duplicate version property")
        values[key] = value
    if set(values) != {"versionName", "versionCode"}:
        raise ValueError(f"{path}: versionName and versionCode are required")
    code = values["versionCode"]
    if not re.fullmatch(r"[1-9][0-9]*", code):
        raise ValueError(f"{path}: versionCode must be a positive integer without leading zeroes")
    return Version(values["versionName"], int(code))


def build_commit(root: Path | str = ROOT) -> str:
    """Prefer an image's full source revision; otherwise inspect this checkout."""
    injected = os.environ.get("JZMEDIA_BUILD_COMMIT", "").strip()
    if _COMMIT_RE.fullmatch(injected):
        return injected.lower()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root,
            capture_output=True, text=True, timeout=5, check=False,
        )
        commit = out.stdout.strip()
        if out.returncode == 0 and _COMMIT_RE.fullmatch(commit):
            return commit.lower()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


VERSION = read_version()
