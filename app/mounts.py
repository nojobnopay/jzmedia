"""远程库挂载管理（MULTI_LIBRARY_PLAN §7）：SMB/NFS 应用内挂载 + 看门狗。

- 挂载点固定 `data_dir/mounts/lib_<id>`（重启路径稳定，无需 compose 变更）。
- 凭据只存 Fernet 密文；挂载时写 0600 临时凭据文件（SMB），绝不进日志。
- 能力检测（CAP_SYS_ADMIN + mount.cifs/mount.nfs 可用）；不可用时返回宿主挂载指引。
- 看门狗：60s 巡检 auto_mount 的远程库，失联按退避重挂（5s→5min）；状态写 libraries。
- env `ALLOW_SMB_MOUNT=0` 可整体禁用应用内挂载（只读宿主挂载模式）。
"""
import os
import re
import shutil
import subprocess
import threading
import time

from . import library_paths, secrets, store
from .config import settings
from .log import get_logger

logger = get_logger("mounts")

_MOUNT_WAIT = 20          # 单次 mount 命令超时（秒）
_WATCH_INTERVAL = 60      # 看门狗巡检间隔
_BACKOFF_MAX = 300        # 失联重挂最大退避（秒）
_BACKOFF_BASE = 5

# mount 参数白名单（防注入/误配置；值只允许字母数字与 . _ - : = , / @）
_SMB_KEYS = {"vers", "sec", "iocharset", "cache", "uid", "gid", "forceuid", "forcegid",
             "file_mode", "dir_mode", "mfsymlinks", "nobrl", "soft", "hard",
             "retrans", "actimeo", "noatime", "ro", "rw", "nounix", "noserverino",
             "serverino", "echo_interval", "rsize", "wsize", "domainauto"}
_NFS_KEYS = {"vers", "proto", "soft", "hard", "timeo", "retrans", "noatime", "ro",
             "rw", "nordirplus", "noac", "rsize", "wsize", "actimeo"}
_OPT_VAL_RE = re.compile(r"^[A-Za-z0-9._\-:=/@+]*$")

_watchdog_thread: threading.Thread | None = None
_watchdog_stop = threading.Event()
_backoff: dict[int, tuple[float, int]] = {}   # lid -> (next_try_ts, fail_count)


def enabled() -> bool:
    return os.getenv("ALLOW_SMB_MOUNT", "1").strip().lower() not in ("0", "false", "no", "off")


def mounts_dir() -> str:
    return os.path.join(settings.data_dir, "mounts")


def mount_point(library_id: int) -> str:
    return os.path.join(mounts_dir(), f"lib_{int(library_id)}")


def _cred_path(library_id: int) -> str:
    return os.path.join(mounts_dir(), f".cred_{int(library_id)}")


def has_cap_sys_admin() -> bool:
    if os.geteuid() == 0:
        return True
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("CapEff:"):
                    caps = int(line.split()[1], 16)
                    return bool(caps & (1 << 21))   # CAP_SYS_ADMIN = 21
    except (OSError, ValueError, IndexError):
        pass
    return False


def mount_supported() -> tuple[bool, str]:
    """(是否支持应用内挂载, 原因)。"""
    if not enabled():
        return False, "应用内挂载已禁用（ALLOW_SMB_MOUNT=0）"
    if not has_cap_sys_admin():
        return False, "容器缺少 CAP_SYS_ADMIN（compose 需 cap_add: [SYS_ADMIN]，DSM 可能需 privileged）"
    if not (shutil.which("mount.cifs") or os.path.exists("/sbin/mount.cifs")):
        return False, "缺少 mount.cifs（镜像需安装 cifs-utils）"
    if not (shutil.which("mount.nfs") or os.path.exists("/sbin/mount.nfs")):
        return False, "缺少 mount.nfs（镜像需安装 nfs-common）"
    return True, ""


def _sanitize_options(raw: str, allowed: set[str]) -> list[str]:
    """逗号分隔的挂载参数 → argv 片段；键必须在白名单，值做字符白名单校验。"""
    out: list[str] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        key, _, val = part.partition("=")
        key = key.strip().lower()
        if key not in allowed:
            logger.warning("忽略未知挂载参数 %s", key)
            continue
        if val and not _OPT_VAL_RE.match(val):
            logger.warning("忽略非法挂载参数值 %s=%s", key, val[:20])
            continue
        out.append(part)
    return out


def _write_cred_file(lib: dict) -> str:
    """SMB 凭据文件（0600）；密码只存在于文件与内存，不落日志。"""
    os.makedirs(mounts_dir(), exist_ok=True)
    path = _cred_path(int(lib["id"]))
    pwd = secrets.decrypt_str(lib.get("smb_password") or "")
    content = (f"username={lib.get('smb_username') or ''}\n"
               f"password={pwd}\n"
               f"domain={lib.get('smb_domain') or ''}\n")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def suggested_cmd(lib: dict) -> str:
    """宿主挂载指引（能力不足时的降级文案，含 fstab 风格）。"""
    path = str(lib.get("path") or mount_point(int(lib.get("id") or 0)))
    if str(lib.get("source")) == "smb":
        share = f"//{lib.get('smb_host')}/{lib.get('smb_share')}"
        if lib.get("smb_subpath"):
            share += "/" + str(lib["smb_subpath"])
        return (f"sudo mount -t cifs '{share}' '{path}' -o credentials=/etc/jzmedia-smb.cred,"
                "uid=$(id -u),gid=$(id -g),iocharset=utf8,soft,retrans=3,vers=3.0,nobrl")
    export = str(lib.get("nfs_export") or "NAS:/export")
    return f"sudo mount -t nfs '{export}' '{path}' -o vers=4.1,soft,timeo=100"


def _run_mount(lib: dict) -> tuple[bool, str]:
    """执行挂载。返回 (成功, 脱敏错误文案)。"""
    lid = int(lib["id"])
    mp = mount_point(lid)
    os.makedirs(mp, exist_ok=True)
    source = str(lib.get("source") or "")
    if source == "smb":
        share = f"//{lib.get('smb_host')}/{lib.get('smb_share')}"
        if lib.get("smb_subpath"):
            share += "/" + str(lib["smb_subpath"])
        try:
            cred = _write_cred_file(lib)
        except secrets.SecretError as e:
            return False, str(e)
        opts = ["credentials=" + cred, "uid=" + str(os.getuid()),
                "gid=" + str(os.getgid()), "iocharset=utf8", "soft", "retrans=3",
                "vers=3.0", "nobrl"]
        if int(lib.get("read_only") or 0):
            opts.append("ro")
        opts = _sanitize_options(",".join(opts) + ("," + (lib.get("smb_options") or "")
                                                   if lib.get("smb_options") else ""),
                                 _SMB_KEYS | {"credentials", "uid", "gid", "ro"})
        argv = ["mount", "-t", "cifs", share, mp, "-o", ",".join(opts)]
    elif source == "nfs":
        opts = ["vers=4.1", "soft", "timeo=100"] + (
            ["ro"] if int(lib.get("read_only") or 0) else [])
        if lib.get("nfs_options"):
            opts += _sanitize_options(str(lib["nfs_options"]), _NFS_KEYS)
        argv = ["mount", "-t", "nfs", str(lib.get("nfs_export") or ""), mp,
                "-o", ",".join(opts)]
    else:
        return False, f"不支持的来源: {source}"
    try:
        r = subprocess.run(argv, capture_output=True, timeout=_MOUNT_WAIT, check=False)
    except subprocess.TimeoutExpired:
        return False, "挂载超时（网络不可达？）"
    except OSError as e:
        return False, f"执行 mount 失败: {e}"
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", errors="replace").strip()
        # stderr 可能包含用户名/主机，但不含密码（凭据走文件）；截断并单行化
        err = " ".join(err.split())[:200]
        return False, f"mount 失败: {err}"
    return True, ""


def _run_umount(mp: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(["umount", mp], capture_output=True, timeout=_MOUNT_WAIT,
                           check=False)
    except subprocess.TimeoutExpired:
        return False, "卸载超时"
    except OSError as e:
        return False, f"执行 umount 失败: {e}"
    if r.returncode != 0:
        err = " ".join((r.stderr or b"").decode("utf-8", errors="replace").split())[:200]
        return False, f"umount 失败: {err}"
    return True, ""


def is_mounted(lib: dict) -> bool:
    """挂载点已挂载且可读。"""
    mp = mount_point(int(lib["id"]))
    try:
        return os.path.ismount(mp) and os.access(mp, os.R_OK)
    except OSError:
        return False


def mount_library(lib: dict) -> dict:
    """挂载并落状态。返回 {ok, status, error, mount_supported, suggested_cmd}。"""
    if str(lib.get("source")) == "local":
        return {"ok": True, "status": "local", "error": "",
                "mount_supported": True, "suggested_cmd": ""}
    ok_cap, reason = mount_supported()
    if not ok_cap:
        store.set_library_status(int(lib["id"]), "not_mounted", reason)
        library_paths.invalidate_cache()
        return {"ok": False, "status": "not_mounted", "error": reason,
                "mount_supported": False, "suggested_cmd": suggested_cmd(lib)}
    if is_mounted(lib):
        store.set_library_status(int(lib["id"]), "ok", "")
        library_paths.invalidate_cache()
        return {"ok": True, "status": "ok", "error": "",
                "mount_supported": True, "suggested_cmd": ""}
    ok, err = _run_mount(lib)
    status = "ok" if ok else ("auth_failed" if "auth" in err.lower()
                              or "password" in err.lower() else "unreachable")
    store.set_library_status(int(lib["id"]), status, err)
    library_paths.invalidate_cache()
    if ok:
        logger.info("挂载成功 lib=%s path=%s", lib.get("id"), lib.get("path"))
    else:
        logger.warning("挂载失败 lib=%s status=%s err=%s", lib.get("id"), status, err)
    return {"ok": ok, "status": status, "error": err,
            "mount_supported": True, "suggested_cmd": suggested_cmd(lib)}


def unmount_library(lib: dict) -> dict:
    if str(lib.get("source")) == "local":
        return {"ok": True, "status": "local", "error": ""}
    mp = mount_point(int(lib["id"]))
    if not os.path.ismount(mp):
        store.set_library_status(int(lib["id"]), "not_mounted", "")
        library_paths.invalidate_cache()
        return {"ok": True, "status": "not_mounted", "error": ""}
    ok, err = _run_umount(mp)
    store.set_library_status(int(lib["id"]), "not_mounted" if ok else "error", err)
    library_paths.invalidate_cache()
    return {"ok": ok, "status": "not_mounted" if ok else "error", "error": err}


def cleanup_library(lib: dict) -> None:
    """删库后清理凭据文件与挂载点目录（best-effort）。"""
    lid = int(lib.get("id") or 0)
    for p in (_cred_path(lid), mount_point(lid)):
        try:
            if os.path.isfile(p):
                os.remove(p)
            elif os.path.isdir(p) and not os.listdir(p):
                os.rmdir(p)
        except OSError as e:
            logger.debug("cleanup library mount failed lid=%s path=%s: %s", lid, p, e)


def check_library(lib: dict) -> dict:
    """可读性自检（不主动挂载）：smb/nfs 未挂载返回 not_mounted。"""
    lid = int(lib["id"])
    source = str(lib.get("source") or "local")
    path = str(lib.get("path") or "")
    exists = bool(path) and os.path.isdir(path)
    readable = exists and os.access(path, os.R_OK)
    writable = exists and os.access(path, os.W_OK)
    ok_cap, cap_reason = mount_supported()
    if source == "local":
        status = "ok" if readable else "error"
        err = "" if readable else "路径不可读"
    else:
        if is_mounted(lib):
            status, err = "ok", ""
        elif readable:
            status, err = "ok", ""      # 宿主已挂载但不在我们管理范围内也算可用
        else:
            status, err = "not_mounted", cap_reason or "未挂载"
    store.set_library_status(lid, status, err)
    library_paths.invalidate_cache()
    return {"id": lid, "source": source, "path": path,
            "exists": exists, "readable": readable, "writable": writable,
            "read_only": bool(lib.get("read_only")),
            "mounted": is_mounted(lib) if source != "local" else False,
            "mount_supported": ok_cap, "reason": cap_reason,
            "suggested_cmd": suggested_cmd(lib), "last_status": status,
            "error": err}


def _watchdog_loop() -> None:
    while not _watchdog_stop.wait(_WATCH_INTERVAL):
        if not enabled():
            continue
        try:
            libs = [l for l in store.list_libraries(only_enabled=True)
                    if str(l.get("source")) in ("smb", "nfs")
                    and int(l.get("auto_mount") or 0)]
        except Exception as e:
            logger.debug("watchdog list libraries failed: %s", e)
            continue
        now = time.time()
        for lib in libs:
            lid = int(lib["id"])
            if is_mounted(lib):
                _backoff.pop(lid, None)
                continue
            next_try, fails = _backoff.get(lid, (0.0, 0))
            if now < next_try:
                continue
            res = mount_library(lib)
            if not res.get("ok"):
                delay = min(_BACKOFF_MAX, _BACKOFF_BASE * (2 ** min(fails, 6)))
                _backoff[lid] = (now + delay, fails + 1)
                logger.info("看门狗重挂失败 lib=%s，%ss 后重试（第 %s 次）",
                            lid, delay, fails + 1)


def start_watchdog() -> None:
    """lifespan 启动时调用：立即尝试重挂一次 + 后台线程巡检。"""
    global _watchdog_thread
    if not enabled():
        logger.info("应用内挂载已禁用（ALLOW_SMB_MOUNT=0），跳过后台看门狗")
        return
    if _watchdog_thread and _watchdog_thread.is_alive():
        return
    _watchdog_stop.clear()
    _watchdog_thread = threading.Thread(target=_watchdog_loop, daemon=True,
                                        name="jzmedia-mounts")
    _watchdog_thread.start()
    threading.Thread(target=_initial_remount, daemon=True,
                     name="jzmedia-mounts-init").start()


def _initial_remount() -> None:
    try:
        libs = [l for l in store.list_libraries(only_enabled=True)
                if str(l.get("source")) in ("smb", "nfs")
                and int(l.get("auto_mount") or 0)]
    except Exception as e:
        logger.warning("startup remount list failed: %s", e)
        return
    for lib in libs:
        if not is_mounted(lib):
            mount_library(lib)


def shutdown_mounts() -> None:
    """应用退出：停看门狗并卸载我们管理的挂载点（防残留挂载）。"""
    _watchdog_stop.set()
    global _watchdog_thread
    if _watchdog_thread:
        try:
            _watchdog_thread.join(timeout=3)
        except Exception:
            pass
        _watchdog_thread = None
    if not enabled():
        return
    try:
        libs = store.list_libraries()
    except Exception:
        return
    for lib in libs:
        if str(lib.get("source")) in ("smb", "nfs") and is_mounted(lib):
            ok, err = _run_umount(mount_point(int(lib["id"])))
            if not ok:
                logger.warning("退出卸载失败 lib=%s: %s", lib.get("id"), err)


__all__ = ['enabled', 'mounts_dir', 'mount_point', 'mount_supported', 'is_mounted',
           'mount_library', 'unmount_library', 'check_library', 'cleanup_library',
           'suggested_cmd', 'start_watchdog', 'shutdown_mounts', 'has_cap_sys_admin']
