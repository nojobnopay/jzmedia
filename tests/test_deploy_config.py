"""部署配置：无需 .env 即可解析，同时保留已有文件中的部署与应用配置。"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_main_compose_sets_user_from_env():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'user: "${UID:-0}:${GID:-0}"' in text


def test_env_example_has_uid_gid():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "UID=" in text and "GID=" in text


@pytest.mark.parametrize("with_env", [False, True], ids=["no-env", "existing-env"])
def test_compose_config_resolves_optional_env(tmp_path, with_env):
    """只渲染隔离副本，不访问用户配置或 Docker daemon。"""
    if not shutil.which("docker"):
        pytest.skip("docker not available")
    process_env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    version = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True,
        timeout=60, env=process_env)
    if version.returncode:
        pytest.skip("docker compose not available")

    compose = tmp_path / "docker-compose.yml"
    compose.write_text((ROOT / "docker-compose.yml").read_text())
    environment = tmp_path / ".env"
    if with_env:
        environment.write_text(
            "UID=1234\nGID=2345\nAPP_PORT=18080\nAPP_VERSION=v1.2.3\n"
            "MEDIA_HOST_PATH=./custom-media\nDATA_HOST_PATH=./custom-data\n"
            "MEDIA_ROOT=/ignored-media\nDATA_DIR=/ignored-data\n"
            "TMDB_LANGUAGE=en-US\nTRANSCODER=sw\n")

    out = subprocess.run(
        ["docker", "compose", "-f", str(compose), "config", "--format", "json"],
        capture_output=True, text=True, timeout=60, cwd=tmp_path, env=process_env)
    assert out.returncode == 0, out.stderr
    service = json.loads(out.stdout)["services"]["mymedia"]
    assert service["user"] == ("1234:2345" if with_env else "0:0")
    assert service["image"] == ("jzmedia:v1.2.3" if with_env else "jzmedia:latest")
    assert service["ports"][0]["target"] == 8080
    assert service["ports"][0]["published"] == ("18080" if with_env else "8080")
    volumes = {volume["target"]: volume["source"] for volume in service["volumes"]}
    assert volumes == {
        "/app/media": str(tmp_path / ("custom-media" if with_env else "media")),
        "/app/data": str(tmp_path / ("custom-data" if with_env else "data")),
    }
    container_env = service["environment"]
    assert container_env["MEDIA_ROOT"] == "/app/media"
    assert container_env["DATA_DIR"] == "/app/data"
    if with_env:
        assert container_env["TMDB_LANGUAGE"] == "en-US"
        assert container_env["TRANSCODER"] == "sw"
    else:
        assert set(container_env) == {"MEDIA_ROOT", "DATA_DIR"}
        assert not environment.exists()
