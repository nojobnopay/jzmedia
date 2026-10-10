#!/usr/bin/env python3
"""提醒 Markdown 页首 version/reviewed 元信息未随正文更新。

用法（仓库根目录）::

    python3 scripts/check_docs_meta.py                  # 工作树相对 HEAD
    python3 scripts/check_docs_meta.py --base v0.23.0   # 工作树相对指定基线
    python3 scripts/check_docs_meta.py docs/foo.md      # 只看指定文件
    python3 scripts/check_docs_meta.py --strict         # 有提醒时退出 1

公开帮助页（docs/.vitepress/public-pages.mjs 的导航清单，导航即公开许可）
正文变化时，行为、约束或命令类修改应同步更新 version（当前开发版本）与
reviewed（核对当日）；纯文字润色可以不动。本检查无法判断改动性质，
只列出候选页面供人工确认，默认不影响退出码。扫描范围复用链接检查的
同一份源文件清单，私有记录、路线图与构建产物不在其中。
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
VERSION_RE = re.compile(r"^version:\s*(\S*)\s*$", re.MULTILINE)
REVIEWED_RE = re.compile(r"^reviewed:\s*(\S*)\s*$", re.MULTILINE)
VERSION_FORMAT = re.compile(r"\d+\.\d+\.\d+")
REVIEWED_FORMAT = re.compile(r"\d{4}-\d{2}-\d{2}")


def link_checker():
    spec = importlib.util.spec_from_file_location(
        "check_docs_links", Path(__file__).with_name("check_docs_links.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def changed_markdown(base: str) -> list[str]:
    diff = run_git("diff", "--name-only", "--diff-filter=ACMR", base, "--", "*.md")
    if diff.returncode:
        raise SystemExit(f"无法比较基线 {base}：{diff.stderr.strip()}")
    files = {line for line in diff.stdout.splitlines() if line.endswith(".md")}
    others = run_git("ls-files", "--others", "--exclude-standard", "--", "*.md")
    files |= {line for line in others.stdout.splitlines() if line.endswith(".md")}
    return sorted(files)


def public_pages() -> set[str] | None:
    """导航清单是公开许可的唯一来源；读不到时放弃缺元信息类提醒。"""
    try:
        result = subprocess.run(
            ["node", "-e",
             "import('file://' + process.argv[1]).then(m => console.log(JSON.stringify(m.publicPages())))",
             str(ROOT / "docs" / ".vitepress" / "public-pages.mjs")],
            capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return set(json.loads(result.stdout))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def split_frontmatter(text: str) -> tuple[str, str]:
    match = FRONT_RE.match(text)
    if not match:
        return "", text
    return match.group(1), text[match.end():]


def meta_of(meta_text: str) -> tuple[str | None, str | None]:
    version = VERSION_RE.search(meta_text)
    reviewed = REVIEWED_RE.search(meta_text)
    return (version.group(1) if version else None,
            reviewed.group(1) if reviewed else None)


def base_text(base: str, rel: str) -> str | None:
    result = run_git("show", f"{base}:{rel}")
    return result.stdout if result.returncode == 0 else None


def _valid_date(value: str) -> bool:
    try:
        datetime.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="提醒 version/reviewed 元信息未随正文更新")
    parser.add_argument("--base", default="HEAD", help="比较基线，默认 HEAD（工作树改动）")
    parser.add_argument("--strict", action="store_true", help="有提醒时退出 1")
    parser.add_argument("files", nargs="*", help="只检查这些仓库相对路径（lint 局部检查时传入）")
    args = parser.parse_args()

    checker = link_checker()
    repo = ROOT
    in_scope = {f.relative_to(repo).as_posix() for f in checker.markdown_files(repo, repo)}
    selected = {os.path.normpath(f).replace("\\", "/") for f in args.files} or None
    changed = [f for f in changed_markdown(args.base)
               if f in in_scope and (selected is None or f in selected)]
    public = public_pages()

    unverified: list[str] = []
    missing: list[str] = []
    malformed: list[str] = []
    for rel in changed:
        current = (repo / rel).read_text(encoding="utf-8")
        current_meta, current_body = split_frontmatter(current)
        version, reviewed = meta_of(current_meta)
        previous = base_text(args.base, rel)
        previous_meta, previous_body = split_frontmatter(previous or "")
        old_version, old_reviewed = meta_of(previous_meta)
        if previous is not None and current_body == previous_body:
            continue
        if public is None:
            continue
        # 公开清单以 docs/ 为根（如 developer/documentation.md）。
        docs_rel = rel[len("docs/"):] if rel.startswith("docs/") else None
        if docs_rel is None or docs_rel not in public:
            continue
        if version is None or reviewed is None:
            missing.append(rel)
            continue
        if (version, reviewed) == (old_version, old_reviewed):
            unverified.append(rel)
        if (not VERSION_FORMAT.fullmatch(version)
                or not REVIEWED_FORMAT.fullmatch(reviewed) or not _valid_date(reviewed)):
            malformed.append(rel)

    if public is None:
        print("提示：无法读取公开页清单，本次跳过元信息提醒。")
    if unverified:
        print("以下公开页正文有改动，但页首 version/reviewed 未更新，请确认是否需要"
              "（行为/约束/命令变化 → version 升当前开发版本、reviewed 写核对当日；纯文字润色不动）：")
        for rel in unverified:
            print(f"  {rel}")
    if missing:
        print("以下公开页缺少 version/reviewed 元信息（新增页或元信息被移除），请补充：")
        for rel in missing:
            print(f"  {rel}")
    if malformed:
        print("以下公开页的 version/reviewed 格式不对（应为 X.Y.Z 与 YYYY-MM-DD 有效日期）：")
        for rel in malformed:
            print(f"  {rel}")
    count = len(unverified) + len(missing) + len(malformed)
    print(f"元信息提醒 {count} 条" + ("：无" if not count else ""))
    return 1 if args.strict and count else 0


if __name__ == "__main__":
    sys.exit(main())
