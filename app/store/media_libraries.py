"""store.media_libraries：媒体库（存储连接/根）读写与视频库级联（v17 两层模型）。

- 媒体库持有连接类字段（source/path/smb_*/nfs_*/read_only/auto_mount/能力状态/存储身份）。
- 视频库（`store.libraries`）以 `media_library_id + subpath` 挂在下面；本模块负责
  建/改/删/检查、身份与嵌套守卫、路径派生同步。
- 删媒体库级联清掉其全部视频库记录（磁盘文件与远端数据绝不触碰）。
"""
import os
import sqlite3
import time

from .. import secrets
from ..db import mount_point
from ._base import _conn, _identity_of, _lock, logger
from .libraries import (SOURCES, _delete_ids, _delete_video_rows, _ensure_local_subdir,
                        _insert_video, _norm_choice, _norm_subpath, _recompute_library_paths,
                        _sub_overlap, default_library, get_library, list_libraries)

__all__ = ['list_media_libraries', 'get_media_library', 'default_media_library',
           'set_media_status', 'public_media_library', 'create_media_library',
           'update_media_library', 'delete_media_library', 'video_libraries_of',
           'media_counts', 'library_ids_for_media', 'media_id_for_library',
           'default_media_id']

_SECRET_FIELDS = ("smb_password", "nfs_password")


def _now() -> int:
    return int(time.time())


def _invalidate_paths() -> None:
    try:
        from .. import library_paths
        library_paths.invalidate_cache()
    except Exception:
        pass


def _real(p: str) -> str:
    try:
        return os.path.realpath(os.path.normpath(str(p or "")))
    except (OSError, ValueError):
        return os.path.normpath(str(p or ""))


def _is_within(a: str, b: str) -> bool:
    """a 是否在 b 子树内（含相等）。"""
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def list_media_libraries(only_enabled: bool = False) -> list[dict]:
    with _lock, _conn() as c:
        sql = "SELECT * FROM media_libraries"
        if only_enabled:
            sql += " WHERE enabled=1"
        sql += " ORDER BY sort_order, id"
        try:
            rows = c.execute(sql).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]


def get_media_library(media_library_id: int) -> dict | None:
    try:
        mid = int(media_library_id)
    except (TypeError, ValueError):
        return None
    with _lock, _conn() as c:
        try:
            row = c.execute("SELECT * FROM media_libraries WHERE id=?", (mid,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return dict(row) if row else None


def default_media_library() -> dict | None:
    rows = list_media_libraries()
    if not rows:
        return None
    enabled = [r for r in rows if int(r.get("enabled") or 0)]
    return enabled[0] if enabled else rows[0]


def set_media_status(media_library_id: int, status: str, error: str = "") -> None:
    """挂载/可读性检查后落库（错误信息由调用方脱敏后传入）。"""
    try:
        mid = int(media_library_id)
    except (TypeError, ValueError):
        return
    with _lock, _conn() as c:
        try:
            c.execute("UPDATE media_libraries SET last_status=?, last_error=?,"
                      " last_check_at=?, updated_at=? WHERE id=?",
                      (str(status or ""), str(error or "")[:300], _now(), _now(), mid))
        except sqlite3.OperationalError as e:
            logger.debug("set media status failed mid=%s: %s", mid, e)


def media_counts(media_library_id: int) -> dict:
    """媒体库记录计数：movies/episodes/shows（跨其全部视频库）。"""
    try:
        mid = int(media_library_id)
    except (TypeError, ValueError):
        return {"movies": 0, "episodes": 0, "shows": 0}
    out = {"movies": 0, "episodes": 0, "shows": 0}
    sub = "(SELECT id FROM libraries WHERE media_library_id=?)"
    with _lock, _conn() as c:
        for key, table in (("movies", "movies"), ("episodes", "tv_episodes"),
                           ("shows", "tv_shows")):
            try:
                out[key] = int(c.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE library_id IN {sub}",
                    (mid,)).fetchone()[0])
            except sqlite3.OperationalError:
                pass
    return out


def video_libraries_of(media_library_id: int, only_enabled: bool = False) -> list[dict]:
    """媒体库下的视频库（增强视图）；可选只看启用态。"""
    try:
        mid = int(media_library_id)
    except (TypeError, ValueError):
        return []
    rows = [l for l in list_libraries() if int(l.get("media_library_id") or 0) == mid]
    if only_enabled:
        rows = [l for l in rows if l.get("effective_enabled")]
    return rows


def library_ids_for_media(media_library_id: int) -> list[int]:
    """媒体库 → 其全部视频库 id（读接口聚合用；未知媒体库返回空列表）。"""
    return [int(v["id"]) for v in video_libraries_of(media_library_id)]


def media_id_for_library(library_id: int) -> int | None:
    """视频库 id → 所属媒体库 id（旧参数兼容映射用）。"""
    try:
        lib = get_library(int(library_id))
    except (TypeError, ValueError):
        return None
    if not lib:
        return None
    return int(lib.get("media_library_id") or 0) or None


def default_media_id() -> int | None:
    """默认媒体库 id：默认视频库所属媒体库，其次第一个启用媒体库。"""
    lib = default_library()
    if lib and lib.get("media_library_id"):
        return int(lib["media_library_id"])
    m = default_media_library()
    return int(m["id"]) if m else None


def public_media_library(lib: dict | None) -> dict | None:
    """API 输出视图：凭据字段只返回是否已设置（与 public_library 同规则）。"""
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
    url = str(smb.get("url") or smb.get("address") or "").strip()
    if url:
        from ..smburl import parse_smb_url
        p = parse_smb_url(url)
        if p.get("error"):
            raise ValueError(p["error"])
        host = p["host"]
        share = p["share"]
        subpath = p["subpath"]
        if p.get("username") and not str(smb.get("username") or "").strip():
            smb = {**smb, "username": p["username"]}
    else:
        host = str(smb.get("host") or "").strip()
        share = str(smb.get("share") or "").strip().strip("/")
        subpath = _norm_subpath(smb.get("subpath"))
    out = {
        "smb_host": host,
        "smb_share": share,
        "smb_subpath": subpath,
        "smb_domain": str(smb.get("domain") or "").strip(),
        "smb_username": str(smb.get("username") or "").strip(),
        "smb_options": str(smb.get("options") or "").strip(),
        "smb_connect_host": str(smb.get("connect_host") or "").strip(),
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


def _check_identity(ident: str, exclude_id=None) -> None:
    """防重复入库（指导 §26）：同一存储身份只允许一个媒体库；不做自动合并。"""
    if not ident:
        return
    for lib in list_media_libraries():
        if exclude_id is not None and int(lib.get("id") or 0) == int(exclude_id):
            continue
        if str(lib.get("storage_identity") or "") == ident:
            raise ValueError(
                f"已存在指向同一存储的媒体库「{lib.get('name')}」；请直接使用该库，"
                "如要改变访问方式（直读/挂载/宿主挂载）在原库上操作即可，无需新建")


def _check_no_nesting(source: str, path: str, smb_host: str = "", smb_share: str = "",
                      smb_subpath: str = "", nfs_export: str = "",
                      exclude_id=None) -> None:
    """媒体库根之间不允许重叠/嵌套（防双扫描与定位歧义）。"""
    new_local = _real(path) if source == "local" else ""
    new_smb = (str(smb_host or "").strip().lower(),
               str(smb_share or "").strip().strip("/").lower(),
               str(smb_subpath or "").strip("/").lower())
    new_nfs = str(nfs_export or "").strip("/").lower()
    for lib in list_media_libraries():
        if exclude_id is not None and int(lib.get("id") or 0) == int(exclude_id):
            continue
        src = str(lib.get("source") or "local")
        name = lib.get("name")
        if source == "local" and src == "local":
            other = _real(lib.get("path") or "")
            if new_local and other and (_is_within(new_local, other)
                                        or _is_within(other, new_local)):
                raise ValueError(
                    f"媒体库根与现有库「{name}」重叠（{lib.get('path')}）；请选独立目录")
        elif source == "smb" and src == "smb":
            host, share, sub = (str(lib.get("smb_host") or "").strip().lower(),
                                str(lib.get("smb_share") or "").strip().strip("/").lower(),
                                str(lib.get("smb_subpath") or "").strip("/").lower())
            if new_smb[:2] != (host, share):
                continue
            if _sub_overlap(new_smb[2], sub):
                raise ValueError(
                    f"共享目录与现有媒体库「{name}」重叠（{share}/{sub or ''}）；"
                    "请选独立目录或合并为一个媒体库")
        elif source == "nfs" and src == "nfs":
            other = str(lib.get("nfs_export") or "").strip("/").lower()
            if new_nfs and other and (new_nfs == other or new_nfs.startswith(other + "/")
                                      or other.startswith(new_nfs + "/")):
                raise ValueError(f"NFS export 与现有媒体库「{name}」重叠；请选独立 export")


def _video_path(media: dict, subpath: str) -> str:
    base = str(media.get("path") or "")
    return os.path.join(base, subpath) if subpath else base


def create_media_library(name: str, source: str = "local", path: str = "",
                         read_only: bool = False, auto_mount: bool = True,
                         enabled: bool = True, sort_order: int = 0,
                         smb: dict | None = None, nfs: dict | None = None,
                         video_libraries: list[dict] | None = None) -> dict:
    """新建媒体库（至少一个视频库；本地根必须存在，视频子目录按需创建）。"""
    name = str(name or "").strip()
    if not name:
        raise ValueError("媒体库名不能为空")
    source = _norm_choice(source, SOURCES, "source", "local")
    videos = [v or {} for v in (video_libraries or [])]
    if not videos:
        raise ValueError("媒体库至少需要一个视频库（电影/剧集）")
    path = str(path or "").strip()
    if source == "local":
        if not path:
            raise ValueError("本地媒体库必须提供路径")
        if not os.path.isdir(path):
            raise ValueError(f"路径不存在或不是目录: {path}")
    else:
        path = ""
    row = {
        "name": name, "source": source, "path": path,
        "read_only": 1 if read_only else 0,
        "auto_mount": 1 if auto_mount else 0,
        "enabled": 1 if enabled else 0,
        "sort_order": int(sort_order or 0),
        "created_at": _now(), "updated_at": _now(),
    }
    if source == "smb":
        row.update(_smb_fields(smb))
        if not row["smb_host"] or not row["smb_share"]:
            raise ValueError("SMB 媒体库需要主机与共享名")
    elif source == "nfs":
        row.update(_nfs_fields(nfs))
        if not row["nfs_export"]:
            raise ValueError("NFS 媒体库需要 export")
    ident = _identity_of(row)
    _check_identity(ident)          # 完全相同的存储 → 明确提示（先于重叠判定）
    if source == "local":
        _check_no_nesting("local", path)
    elif source == "smb":
        _check_no_nesting("smb", "", row["smb_host"], row["smb_share"], row["smb_subpath"])
    elif source == "nfs":
        _check_no_nesting("nfs", "", nfs_export=row["nfs_export"])
    row["storage_identity"] = ident

    # 视频库清单预校验（名字去重 + 子路径不重叠）
    subs: list[str] = []
    names: set[str] = set()
    for v in videos:
        sub = _norm_subpath(v.get("subpath"))
        for s in subs:
            if _sub_overlap(sub, s):
                raise ValueError(f"视频库子路径重复/嵌套: {sub or '/'} 与 {s or '/'}")
        subs.append(sub)
        vname = str(v.get("name") or "").strip() or (sub.rsplit("/", 1)[-1] if sub else name)
        if vname in names:
            raise ValueError(f"视频库名重复: {vname}")
        names.add(vname)

    cols = ", ".join(row.keys())
    qs = ", ".join("?" for _ in row)
    with _lock, _conn() as c:
        try:
            cur = c.execute(f"INSERT INTO media_libraries({cols}) VALUES({qs})",
                            tuple(row.values()))
        except sqlite3.IntegrityError as e:
            raise ValueError(f"媒体库名已存在: {name}") from e
        mid = int(cur.lastrowid)
        if source != "local":
            path = mount_point(mid)
            c.execute("UPDATE media_libraries SET path=? WHERE id=?", (path, mid))
        media = {**row, "id": mid, "path": path}
        for v in videos:
            sub = _norm_subpath(v.get("subpath"))
            _ensure_local_subdir(media, sub)
            _insert_video(c, media, str(v.get("name") or ""),
                          str(v.get("kind") or "movie"), sub,
                          naming_profile=str(v.get("naming_profile") or "kodi"),
                          artwork_mode=str(v.get("artwork_mode") or "nfo"),
                          organize_target=str(v.get("organize_target") or "电影"),
                          inbox_dir=str(v.get("inbox_dir") or "待整理"),
                          metadata_providers=str(v.get("metadata_providers") or ""),
                          enabled=bool(v.get("enabled", True)),
                          sort_order=int(v.get("sort_order") or 0))
    logger.info("新建媒体库 id=%s name=%s source=%s path=%s 视频库=%s",
                mid, name, source, path, len(videos))
    _invalidate_paths()
    return get_media_library(mid) or {}


_UPDATE_SCALARS = ("name", "read_only", "auto_mount", "enabled", "sort_order")


def update_media_library(media_library_id: int, **fields) -> dict | None:
    """媒体库局部更新（连接/路径/只读/开关；smb/nfs 传 dict；密码空串=清空）。"""
    cur = get_media_library(media_library_id)
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
                raise ValueError("媒体库名不能为空")
        elif k in ("read_only", "auto_mount", "enabled"):
            v = 1 if v else 0
        elif k == "sort_order":
            v = int(v or 0)
        data[k] = v
    if "path" in fields and fields["path"] is not None:
        if str(cur.get("source") or "local") != "local":
            raise ValueError("远程媒体库路径由挂载点自动决定，不可修改")
        new_path = str(fields["path"]).strip()
        if not new_path:
            raise ValueError("路径不能为空")
        if not os.path.isdir(new_path):
            raise ValueError(f"路径不存在或不是目录: {new_path}")
        counts = media_counts(int(media_library_id))
        if counts["movies"] or counts["episodes"]:
            raise ValueError("媒体库内已有影片/剧集记录，不能改路径（请先清理记录或新建库）")
        _check_no_nesting("local", new_path, exclude_id=int(media_library_id))
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
    source = str(cur.get("source") or "local")
    ident = _identity_of({**cur, **data})
    if ident != (cur.get("storage_identity") or ""):
        _check_identity(ident, exclude_id=int(media_library_id))
        data["storage_identity"] = ident
    if source == "smb" and any(k in data for k in ("smb_host", "smb_share", "smb_subpath")):
        host = data.get("smb_host", cur.get("smb_host")) or ""
        share = data.get("smb_share", cur.get("smb_share")) or ""
        sub = data.get("smb_subpath", cur.get("smb_subpath")) or ""
        _check_no_nesting("smb", "", host, share, sub, exclude_id=int(media_library_id))
    if source == "nfs" and "nfs_export" in data:
        _check_no_nesting("nfs", "", nfs_export=data.get("nfs_export") or "",
                          exclude_id=int(media_library_id))
    data["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in data)
    with _lock, _conn() as c:
        try:
            c.execute(f"UPDATE media_libraries SET {cols} WHERE id=?",
                      (*data.values(), int(media_library_id)))
        except sqlite3.IntegrityError as e:
            raise ValueError(f"媒体库名已存在: {data.get('name')}") from e
        if "path" in data:
            _recompute_library_paths(c, int(media_library_id))
    logger.info("更新媒体库 id=%s 字段=%s", media_library_id, list(data.keys()))
    _invalidate_paths()
    return get_media_library(media_library_id)


def delete_media_library(media_library_id: int) -> dict | None:
    """删媒体库事务：级联清掉其全部视频库记录（只清 DB，不动磁盘/远端文件）。"""
    cur = get_media_library(media_library_id)
    if not cur:
        return None
    mid = int(media_library_id)
    stats = {"library": cur.get("name"), "movies": 0, "extras": 0, "scan_state": 0,
             "tv_shows": 0, "tv_episodes": 0, "collections": 0, "video_libraries": 0}
    with _lock, _conn() as c:
        vids = [int(r["id"]) for r in c.execute(
            "SELECT id FROM libraries WHERE media_library_id=?", (mid,))]
        for vid in vids:
            sub = _delete_video_rows(c, vid)
            for k in ("movies", "extras", "scan_state", "tv_shows", "tv_episodes"):
                stats[k] += int(sub.get(k) or 0)
            stats["video_libraries"] += 1
        # 合集属媒体库级（v18）：随媒体库删除，连同成员
        col_ids = [int(r["id"]) for r in c.execute(
            "SELECT id FROM collections WHERE media_library_id=?", (mid,))]
        _delete_ids(c, "collection_members", "collection_id", col_ids)
        stats["collections"] = _delete_ids(c, "collections", "id", col_ids)
        _delete_ids(c, "libraries", "id", vids)
        c.execute("DELETE FROM media_libraries WHERE id=?", (mid,))
    logger.info("删除媒体库 id=%s name=%s 记录清理: %s", mid, cur.get("name"), stats)
    _invalidate_paths()
    return stats
