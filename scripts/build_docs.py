#!/usr/bin/env python3
"""宿主启动时按输入内容构建帮助站；包含删除检测，不依赖文件修改时间。"""

import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SKIP = {"node_modules", "private", "roadmap", "dist", "cache", ".temp", "generated",
        ".artifacts", "__pycache__"}


def source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / "docs").rglob("*")):
        relative = path.relative_to(root / "docs")
        if not path.is_file() or any(part in SKIP for part in relative.parts):
            continue
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main():
    docs = ROOT / "docs"
    stamp = docs / ".vitepress/dist/.build-inputs.sha256"
    before = source_digest(ROOT)
    if (docs / ".vitepress/dist/index.html").is_file() and stamp.is_file() and stamp.read_text() == before:
        print("[start] help docs are fresh, skip build.", flush=True)
        return
    lock = hashlib.sha256((docs / "package-lock.json").read_bytes()).hexdigest()
    dependencies = docs / "node_modules/.jzmedia-lock.sha256"
    if (not (docs / "node_modules/vitepress/package.json").is_file() or
            not dependencies.is_file() or dependencies.read_text() != lock):
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=docs, check=True)
        dependencies.write_text(lock)
    print("[start] help docs changed, rebuilding...", flush=True)
    subprocess.run(["npm", "run", "build"], cwd=docs, check=True)
    stamp.write_text(before)


if __name__ == "__main__":
    main()
