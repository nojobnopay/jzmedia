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
