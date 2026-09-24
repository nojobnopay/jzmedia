"""data/posters 图片布局（单一来源）。

按功能拆子目录（根部不再扁平混放）：

    movies/<tmdb>.jpg          电影主海报（w500）
    orig/<tmdb>.jpg            电影原图缓存（original；目录即语义，去掉旧 `_orig` 后缀）
    backdrops/movie_<id>.jpg   电影背景（w780）
    backdrops/tv_<id>.jpg      剧集背景（电影/剧集 TMDB id 空间独立，必须加前缀区分）
    persons/<id>.jpg           人物头像（w185，去掉旧 `person_` 前缀）
    tv/<id>.jpg                剧集海报（w500，去掉旧 `tv_` 前缀）
    tv/<id>_s<N>.jpg           季海报（w300，`_sN` 保留以区分）
    stills/<episode_id>.jpg    集剧照（w300 懒下载，去掉旧 `tv_e` 前缀）
    cand/                      换海报候选缩略图（已是子目录，不动）
    tvcast/                    剧集演职头像缓存（已是子目录，不动）

DB 存 DATA_DIR 相对路径（如 `posters/movies/123.jpg`）；`resolve()` 兼容
旧值（裸文件名 / 根部旧名前缀 / `posters/<旧名>`），供读侧回退。
存量文件 + DB 值由 `migrate_posters()` 在启动时一次性搬迁（幂等、可重入）。
"""
import os
import re

from .log import get_logger

logger = get_logger("posters")

# ensure_dirs 建目录用（db.ensure_dirs 运行时 lazy-import，避免导入期循环）。
SUBDIRS = ("movies", "orig", "backdrops", "persons", "tv", "stills",
           "cand", "tvcast")

_EXT = r"(?:jpg|jpeg|png)"


def movie_poster_rel(tmdb_id: int) -> str:
    """电影主海报（DATA_DIR 相对路径，入库用）。"""
    return f"posters/movies/{int(tmdb_id)}.jpg"


def movie_orig_rel(tmdb_id: int) -> str:
    """电影原图缓存（DATA_DIR 相对路径，不入库、按需重建）。"""
    return f"posters/orig/{int(tmdb_id)}.jpg"


def movie_backdrop_rel(tmdb_id: int) -> str:
    return f"posters/backdrops/movie_{int(tmdb_id)}.jpg"


def person_avatar_rel(person_tmdb_id: int) -> str:
    return f"posters/persons/{int(person_tmdb_id)}.jpg"


def tv_poster_rel(tmdb_id: int) -> str:
    return f"posters/tv/{int(tmdb_id)}.jpg"


def tv_backdrop_rel(tmdb_id: int) -> str:
    return f"posters/backdrops/tv_{int(tmdb_id)}.jpg"


def tv_season_poster_rel(tmdb_id: int, season: int) -> str:
    return f"posters/tv/{int(tmdb_id)}_s{int(season)}.jpg"


def episode_still_rel(episode_id: int) -> str:
    return f"posters/stills/{int(episode_id)}.jpg"


# ---- 旧根部文件名 → 新 POSTER_DIR 相对路径（迁移 + 读回退共用） ----

_LEGACY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"^tv_(\d+)_s(\d+)\.{_EXT}$"), r"tv/\1_s\2.jpg"),
    (re.compile(rf"^tv_backdrop_(\d+)\.{_EXT}$"), r"backdrops/tv_\1.jpg"),
    (re.compile(rf"^tv_e(\d+)\.{_EXT}$"), r"stills/\1.jpg"),
    (re.compile(rf"^tv_(\d+)\.{_EXT}$"), r"tv/\1.jpg"),
    (re.compile(rf"^backdrop_(\d+)\.{_EXT}$"), r"backdrops/movie_\1.jpg"),
    (re.compile(rf"^person_(\d+)\.{_EXT}$"), r"persons/\1.jpg"),
    (re.compile(rf"^(\d+)_orig\.{_EXT}$"), r"orig/\1.jpg"),
    (re.compile(rf"^(\d+)\.{_EXT}$"), r"movies/\1.jpg"),
]


def legacy_rel(name: str) -> str:
    """旧根部文件名 → 新 POSTER_DIR 相对路径；不匹配返回 ''。"""
    base = os.path.basename(str(name or "").strip())
    for pat, tpl in _LEGACY_PATTERNS:
        m = pat.match(base)
        if m:
            return m.expand(tpl)
    return ""


def resolve(poster_dir: str, value: str, data_dir: str | None = None) -> str:
    """DB/调用方给的图片值 → 绝对路径；不存在返回 ''。

    兼容：新 DATA_DIR 相对值（`posters/movies/1.jpg`）、POSTER_DIR 相对值
    （`movies/1.jpg`）、旧裸文件名（`tv_1.jpg`，自动映射到新位置，兼顾
    根部残留旧文件回退）。
    """
    v = str(value or "").strip()
    if not v:
        return ""
    cands: list[str] = []
    if data_dir and (v.startswith("posters/") or "/" in v):
        cands.append(os.path.join(data_dir, v))
    if "/" in v:
        # POSTER_DIR 相对值或未知子路径：直接拼 + basename 映射兜底
        cands.append(os.path.join(poster_dir, v))
        mapped = legacy_rel(v)
        if mapped:
            cands.append(os.path.join(poster_dir, mapped))
    else:
        mapped = legacy_rel(v)
        if mapped:
            cands.append(os.path.join(poster_dir, mapped))
        cands.append(os.path.join(poster_dir, v))
    for c in cands:
        try:
            if c and os.path.isfile(c) and os.path.getsize(c) > 0:
                return c
        except OSError:
            continue
    return ""


def _move_file(src: str, dst: str) -> str:
    """原子搬迁（同盘 replace）；返回 moved|reused|error。"""
    try:
        if os.path.isfile(dst) and os.path.getsize(dst) > 0:
            try:
                os.remove(src)
            except OSError:
                pass
            return "reused"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(src, dst)
        return "moved"
    except OSError as e:
        logger.warning("poster migrate failed %s -> %s: %s", src, dst, e)
        return "error"


# DB 列改写规则：(table, column, 匹配旧值的正则, 新值模板 DATA_DIR 相对)
_DB_RULES: list[tuple[str, str, re.Pattern, str]] = [
    ("movies", "poster_path",
     re.compile(rf"^(?:posters/)?(\d+)\.{_EXT}$"), r"posters/movies/\1.jpg"),
    ("persons", "avatar",
     re.compile(rf"^(?:posters/)?person_(\d+)\.{_EXT}$"), r"posters/persons/\1.jpg"),
    ("tv_shows", "poster_path",
     re.compile(rf"^(?:posters/)?tv_(\d+)\.{_EXT}$"), r"posters/tv/\1.jpg"),
    ("tv_shows", "backdrop_path",
     re.compile(rf"^(?:posters/)?tv_backdrop_(\d+)\.{_EXT}$"),
     r"posters/backdrops/tv_\1.jpg"),
    ("tv_seasons", "poster_path",
     re.compile(rf"^(?:posters/)?tv_(\d+)_s(\d+)\.{_EXT}$"),
     r"posters/tv/\1_s\2.jpg"),
    ("collections", "poster_path",
     re.compile(rf"^(?:posters/)?(\d+)\.{_EXT}$"), r"posters/movies/\1.jpg"),
]


def remap_db_value(table: str, col: str, value: str) -> str:
    """DB 旧值 → 新值；不匹配规则原样返回（纯函数，可单测）。"""
    v = str(value or "").strip()
    for t, c, pat, tpl in _DB_RULES:
        if t == table and c == col:
            m = pat.match(v)
            return m.expand(tpl) if m else v
    return v


def migrate_posters(poster_dir: str | None = None,
                    data_dir: str | None = None) -> dict:
    """启动一次性搬迁：根部旧文件移入子目录 + DB 旧值改写。幂等、可重入，
    失败自吞（记 warning，不阻塞启动）。返回 {moved, reused, db_updated}。"""
    from .config import settings
    from .db import POSTER_DIR as _DEFAULT_POSTER_DIR
    poster_dir = poster_dir or _DEFAULT_POSTER_DIR
    data_dir = data_dir or settings.data_dir
    stats = {"moved": 0, "reused": 0, "db_updated": 0}
    try:
        entries = os.listdir(poster_dir)
    except OSError as e:
        logger.warning("poster migrate list failed dir=%s: %s", poster_dir, e)
        return stats
    for name in entries:
        src = os.path.join(poster_dir, name)
        try:
            if not os.path.isfile(src):
                continue
            if os.path.getsize(src) == 0:
                try:
                    os.remove(src)
                except OSError:
                    pass
                continue
            new_rel = legacy_rel(name)
            if not new_rel:
                continue
            # 目标已存在非空（重复跑/手工投放）则只删源，不覆盖
            r = _move_file(src, os.path.join(poster_dir, new_rel))
            if r == "moved":
                stats["moved"] += 1
            elif r == "reused":
                stats["reused"] += 1
        except OSError as e:
            logger.debug("poster migrate skip %s: %s", name, e)
            continue
    # DB 旧值改写（逐行 Python 映射，量级小：头像行最多几千）
    try:
        from . import store as _store
        with _store._lock, _store._conn() as c:
            for table, col, pat, tpl in _DB_RULES:
                try:
                    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                except Exception:
                    continue
                if col not in cols:
                    continue
                try:
                    rows = c.execute(
                        f"SELECT rowid, {col} FROM {table}"
                        f" WHERE {col} IS NOT NULL AND {col}!=''").fetchall()
                except Exception:
                    continue
                for rowid, val in rows:
                    new_val = remap_db_value(table, col, str(val or ""))
                    if new_val == (val or ""):
                        continue
                    try:
                        c.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?",
                                  (new_val, rowid))
                        stats["db_updated"] += 1
                    except Exception as e:
                        logger.debug("poster migrate db skip %s.%s: %s",
                                     table, col, e)
                        continue
    except Exception as e:
        logger.warning("poster migrate db failed: %s", e)
    if stats["moved"] or stats["db_updated"]:
        logger.info("poster migrate done moved=%s reused=%s db_updated=%s",
                    stats["moved"], stats["reused"], stats["db_updated"])
    return stats


__all__ = ['SUBDIRS', 'movie_poster_rel', 'movie_orig_rel', 'movie_backdrop_rel',
           'person_avatar_rel', 'tv_poster_rel', 'tv_backdrop_rel',
           'tv_season_poster_rel', 'episode_still_rel', 'legacy_rel',
           'resolve', 'remap_db_value', 'migrate_posters']
