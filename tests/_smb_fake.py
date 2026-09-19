"""SMB 测试替身：把 //host/share[/sub]/rel 映射到本地目录（不触网）。

供 tests/test_storage_smb.py 与 tests/test_storage_diag.py 共用。
"""
import datetime
import os
import shutil
import stat as pystat
from pathlib import Path
from types import SimpleNamespace

_ATTR_DIR = 0x10
_ATTR_FILE = 0x80


class FakeScandir:
    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, *exc):
        return False


class FakeEntry:
    def __init__(self, name, path, st):
        self.name = name
        self.path = path
        self._info = SimpleNamespace(
            end_of_file=st.st_size,
            file_attributes=_ATTR_DIR if pystat.S_ISDIR(st.st_mode) else _ATTR_FILE,
            last_write_time=datetime.datetime.fromtimestamp(
                st.st_mtime, datetime.timezone.utc),
        )

    @property
    def smb_info(self):
        return self._info


def auth_error():
    from smbprotocol.exceptions import SMBAuthenticationError
    return SMBAuthenticationError("STATUS_LOGON_FAILURE")


def os_error(ntstatus: int, filename: str = ""):
    from smbprotocol.exceptions import SMBOSError
    return SMBOSError(ntstatus, filename or "//nas/video")


class FakeSmbClient:
    def __init__(self, root, password_ok=True, offline=False, stat_ntstatus=None,
                 write_denied=False):
        self.root = Path(root)
        self.password_ok = password_ok
        self.offline = offline
        self.stat_ntstatus = stat_ntstatus
        self.write_denied = write_denied
        self.sessions = []
        self.calls = []
        self.path = SimpleNamespace(isdir=self.isdir)
        self.shutil = SimpleNamespace(rmtree=self.rmtree)

    def _p(self, unc: str) -> Path:
        parts = [p for p in str(unc).replace("\\", "/").split("/") if p]
        return self.root.joinpath(*parts[2:])   # 去掉 host/share

    def _guard(self):
        if self.offline:
            raise ConnectionResetError("fake network down")

    def _wguard(self):
        self._guard()
        if self.write_denied:
            raise PermissionError("fake write denied")

    def register_session(self, server, **kwargs):
        self.calls.append(("register_session", server))
        if not self.password_ok:
            raise auth_error()
        self.sessions.append((server, kwargs))

    def scandir(self, unc, search_pattern="*", **kwargs):
        self._guard()
        p = self._p(unc)
        entries = []
        for e in os.scandir(p):
            entries.append(FakeEntry(e.name, str(Path(unc) / e.name), e.stat()))
        return FakeScandir(entries)

    def stat(self, unc, **kwargs):
        self._guard()
        if self.stat_ntstatus is not None:
            raise os_error(self.stat_ntstatus, str(unc))
        return os.stat(self._p(unc))

    def open_file(self, unc, mode="r", share_access=None, **kwargs):
        if any(c in mode for c in "wax+"):
            self._wguard()
        else:
            self._guard()
        return open(self._p(unc), mode)

    def makedirs(self, unc, exist_ok=False, **kwargs):
        self._wguard()
        self._p(unc).mkdir(parents=True, exist_ok=exist_ok)

    def mkdir(self, unc, **kwargs):
        self._wguard()
        self._p(unc).mkdir()

    def remove(self, unc, **kwargs):
        self._wguard()
        self._p(unc).unlink()

    def rmdir(self, unc, **kwargs):
        self._wguard()
        self._p(unc).rmdir()

    def replace(self, src, dst, **kwargs):
        self._wguard()
        os.replace(self._p(src), self._p(dst))

    def rmtree(self, unc, **kwargs):
        self._wguard()
        shutil.rmtree(self._p(unc))

    def isdir(self, unc):
        self._guard()
        return self._p(unc).is_dir()
