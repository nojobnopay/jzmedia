"""Cloud release identity and resumable publication without network or Docker access."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
from types import SimpleNamespace
import zipfile

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("github_release_tools", SCRIPTS / "github_release.py")
github = importlib.util.module_from_spec(spec)
spec.loader.exec_module(github)
COMMIT = "a" * 40
CERTIFICATE = "b" * 64
VERSION = SimpleNamespace(name="0.20.2", code=14, parts=(0, 20, 2))
TAG = "v0.20.2"
REPO = "example/jzmedia"
IMAGE = f"ghcr.io/{REPO}:{TAG}"


@pytest.fixture
def tag_context(monkeypatch):
    for name, value in {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": TAG,
                        "GITHUB_SHA": "c" * 40, "GITHUB_REPOSITORY": REPO}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(github.release, "check_versions", lambda root: VERSION)
    monkeypatch.setattr(github.release, "clean_commit", lambda root: COMMIT)
    monkeypatch.setattr(github.release, "git_tag_commit", lambda root, tag: COMMIT)
    monkeypatch.setattr(github.release, "check_tag_history", lambda *args: None)
    monkeypatch.setattr(github.release, "command", lambda *args, **kwargs: COMMIT)


def test_tag_check_accepts_annotated_event_resolving_to_same_commit(tag_context):
    assert github.check_tag() == (VERSION, COMMIT, TAG)


@pytest.mark.parametrize("field,value", [
    ("GITHUB_REF_TYPE", "branch"), ("GITHUB_REF_NAME", "v00.20.2"),
    ("GITHUB_REF_NAME", "v0.20.3"), ("GITHUB_REF_NAME", "v0.20.2-rc1"),
    ("GITHUB_SHA", ""), ("GITHUB_SHA", "a" * 39),
])
def test_tag_check_rejects_wrong_event_or_version(tag_context, monkeypatch, field, value):
    monkeypatch.setenv(field, value)
    with pytest.raises(github.release.ReleaseError):
        github.check_tag()


@pytest.mark.parametrize("changed", ["git_tag_commit", "command"])
def test_tag_and_event_must_both_resolve_to_checked_out_commit(tag_context, monkeypatch, changed):
    monkeypatch.setattr(github.release, changed, lambda *args, **kwargs: "d" * 40)
    with pytest.raises(github.release.ReleaseError):
        github.check_tag()


@pytest.mark.parametrize("remote_commit", [COMMIT, "d" * 40])
def test_remote_annotated_tag_is_dereferenced_and_must_match_source(monkeypatch, remote_commit):
    def api(path):
        if "/git/ref/" in path:
            return {"object": {"type": "tag", "sha": "e" * 40}}
        assert path.endswith("/git/tags/" + "e" * 40)
        return {"object": {"type": "commit", "sha": remote_commit}}
    monkeypatch.setattr(github, "api", api)
    if remote_commit == COMMIT:
        github.check_remote_tag(REPO, TAG, COMMIT)
    else:
        with pytest.raises(github.release.ReleaseError, match="Remote Git tag changed"):
            github.check_remote_tag(REPO, TAG, COMMIT)


@pytest.fixture
def preflight_context(tag_context, tmp_path, monkeypatch):
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setattr(github, "check_tag", lambda: (VERSION, COMMIT, TAG))
    return output


def test_manual_preflight_only_validates_source_without_remote_access(preflight_context, monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("GITHUB_REF_TYPE", "branch")

    def forbidden(*args, **kwargs):
        pytest.fail("Manual validation must not access remote releases or require a version tag")

    monkeypatch.setattr(github, "check_tag", forbidden)
    monkeypatch.setattr(github, "find_release", forbidden)
    monkeypatch.setattr(github.release, "command", forbidden)
    github.preflight()
    assert preflight_context.read_text() == f"mode=validate\ntag={TAG}\n"


@pytest.mark.parametrize("remote_commit", [COMMIT, "d" * 40])
def test_published_preflight_requires_same_identity_and_skips_later_tag_history(
        preflight_context, monkeypatch, remote_commit):
    monkeypatch.setattr(github, "find_release", lambda repo, tag: {
        "draft": False, "assets": [{"name": "manifest.json", "id": 123}],
    })

    def download(args, **kwargs):
        assert args == ["gh", "api", f"repos/{REPO}/releases/assets/123",
                        "-H", "Accept: application/octet-stream"]
        return json.dumps({"version": VERSION.name, "androidVersionCode": VERSION.code,
                           "commit": remote_commit})

    def history(*args):
        pytest.fail("An already published version must not be rebuilt against newer tag history")

    monkeypatch.setattr(github.release, "command", download)
    monkeypatch.setattr(github.release, "check_tag_history", history)
    if remote_commit == COMMIT:
        github.preflight()
        assert preflight_context.read_text() == f"mode=complete\ntag={TAG}\n"
    else:
        with pytest.raises(github.release.ReleaseError, match="different source"):
            github.preflight()
        assert not preflight_context.exists()


def test_preflight_rejects_rebuilding_existing_draft_and_explains_resume(preflight_context, monkeypatch):
    monkeypatch.setattr(github, "find_release", lambda repo, tag: {"draft": True})
    monkeypatch.setattr(github.release, "command", lambda *args, **kwargs: pytest.fail("Draft was accessed or changed"))
    with pytest.raises(github.release.ReleaseError, match="re-run only the failed publish job"):
        github.preflight()
    assert not preflight_context.exists()


def test_new_tag_preflight_still_checks_monotonic_version_history(preflight_context, monkeypatch):
    calls = []
    monkeypatch.setattr(github, "find_release", lambda repo, tag: None)
    monkeypatch.setattr(github.release, "check_tag_history", lambda *args: calls.append(args))
    github.preflight()
    assert calls == [(github.ROOT, VERSION.name, VERSION.code)]
    assert preflight_context.read_text() == f"mode=build\ntag={TAG}\n"


def write_checksums(directory, record):
    (directory / "SHA256SUMS").write_text("".join(
        f"{github.release.sha256(directory / name)}  {name}\n" for name in github.asset_names(record)))


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("JZMEDIA_ANDROID_DEBUG_CERT_SHA256", CERTIFICATE)
    directory = tmp_path / "bundle"
    directory.mkdir()
    apk = directory / f"jzmedia-tv-{VERSION.name}-debug.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/jzmedia-build.json", json.dumps({
            "versionName": VERSION.name, "versionCode": VERSION.code,
            "source": {"commit": COMMIT, "dirty": False},
        }))
    android = {"file": apk.name, "versionName": VERSION.name + "-debug", "versionCode": VERSION.code,
               "applicationId": "org.jzmedia.tv.debug", "variant": "debug", "unsigned": False,
               "sha256": github.release.sha256(apk), "source": {"commit": COMMIT, "dirty": False},
               "signing": {"verified": True, "certificateSha256": CERTIFICATE, "debugCertificate": True}}
    apk.with_suffix(".apk.json").write_text(json.dumps(android))
    apk.with_suffix(".apk.sha256").write_text(f"{android['sha256']}  {apk.name}\n")
    config = {"os": "linux", "architecture": "amd64", "config": {"Labels": {
        "org.opencontainers.image.version": VERSION.name, "org.opencontainers.image.revision": COMMIT}}}
    config_bytes = json.dumps(config).encode()
    config_digest = hashlib.sha256(config_bytes).hexdigest()
    config_file = config_digest + ".json"
    with tarfile.open(directory / github.archive_name(TAG), "w:gz") as archive:
        for name, contents in ((config_file, config_bytes), ("manifest.json", json.dumps([
                {"Config": config_file, "RepoTags": [IMAGE], "Layers": []}]).encode())):
            member = tarfile.TarInfo(name)
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
    record = {"schema": 1, "version": VERSION.name, "androidVersionCode": VERSION.code,
              "commit": COMMIT, "gitTag": TAG, "apkVariant": "debug", "android": android,
              "docker": {"tags": [f"jzmedia:{TAG}", "jzmedia:latest"], "image": {
                  "id": "sha256:" + config_digest, "platform": "linux/amd64", "sizeBytes": 123}}}
    (directory / "manifest.json").write_text(json.dumps(record))
    (directory / "docker-compose.yml").write_text(f"services:\n  mymedia:\n    image: {IMAGE}\n")
    (directory / "LICENSE").write_text("BSD-3-Clause test fixture\n")
    write_checksums(directory, record)
    return SimpleNamespace(directory=directory, record=record)


def test_bundle_verifies_same_source_apk_certificate_and_docker_archive(bundle):
    assert github.verify_bundle(bundle.directory, REPO, TAG, COMMIT) == bundle.record


def test_bundle_accepts_uppercase_certificate_fingerprint_variable(bundle, monkeypatch):
    monkeypatch.setenv("JZMEDIA_ANDROID_DEBUG_CERT_SHA256", CERTIFICATE.upper())
    assert github.verify_bundle(bundle.directory, REPO, TAG, COMMIT) == bundle.record


@pytest.mark.parametrize("change", ["validation", "checksum", "apk_path", "checksum_path", "certificate"])
def test_bundle_rejects_validation_tampering_and_unsafe_paths(bundle, monkeypatch, change):
    directory, record = bundle.directory, bundle.record
    if change == "validation":
        record["validationOnly"] = True
        (directory / "manifest.json").write_text(json.dumps(record))
    elif change == "checksum":
        (directory / "LICENSE").write_text("tampered\n")
    elif change == "apk_path":
        record["android"]["file"] = "../outside.apk"
        (directory / "manifest.json").write_text(json.dumps(record))
    elif change == "checksum_path":
        with (directory / "SHA256SUMS").open("a") as stream:
            stream.write("0" * 64 + "  ../outside\n")
    else:
        monkeypatch.setenv("JZMEDIA_ANDROID_DEBUG_CERT_SHA256", "f" * 64)
    with pytest.raises(github.release.ReleaseError):
        github.verify_bundle(directory, REPO, TAG, COMMIT)


def test_bundle_cannot_verify_one_apk_but_upload_another(bundle):
    directory, record = bundle.directory, bundle.record
    verified = record["android"]["file"]
    replacement = "unchecked.apk"
    (directory / verified).with_suffix(".apk.json").rename(directory / (replacement + ".json"))
    (directory / verified).with_suffix(".apk.sha256").replace(directory / (replacement + ".sha256"))
    # Keep the original checksum required by verify_apk; only the replacement is in SHA256SUMS.
    (directory / (verified + ".sha256")).write_text(f"{record['android']['sha256']}  {verified}\n")
    (directory / replacement).write_bytes(b"not the verified APK")
    record["android"] = {**record["android"], "file": replacement}
    (directory / "manifest.json").write_text(json.dumps(record))
    write_checksums(directory, record)
    with pytest.raises(github.release.ReleaseError):
        github.verify_bundle(directory, REPO, TAG, COMMIT)


def test_registry_compose_removes_build_and_preserves_all_runtime_configuration():
    source = (SCRIPTS.parent / "docker-compose.yml").read_text()
    generated = github.compose_for_registry(source, IMAGE)
    assert f"    image: {IMAGE}\n" in generated
    assert "    build:" not in generated and "BUILD_HTTP_PROXY" not in generated
    runtime = source.split("    image: ", 1)[1].split("\n", 1)[1]
    assert generated.endswith(runtime)
    assert "MEDIA_ROOT: /app/media" in generated and "required: false" in generated


def remote_asset(directory, name):
    return {"id": name, "name": name, "state": "uploaded", "size": (directory / name).stat().st_size,
            "digest": "sha256:" + github.release.sha256(directory / name)}


def test_asset_retry_skips_identical_files_and_rejects_changed_or_missing_public_assets(bundle):
    names = [*github.asset_names(bundle.record), "SHA256SUMS"]
    record = {"tag_name": TAG, "draft": True, "assets": [remote_asset(bundle.directory, names[0])]}
    assert github.verify_assets(record, bundle.directory) == names[1:]
    with pytest.raises(github.release.ReleaseError, match="inventory"):
        github.verify_assets(record, bundle.directory, complete=True)
    record["assets"][0]["digest"] = "sha256:" + "f" * 64
    with pytest.raises(github.release.ReleaseError, match="differs"):
        github.verify_assets(record, bundle.directory)


def test_empty_interrupted_draft_upload_is_retryable_but_never_counts_as_complete(bundle):
    names = [*github.asset_names(bundle.record), "SHA256SUMS"]
    record = {"tag_name": TAG, "draft": True,
              "assets": [remote_asset(bundle.directory, name) for name in names]}
    record["assets"][0].update(state="starter", size=0, digest=None)
    assert github.verify_assets(record, bundle.directory) == [names[0]]
    with pytest.raises(github.release.ReleaseError):
        github.verify_assets(record, bundle.directory, complete=True)
    record["draft"] = False
    with pytest.raises(github.release.ReleaseError):
        github.verify_assets(record, bundle.directory)


@pytest.fixture
def publisher(bundle, monkeypatch):
    state = SimpleNamespace(events=[], uploads=[], existing=None, remote=None)
    expected = bundle.record["docker"]["image"]["id"]
    manifest = {"config": {"digest": expected}, "layers": []}
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_ACTOR", "example")
    monkeypatch.setenv("GH_TOKEN", "fake-test-token")
    monkeypatch.setattr(github, "check_tag", lambda: (VERSION, COMMIT, TAG))
    monkeypatch.setattr(github, "releases", lambda repo: [state.existing] if state.existing else [])
    monkeypatch.setattr(github, "find_release", lambda repo, tag: state.existing)
    monkeypatch.setattr(github, "api", lambda path: {"object": {"type": "commit", "sha": COMMIT}})
    monkeypatch.setattr(github.release, "image_info", lambda image: {
        "Id": expected, "Os": "linux", "Architecture": "amd64", "Size": 123,
        "Config": {"Labels": {"org.opencontainers.image.version": VERSION.name,
                               "org.opencontainers.image.revision": COMMIT}}})

    def command(args, **kwargs):
        args = list(map(str, args))
        assert "--clobber" not in args
        if args[:3] == ["gh", "release", "create"]:
            state.events.append("draft")
            state.existing = {"tag_name": TAG, "draft": True, "prerelease": False, "assets": []}
        elif args[:3] == ["gh", "release", "upload"]:
            names = [Path(name).name for name in args[6:]]
            state.uploads.extend(names)
            state.existing["assets"].extend(remote_asset(bundle.directory, name) for name in names)
            state.events.append("assets_persisted")
        elif args[:2] == ["docker", "push"]:
            state.events.append("latest" if args[2].endswith(":latest") else "version_push")
            state.remote = manifest
        elif args[:3] == ["gh", "release", "edit"]:
            state.events.append("published")
            state.existing["draft"] = False
        elif args[:4] == ["gh", "api", "--method", "DELETE"]:
            asset_id = args[4].rsplit("/", 1)[1]
            removed = [item for item in state.existing["assets"] if item["id"] == asset_id]
            assert len(removed) == 1 and removed[0]["state"] == "starter" and removed[0]["size"] == 0
            state.existing["assets"].remove(removed[0])
            state.events.append("empty_upload_removed")
        else:
            assert args[:2] in (["docker", "load"], ["docker", "login"], ["docker", "tag"])
        return ""

    def inspect(image, env, *, missing_ok=False):
        if "jzmedia-anonymous-" in env["DOCKER_CONFIG"]:
            state.events.append("anonymous_verified")
        return state.remote

    monkeypatch.setattr(github.release, "command", command)
    monkeypatch.setattr(github, "remote_manifest", inspect)
    state.manifest = manifest
    return state


@pytest.mark.parametrize("partial_retry", [False, True])
def test_publication_persists_all_assets_before_registry_and_publishes_latest_last(bundle, publisher, partial_retry):
    names = [*github.asset_names(bundle.record), "SHA256SUMS"]
    if partial_retry:
        publisher.existing = {"tag_name": TAG, "draft": True, "prerelease": False,
                              "assets": [remote_asset(bundle.directory, name) for name in names[:3]]}
    github.publish(bundle.directory)
    assert publisher.uploads == (names[3:] if partial_retry else names)
    assert publisher.events[-5:] == ["assets_persisted", "version_push", "anonymous_verified", "published", "latest"]
    assert publisher.existing["draft"] is False
    assert len(publisher.existing["assets"]) == len(names)


def test_different_remote_version_image_blocks_registry_and_release_mutation(bundle, publisher):
    publisher.remote = {"config": {"digest": "sha256:" + "f" * 64}}
    with pytest.raises(github.release.ReleaseError, match="refusing overwrite"):
        github.publish(bundle.directory)
    assert publisher.events == [] and publisher.uploads == []


def test_existing_identical_published_release_can_finish_latest_without_replacing_assets(bundle, publisher):
    names = [*github.asset_names(bundle.record), "SHA256SUMS"]
    publisher.remote = publisher.manifest
    publisher.existing = {"tag_name": TAG, "draft": False, "prerelease": False,
                          "assets": [remote_asset(bundle.directory, name) for name in names]}
    github.publish(bundle.directory)
    assert publisher.uploads == []
    assert publisher.events == ["anonymous_verified", "latest"]


def test_retry_removes_only_empty_interrupted_upload_and_keeps_completed_assets(bundle, publisher):
    names = [*github.asset_names(bundle.record), "SHA256SUMS"]
    publisher.existing = {"tag_name": TAG, "draft": True, "prerelease": False,
                          "assets": [remote_asset(bundle.directory, name) for name in names]}
    publisher.existing["assets"][0].update(state="starter", size=0, digest=None)
    github.publish(bundle.directory)
    assert publisher.uploads == [names[0]]
    assert publisher.events[:2] == ["empty_upload_removed", "assets_persisted"]
    assert publisher.existing["draft"] is False


def test_tag_moving_during_upload_keeps_release_in_draft_and_latest_unchanged(bundle, publisher, monkeypatch):
    calls = []

    def tag_check(*args):
        calls.append(args)
        if len(calls) == 2:
            raise github.release.ReleaseError("Remote Git tag changed")

    monkeypatch.setattr(github, "check_remote_tag", tag_check)
    with pytest.raises(github.release.ReleaseError, match="Remote Git tag changed"):
        github.publish(bundle.directory)
    assert "published" not in publisher.events and "latest" not in publisher.events
    assert publisher.existing["draft"] is True


def test_newer_release_prevents_latest_rollback_before_remote_writes(bundle, publisher, monkeypatch):
    monkeypatch.setattr(github, "releases", lambda repo: [
        {"tag_name": "v0.21.0", "draft": False, "prerelease": False}])
    with pytest.raises(github.release.ReleaseError, match="latest backwards"):
        github.publish(bundle.directory)
    assert publisher.events == []


def test_wait_for_release_retries_list_lag_then_returns_draft(monkeypatch):
    release_obj = {"tag_name": TAG, "draft": True}
    calls = []
    monkeypatch.setattr(github, "find_release",
                        lambda repo, tag: calls.append(tag) or (release_obj if len(calls) > 2 else None))
    slept = []
    monkeypatch.setattr(github.time, "sleep", lambda s: slept.append(s))
    assert github.wait_for_release(REPO, TAG, attempts=5, delay=1) is release_obj
    assert len(calls) == 3 and slept == [1, 1]


def test_wait_for_release_gives_clear_error_instead_of_none_crash(monkeypatch):
    monkeypatch.setattr(github, "find_release", lambda repo, tag: None)
    monkeypatch.setattr(github.time, "sleep", lambda s: None)
    with pytest.raises(github.release.ReleaseError, match="not visible after creation"):
        github.wait_for_release(REPO, TAG, attempts=3, delay=0)
