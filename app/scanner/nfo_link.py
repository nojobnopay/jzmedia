"""scanner.nfo_link（自 app/scanner.py 拆分，评审 B9/R03-Q1；对外经 app.scanner 门面使用）。

NFO 收敛策略单一来源：本地路径走 POSIX 适配器，远程直读库走 StorageBackend 适配器
（同一套独占/共享/多版本规则；远程绝不删除非 NFO 文件，失败自吞）。
"""
import hashlib
import os
from .. import library_paths
from .. import store
from ..nfo import render_movie_nfo_bytes
from ..log import get_logger
logger = get_logger("scanner.nfo_link")
from .parse import normalize_title, parse_filename
from .classify import is_feature_video
__all__ = ['_list_dir_nfos', '_same_film', '_ref_title_year', 'sync_nfos_for',
           'sync_nfos_for_file', '_write_nfo_for']


class _LocalDirFS:
    """本地影片目录适配（abs_dir 为绝对路径；dir 为库内相对目录）。"""

    def __init__(self, abs_dir: str, lib_id):
        self.abs_dir = abs_dir
        self.lib_id = lib_id
        root = library_paths.library_root(lib_id)
        try:
            rel = os.path.relpath(abs_dir, root) if abs_dir else ""
        except ValueError:
            rel = ""
        self.dir = "" if rel in (".", "") else rel

    def exists(self) -> bool:
        return bool(self.abs_dir) and os.path.isdir(self.abs_dir)

    def names(self):
        try:
            return sorted(os.listdir(self.abs_dir))
        except OSError:
            return None

    def rel_of(self, name: str) -> str:
        return f"{self.dir}/{name}" if self.dir else name

    def path(self, name: str) -> str:
        return os.path.join(self.abs_dir, name)

    def is_file(self, name: str) -> bool:
        return os.path.isfile(self.path(name))

    def read(self, name: str):
        try:
            with open(self.path(name), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def write(self, name: str, data: bytes) -> None:
        from ..fsutil import atomic_write_bytes
        if self.abs_dir:
            os.makedirs(self.abs_dir, exist_ok=True)
        atomic_write_bytes(self.path(name), data)

    def remove(self, name: str) -> None:
        os.remove(self.path(name))


class _BackendDirFS:
    """远程存储适配：与本地同一套 NFO 收敛策略，经 StorageBackend 落盘。"""

    def __init__(self, backend, rel_dir: str):
        self.backend = backend
        self.dir = backend.norm(rel_dir or "")

    def exists(self) -> bool:
        try:
            return self.backend.is_dir(self.dir)
        except Exception:
            return False

    def names(self):
        try:
            return sorted(e["name"] for e in self.backend.list(self.dir))
        except Exception:
            return None

    def rel_of(self, name: str) -> str:
        return f"{self.dir}/{name}" if self.dir else name

    def path(self, name: str) -> str:
        return self.rel_of(name)

    def is_file(self, name: str) -> bool:
        try:
            return not self.backend.stat(self.rel_of(name)).is_dir
        except Exception:
            return False

    def read(self, name: str):
        try:
            return self.backend.read(self.rel_of(name))
        except Exception:
            return None

    def write(self, name: str, data: bytes) -> None:
        self.backend.write(self.rel_of(name), data)

    def remove(self, name: str) -> None:
        self.backend.delete(self.rel_of(name))


def _with_cache(movie: dict | None) -> dict | None:
    """附加 tmdb_cache（D6：NFO 写 premiered/tagline/set/ratings 等字段用）。"""
    if not movie:
        return movie
    tid = movie.get("tmdb_id")
    if tid:
        try:
            movie["_tmdb_cache"] = store.get_tmdb_cached(int(tid)) or {}
        except Exception:
            movie["_tmdb_cache"] = {}
    return movie


def _write_one(data: dict, fs, name: str, force: bool = False) -> str:
    """写单个 NFO（带所有权保护）：返回 written|skipped_external|failed。
    磁盘文件被用户/其他工具改过（哈希与 nfo_hash 不一致）时默认不覆盖。"""
    try:
        mid = int(data.get("id") or 0)
        stored = str(data.get("nfo_hash") or "")
        if not force and stored:
            old = fs.read(name)
            if old is not None and hashlib.sha1(old).hexdigest() != stored:
                logger.info("NFO 被外部修改，跳过覆盖 file=%s", fs.path(name))
                return "skipped_external"
        payload = render_movie_nfo_bytes(data)
        fs.write(name, payload)
        if mid:
            store.update_movie_meta(mid, nfo_hash=hashlib.sha1(payload).hexdigest())
        return "written"
    except Exception as e:
        logger.warning("nfo write failed file=%s: %s", fs.path(name), e)
        return "failed"


def _list_dir_nfos(fs) -> tuple[list[str] | None, list[str]]:
    """目录顶层 (全部文件名|None不可读, 正片视频名列表)。只看顶层，不进 extras/ 等子目录。"""
    names = fs.names()
    if names is None:
        return None, []
    feats = [n for n in names if fs.is_file(n) and is_feature_video(fs.rel_of(n))]
    return names, sorted(feats)


def _same_film(row_tmdb, row_title, row_year,
               cur_tmdb, cur_title: str, cur_year) -> bool:
    """两行是否算“同片多版本”。有 tmdb_id 按 id 比；都无 id 按归一标题+年份比；
    一有一无保守判异（→共享目录，不互删 NFO）。"""
    if cur_tmdb and row_tmdb:
        try:
            return int(row_tmdb) == int(cur_tmdb)
        except (TypeError, ValueError):
            return row_tmdb == cur_tmdb
    if not cur_tmdb and not row_tmdb:
        rt = normalize_title(row_title or "")
        ct = normalize_title(cur_title or "")
        if not rt or not ct or rt != ct:
            return False
        if row_year and cur_year:
            try:
                return int(row_year) == int(cur_year)
            except (TypeError, ValueError):
                return False
        return True
    return False


def _ref_title_year(movie: dict, name: str) -> tuple[str, object]:
    t = ((movie or {}).get("title") or "")
    y = (movie or {}).get("year")
    if t:
        return t, y
    try:
        p = parse_filename(name or "")
        return p.get("title") or "", p.get("year")
    except Exception:
        return "", y


def sync_nfos_for(mid: int, abs_path: str = "", dry_run: bool = False,
                  force: bool = False, backend=None, rel: str = "") -> dict:
    """NFO 收敛唯一入口（幂等，失败自吞，调用方无需 try）。

    规则：movie.nfo 在独占目录永远保留；仅“同片多行同目录”（同 tmdb_id，
    无 id 则同归一标题+年份）才补各版本同名 .nfo；单版本删历史残留的同名 .nfo；
    不同电影混放的共享目录只写当前文件的同名 .nfo，不碰 movie.nfo、不删别人的。
    返回 {ok, mode, dir, wrote[], deleted[]}（dry_run 只计算不落盘）。

    本地传 `abs_path`；远程直读库传 `backend` + 库内相对 `rel`（abs_path 留空）。
    """
    try:
        movie = _with_cache(store.get_movie(mid))
        if not movie:
            return {"ok": False, "mode": "missing", "dir": "",
                    "wrote": [], "deleted": []}
        if backend is not None:
            rel_file = backend.norm(rel or movie.get("file_path") or "")
            fs = _BackendDirFS(backend, os.path.dirname(rel_file))
            name = os.path.basename(rel_file)
        else:
            if not abs_path:
                return {"ok": False, "mode": "no_path", "dir": "", "wrote": [],
                        "deleted": [], "reason": "no media path"}
            lib_id = movie.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
            fs = _LocalDirFS(os.path.dirname(abs_path), lib_id)
            name = os.path.basename(abs_path)
        return _sync_nfos(movie, fs, name, dry_run, force)
    except Exception as e:
        logger.warning("sync_nfos_for failed mid=%s path=%s: %s", mid,
                       abs_path or rel, e)
        return {"ok": False, "mode": "error", "dir": "",
                "wrote": [], "deleted": []}


def sync_nfos_for_file(mid: int, backend, rel: str, dry_run: bool = False,
                       force: bool = False) -> dict:
    """远程直读库入口：backend + 库内相对路径。"""
    return sync_nfos_for(mid, abs_path="", dry_run=dry_run, force=force,
                         backend=backend, rel=rel)


def _fallback_writes(movie, fs, stem: str, dry_run: bool, force: bool) -> dict:
    """目录不可用（不存在/不可读）时退化为旧双写，保证元数据不丢。"""
    wrote = ["movie.nfo"] + ([stem + ".nfo"] if stem and stem != "movie" else [])
    if dry_run:
        return {"ok": True, "mode": "fallback", "dir": fs.dir, "wrote": wrote,
                "deleted": []}
    st = _write_one(movie, fs, "movie.nfo", force)
    if stem and stem != "movie":
        st2 = _write_one(movie, fs, stem + ".nfo", force)
        if st2 == "failed":
            st = "failed"
    if st == "failed":
        return {"ok": False, "mode": "fallback", "dir": fs.dir,
                "wrote": [], "deleted": []}
    return {"ok": True, "mode": "fallback", "dir": fs.dir, "wrote": wrote,
            "deleted": []}


def _sync_nfos(movie: dict, fs, name: str, dry_run: bool,
               force: bool) -> dict:
    mid = int(movie.get("id") or 0)
    rel_dir = fs.dir
    stem = os.path.splitext(name)[0]
    names = fs.names()
    if names is None:
        # 目录尚不存在（如搬迁竞态）或不可读：退化为旧双写
        return _fallback_writes(movie, fs, stem, dry_run, force)
    feats = sorted(n for n in names
                   if fs.is_file(n) and is_feature_video(fs.rel_of(n)))
    if stem == "movie":
        # 文件本身就叫 movie.*：同名 NFO 即 movie.nfo，只写一份
        st = "written"
        if not dry_run:
            st = _write_one(movie, fs, "movie.nfo", force)
        return {"ok": st != "failed", "mode": "single-movie-stem", "dir": rel_dir,
                "wrote": ["movie.nfo"], "deleted": []}
    try:
        rows = store.list_movies_in_dir(
            rel_dir, library_id=movie.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
    except Exception as e:
        logger.debug("list movies in dir failed dir=%s: %s", rel_dir, e)
        rows = []
    row_by_base: dict[str, dict] = {}
    for r in rows:
        try:
            if not is_feature_video(r.get("file_path", "")):
                continue
        except Exception:
            continue
        b = os.path.basename(r.get("file_path", ""))
        if b and b not in row_by_base:
            row_by_base[b] = r
    cur_tmdb = movie.get("tmdb_id")
    ref_title, ref_year = _ref_title_year(movie, name)
    ref_norm = normalize_title(ref_title or "")
    related: set[str] = set()
    foreign: set[str] = set()
    for f in feats:
        row = row_by_base.get(f)
        if row is not None:
            if _same_film(row.get("tmdb_id"), row.get("title"), row.get("year"),
                          cur_tmdb, ref_title, ref_year):
                related.add(f)
            else:
                foreign.add(f)
            continue
        # 磁盘有、库无（扫描中途）：文件名相似才算同片，避免把 待整理/ 误判独占
        try:
            p = parse_filename(f)
            pt = normalize_title(p.get("title") or "")
            py = p.get("year")
        except Exception:
            foreign.add(f)
            continue
        same = bool(ref_norm and pt and pt == ref_norm)
        if same and py and ref_year:
            try:
                same = int(py) == int(ref_year)
            except (TypeError, ValueError):
                same = False
        (related if same else foreign).add(f)
    # 共享目录：只写当前同名，不碰 movie.nfo、不删任何
    if foreign:
        st = "written"
        if not dry_run:
            st = _write_one(movie, fs, stem + ".nfo", force)
        return {"ok": st != "failed", "mode": "shared", "dir": rel_dir,
                "wrote": [stem + ".nfo"], "deleted": []}
    existing_nfos = [n for n in (names or []) if n.endswith(".nfo") and fs.is_file(n)]
    if len(feats) <= 1:
        deleted = [n for n in existing_nfos if n != "movie.nfo"]
        st = "written"
        if not dry_run:
            st = _write_one(movie, fs, "movie.nfo", force)
            for n in deleted:
                try:
                    fs.remove(n)
                except Exception as e:
                    logger.debug("nfo remove failed file=%s: %s", n, e)
        if st == "failed":
            logger.warning("sync_nfos_for failed mid=%s dir=%s: nfo write failed",
                           mid, rel_dir)
        return {"ok": st != "failed", "mode": "single", "dir": rel_dir,
                "wrote": ["movie.nfo"], "deleted": sorted(deleted)}
    # 独占多版本：默认只写 movie.nfo（kodi 等把多版本合并为一个条目）；
    # 仅 plex 档且版本间 edition 不同（Plex 会拆独立条目）才补各版本同名 .nfo。
    # 旧行为（每版本都写）可用 env PER_VERSION_META=1 回退。
    ver_rows: list[tuple[str, dict]] = []
    eds: list[dict] = [{"edition": movie.get("edition") or ""}]
    for f in sorted(related):
        st = os.path.splitext(f)[0]
        if not st or st == "movie":
            continue
        row = row_by_base.get(f)
        full = movie
        if row is not None and int(row.get("id", -1)) != int(mid):
            try:
                got = _with_cache(store.get_movie(int(row["id"])))
                if got:
                    full = got
            except Exception as e:
                logger.debug("load version row failed id=%s: %s", row.get("id"), e)
        ver_rows.append((st, full))
        eds.append({"edition": full.get("edition") or ""})
    per_version = library_paths.per_version_meta(
        movie.get("library_id") or library_paths.DEFAULT_LIBRARY_ID, eds)
    wanted = {"movie.nfo"}
    writers: list[tuple[str, dict]] = [("movie.nfo", movie)]
    if per_version:
        for st, full in ver_rows:
            wanted.add(st + ".nfo")
            writers.append((st + ".nfo", full))
    deleted = [n for n in existing_nfos if n not in wanted]
    if not dry_run:
        failed = 0
        for wname, data in writers:
            if _write_one(data, fs, wname, force) == "failed":
                failed += 1
        for n in deleted:
            try:
                fs.remove(n)
            except Exception as e:
                logger.debug("nfo remove failed file=%s: %s", n, e)
        if failed:
            return {"ok": False, "mode": "multi", "dir": rel_dir,
                    "wrote": sorted({n for n, _ in writers}),
                    "deleted": sorted(deleted)}
    return {"ok": True, "mode": "multi", "dir": rel_dir,
            "wrote": sorted({n for n, _ in writers}), "deleted": sorted(deleted)}


def _write_nfo_for(mid: int, abs_path: str = "", backend=None, rel: str = "") -> bool:
    try:
        return bool(sync_nfos_for(mid, abs_path or "", backend=backend,
                                  rel=rel).get("ok"))
    except Exception:
        logger.debug("write nfo failed mid=%s path=%s", mid, abs_path or rel,
                     exc_info=True)
        return False
