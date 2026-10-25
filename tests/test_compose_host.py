"""Resolve the actual host overlay without a daemon, NAS or real credentials."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("host_compose_release", SCRIPTS / "github_release.py")
release_tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_tools)


@pytest.fixture
def deployment(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose client is not installed")
    probe = subprocess.run(["docker", "compose", "version", "--short"], capture_output=True, text=True)
    if probe.returncode:
        pytest.skip("Docker Compose client is not installed")
    version = re.search(r"(?:v)?(\d+)\.(\d+)\.(\d+)", probe.stdout)
    if not version or tuple(map(int, version.groups())) < (2, 24, 0):
        pytest.skip("Host overlay resolution requires Docker Compose 2.24.0 or newer")
    # Copy templates away from the working tree so the developer's .env is never read.
    for name in ("docker-compose.yml", "compose.host.yml", "compose.nvidia.yml"):
        (tmp_path / name).write_text((ROOT / name).read_text())
    (tmp_path / "compose.proxy-test.yml").write_text(
        "services:\n"
        "  mymedia:\n"
        "    networks:\n"
        "      - default\n"
        "      - proxy_test\n"
        "networks:\n"
        "  proxy_test:\n"
        "    external: true\n")
    (tmp_path / ".env").write_text(
        "APP_VERSION=compose-test\nAPP_PORT=59123\nUID=1000\nGID=1000\n"
        "TMDB_PROXY=http://isolated-proxy:7890\n")
    return tmp_path


def resolved(directory, *overlays):
    command = ["docker", "compose", "--project-directory", str(directory), "-f", str(directory / "docker-compose.yml")]
    for name in overlays:
        command.extend(("-f", str(directory / name)))
    command.extend(("config", "--format", "json"))
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    result = subprocess.run(command, cwd=directory, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]["mymedia"]


@pytest.mark.parametrize("extra", [(), ("compose.proxy-test.yml",), ("compose.nvidia.yml",)])
def test_host_overlay_removes_bridge_and_published_ports_without_changing_runtime(deployment, extra):
    original = resolved(deployment, *extra)
    host = resolved(deployment, *extra, "compose.host.yml")
    assert original["ports"][0]["published"] == "59123"
    assert original.get("network_mode") != "host"
    assert host["network_mode"] == "host"
    assert not host.get("ports") and not host.get("networks")
    for key in ("image", "build", "command", "environment", "volumes", "user", "init", "restart", "healthcheck", "deploy"):
        assert host.get(key) == original.get(key), key
    assert host["command"][-2:] == ["--port", "8080"]


def test_registry_release_compose_accepts_the_same_host_overlay(deployment):
    image = "ghcr.io/example/jzmedia:v1.2.3"
    base = deployment / "docker-compose.yml"
    base.write_text(release_tools.compose_for_registry(base.read_text(), image))
    host = resolved(deployment, "compose.host.yml")
    assert host["image"] == image and "build" not in host
    assert host["network_mode"] == "host"
    assert not host.get("ports") and not host.get("networks")
