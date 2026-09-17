"""routers.fs.ops（自 app/routers/fs.py 拆分，评审 R01-Q4；经 fs 门面使用）。"""
import os

from ... import store
from ...config import settings
from ...scanner import is_sidecar, sync_nfos_for
from ..files import (_cleanup_old_dir, _rename_or_move, _resync_old_dir,
                     _sibling_followers)
from .classify import _classify
from .common import logger
__all__ = ['_exec_delete_one', '_move_db_follow', '_exec_move_one']


def _exec_delete_one(plan: dict) -> dict:
    """执行单文件删除（含 DB 联动与旧目录 NFO 收尾）。调用方已确认。"""
    rel = plan["rel"]
    abs_p = os.path.join(settings.media_root, rel)
    old_dir = os.path.dirname(abs_p)
    if not os.path.isfile(abs_p) and not os.path.lexists(abs_p):
        return {**plan, "status": "skipped_missing_src"}
    try:
        os.remove(abs_p)
    except OSError as e:
        return {**plan, "status": f"error: {e}"}
    kind = plan.get("kind") or "other"
    try:
        if kind == "feature" and plan.get("movie_id"):
            store.delete_movie(plan["movie_id"])
        elif kind == "sidecar":
            try:
                store.delete_extra_by_path(rel)
            except Exception:
                pass
    except Exception as e:
        return {**plan, "status": f"error: {e}"}
    _cleanup_old_dir(old_dir)
    _resync_old_dir(old_dir)
    return {**plan, "status": "deleted"}


def _move_db_follow(fr: str, to: str, info: dict) -> None:
    """移动后的 DB 联动：正片改 file_path + NFO；花絮改路径归属；其余不管。"""
    if info.get("kind") == "feature" and info.get("movie_id"):
        store.update_movie_local(info["movie_id"], file_path=to)
        try:
            sync_nfos_for(info["movie_id"],
                          os.path.join(settings.media_root, to))
        except Exception:
            pass
    elif info.get("kind") == "sidecar":
        try:
            rows = [e for e in store.list_all_extras()
                    if e["file_path"] == fr]
        except Exception:
            rows = []
        if rows:
            e = rows[0]
            try:
                store.delete_extra_by_path(fr)
            except Exception:
                pass
            try:
                store.upsert_extra(to, e.get("movie_id"), e.get("kind") or "extra")
            except Exception:
                pass


def _exec_move_one(fr: str, to: str) -> dict:
    """执行单文件改名/移动（含跟随字幕/花絮兄弟与 DB 联动）。"""
    base = {"from": fr, "to": to}
    src = os.path.join(settings.media_root, fr)
    dst = os.path.join(settings.media_root, to)
    if not os.path.isfile(src):
        return {**base, "status": "skipped_missing_src"}
    if os.path.exists(dst):
        return {**base, "status": "conflict_disk_exists"}
    info = _classify(fr)
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        followers = _sibling_followers(src)
        _rename_or_move(src, dst)
        _move_db_follow(fr, to, info)
        # 同茎跟随：字幕/花絮兄弟随新茎改名
        new_stem = os.path.splitext(os.path.basename(dst))[0]
        old_stem = os.path.splitext(os.path.basename(src))[0]
        followed = 0
        for f in followers:
            suffix = os.path.basename(f)[len(old_stem):]
            fdst = os.path.join(os.path.dirname(dst), new_stem + suffix)
            try:
                if not os.path.exists(fdst):
                    frel_old = os.path.relpath(f, settings.media_root)
                    _rename_or_move(f, fdst)
                    frel_new = os.path.relpath(fdst, settings.media_root)
                    try:
                        rows = [e for e in store.list_all_extras()
                                if e["file_path"] == frel_old]
                        if rows:
                            store.delete_extra_by_path(frel_old)
                            store.upsert_extra(
                                frel_new, rows[0].get("movie_id"),
                                rows[0].get("kind") or "extra")
                    except Exception:
                        pass
                    followed += 1
            except OSError:
                continue
        # NFO 残留与旧目录收尾（正片才有意义，其余调用自吞无影响）
        try:
            if info.get("kind") == "feature":
                old_nfo = os.path.join(os.path.dirname(dst), old_stem + ".nfo")
                new_nfo = os.path.join(os.path.dirname(dst), new_stem + ".nfo")
                if (old_nfo != new_nfo and os.path.dirname(src) == os.path.dirname(dst)
                        and os.path.exists(old_nfo)):
                    try:
                        os.remove(old_nfo)
                    except OSError:
                        pass
        except Exception:
            pass
        _cleanup_old_dir(os.path.dirname(src))
        if os.path.normpath(os.path.dirname(src)) != os.path.normpath(os.path.dirname(dst)):
            _resync_old_dir(os.path.dirname(src))
        return {**base, "status": "moved", "followed": followed,
                "kind": info.get("kind") or "other"}
    except Exception as e:
        return {**base, "status": f"error: {e}"}


