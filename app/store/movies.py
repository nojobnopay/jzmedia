"""store.movies（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。"""
import json
import os
import time
from ..db import DB_PATH
from ..db import POSTER_DIR
from ..regions import country_name
from ._base import (DEFAULT_LIBRARY_ID, LOCAL_FIELDS, _attach_versions,
                    _collections_for_film, _conn, _lock, _row_to_dict, logger)
from .search import resync_fts
__all__ = ['upsert_movie_by_path', 'update_movie_local', 'update_movie_meta', 'get_by_path', 'list_movies_in_dir', 'get_movie', 'expand_ids_to_versions', 'get_movie_by_tmdb', 'get_movie_tags', 'list_movie_ids_by_library', 'list_movie_paths_by_tmdb', 'delete_movie', '_dir_size', 'library_stats']

def upsert_movie_by_path(file_path: str,
                         library_id: int = DEFAULT_LIBRARY_ID) -> int:
    with _lock, _conn() as c:
        c.execute("INSERT OR IGNORE INTO movies(file_path, library_id, updated_at)"
                  " VALUES(?, ?, ?)",
                  (file_path, int(library_id), int(time.time())))
        row = c.execute("SELECT id FROM movies WHERE file_path=? AND library_id=?",
                        (file_path, int(library_id))).fetchone()
        mid = int(row["id"])
        # 原始路径审计：仅首次入库（空值）时写入，之后搬迁改 file_path 也不碰它
        c.execute("UPDATE movies SET original_file_path=? WHERE id=? "
                  "AND (original_file_path IS NULL OR original_file_path='')",
                  (file_path, mid))
        return mid


def update_movie_local(movie_id: int, **fields) -> None:
    """本地写专用：只允许 LOCAL_FIELDS（file_path/手工标题/覆盖简介/评分/tags/待确认），
    传入 TMDB 镜像列会被静默丢弃，从机制上保证路径/标签小改动不污染镜像。"""
    safe = {k: v for k, v in fields.items() if k in LOCAL_FIELDS}
    if safe:
        update_movie_meta(movie_id, **safe)


def update_movie_meta(movie_id: int, **fields) -> None:
    allowed = {"file_path", "title", "original_title", "year", "overview", "overview_override",
               "title_auto",
               "tmdb_id", "imdb_id", "tmdb_rating", "douban_rating", "custom_rating",
               "poster_path", "genres", "genre_ids", "tags", "needs_review",
               "watched", "watched_at",
               "origin_country", "origin_countries", "original_language",
               "region", "media_type", "edition", "spec", "original_file_path",
               "nfo_hash", "match_source"}
    data = {k: (json.dumps(v, ensure_ascii=False) if k in ("genres", "genre_ids", "tags", "origin_countries") else v)
            for k, v in fields.items() if k in allowed}
    if not data:
        return
    data["updated_at"] = int(time.time())
    cols = ", ".join(f"{k}=?" for k in data)
    with _lock, _conn() as c:
        c.execute(f"UPDATE movies SET {cols} WHERE id=?", (*data.values(), movie_id))
    resync_fts(movie_id)


def get_by_path(file_path: str,
                library_id: int = DEFAULT_LIBRARY_ID) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM movies WHERE file_path=? AND library_id=?",
                        (file_path, int(library_id))).fetchone()
        return _row_to_dict(row) if row else None


def list_movies_in_dir(rel_dir: str,
                       library_id: int = DEFAULT_LIBRARY_ID) -> list[dict]:
    """同目录顶层 movies 行（不递归子目录），供 NFO 独占/共享判定用。
    只返回轻量列；调用方再按需 get_movie() 取全量（含人物）。"""
    norm = os.path.normpath((rel_dir or "").strip().strip("/"))
    with _lock, _conn() as c:
        if norm in ("", "."):
            rows = c.execute(
                "SELECT id, file_path, tmdb_id, title, year FROM movies "
                "WHERE library_id=? AND file_path NOT LIKE '%/%'",
                (int(library_id),)).fetchall()
            return [dict(r) for r in rows]
        esc = norm.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = c.execute(
            "SELECT id, file_path, tmdb_id, title, year FROM movies "
            "WHERE library_id=? AND file_path LIKE ? ESCAPE '\\'",
            (int(library_id), esc + "/%")).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if os.path.dirname(d.get("file_path", "").replace("\\", "/")) == norm:
                out.append(d)
        return out


def get_movie(movie_id: int) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        d["persons"] = [dict(r) for r in c.execute(
            "SELECT p.name, p.tmdb_id, p.avatar, mp.role, mp.character_name, mp.cast_order "
            "FROM persons p JOIN movie_person mp ON mp.person_id=p.id "
            "WHERE mp.movie_id=? ORDER BY mp.cast_order", (movie_id,))]
        if d["overview_override"]:
            d["overview_display"] = d["overview_override"]
        else:
            d["overview_display"] = d["overview"]
        # 主产地中文名后端下发（评审 B5a-3/R10-D6）：前端不再维护国家名映射
        primary = d.get("origin_country") or (
            (d.get("origin_countries") or [""])[0] if d.get("origin_countries") else "")
        d["origin_country_name"] = country_name(primary) if primary else ""
        d = _attach_versions(c, d)
        d["collections"] = _collections_for_film(c, d.get("tmdb_id"), d.get("id"))
        return d


def expand_ids_to_versions(rep_ids: list[int]) -> dict[int, list[int]]:
    """代表 id → 该海报全版本 id 列表（海报粒度批量操作的展开，限同库）。
    不存在的 id 映射为空列表。"""
    out: dict[int, list[int]] = {}
    with _lock, _conn() as c:
        for rid in rep_ids:
            try:
                rid = int(rid)
            except (TypeError, ValueError):
                continue
            row = c.execute("SELECT id, tmdb_id, library_id FROM movies WHERE id=?",
                            (rid,)).fetchone()
            if not row:
                out[rid] = []
                continue
            if row["tmdb_id"]:
                vers = [int(r["id"]) for r in c.execute(
                    "SELECT id FROM movies WHERE tmdb_id=? AND library_id=? ORDER BY id",
                    (row["tmdb_id"], row["library_id"]))]
                out[rid] = vers
            else:
                out[rid] = [rid]
    return out


def get_movie_by_tmdb(tmdb_id: int) -> dict | None:
    """同 tmdb_id 的代表行（最新更新），供回填进度展示标题用。"""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT *, MAX(updated_at) AS _u FROM movies WHERE tmdb_id=?"
            " GROUP BY COALESCE(tmdb_id, -id)", (int(tmdb_id),)).fetchone()
        if not row:
            return None
        return _attach_versions(c, _row_to_dict(row))


def list_movie_ids_by_library(library_id: int) -> list[int]:
    """某库全部版本行 id（删库/关会话用）。"""
    with _lock, _conn() as c:
        rows = c.execute("SELECT id FROM movies WHERE library_id=? ORDER BY id",
                         (int(library_id),)).fetchall()
        return [int(r["id"]) for r in rows]


def list_movie_paths_by_tmdb(tmdb_id: int, library_id) -> list[dict]:
    """同库同 tmdb 的版本行轻量列表（本地图片落盘判定用）。"""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT id, file_path FROM movies WHERE tmdb_id=? AND library_id=?"
            " ORDER BY id", (int(tmdb_id), int(library_id))).fetchall()
        return [dict(r) for r in rows]


def get_movie_tags(movie_id: int) -> list[str] | None:
    """仅取 tags（评审 B8/R05-B2：批量操作不再走重型 get_movie）。None=行不存在。"""
    with _lock, _conn() as c:
        row = c.execute("SELECT tags FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not row:
            return None
        try:
            v = json.loads(row["tags"] or "[]")
            return v if isinstance(v, list) else []
        except Exception:
            return []


def delete_movie(movie_id: int) -> bool:
    """彻底删除单行（软件外删片/移动后产生）：删关联+主行+FTS行。
    海报与 tmdb_cache 保留（多版本/重扫复用）。播放侧 media_info/progress 级联清理。
    花絮归属置空（评审 B7/R02-B2：悬挂 movie_id 会让花絮既不在归属也不在 orphan 列表）。"""
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM movie_person WHERE movie_id=?", (movie_id,))
        c.execute("DELETE FROM movies WHERE id=?", (movie_id,))
        c.execute("DELETE FROM movies_fts WHERE rowid=?", (movie_id,))
        try:
            c.execute("UPDATE extras SET movie_id=NULL, updated_at=? WHERE movie_id=?",
                      (int(time.time()), movie_id))
        except Exception as e:
            logger.warning("clear extras refs failed mid=%s: %s", movie_id, e)
        try:
            c.execute("DELETE FROM media_info WHERE kind='movie' AND item_id=?", (movie_id,))
        except Exception:
            pass
        try:
            c.execute("DELETE FROM playback_progress WHERE kind='movie' AND item_id=?",
                      (movie_id,))
        except Exception:
            pass
        return True


def _dir_size(path: str) -> int:
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def library_stats(library_id: int | None = None) -> dict:
    """库状态一览（设置页展示用，纯本地聚合）。library_id=None 为全库合计。"""
    where, params = "", ()
    if library_id is not None:
        where, params = " WHERE library_id=?", (int(library_id),)
    with _lock, _conn() as c:
        movies = c.execute(f"SELECT COUNT(*) AS n FROM movies{where}",
                           params).fetchone()["n"]
        versions = movies
        grouped = c.execute(
            f"SELECT COUNT(*) AS n FROM (SELECT 1 FROM movies{where}"
            " GROUP BY library_id, COALESCE(tmdb_id, -id))", params).fetchone()["n"]
        cond = " AND " if where else " WHERE "
        needs_review = c.execute(
            f"SELECT COUNT(*) AS n FROM movies{where}{cond}needs_review=1",
            params).fetchone()["n"]
        no_match = c.execute(
            f"SELECT COUNT(*) AS n FROM movies{where}{cond}tmdb_id IS NULL",
            params).fetchone()["n"]
        cache = c.execute("SELECT COUNT(*) AS n FROM tmdb_cache").fetchone()["n"]
        persons = c.execute("SELECT COUNT(*) AS n FROM persons").fetchone()["n"]
    try:
        db_bytes = os.path.getsize(DB_PATH)
    except OSError:
        db_bytes = 0
    return {"movies": movies, "versions": versions, "grouped": grouped,
            "needs_review": int(needs_review), "no_match": int(no_match),
            "tmdb_cache": cache, "persons": persons,
            "db_bytes": db_bytes, "posters_bytes": _dir_size(POSTER_DIR)}

