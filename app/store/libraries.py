"""store.libraries：库注册表读写（MULTI_LIBRARY_PLAN D1，一库一根）。

- A 阶段提供读取与状态更新；B 阶段补 CRUD 与删库事务（只清 DB，绝不动磁盘文件）。
- 远程凭据只写不读：入库前经 app.secrets Fernet 加密；`public_library()` 供 API 脱敏输出。
"""
import os
import sqlite3
import time

from .. import secrets
from ..db import mount_point
from ._base import DEFAULT_LIBRARY_ID, _conn, _lock, logger

__all__ = ['list_libraries', 'get_library', 'default_library', 'set_library_status',
           'create_library', 'update_library', 'delete_library',
           'library_movie_count', 'public_library']

KINDS = ("movie", "tv")
SOURCES = ("local", "smb", "nfs")
NAMING_PROFILES = ("plex", "kodi", "off")
ARTWORK_MODES = ("none", "nfo", "nfo_art")

# API 输出脱敏字段（密码永不回传）
_SECRET_FIELDS = ("smb_password", "nfs_password")


def _now() -> int:
    return int(time.time())


def _norm_choice(value, allowed, field: str, default: str | None = None) -> str:
    v = str(value if value is not None else default or "").strip().lower()
    if v not in allowed:
        raise ValueError(f"invalid {field}: {value!r}")
    return v


def _real(p: str) -> str:
    try:
        return os.path.realpath(os.path.normpath(str(p or "")))
    except (OSError, ValueError):
        return os.path.normpath(str(p or ""))


def _is_within(a: str, b: str) -> bool:
    """a 是否在 b 子树内（含相等）。"""
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def _check_no_nesting(new_path: str, exclude_id=None) -> None:
    """库根不允许与现有库重叠/嵌套（D1 一库一根；防 locate 歧义与重复扫描）。"""
    real = _real(new_path)
    for lib in list_libraries():
        if exclude_id is not None and int(lib.get("id") or 0) == int(exclude_id):
            continue
        other = _real(lib.get("path") or "")
        if not other:
            continue
        if _is_within(real, other) or _is_within(other, real):
            raise ValueError(
                f"库根与现有库「{lib.get('name')}」重叠（{lib.get('path')}）；"
                "请选独立目录")


def list_libraries(only_enabled: bool = False) -> list[dict]:
    with _lock, _conn() as c:
        sql = "SELECT * FROM libraries"
        if only_enabled:
            sql += " WHERE enabled=1"
        sql += " ORDER BY sort_order, id"
        try:
            rows = c.execute(sql).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]


def get_library(library_id: int) -> dict | None:
    try:
        lid = int(library_id)
    except (TypeError, ValueError):
        return None
    with _lock, _conn() as c:
        try:
            row = c.execute("SELECT * FROM libraries WHERE id=?", (lid,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return dict(row) if row else None


def default_library() -> dict | None:
    """默认库：优先 DEFAULT_LIBRARY_ID，其次第一个启用库，最后任意一行。"""
    with _lock, _conn() as c:
        try:
            row = c.execute(
                "SELECT * FROM libraries ORDER BY "
                "CASE WHEN id=? THEN 0 ELSE 1 END, sort_order, id LIMIT 1",
                (DEFAULT_LIBRARY_ID,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return dict(row) if row else None


def set_library_status(library_id: int, status: str, error: str = "") -> None:
    """挂载/可读性检查后落库（错误信息由调用方脱敏后传入）。"""
    try:
        lid = int(library_id)
    except (TypeError, ValueError):
        return
    with _lock, _conn() as c:
        try:
            c.execute("UPDATE libraries SET last_status=?, last_error=?, last_check_at=?,"
                      " updated_at=? WHERE id=?",
                      (str(status or ""), str(error or "")[:300], _now(), _now(), lid))
        except sqlite3.OperationalError as e:
            logger.debug("set library status failed lid=%s: %s", lid, e)


def library_movie_count(library_id: int) -> int:
    try:
        lid = int(library_id)
    except (TypeError, ValueError):
        return 0
    with _lock, _conn() as c:
        try:
            return int(c.execute("SELECT COUNT(*) FROM movies WHERE library_id=?",
                                 (lid,)).fetchone()[0])
        except sqlite3.OperationalError:
            return 0


def public_library(lib: dict | None) -> dict | None:
    """API 输出视图：凭据字段只返回是否已设置。"""
    if not lib:
        return None
    out = dict(lib)
    for k in _SECRET_FIELDS:
        out[f"{k}_set"] = bool(out.get(k))
        out.pop(k, None)
    for k in ("read_only", "auto_mount", "enabled"):
        if k in out:
            out[k] = bool(out[k])
    return out


def _smb_fields(smb: dict | None) -> dict:
    smb = smb or {}
    out = {
        "smb_host": str(smb.get("host") or "").strip(),
        "smb_share": str(smb.get("share") or "").strip().strip("/"),
        "smb_subpath": str(smb.get("subpath") or "").strip().strip("/"),
        "smb_domain": str(smb.get("domain") or "").strip(),
        "smb_username": str(smb.get("username") or "").strip(),
        "smb_options": str(smb.get("options") or "").strip(),
    }
    pwd = smb.get("password")
    if pwd is not None and str(pwd) != "":
        out["smb_password"] = secrets.encrypt_str(str(pwd))
    return out


def _nfs_fields(nfs: dict | None) -> dict:
    nfs = nfs or {}
    out = {
        "nfs_export": str(nfs.get("export") or "").strip(),
        "nfs_options": str(nfs.get("options") or "").strip(),
    }
    pwd = nfs.get("password")
    if pwd is not None and str(pwd) != "":
        out["nfs_password"] = secrets.encrypt_str(str(pwd))
    return out


def create_library(name: str, kind: str = "movie", source: str = "local",
                   path: str = "", read_only: bool = False, auto_mount: bool = True,
                   enabled: bool = True, sort_order: int = 0,
                   naming_profile: str = "kodi", artwork_mode: str = "nfo",
                   organize_target: str = "电影", inbox_dir: str = "待整理",
                   metadata_providers: str = "", smb: dict | None = None,
                   nfs: dict | None = None) -> dict:
    name = str(name or "").strip()
    if not name:
        raise ValueError("库名不能为空")
    kind = _norm_choice(kind, KINDS, "kind", "movie")
    source = _norm_choice(source, SOURCES, "source", "local")
    naming_profile = _norm_choice(naming_profile, NAMING_PROFILES, "naming_profile", "kodi")
    artwork_mode = _norm_choice(artwork_mode, ARTWORK_MODES, "artwork_mode", "nfo")
    path = str(path or "").strip()
    if source == "local":
        if not path:
            raise ValueError("本地库必须提供路径")
        if not os.path.isdir(path):
            raise ValueError(f"路径不存在或不是目录: {path}")
        _check_no_nesting(path)
    else:
        # 远程库挂载点由应用内挂载统一决定（data_dir/mounts/lib_<id>），建行后派生
        path = ""
    row = {
        "name": name, "kind": kind, "source": source, "path": path,
        "read_only": 1 if read_only else 0,
        "auto_mount": 1 if auto_mount else 0,
        "enabled": 1 if enabled else 0,
        "sort_order": int(sort_order or 0),
        "naming_profile": naming_profile, "artwork_mode": artwork_mode,
        "organize_target": str(organize_target or "电影").strip() or "电影",
        "inbox_dir": str(inbox_dir or "待整理").strip() or "待整理",
        "metadata_providers": str(metadata_providers or "").strip(),
        "created_at": _now(), "updated_at": _now(),
    }
    if source == "smb":
        row.update(_smb_fields(smb))
        if not row["smb_host"] or not row["smb_share"]:
            raise ValueError("SMB 库需要主机与共享名")
    elif source == "nfs":
        row.update(_nfs_fields(nfs))
        if not row["nfs_export"]:
            raise ValueError("NFS 库需要 export")
    cols = ", ".join(row.keys())
    qs = ", ".join("?" for _ in row)
    with _lock, _conn() as c:
        try:
            cur = c.execute(f"INSERT INTO libraries({cols}) VALUES({qs})",
                            tuple(row.values()))
        except sqlite3.IntegrityError as e:
            raise ValueError(f"库名已存在: {name}") from e
        lid = int(cur.lastrowid)
        if source != "local":
            path = mount_point(lid)
            c.execute("UPDATE libraries SET path=? WHERE id=?", (path, lid))
    logger.info("新建库 id=%s name=%s kind=%s source=%s path=%s",
                lid, name, kind, source, path)
    return get_library(lid) or {}


_UPDATE_SCALARS = ("name", "kind", "read_only", "auto_mount", "enabled", "sort_order",
                   "naming_profile", "artwork_mode", "organize_target", "inbox_dir",
                   "metadata_providers")


def update_library(library_id: int, **fields) -> dict | None:
    """局部更新（缺省字段不动；smb/nfs 传 dict；密码空串=清空）。"""
    cur = get_library(library_id)
    if not cur:
        return None
    data: dict = {}
    for k in _UPDATE_SCALARS:
        if k not in fields or fields[k] is None:
            continue
        v = fields[k]
        if k == "name":
            v = str(v).strip()
            if not v:
                raise ValueError("库名不能为空")
        elif k == "kind":
            v = _norm_choice(v, KINDS, "kind")
        elif k == "naming_profile":
            v = _norm_choice(v, NAMING_PROFILES, "naming_profile")
        elif k == "artwork_mode":
            v = _norm_choice(v, ARTWORK_MODES, "artwork_mode")
        elif k in ("read_only", "auto_mount", "enabled"):
            v = 1 if v else 0
        elif k == "sort_order":
            v = int(v or 0)
        else:
            v = str(v).strip()
        data[k] = v
    if "path" in fields and fields["path"] is not None:
        new_path = str(fields["path"]).strip()
        if str(cur.get("source") or "local") != "local":
            raise ValueError("远程库路径由挂载点自动决定，不可修改")
        if library_movie_count(library_id) > 0:
            raise ValueError("库内已有影片记录，不能改路径（请先清理记录或新建库）")
        if not new_path:
            raise ValueError("路径不能为空")
        if not os.path.isdir(new_path):
            raise ValueError(f"路径不存在或不是目录: {new_path}")
        _check_no_nesting(new_path, exclude_id=int(library_id))
        data["path"] = new_path
    if "smb" in fields and fields["smb"] is not None:
        data.update(_smb_fields(fields["smb"]))
    if "nfs" in fields and fields["nfs"] is not None:
        data.update(_nfs_fields(fields["nfs"]))
    for k in _SECRET_FIELDS:
        if k in fields and fields[k] == "":
            data[k] = ""    # 显式空串=清空凭据
    if not data:
        return cur
    data["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in data)
    with _lock, _conn() as c:
        try:
            c.execute(f"UPDATE libraries SET {cols} WHERE id=?",
                      (*data.values(), int(library_id)))
        except sqlite3.IntegrityError as e:
            raise ValueError(f"库名已存在: {data.get('name')}") from e
    return get_library(library_id)


def _delete_ids(c, table: str, column: str, ids: list[int]) -> int:
    """分块 IN 删除（防 SQLite 变量数上限），返回删除行数。"""
    if not ids:
        return 0
    n = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ph = ",".join("?" for _ in chunk)
        n += int(c.execute(f"DELETE FROM {table} WHERE {column} IN ({ph})",
                           chunk).rowcount or 0)
    return n


def delete_library(library_id: int) -> dict | None:
    """删库事务（§10.4）：只清 DB 记录，绝不触碰磁盘媒体文件。

    保留：tmdb_cache / match_index / persons / 海报/头像文件。
    """
    cur = get_library(library_id)
    if not cur:
        return None
    lid = int(library_id)
    stats = {"library": cur.get("name"), "movies": 0, "extras": 0, "scan_state": 0,
             "tv_shows": 0, "tv_episodes": 0, "collections": 0}
    with _lock, _conn() as c:
        ids = [int(r["id"]) for r in c.execute(
            "SELECT id FROM movies WHERE library_id=?", (lid,))]
        _delete_ids(c, "movies_fts", "rowid", ids)
        _delete_ids(c, "movie_person", "movie_id", ids)
        if ids:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                ph = ",".join("?" for _ in chunk)
                c.execute(f"DELETE FROM media_info WHERE kind='movie' AND item_id IN ({ph})",
                          chunk)
                c.execute(f"DELETE FROM playback_progress WHERE kind='movie'"
                          f" AND item_id IN ({ph})", chunk)
        stats["movies"] = _delete_ids(c, "movies", "id", ids)
        stats["extras"] = int(c.execute(
            "DELETE FROM extras WHERE library_id=?", (lid,)).rowcount or 0)
        stats["scan_state"] = int(c.execute(
            "DELETE FROM scan_state WHERE library_id=?", (lid,)).rowcount or 0)
        stats["tv_episodes"] = int(c.execute(
            "DELETE FROM tv_episodes WHERE library_id=?", (lid,)).rowcount or 0)
        stats["tv_shows"] = int(c.execute(
            "DELETE FROM tv_shows WHERE library_id=?", (lid,)).rowcount or 0)
        col_ids = [int(r["id"]) for r in c.execute(
            "SELECT id FROM collections WHERE library_id=?", (lid,))]
        _delete_ids(c, "collection_members", "collection_id", col_ids)
        stats["collections"] = _delete_ids(c, "collections", "id", col_ids)
        c.execute("DELETE FROM libraries WHERE id=?", (lid,))
    logger.info("删除库 id=%s name=%s 记录清理: %s", lid, cur.get("name"), stats)
    return stats
