"""Online-help publication follows immutable formal releases without remote writes."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("github_pages_tools", SCRIPTS / "github_pages.py")
pages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pages)
TAG = "v0.20.2"
COMMIT = "a" * 40
REPO = "example/jzmedia"
VERSION = SimpleNamespace(name="0.20.2", code=14)


@pytest.fixture
def published(tmp_path, monkeypatch):
    manifest = {"gitTag": TAG, "version": VERSION.name, "androidVersionCode": VERSION.code, "commit": COMMIT}
    record = {"id": 77, "tag_name": TAG, "draft": False, "prerelease": False,
              "assets": [{"id": 88, "name": "manifest.json", "state": "uploaded"}]}
    state = SimpleNamespace(manifest=manifest, record=record, latest=record,
                            releases=[record], remote_commit=COMMIT, calls=[])

    def api(path):
        state.calls.append(path)
        if path == f"repos/{REPO}":
            return {"default_branch": "main"}
        if path == f"repos/{REPO}/releases/latest":
            return state.latest
        if path.startswith(f"repos/{REPO}/git/ref/tags/"):
            return {"object": {"type": "tag", "sha": "b" * 40}}
        if path == f"repos/{REPO}/git/tags/" + "b" * 40:
            return {"object": {"type": "commit", "sha": state.remote_commit}}
        pytest.fail(f"Unexpected API access: {path}")

    def command(args, **kwargs):
        assert args == ["gh", "api", f"repos/{REPO}/releases/assets/88",
                        "-H", "Accept: application/octet-stream"]
        return json.dumps(state.manifest)

    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    state.output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(state.output))
    monkeypatch.setattr(pages.github, "releases", lambda repo: state.releases)
    monkeypatch.setattr(pages.github, "api", api)
    monkeypatch.setattr(pages.github.release, "command", command)
    monkeypatch.setattr(pages.github, "check_tag", lambda: (VERSION, COMMIT, TAG))
    return state


def test_manual_deployment_uses_manifest_commit_and_checks_annotated_tag(published):
    result = pages.select()
    assert result == {"deploy": "true", "tag": TAG, "commit": COMMIT, "version_code": "14",
                      "release_id": "77", "manifest_sha256": hashlib.sha256(
                          json.dumps(published.manifest).encode()).hexdigest()}
    assert f"repos/{REPO}/git/tags/" + "b" * 40 in published.calls
    assert f"commit={COMMIT}\n" in published.output.read_text()


@pytest.mark.parametrize("tag,ref,event", [
    (TAG, "refs/heads/main", "workflow_dispatch"),
    ("", "refs/heads/experiment", "workflow_dispatch"),
    ("", "refs/tags/v0.20.2", "workflow_dispatch"),
    ("", "refs/heads/main", "pull_request"),
    ("", "refs/heads/main", "release"),
])
def test_manual_deployment_has_no_unreleased_source_or_arbitrary_tag_selector(
        published, monkeypatch, tag, ref, event):
    monkeypatch.setenv("GITHUB_REF", ref)
    monkeypatch.setenv("GITHUB_EVENT_NAME", event)
    with pytest.raises(pages.github.release.ReleaseError):
        pages.select(tag)
    assert not published.output.exists()


@pytest.mark.parametrize("tag,commit,code", [
    (TAG, COMMIT, 14), ("", COMMIT, 14), ("v0.20.3", COMMIT, 14),
    (TAG, "c" * 40, 14), (TAG, COMMIT, 15),
])
def test_automatic_deployment_requires_the_successful_release_identity(
        published, monkeypatch, tag, commit, code):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setattr(pages.github, "check_tag", lambda: (
        SimpleNamespace(name=VERSION.name, code=code), commit, TAG))
    if (tag, commit, code) == (TAG, COMMIT, 14):
        assert pages.select(tag)["deploy"] == "true"
    else:
        with pytest.raises(pages.github.release.ReleaseError):
            pages.select(tag)
        assert not published.output.exists()


def newer_release(published):
    newer = {**published.record, "id": 99, "tag_name": "v0.21.0"}
    published.releases.append(newer)
    published.latest = newer


def test_old_release_retry_skips_before_reading_its_manifest(published, monkeypatch):
    newer_release(published)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setattr(pages.github.release, "command", lambda *a, **kw: pytest.fail("Old manifest read"))
    assert pages.select(TAG) == {"deploy": "false"}


def test_formal_version_order_ignores_drafts_prereleases_and_nonversion_releases(published):
    published.releases.extend([
        {**published.record, "id": 98, "tag_name": "v0.99.0", "draft": True},
        {**published.record, "id": 99, "tag_name": "v0.98.0", "prerelease": True},
        {**published.record, "id": 100, "tag_name": "v1.0.0-rc1"},
        {**published.record, "id": 101, "tag_name": "v00.99.0"},
        {**published.record, "id": 102, "tag_name": "v0.9.9"},
    ])
    assert pages.select()["tag"] == TAG


def test_no_formal_release_and_duplicate_versions_fail_closed(published):
    published.releases.clear()
    with pytest.raises(pages.github.release.ReleaseError, match="Publish a formal"):
        pages.select()
    published.releases.extend([published.record, copy.deepcopy(published.record)])
    with pytest.raises(pages.github.release.ReleaseError, match="Multiple releases"):
        pages.select()


def test_latest_pointer_must_agree_with_highest_formal_version(published):
    published.releases.append({**published.record, "id": 99, "tag_name": "v0.21.0"})
    with pytest.raises(pages.github.release.ReleaseError, match="GitHub latest"):
        pages.select()


@pytest.mark.parametrize("field,value", [
    ("gitTag", "v0.20.3"), ("version", "0.20.3"), ("validationOnly", True),
    ("commit", ""), ("commit", "main"), ("commit", "a" * 39), ("commit", None),
    ("androidVersionCode", 0), ("androidVersionCode", True), ("androidVersionCode", "14"),
])
def test_invalid_manifest_identity_blocks_build(published, field, value):
    published.manifest[field] = value
    with pytest.raises(pages.github.release.ReleaseError, match="invalid source or version"):
        pages.select()
    assert not published.output.exists()


@pytest.mark.parametrize("assets", [[], [{"name": "manifest.json", "state": "starter"}],
                                    [{"name": "manifest.json"}, {"name": "manifest.json"}]])
def test_missing_incomplete_or_ambiguous_manifest_blocks_build(published, assets):
    published.record["assets"] = assets
    with pytest.raises(pages.github.release.ReleaseError, match="complete manifest"):
        pages.select()


def test_moved_remote_tag_blocks_build(published):
    published.remote_commit = "c" * 40
    with pytest.raises(pages.github.release.ReleaseError, match="Remote Git tag changed"):
        pages.select()


def test_new_release_between_build_and_deploy_skips_old_artifact(published):
    identity = pages.current_identity(REPO)
    newer_release(published)
    assert pages.check_current(identity) == {"deploy": "false"}


@pytest.mark.parametrize("change", ["manifest", "release", "tag"])
def test_mutation_between_build_and_deploy_rejects_artifact(published, change):
    identity = pages.current_identity(REPO)
    if change == "manifest":
        published.manifest["createdAt"] = "changed after build"
    elif change == "release":
        published.record["id"] = 78
    else:
        published.remote_commit = "c" * 40
    with pytest.raises(pages.github.release.ReleaseError, match="changed"):
        pages.check_current(identity)


def test_unchanged_release_can_deploy_or_retry_without_docker_or_apk_work(published):
    assert pages.check_current(pages.current_identity(REPO)) == {"deploy": "true"}


@pytest.mark.parametrize("change", [None, "commit", "version", "code", "unsupported"])
def test_build_requires_clean_exact_release_source_with_pages_support(published, tmp_path, monkeypatch, change):
    identity = pages.current_identity(REPO)
    config = tmp_path / "docs/scripts/site-config.mjs"
    config.parent.mkdir(parents=True)
    if change != "unsupported":
        config.touch()

    def clean(root, expected):
        assert root == tmp_path and expected == COMMIT
        if change == "commit":
            raise pages.github.release.ReleaseError("HEAD changed")
        return COMMIT

    monkeypatch.setattr(pages.github.release, "clean_commit", clean)
    monkeypatch.setattr(pages.github.release, "check_versions", lambda root: SimpleNamespace(
        name="0.20.3" if change == "version" else VERSION.name,
        code=15 if change == "code" else VERSION.code))
    if change:
        with pytest.raises(pages.github.release.ReleaseError):
            pages.verify_source(identity, tmp_path)
    else:
        pages.verify_source(identity, tmp_path)
