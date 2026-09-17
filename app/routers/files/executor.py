"""routers.files.executor（自 app/routers/files.py 拆分，评审 B9/R09-Q1；经 files 门面使用）。"""
import os
from ... import store
from ...config import settings
from ...scanner import SUBTITLE_EXTS
from ...scanner import is_feature_video
from ...scanner import is_sidecar
from ...scanner import sync_nfos_for
from ...log import get_logger
logger = get_logger("files.executor")
from .paths import _rename_or_move, _safe_component
__all__ = ['_write_nfos', '_sibling_followers', '_cleanup_old_dir', 'move_attached_extras', '_resync_old_dir', '_move_one']

def _write_nfos(movie_id: int, dst_abs: str) -> None:
    """整理后 NFO 收敛：委托 scanner.sync_nfos_for（单版本只留 movie.nfo，
    同片多版本才补同名，共享目录只写当前同名）。失败自吞。"""
    try:
        sync_nfos_for(movie_id, dst_abs)
    except Exception as e:
        logger.debug("write nfos failed id=%s dst=%s: %s", movie_id, dst_abs, e)


def _sibling_followers(src_abs: str) -> list[str]:
    """同名前缀跟随文件：同目录下以正片 stem 开头、本身是花絮/样片/字幕的兄弟。"""
    src_dir = os.path.dirname(src_abs)
    stem = os.path.splitext(os.path.basename(src_abs))[0]
    out = []
    try:
        names = os.listdir(src_dir)
    except OSError:
        return out
    for n in names:
        full = os.path.join(src_dir, n)
        if full == src_abs or not os.path.isfile(full):
            continue
        st, ex = os.path.splitext(n)
        if st == stem or st.startswith(stem + "-") or st.startswith(stem + ".") \
                or st.startswith(stem + "_") or st.startswith(stem + " "):
            rel_probe = os.path.relpath(full, settings.media_root)
            if is_sidecar(rel_probe) or ex.lower() in SUBTITLE_EXTS:
                out.append(full)
    return out


def _cleanup_old_dir(old_dir_abs: str) -> None:
    """旧目录无正片残留时清掉 NFO 残留；空目录则删掉（共享大目录不会为空，无动作）。"""
    try:
        names = os.listdir(old_dir_abs)
    except OSError:
        return
    has_feature = False
    for n in names:
        full = os.path.join(old_dir_abs, n)
        if not os.path.isfile(full):
            continue
        rel = os.path.relpath(full, settings.media_root)
        if is_feature_video(rel):
            has_feature = True
            break
    if has_feature:
        return
    for n in names:
        if n == "movie.nfo" or n.endswith(".nfo"):
            try:
                os.remove(os.path.join(old_dir_abs, n))
            except OSError as e:
                logger.debug("remove stale nfo failed dir=%s name=%s: %s", old_dir_abs, n, e)
    try:
        if not os.listdir(old_dir_abs):
            os.rmdir(old_dir_abs)
    except OSError as e:
        logger.debug("rmdir old dir failed dir=%s: %s", old_dir_abs, e)


def move_attached_extras(movie_id: int, movie_dir_abs: str) -> dict:
    """把已归属花絮搬进指定影片目录的 extras/ 子目录（茎名清洗保留），更新归属路径。
    供 _move_one 跟随与 /api/extras/collect 共用。
    返回 {moved, skipped}；skipped=目标已存在等未搬项（评审 R08-D3：不再静默跳过）。"""
    moved = 0
    skipped: list[dict] = []
    try:
        rows = store.list_extras_by_movie(movie_id)
    except Exception:
        return {"moved": 0, "skipped": []}
    for e in rows:
        esrc = os.path.join(settings.media_root, e["file_path"])
        if not os.path.isfile(esrc):
            continue
        edst_dir = os.path.join(movie_dir_abs, "extras")
        base = _safe_component(os.path.splitext(os.path.basename(esrc))[0])
        if not base:
            continue
        edst = os.path.join(edst_dir, base + os.path.splitext(esrc)[1])
        try:
            if os.path.normpath(esrc) == os.path.normpath(edst):
                continue
            os.makedirs(edst_dir, exist_ok=True)
            if not os.path.exists(edst):
                os.rename(esrc, edst)
                store.upsert_extra(os.path.relpath(edst, settings.media_root),
                                   movie_id, e.get("kind") or "extra")
                try:
                    store.delete_extra_by_path(e["file_path"])
                except Exception as e:
                    logger.debug("delete old extra row failed path=%s: %s", e["file_path"], e)
                moved += 1
            else:
                skipped.append({"file_path": e["file_path"],
                                "reason": "target_exists"})
        except OSError as ex:
            skipped.append({"file_path": e["file_path"], "reason": f"error: {ex}"})
            continue
    return {"moved": moved, "skipped": skipped}


def _resync_old_dir(old_dir_abs: str) -> None:
    """搬迁后旧目录重收敛：还有正片残留（如多版本搬走其一）时，以剩余行重调
    sync（多→单自动删多余同名 NFO）；空了则沿用 _cleanup_old_dir 清场。失败自吞。"""
    try:
        if not os.path.isdir(old_dir_abs):
            return
        try:
            old_rel = os.path.relpath(old_dir_abs, settings.media_root)
            if old_rel == ".":
                old_rel = ""
        except ValueError:
            return
        try:
            rows = store.list_movies_in_dir(old_rel)
        except Exception:
            return
        remaining = []
        for r in rows:
            try:
                fp = r.get("file_path", "")
                if not fp or not is_feature_video(fp):
                    continue
                if os.path.isfile(os.path.join(settings.media_root, fp)):
                    remaining.append(r)
            except Exception:
                continue
        if not remaining:
            return
        remaining.sort(key=lambda r: int(r.get("id", 0)))
        first = remaining[0]
        try:
            sync_nfos_for(int(first["id"]),
                          os.path.join(settings.media_root, first["file_path"]))
        except Exception as e:
            logger.debug("resync nfos failed dir=%s: %s", old_dir_abs, e)
    except Exception as e:
        logger.debug("resync old dir failed dir=%s: %s", old_dir_abs, e)


def _move_one(p: dict) -> dict:
    src = os.path.join(settings.media_root, p["from"])
    dst = os.path.join(settings.media_root, p["to"])
    if not os.path.exists(src):
        return {**p, "status": "skipped_missing_src"}
    if os.path.exists(dst):
        return {**p, "status": "conflict_disk_exists"}
    # 库内占用保护（评审 P1-06）：目标路径若挂在另一条库行上（哪怕文件缺失），
    # 先改库后挪盘会撞 UNIQUE(file_path) → 盘已动库未动。此处提前拒绝。
    existing = store.get_by_path(p["to"])
    if existing is not None and int(existing.get("id") or -1) != int(p["id"]):
        return {**p, "status": "conflict_db_occupied"}
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        followers = _sibling_followers(src)
        _rename_or_move(src, dst)
        # 本地写：只改 file_path，不碰 TMDB 镜像列
        try:
            store.update_movie_local(p["id"], file_path=p["to"])
        except Exception:
            # 库写失败回滚移动，保证盘/库一致（正常路径已被上面的占用检查挡住）
            try:
                os.rename(dst, src)
            except OSError:
                logger.error("move rollback failed id=%s dst=%s src=%s", p["id"],
                             p["to"], p["from"])
            raise
        # 规划期识别的版本/编号后缀落库（DB 为空才写，手工值优先），防下次预览回环
        try:
            cur = store.get_movie(p["id"]) or {}
        except Exception:
            cur = {}
        persist: dict = {}
        if p.get("edition") and not cur.get("edition"):
            persist["edition"] = p["edition"]
        if not cur.get("spec"):
            if p.get("numbered"):
                # 存完整渲染串（含前面的规格段），保证下次预览收敛
                full = ((p.get("spec") or "") + "-" if p.get("spec") else "") + p["numbered"]
                persist["spec"] = full
            elif p.get("spec"):
                persist["spec"] = p["spec"]
        if persist:
            try:
                store.update_movie_local(p["id"], **persist)
            except Exception as e:
                logger.debug("persist spec/edition failed id=%s: %s", p.get("id"), e)
        _write_nfos(p["id"], dst)
        # 花絮/字幕跟随：新 stem + 原后缀
        new_stem = os.path.splitext(os.path.basename(dst))[0]
        old_stem = os.path.splitext(os.path.basename(src))[0]
        followed = 0
        for f in followers:
            suffix = os.path.basename(f)[len(old_stem):]
            fdst = os.path.join(os.path.dirname(dst), new_stem + suffix)
            try:
                if not os.path.exists(fdst):
                    _rename_or_move(f, fdst)
                    followed += 1
            except OSError:
                continue
        _cleanup_old_dir(os.path.dirname(src))
        # 归属花絮跟随：搬进目标 extras/ 子目录（Plex 子目录名 collapsing，茎名清洗保留）
        _ex = move_attached_extras(p["id"], os.path.dirname(dst))
        extras_moved = _ex.get("moved", 0)
        # 旧目录收尾（花絮搬走后再清一次）：无正片则清 NFO，空目录删掉
        _cleanup_old_dir(os.path.dirname(src))
        # 跨目录搬迁：旧目录还有正片残留则重收敛（多→单删多余同名 NFO）
        if os.path.normpath(os.path.dirname(src)) != os.path.normpath(os.path.dirname(dst)):
            _resync_old_dir(os.path.dirname(src))
        # 同目录改名：删掉旧 stem 的 per-file NFO 残留（movie.nfo 已重写）
        if os.path.dirname(src) == os.path.dirname(dst):
            old_nfo = os.path.join(os.path.dirname(dst), old_stem + ".nfo")
            new_nfo = os.path.join(os.path.dirname(dst), new_stem + ".nfo")
            if old_nfo != new_nfo:
                try:
                    if os.path.exists(old_nfo):
                        os.remove(old_nfo)
                except OSError as e:
                    logger.debug("remove old nfo failed path=%s: %s", old_nfo, e)
        return {**p, "status": "moved", "followed": followed,
                "extras_moved": extras_moved}
    except Exception as e:
        logger.warning("move failed id=%s %s -> %s: %s", p.get("id"),
                       p.get("from"), p.get("to"), e)
        return {**p, "status": f"error: {e}"}

