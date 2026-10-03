#!/usr/bin/env python3
"""Build the web client when any shipped input changes, including deletion."""

import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def source_digest(root: Path) -> str:
    frontend = root / "frontend"
    files = [frontend / "index.html", frontend / "package.json", frontend / "package-lock.json"]
    files.extend(frontend.glob("vite.config.*"))
    for directory in ("src", "public"):
        files.extend(path for path in (frontend / directory).rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in sorted(files):
        if path.is_file():
            digest.update(path.relative_to(frontend).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def main():
    frontend = ROOT / "frontend"
    stamp = frontend / "dist/.build-inputs.sha256"
    before = source_digest(ROOT)
    if (frontend / "dist/index.html").is_file() and stamp.is_file() and stamp.read_text() == before:
        print("[start] frontend dist is fresh, skip build.", flush=True)
        return
    lock = hashlib.sha256((frontend / "package-lock.json").read_bytes()).hexdigest()
    dependencies = frontend / "node_modules/.jzmedia-lock.sha256"
    if (not (frontend / "node_modules/vite/package.json").is_file()
            or not dependencies.is_file() or dependencies.read_text() != lock):
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=frontend, check=True)
        dependencies.write_text(lock)
    print("[start] frontend changed, rebuilding...", flush=True)
    subprocess.run(["npm", "run", "build"], cwd=frontend, check=True)
    # A concurrent edit must not mark a partial build as fresh.
    if source_digest(ROOT) == before:
        stamp.write_text(before)


if __name__ == "__main__":
    main()
