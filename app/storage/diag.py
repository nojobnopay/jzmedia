"""SMB 连接诊断管线（指导 §13/§14）：分阶段结构化结果，UI 可直接解释失败原因。

阶段：ADDRESS → DNS → TCP → SMB → AUTH → SHARE → PATH → READ → WRITE → STREAM → FFPROBE。
失败返回 `{status:"failed", stage, code, message, suggestions}` + 已完成阶段耗时；
结果与日志绝不包含密码（凭据只在内存参数里）。
"""
from __future__ import annotations

import os
import socket
import subprocess
import time

from ..log import get_logger
from .base import StorageError
from .smb import SmbStorageBackend

logger = get_logger("storage.diag")

__all__ = ['diagnose_smb']

_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".webm"}
_NOT_FOUND = 0xC0000034
_PATH_NOT_FOUND = 0xC000003A
_BAD_NETWORK_NAME = 0xC00000CC
_ACCESS_DENIED = 0xC0000022

_SUGGESTIONS = {
    "INVALID_ADDRESS": ["服务器地址填主机名或 IP（不带 // 与共享名）"],
    "INVALID_SHARE": ["共享名如 video，区分大小写；可在 NAS 的共享列表中核对"],
    "INVALID_PATH": ["目录写成相对共享根的路径，如 Movies/4K"],
    "HOSTNAME_RESOLVE_FAILED": [
        "改用 Tailscale IP（100.x.x.x）或完整 MagicDNS 名称",
        "容器内 DNS 可能不继承宿主：可先用 IP 建库",
    ],
    "TCP_TIMEOUT": ["确认 NAS 的 SMB 服务已开启（445 端口）",
                    "检查 Tailscale / 防火墙是否放行 445"],
    "CONNECTION_REFUSED": ["NAS 未在 445 监听，或地址/端口不对"],
    "TCP_UNREACHABLE": ["网络不可达：检查 Tailscale 是否在线、IP 是否正确"],
    "SMB_NEGOTIATE_FAILED": ["确认 NAS 支持 SMB2/3（DSM 可在控制面板开启）",
                             "若启用了 SMB1 兼容模式，建议升级为 SMB2/3"],
    "AUTH_FAILED": ["核对用户名/密码（NAS 只读账号即可）",
                    "域账号需填写 DOMAIN\\用户 或改用本地账号"],
    "SHARE_NOT_FOUND": ["共享名可能拼写错误或未对账号开放"],
    "SHARE_ACCESS_DENIED": ["账号对该共享无权限；在 NAS 共享权限中放行"],
    "PATH_NOT_FOUND": ["共享内目录不存在；检查子目录拼写"],
    "READ_FAILED": ["目录可访问但读文件失败；检查文件权限或 SMB 会话"],
    "WRITE_FAILED": ["库配置为只读或账号无写权限；只读库请忽略此项"],
    "STREAM_FAILED": ["随机读失败：Range/seek 可能不可用，播放会受影响"],
    "NO_SAMPLE": [],
    "FFPROBE_FAILED": ["ffprobe 无法读取该文件（编码/权限/网络中断）",
                       "可先在 NAS 上确认文件可读"],
    "FFPROBE_MISSING": ["容器缺少 ffprobe（镜像未装 ffmpeg）"],
}


class _Fail(Exception):
    def __init__(self, stage: str, code: str, message: str, suggestions=()):
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.message = message
        self.suggestions = list(suggestions or _SUGGESTIONS.get(code, []))


def _result(ok: bool, stage: str, code: str, message: str, suggestions, stages,
            elapsed_ms: int, warnings=()) -> dict:
    return {"status": "ok" if ok else "failed", "ok": ok, "stage": stage,
            "code": code, "message": message, "suggestions": list(suggestions or []),
            "warnings": list(warnings or []),
            "stages": stages, "elapsed_ms": elapsed_ms}


def diagnose_smb(host: str, share: str, subpath: str = "", username: str = "",
                 password: str = "", domain: str = "", read_only: bool = False,
                 timeout: float = 10.0, check_ffprobe: bool = True,
                 connect_host: str = "") -> dict:
    """完整诊断；不落库、不写盘（WRITE 只写临时文件并删除）。
    只读账号/只读库的写失败记为 warning 不整体失败（扫描/播放仍可用）。"""
    t_all = time.perf_counter()
    stages: list[dict] = []
    warnings: list[dict] = []

    def done(stage: str, t0: float, skipped: bool = False) -> None:
        row = {"stage": stage, "ok": True, "ms": int((time.perf_counter() - t0) * 1000)}
        if skipped:
            row["skipped"] = True
        stages.append(row)

    def fail(stage: str, code: str, message: str, suggestions=()):
        stages.append({"stage": stage, "ok": False, "code": code, "message": message})
        raise _Fail(stage, code, message, suggestions)

    def warn(stage: str, code: str, message: str, suggestions=()):
        stages.append({"stage": stage, "ok": False, "code": code, "message": message,
                       "warning": True})
        warnings.append({"stage": stage, "code": code, "message": message,
                         "suggestions": list(suggestions or _SUGGESTIONS.get(code, []))})

    try:
        # 1) ADDRESS
        t0 = time.perf_counter()
        host = str(host or "").strip().strip("/")
        connect = str(connect_host or "").strip().strip("/") or host
        share = str(share or "").strip().strip("/")
        subpath = str(subpath or "").strip().strip("/")
        if not host or "/" in host or "\\" in host or " " in host:
            fail("ADDRESS", "INVALID_ADDRESS", "服务器地址无效", _SUGGESTIONS["INVALID_ADDRESS"])
        if not connect or "/" in connect or "\\" in connect or " " in connect:
            fail("ADDRESS", "INVALID_ADDRESS", "连接地址无效", _SUGGESTIONS["INVALID_ADDRESS"])
        if not share or "/" in share or "\\" in share:
            fail("ADDRESS", "INVALID_SHARE", "共享名无效", _SUGGESTIONS["INVALID_SHARE"])
        if subpath.startswith("..") or "/../" in f"/{subpath}/":
            fail("ADDRESS", "INVALID_PATH", "目录路径无效", _SUGGESTIONS["INVALID_PATH"])
        done("ADDRESS", t0)

        # 2) DNS
        t0 = time.perf_counter()
        try:
            socket.getaddrinfo(connect, 445, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            fail("DNS", "HOSTNAME_RESOLVE_FAILED", f"无法解析主机名 {connect}",
                 _SUGGESTIONS["HOSTNAME_RESOLVE_FAILED"])
        done("DNS", t0)

        # 3) TCP 445
        t0 = time.perf_counter()
        try:
            with socket.create_connection((connect, 445), timeout=timeout):
                pass
        except (socket.timeout, TimeoutError):
            fail("TCP", "TCP_TIMEOUT", "TCP 445 连接超时", _SUGGESTIONS["TCP_TIMEOUT"])
        except ConnectionRefusedError:
            fail("TCP", "CONNECTION_REFUSED", "TCP 445 连接被拒绝",
                 _SUGGESTIONS["CONNECTION_REFUSED"])
        except OSError as e:
            fail("TCP", "TCP_UNREACHABLE", f"TCP 445 不可达: {e}",
                 _SUGGESTIONS["TCP_UNREACHABLE"])
        done("TCP", t0)

        # 4-7) SMB 协商 / AUTH / SHARE / PATH（smbclient 高层：一次注册 + 一次 stat）
        _smb_stages(connect, share, subpath, username, password, domain, timeout,
                    stages, done, fail)

        # 8-11) READ / WRITE / STREAM / FFPROBE（复用直读后端）
        lib = {"id": 0, "name": "诊断", "source": "smb", "path": "",
               "read_only": 1 if read_only else 0,
               "smb_host": host, "smb_connect_host": connect,
               "smb_share": share, "smb_subpath": subpath,
               "smb_domain": domain, "smb_username": username,
               "smb_password": ""}
        _backend_stages(lib, password, timeout, read_only, check_ffprobe,
                        stages, done, fail, warn)
    except _Fail as f:
        return _result(False, f.stage, f.code, f.message, f.suggestions, stages,
                       int((time.perf_counter() - t_all) * 1000), warnings)
    return _result(True, "FFPROBE", "", "远程库就绪", [], stages,
                   int((time.perf_counter() - t_all) * 1000), warnings)


def _smb_stages(host, share, subpath, username, password, domain, timeout,
                stages, done, fail) -> None:
    from . import smb as smb_mod
    smbclient, smb_exc = smb_mod.smbclient, smb_mod.smb_exc
    if smbclient is None:
        fail("SMB", "SMB_NEGOTIATE_FAILED",
             f"缺少 smbprotocol 依赖: {smb_mod._IMPORT_ERROR}", [])
    user = username
    if domain and user and "\\" not in user and "@" not in user:
        user = f"{domain}\\{user}"
    kwargs: dict = {"connection_timeout": max(1, int(timeout))}
    if user:
        kwargs["username"] = user
    if user or password:
        kwargs["password"] = password
    t0 = time.perf_counter()
    try:
        smbclient.register_session(host, **kwargs)
    except smb_exc.SMBAuthenticationError:
        done("SMB", t0)
        fail("AUTH", "AUTH_FAILED", "用户名或密码无效")
    except (socket.timeout, TimeoutError):
        fail("SMB", "SMB_NEGOTIATE_FAILED", "SMB 协商超时")
    except smb_exc.SMBException as e:
        fail("SMB", "SMB_NEGOTIATE_FAILED", "SMB 协商失败: " + " ".join(str(e).split())[:160])
    except OSError as e:
        fail("SMB", "TCP_UNREACHABLE", f"连接中断: {e}")
    done("SMB", t0)
    t0 = time.perf_counter()
    done("AUTH", t0)

    unc = "//" + "/".join(p.strip("/") for p in (host, share, subpath) if p)
    t0 = time.perf_counter()
    try:
        smbclient.stat(unc)
    except smb_exc.SMBOSError as e:
        status = 0
        try:
            status = int(getattr(e, "ntstatus", 0) or 0)
        except (TypeError, ValueError):
            status = 0
        if status == _BAD_NETWORK_NAME:
            fail("SHARE", "SHARE_NOT_FOUND", f"共享不存在: {share}")
        if status == _ACCESS_DENIED:
            fail("SHARE", "SHARE_ACCESS_DENIED", "账号对该共享无访问权限")
        if status in (_NOT_FOUND, _PATH_NOT_FOUND):
            fail("PATH", "PATH_NOT_FOUND", f"目录不存在: {subpath or '/'}")
        fail("SHARE", "SHARE_NOT_FOUND", "SMB 错误: " + " ".join(str(e).split())[:160])
    except smb_exc.SMBException as e:
        fail("SHARE", "SHARE_NOT_FOUND", "SMB 错误: " + " ".join(str(e).split())[:160])
    except OSError as e:
        fail("PATH", "PATH_NOT_FOUND", f"目录不可访问: {e}")
    done("SHARE", t0)
    t0 = time.perf_counter()
    done("PATH", t0)


def _backend_stages(lib, password, timeout, read_only, check_ffprobe,
                    stages, done, fail, warn) -> None:
    from .. import secrets
    lib = {**lib, "smb_password": secrets.encrypt_str(password) if password else ""}
    try:
        backend = SmbStorageBackend(lib)
    except StorageError as e:
        fail("READ", getattr(e, "code", "READ_FAILED"), f"直读后端不可用: {e}")

    # READ：列目录 + 抽样小读
    t0 = time.perf_counter()
    try:
        backend.test_read()
    except StorageError as e:
        fail("READ", "READ_FAILED", f"读取失败: {e}")
    done("READ", t0)

    # WRITE：只读库/只读账号不整体失败（扫描与播放仍可用；归档/NFO 才需要写）
    t0 = time.perf_counter()
    wr = backend.test_write()
    if wr.get("skipped"):
        done("WRITE", t0, skipped=True)
    elif not wr.get("ok"):
        warn("WRITE", "WRITE_FAILED", f"写入失败: {wr.get('error', '')}")
    else:
        done("WRITE", t0)

    # STREAM：找样本视频做 1MB 随机读（Range/seek 可行性）
    sample = _find_sample(backend)
    if sample is None:
        done("STREAM", t0, skipped=True)
        done("FFPROBE", t0, skipped=True)
        return
    rel, size = sample
    t0 = time.perf_counter()
    try:
        offset = max(0, size // 2 - (1 << 19))
        if not backend.read(rel, offset, 1 << 20):
            raise StorageError("随机读返回空数据")
    except StorageError as e:
        fail("STREAM", "STREAM_FAILED", f"随机读失败: {e}")
    done("STREAM", t0)

    # FFPROBE：真实探测一个媒体文件
    if not check_ffprobe:
        done("FFPROBE", t0, skipped=True)
        return
    from ..media import ffprobe_bin
    fp = ffprobe_bin()
    t0 = time.perf_counter()
    try:
        from . import httpproxy
        # 诊断用临时令牌（lib id 可能为 0/未入库，不能走库行解析）
        url = httpproxy.url_for_backend(backend, rel)
        r = subprocess.run([fp, "-v", "error", "-show_entries", "format=duration",
                            "-of", "json", url],
                           capture_output=True, timeout=max(30, timeout * 3), check=False)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", "replace").strip()[:160]
            fail("FFPROBE", "FFPROBE_FAILED", f"ffprobe 失败: {err}")
    except subprocess.TimeoutExpired:
        fail("FFPROBE", "FFPROBE_FAILED", "ffprobe 超时（网络抖动或文件不可读）")
    except FileNotFoundError:
        fail("FFPROBE", "FFPROBE_MISSING", "未找到 ffprobe 可执行文件")
    done("FFPROBE", t0)


def _find_sample(backend: SmbStorageBackend, max_dirs: int = 24):
    """广度优先找第一个可读视频（限制目录数，避免大库诊断过慢）。"""
    queue = [""]
    seen = 0
    while queue and seen < max_dirs:
        cur = queue.pop(0)
        seen += 1
        try:
            entries = backend.list(cur)
        except StorageError:
            continue
        for e in entries:
            rel = f"{cur}/{e['name']}" if cur else e["name"]
            if e["is_dir"]:
                queue.append(rel)
            elif os.path.splitext(e["name"])[1].lower() in _VIDEO_EXTS \
                    and e["size"] > 0:
                return rel, e["size"]
    return None
