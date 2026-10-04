"""A release has one version and cannot silently mix npm or Android identities."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app import version as runtime
from scripts import versioning


@pytest.fixture
def version_tree(tmp_path):
    (tmp_path / "version.properties").write_text(
        "# shared release\nversionName=0.9.9\nversionCode=10\n", encoding="utf-8")
    for project in ("frontend", "docs"):
        directory = tmp_path / project
        directory.mkdir()
        package = {"name": project, "version": "0.9.9", "dependencies": {"example": "2.0.0"}}
        (directory / "package.json").write_text(json.dumps(package), encoding="utf-8")
        lock = {**package, "lockfileVersion": 3, "packages": {
            "": package, "node_modules/example": {"version": "2.0.0", "integrity": "keep-this"}}}
        (directory / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    return tmp_path


def test_release_advances_all_packages_and_preserves_dependencies(version_tree):
    updated = versioning.set_version(version_tree, "0.10.0", 11)
    assert updated == runtime.Version("0.10.0", 11)
    assert versioning.check_versions(version_tree) == updated
    assert (version_tree / "version.properties").read_text().startswith("# shared release\n")
    for project in ("frontend", "docs"):
        lock = json.loads((version_tree / project / "package-lock.json").read_text())
        assert lock["packages"]["node_modules/example"] == {"version": "2.0.0", "integrity": "keep-this"}
        assert lock["dependencies"] == {"example": "2.0.0"}


@pytest.mark.parametrize("name,code", [
    ("0.9.8", 11), ("0.9.9", 11), ("0.10.0", 10), ("0.10.0", 9),
    ("v0.10.0", 11), ("00.10.0", 11), ("0.10.0-rc.1", 11),
    ("0.10.0+local", 11), ("0.10.0", True), ("0.10.0", "11"),
    ("0.10.0", 2_100_000_001), ("0.10.0", 0),
])
def test_invalid_transition_leaves_every_file_untouched(version_tree, name, code):
    before = {path: path.read_bytes() for path in version_tree.rglob("*") if path.is_file()}
    with pytest.raises(ValueError):
        versioning.set_version(version_tree, name, code)
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("file,nested", [
    ("frontend/package.json", False), ("docs/package.json", False),
    ("frontend/package-lock.json", False), ("docs/package-lock.json", False),
    ("frontend/package-lock.json", True), ("docs/package-lock.json", True),
])
def test_drift_blocks_build_and_same_version_can_repair_it(version_tree, file, nested):
    path = version_tree / file
    data = json.loads(path.read_text())
    target = data["packages"][""] if nested else data
    target["version"] = "0.1.0"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=file):
        versioning.check_versions(version_tree)
    versioning.set_version(version_tree, "0.9.9", 10)
    assert versioning.check_versions(version_tree) == runtime.Version("0.9.9", 10)


def test_invalid_lockfile_is_detected_before_any_write(version_tree):
    path = version_tree / "docs/package-lock.json"
    path.write_text('{"version":"0.9.9"}', encoding="utf-8")
    before = {path: path.read_bytes() for path in version_tree.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="root packages entry"):
        versioning.set_version(version_tree, "0.10.0", 11)
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("contents", [
    "versionName=0.1.0\n", "versionName=0.1.0\nversionCode=01\n",
    "versionName=0.1.0\nversionCode=1\nversionName=0.2.0\n",
    "versionName=0.1.0\nversionCode=1\nother=2\n",
    "versionName=0.1.0\nversionCode=1.0\n",
])
def test_invalid_source_is_rejected(tmp_path, contents):
    (tmp_path / "version.properties").write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError):
        runtime.read_version(tmp_path)


def test_runtime_version_works_in_image_layout_without_git_or_scripts(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    shutil.copyfile(Path(runtime.__file__), app_dir / "version.py")
    (tmp_path / "version.properties").write_text("versionName=2.3.4\nversionCode=25\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", "from app.version import VERSION; print(VERSION.name, VERSION.code)"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "2.3.4 25"


@pytest.mark.parametrize("commit", ["a" * 40, "B" * 64])
def test_image_commit_is_available_without_git(monkeypatch, commit):
    monkeypatch.setenv("JZMEDIA_BUILD_COMMIT", commit)

    def unexpected_git(*args, **kwargs):
        pytest.fail("Injected image identity must not require Git")

    monkeypatch.setattr(runtime.subprocess, "run", unexpected_git)
    assert runtime.build_commit() == commit.lower()


@pytest.mark.parametrize("injected", ["", "abc123", "<untrusted>", "a" * 41])
def test_source_commit_fallback_uses_repository_directory(monkeypatch, tmp_path, injected):
    monkeypatch.setenv("JZMEDIA_BUILD_COMMIT", injected)
    commit = "b" * 40

    def git(args, **kwargs):
        assert args == ["git", "rev-parse", "HEAD"]
        assert kwargs["cwd"] == tmp_path
        return SimpleNamespace(returncode=0, stdout=commit + "\n")

    monkeypatch.setattr(runtime.subprocess, "run", git)
    assert runtime.build_commit(tmp_path) == commit


def test_unknown_commit_does_not_echo_untrusted_values(monkeypatch):
    monkeypatch.setenv("JZMEDIA_BUILD_COMMIT", "<untrusted>")
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout="<also-untrusted>"))
    assert runtime.build_commit() == "unknown"

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(runtime.subprocess, "run", no_git)
    assert runtime.build_commit() == "unknown"


def test_repository_package_versions_match_source():
    assert versioning.check_versions() == runtime.VERSION


def test_health_reports_same_release_as_fastapi_and_full_image_commit(monkeypatch):
    from app import media, store, transcode
    from app.main import app
    from app.routers import health

    commit = "1234567890abcdef" * 2 + "12345678"
    monkeypatch.setenv("JZMEDIA_BUILD_COMMIT", commit)
    monkeypatch.setattr(media, "bin_status", lambda: {"ffmpeg": True, "ffprobe": True})
    monkeypatch.setattr(transcode, "backend_info", lambda: {"name": "software", "hw": False})
    monkeypatch.setattr(store, "health_check", lambda: {"ok": True})
    monkeypatch.setattr(store, "list_media_libraries", lambda **kw: [])
    monkeypatch.setattr(health, "_disk_space", lambda: {"ok": True})
    body = health.health()
    assert body["version"] == app.version == runtime.VERSION.name
    assert body["version_code"] == runtime.VERSION.code
    assert body["build_commit"] == commit
    assert body["build"] == commit[:12]
