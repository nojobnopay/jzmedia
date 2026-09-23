"""TV NFO 收敛（T3）：`tvshow.nfo`（剧根）+ `season.nfo`（季目录）+ 每集 `<stem>.nfo`。

- 与电影同一套**所有权哈希保护**（`nfo_hash`）：磁盘文件被外部改过默认不覆盖；
- 本地/远程直读统一走 StorageBackend 适配器（`nfo_link._BackendDirFS`），远程不删非 NFO；
- `.plexmatch` 为可选（env `TV_PLEXMATCH=1`）：写 title/year/tmdb/tvdb 匹配提示。
"""
import concurrent.futures
import hashlib
import os

from .. import store
from ..log import get_logger
from ..nfo import (render_episode_nfo_bytes, render_season_nfo_bytes,
                   render_tvshow_nfo_bytes)
from .nfo_link import _BackendDirFS
from .tv_parse import season_from_dir

logger = get_logger("scanner.tv_nfo_link")
__all__ = ['sync_tv_nfos_for', 'show_dir_of', 'season_dir_of', 'write_plexmatch']


def show_dir_of(rel_file: str, direct_dirs=None) -> str:
    """剧根目录：从剧集文件路径向上跳过季目录与发布包装目录。

    - `Show/Season 01/E01.mkv` → `Show`；
    - `Show/<release-wrapper>/Season 01/E01.mkv` → `Show`（wrapper 只含子目录）；
    - 子剧 `七龙珠/七龙珠.Z/001.mkv` → `七龙珠/七龙珠.Z`（该目录直接含集文件）。
    `direct_dirs`：直接含集文件的目录集合（由调用方从集列表计算；None 时只跳季目录，
    用于无集上下文的兼容路径）。顶层目录（深度 1）永远是剧根。
    """
    parts = [p for p in str(rel_file or "").replace("\\", "/").split("/")[:-1] if p]
    while len(parts) > 1:
        cur = "/".join(parts)
        if season_from_dir(parts[-1]) is not None:
            parts.pop()
            continue
        if direct_dirs is not None and cur not in direct_dirs:
            parts.pop()
            continue
        break
    return "/".join(parts)


def _direct_dirs(episodes) -> set[str]:
    """直接含集文件的目录集合（剧根/子剧判定用）。"""
    out: set[str] = set()
    for e in episodes or []:
        d = os.path.dirname(str(e.get("file_path") or "").replace("\\", "/"))
        if d:
            out.add(d)
    return out


def season_dir_of(rel_file: str) -> str:
    return os.path.dirname(str(rel_file or "").replace("\\", "/"))


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _write_owned(fs, name: str, payload: bytes, stored_hash: str,
                 force: bool) -> str:
    """带所有权保护的写入：written|unchanged|skipped_external|failed。"""
    try:
        old = fs.read(name)
        if not force and stored_hash and old is not None \
                and _sha1(old) != stored_hash:
            logger.info("TV NFO 被外部修改，跳过覆盖 file=%s", fs.path(name))
            return "skipped_external"
        if old == payload:
            return "unchanged"
        fs.write(name, payload)
        return "written"
    except Exception as e:
        logger.warning("TV NFO 写入失败 file=%s: %s", fs.path(name), e)
        return "failed"


def _write_plain(fs, name: str, payload: bytes) -> str:
    """无哈希保护的幂等写（season.nfo/.plexmatch）：内容相同不写。"""
    try:
        if fs.read(name) == payload:
            return "unchanged"
        fs.write(name, payload)
        return "written"
    except Exception as e:
        logger.warning("TV 文件写入失败 file=%s: %s", fs.path(name), e)
        return "failed"


def _credits_for(show: dict) -> dict:
    tid = show.get("tmdb_id")
    if not tid:
        return {"cast": [], "crew": []}
    try:
        cache = store.get_tmdb_cached(int(tid), "tv") or {}
        cr = cache.get("credits")
        return cr if isinstance(cr, dict) else {"cast": [], "crew": []}
    except Exception as e:
        logger.debug("tv credits cache read failed show=%s: %s", show.get("id"), e)
        return {"cast": [], "crew": []}


def _episode_nfo_enabled(backend) -> bool:
    """逐集 NFO 开关：env `TV_EPISODE_NFO` 显式设置优先；否则本地库写、远程库不写
    （SMB 实测单文件原子写 ~1.9s，3,877 集逐集写 ≈ 2 小时，不适合默认开）。"""
    env = os.getenv("TV_EPISODE_NFO")
    if env is not None and env.strip() != "":
        return env.strip().lower() in ("1", "true", "yes", "on")
    try:
        return backend.abs_path("") is not None
    except Exception:
        return False


def sync_tv_nfos_for(show_id: int, backend=None, dry_run: bool = False,
                     force: bool = False, episode_nfo: bool | None = None,
                     workers: int = 1) -> dict:
    """写该剧 NFO（幂等，失败自吞）。返回 {ok, dir, wrote[], skipped[], failed[], deleted[]}。

    `dry_run=True` 只计算会写哪些文件，不落盘。远程直读库传 `backend`（必传，
    与电影不同：剧集没有单一文件路径，show_dir 由集文件路径推导）。
    `episode_nfo`：逐集 `<stem>.nfo` 开关（None=按库类型/env 自动，见上）。
    `workers>1` 时逐集 NFO 并发写（弱 NAS 实测 ~1.8×；剧/季 NFO 仍串行）。
    """
    result = {"ok": False, "dir": "", "wrote": [], "skipped": [], "failed": [],
              "deleted": []}
    try:
        show = store.get_show_meta(show_id)
        if not show:
            return result
        detail = store.get_show(show_id)
        episodes = (detail or {}).get("episodes") or []
        if not episodes:
            return result
        seasons = (detail or {}).get("seasons") or []
        lib_id = show.get("library_id") or store.DEFAULT_LIBRARY_ID
        backend = backend if backend is not None else _backend_for(lib_id)
        if backend is None:
            result["failed"].append("no_backend")
            return result
        direct = _direct_dirs(episodes)
        show_dir = show_dir_of(episodes[0]["file_path"], direct)
        result["dir"] = show_dir
        show_fs = _BackendDirFS(backend, show_dir)
        show_payload = render_tvshow_nfo_bytes(show, seasons, _credits_for(show))
        if dry_run:
            result["wrote"].append("tvshow.nfo")
        else:
            st = _write_owned(show_fs, "tvshow.nfo", show_payload,
                              str(show.get("nfo_hash") or ""), force)
            _record(result, "tvshow.nfo", st)
            if st in ("written", "unchanged"):
                new_hash = _sha1(show_payload)
                if str(show.get("nfo_hash") or "") != new_hash:
                    store.update_show_meta(int(show_id), nfo_hash=new_hash)
        # 季 NFO：每季目录一份（Jellyfin/Emby 读季名/简介）
        seen_dirs: set[str] = set()
        for s in seasons:
            try:
                sn = int(s.get("season") or 0)
            except (TypeError, ValueError):
                continue
            rep = next((e for e in episodes
                        if int(e.get("season") or 0) == sn), None)
            if not rep:
                continue
            sdir = season_dir_of(rep["file_path"])
            if not sdir or sdir in seen_dirs:
                continue
            seen_dirs.add(sdir)
            name = "season.nfo"
            if dry_run:
                result["wrote"].append(f"{sdir}/{name}")
                continue
            st = _write_plain(_BackendDirFS(backend, sdir), name,
                              render_season_nfo_bytes(s))
            _record(result, f"{sdir}/{name}", st)
        # 清理历史误放：包装目录里的 tvshow.nfo（旧 show_dir_of 推导），仅当哈希匹配
        if not dry_run:
            checked: set[str] = set()
            for e in episodes:
                sdir = season_dir_of(e["file_path"])
                parent = os.path.dirname(sdir)
                if not parent or parent == show_dir or parent in checked:
                    continue
                checked.add(parent)
                pfs = _BackendDirFS(backend, parent)
                if not pfs.is_file("tvshow.nfo"):
                    continue
                data = pfs.read("tvshow.nfo")
                stored = str(show.get("nfo_hash") or "")
                if data and stored and _sha1(data) == stored:
                    try:
                        pfs.remove("tvshow.nfo")
                        result["deleted"].append(f"{parent}/tvshow.nfo")
                    except Exception as e:
                        logger.debug("stale tvshow.nfo remove failed %s: %s", parent, e)
        # 每集 NFO：与视频同目录同名（远程库默认关，见 _episode_nfo_enabled）
        write_eps = _episode_nfo_enabled(backend) if episode_nfo is None else bool(episode_nfo)
        todo = [e for e in (episodes if write_eps else []) if e.get("file_path")]
        if dry_run:
            for e in todo:
                stem = os.path.splitext(os.path.basename(str(e["file_path"])))[0]
                if stem:
                    result["wrote"].append(stem + ".nfo")
        else:
            def _one(e: dict):
                rel = str(e.get("file_path") or "")
                stem = os.path.splitext(os.path.basename(rel))[0]
                if not stem:
                    return None
                name = stem + ".nfo"
                fs = _BackendDirFS(backend, os.path.dirname(rel))
                payload = render_episode_nfo_bytes(show, e)
                st = _write_owned(fs, name, payload,
                                  str(e.get("nfo_hash") or ""), force)
                # 内容未变但库里无哈希（历史半成品）：补哈希，否则下次外部改动无法识别
                if st in ("written", "unchanged"):
                    new_hash = _sha1(payload)
                    if str(e.get("nfo_hash") or "") != new_hash:
                        store.update_episode_meta(int(e["id"]), nfo_hash=new_hash)
                return name, st

            if workers and int(workers) > 1:
                with concurrent.futures.ThreadPoolExecutor(
                        max_workers=int(workers)) as ex:
                    for out in ex.map(_one, todo):
                        if out:
                            _record(result, out[0], out[1])
            else:
                for e in todo:
                    out = _one(e)
                    if out:
                        _record(result, out[0], out[1])
        if os.getenv("TV_PLEXMATCH", "").strip() not in ("", "0", "false", "no", "off"):
            write_plexmatch(int(show_id), backend=backend)
        result["ok"] = not result["failed"]
        return result
    except Exception as e:
        logger.warning("sync_tv_nfos_for failed show=%s: %s", show_id, e)
        result["failed"].append(str(e)[:200])
        return result


def _record(result: dict, name: str, status: str) -> None:
    if status == "written":
        result["wrote"].append(name)
    elif status in ("skipped_external", "unchanged"):
        result["skipped"].append(name)
    else:
        result["failed"].append(name)


def _backend_for(library_id):
    try:
        from .. import storage
        return storage.backend_for(int(library_id))
    except Exception as e:
        logger.debug("tv nfo backend resolve failed lib=%s: %s", library_id, e)
        return None


def write_plexmatch(show_id: int, backend=None) -> dict:
    """可选 `.plexmatch`（env `TV_PLEXMATCH=1`）：Plex 匹配提示（title/year/tmdb/tvdb）。"""
    try:
        show = store.get_show_meta(show_id)
        if not show:
            return {"ok": False}
        episodes = store.list_episodes(show_id)
        if not episodes:
            return {"ok": False}
        lib_id = show.get("library_id") or store.DEFAULT_LIBRARY_ID
        backend = backend if backend is not None else _backend_for(lib_id)
        if backend is None:
            return {"ok": False}
        lines = []
        if show.get("title"):
            lines.append(f"title: {show['title']}")
        if show.get("year"):
            lines.append(f"year: {show['year']}")
        if show.get("tmdb_id"):
            lines.append(f"tmdb: {show['tmdb_id']}")
        if show.get("tvdb_id"):
            lines.append(f"tvdb: {show['tvdb_id']}")
        if show.get("imdb_id"):
            lines.append(f"imdb: {show['imdb_id']}")
        if len(lines) < 2:
            return {"ok": False}
        fs = _BackendDirFS(backend, show_dir_of(episodes[0]["file_path"],
                                                _direct_dirs(episodes)))
        return {"ok": _write_plain(fs, ".plexmatch",
                                   ("\n".join(lines) + "\n").encode("utf-8"))
                != "failed"}
    except Exception as e:
        logger.debug("plexmatch write failed show=%s: %s", show_id, e)
        return {"ok": False}
