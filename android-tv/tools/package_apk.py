#!/usr/bin/env python3
"""Build and export a versioned APK without dates or feature-name suffixes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile


PROJECT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def read_version(project):
    values = {}
    for line in (project.parent / "version.properties").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key in values:
            raise ValueError(f"Duplicate version property: {key}")
        values[key] = value
    name = values.get("versionName", "")
    code = int(values.get("versionCode", "0"))
    if not VERSION.fullmatch(name) or not 1 <= code <= 2_100_000_000:
        raise ValueError("version.properties requires versionName=X.Y.Z and a positive versionCode")
    return name, code


def git_revision(project):
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True)
    except FileNotFoundError:
        # Building an unpacked source archive does not require a Git installation.
        return {"commit": None, "dirty": None}
    return {"commit": commit.stdout.strip() if commit.returncode == 0 else None,
            "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None}


def resolve_revision(project, commit=None):
    revision = git_revision(project)
    if commit is not None:
        if not COMMIT.fullmatch(commit):
            raise ValueError("--commit must be a full lowercase Git commit hash")
        if revision["commit"] is not None and revision["commit"] != commit:
            raise ValueError("--commit differs from the checked-out source")
        # The unified release builds a clean git archive without a .git directory.
        if revision["commit"] is None:
            return {"commit": commit, "dirty": False}
    return revision


def embedded_source(apk, name, code):
    try:
        with zipfile.ZipFile(apk) as archive:
            record = json.loads(archive.read("assets/jzmedia-build.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as error:
        raise ValueError("APK has no valid embedded build record; rebuild before exporting") from error
    if not isinstance(record, dict) or not isinstance(record.get("source"), dict):
        raise ValueError("APK has no valid embedded source record; rebuild before exporting")
    revision = record["source"]
    commit = revision.get("commit")
    dirty = revision.get("dirty")
    if (record.get("versionName") != name or record.get("versionCode") != code
            or (commit is not None and (not isinstance(commit, str) or not COMMIT.fullmatch(commit)))
            or (dirty is not None and type(dirty) is not bool)
            or set(revision) != {"commit", "dirty"}):
        raise ValueError("APK embedded build record differs from version.properties or has invalid provenance")
    return revision


def verify_signature(apk, apksigner=None):
    """Verify a signed APK and return its certificate digest, without reading a key."""
    if apksigner is None:
        import tv
        sdk = tv.sdk_location()
        if sdk is None:
            raise ValueError("Android SDK is required to verify the APK signature")
        build_tools = tv.sdk_requirements()[1]
        apksigner = sdk / "build-tools" / build_tools / ("apksigner.bat" if os.name == "nt" else "apksigner")
    result = subprocess.run([str(apksigner), "verify", "--print-certs", str(apk)],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError("APK signature verification failed: " + (result.stderr or result.stdout).strip())
    certificates = re.findall(r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$",
                              result.stdout, re.MULTILINE)
    if len(certificates) != 1:
        raise ValueError("Expected one APK signer with a SHA-256 certificate digest")
    return {"verified": True, "certificateSha256": certificates[0].lower(),
            "debugCertificate": bool(re.search(
                r"certificate DN:[^\r\n]*\bCN=Android Debug(?:,|\r?$)", result.stdout, re.MULTILINE))}


def check_delivered_versions(output, name, code):
    version = tuple(map(int, name.split(".")))
    for path in output.glob("jzmedia-tv-*.apk.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        previous = str(record.get("versionName", "")).removesuffix("-debug")
        previous_code = record.get("versionCode")
        if (not VERSION.fullmatch(previous) or type(previous_code) is not int
                or not 1 <= previous_code <= 2_100_000_000):
            raise ValueError(f"Invalid delivery record: {path.name}")
        if name == previous:
            if code != previous_code:
                raise ValueError("A new versionCode requires a new versionName")
        elif version <= tuple(map(int, previous.split("."))) or code <= previous_code:
            raise ValueError("A new delivery must increase both versionName and versionCode")


def export_apk(project, variant, output, revision=None):
    name, code = read_version(project)
    if variant not in ("debug", "release"):
        raise ValueError("variant must be debug or release")
    directory = project / "app/build/outputs/apk" / variant
    metadata = json.loads((directory / "output-metadata.json").read_text(encoding="utf-8"))
    expected_name = name + ("-debug" if variant == "debug" else "")
    expected_id = "org.jzmedia.tv" + (".debug" if variant == "debug" else "")
    elements = metadata.get("elements", [])
    if (metadata.get("variantName") != variant or metadata.get("applicationId") != expected_id
            or len(elements) != 1 or elements[0].get("filters")
            or elements[0].get("versionName") != expected_name or elements[0].get("versionCode") != code):
        raise ValueError("APK metadata differs from version.properties; rebuild before exporting")
    filename = elements[0]["outputFile"]
    if Path(filename).name != filename or not filename.endswith(".apk"):
        raise ValueError("Expected one APK in the build output directory")
    source = directory / filename
    provenance = embedded_source(source, name, code)
    if revision is not None and provenance != revision:
        raise ValueError("APK source differs from the requested source; rebuild before exporting")
    unsigned = filename.endswith("-unsigned.apk")
    if variant == "debug" and unsigned:
        raise ValueError("Cannot distribute an unsigned debug APK")
    signing = ({"verified": False, "certificateSha256": None, "debugCertificate": None}
               if unsigned else verify_signature(source))
    if variant == "release" and signing["debugCertificate"]:
        raise ValueError("A release APK must not use an Android debug signing certificate")
    suffix = "-debug" if variant == "debug" else "-unsigned" if unsigned else ""
    destination = output / f"jzmedia-tv-{name}{suffix}.apk"
    contents = source.read_bytes()
    digest = hashlib.sha256(contents).hexdigest()
    check_delivered_versions(output, name, code)
    details = {"file": destination.name, "versionName": expected_name, "versionCode": code,
               "applicationId": expected_id, "variant": variant, "unsigned": unsigned, "sha256": digest,
               "source": provenance, "signing": signing}
    manifest = destination.with_suffix(".apk.json")
    if manifest.exists():
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
        if any(recorded.get(key) != value for key, value in details.items()):
            raise ValueError(f"Existing delivery record disagrees with the APK: {manifest.name}")
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        raise ValueError(f"{destination.name} already contains a different build; increment versionName and versionCode")
    output.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        # Exclusive creation prevents concurrent exports from replacing an APK.
        with destination.open("xb") as stream:
            stream.write(contents)
    destination.with_suffix(".apk.sha256").write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    if not manifest.exists():
        with manifest.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({
                **details, "exportedAt": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False, indent=2) + "\n")
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", choices=("debug", "release"))
    parser.add_argument("--offline", action="store_true", help="Build using cached Gradle dependencies")
    parser.add_argument("--output", type=Path, default=PROJECT.parent / "output/android-tv",
                        help="Directory for the APK, checksum and delivery JSON")
    parser.add_argument("--commit", help="Full commit hash (also passed to Gradle for source archives)")
    args = parser.parse_args(argv)
    version = read_version(PROJECT)
    revision = resolve_revision(PROJECT, args.commit)
    command = [str(PROJECT / ("gradlew.bat" if os.name == "nt" else "gradlew"))]
    if args.offline:
        command.append("--offline")
    command.append(f"-PjzmediaGitCommit={revision['commit'] or 'unknown'}")
    dirty = str(revision["dirty"]).lower() if revision["dirty"] is not None else "unknown"
    command.append(f"-PjzmediaGitDirty={dirty}")
    command.append(f":app:assemble{args.variant.title()}")
    subprocess.run(command, cwd=PROJECT, check=True)
    if resolve_revision(PROJECT, args.commit) != revision or read_version(PROJECT) != version:
        raise ValueError("Source or version changed during the build; rebuild before exporting")
    artifact = export_apk(PROJECT, args.variant, args.output, revision)
    print(artifact)
    if artifact.name.endswith("-unsigned.apk"):
        print("Unsigned build: sign and verify it before installation or release.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
