#!/usr/bin/env python3
"""检查 Markdown 文档中的本地链接、图片引用与章节锚点。

用法（仓库根目录）::
    python3 scripts/check_docs_links.py
    python3 scripts/check_docs_links.py --root docs

只检查仓库内相对路径与 `#锚点`；http(s) 外链、模板变量不检查。
存在性：链接目标文件必须存在；锚点必须与目标文件标题生成的
GitHub 风格 slug 匹配（小写、空格→`-`，去标点，中文保留）。
退出码：有错误时为 1，并逐行输出。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def slugify(heading: str) -> str:
    heading = re.sub(r"`([^`]*)`", r"\1", heading).strip().lower()
    heading = heading.replace(" ", "-")
    heading = re.sub(r"[^\w\u4e00-\u9fff\-]", "", heading)
    return heading


def headings_of(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    out = set()
    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            out.add(slugify(m.group(2)))
    return out


def iter_links(text: str) -> list[str]:
    found = []
    for m in LINK_RE.finditer(text):
        target = m.group(1).strip()
        if not target or target.startswith(("http://", "https://", "mailto:", "{{", "<")):
            continue
        found.append(target)
    return found


def check_file(md: Path, repo: Path) -> list[str]:
    errors: list[str] = []
    try:
        text = md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [f"{md}: 无法读取: {e}"]
    for target in iter_links(text):
        if target.startswith("#"):
            anchors = headings_of(md)
            if target[1:] not in anchors:
                errors.append(f"{md}: 锚点 #{target[1:]} 在本文件无对应标题")
            continue
        if "#" in target:
            rel, anchor = target.split("#", 1)
        else:
            rel, anchor = target, None
        dest = (md.parent / rel).resolve()
        try:
            dest.relative_to(repo.resolve())
        except ValueError:
            errors.append(f"{md}: 链接 {target} 指向仓库外")
            continue
        if not dest.exists():
            errors.append(f"{md}: 链接目标不存在: {target}")
            continue
        if anchor and dest.suffix.lower() == ".md":
            if anchor not in headings_of(dest):
                errors.append(f"{md}: 锚点 #{anchor} 在 {rel} 无对应标题")
    return errors


def fence_inline_violations(md: Path, raw: str) -> list[str]:
    """报告不在行首的代码围栏（如 mermaid 围栏粘连在段落末尾，会导致渲染失败）。"""
    errors: list[str] = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        if "```" in line and not line.lstrip().startswith("```"):
            errors.append(f"{md}:{lineno}: 代码围栏未独立成行: {line.strip()[:60]}")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="检查文档本地链接与锚点")
    ap.add_argument("--root", default=".", help="扫描根目录（相对仓库根）")
    args = ap.parse_args()
    repo = Path(__file__).resolve().parent.parent
    root = (repo / args.root).resolve()
    files = sorted(root.rglob("*.md")) if root.is_dir() else [root]
    # 跳过私有本地资料与 node_modules
    files = [f for f in files if "docs/private" not in f.parts and "node_modules" not in f.parts]
    errors: list[str] = []
    for f in files:
        errors.extend(check_file(f, repo))
        try:
            errors.extend(fence_inline_violations(f, f.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            pass
    for e in errors:
        print(e)
    print(f"检查 {len(files)} 个文件，发现 {len(errors)} 个问题")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
