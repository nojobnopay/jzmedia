"""Release version validation and synchronization; no third-party dependencies."""

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.version import Version, read_version  # noqa: E402


def _package_files(root: Path):
    for project in ("frontend", "docs"):
        for name in ("package.json", "package-lock.json"):
            yield root / project / name


def _read_package(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a package object")
    if path.name == "package-lock.json":
        packages = data.get("packages")
        if not isinstance(packages, dict) or not isinstance(packages.get(""), dict):
            raise ValueError(f"{path}: missing root packages entry")
    return data


def check_versions(root: Path | str = ROOT) -> Version:
    """Raise on generated npm metadata drift before any build is started."""
    root = Path(root)
    version = read_version(root)
    errors = []
    for path in _package_files(root):
        data = _read_package(path)
        if data.get("version") != version.name:
            errors.append(f"{path.relative_to(root)} version={data.get('version')!r}")
        if path.name == "package-lock.json" and data["packages"][""].get("version") != version.name:
            errors.append(f"{path.relative_to(root)} packages[''].version={data['packages'][''].get('version')!r}")
    if errors:
        raise ValueError(f"Versions differ from version.properties ({version.name}): " + "; ".join(errors))
    return version


def set_version(root: Path | str, name: str, code: int) -> Version:
    """Advance both release identifiers, or repair metadata for the same release.

    All files and the requested transition are validated before the first write.
    Dependencies and their lockfile versions are left unchanged.
    """
    root = Path(root)
    current, target = read_version(root), Version(name, code)
    if target != current and (target.parts <= current.parts or target.code <= current.code):
        raise ValueError("Both versionName and versionCode must increase for a new release")
    outputs = {}
    for path in _package_files(root):
        data = _read_package(path)
        data["version"] = target.name
        if path.name == "package-lock.json":
            data["packages"][""]["version"] = target.name
        outputs[path] = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path = root / "version.properties"
    lines = path.read_text(encoding="utf-8").splitlines()
    values = {"versionName": target.name, "versionCode": str(target.code)}
    outputs[path] = "\n".join(
        f"{key}={values[key]}" if (key := line.partition("=")[0].strip()) in values else line
        for line in lines
    ) + "\n"
    for path, contents in outputs.items():
        path.write_text(contents, encoding="utf-8")
    return target
