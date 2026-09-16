"""scanner.nfo_link（自 app/scanner.py 拆分，评审 B9/R03-Q1；对外经 app.scanner 门面使用）。"""
import os
from ..config import settings
from .. import store
from ..nfo import write_movie_nfo
from ..log import get_logger
logger = get_logger("scanner.nfo_link")
from .parse import normalize_title, parse_filename
from .classify import is_feature_video
__all__ = ['_list_dir_nfos', '_same_film', '_ref_title_year', 'sync_nfos_for', '_write_nfo_for']

def _list_dir_nfos(movie_dir_abs: str) -> tuple[list[str] | None, list[str]]:
    """目录顶层 (全部文件名|None不可读, 正片视频名列表)。只看顶层，不进 extras/ 等子目录。"""
    try:
        names = sorted(os.listdir(movie_dir_abs))
    except OSError:
        return None, []
    feats = []
    for n in names:
        full = os.path.join(movie_dir_abs, n)
        try:
            if not os.path.isfile(full):
                continue
        except OSError:
            continue
        try:
            rel = os.path.relpath(full, settings.media_root)
        except ValueError:
            continue
        if is_feature_video(rel):
            feats.append(n)
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


def _ref_title_year(movie: dict, abs_path: str) -> tuple[str, object]:
    t = ((movie or {}).get("title") or "")
    y = (movie or {}).get("year")
    if t:
        return t, y
    try:
        p = parse_filename(os.path.basename(abs_path or ""))
        return p.get("title") or "", p.get("year")
    except Exception:
        return "", y


def sync_nfos_for(mid: int, abs_path: str, dry_run: bool = False) -> dict:
    """NFO 收敛唯一入口（幂等，失败自吞，调用方无需 try）。

    规则：movie.nfo 在独占目录永远保留；仅“同片多行同目录”（同 tmdb_id，
    无 id 则同归一标题+年份）才补各版本同名 .nfo；单版本删历史残留的同名 .nfo；
    不同电影混放的共享目录只写当前文件的同名 .nfo，不碰 movie.nfo、不删别人的。
    返回 {ok, mode, dir, wrote[], deleted[]}（dry_run 只计算不落盘）。
    """
    try:
        movie = store.get_movie(mid)
        if not movie:
            return {"ok": False, "mode": "missing", "dir": "",
                    "wrote": [], "deleted": []}
        movie_dir = os.path.dirname(abs_path)
        try:
            rel_dir = os.path.relpath(movie_dir, settings.media_root)
            if rel_dir == ".":
                rel_dir = ""
        except ValueError:
            rel_dir = os.path.dirname(movie.get("file_path", ""))
        stem = os.path.splitext(os.path.basename(abs_path))[0]
        if movie_dir and not os.path.isdir(movie_dir):
            # 目录尚不存在（如搬迁竞态）：退化为旧双写，保证元数据不丢
            if not dry_run:
                try:
                    write_movie_nfo(movie, os.path.join(movie_dir, "movie.nfo"))
                    if stem and stem != "movie":
                        write_movie_nfo(movie, os.path.join(movie_dir, stem + ".nfo"))
                except Exception as e:
                    logger.warning("nfo fallback write failed mid=%s dir=%s: %s",
                                   mid, rel_dir, e)
                    return {"ok": False, "mode": "fallback", "dir": rel_dir,
                            "wrote": [], "deleted": []}
            return {"ok": True, "mode": "fallback", "dir": rel_dir,
                    "wrote": ["movie.nfo"] + ([stem + ".nfo"] if stem and stem != "movie" else []),
                    "deleted": []}
        names, feats = _list_dir_nfos(movie_dir)
        if names is None:
            if not dry_run:
                try:
                    write_movie_nfo(movie, os.path.join(movie_dir, "movie.nfo"))
                    if stem and stem != "movie":
                        write_movie_nfo(movie, os.path.join(movie_dir, stem + ".nfo"))
                except Exception as e:
                    logger.warning("nfo fallback write failed mid=%s dir=%s: %s",
                                   mid, rel_dir, e)
                    return {"ok": False, "mode": "fallback", "dir": rel_dir,
                            "wrote": [], "deleted": []}
            wrote = ["movie.nfo"] + ([stem + ".nfo"] if stem and stem != "movie" else [])
            return {"ok": True, "mode": "fallback", "dir": rel_dir,
                    "wrote": wrote, "deleted": []}
        if stem == "movie":
            # 文件本身就叫 movie.*：同名 NFO 即 movie.nfo，只写一份
            if not dry_run:
                write_movie_nfo(movie, os.path.join(movie_dir, "movie.nfo"))
            return {"ok": True, "mode": "single-movie-stem", "dir": rel_dir,
                    "wrote": ["movie.nfo"], "deleted": []}
        try:
            rows = store.list_movies_in_dir(rel_dir)
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
        ref_title, ref_year = _ref_title_year(movie, abs_path)
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
            target = os.path.join(movie_dir, stem + ".nfo")
            if not dry_run:
                write_movie_nfo(movie, target)
            return {"ok": True, "mode": "shared", "dir": rel_dir,
                    "wrote": [stem + ".nfo"], "deleted": []}
        existing_nfos = [n for n in (names or [])
                         if n.endswith(".nfo") and os.path.isfile(os.path.join(movie_dir, n))]
        if len(feats) <= 1:
            deleted = [n for n in existing_nfos if n != "movie.nfo"]
            if not dry_run:
                write_movie_nfo(movie, os.path.join(movie_dir, "movie.nfo"))
                for n in deleted:
                    try:
                        os.remove(os.path.join(movie_dir, n))
                    except OSError as e:
                        logger.debug("nfo remove failed file=%s: %s", n, e)
            return {"ok": True, "mode": "single", "dir": rel_dir,
                    "wrote": ["movie.nfo"], "deleted": sorted(deleted)}
        # 独占多版本：movie.nfo + 每个相关版本各写同名（各用各行的全量 dict，保留手工评分差异）
        wanted = {"movie.nfo"}
        writers: list[tuple[str, dict]] = [("movie.nfo", movie)]
        for f in sorted(related):
            st = os.path.splitext(f)[0]
            if not st or st == "movie":
                continue
            wanted.add(st + ".nfo")
            row = row_by_base.get(f)
            full = movie
            if row is not None and int(row.get("id", -1)) != int(mid):
                try:
                    got = store.get_movie(int(row["id"]))
                    if got:
                        full = got
                except Exception as e:
                    logger.debug("load version row failed id=%s: %s", row.get("id"), e)
            writers.append((st + ".nfo", full))
        deleted = [n for n in existing_nfos if n not in wanted]
        if not dry_run:
            failed = 0
            for name, data in writers:
                try:
                    write_movie_nfo(data, os.path.join(movie_dir, name))
                except Exception as e:
                    failed += 1
                    logger.warning("nfo write failed mid=%s file=%s: %s", mid, name, e)
            for n in deleted:
                try:
                    os.remove(os.path.join(movie_dir, n))
                except OSError as e:
                    logger.debug("nfo remove failed file=%s: %s", n, e)
            if failed:
                return {"ok": False, "mode": "multi", "dir": rel_dir,
                        "wrote": sorted({n for n, _ in writers}), "deleted": sorted(deleted)}
        return {"ok": True, "mode": "multi", "dir": rel_dir,
                "wrote": sorted({n for n, _ in writers}), "deleted": sorted(deleted)}
    except Exception as e:
        logger.warning("sync_nfos_for failed mid=%s dir=%s: %s", mid, abs_path, e)
        return {"ok": False, "mode": "error", "dir": "",
                "wrote": [], "deleted": []}


def _write_nfo_for(mid: int, abs_path: str) -> bool:
    try:
        return bool(sync_nfos_for(mid, abs_path).get("ok"))
    except Exception:
        logger.debug("write nfo failed mid=%s path=%s", mid, abs_path, exc_info=True)
        return False

