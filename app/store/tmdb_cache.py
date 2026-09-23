"""store.tmdb_cache（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。"""
import json
import time
from ._base import _conn, _dump_list, _lock, logger
from .movies import update_movie_meta
__all__ = ['seed_tmdb_cache_from_movies', 'get_tmdb_cached', 'upsert_tmdb_cache', 'list_movie_ids_by_tmdb', 'copy_tmdb_to_movie', 'tmdb_ids_missing_collection', 'set_poster_override']

def _match_source_id(tmdb_id: int, media_type: str) -> str:
    """match_index source_id：电影保持历史格式（`123`）避免重复播种，剧集加前缀。"""
    return str(int(tmdb_id)) if (media_type or "movie") == "movie" \
        else f"{media_type}:{int(tmdb_id)}"


def _index_match(meta: dict, tmdb_id: int, media_type: str = "movie") -> None:
    """同步离线候选索引（E）：每次 TMDB 缓存写入后刷新 match_index。"""
    try:
        from .match_index import upsert_match_entry
        upsert_match_entry("tmdb", _match_source_id(tmdb_id, media_type),
                           str(media_type or "movie"),
                           meta.get("title", "") or "",
                           meta.get("original_title", "") or "",
                           meta.get("year"), int(tmdb_id),
                           meta.get("imdb_id", "") or "",
                           {"collection_tmdb_id": meta.get("collection_tmdb_id")})
    except Exception as e:
        logger.debug("index tmdb entry failed tmdb_id=%s: %s", tmdb_id, e)


def seed_tmdb_cache_from_movies() -> int:
    """离线种子：用 movies 现有行补 tmdb_cache 缺失项，不调网。
    credits 为空（人物链接已在 movie_person 中，新版本复用时走 sibling 复制），fetched_at=0 标记“本地种子”。"""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT *, MAX(updated_at) AS _u FROM movies WHERE tmdb_id IS NOT NULL "
            "GROUP BY tmdb_id").fetchall()
        inserted = 0
        for r in rows:
            tid = r["tmdb_id"]
            if tid is None:
                continue
            exists = c.execute("SELECT 1 FROM tmdb_cache"
                               " WHERE media_type='movie' AND tmdb_id=?", (tid,)).fetchone()
            if exists:
                continue
            c.execute(
                "INSERT INTO tmdb_cache(tmdb_id, media_type, title, original_title, year,"
                " overview, imdb_id, tmdb_rating, genres, genre_ids, origin_country,"
                " origin_countries, original_language, region, poster_tmdb_path, credits,"
                " fetched_at)"
                " VALUES(?, 'movie', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, r["title"] or "", r["original_title"] or "", r["year"],
                 r["overview"] or "", r["imdb_id"] or "", r["tmdb_rating"],
                 r["genres"] or "[]", r["genre_ids"] or "[]",
                 r["origin_country"] or "", r["origin_countries"] or "[]",
                 r["original_language"] or "", r["region"] or "",
                 "", '{"cast":[],"crew":[]}', 0))
            inserted += 1
        return inserted


def get_tmdb_cached(tmdb_id: int, media_type: str = "movie") -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM tmdb_cache WHERE media_type=? AND tmdb_id=?",
                        (str(media_type or "movie"), tmdb_id)).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("genres", "genre_ids", "origin_countries", "studios"):
            try:
                v = json.loads(d.get(k) or "[]")
                d[k] = v if isinstance(v, list) else []
            except Exception:
                d[k] = []
        try:
            cr = json.loads(d.get("credits") or '{"cast":[],"crew":[]}')
            d["credits"] = cr if isinstance(cr, dict) else {"cast": [], "crew": []}
        except Exception:
            d["credits"] = {"cast": [], "crew": []}
        return d


def upsert_tmdb_cache(tmdb_id: int, meta: dict,
                      credits: dict | None = None,
                      poster_tmdb_path: str = "",
                      media_type: str = "movie") -> bool:
    """写入镜像。无变化时仅刷新 fetched_at 并返回 False（调用方应跳过 movies 传播）。
    meta 为 meta_from_detail() 产出的 TMDB 列字典。返回 True=内容变化。
    media_type 为复合主键组成部分（movie|tv，缺省 movie 保持旧行为）。
    每次成功写入都盖 collection_checked_at（本次抓取已确认系列状态，
    含“确认无系列”的阴性结论；失败抛异常走不到这里，下次继续排查）。"""
    media_type = str(media_type or "movie")
    now = int(time.time())
    genres_s = _dump_list(meta.get("genres"))
    genre_ids_s = _dump_list(meta.get("genre_ids"))
    origin_countries_s = _dump_list(meta.get("origin_countries"))
    credits_s = json.dumps(credits or {"cast": [], "crew": []}, ensure_ascii=False, sort_keys=True)
    poster_tmdb_path = poster_tmdb_path or ""
    premiered = str(meta.get("premiered") or "")[:10]
    tagline = str(meta.get("tagline") or "")
    try:
        runtime = max(0, int(meta.get("runtime") or 0))
    except (TypeError, ValueError):
        runtime = 0
    studios_s = _dump_list(meta.get("studios"))
    backdrop = str(meta.get("backdrop_tmdb_path") or "")
    logo = str(meta.get("logo_tmdb_path") or "")
    try:
        payload_s = json.dumps(meta, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload_s = "{}"
    col_id = meta.get("collection_tmdb_id")
    try:
        col_id = int(col_id) if col_id is not None else None
    except (TypeError, ValueError):
        col_id = None
    col_name = meta.get("collection_name") or ""
    col_poster = meta.get("collection_poster_path") or ""
    def _apply(c) -> bool:
        row = c.execute("SELECT * FROM tmdb_cache WHERE media_type=? AND tmdb_id=?",
                        (media_type, tmdb_id)).fetchone()
        # v19：手工选过候选海报 → 刷新不得把默认海报写回去（override 只由 set_poster_override 改）
        if row is not None:
            try:
                override = (row["poster_override"] or "").strip()
            except Exception:
                override = ""
            if override:
                nonlocal poster_tmdb_path
                poster_tmdb_path = override
        if not row:
            c.execute(
                "INSERT INTO tmdb_cache(tmdb_id, title, original_title, year, overview,"
                " imdb_id, tmdb_rating, genres, genre_ids, origin_country, origin_countries,"
                " original_language, region, media_type, poster_tmdb_path, credits,"
                " collection_tmdb_id, collection_name, collection_poster_path,"
                " collection_checked_at, fetched_at, source, payload_json, premiered,"
                " tagline, runtime, studios, backdrop_tmdb_path, logo_tmdb_path)"
                " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " 'tmdb', ?, ?, ?, ?, ?, ?, ?)",
                (tmdb_id, meta.get("title", "") or "", meta.get("original_title", "") or "",
                 meta.get("year"), meta.get("overview", "") or "",
                 meta.get("imdb_id", "") or "", meta.get("tmdb_rating"),
                 genres_s, genre_ids_s,
                 meta.get("origin_country", "") or "", origin_countries_s,
                 meta.get("original_language", "") or "", meta.get("region", "") or "",
                 media_type,
                 poster_tmdb_path, credits_s, col_id, col_name, col_poster, now, now,
                 payload_s, premiered, tagline, runtime, studios_s, backdrop, logo))
            return True
        # 兼容老库：SELECT * 可能无新列
        try:
            old_col_id = row["collection_tmdb_id"]
        except Exception:
            old_col_id = None
        try:
            old_col_name = row["collection_name"] or ""
        except Exception:
            old_col_name = ""
        try:
            old_col_poster = row["collection_poster_path"] or ""
        except Exception:
            old_col_poster = ""
        same = (
            (row["title"] or "") == (meta.get("title", "") or "")
            and (row["original_title"] or "") == (meta.get("original_title", "") or "")
            and row["year"] == meta.get("year")
            and (row["overview"] or "") == (meta.get("overview", "") or "")
            and (row["imdb_id"] or "") == (meta.get("imdb_id", "") or "")
            and (row["tmdb_rating"] == meta.get("tmdb_rating"))
            and (row["genres"] or "[]") == genres_s
            and (row["genre_ids"] or "[]") == genre_ids_s
            and (row["origin_country"] or "") == (meta.get("origin_country", "") or "")
            and (row["origin_countries"] or "[]") == origin_countries_s
            and (row["original_language"] or "") == (meta.get("original_language", "") or "")
            and (row["region"] or "") == (meta.get("region", "") or "")
            and (row["media_type"] or "movie") == media_type
            and (row["poster_tmdb_path"] or "") == poster_tmdb_path
            and (row["credits"] or '{"cast":[],"crew":[]}') == credits_s
            and (old_col_id == col_id)
            and (old_col_name == col_name)
            and (old_col_poster == col_poster)
            and (row["premiered"] or "") == premiered
            and (row["tagline"] or "") == tagline
            and int(row["runtime"] or 0) == runtime
            and (row["studios"] or "[]") == studios_s
            and (row["backdrop_tmdb_path"] or "") == backdrop
            and (row["logo_tmdb_path"] or "") == logo
        )
        if same:
            c.execute("UPDATE tmdb_cache SET fetched_at=?, collection_checked_at=?"
                      " WHERE media_type=? AND tmdb_id=?",
                      (now, now, media_type, tmdb_id))
            return False
        c.execute(
            "UPDATE tmdb_cache SET title=?, original_title=?, year=?, overview=?,"
            " imdb_id=?, tmdb_rating=?, genres=?, genre_ids=?, origin_country=?,"
            " origin_countries=?, original_language=?, region=?, media_type=?,"
            " poster_tmdb_path=?, credits=?, collection_tmdb_id=?,"
            " collection_name=?, collection_poster_path=?,"
            " collection_checked_at=?, fetched_at=?, payload_json=?, premiered=?,"
             " tagline=?, runtime=?, studios=?, backdrop_tmdb_path=?, logo_tmdb_path=?"
             " WHERE media_type=? AND tmdb_id=?",
             (meta.get("title", "") or "", meta.get("original_title", "") or "",
              meta.get("year"), meta.get("overview", "") or "",
              meta.get("imdb_id", "") or "", meta.get("tmdb_rating"),
              genres_s, genre_ids_s,
              meta.get("origin_country", "") or "", origin_countries_s,
              meta.get("original_language", "") or "", meta.get("region", "") or "",
              media_type,
              poster_tmdb_path, credits_s, col_id, col_name, col_poster, now, now,
              payload_s, premiered, tagline, runtime, studios_s, backdrop, logo,
              media_type, tmdb_id))
        return True

    with _lock, _conn() as c:
        changed = _apply(c)
    if changed:
        _index_match(meta, tmdb_id, media_type)
    return changed


def set_poster_override(tmdb_id: int, file_path: str, media_type: str = "movie") -> bool:
    """记录候选海报手工选择（v19）：写 `poster_override` + `poster_tmdb_path`。

    此后 `upsert_tmdb_cache`（刷新/重刮）不会把 TMDB 默认海报写回。
    传空串 = 清除选择（仅清 override，不重置当前 poster_tmdb_path）。返回是否有该缓存行。
    """
    fp = (file_path or "").strip()
    tid = int(tmdb_id)
    mt = str(media_type or "movie")
    with _lock, _conn() as c:
        if fp:
            cur = c.execute("UPDATE tmdb_cache SET poster_override=?, poster_tmdb_path=?"
                            " WHERE media_type=? AND tmdb_id=?", (fp, fp, mt, tid))
        else:
            cur = c.execute("UPDATE tmdb_cache SET poster_override=''"
                            " WHERE media_type=? AND tmdb_id=?", (mt, tid))
        return cur.rowcount > 0


def list_movie_ids_by_tmdb(tmdb_id: int) -> list[int]:
    with _lock, _conn() as c:
        return [int(r["id"]) for r in
                c.execute("SELECT id FROM movies WHERE tmdb_id=? ORDER BY id", (tmdb_id,))]


def copy_tmdb_to_movie(movie_id: int, old_title: str | None = None) -> bool:
    """从 tmdb_cache 向单行 movies 复制 TMDB 列（不含 poster_path，海报由 scanner 按文件存在性处理）。
    标题保护：old_title=None（新建/离线补齐）时空标题才写入；old_title!=None（刷新路径）时
    仅当当前标题==old_title 或为空才跟随新标题，否则视为手工改过予以保留。返回是否实际写入。"""
    with _lock, _conn() as c:
        mrow = c.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
        if not mrow or not mrow["tmdb_id"]:
            return False
        crow = c.execute("SELECT * FROM tmdb_cache WHERE media_type='movie' AND tmdb_id=?",
                         (mrow["tmdb_id"],)).fetchone()
        if not crow:
            return False
        cur_title = (mrow["title"] or "")
        new_title = (crow["title"] or "")
        auto_title = int(mrow["title_auto"] or 0) if "title_auto" in mrow.keys() else 0
        if old_title is None:
            # 空标题 or 扫描自动写入的文件名标题 → 允许 TMDB 标题覆盖
            want_title = (not cur_title) or bool(auto_title)
        else:
            want_title = (not cur_title) or (cur_title == (old_title or "")) or bool(auto_title)
        fields: dict = {
            "original_title": crow["original_title"] or "",
            "year": crow["year"],
            "overview": crow["overview"] or "",
            "tmdb_id": crow["tmdb_id"],
            "imdb_id": crow["imdb_id"] or "",
            "tmdb_rating": crow["tmdb_rating"],
            "genres": crow["genres"] or "[]",
            "genre_ids": crow["genre_ids"] or "[]",
            "origin_country": crow["origin_country"] or "",
            "origin_countries": crow["origin_countries"] or "[]",
            "original_language": crow["original_language"] or "",
            "region": crow["region"] or "",
            "media_type": crow["media_type"] or "movie",
        }
        if want_title:
            fields["title"] = new_title
            fields["title_auto"] = 0   # 标题来源转为 TMDB（此后视为受保护标题）
    if not fields:
        return False
    # 走 update_movie_meta 以复用 updated_at+FTS 逻辑（调用方已判定确需写入）
    update_movie_meta(movie_id, **{k: (json.loads(v) if k in ("genres", "genre_ids", "origin_countries") and isinstance(v, str) else v)
                                   for k, v in fields.items()})
    return True


def tmdb_ids_missing_collection(limit: int = 200, force: bool = False) -> list[int]:
    """待排查系列信息的 tmdb_id 列表（cache 缺失或系列为空且未确认过），供回填口用。
    确认无系列的独立片已盖 collection_checked_at，不再重复检查；force=True 忽略盖戳全量重查。"""
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 200
    with _lock, _conn() as c:
        if force:
            rows = c.execute(
                "SELECT DISTINCT m.tmdb_id AS tid FROM movies m"
                " LEFT JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id AND t.media_type='movie'"
                " WHERE m.tmdb_id IS NOT NULL"
                " AND (t.tmdb_id IS NULL OR t.collection_tmdb_id IS NULL)"
                " ORDER BY m.tmdb_id LIMIT ?", (limit,)).fetchall()
        else:
            try:
                rows = c.execute(
                    "SELECT DISTINCT m.tmdb_id AS tid FROM movies m"
                    " LEFT JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id AND t.media_type='movie'"
                    " WHERE m.tmdb_id IS NOT NULL"
                    " AND (t.tmdb_id IS NULL"
                    " OR (t.collection_tmdb_id IS NULL"
                    " AND COALESCE(t.collection_checked_at, 0) = 0))"
                    " ORDER BY m.tmdb_id LIMIT ?", (limit,)).fetchall()
            except Exception:
                # 极老库无盖戳列时退化为旧口径
                rows = c.execute(
                    "SELECT DISTINCT m.tmdb_id AS tid FROM movies m"
                    " LEFT JOIN tmdb_cache t ON t.tmdb_id=m.tmdb_id AND t.media_type='movie'"
                    " WHERE m.tmdb_id IS NOT NULL"
                    " AND (t.tmdb_id IS NULL OR t.collection_tmdb_id IS NULL)"
                    " ORDER BY m.tmdb_id LIMIT ?", (limit,)).fetchall()
        return [int(r["tid"]) for r in rows if r["tid"]]

