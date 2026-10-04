#!/usr/bin/env python3
"""Select and recheck immutable release source for the GitHub Pages help site."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import github_release as github

ROOT = Path(__file__).resolve().parents[1]
IDENTITY_FIELDS = ("tag", "commit", "version_code", "release_id", "manifest_sha256")


def latest_release(repo):
    candidates = [item for item in github.releases(repo)
                  if not item["draft"] and not item["prerelease"]
                  and github.VERSION_TAG.fullmatch(item["tag_name"])]
    if not candidates:
        raise github.release.ReleaseError("Publish a formal vX.Y.Z release before deploying online help")
    newest = max(candidates, key=lambda item: tuple(map(int, item["tag_name"][1:].split("."))))
    if sum(item["tag_name"] == newest["tag_name"] for item in candidates) != 1:
        raise github.release.ReleaseError("Multiple releases claim the latest version")
    latest = github.api(f"repos/{repo}/releases/latest")
    if latest["id"] != newest["id"]:
        raise github.release.ReleaseError("GitHub latest does not identify the newest formal version")
    return newest


def release_identity(repo, record):
    tag = record["tag_name"]
    manifests = [item for item in record["assets"] if item["name"] == "manifest.json"]
    if len(manifests) != 1 or manifests[0]["state"] != "uploaded":
        raise github.release.ReleaseError("Published release needs one complete manifest.json")
    raw = github.release.command([
        "gh", "api", f"repos/{repo}/releases/assets/{manifests[0]['id']}",
        "-H", "Accept: application/octet-stream"])
    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or manifest.get("validationOnly")
            or manifest.get("gitTag") != tag or manifest.get("version") != tag[1:]
            or not isinstance(manifest.get("commit"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", manifest["commit"])
            or type(manifest.get("androidVersionCode")) is not int
            or manifest["androidVersionCode"] <= 0):
        raise github.release.ReleaseError("Release manifest has invalid source or version identity")
    github.check_remote_tag(repo, tag, manifest["commit"])
    return {"tag": tag, "commit": manifest["commit"],
            "version_code": str(manifest["androidVersionCode"]), "release_id": str(record["id"]),
            "manifest_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}


def current_identity(repo, tag=""):
    record = latest_release(repo)
    if tag and record["tag_name"] != tag:
        current = tuple(map(int, record["tag_name"][1:].split(".")))
        requested = tuple(map(int, tag[1:].split(".")))
        if current > requested:
            print(f"Skipping {tag}: {record['tag_name']} is already published")
            return None
        raise github.release.ReleaseError("Requested version is not the latest published release")
    return release_identity(repo, record)


def emit(values):
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in values.items())
    print(json.dumps(values))
    return values


def select(tag=""):
    repo = github.repository()
    event = os.environ.get("GITHUB_EVENT_NAME")
    if event == "push":
        version, commit, event_tag = github.check_tag()
        if tag != event_tag:
            raise github.release.ReleaseError("Pages request must match the successful release tag")
    elif event == "workflow_dispatch":
        default_branch = github.api(f"repos/{repo}")["default_branch"]
        if tag or os.environ.get("GITHUB_REF") != f"refs/heads/{default_branch}":
            raise github.release.ReleaseError("Run manual online-help deployment from the default branch")
    else:
        raise github.release.ReleaseError("Only release tag publication and manual deployment are supported")
    identity = current_identity(repo, tag)
    if identity is None:
        return emit({"deploy": "false"})
    if event == "push" and (identity["commit"] != commit or identity["version_code"] != str(version.code)):
        raise github.release.ReleaseError("Published manifest differs from the successful release source")
    return emit({"deploy": "true", **identity})


def verify_source(identity, root=ROOT):
    github.release.clean_commit(root, identity["commit"])
    version = github.release.check_versions(root)
    if f"v{version.name}" != identity["tag"] or str(version.code) != identity["version_code"]:
        raise github.release.ReleaseError("Checked-out documentation version differs from the release manifest")
    if not (root / "docs/scripts/site-config.mjs").is_file():
        raise github.release.ReleaseError("This release predates online help; publish a version with Pages support")


def check_current(identity):
    current = current_identity(github.repository(), identity["tag"])
    if current is None:
        return emit({"deploy": "false"})
    if current != identity:
        raise github.release.ReleaseError("Release identity changed after documentation was built")
    return emit({"deploy": "true"})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    select_parser = commands.add_parser("select", help="Select the latest formal release for this event")
    select_parser.add_argument("--tag", default="", help="Successful release tag; omit for manual deployment")
    for name in ("verify-source", "check-current"):
        child = commands.add_parser(name)
        for field in IDENTITY_FIELDS:
            child.add_argument("--" + field.replace("_", "-"), required=True)
    args = parser.parse_args(argv)
    if args.command == "select":
        select(args.tag)
        return
    identity = {field: getattr(args, field) for field in IDENTITY_FIELDS}
    if not github.VERSION_TAG.fullmatch(identity["tag"]):
        raise github.release.ReleaseError("Expected a formal vX.Y.Z release tag")
    if args.command == "verify-source":
        verify_source(identity)
    else:
        check_current(identity)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
