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


PROJECT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def read_version(project):
    values = {}
    for line in (project / "version.properties").read_text(encoding="utf-8").splitlines():
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
    unsigned = filename.endswith("-unsigned.apk")
    if variant == "debug" and unsigned:
        raise ValueError("Cannot distribute an unsigned debug APK")
    suffix = "-debug" if variant == "debug" else "-unsigned" if unsigned else ""
    destination = output / f"jzmedia-tv-{name}{suffix}.apk"
    contents = source.read_bytes()
    digest = hashlib.sha256(contents).hexdigest()
    check_delivered_versions(output, name, code)
    details = {"file": destination.name, "versionName": expected_name, "versionCode": code,
               "applicationId": expected_id, "variant": variant, "unsigned": unsigned, "sha256": digest}
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
        manifest.write_text(json.dumps({
            **details, "exportedAt": datetime.now(timezone.utc).isoformat(),
            "source": revision or {},
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", choices=("debug", "release"))
    parser.add_argument("--offline", action="store_true", help="Build using cached Gradle dependencies")
    args = parser.parse_args()
    read_version(PROJECT)
    command = [str(PROJECT / ("gradlew.bat" if os.name == "nt" else "gradlew"))]
    if args.offline:
        command.append("--offline")
    command.append(f":app:assemble{args.variant.title()}")
    subprocess.run(command, cwd=PROJECT, check=True)
    artifact = export_apk(PROJECT, args.variant, PROJECT.parent / "output/android-tv", git_revision(PROJECT))
    print(artifact)
    if artifact.name.endswith("-unsigned.apk"):
        print("Unsigned build: sign and verify it before installation or release.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
