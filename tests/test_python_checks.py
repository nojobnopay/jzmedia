"""Export exceptions must not hide undefined names, ordinary unused imports or syntax errors."""

from io import StringIO

import pytest

from scripts.check_python import check_paths


def check_source(root, relative, source):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    stdout, stderr = StringIO(), StringIO()
    result = check_paths([path], root=root, stdout=stdout, stderr=stderr)
    return result, stdout.getvalue() + stderr.getvalue()


def test_approved_facade_accepts_only_its_explicit_export_forms(tmp_path):
    result, output = check_source(tmp_path, "app/scanner/__init__.py", """
from .routes import *
from .. import store as store
import os as os
""")
    assert result.errors == 0 and output == ""
    assert sum(result.ignored.values()) == 4


@pytest.mark.parametrize("statement,match", [
    ("import os", "imported but unused"),
    ("import os  # noqa: F401", "imported but unused"),
    ("from .. import store", "imported but unused"),
    ("print(missing)", "may be undefined"),
    ("__all__ = ['missing']", "may be undefined"),
    ("def f():\n    value = 1", "assigned to but never used"),
])
def test_real_diagnostics_are_not_hidden_inside_facades(tmp_path, statement, match):
    result, output = check_source(tmp_path, "app/scanner/__init__.py",
                                  "from .routes import *\n" + statement + "\n")
    assert result.errors > 0 and match in output


@pytest.mark.parametrize("relative", ["app/feature.py", "app/new_package/__init__.py"])
def test_unreviewed_paths_get_no_export_exceptions(tmp_path, relative):
    result, output = check_source(tmp_path, relative, "from .routes import *\nimport os as os\n")
    assert result.errors == 3 and "unable to detect undefined names" in output
    assert result.ignored == {}


def test_absolute_wildcard_import_is_not_an_allowed_facade_export(tmp_path):
    result, output = check_source(tmp_path, "app/scanner/__init__.py", "from os import *\n")
    assert result.errors == 2 and "unable to detect undefined names" in output
    assert result.ignored == {}


def test_syntax_and_missing_file_are_failures(tmp_path):
    result, output = check_source(tmp_path, "app/scanner/__init__.py", "def broken(:\n")
    assert result.errors == 1 and "syntax" in output
    stdout, stderr = StringIO(), StringIO()
    missing = check_paths([tmp_path / "absent.py"], root=tmp_path, stdout=stdout, stderr=stderr)
    assert missing.errors == 1 and "absent.py" in stderr.getvalue()
