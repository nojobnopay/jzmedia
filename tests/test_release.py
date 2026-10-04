"""Release safety: source identity, immutable deliveries and all-or-nothing promotion."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("release_tools", SCRIPTS / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
COMMIT = "a" * 40


def artifact(directory):
    directory.mkdir(parents=True, exist_ok=True)
    apk = directory / "jzmedia-tv-0.20.0-debug.apk"
    write_apk(apk, COMMIT)
    record = {"file": apk.name, "versionName": "0.20.0-debug", "versionCode": 11,
              "applicationId": "org.jzmedia.tv.debug", "variant": "debug", "unsigned": False,
              "sha256": release.sha256(apk), "source": {"commit": COMMIT, "dirty": False},
              "signing": {"verified": True, "certificateSha256": "b" * 64, "debugCertificate": True}}
    apk.with_suffix(".apk.json").write_text(json.dumps(record))
    apk.with_suffix(".apk.sha256").write_text(f"{record['sha256']}  {apk.name}\n")
    return apk, record


def write_apk(apk, commit):
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/jzmedia-build.json", json.dumps({
            "versionName": "0.20.0", "versionCode": 11, "source": {"commit": commit, "dirty": False},
        }))


def test_recomputed_sidecars_cannot_relabel_apk_from_other_source(tmp_path):
    apk, record = artifact(tmp_path)
    write_apk(apk, "c" * 40)
    record["sha256"] = release.sha256(apk)
    apk.with_suffix(".apk.json").write_text(json.dumps(record))
    apk.with_suffix(".apk.sha256").write_text(f"{record['sha256']}  {apk.name}\n")
    with pytest.raises(release.ReleaseError, match="embedded identity"):
        release.verify_apk(tmp_path, "0.20.0", 11, COMMIT, "debug")


@pytest.mark.parametrize("change", ["bytes", "commit", "dirty", "version", "unsigned", "signature", "checksum"])
def test_delivery_rejects_artifact_or_identity_drift(tmp_path, change):
    apk, record = artifact(tmp_path)
    assert release.verify_apk(tmp_path, "0.20.0", 11, COMMIT, "debug") == record
    if change == "bytes":
        apk.write_bytes(b"different apk")
    elif change == "checksum":
        apk.with_suffix(".apk.sha256").write_text("wrong")
    else:
        if change == "commit":
            record["source"]["commit"] = "c" * 40
        elif change == "dirty":
            record["source"]["dirty"] = True
        elif change == "version":
            record["versionName"] = "0.21.0-debug"
        elif change == "unsigned":
            record["unsigned"] = True
        else:
            record["signing"]["verified"] = False
        apk.with_suffix(".apk.json").write_text(json.dumps(record))
    with pytest.raises(release.ReleaseError):
        release.verify_apk(tmp_path, "0.20.0", 11, COMMIT, "debug")


def test_clean_source_required_and_head_must_not_move(monkeypatch, tmp_path):
    def command(args, **kwargs):
        return COMMIT if "rev-parse" in args else " M app/main.py"
    monkeypatch.setattr(release, "command", command)
    with pytest.raises(release.ReleaseError, match="working tree"):
        release.clean_commit(tmp_path)
    monkeypatch.setattr(release, "command", lambda args, **kw: COMMIT if "rev-parse" in args else "")
    with pytest.raises(release.ReleaseError, match="HEAD changed"):
        release.clean_commit(tmp_path, "b" * 40)


def test_history_requires_monotonic_android_code_and_version(tmp_path):
    history = tmp_path / "v0.20.0"
    history.mkdir()
    (history / "manifest.json").write_text(json.dumps({"version": "0.20.0", "androidVersionCode": 11}))
    for version, code in (("0.20.1", 10), ("0.20.1", 11), ("0.19.0", 12), ("0.20.0", 12)):
        with pytest.raises(release.ReleaseError):
            release.check_history(tmp_path, version, code)
    release.check_history(tmp_path, "0.20.1", 12)


def test_release_lock_excludes_second_writer_and_is_removed(tmp_path):
    with release.release_lock(tmp_path):
        with pytest.raises(release.ReleaseError, match="lock"):
            with release.release_lock(tmp_path):
                pytest.fail("A concurrent writer acquired the lock")
    assert not (tmp_path / ".release.lock").exists()


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_promotion_rolls_back_after_latest_failure(tmp_path, monkeypatch, failure):
    staging, destination = tmp_path / "staging", tmp_path / "v0.20.0"
    staging.mkdir()
    (staging / "manifest.json").write_text("{}")
    images = {"jzmedia:latest": {"Id": "old"}}
    tags = {}
    monkeypatch.setattr(release, "clean_commit", lambda *a: COMMIT)
    monkeypatch.setattr(release, "image_info", lambda ref: images.get(ref))
    monkeypatch.setattr(release, "git_tag_commit", lambda root, tag: tags.get(tag))

    def command(args, **kwargs):
        if args[:3] == ["docker", "image", "tag"]:
            images[args[4]] = {"Id": args[3]}
            if args[3] == "candidate" and args[4] == "jzmedia:latest":
                raise failure("simulated interruption after Docker applied the tag")
        elif args[:3] == ["docker", "image", "rm"]:
            images.pop(args[3], None)
        elif args[:3] == ["git", "tag", "-a"]:
            tags[args[3]] = args[-1]
        elif args[:3] == ["git", "tag", "-d"]:
            tags.pop(args[3], None)
        return ""

    monkeypatch.setattr(release, "command", command)
    record = {"version": "0.20.0", "commit": COMMIT,
              "docker": {"image": {"id": "candidate"}}, "android": {"sha256": "d" * 64}}
    with pytest.raises(failure):
        release.promote(tmp_path, staging, destination, "candidate", record)
    assert staging.exists() and not destination.exists()
    assert images == {"jzmedia:latest": {"Id": "old"}}
    assert tags == {}


def test_existing_version_image_never_overwritten(tmp_path, monkeypatch):
    staging = tmp_path / "stage"
    staging.mkdir()
    monkeypatch.setattr(release, "clean_commit", lambda *a: COMMIT)
    monkeypatch.setattr(release, "image_info", lambda ref: {"Id": "previous"})
    monkeypatch.setattr(release, "command", lambda *a, **kw: pytest.fail("Must reject before mutation"))
    with pytest.raises(release.ReleaseError, match="refusing to overwrite"):
        release.promote(tmp_path, staging, tmp_path / "final", "candidate", {"version": "0.20.0", "commit": COMMIT})


def test_second_build_failure_does_not_promote_first_artifact(tmp_path, monkeypatch):
    interpreter = tmp_path / "python"
    interpreter.touch()
    monkeypatch.setattr(release, "check_versions", lambda *a: SimpleNamespace(name="0.20.0", code=11))
    monkeypatch.setattr(release, "clean_commit", lambda *a: COMMIT)
    monkeypatch.setattr(release, "check_tag_history", lambda *a: None)
    monkeypatch.setattr(release, "git_tag_commit", lambda *a: None)
    monkeypatch.setattr(release, "image_info", lambda *a: None)
    monkeypatch.setattr(release, "android_environment", lambda *a: {})
    monkeypatch.setattr(release, "snapshot", lambda root, commit, directory: directory)
    monkeypatch.setattr(release, "run_checks", lambda *a: [])
    monkeypatch.setattr(release, "build_docker", lambda *a: {"id": "built image"})
    monkeypatch.setattr(release, "promote", lambda *a: pytest.fail("Partial release was promoted"))

    def fail_android(*args):
        raise release.ReleaseError("Android compiler failed")
    monkeypatch.setattr(release, "build_android", fail_android)
    commands = []
    monkeypatch.setattr(release, "command", lambda args, **kw: commands.append(args) or "")
    args = SimpleNamespace(python=str(interpreter), output=str(tmp_path / "output"),
                           apk_variant="debug", offline=True, build_proxy="")
    with pytest.raises(release.ReleaseError, match="Android compiler failed"):
        release.build(args, root=tmp_path)
    assert not (tmp_path / "output/v0.20.0").exists()
    assert all(args[:3] != ["docker", "image", "tag"] for args in commands)


@pytest.fixture
def validation_build(tmp_path, monkeypatch):
    """Keep an existing delivery and its tags while faking only expensive build tools."""
    interpreter = tmp_path / "python"
    interpreter.touch()
    previous = tmp_path / "output/releases/v0.20.0"
    previous.mkdir(parents=True)
    (previous / "manifest.json").write_text(json.dumps({
        "version": "0.20.0", "androidVersionCode": 11, "commit": "c" * 40,
    }))
    images = {"jzmedia:v0.20.0": {"Id": "old-version"}, "jzmedia:latest": {"Id": "old-latest"}}
    calls = []
    monkeypatch.setattr(release, "check_versions", lambda *a: SimpleNamespace(name="0.20.0", code=11))
    monkeypatch.setattr(release, "clean_commit", lambda *a: calls.append(("clean", a)) or COMMIT)
    monkeypatch.setattr(release, "check_tag_history", lambda *a: calls.append(("history", a)))
    monkeypatch.setattr(release, "git_tag_commit", lambda *a: "c" * 40)
    monkeypatch.setattr(release, "image_info", images.get)
    monkeypatch.setattr(release, "android_environment", lambda *a: {})
    monkeypatch.setattr(release, "promote", lambda *a: pytest.fail("Validation must never promote artifacts"))

    def snapshot(root, commit, directory):
        source = directory / "source"
        source.mkdir()
        calls.append(("snapshot", (root, commit, source)))
        return source

    def checks(source, python, env, offline, log):
        calls.append(("checks", (source, python, offline)))
        log.write("checks passed\n")
        return [[str(python), "checks"]]

    def docker(source, candidate, version, commit, proxy, log):
        calls.append(("docker", (source, version, commit)))
        images[candidate] = {"Id": "candidate-image"}
        log.write_text("image and isolated smoke passed\n")
        return {"id": "candidate-image", "platform": "linux/amd64", "sizeBytes": 1}

    def android(source, staging, version, code, commit, variant, offline, env, log):
        calls.append(("android", (source, version, code, commit)))
        log.write_text("APK signature and identity passed\n")
        return artifact(staging)[1]

    def command(args, **kwargs):
        if args[:3] == ["docker", "image", "rm"]:
            assert args[3].startswith("jzmedia:build-")
            images.pop(args[3])
        else:
            assert args == ["docker", "info", "--format", "{{.OSType}}"]
        return ""

    for name, function in (("snapshot", snapshot), ("run_checks", checks), ("build_docker", docker),
                           ("build_android", android), ("command", command)):
        monkeypatch.setattr(release, name, function)
    args = SimpleNamespace(python=str(interpreter), output=None, apk_variant="debug",
                           offline=False, build_proxy="", validate_only=True)
    return SimpleNamespace(root=tmp_path, previous=previous, args=args, calls=calls, images=images)


@pytest.mark.parametrize("custom_output", [False, True])
def test_validation_build_checks_both_artifacts_without_changing_existing_release(validation_build, capsys, custom_output):
    state = validation_build
    expected_parent = state.root / ("custom-output" if custom_output else "output")
    if custom_output:
        state.args.output = str(expected_parent)
    previous = (state.previous / "manifest.json").read_bytes()
    images = dict(state.images)
    result = release.build(state.args, root=state.root)
    assert result.parent == expected_parent
    assert result.name.startswith(".validation-v0.20.0-")
    record = json.loads((result / "manifest.json").read_text())
    assert record["validationOnly"] is True
    assert record["gitTag"] is None
    assert record["docker"]["tags"] == []
    assert record["commit"] == record["android"]["source"]["commit"] == COMMIT
    assert record["checks"]
    calls = dict(state.calls)
    source = calls["snapshot"][2]
    assert calls["checks"][0] == calls["docker"][0] == calls["android"][0] == source
    assert calls["docker"][2] == calls["android"][3] == calls["snapshot"][1] == COMMIT
    assert calls["history"] == (state.root, "0.20.0", 11)
    assert [args for name, args in state.calls if name == "clean"] == [(state.root,), (state.root, COMMIT)]
    assert state.images == images
    assert (state.previous / "manifest.json").read_bytes() == previous
    assert not (state.root / "output/releases/.release.lock").exists()
    assert "nothing published or promoted" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["checks", "android", "source_changed"])
def test_failed_validation_preserves_existing_delivery_and_cleans_candidate(validation_build, monkeypatch, failure):
    state = validation_build
    images = dict(state.images)

    def fail(*args):
        raise release.ReleaseError(failure)

    if failure == "checks":
        monkeypatch.setattr(release, "run_checks", fail)
    elif failure == "android":
        monkeypatch.setattr(release, "build_android", fail)
    else:
        def clean(root, expected=None):
            return fail() if expected else COMMIT
        monkeypatch.setattr(release, "clean_commit", clean)
    with pytest.raises(release.ReleaseError, match=failure):
        release.build(state.args, root=state.root)
    assert state.images == images
    assert (state.previous / "manifest.json").is_file()
    assert not list((state.root / "output").glob(".validation-*/manifest.json"))
    assert not (state.root / "output/releases/.release.lock").exists()


def test_validation_still_rejects_android_upgrade_regression(validation_build, monkeypatch):
    state = validation_build
    monkeypatch.setattr(release, "check_versions", lambda *a: SimpleNamespace(name="0.20.0", code=10))
    with pytest.raises(release.ReleaseError, match="different Android versionCode"):
        release.build(state.args, root=state.root)
    assert all(name not in ("checks", "docker", "android") for name, args in state.calls)


def test_formal_build_still_rejects_different_source_for_existing_release(validation_build):
    state = validation_build
    state.args.validate_only = False
    with pytest.raises(release.ReleaseError, match="different source"):
        release.build(state.args, root=state.root)
    assert all(name not in ("checks", "docker", "android") for name, args in state.calls)


def test_cli_accepts_validation_only(monkeypatch):
    calls = []
    monkeypatch.setattr(release, "build", lambda args: calls.append(args))
    monkeypatch.setattr(release.sys, "version_info", (3, 12))
    release.main(["build", "--validate-only"])
    assert len(calls) == 1 and calls[0].validate_only is True
