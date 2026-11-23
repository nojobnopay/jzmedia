"""Recognize the registered video directory before reconciling catalogue records."""
import os
import secrets
import stat
from contextlib import contextmanager

from . import storage
from .fsutil import file_birthtime_ns
from .library_mutex import library_mutation_lock


class RootReview(storage.StorageOffline):
    def __init__(self, backend, token, reason):
        super().__init__(reason)
        self.review = {"library_id": backend.library_id, "name": backend.name,
                       "token": token, "reason": reason}


def _root_stamp(info, birth, change, driver):
    if not stat.S_ISDIR(info.st_mode):
        return None
    volume_inode = f"{driver}{info.st_dev}:{info.st_ino}"
    if birth and birth > 0:
        return f"{volume_inode}:birth:{birth}"
    if not info.st_ino or change is None or change <= 0:
        return None
    # Directory ctime also changes on legitimate changes to children. If it
    # differs we need independent evidence before accepting the directory again.
    return f"{volume_inode}:change:{change}"


def _local_identity(path):
    try:
        path = os.path.realpath(path)
        info = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise storage.StorageError(f"读取库根目录失败: {exc}") from exc
    return _root_stamp(info, file_birthtime_ns(path, info), info.st_ctime_ns, "")


def register_local(c, library_id, path):
    identity = _local_identity(path)
    if identity:
        c.execute("INSERT OR IGNORE INTO library_scan_roots(library_id, accepted) VALUES(?,?)",
                  (library_id, identity))


def identity(backend):
    path = backend.abs_path("")
    if path is not None:
        value = _local_identity(path)
        if value:
            return value
    else:
        try:
            info = backend.scan_root_info()
            # smbclient exposes server CreationTime as ctime and ChangeTime separately.
            change = getattr(info, "st_chgtime_ns", None)
            birth = info.st_ctime_ns if change is not None else getattr(info, "st_birthtime_ns", None)
            value = _root_stamp(info, birth, change if change is not None else info.st_ctime_ns, "smb:")
            if value:
                return value
        except storage.StorageNotFound:
            pass
    raise storage.StorageOffline(f"库「{backend.name}」的根目录不可用")


def require_same(backend, original):
    observed = identity(backend)
    if observed != original:
        # Attach a confirmation token for the newly observed directory if it is
        # still present. Even if the original disk returns meanwhile, restart this scan.
        same_weak_dir = (":change:" in original and original.rsplit(":", 2)[0] == observed.rsplit(":", 2)[0])
        if same_weak_dir and (not backend.library_id or checked(backend) == observed):
            return
        if not same_weak_dir and backend.library_id:
            checked(backend)
        raise storage.StorageOffline("扫描期间库根目录已更换，请核对存储后重新扫描")


@contextmanager
def upload_root(backend):
    """Keep a weakly identified directory open while our own upload changes its ctime."""
    from . import store
    original = checked(backend) if backend.library_id else identity(backend)
    if ":change:" not in original:
        yield lambda: require_same(backend, original)
        return
    local = backend.abs_path("")
    try:
        handle = os.scandir(local) if local is not None else backend.open_scan_root()
    except OSError as exc:
        raise storage.StorageOffline("上传前无法打开库根目录，请检查存储") from exc
    with handle:
        if identity(backend) != original:
            raise storage.StorageOffline("上传前库根目录已变化，请重试")
        volume_inode = original.rsplit(":", 2)[0]

        def verify():
            observed = identity(backend)
            if observed.rsplit(":", 2)[0] != volume_inode:
                raise storage.StorageOffline("上传期间库根目录已更换，请核对存储")
            return observed

        try:
            yield verify
        finally:
            # Both publishing and cleaning a failed transfer modify the same
            # directory. Its open handle prevents inode reuse during this check.
            try:
                observed = verify()
            except storage.StorageError:
                observed = None
            if observed and observed != original and backend.library_id:
                with store._lock, store._conn() as c:
                    c.execute("UPDATE library_scan_roots SET accepted=? WHERE library_id=?"
                              " AND accepted=? AND candidate=''", (observed, backend.library_id, original))


def _known_file_exists(backend, paths):
    if backend.abs_path("") is not None or len(paths) == 1:
        stat_file = getattr(backend, "stat_uncached", backend.stat)
        for path in paths:
            try:
                if not stat_file(path).is_dir:
                    return True
            except storage.StorageNotFound:
                continue
        return False
    # A replaced SMB share can have thousands of missing catalogue paths. List
    # only known, present directories; an empty share requires one directory read.
    names, children = {}, {}
    for path in paths:
        directory, name = os.path.split(path)
        names.setdefault(directory, set()).add(name)
        while directory:
            parent, child = os.path.split(directory)
            siblings = children.setdefault(parent, set())
            if child in siblings:
                break
            siblings.add(child)
            directory = parent
    pending = [""]
    while pending:
        directory = pending.pop()
        try:
            entries = backend.list_uncached(directory)
        except storage.StorageNotFound:
            continue
        files, folders = names.get(directory, ()), children.get(directory, ())
        for entry in entries:
            name = entry["name"]
            if entry["is_dir"] and name in folders:
                pending.append(os.path.join(directory, name))
            elif not entry["is_dir"] and name in files:
                return True
    return False


def checked(backend):
    """An untracked legacy library needs evidence of a known file or explicit approval."""
    from . import store
    lid = backend.library_id
    with library_mutation_lock(lid):
        observed = identity(backend)
        with store._lock, store._conn() as c:
            row = c.execute("SELECT * FROM library_scan_roots WHERE library_id=?", (lid,)).fetchone()
            if row and row["accepted"] == observed:
                if row["candidate"]:
                    c.execute("UPDATE library_scan_roots SET candidate='', token='' WHERE library_id=?", (lid,))
                return observed
            accepted = row["accepted"] if row else ""
            volume_inode = observed.rsplit(":", 2)[0]
            needs_evidence = (not accepted or accepted == volume_inode
                              or accepted.startswith(volume_inode + ":change:"))
            paths = [] if not needs_evidence else [r[0] for r in c.execute(
                "SELECT file_path FROM main.movies WHERE library_id=? UNION ALL "
                "SELECT file_path FROM main.tv_episodes WHERE library_id=? UNION ALL "
                "SELECT file_path FROM main.extras WHERE library_id=?", (lid, lid, lid))]
        establish = needs_evidence and (not paths or _known_file_exists(backend, paths))
        if paths and identity(backend) != observed:
            raise storage.StorageOffline("读取期间库根目录已更换，请重新扫描")
        if row and row["candidate"] == observed and not establish:
            raise RootReview(backend, row["token"], _reason(accepted))
        with store._lock, store._conn() as c:
            if establish:
                c.execute("INSERT OR REPLACE INTO library_scan_roots(library_id, accepted) VALUES(?,?)",
                          (lid, observed))
                return observed
            token = secrets.token_urlsafe(24)
            c.execute("INSERT OR REPLACE INTO library_scan_roots(library_id, accepted, candidate, token)"
                      " VALUES(?,?,?,?)", (lid, accepted, observed, token))
        raise RootReview(backend, token, _reason(accepted))


def _reason(accepted):
    return ("库根目录的存储身份已变化，请核对是否更换了媒体盘或目录。" if accepted
            else "尚无库根目录身份记录，当前目录中未找到已入库文件，请核对存储。")


def accept(backend, token):
    from . import store
    lid = backend.library_id
    with library_mutation_lock(lid):
        observed = identity(backend)
        if not observed or not token:
            return False
        with store._lock, store._conn() as c:
            return bool(c.execute("UPDATE library_scan_roots SET accepted=candidate, candidate='', token=''"
                                  " WHERE library_id=? AND candidate=? AND token=?",
                                  (lid, observed, token)).rowcount)
