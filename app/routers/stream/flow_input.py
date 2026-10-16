"""为可暂停的远程 HLS 输入提供有限重连，不改变产物格式或缓存标识。"""
from functools import lru_cache
import signal
import subprocess

from ...log import get_logger
from .flow_control import lookahead_seconds

logger = get_logger("stream.flow")


@lru_cache(maxsize=4)
def _input_options(ffmpeg: str) -> frozenset[str]:
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-h", "full"],
                                capture_output=True, text=True, timeout=10, check=True)
        return frozenset(line.split()[0] for line in result.stdout.splitlines()
                         if line.lstrip().startswith("-") and line.split())
    except (OSError, subprocess.SubprocessError):
        return frozenset()


def prepare_flow_input(cmd: list[str], source: str, plan: dict) -> tuple[list[str], bool]:
    """本地输入保持原读取参数；远程断链按原字节位置有限重连。"""
    if (plan.get("seg") == "mp4" or lookahead_seconds() <= 0
            or not hasattr(signal, "SIGSTOP") or not hasattr(signal, "SIGCONT") or "-i" not in cmd):
        return cmd, False
    if not source.startswith(("http://", "https://")):
        return cmd, True
    options = _input_options(cmd[0])
    extras = []
    main_input = cmd.index("-i")
    reconnect = {"-reconnect", "-reconnect_on_network_error", "-reconnect_delay_max"}
    if not reconnect.issubset(options):
        logger.warning("HLS lookahead disabled: FFmpeg HTTP reconnection unavailable")
        return cmd, False
    extras += ["-reconnect", "1", "-reconnect_on_network_error", "1", "-reconnect_delay_max", "2"]
    if "-reconnect_max_retries" in options:
        extras += ["-reconnect_max_retries", "3"]
    if "-reconnect_delay_total_max" in options:
        extras += ["-reconnect_delay_total_max", "10"]
    return cmd[:main_input] + extras + cmd[main_input:], True
