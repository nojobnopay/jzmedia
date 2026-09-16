"""部署配置回归网（P1-09）：主 compose 必须以 .env 的 UID/GID 运行容器。"""
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_main_compose_sets_user_from_env():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'user: "${UID:-0}:${GID:-0}"' in text


def test_env_example_has_uid_gid():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "UID=" in text and "GID=" in text


def test_compose_config_resolves_user():
    """有 docker 时用 compose 渲染验证插值（无 docker 跳过）。"""
    if not shutil.which("docker"):
        import pytest
        pytest.skip("docker not available")
    out = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "-f",
         str(ROOT / "docker-compose.yml"), "config"],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "UID": "", "GID": ""})
    assert out.returncode == 0, out.stderr
    assert "user: '0:0'" in out.stdout or "user: 0:0" in out.stdout
