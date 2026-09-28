"""store.libraries：视频库（媒体库根下带类型的子树）注册表读写（v17 两层模型）。

- 父级 `media_libraries` = 存储连接/根（凭据/挂载/身份/健康）；本模块只管视频库：
  `media_library_id + subpath + kind(movie|tv)`，`path` 为「媒体根 + subpath」派生生效根。
- `list_libraries/get_library/default_library` 返回**增强视图**（联表带出媒体连接字段，
  字段名沿用 source/read_only/smb_*/nfs_* 等），storage/mounts/scanner/library_paths 零感知。
- 旧扁平建库 `create_library()` 保留为兼容包装：自动建同名媒体库 + 一个根视频库。
- 删视频库只清 DB 记录（存储连接与磁盘文件不动）；删媒体库由 media_libraries 级联。
"""
import os
import sqlite3
import time

from ..db import mount_point
from ._base import DEFAULT_LIBRARY_ID, _conn, _lock, logger

__all__ = ['list_libraries', 'get_library', 'default_library', 'create_library',
           'create_video_library', 'update_library', 'delete_library',
           'library_movie_count', 'library_tv_count', 'public_library',
           'KINDS', 'SOURCES', 'NAMING_PROFILES', 'ARTWORK_MODES',
           '_norm_subpath', '_sub_overlap', '_recompute_library_paths']

KINDS = ("movie", "tv")
SOURCES = ("local", "smb", "nfs")
NAMING_PROFILES = ("plex", "kodi", "off")
ARTWORK_MODES = ("none", "nfo", "nfo_art")

# API 输出脱敏字段（密码永不回传）
_SECRET_FIELDS = ("smb_password", "nfs_password")

_VIDEO_SELECT = """
SELECT l.id, l.media_library_id, l.name, l.kind, l.subpath, l.path,
       l.enabled, l.sort_order, l.naming_profile, l.artwork_mode,
       l.organize_target, l.inbox_dir, l.metadata_providers,
       l.created_at, l.updated_at,
       m.name AS media_name, m.source, m.read_only, m.auto_mount,
       m.enabled AS media_enabled, m.path AS media_path,
       m.smb_host, m.smb_share,
       CASE WHEN m.smb_subpath != '' AND l.subpath != ''
            THEN m.smb_subpath || '/' || l.subpath
            WHEN l.subpath != '' THEN l.subpath
            ELSE m.smb_subpath END AS smb_subpath,
       m.smb_domain, m.smb_username, m.smb_password, m.smb_options, m.smb_connect_host,
       m.nfs_export, m.nfs_password, m.nfs_options, m.storage_identity,
       m.last_status, m.last_error, m.last_check_at
FROM libraries l JOIN media_libraries m ON m.id = l.media_library_id
"""


def _now() -> int:
    return int(time.time())


def _view(row) -> dict:
    """行 → 增强视图（媒体挂载点/生效启用态；供 storage/mounts/health 使用）。"""
    d = dict(row)
    d["media_mount_point"] = str(d.get("media_path") or d.get("path") or "")
    d["effective_enabled"] = (bool(int(d.get("enabled") or 0))
                              and bool(int(d.get("media_enabled") or 0)))
    return d


def _invalidate_paths() -> None:
    try:
        from .. import library_paths
        library_paths.invalidate_cache()
    except Exception:
        pass


def list_libraries(only_enabled: bool = False) -> list[dict]:
    with _lock, _conn() as c:
        sql = _VIDEO_SELECT
        if only_enabled:
            sql += " WHERE l.enabled=1 AND m.enabled=1"
        sql += " ORDER BY m.sort_order, l.sort_order, l.id"
        try:
            rows = c.execute(sql).fetchall()
        except sqlite3.OperationalError:
            return []
        return [_view(r) for r in rows]


def get_library(library_id: int) -> dict | None:
    try:
        lid = int(library_id)
    except (TypeError, ValueError):
        return None
    with _lock, _conn() as c:
        try:
            row = c.execute(_VIDEO_SELECT + " WHERE l.id=?", (lid,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return _view(row) if row else None


def default_library() -> dict | None:
    """默认视频库：优先 DEFAULT_LIBRARY_ID，其次第一个启用库，最后第一行。"""
    rows = list_libraries()
    if not rows:
        return None
    for r in rows:
        if int(r.get("id") or 0) == DEFAULT_LIBRARY_ID:
            return r
    enabled = [r for r in rows if r.get("effective_enabled")]
    return enabled[0] if enabled else rows[0]


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


def library_tv_count(library_id: int) -> int:
    """视频库内剧集数（改 kind 前的空库判定用）。"""
    try:
        lid = int(library_id)
    except (TypeError, ValueError):
        return 0
    with _lock, _conn() as c:
        try:
            return int(c.execute("SELECT COUNT(*) FROM tv_episodes WHERE library_id=?",
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
    for k in ("read_only", "auto_mount", "enabled", "media_enabled",
              "effective_enabled"):
        if k in out:
            out[k] = bool(out[k])
    return out


def _norm_choice(value, allowed, field: str, default: str | None = None) -> str:
    v = str(value if value is not None else default or "").strip().lower()
    if v not in allowed:
        raise ValueError(f"invalid {field}: {value!r}")
    return v


def _norm_subpath(value) -> str:
    """子路径归一（POSIX 相对路径；'' = 媒体库根）；非法抛 ValueError。"""
    raw = str(value if value is not None else "").strip().replace("\\", "/")
    if "\x00" in raw:
        raise ValueError("子路径非法")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ValueError("子路径必须是相对媒体库根的相对路径")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("子路径不能包含 ..")
    return "/".join(parts)


def _sub_overlap(a: str, b: str) -> bool:
    """两个视频库子路径是否重叠（含相等与嵌套；大小写不敏感，SMB 语义）。"""
    x, y = (a or "").strip("/").lower(), (b or "").strip("/").lower()
    if x == y:
        return True
    if not x or not y:
        return True   # 根与任意子目录重叠
    return x.startswith(y + "/") or y.startswith(x + "/")


def _check_subpath_free(c, media_library_id: int, subpath: str, exclude_id=None) -> None:
    for r in c.execute("SELECT id, subpath, name FROM libraries WHERE media_library_id=?",
                       (int(media_library_id),)).fetchall():
        if exclude_id is not None and int(r["id"]) == int(exclude_id):
            continue
        if _sub_overlap(subpath, str(r["subpath"] or "")):
            raise ValueError(
                f"子路径与视频库「{r['name']}」重叠（{r['subpath'] or '/'}）；请选独立子目录")


def _media_of(c, media_library_id: int) -> dict:
    row = c.execute("SELECT * FROM media_libraries WHERE id=?",
                    (int(media_library_id),)).fetchone()
    if not row:
        raise ValueError("媒体库不存在")
    return dict(row)


def _ensure_local_subdir(media: dict, subpath: str) -> None:
    """本地媒体库：按需创建视频库子目录（只读库缺失时给明确错误）。"""
    if str(media.get("source") or "local") != "local" or not subpath:
        return
    full = os.path.join(str(media.get("path") or ""), subpath)
    if os.path.isdir(full):
        return
    if int(media.get("read_only") or 0):
        raise ValueError(f"只读媒体库缺少子目录: {subpath}（请先在存储上创建）")
    try:
        os.makedirs(full, exist_ok=True)
    except OSError as e:
        raise ValueError(f"创建子目录失败: {full}（{e}）") from e


def _video_path(media: dict, subpath: str) -> str:
    base = str(media.get("path") or "")
    return os.path.join(base, subpath) if subpath else base


def _recompute_library_paths(c, media_library_id: int) -> None:
    """媒体库路径/子路径变化后重算其全部视频库的派生生效根。"""
    m = c.execute("SELECT * FROM media_libraries WHERE id=?",
                  (int(media_library_id),)).fetchone()
    if not m:
        return
    for r in c.execute("SELECT id, subpath FROM libraries WHERE media_library_id=?",
                       (int(media_library_id),)).fetchall():
        sub = str(r["subpath"] or "").strip("/")
        p = os.path.join(str(m["path"] or ""), sub) if sub else str(m["path"] or "")
        c.execute("UPDATE libraries SET path=?, updated_at=? WHERE id=?",
                  (p, _now(), int(r["id"])))


def _video_path_for_media(lib: dict) -> str:
    """媒体挂载点（远程）或本地根：mounts/storage 的展示与检查用。"""
    if str(lib.get("source") or "local") != "local":
        return mount_point(int(lib.get("media_library_id") or lib.get("id") or 0))
    return str(lib.get("media_path") or lib.get("path") or "")


def _insert_video(c, media: dict, name: str, kind: str, subpath: str,
                  naming_profile: str = "kodi", artwork_mode: str = "nfo",
                  organize_target: str = "电影", inbox_dir: str = "待整理",
                  metadata_providers: str = "", enabled: bool = True,
                  sort_order: int = 0) -> int:
    """低层插入（调用方已完成校验/建目录）；返回新视频库 id。"""
    name = str(name or "").strip() or (subpath.rsplit("/", 1)[-1] if subpath
                                       else str(media.get("name") or "库"))
    kind = _norm_choice(kind, KINDS, "kind", "movie")
    naming_profile = _norm_choice(naming_profile, NAMING_PROFILES, "naming_profile", "kodi")
    artwork_mode = _norm_choice(artwork_mode, ARTWORK_MODES, "artwork_mode", "nfo")
    row = {"media_library_id": int(media["id"]), "name": name, "kind": kind,
           "subpath": subpath, "path": _video_path(media, subpath),
           "enabled": 1 if enabled else 0, "sort_order": int(sort_order or 0),
           "naming_profile": naming_profile, "artwork_mode": artwork_mode,
           "organize_target": str(organize_target or "电影").strip() or "电影",
           "inbox_dir": str(inbox_dir or "待整理").strip() or "待整理",
           "metadata_providers": str(metadata_providers or "").strip(),
           "created_at": _now(), "updated_at": _now()}
    cols = ", ".join(row.keys())
    qs = ", ".join("?" for _ in row)
    try:
        cur = c.execute(f"INSERT INTO libraries({cols}) VALUES({qs})", tuple(row.values()))
    except sqlite3.IntegrityError as e:
        raise ValueError(f"库名已存在: {name}") from e
    return int(cur.lastrowid)


def create_video_library(media_library_id: int, name: str = "", kind: str = "movie",
                         subpath: str = "", naming_profile: str = "kodi",
                         artwork_mode: str = "nfo", organize_target: str = "电影",
                         inbox_dir: str = "待整理", metadata_providers: str = "",
                         enabled: bool = True, sort_order: int = 0) -> dict:
    """在媒体库下新建视频库（子路径不重叠；本地按需建目录）。"""
    sub = _norm_subpath(subpath)
    with _lock, _conn() as c:
        media = _media_of(c, media_library_id)
        _check_subpath_free(c, media_library_id, sub)
        _ensure_local_subdir(media, sub)
        vid = _insert_video(c, media, name, kind, sub, naming_profile, artwork_mode,
                            organize_target, inbox_dir, metadata_providers, enabled,
                            sort_order)
    logger.info("新建视频库 id=%s media=%s name=%s kind=%s subpath=%s",
                vid, media_library_id, name or sub, kind, sub)
    _invalidate_paths()
    return get_library(vid) or {}


def create_library(name: str, kind: str = "movie", source: str = "local",
                   path: str = "", read_only: bool = False, auto_mount: bool = True,
                   enabled: bool = True, sort_order: int = 0,
                   naming_profile: str = "kodi", artwork_mode: str = "nfo",
                   organize_target: str = "电影", inbox_dir: str = "待整理",
                   metadata_providers: str = "", smb: dict | None = None,
                   nfs: dict | None = None) -> dict:
    """兼容旧扁平建库：自动建同名媒体库 + 一个根视频库（subpath=''）。"""
    from . import media_libraries as _ml
    media = _ml.create_media_library(
        name=name, source=source, path=path, read_only=read_only,
        auto_mount=auto_mount, enabled=enabled, sort_order=sort_order,
        smb=smb, nfs=nfs,
        video_libraries=[{"name": name, "kind": kind, "subpath": "",
                          "naming_profile": naming_profile,
                          "artwork_mode": artwork_mode,
                          "organize_target": organize_target,
                          "inbox_dir": inbox_dir,
                          "metadata_providers": metadata_providers}])
    for lib in list_libraries():
        if int(lib.get("media_library_id") or 0) == int(media["id"]):
            return lib
    return {}


_UPDATE_SCALARS = ("name", "kind", "subpath", "enabled", "sort_order",
                   "naming_profile", "artwork_mode", "organize_target", "inbox_dir",
                   "metadata_providers")
_MEDIA_OWNED_FIELDS = ("path", "source", "read_only", "auto_mount", "smb", "nfs",
                       "smb_url", "nfs_password", "smb_password")


def update_library(library_id: int, **fields) -> dict | None:
    """视频库局部更新；连接/路径/只读属于媒体库，请走 /api/media-libraries。"""
    cur = get_library(library_id)
    if not cur:
        return None
    for k in _MEDIA_OWNED_FIELDS:
        if k in fields and fields[k] is not None:
            raise ValueError("存储连接/路径/只读由媒体库管理，请在媒体库上修改")
    data: dict = {}
    kind_change = sub_change = False
    for k in _UPDATE_SCALARS:
        if k not in fields or fields[k] is None:
            continue
        v = fields[k]
        if k == "name":
            v = str(v).strip()
            if not v:
                raise ValueError("视频库名不能为空")
        elif k == "kind":
            v = _norm_choice(v, KINDS, "kind")
            kind_change = v != str(cur.get("kind") or "movie")
        elif k == "subpath":
            v = _norm_subpath(v)
            sub_change = v != str(cur.get("subpath") or "")
        elif k == "naming_profile":
            v = _norm_choice(v, NAMING_PROFILES, "naming_profile")
        elif k == "artwork_mode":
            v = _norm_choice(v, ARTWORK_MODES, "artwork_mode")
        elif k == "enabled":
            v = 1 if v else 0
        elif k == "sort_order":
            v = int(v or 0)
        else:
            v = str(v).strip()
        data[k] = v
    if not data:
        return cur
    if kind_change and (library_movie_count(library_id) > 0
                        or library_tv_count(library_id) > 0):
        raise ValueError("视频库内已有影片/剧集记录，不能修改类型（请清空记录或新建库）")
    with _lock, _conn() as c:
        if sub_change:
            media = _media_of(c, int(cur["media_library_id"]))
            _check_subpath_free(c, int(cur["media_library_id"]), data["subpath"],
                                exclude_id=int(library_id))
            _ensure_local_subdir(media, data["subpath"])
            data["path"] = _video_path(media, data["subpath"])
        data["updated_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in data)
        try:
            c.execute(f"UPDATE libraries SET {cols} WHERE id=?",
                      (*data.values(), int(library_id)))
        except sqlite3.IntegrityError as e:
            raise ValueError(f"库名已存在: {data.get('name')}") from e
    _invalidate_paths()
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


def _delete_video_rows(c, lid: int, name: str = "") -> dict:
    """删除视频库的全部从属记录（不含 libraries 行；供单删与媒体级联共用）。
    v18：合集属媒体库级，不随单个视频库删除（成员读时按现存影片解析）。"""
    stats = {"library": name, "movies": 0, "extras": 0, "scan_state": 0,
             "tv_shows": 0, "tv_episodes": 0, "collections": 0}
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
    c.execute("DELETE FROM tv_directory_bindings WHERE library_id=?", (lid,))
    c.execute("DELETE FROM tv_binding_history WHERE library_id=?", (lid,))
    c.execute("DELETE FROM tv_seasons WHERE library_id=?", (lid,))
    stats["tv_shows"] = int(c.execute(
        "DELETE FROM tv_shows WHERE library_id=?", (lid,)).rowcount or 0)
    return stats


def delete_library(library_id: int) -> dict | None:
    """删视频库事务（只清 DB 记录，绝不触碰磁盘文件与媒体库连接）。"""
    cur = get_library(library_id)
    if not cur:
        return None
    lid = int(library_id)
    with _lock, _conn() as c:
        stats = _delete_video_rows(c, lid, str(cur.get("name") or ""))
        c.execute("DELETE FROM libraries WHERE id=?", (lid,))
    logger.info("删除视频库 id=%s name=%s 记录清理: %s", lid, cur.get("name"), stats)
    _invalidate_paths()
    return stats
