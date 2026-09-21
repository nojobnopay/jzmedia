"""routers.files.routes（自 app/routers/files.py 拆分，评审 B9/R09-Q1；经 files 门面使用）。"""
import os
from fastapi import APIRouter, HTTPException
from ... import library_paths, store
from ...scanner import sync_nfos_for
from ...log import get_logger
logger = get_logger("files.routes")
from .paths import (_backend_for, _check_inside_root, _exists_map, _is_file,
                    _only_ids, _rename_or_move, _require_writable)
from .planner import _collect_plans, _ordered_plans
from .executor import (_cleanup_old_dir, _remote_cleanup_old_dir,
                       _remote_resync_old_dir, _resync_old_dir, _move_one)
__all__ = ['router', '_organize', 'organize', 'preview', 'unmatched', 'missing', 'clean', '_restore_candidates', '_restore_one', 'restore_candidates', 'restore_original', '_under_root', '_delete_rows', '_scope_libs']

router = APIRouter(prefix="/api/files")


def _under_root(path: str, root: str) -> bool:
    """路径是否在 root 子树内（含自身）。两侧 normpath 归一（评审 B11/R09-B7）：
    `./电影/x`、`电影//x` 等非规范写法不再漏判。"""
    p = os.path.normpath(path or "")
    r = os.path.normpath(root or "")
    return p == r or p.startswith(r.rstrip("/") + "/")


def _scope_libs(library: str | None,
                media_library: int | str | None = None) -> list[int] | None:
    """只读接口作用域（库工具媒体库化）：media_library 优先 → 其全部视频库；
    未知媒体库返回 `[-1]` 哨兵（绝不退化成全库）；library 兼容逗号多库；
    都缺省=None=全库。"""
    if media_library is not None and str(media_library).strip() != "":
        try:
            mid = int(media_library)
        except (TypeError, ValueError):
            raise HTTPException(422, "media_library must be int")
        return store.library_ids_for_media(mid) or [-1]
    return store._split_ints(library)


def _organize(mode: str, from_prefix: str | None = None,
              to_dir: str | None = None,
              only: set | None = None, dry_run: bool = True,
              library_id: int | None = None,
              media_library_id: int | None = None) -> dict:
    """统一整理入口：inplace=就地归档（保留用户自建父目录，规范影片自身目录/文件）；
    relocate=搬到视频库根（平铺为 `标题 (年份)/…`）。
    作用域：`media_library_id`=整个媒体库（其全部视频库，目标根=各视频库根）；
    `library_id`=单个视频库（缺省=默认库）；带 `ids` 时按行自带库（详情页单行入口）。
    兼容旧参数 `from_prefix`/`to_dir`（仅单库作用域；to_dir 空=库根）。"""
    if mode not in ("inplace", "relocate"):
        raise HTTPException(422, "mode must be inplace|relocate")

    if media_library_id is not None and str(media_library_id).strip() != "":
        try:
            mid = int(media_library_id)
        except (TypeError, ValueError):
            raise HTTPException(422, "media_library_id must be int")
        lib_ids: set[int] | None = set(store.library_ids_for_media(mid))
        lid: int | None = None
        scope: dict = {"media_library_id": mid}
        if not lib_ids:
            empty = {"dry_run": dry_run, "mode": mode, **scope}
            if dry_run:
                return {**empty, "plans": [], "conflicts": []}
            return {**empty, "results": [], "conflicts": []}
    else:
        lid = int(library_id) if library_id is not None else library_paths.default_id()
        lib_ids = None
        scope = {"library_id": lid}

    def _inside(rel: str) -> str:
        try:
            return library_paths.check_inside(lid, rel)
        except ValueError as e:
            raise HTTPException(422, str(e))

    if mode == "inplace":
        # 就地：ids 且未指定媒体库作用域时按行自带库（详情页单行入口不强制传库）
        plans, conflicts = _collect_plans(
            only=only,
            library_id=(lib_ids if lib_ids is not None
                        else (None if only else lid)))
        base: dict = {"dry_run": dry_run, "mode": mode, **scope}
    else:
        td = ""
        if to_dir not in (None, ""):
            if lib_ids is not None:
                raise HTTPException(
                    422, "to_dir 仅单库（library_id）可用；媒体库作用域固定到各视频库根")
            td = _inside(str(to_dir))
        if from_prefix not in (None, ""):
            if lib_ids is not None:
                raise HTTPException(
                    422, "from_prefix 仅单库（library_id）可用；媒体库作用域为整库收敛")
            fp = _inside(str(from_prefix))
        else:
            fp = None
        if fp and td and os.path.normpath(fp) == os.path.normpath(td):
            raise HTTPException(422, "from_prefix == to_dir, nothing to do")
        all_rows = store.list_movies(grouped=False, limit=100000)
        # 显式作用域（媒体库或指定库）才做库过滤；只给 ids 时按行自带库
        explicit_scope = lib_ids is not None or library_id is not None

        def _in_lib(m: dict) -> bool:
            if lib_ids is not None:
                return int(m.get("library_id")
                           or library_paths.DEFAULT_LIBRARY_ID) in lib_ids
            if only and not explicit_scope:
                return True
            return int(m.get("library_id")
                       or library_paths.DEFAULT_LIBRARY_ID) == lid

        if only:
            scoped_rows = [m for m in all_rows if m["id"] in only and _in_lib(m)]
        elif fp:
            # 全量收敛：from_prefix 子树 + 已在目标根下但尚未规范的行
            scoped_rows = [m for m in all_rows if _in_lib(m)
                           and (_under_root(m["file_path"], fp)
                                or (td and _under_root(m["file_path"], td)))]
        else:
            # 媒体库/单库全量：目标根下未规范 + 更深层遗留（平铺收敛）
            scoped_rows = [m for m in all_rows if _in_lib(m)]
        scoped = {m["id"] for m in scoped_rows}
        plans, conflicts = _collect_plans(
            target_root=td, only=scoped or {-1}, all_rows=all_rows,
            library_id=(lib_ids if lib_ids is not None
                        else (None if only else lid)))
        base = {"dry_run": dry_run, "mode": mode, "from_prefix": fp,
                "to_dir": td, **scope}
    if dry_run:
        return {**base, "plans": plans, "conflicts": conflicts}
    for lid2 in sorted({int(p.get("library_id")
                      or library_paths.DEFAULT_LIBRARY_ID) for p in plans}):
        _require_writable(lid2)
    return {**base, "results": [_move_one(p) for p in _ordered_plans(plans)],
            "conflicts": conflicts}


@router.post("/organize")
def organize(body: dict | None = None):
    """统一整理口：mode=inplace 就地归档；mode=relocate 搬到视频库根（平铺）。
    dry_run 默认 true 只预览。`media_library_id`=整个媒体库；`library_id`（或别名
    `library`）单库，缺省=默认库；旧 `from_prefix`/`to_dir` 仅单库兼容。"""
    body = body or {}
    lib = body.get("library_id", body.get("library"))
    media = body.get("media_library_id", body.get("media_library"))
    try:
        lib = int(lib) if lib not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(422, "library_id must be int")
    return _organize(mode=str(body.get("mode") or "inplace"),
                     from_prefix=body.get("from_prefix"),
                     to_dir=body.get("to_dir"),
                     only=_only_ids(body),
                     dry_run=body.get("dry_run", True),
                     library_id=lib,
                     media_library_id=media)


@router.get("/preview")
def preview():
    return _organize("inplace", dry_run=True)


@router.get("/unmatched")
def unmatched(library: str | None = None, media_library: int | None = None):
    """待处理明细（只读，供设置页展示+跳转处理）：
    unmatched=TMDB 无命中；needs_review=模糊命中待确认；
    suspect_title=标题无 CJK（high=非英语片，疑似错配/缺译；info=英语片，多为正常/陈旧）；
    orphan_extras=未归属花絮（文件原地保留）。library/media_library 缺省=全库；
    media_library=整个媒体库（其全部视频库）。"""
    import re as _re
    libs = _scope_libs(library, media_library)
    un, nr, high, info, orphans = [], [], [], [], []
    for m in store.list_movies(grouped=False, limit=100000):
        if libs and int(m.get("library_id") or 0) not in libs:
            continue
        item = {"id": m["id"], "title": m.get("title", ""),
                "original_title": m.get("original_title", ""),
                "year": m.get("year"), "file_path": m["file_path"],
                "tmdb_id": m.get("tmdb_id"),
                "library_id": m.get("library_id"),
                "original_language": m.get("original_language", "")}
        if not m.get("tmdb_id"):
            un.append(item)
        else:
            if m.get("needs_review"):
                item["needs_review"] = 1
                nr.append(item)
            title = m.get("title") or ""
            if (title and _re.search(r"[A-Za-z]", title)
                    and not _re.search(r"[\u4e00-\u9fff]", title)):
                (high if (m.get("original_language") or "")
                 not in ("en", "") else info).append(item)
    un.sort(key=lambda x: x["file_path"])
    for e in store.list_orphan_extras():
        if libs and int(e.get("library_id") or 0) not in libs:
            continue
        orphans.append({"id": e["id"], "file_path": e["file_path"],
                        "kind": e.get("kind") or "extra",
                        "library_id": e.get("library_id")})
    return {"unmatched": un, "needs_review": nr,
            "suspect_title_high": high, "suspect_title_info": info,
            "orphan_extras": orphans,
            "total_unmatched": len(un), "total_needs_review": len(nr),
            "total_suspect_high": len(high), "total_suspect_info": len(info),
            "total_orphan_extras": len(orphans)}


def _delete_rows(cands: list) -> dict:
    """执行删行（只删库，文件保留）：逐行上报结果与失败原因。clean 使用。"""
    done, failed = [], []
    for m in cands:
        try:
            ok = store.delete_movie(m["id"])
            done.append({"id": m["id"], "file_path": m["file_path"],
                         "status": "deleted" if ok else "already_gone"})
        except Exception as e:
            logger.warning("delete movie row failed id=%s path=%s: %s",
                           m.get("id"), m.get("file_path"), e)
            failed.append({"id": m["id"], "file_path": m["file_path"],
                           "error": str(e)})
    return {"dry_run": False, "total": len(cands), "deleted": len(done),
            "failed": failed, "results": done}


@router.get("/missing")
def missing(library: str | None = None, media_library: int | None = None):
    """预览失效条目：库中有记录但文件已不存在的行（软件外删片/移动后产生）。
    library/media_library 缺省=全库；返回项带 library_id 供按视频库分表。
    显式“重新看盘”：先清元数据短 TTL 缓存（外部删除必须立即反映）。"""
    from ... import storage
    storage.clear_meta_cache()
    libs = _scope_libs(library, media_library)
    rows = [m for m in store.list_movies(grouped=False, limit=100000)
            if not libs or int(m.get("library_id") or 0) in libs]
    groups: dict[int, set] = {}
    for m in rows:
        lid = int(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        groups.setdefault(lid, set()).add(m["file_path"])
    maps = {lid: _exists_map(lid, rels) for lid, rels in groups.items()}
    out = []
    for m in rows:
        lid = int(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        if not maps[lid].get(m["file_path"], True):
            out.append({"id": m["id"], "title": m.get("title", ""),
                        "year": m.get("year"), "file_path": m["file_path"],
                        "tmdb_id": m.get("tmdb_id"),
                        "library_id": m.get("library_id")})
    return {"total": len(out), "items": out}


@router.post("/clean")
def clean(body: dict | None = None):
    """清理失效条目：彻底删除DB行+演职员关联+FTS（海报与tmdb_cache保留供重扫复用）。
    默认 dry_run 预览（评审 B5a-4/R09-D4）；
    body.ids 不传则清理全部缺失行；建议先 GET /missing 预览勾选。
    body.library_id/library 单库或多库；media_library_id 整个媒体库（缺省=全库）。"""
    from ... import storage
    storage.clear_meta_cache()   # 显式“重新看盘”（绝不能因缓存漏删/误删）
    body = body or {}
    dry_run = body.get("dry_run", True)
    only_set = _only_ids(body)
    libs = _scope_libs(body.get("library_id", body.get("library")),
                       body.get("media_library_id", body.get("media_library")))
    rows = [m for m in store.list_movies(grouped=False, limit=100000)
            if (only_set is None or m["id"] in only_set)
            and (not libs or int(m.get("library_id") or 0) in libs)]
    groups: dict[int, set] = {}
    for m in rows:
        lid = int(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        groups.setdefault(lid, set()).add(m["file_path"])
    maps = {lid: _exists_map(lid, rels) for lid, rels in groups.items()}
    cands = [m for m in rows
             if not maps[int(m.get("library_id")
                             or library_paths.DEFAULT_LIBRARY_ID)].get(
                                 m["file_path"], True)]
    plans = [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year"),
              "file_path": m["file_path"],
              "tmdb_id": m.get("tmdb_id")} for m in cands]
    if dry_run:
        return {"dry_run": True, "total": len(plans), "plans": plans}
    return _delete_rows(cands)


def _restore_candidates(only: set | None, libs: set[int] | None = None) -> list[dict]:
    """偏离原始位置的行：有原始路径、与当前位置不一致、当前文件仍存在。
    libs 非空时只取这些库的行（设置页按库展示）。
    批量存在性按父目录分组（远程直读库免逐行 stat）。"""
    rows = []
    for m in store.list_movies(grouped=False, limit=100000):
        if only is not None and m.get("id") not in only:
            continue
        if libs and int(m.get("library_id") or 0) not in libs:
            continue
        orig = (m.get("original_file_path") or "").strip()
        cur = m.get("file_path", "")
        if not orig or os.path.normpath(orig) == os.path.normpath(cur):
            continue
        rows.append(m)
    groups: dict[int, set] = {}
    for m in rows:
        lid = int(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
        groups.setdefault(lid, set()).add(m["file_path"])
    maps = {lid: _exists_map(lid, rels) for lid, rels in groups.items()}
    out = [m for m in rows
           if maps[int(m.get("library_id")
                     or library_paths.DEFAULT_LIBRARY_ID)].get(
                         m["file_path"], True)]
    out.sort(key=lambda m: (m.get("file_path", ""), m.get("id", 0)))
    return out


def _restore_one(m: dict, dry_run: bool) -> dict:
    """单行恢复到原始路径。dry_run 只规划；执行时复用 NFO 收敛+旧目录清理。
    远程直读库经 StorageBackend 执行（无挂载依赖）。"""
    base = {"id": m["id"], "title": m.get("title", ""),
            "from": m["file_path"], "to": m.get("original_file_path") or "",
            "library_id": m.get("library_id")}
    # 目标边界守卫（评审 B6/R09-B4）：to 来自库内历史值，理论上合法；
    # 防御性再校验一次，防止库被外部工具改坏后恢复到 MEDIA_ROOT 之外
    try:
        base["to"] = _check_inside_root(base["to"])
    except HTTPException as e:
        return {**base, "status": f"error: illegal target ({e.detail})"}
    lib_id = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    backend = _backend_for(lib_id)
    if backend is not None and backend.abs_path(base["from"]) is None:
        return _restore_one_remote(m, base, backend, dry_run)
    src = library_paths.resolve(
        m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID, base["from"])
    dst = library_paths.resolve(
        m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID, base["to"])
    if not os.path.isfile(src):
        return {**base, "status": "skipped_missing_src"}
    if os.path.exists(dst):
        return {**base, "status": "conflict_disk_exists"}
    # 库内占用保护（评审 P1-06）：同 _move_one，目标挂在别的库行上时先拒绝
    existing = store.get_by_path(
        base["to"],
        library_id=m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID) \
        if base["to"] else None
    if existing is not None and int(existing.get("id") or -1) != int(m["id"]):
        return {**base, "status": "conflict_db_occupied"}
    if dry_run:
        return {**base, "status": "planned"}
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        old_dir = os.path.dirname(src)
        _rename_or_move(src, dst)
        # 本地写：只改 file_path，不碰 TMDB 镜像列与 original_file_path
        try:
            store.update_movie_local(m["id"], file_path=base["to"])
        except Exception as e:
            logger.warning("update db after restore failed id=%s %s -> %s: %s",
                           m.get("id"), base.get("from"), base.get("to"), e)
            try:
                _rename_or_move(dst, src)
            except OSError as rb:
                logger.warning("restore rollback failed id=%s dst=%s: %s", m.get("id"), dst, rb)
            raise
        try:
            sync_nfos_for(m["id"], dst)
        except Exception as e:
            logger.debug("restore nfos sync failed id=%s dst=%s: %s", m.get("id"), dst, e)
        _cleanup_old_dir(old_dir)
        if os.path.normpath(old_dir) != os.path.normpath(os.path.dirname(dst)):
            _resync_old_dir(old_dir)
        return {**base, "status": "restored"}
    except Exception as e:
        logger.warning("restore failed id=%s %s -> %s: %s", m.get("id"),
                       base.get("from"), base.get("to"), e)
        return {**base, "status": f"error: {e}"}


def _restore_one_remote(m: dict, base: dict, backend, dry_run: bool) -> dict:
    """远程直读库恢复：库内相对路径 rename + DB 更新 + NFO/旧目录收敛。"""
    from ... import storage
    lib_id = m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    src_rel = backend.norm(base["from"])
    dst_rel = backend.norm(base["to"])
    try:
        if not _is_file(lib_id, src_rel):
            return {**base, "status": "skipped_missing_src"}
        if backend.exists(dst_rel):
            return {**base, "status": "conflict_disk_exists"}
    except storage.StorageError as e:
        return {**base, "status": f"error: {e}"}
    existing = store.get_by_path(base["to"], library_id=lib_id) if base["to"] else None
    if existing is not None and int(existing.get("id") or -1) != int(m["id"]):
        return {**base, "status": "conflict_db_occupied"}
    if dry_run:
        return {**base, "status": "planned"}
    try:
        parent = os.path.dirname(dst_rel)
        if parent:
            backend.mkdir(parent, parents=True)
        old_dir = os.path.dirname(src_rel)
        backend.rename(src_rel, dst_rel)
        try:
            store.update_movie_local(m["id"], file_path=base["to"])
        except Exception as e:
            logger.warning("update db after restore failed id=%s %s -> %s: %s",
                           m.get("id"), base.get("from"), base.get("to"), e)
            try:
                backend.rename(dst_rel, src_rel)
            except Exception as rb:
                logger.warning("restore rollback failed id=%s dst=%s: %s",
                               m.get("id"), dst_rel, rb)
            raise
        try:
            sync_nfos_for(m["id"], backend=backend, rel=dst_rel)
        except Exception as e:
            logger.debug("restore nfos sync failed id=%s dst=%s: %s", m.get("id"), dst_rel, e)
        _remote_cleanup_old_dir(backend, old_dir)
        if os.path.normpath(old_dir) != os.path.normpath(os.path.dirname(dst_rel)):
            _remote_resync_old_dir(backend, old_dir, lib_id)
        return {**base, "status": "restored"}
    except Exception as e:
        logger.warning("remote restore failed id=%s %s -> %s: %s", m.get("id"),
                       base.get("from"), base.get("to"), e)
        return {**base, "status": f"error: {e}"}


@router.get("/restore-candidates")
def restore_candidates(library: str | None = None,
                       media_library: int | None = None):
    """预览偏离原始位置的行（只读，供设置页恢复区展示）。
    library/media_library 缺省=全库；返回项带 library_id 供按视频库分表。
    显式“重新看盘”：先清元数据短 TTL 缓存。"""
    from ... import storage
    storage.clear_meta_cache()
    libs = _scope_libs(library, media_library)
    cands = _restore_candidates(None, set(libs) if libs else None)
    return {"total": len(cands),
            "items": [{"id": m["id"], "title": m.get("title", ""),
                       "year": m.get("year"),
                       "file_path": m["file_path"],
                       "original_file_path": m.get("original_file_path") or "",
                       "library_id": m.get("library_id")}
                      for m in cands]}


@router.post("/restore-original")
def restore_original(body: dict | None = None):
    """恢复到原始位置：把整理/搬迁后偏离原始路径的影片搬回 original_file_path。
    默认 dry_run:true 只预览；确认后 dry_run:false 执行。目标被占/源缺失则跳过上报，绝不覆盖。
    body.library_id/library 单库或多库；media_library_id 整个媒体库（缺省=全库）。"""
    from ... import storage
    storage.clear_meta_cache()   # 显式“重新看盘”
    body = body or {}
    only = _only_ids(body)
    dry_run = body.get("dry_run", True)
    libs = _scope_libs(body.get("library_id", body.get("library")),
                       body.get("media_library_id", body.get("media_library")))
    lib_set = set(libs) if libs else None
    if dry_run:
        plans = [{"id": m["id"], "title": m.get("title", ""), "year": m.get("year"),
                  "from": m["file_path"], "to": m.get("original_file_path") or "",
                  "status": _restore_one(m, dry_run=True)["status"]}
                 for m in _restore_candidates(only, lib_set)]
        return {"dry_run": True, "total": len(plans), "plans": plans}
    cands = _restore_candidates(only, lib_set)
    for lid in sorted({int(m.get("library_id") or library_paths.DEFAULT_LIBRARY_ID)
                       for m in cands}):
        _require_writable(lid)
    results = [_restore_one(m, dry_run=False) for m in cands]
    ok = sum(1 for r in results if r["status"] == "restored")
    return {"dry_run": False, "total": len(results),
            "restored": ok, "results": results}

