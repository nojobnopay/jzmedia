"""Version-specific release summaries and offline Git draft ranges."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("release_notes_tools", SCRIPTS / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_notes_select_one_version_without_following_entries_or_comments(tmp_path):
    body = "### 新增\n\n- 支持投屏。\n\n```md\n## [0.26.0]\n- 示例\n```"
    (tmp_path / "CHANGELOG.md").write_text(
        f"# 版本说明\n\n<!-- ## [0.26.0] -->\n\n## [0.26.0] - 2026-10-15\n\n{body}\n\n"
        "## [0.25.0]\n\n- 旧版变化。\n", encoding="utf-8")
    assert release.read_release_notes(tmp_path, "0.26.0") == body


@pytest.mark.parametrize("entry", [
    None,
    "## [0.25.0]\n\n- 旧版变化。",
    "## [0.26.0]\n\n### 新增\n",
    "## [0.26.0]\n\n<!-- - 只有注释。 -->\n",
    "## [0.26.0]\n\n```md\n- 只有示例。\n```",
    "## [0.26.0]\n\n- TODO",
    "## [0.26.0]\n\n- 待补充：本次变更",
    "## [0.26.0]\n\n> 草稿：请核对后删除。\n\n- feat 示例。",
    "## [0.26.0]\n\n- 变化。\n\n## [0.26.0]\n\n- 重复版本。",
])
def test_missing_empty_duplicate_or_unreviewed_notes_cannot_release(tmp_path, entry):
    if entry is not None:
        (tmp_path / "CHANGELOG.md").write_text("# 版本说明\n\n" + entry, encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="CHANGELOG.md"):
        release.read_release_notes(tmp_path, "0.26.0")


@pytest.fixture
def history(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, text=True,
                              capture_output=True).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Release Test")
    git("config", "user.email", "release@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "tag.gpgsign", "false")

    def commit(subject):
        git("commit", "--allow-empty", "-m", subject)
        return git("rev-parse", "HEAD")

    return SimpleNamespace(root=tmp_path, git=git, commit=commit)


def test_draft_uses_highest_prior_reachable_numeric_tag_and_requires_review(history):
    state = history
    state.commit("feat: old change")
    state.git("tag", "v0.9.0")
    state.commit("fix: already released")
    state.git("tag", "v0.10.0")
    state.git("branch", "other")
    state.git("checkout", "other")
    state.commit("feat: another branch")
    state.git("tag", "v0.11.0")
    other_commit = state.git("rev-parse", "HEAD")
    state.git("checkout", "main")
    state.commit("feat(cast): support casting")
    state.git("tag", "v0.12.0")  # A pushed current tag must not erase its own draft.
    state.git("tag", "v0.11.9-rc1")
    state.commit("fix(tv): reduce audio stalls")
    state.commit("feat(api)!: change response format")
    state.commit("Document manual upgrades")
    draft = release.draft_release_notes(state.root, "0.12.0")
    assert "old change" not in draft and "already released" not in draft and "another branch" not in draft
    assert "### 新增\n\n- support casting" in draft
    assert "### 修复\n\n- reduce audio stalls" in draft
    assert "### 兼容与升级\n\n- change response format" in draft
    assert "Document manual upgrades" in draft
    assert not (state.root / "CHANGELOG.md").exists()
    (state.root / "CHANGELOG.md").write_text("# 版本说明\n\n" + draft, encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="remove placeholders"):
        release.read_release_notes(state.root, "0.12.0")
    with pytest.raises(release.ReleaseError):
        release.draft_release_notes(state.root, "0.12.0", other_commit)


def test_draft_without_prior_tag_and_explicit_commit_range(history):
    state = history
    base = state.commit("Initial implementation")
    state.commit("docs: explain upgrades")
    assert "Initial implementation" in release.draft_release_notes(state.root, "0.1.0")
    draft = release.draft_release_notes(state.root, "0.1.0", base)
    assert "Initial implementation" not in draft
    assert "### 文档\n\n- explain upgrades" in draft
    with pytest.raises(release.ReleaseError, match="No commits"):
        release.draft_release_notes(state.root, "0.1.0", "HEAD")
    with pytest.raises(release.ReleaseError):
        release.draft_release_notes(state.root, "0.1.0", "--all")


def test_shallow_clone_refuses_incomplete_draft(history):
    history.commit("feat: initial version")
    history.commit("fix: improve playback")
    shallow = history.root / "shallow"
    history.git("clone", "--depth", "1", history.root.as_uri(), str(shallow))
    with pytest.raises(release.ReleaseError, match="full Git history"):
        release.draft_release_notes(shallow, "0.1.0")


def test_cli_check_and_preview_require_current_version_notes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "check_versions", lambda root: SimpleNamespace(name="0.26.0", code=40))
    for args in (["check"], ["notes"]):
        with pytest.raises(release.ReleaseError, match="Missing CHANGELOG.md"):
            release.main(args)
    (tmp_path / "CHANGELOG.md").write_text("# 版本说明\n\n## [0.26.0]\n\n- 支持投屏。\n", encoding="utf-8")
    release.main(["notes"])
    assert capsys.readouterr().out == "- 支持投屏。\n"
    release.main(["check"])
    assert "release notes present" in capsys.readouterr().out
