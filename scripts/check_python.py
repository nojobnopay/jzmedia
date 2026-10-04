#!/usr/bin/env python3
"""Run pyflakes with narrow exceptions for the repository's existing export facades."""

import argparse
import ast
from collections import Counter
from pathlib import Path
import sys

from pyflakes import api, messages
from pyflakes.reporter import Reporter


ROOT = Path(__file__).resolve().parent.parent
# These package entrypoints deliberately preserve exports and monkeypatch hooks
# while their implementation lives in sibling modules. New facades need review.
EXPORT_FACADES = frozenset({
    "app/scanner/__init__.py",
    "app/store/__init__.py",
    "app/playback/__init__.py",
    "app/routers/movies/__init__.py",
    "app/routers/files/__init__.py",
    "app/routers/stream/__init__.py",
    "app/routers/fs/__init__.py",
})


class ProjectReporter(Reporter):
    def __init__(self, stdout, stderr, root=ROOT):
        super().__init__(stdout, stderr)
        self.root = Path(root).resolve()
        self.errors = 0
        self.ignored = Counter()
        self._exports = {}

    def _explicit_exports(self, path):
        if path not in self._exports:
            exports = set()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeError):
                self._exports[path] = exports
                return exports
            # Only module-level, explicitly same-name aliases are exports.
            # An ordinary unused import (even with noqa) remains an error.
            for node in tree.body:
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                module = ("." * node.level + (node.module or "")) if isinstance(node, ast.ImportFrom) else ""
                prefix = module + ("" if module.endswith(".") else ".") if module else ""
                for alias in node.names:
                    if alias.asname == alias.name:
                        exports.add((node.lineno, prefix + alias.name))
            self._exports[path] = exports
        return self._exports[path]

    def flake(self, message):
        path = Path(message.filename).resolve()
        try:
            facade = path.relative_to(self.root).as_posix() in EXPORT_FACADES
        except ValueError:
            facade = False
        name = message.message_args[0] if message.message_args else ""
        if facade:
            if (isinstance(message, messages.ImportStarUsed) and name.startswith(".")) or (
                    isinstance(message, messages.UnusedImport) and name.startswith(".") and name.endswith(".*")):
                self.ignored["relative wildcard exports in approved facades"] += 1
                return
            if (isinstance(message, messages.UnusedImport)
                    and (message.lineno, name) in self._explicit_exports(path)):
                self.ignored["explicit same-name re-exports in approved facades"] += 1
                return
        self.errors += 1
        super().flake(message)

    def syntaxError(self, *args):
        self.errors += 1
        super().syntaxError(*args)

    def unexpectedError(self, *args):
        self.errors += 1
        super().unexpectedError(*args)


def check_paths(paths, *, root=ROOT, stdout=None, stderr=None):
    reporter = ProjectReporter(stdout or sys.stdout, stderr or sys.stderr, root)
    api.checkRecursive([str(path) for path in paths], reporter)
    return reporter


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Additional files or directories (app is always checked)")
    args = parser.parse_args(argv)
    reporter = check_paths([ROOT / "app", *args.paths])
    for reason, count in sorted(reporter.ignored.items()):
        print(f"Allowed {count} pyflakes diagnostics: {reason}.")
    print(f"Python check: {reporter.errors} error(s); all other pyflakes diagnostics block release.")
    return int(bool(reporter.errors))


if __name__ == "__main__":
    raise SystemExit(main())
