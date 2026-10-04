#!/usr/bin/env python3
"""Build a matching Docker image and Android APK from one clean Git commit (Python 3.12+)."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
import zipfile

from versioning import check_versions, set_version

ROOT = Path(__file__).resolve().parents[1]


class ReleaseError(ValueError):
    """A release prerequisite or identity check failed."""


def command(args, *, cwd=ROOT, env=None, log=None, input=None):
    args = [str(arg) for arg in args]
    result = subprocess.run(args, cwd=cwd, env=env, input=input, text=True,
                            stdout=log or subprocess.PIPE, stderr=log or subprocess.PIPE)
    if result.returncode:
        # Build logs can contain proxy configuration; do not dump them automatically.
        detail = f"; see {log.name}" if log else f": {(result.stderr or '').strip()[-1500:]}"
        raise ReleaseError(f"{Path(args[0]).name} failed ({result.returncode}){detail}")
    return (result.stdout or "").strip()


def clean_commit(root, expected=None):
    commit = command(["git", "rev-parse", "HEAD"], cwd=root)
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise ReleaseError("A Git checkout with a full commit ID is required")
    if command(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root):
        raise ReleaseError("Commit all source changes before release; the working tree must be clean")
    if expected and commit != expected:
        raise ReleaseError("HEAD changed during release; no release tags were promoted")
    return commit


def image_info(reference):
    result = subprocess.run(["docker", "image", "inspect", reference],
                            capture_output=True, text=True)
    if result.returncode:
        # Distinguish a missing image from an unavailable engine.
        command(["docker", "info", "--format", "{{.OSType}}"])
        if "No such image" not in result.stderr and "No such object" not in result.stderr:
            raise ReleaseError(result.stderr.strip())
        return None
    return json.loads(result.stdout)[0]


def verify_image(info, version, commit):
    labels = info.get("Config", {}).get("Labels") or {}
    if (labels.get("org.opencontainers.image.version") != version
            or labels.get("org.opencontainers.image.revision") != commit):
        raise ReleaseError("Docker image version/commit does not match this release")
    return {"id": info["Id"], "platform": f"{info['Os']}/{info['Architecture']}",
            "sizeBytes": info["Size"]}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_apk(directory, version, code, commit, variant):
    records = list(directory.glob("*.apk.json"))
    if len(records) != 1:
        raise ReleaseError("Expected exactly one APK delivery record")
    record = json.loads(records[0].read_text(encoding="utf-8"))
    filename = record.get("file", "")
    if Path(filename).name != filename or not filename.endswith(".apk"):
        raise ReleaseError("Invalid APK filename in delivery record")
    apk = directory / filename
    expected = version + ("-debug" if variant == "debug" else "")
    signing = record.get("signing", {})
    if (record.get("versionName") != expected or record.get("versionCode") != code
            or record.get("variant") != variant or record.get("unsigned") is not False
            or record.get("applicationId") != "org.jzmedia.tv" + (".debug" if variant == "debug" else "")
            or signing.get("verified") is not True
            or not re.fullmatch(r"[0-9a-f]{64}", signing.get("certificateSha256", ""))
            or (variant == "release" and signing.get("debugCertificate") is not False)
            or record.get("source", {}).get("commit") != commit
            or record.get("source", {}).get("dirty") is not False
            or record.get("sha256") != sha256(apk)):
        raise ReleaseError("APK version, signature status, source or SHA-256 differs from this release")
    checksum = apk.with_suffix(".apk.sha256").read_text(encoding="utf-8").strip()
    if checksum != f"{record['sha256']}  {filename}":
        raise ReleaseError("APK checksum file differs from delivery record")
    try:
        with zipfile.ZipFile(apk) as archive:
            embedded = json.loads(archive.read("assets/jzmedia-build.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as error:
        raise ReleaseError("APK has no valid embedded build identity") from error
    if (embedded.get("versionName") != version or embedded.get("versionCode") != code
            or embedded.get("source") != {"commit": commit, "dirty": False}):
        raise ReleaseError("APK embedded identity differs from its delivery record")
    return record


def check_history(output, version, code):
    """The new staging directory alone cannot enforce global Android upgrade ordering."""
    for path in output.glob("v*/manifest.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        previous = record.get("version", "")
        previous_code = record.get("androidVersionCode")
        if (not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", previous)
                or type(previous_code) is not int or previous_code < 1):
            raise ReleaseError(f"Invalid release history: {path}")
        if previous == version:
            if code != previous_code:
                raise ReleaseError("An existing version cannot use a different Android versionCode")
        elif tuple(map(int, version.split("."))) <= tuple(map(int, previous.split("."))) or code <= previous_code:
            raise ReleaseError("Both version and Android versionCode must exceed previous releases")


def check_tag_history(root, version, code):
    """Local Git tags keep upgrade ordering even if output/ has been archived away."""
    tags = command(["git", "tag", "--list"], cwd=root).splitlines()
    for tag in tags:
        match = re.fullmatch(r"(android-tv-)?v((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))", tag)
        if not match:
            continue
        old_version = match[2]
        if not match[1] and tuple(map(int, old_version.split("."))) > tuple(map(int, version.split("."))):
            raise ReleaseError(f"Version must not precede existing Git tag {tag}")
        for path in ("version.properties", "android-tv/version.properties"):
            result = subprocess.run(["git", "show", f"refs/tags/{tag}:{path}"], cwd=root,
                                    capture_output=True, text=True)
            if result.returncode == 0:
                previous = re.search(r"^versionCode=(\d+)$", result.stdout, re.MULTILINE)
                if previous:
                    minimum = int(previous[1]) + (0 if not match[1] and old_version == version else 1)
                    if code < minimum:
                        raise ReleaseError(f"Android versionCode must advance past Git tag {tag}")
                break


def existing_release(directory, version, code, commit, variant):
    if not directory.exists():
        return None
    path = directory / "manifest.json"
    if not path.is_file():
        raise ReleaseError(f"Incomplete existing release: {directory}; do not overwrite it")
    record = json.loads(path.read_text(encoding="utf-8"))
    if (record.get("version") != version or record.get("androidVersionCode") != code
            or record.get("commit") != commit or record.get("apkVariant") != variant):
        raise ReleaseError("This release version already belongs to different source or APK variant; bump version")
    apk = verify_apk(directory, version, code, commit, variant)
    if apk != record.get("android"):
        raise ReleaseError("Existing APK record has changed")
    info = image_info(f"jzmedia:v{version}")
    if info is None or verify_image(info, version, commit) != record["docker"]["image"]:
        raise ReleaseError("Existing release image is missing or changed; restore it rather than overwrite the release")
    return record


@contextmanager
def release_lock(output):
    output.mkdir(parents=True, exist_ok=True)
    lock = output / ".release.lock"
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise ReleaseError(f"Release lock exists: {lock}; check for a running release before removing it") from error
    try:
        (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")
        yield
    finally:
        shutil.rmtree(lock)


def snapshot(root, commit, directory):
    archive = directory / "source.tar"
    source = directory / "source"
    command(["git", "archive", "--format=tar", f"--output={archive}", commit], cwd=root)
    source.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(source, filter="data")
    archive.unlink()
    return source


def android_environment(root):
    spec = importlib.util.spec_from_file_location("release_tv", root / "android-tv/tools/tv.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_environment()


def run_checks(source, python, env, offline, log):
    steps = [
        [python, "-m", "pytest", "-q"],
        [python, "scripts/check_python.py", "scripts/release.py", "scripts/versioning.py", "scripts/check_python.py"],
        [python, "scripts/build_design.py", "--check"],
        ["npm", "--prefix", "frontend", "ci", "--prefer-offline", "--no-audit", "--no-fund"],
        ["npm", "--prefix", "frontend", "test"],
        ["npm", "--prefix", "frontend", "run", "lint"],
        ["npm", "--prefix", "docs", "ci", "--prefer-offline", "--no-audit", "--no-fund"],
        ["npm", "--prefix", "docs", "run", "verify"],
        [python, "android-tv/tools/tv.py", "test", *(["--offline"] if offline else [])],
    ]
    for step in steps:
        print("Check: " + " ".join(str(part) for part in step), flush=True)
        log.write("\n$ " + " ".join(str(part) for part in step) + "\n")
        log.flush()
        command(step, cwd=source, env=env, log=log)
    return [list(map(str, step)) for step in steps]


IMAGE_CHECK = r'''
import json, re, sys, time, urllib.request
from pathlib import Path
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path):
    with opener.open('http://127.0.0.1:8080' + path, timeout=5) as r:
        assert r.status == 200
        return r.read(), r.headers
for attempt in range(60):
    try:
        health = json.loads(get('/api/health')[0])
        break
    except OSError:
        time.sleep(.5)
else:
    raise RuntimeError('Application did not start')
assert health['status'] == 'ok' and health['disks']['ok'], health
assert health['ffmpeg'] and health['ffprobe'], health
assert health['version'] == sys.argv[1] and health['build_commit'] == sys.argv[2], health
assert json.loads(get('/openapi.json')[0])['info']['version'] == sys.argv[1]
for page in ('/', '/help/'):
    body, headers = get(page)
    assert 'text/html' in headers.get('Content-Type', '')
    assets = re.findall(r'(?:src|href)="(/(?:help/)?assets/[^\"]+)"', body.decode())
    assert assets, page
    for asset in assets:
        content, headers = get(asset)
        assert content and 'text/html' not in headers.get('Content-Type', '')
assert json.loads(Path('/app/docs/.vitepress/dist/csp-hashes.json').read_text())['scriptHashes']
print(json.dumps({'health': 'ok', 'version': sys.argv[1], 'commit': sys.argv[2]}))
'''


def smoke_image(reference, version, commit, log):
    name = "jzmedia-release-smoke-" + uuid.uuid4().hex[:12]
    command(["docker", "run", "--rm", "-d", "--name", name, "--network", "none", "--init",
             "--tmpfs", "/tmp:rw,size=128m", "-e", "DATA_DIR=/tmp/release/data",
             "-e", "MEDIA_ROOT=/tmp/release/media", "-e", "ALLOW_SMB_MOUNT=0",
             "-e", "TRANSCODER=sw", reference], log=log)
    try:
        command(["docker", "exec", name, "python", "-c", IMAGE_CHECK, version, commit], log=log)
    finally:
        command(["docker", "stop", "--timeout", "10", name], log=log)


def build_docker(source, candidate, version, commit, proxy, log_path):
    print("Building Docker image…", flush=True)
    args = ["docker", "build", "--tag", candidate, "--build-arg", f"APP_VERSION={version}",
            "--build-arg", f"GIT_SHA={commit}"]
    if proxy:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            args += ["--build-arg", f"{key}={proxy}"]
    with log_path.open("w", encoding="utf-8") as log:
        command([*args, "."], cwd=source, log=log)
        info = image_info(candidate)
        result = verify_image(info, version, commit)
        smoke_image(candidate, version, commit, log)
    print("Docker build and isolated startup check passed.", flush=True)
    return result


def build_android(source, output, version, code, commit, variant, offline, env, log_path):
    print("Building Android APK…", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        command([sys.executable, "android-tv/tools/package_apk.py", variant,
                 "--output", output, "--commit", commit, *(["--offline"] if offline else [])],
                cwd=source, env=env, log=log)
    result = verify_apk(output, version, code, commit, variant)
    print("Android build and delivery identity check passed.", flush=True)
    return result


def git_tag_commit(root, tag):
    result = subprocess.run(["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
                            cwd=root, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def promote(root, staging, destination, candidate, record):
    """Only called after both builds and identity checks succeed; roll back our new refs on failure."""
    commit, version = record["commit"], record["version"]
    clean_commit(root, commit)
    tag = f"v{version}"
    version_ref = f"jzmedia:{tag}"
    if destination.exists() or image_info(version_ref) is not None:
        raise ReleaseError("Release destination or version image appeared during build; refusing to overwrite")
    old_tag = git_tag_commit(root, tag)
    if old_tag and old_tag != commit:
        raise ReleaseError(f"Git tag {tag} already identifies another commit")
    previous_latest = image_info("jzmedia:latest")
    made_image = made_tag = moved = latest_attempted = False
    try:
        made_image = True
        command(["docker", "image", "tag", candidate, version_ref])
        if old_tag is None:
            message = staging / "tag-message.txt"
            message.write_text(f"jzmedia {version}\n\nDocker: {record['docker']['image']['id']}\n"
                               f"APK SHA-256: {record['android']['sha256']}\n", encoding="utf-8")
            made_tag = True
            command(["git", "tag", "-a", tag, "--file", message, commit], cwd=root)
            message.unlink()
        moved = True
        staging.rename(destination)
        # Last promotion action. Until this succeeds the user's latest image is untouched.
        latest_attempted = True
        command(["docker", "image", "tag", candidate, "jzmedia:latest"])
    except BaseException:
        actions = []
        if latest_attempted:
            actions.append(lambda: command(["docker", "image", "tag", previous_latest["Id"], "jzmedia:latest"])
                           if previous_latest else command(["docker", "image", "rm", "jzmedia:latest"]))
        if moved and destination.exists() and not staging.exists():
            actions.append(lambda: destination.rename(staging))
        if made_tag and git_tag_commit(root, tag) == commit:
            actions.append(lambda: command(["git", "tag", "-d", tag], cwd=root))
        if made_image and image_info(version_ref) is not None:
            actions.append(lambda: command(["docker", "image", "rm", version_ref]))
        for action in actions:
            try:
                action()
            except BaseException as error:
                print(f"Release rollback needs attention: {error}", file=sys.stderr)
        raise


def build(args, root=ROOT):
    version = check_versions(root)
    commit = clean_commit(root)
    output = Path(args.output).resolve() if args.output else root / "output/releases"
    destination = output / f"v{version.name}"
    python = Path(args.python).resolve() if args.python else root / ".venv/bin/python"
    if not python.is_file():
        raise ReleaseError("Validation Python is missing; install requirements-dev.txt and use --python PATH")
    command(["docker", "info", "--format", "{{.OSType}}"])
    # Different --output locations still publish the same repository/image tags.
    with release_lock(root / "output/releases"):
        output.mkdir(parents=True, exist_ok=True)
        check_history(output, version.name, version.code)
        check_tag_history(root, version.name, version.code)
        existing = existing_release(destination, version.name, version.code, commit, args.apk_variant)
        if existing:
            if git_tag_commit(root, f"v{version.name}") != commit:
                raise ReleaseError("Existing release Git tag is missing or changed")
            print(f"Verified existing release: {destination}; no rebuild or latest movement.")
            return destination
        if image_info(f"jzmedia:v{version.name}") is not None:
            raise ReleaseError("Version image already exists without this release record; bump the version")
        prior = git_tag_commit(root, f"v{version.name}")
        if prior and prior != commit:
            raise ReleaseError("Release Git tag already points to different source; bump the version")
        env = android_environment(root)
        if args.apk_variant == "release":
            keys = ("JZMEDIA_ANDROID_KEYSTORE", "JZMEDIA_ANDROID_STORE_PASSWORD",
                    "JZMEDIA_ANDROID_KEY_ALIAS", "JZMEDIA_ANDROID_KEY_PASSWORD")
            if not all(env.get(key) for key in keys):
                raise ReleaseError("Signed release APK requires all JZMEDIA_ANDROID_* signing settings; use debug for trials")
            if not Path(env["JZMEDIA_ANDROID_KEYSTORE"]).is_absolute():
                raise ReleaseError("JZMEDIA_ANDROID_KEYSTORE must be an absolute path outside the source snapshot")
        staging = output / f".staging-v{version.name}-{uuid.uuid4().hex[:8]}"
        staging.mkdir()
        logs = staging / "logs"
        logs.mkdir()
        candidate = f"jzmedia:build-{commit[:12]}-{uuid.uuid4().hex[:8]}"
        print(f"Release {version.name}, Android code {version.code}, commit {commit}\nLogs: {logs}", flush=True)
        try:
            with tempfile.TemporaryDirectory(prefix="jzmedia-release-") as temporary:
                source = snapshot(root, commit, Path(temporary))
                check_versions(source)
                with (logs / "checks.log").open("w", encoding="utf-8") as log:
                    checks = run_checks(source, python, env, args.offline, log)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    docker = pool.submit(build_docker, source, candidate, version.name, commit,
                                         args.build_proxy, logs / "docker.log")
                    android = pool.submit(build_android, source, staging, version.name, version.code,
                                          commit, args.apk_variant, args.offline, env, logs / "android.log")
                    # Wait for both even when one fails; neither may outlive its source snapshot.
                    image, apk = docker.result(), android.result()
                record = {"schema": 1, "version": version.name, "androidVersionCode": version.code,
                          "commit": commit, "gitTag": f"v{version.name}", "apkVariant": args.apk_variant,
                          "createdAt": datetime.now(timezone.utc).isoformat(),
                          "docker": {"tags": [f"jzmedia:v{version.name}", "jzmedia:latest"], "image": image},
                          "android": apk, "checks": checks}
                (staging / "manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                                                        encoding="utf-8")
                promote(root, staging, destination, candidate, record)
        except BaseException:
            print(f"Release incomplete; logs kept at {staging}. Check any rollback errors before retrying.", file=sys.stderr)
            raise
        finally:
            # Only remove our transient tag; never prune other images or build caches.
            try:
                if image_info(candidate) is not None:
                    command(["docker", "image", "rm", candidate])
            except (ReleaseError, OSError) as error:
                print(f"Temporary image tag cleanup failed ({candidate}): {error}", file=sys.stderr)
        print(f"Release complete: {destination}", flush=True)
        return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Check the canonical version and all mirrored package versions")
    version = commands.add_parser("version", help="Advance the unified version and Android versionCode")
    version.add_argument("name", help="X.Y.Z")
    version.add_argument("--android-code", type=int, required=True)
    release = commands.add_parser("build", help="Validate, build both artifacts and record one local release")
    release.add_argument("--apk-variant", choices=("debug", "release"), default="debug")
    release.add_argument("--offline", action="store_true", help="Use cached Android/Gradle dependencies")
    release.add_argument("--build-proxy", default=os.environ.get("BUILD_HTTP_PROXY", ""),
                         help="Proxy for Docker dependency downloads only; .env is not loaded")
    release.add_argument("--python", help="Python with requirements-dev.txt installed (default: .venv/bin/python)")
    release.add_argument("--output", help="Release root (default: output/releases)")
    args = parser.parse_args(argv)
    if args.command == "check":
        value = check_versions(ROOT)
        print(f"Version {value.name}, Android versionCode {value.code}: consistent")
    elif args.command == "version":
        value = set_version(ROOT, args.name, args.android_code)
        print(f"Version {value.name}, Android versionCode {value.code}; review and commit before building")
    else:
        if sys.version_info < (3, 12):
            raise ReleaseError("Unified release builds require Python 3.12+")
        build(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
