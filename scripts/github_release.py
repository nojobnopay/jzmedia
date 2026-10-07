#!/usr/bin/env python3
"""Package and publish the already verified unified release from GitHub Actions."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time

import release

ROOT = Path(__file__).resolve().parents[1]
VERSION_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def repository():
    value = os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise release.ReleaseError("GITHUB_REPOSITORY must identify the destination repository")
    return value


def api(path):
    return json.loads(release.command(["gh", "api", path]))


def releases(repo):
    pages = json.loads(release.command(["gh", "api", f"repos/{repo}/releases", "--paginate", "--slurp"]))
    return [item for page in pages for item in page]


def find_release(repo, tag):
    # The by-tag REST endpoint does not return drafts.
    matches = [item for item in releases(repo) if item["tag_name"] == tag]
    if len(matches) > 1:
        raise release.ReleaseError("Multiple releases claim this version")
    return matches[0] if matches else None


def wait_for_release(repo, tag, attempts=12, delay=5.0):
    """创建/上传后重读：releases 列表接口有复制滞后，刚写入的草稿可能暂时查不到。

    取不到时退避重试，耗尽仍失败报清晰错误，而不是让下游在 None 上崩溃。
    首次发布前的“是否存在”检查不用它（不存在是合法状态）。
    """
    for _ in range(max(1, int(attempts))):
        existing = find_release(repo, tag)
        if existing is not None:
            return existing
        time.sleep(delay)
    raise release.ReleaseError(
        f"Release {tag} not visible after creation; refusing to continue without a verified draft")


def check_remote_tag(repo, tag, commit):
    value = api(f"repos/{repo}/git/ref/tags/{tag}")["object"]
    while value["type"] == "tag":
        value = api(f"repos/{repo}/git/tags/{value['sha']}")["object"]
    if value["type"] != "commit" or value["sha"] != commit:
        raise release.ReleaseError("Remote Git tag changed; refusing to publish artifacts from another commit")


def check_tag(root=ROOT):
    version = release.check_versions(root)
    commit = release.clean_commit(root)
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if (os.environ.get("GITHUB_REF_TYPE") != "tag" or not VERSION_TAG.fullmatch(tag)
            or tag != f"v{version.name}" or release.git_tag_commit(root, tag) != commit):
        raise release.ReleaseError("Push a vX.Y.Z tag matching version.properties and the checked-out commit")
    event_sha = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", event_sha):
        raise release.ReleaseError("Missing GitHub event commit")
    if release.command(["git", "rev-parse", f"{event_sha}^{{commit}}"], cwd=root) != commit:
        raise release.ReleaseError("Checked-out source differs from the GitHub event")
    return version, commit, tag


def preflight():
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        release.clean_commit(ROOT)
        version = release.check_versions(ROOT)
        mode = "validate"
    elif os.environ.get("GITHUB_EVENT_NAME") == "push":
        version, commit, tag = check_tag()
        existing = find_release(repository(), tag)
        mode = "build"
        if existing:
            if existing["draft"]:
                raise release.ReleaseError("A draft exists: re-run only the failed publish job to reuse its build artifact")
            assets = {item["name"]: item for item in existing["assets"]}
            if "manifest.json" not in assets:
                raise release.ReleaseError("Existing release has no identity manifest; refusing to replace it")
            manifest = json.loads(release.command([
                "gh", "api", f"repos/{repository()}/releases/assets/{assets['manifest.json']['id']}",
                "-H", "Accept: application/octet-stream"]))
            if (manifest.get("commit") != commit or manifest.get("version") != version.name
                    or manifest.get("androidVersionCode") != version.code):
                raise release.ReleaseError("Existing release belongs to different source or version metadata")
            mode = "complete"
        if mode == "build":
            release.check_tag_history(ROOT, version.name, version.code)
    else:
        raise release.ReleaseError("Only version tag pushes and manual validation are supported")
    values = {"mode": mode, "tag": f"v{version.name}"}
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in values.items())
    print(json.dumps(values))


def compose_for_registry(source, image):
    lines = source.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line == "    build:\n"), None)
    end = next((i for i, line in enumerate(lines) if line.startswith("    image: ")), None)
    if start is None or end is None or start >= end:
        raise release.ReleaseError("Compose build/image structure changed; review the release template")
    return "".join([*lines[:start], f"    image: {image}\n", *lines[end + 1:]])


def archive_name(tag):
    return f"jzmedia-{tag}-linux-amd64.tar.gz"


def asset_names(record):
    apk = record["android"]["file"]
    if Path(apk).name != apk or not apk.endswith(".apk"):
        raise release.ReleaseError("Invalid APK asset name")
    return [archive_name(record["gitTag"]), "docker-compose.yml", "LICENSE", "manifest.json",
            apk, apk + ".sha256", apk + ".json"]


def verify_bundle(directory, repo, tag, commit):
    record = json.loads((directory / "manifest.json").read_text())
    if (record.get("validationOnly") or record.get("gitTag") != tag
            or record.get("version") != tag.removeprefix("v") or record.get("commit") != commit
            or record.get("apkVariant") != "debug"):
        raise release.ReleaseError("Bundle does not identify this tag and source commit")
    apk = release.verify_apk(directory, record["version"], record["androidVersionCode"], commit, "debug")
    if apk != record.get("android"):
        raise release.ReleaseError("APK record differs from the unified manifest")
    expected_cert = os.environ.get("JZMEDIA_ANDROID_DEBUG_CERT_SHA256", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_cert) or apk["signing"]["certificateSha256"] != expected_cert:
        raise release.ReleaseError("APK certificate differs from the configured upgrade certificate")
    expected_names = asset_names(record)
    checksums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if name in checksums or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise release.ReleaseError("Invalid or duplicate checksum entry")
        checksums[name] = digest
    if set(checksums) != set(expected_names):
        raise release.ReleaseError("Bundle checksum inventory differs from expected release assets")
    for name, digest in checksums.items():
        if release.sha256(directory / name) != digest:
            raise release.ReleaseError(f"Release asset checksum mismatch: {name}")
    expected_image = f"ghcr.io/{repo.lower()}:{tag}"
    with tarfile.open(directory / archive_name(tag), "r:gz") as archive:
        manifests = json.load(archive.extractfile("manifest.json"))
        if len(manifests) != 1 or manifests[0].get("RepoTags") != [expected_image]:
            raise release.ReleaseError("Docker archive contains unexpected image tags")
        config_bytes = archive.extractfile(manifests[0]["Config"]).read()
        if "sha256:" + hashlib.sha256(config_bytes).hexdigest() != record["docker"]["image"]["id"]:
            raise release.ReleaseError("Docker archive config differs from the unified manifest")
        config = json.loads(config_bytes)
        labels = config.get("config", {}).get("Labels", {})
        if (config.get("os") != "linux" or config.get("architecture") != "amd64"
                or labels.get("org.opencontainers.image.version") != record["version"]
                or labels.get("org.opencontainers.image.revision") != commit):
            raise release.ReleaseError("Docker archive identity differs from this release")
    return record


def package(directory):
    version, commit, tag = check_tag()
    repo = repository()
    source = ROOT / "output/releases" / tag
    record = release.existing_release(source, version.name, version.code, commit, "debug")
    if record is None or record["docker"]["image"]["platform"] != "linux/amd64":
        raise release.ReleaseError("A complete linux/amd64 unified build is required")
    directory.mkdir(parents=True, exist_ok=False)
    image = f"ghcr.io/{repo.lower()}:{tag}"
    release.command(["docker", "tag", record["docker"]["image"]["id"], image])
    target = directory / archive_name(tag)
    process = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE)
    try:
        with target.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            shutil.copyfileobj(process.stdout, zipped, length=1024 * 1024)
        process.stdout.close()
        if process.wait():
            raise release.ReleaseError("docker save failed")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()
    for name in asset_names(record):
        if name in (target.name, "docker-compose.yml", "LICENSE"):
            continue
        shutil.copyfile(source / name, directory / name)
    shutil.copyfile(ROOT / "LICENSE", directory / "LICENSE")
    (directory / "docker-compose.yml").write_text(compose_for_registry(
        (ROOT / "docker-compose.yml").read_text(), image))
    (directory / "SHA256SUMS").write_text("".join(
        f"{release.sha256(directory / name)}  {name}\n" for name in asset_names(record)))
    verify_bundle(directory, repo, tag, commit)
    print(f"Verified publication bundle: {directory}")


def verify_assets(record, directory, *, complete=False):
    expected = [*asset_names(json.loads((directory / "manifest.json").read_text())), "SHA256SUMS"]
    assets = {item["name"]: item for item in record["assets"]}
    if set(assets) - set(expected) or (complete and set(assets) != set(expected)):
        raise release.ReleaseError("Release asset inventory differs; refusing to replace existing files")
    for name, asset in assets.items():
        if not complete and record["draft"] and asset["state"] == "starter" and asset["size"] == 0:
            continue
        digest = release.sha256(directory / name)
        if asset["state"] != "uploaded" or asset["size"] != (directory / name).stat().st_size:
            raise release.ReleaseError(f"Existing asset is incomplete or different: {name}")
        if asset.get("digest"):
            equal = asset["digest"] == f"sha256:{digest}"
        else:
            with tempfile.TemporaryDirectory() as temporary:
                release.command(["gh", "release", "download", record["tag_name"], "--repo", repository(),
                                 "--pattern", name, "--dir", temporary])
                equal = release.sha256(Path(temporary) / name) == digest
        if not equal:
            raise release.ReleaseError(f"Existing asset differs: {name}; never overwrite a published version")
    return [name for name in expected if name not in assets or assets[name]["state"] == "starter"]


def remote_manifest(image, env, *, missing_ok=False):
    result = subprocess.run(["docker", "manifest", "inspect", image], env=env, capture_output=True, text=True)
    if result.returncode:
        if missing_ok and any(word in result.stderr.lower() for word in
                              ("no such manifest", "manifest unknown", "name unknown")):
            return None
        raise release.ReleaseError(f"Cannot inspect registry manifest: {result.stderr.strip()[-1000:]}")
    return json.loads(result.stdout)


def check_remote_image(manifest, expected):
    if manifest is not None and manifest.get("config", {}).get("digest") != expected:
        raise release.ReleaseError("GHCR version tag already contains different image content; refusing overwrite")


def notes(record, repo):
    tag = record["gitTag"]
    return (f"## Docker\n\nLinux amd64 镜像：\n\n```bash\ndocker pull ghcr.io/{repo.lower()}:{tag}\n```\n\n"
            "下载附件 `docker-compose.yml`，按部署环境调整用户、端口与媒体／数据路径后启动。"
            "也可用镜像归档离线导入；两种方式使用相同镜像标签。旧 `/media` 实例升级前见"
            f"[部署教程](https://github.com/{repo}/blob/{tag}/docs/getting-started/deployment.md#legacy-media-path)。\n\n"
            "## Android TV\n\n附件 APK 是 Debug 试装包，使用固定签名；真机兼容性仍需验收。"
            f"Android versionCode：{record['androidVersionCode']}。\n\n"
            f"同提交构建：`{record['commit']}`。校验信息见 `SHA256SUMS` 与 `manifest.json`。\n")


def publish(directory):
    version, commit, tag = check_tag()
    repo = repository()
    record = verify_bundle(directory, repo, tag, commit)
    check_remote_tag(repo, tag, commit)
    if record["androidVersionCode"] != version.code:
        raise release.ReleaseError("Bundle Android versionCode differs from source")
    for item in releases(repo):
        name = item["tag_name"]
        if not item["draft"] and not item["prerelease"] and VERSION_TAG.fullmatch(name):
            if tuple(map(int, name[1:].split("."))) > version.parts:
                raise release.ReleaseError("A newer release exists; refusing to move latest backwards")
    existing = find_release(repo, tag)
    if existing:
        verify_assets(existing, directory, complete=not existing["draft"])
    image = f"ghcr.io/{repo.lower()}:{tag}"
    expected = record["docker"]["image"]["id"]
    release.command(["docker", "load", "--input", directory / archive_name(tag)])
    if release.verify_image(release.image_info(image), version.name, commit) != record["docker"]["image"]:
        raise release.ReleaseError("Loaded Docker image differs from the verified build")
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise release.ReleaseError("GH_TOKEN is required for publication")
    with tempfile.TemporaryDirectory(prefix="jzmedia-registry-") as temporary:
        env = {**os.environ, "DOCKER_CONFIG": temporary}
        release.command(["docker", "login", "ghcr.io", "--username", os.environ["GITHUB_ACTOR"],
                         "--password-stdin"], env=env, input=token + "\n")
        remote = remote_manifest(image, env, missing_ok=True)
        check_remote_image(remote, expected)
        if existing is None:
            notes_file = Path(temporary) / "notes.md"
            notes_file.write_text(notes(record, repo))
            release.command(["gh", "release", "create", tag, "--repo", repo, "--verify-tag", "--draft",
                             "--title", f"jzmedia {tag}", "--generate-notes", "--notes-file", notes_file])
            existing = wait_for_release(repo, tag)
        missing = verify_assets(existing, directory, complete=not existing["draft"])
        if missing:
            # Only empty interrupted uploads in this draft are removed; completed assets are immutable.
            for item in existing["assets"]:
                if item["name"] in missing:
                    release.command(["gh", "api", "--method", "DELETE",
                                     f"repos/{repo}/releases/assets/{item['id']}"])
            release.command(["gh", "release", "upload", tag, "--repo", repo,
                             *[directory / name for name in missing]])
        existing = wait_for_release(repo, tag)
        verify_assets(existing, directory, complete=True)
        # A complete recoverable draft and the immutable Actions bundle precede registry writes.
        if remote is None:
            release.command(["docker", "push", image], env=env)
        check_remote_image(remote_manifest(image, env), expected)
        with tempfile.TemporaryDirectory(prefix="jzmedia-anonymous-") as anonymous:
            anonymous_env = {**env, "DOCKER_CONFIG": anonymous}
            check_remote_image(remote_manifest(image, anonymous_env), expected)
        check_remote_tag(repo, tag, commit)
        if existing["draft"]:
            release.command(["gh", "release", "edit", tag, "--repo", repo, "--draft=false", "--latest",
                             "--verify-tag"])
        latest = f"ghcr.io/{repo.lower()}:latest"
        release.command(["docker", "tag", expected, latest])
        release.command(["docker", "push", latest], env=env)
        if remote_manifest(image, env) != remote_manifest(latest, env):
            raise release.ReleaseError("Version and latest registry manifests differ")
    print(f"Published https://github.com/{repo}/releases/tag/{tag}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", help="Validate tag identity or select manual validation mode")
    for name in ("package", "publish"):
        child = commands.add_parser(name)
        child.add_argument("--directory", type=Path, default=ROOT / "output/github-release")
    args = parser.parse_args(argv)
    if args.command == "preflight":
        preflight()
    elif args.command == "package":
        package(args.directory.resolve())
    else:
        publish(args.directory.resolve())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
