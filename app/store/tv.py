"""store.tv：TV 剧/季/集三层（T1 扫描入库；T2 起 TMDB 刮削回填元数据）。

- 剧名分组键 `(library_id, title, COALESCE(year,0))`（SQLite UNIQUE 对 NULL 不去重，
  因此 upsert 手动查重，不用内联 UNIQUE）。
- 集以 `(library_id, file_path)` 幂等；file_path 为库内相对路径。
- 重扫只更新季/集/路径/区间，**不覆盖已刮削的标题/简介等元数据**（空值才补）。
"""
import os
import re
import time

from ._base import DEFAULT_LIBRARY_ID, _conn, _like_esc, _lock, logger
from .search import (_query_terms, _split_ints, _split_multi, normalize_sort)

__all__ = ['upsert_show', 'upsert_episode', 'upsert_season', 'list_shows', 'get_show',
           'list_episodes', 'get_episode', 'count_shows', 'count_episodes',
           'delete_episode_by_path', 'delete_episodes_not_in', 'prune_empty_shows',
           'update_show_meta', 'update_episode_meta', 'season_offsets',
           'remap_absolute_episodes', 'list_shows_for_scrape', 'set_show_match',
           'mark_episode_watched', 'mark_show_watched', 'next_episode',
           'episode_after', 'get_show_meta', 'episode_progress_map',
           'list_episode_versions', 'list_seasons', 'get_episode_by_path',
           'find_show_by_dir_prefix', 'reattach_tv_extras', 'move_tv_paths',
           'repath_tv_episodes_prefix', 'episode_version',
           'has_other_show_under_prefix',
           'record_organize_moves', 'list_organize_moves', 'list_organize_batches',
           'mark_organize_undone', 'delete_scan_state_paths', 'delete_scan_state_prefix',
           'pair_moved_tv_paths', 'backfill_legacy_organize_moves',
           'get_tv_facets', 'suggest_tv_shows', 'suggest_tv_people',
           'tv_status_bucket', 'TV_RATING_SOURCES', 'TV_RATING_STEPS',
           'TV_STATUS_CONTINUING', 'TV_STATUS_ENDED',
           'TV_META_FIELDS', 'EPISODE_META_FIELDS']

import json as _json

# 可写元数据列（TMDB 镜像/本地字段；file_path/library_id 不可经此改）
TV_META_FIELDS = {"title", "sort_title", "original_title", "year", "overview",
                  "overview_override", "tmdb_id", "imdb_id", "tvdb_id", "tmdb_rating",
                  "custom_rating", "poster_path", "backdrop_path", "genres", "genre_ids",
                  "tags", "person_names", "origin_country", "origin_countries",
                  "original_language", "region", "status", "first_air_date",
                  "last_air_date", "number_of_seasons", "number_of_episodes",
                  "episode_run_time", "networks", "created_by", "title_auto",
                  "needs_review", "match_source", "fetched_at", "watched", "watched_at",
                  "nfo_hash"}

EPISODE_META_FIELDS = {"title", "overview", "still_path", "air_date", "runtime",
                       "tmdb_rating", "tmdb_episode_id", "season", "episode",
                       "episode_end", "absolute_number", "watched", "watched_at",
                       "missing", "needs_review", "local_only", "nfo_hash"}

_LIST_COLS = {"genres", "genre_ids", "tags", "origin_countries", "networks", "created_by"}


def _meta_values(fields: dict, allowed: set) -> tuple[list[str], list]:
    cols, vals = [], []
    for k, v in (fields or {}).items():
        if k not in allowed:
            continue
        if k in _LIST_COLS and not isinstance(v, str):
            try:
                v = _json.dumps(v or [], ensure_ascii=False)
            except (TypeError, ValueError):
                v = "[]"
        cols.append(k)
        vals.append(v)
    return cols, vals


def update_show_meta(show_id: int, **fields) -> bool:
    """剧元数据写入（TMDB 镜像/待确认/匹配来源等；allowlist 过滤）。"""
    cols, vals = _meta_values(fields, TV_META_FIELDS)
    if not cols:
        return False
    sets = ", ".join(f"{c}=?" for c in cols) + ", updated_at=?"
    with _lock, _conn() as c:
        cur = c.execute(f"UPDATE tv_shows SET {sets} WHERE id=?",
                        (*vals, int(time.time()), int(show_id)))
        return int(cur.rowcount or 0) > 0


def update_episode_meta(episode_id: int, **fields) -> bool:
    """集元数据写入（刮削回填/编号重映射/已看；allowlist 过滤）。"""
    cols, vals = _meta_values(fields, EPISODE_META_FIELDS)
    if not cols:
        return False
    sets = ", ".join(f"{c}=?" for c in cols) + ", updated_at=?"
    with _lock, _conn() as c:
        cur = c.execute(f"UPDATE tv_episodes SET {sets} WHERE id=?",
                        (*vals, int(time.time()), int(episode_id)))
        return int(cur.rowcount or 0) > 0


def season_offsets(show_id: int) -> list[tuple[int, int]]:
    """绝对集号映射偏移：[(season, tmdb_episode_count)]（仅正季，按季号升序）。"""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT season, episode_count FROM tv_seasons WHERE show_id=? AND season>0"
            " ORDER BY season", (int(show_id),)).fetchall()
    return [(int(r["season"]), int(r["episode_count"] or 0)) for r in rows
            if int(r["episode_count"] or 0) > 0]


def remap_absolute_episodes(show_id: int) -> int:
    """按季集数偏移把绝对集号映射为 (season, episode)（幂等；无偏移不动）。"""
    from ..scanner.tv_parse import map_absolute
    offsets = season_offsets(show_id)
    if not offsets:
        return 0
    changed = 0
    with _lock, _conn() as c:
        rows = c.execute("SELECT id, season, episode, absolute_number FROM tv_episodes"
                         " WHERE show_id=? AND absolute_number IS NOT NULL",
                         (int(show_id),)).fetchall()
        now = int(time.time())
        for r in rows:
            m = map_absolute(int(r["absolute_number"]), offsets)
            if not m:
                continue
            season, episode = m
            if int(r["season"]) == season and int(r["episode"]) == episode:
                continue
            c.execute("UPDATE tv_episodes SET season=?, episode=?, updated_at=?"
                      " WHERE id=?", (season, episode, now, int(r["id"])))
            changed += 1
    return changed


def list_shows_for_scrape(library_ids=None, ids=None, force: bool = False) -> list[dict]:
    """待刮削剧：显式 ids 优先；否则库范围内 (tmdb_id IS NULL OR fetched_at=0)，
    force=True 时全量（跳过已刮削需要显式 force）。"""
    where, params = [], []
    if ids:
        idl = [int(x) for x in ids]
        where.append("id IN (%s)" % ",".join("?" * len(idl)))
        params.extend(idl)
    else:
        cond, cp = _lib_cond("tv_shows", library_ids)
        if cond:
            where.append(cond)
            params.extend(cp)
        if not force:
            where.append("(tmdb_id IS NULL OR COALESCE(fetched_at,0)=0)")
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM tv_shows" + wsql
                         + " ORDER BY sort_title, year IS NULL, year, id",
                         params).fetchall()
    return [_jsonify(dict(r)) for r in rows]


def set_show_match(show_id: int, tmdb_id: int, source: str = "manual",
                   needs_review: int = 0) -> bool:
    return update_show_meta(show_id, tmdb_id=int(tmdb_id), match_source=source,
                            needs_review=int(needs_review))


def mark_episode_watched(episode_id: int, watched: bool = True) -> bool:
    """标已看/未看；标已看同时清断点（不再出现在继续观看）。"""
    ep = get_episode(episode_id)
    if not ep:
        return False
    now = int(time.time())
    ok = update_episode_meta(episode_id, watched=1 if watched else 0,
                             watched_at=now if watched else 0)
    if watched:
        with _lock, _conn() as c:
            c.execute("DELETE FROM playback_progress WHERE kind='episode' AND item_id=?",
                      (int(episode_id),))
    return ok


def mark_show_watched(show_id: int, watched: bool = True) -> int:
    """整剧标已看/未看（返回受影响集数）；标已看清全部集断点。"""
    now = int(time.time())
    with _lock, _conn() as c:
        ids = [int(r["id"]) for r in c.execute(
            "SELECT id FROM tv_episodes WHERE show_id=?", (int(show_id),))]
        if not ids:
            return 0
        c.execute("UPDATE tv_episodes SET watched=?, watched_at=?, updated_at=?"
                  " WHERE show_id=?",
                  (1 if watched else 0, now if watched else 0, now, int(show_id)))
        if watched:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" for _ in chunk)
                c.execute(f"DELETE FROM playback_progress WHERE kind='episode'"
                          f" AND item_id IN ({ph})", chunk)
    return len(ids)


def has_other_show_under_prefix(prefix: str, library_id=None, show_id=None) -> bool:
    """该目录前缀下是否存在**其它剧**的集/花絮（剧根/包装层判定用）。"""
    p = str(prefix or "").strip("/")
    if not p or not show_id:
        return False
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    like = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
    sid = int(show_id)
    with _lock, _conn() as c:
        if c.execute("SELECT 1 FROM tv_episodes WHERE library_id=? AND show_id<>?"
                     " AND file_path LIKE ? ESCAPE '\\' LIMIT 1",
                     (lib_id, sid, like)).fetchone():
            return True
        if c.execute("SELECT 1 FROM extras WHERE library_id=? AND show_id IS NOT NULL"
                     " AND show_id<>? AND file_path LIKE ? ESCAPE '\\' LIMIT 1",
                     (lib_id, sid, like)).fetchone():
            return True
    return False


def episode_version(file_path) -> int:
    """集文件版本号：`剧名-V2-S01E01-…`（版本前缀）/ `…-V2`（旧后缀）；无标记=1。"""
    name = os.path.splitext(os.path.basename(
        str(file_path or "").replace("\\", "/")))[0]
    m = re.search(r"-V(\d+)(?:-|$)", name, re.I)
    try:
        v = int(m.group(1)) if m else 1
    except (TypeError, ValueError):
        v = 1
    return v if v >= 1 else 1


def episode_after(episode_id: int) -> dict | None:
    """按播出顺序的下一集（连播用；不跳已看）。

    多版本（V1/V2）**按版本隔离**：看完 V1E01 接 V1E02，不跳到另一版本的同集。"""
    ep = get_episode(episode_id)
    if not ep:
        return None
    rows = list_episodes(int(ep["show_id"]))
    ver = episode_version(ep.get("file_path"))
    for i, e in enumerate(rows):
        if int(e["id"]) == int(episode_id):
            for nxt in rows[i + 1:]:
                if episode_version(nxt.get("file_path")) == ver:
                    return nxt
            return None
    return None


def next_episode(show_id: int) -> dict | None:
    """下一集（Plex 式）：① 有未看完断点的最近一集 → ② 最后看完的下一集
    → ③ 第一条未看。全部看完返回 None。"""
    with _lock, _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT e.*, p.position AS _pos, p.duration AS _dur,"
            " p.updated_at AS _played FROM tv_episodes e"
            " LEFT JOIN playback_progress p ON p.kind='episode' AND p.item_id=e.id"
            " WHERE e.show_id=? ORDER BY e.season, e.episode, e.id",
            (int(show_id),))]
    if not rows:
        return None

    def _finished(e: dict) -> bool:
        if int(e.get("watched") or 0):
            return True
        dur = float(e.get("_dur") or 0)
        pos = float(e.get("_pos") or 0)
        return dur > 0 and (pos / dur >= 0.95 or dur - pos <= 300)

    partial = [e for e in rows if not _finished(e) and float(e.get("_pos") or 0) >= 15]
    if partial:
        return max(partial, key=lambda e: int(e.get("_played") or 0))
    last_finished = -1
    for i, e in enumerate(rows):
        if _finished(e):
            last_finished = i
    # 优先同版本（看完 V1E01 → V1E02，不跳 V2）；同版本没有才回退原逻辑
    if last_finished >= 0:
        anchor = rows[last_finished]
        anchor_ver = episode_version(anchor.get("file_path"))
        anchor_key = (int(anchor.get("season") or 0), int(anchor.get("episode") or 0))
        for e in rows[last_finished + 1:]:
            if not _finished(e) and episode_version(e.get("file_path")) == anchor_ver:
                return e
        for e in rows[last_finished + 1:]:
            if _finished(e):
                continue
            key = (int(e.get("season") or 0), int(e.get("episode") or 0))
            if key == anchor_key:
                continue      # 同一集的另一版本不算「下一集」
            return e
    for e in rows:
        if not _finished(e):
            return e
    return None


def upsert_show(library_id: int, title: str, year: int | None = None,
                sort_title: str = "", hints: dict | None = None) -> int:
    """按 (library_id, title, year) 幂等建剧；hints 可带 tmdb/tvdb/imdb 目录提示。
    已存在时只刷新 sort_title/updated_at（不覆盖标题/年份等元数据）。"""
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    title = " ".join(str(title or "").split())
    now = int(time.time())
    hints = hints or {}
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT id FROM tv_shows WHERE library_id=? AND title=?"
            " AND COALESCE(year,0)=COALESCE(?,0)", (lib_id, title, year)).fetchone()
        if row:
            c.execute("UPDATE tv_shows SET sort_title=?, updated_at=? WHERE id=?",
                      (sort_title or title, now, int(row["id"])))
            return int(row["id"])
        tmdb_id = None
        if hints.get("tmdb"):
            try:
                tmdb_id = int(hints["tmdb"])
            except (TypeError, ValueError):
                tmdb_id = None
        tvdb_id = None
        if hints.get("tvdb"):
            try:
                tvdb_id = int(hints["tvdb"])
            except (TypeError, ValueError):
                tvdb_id = None
        match_source = "hint" if (tmdb_id or tvdb_id or hints.get("imdb")) else ""
        cur = c.execute(
            "INSERT INTO tv_shows(library_id, title, sort_title, year, tmdb_id, tvdb_id,"
            " imdb_id, match_source, title_auto, added_at, updated_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (lib_id, title, sort_title or title, year, tmdb_id, tvdb_id,
             str(hints.get("imdb") or ""), match_source, now, now))
        return int(cur.lastrowid)


def upsert_season(show_id: int, library_id: int, season: int, name: str = "",
                  overview: str = "", air_date: str = "", poster_path: str = "",
                  episode_count: int = 0, tmdb_season_id: int | None = None) -> int:
    """季元数据幂等写（T2 刮削用；空值不覆盖已有非空值）。"""
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    now = int(time.time())
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM tv_seasons WHERE show_id=? AND season=?",
                        (int(show_id), int(season))).fetchone()
        if row:
            c.execute(
                "UPDATE tv_seasons SET name=CASE WHEN COALESCE(name,'')='' THEN ? ELSE name END,"
                " overview=CASE WHEN COALESCE(overview,'')='' THEN ? ELSE overview END,"
                " air_date=CASE WHEN COALESCE(air_date,'')='' THEN ? ELSE air_date END,"
                " poster_path=CASE WHEN COALESCE(poster_path,'')='' THEN ? ELSE poster_path END,"
                " episode_count=MAX(episode_count, ?), tmdb_season_id=COALESCE(?, tmdb_season_id),"
                " updated_at=? WHERE id=?",
                (name or "", overview or "", air_date or "", poster_path or "",
                 int(episode_count or 0), tmdb_season_id, now, int(row["id"])))
            return int(row["id"])
        cur = c.execute(
            "INSERT INTO tv_seasons(show_id, library_id, season, name, overview, air_date,"
            " poster_path, episode_count, tmdb_season_id, updated_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(show_id), lib_id, int(season), name or "", overview or "", air_date or "",
             poster_path or "", int(episode_count or 0), tmdb_season_id, now))
        return int(cur.lastrowid)


def upsert_episode(show_id: int, library_id: int, file_path: str,
                   season: int, episode: int, title: str = "",
                   episode_end: int = 0, absolute_number: int | None = None) -> int:
    """集幂等写（file_path 唯一）。重扫只改归属/季集/区间；已刮削元数据不被覆盖。"""
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    now = int(time.time())
    with _lock, _conn() as c:
        row = c.execute("SELECT id, title FROM tv_episodes"
                        " WHERE library_id=? AND file_path=?",
                        (lib_id, file_path)).fetchone()
        if row:
            c.execute(
                "UPDATE tv_episodes SET show_id=?, season=?, episode=?, episode_end=?,"
                " absolute_number=COALESCE(?, absolute_number),"
                " title=CASE WHEN COALESCE(title,'')='' THEN ? ELSE title END,"
                " missing=0, updated_at=? WHERE id=?",
                (int(show_id), int(season), int(episode), int(episode_end or 0),
                 absolute_number, title or "", now, int(row["id"])))
            return int(row["id"])
        cur = c.execute(
            "INSERT INTO tv_episodes(show_id, library_id, file_path, season, episode,"
            " episode_end, absolute_number, title, added_at, updated_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(show_id), lib_id, file_path, int(season), int(episode),
             int(episode_end or 0), absolute_number, title or "", now, now))
        return int(cur.lastrowid)


_SHOW_JSON_COLS = ("genres", "genre_ids", "tags", "origin_countries",
                   "networks", "created_by")


def _jsonify(d: dict) -> dict:
    """剧行 JSON 列 → Python 列表（NFO/前端直接消费；原始库列是字符串）。"""
    for k in _SHOW_JSON_COLS:
        if k not in d:
            continue
        v = d.get(k)
        if isinstance(v, str):
            try:
                parsed = _json.loads(v or "[]")
            except (TypeError, ValueError):
                parsed = []
            d[k] = parsed if isinstance(parsed, list) else []
    return d


def _show_row(r) -> dict:
    d = _jsonify(dict(r))
    d["episode_count"] = int(d.get("episode_count") or 0)
    d["season_count"] = int(d.get("season_count") or 0)
    d["watched_count"] = int(d.get("watched_count") or 0)
    return d


def _lib_cond(alias: str, library_ids) -> tuple[str, list]:
    """视频库范围条件片段：单个 id 或 id 列表（v18 媒体库聚合）；None=不过滤。"""
    if library_ids is None:
        return "", []
    ids = ([int(x) for x in library_ids]
           if isinstance(library_ids, (list, tuple, set)) else [int(library_ids)])
    if not ids:
        return "1=0", []
    ph = ",".join("?" for _ in ids)
    return f"{alias}.library_id IN ({ph})", ids


# ---- 剧集墙筛选/联想（对齐电影墙：facet 内 OR、跨维度 AND、tags 多选 AND） ----

# TMDB 剧集无豆瓣评分：只有 tmdb + 手工自评
TV_RATING_SOURCES = {"tmdb": "tmdb_rating", "custom": "custom_rating"}
TV_RATING_STEPS = (9, 8, 7, 6)

# 连载状态归一桶（Tmdb 原值杂，前端按桶筛选/展示）
TV_STATUS_CONTINUING = {"Continuing", "Returning Series", "In Production"}
TV_STATUS_ENDED = {"Ended", "Canceled", "Cancelled"}


def tv_status_bucket(status) -> str:
    """TMDB status 原值 → continuing|ended|other（空值归 other）。"""
    s = str(status or "").strip()
    if s in TV_STATUS_CONTINUING:
        return "continuing"
    if s in TV_STATUS_ENDED:
        return "ended"
    return "other"


def _tv_rating_col(source) -> str:
    return TV_RATING_SOURCES.get(str(source or "tmdb").lower(), "tmdb_rating")


def _tv_structured_where(alias: str, genres=None, regions=None,
                         countries=None, years=None, decades=None,
                         tags=None, min_rating=None, rating_source=None,
                         watched=None, status=None) -> tuple[str, tuple]:
    """剧集结构化过滤（语义与电影 `_structured_where` 一致，作用于 tv_shows）。

    `library_ids` 不在此处理（调用方经 `_lib_cond` 拼）。
    `watched=1` = 整剧已看完（有集且无未看集）；`watched=0` = 未看完。
    `status` 接受 continuing/ended/other 桶名（大小写不敏感）。
    无条件返回 ("1=1", ())。
    """
    from ..regions import REGION_UNKNOWN
    conds: list[str] = []
    params: list = []
    gs = _split_multi(genres)
    if gs:
        conds.append("(%s)" % " OR ".join(
            f"EXISTS (SELECT 1 FROM json_each({alias}.genres) je WHERE je.value=?)"
            for _ in gs))
        params.extend(gs)
    rs = _split_multi(regions)
    if rs:
        parts = []
        known = [r for r in rs if r != REGION_UNKNOWN]
        if known:
            parts.append(f"{alias}.region IN (%s)" % ",".join("?" * len(known)))
            params.extend(known)
        if REGION_UNKNOWN in rs:
            parts.append(f"({alias}.region IS NULL OR {alias}.region='')")
        conds.append("(%s)" % " OR ".join(parts))
    cs = [c.upper() for c in _split_multi(countries)]
    if cs:
        parts = []
        known = [c for c in cs if c not in (REGION_UNKNOWN, "")]
        if known:
            parts.append(f"{alias}.origin_country IN (%s)" % ",".join("?" * len(known)))
            params.extend(known)
            parts.append(
                "EXISTS (SELECT 1 FROM json_each(%s.origin_countries) je"
                " WHERE je.value IN (%s))"
                % (alias, ",".join("?" * len(known))))
            params.extend(known)
        if REGION_UNKNOWN in cs or "" in _split_multi(countries):
            parts.append(f"({alias}.origin_country IS NULL OR {alias}.origin_country='')")
        conds.append("(%s)" % " OR ".join(parts))
    ys = _split_ints(years)
    if ys:
        conds.append(f"{alias}.year IN (%s)" % ",".join("?" * len(ys)))
        params.extend(ys)
    ds = _split_ints(decades)
    if ds:
        parts = []
        for d in ds:
            parts.append(f"({alias}.year>=? AND {alias}.year<=?)")
            params.extend([d, d + 9])
        conds.append("(%s)" % " OR ".join(parts))
    for t in _split_multi(tags):  # 标签多选为 AND（逐个收窄）
        conds.append(
            f"EXISTS (SELECT 1 FROM json_each({alias}.tags) je WHERE je.value=?)")
        params.append(t)
    if min_rating is not None:
        try:
            conds.append(f"({alias}.{_tv_rating_col(rating_source)}>=?)")
            params.append(float(min_rating))
        except (TypeError, ValueError):
            pass
    if watched is not None:
        try:
            w = int(watched)
        except (TypeError, ValueError):
            w = None
        if w == 1:
            conds.append(
                f"(EXISTS (SELECT 1 FROM tv_episodes e WHERE e.show_id={alias}.id)"
                f" AND NOT EXISTS (SELECT 1 FROM tv_episodes e WHERE e.show_id={alias}.id"
                " AND COALESCE(e.watched,0)=0))")
        elif w == 0:
            conds.append(
                f"((NOT EXISTS (SELECT 1 FROM tv_episodes e WHERE e.show_id={alias}.id))"
                f" OR EXISTS (SELECT 1 FROM tv_episodes e WHERE e.show_id={alias}.id"
                " AND COALESCE(e.watched,0)=0))")
    sts = {str(s or "").strip().lower() for s in _split_multi(status)}
    sts.discard("")
    if sts:
        parts = []
        if "continuing" in sts:
            ph = ",".join("?" * len(TV_STATUS_CONTINUING))
            parts.append(f"{alias}.status IN ({ph})")
            params.extend(sorted(TV_STATUS_CONTINUING))
        if "ended" in sts:
            ph = ",".join("?" * len(TV_STATUS_ENDED))
            parts.append(f"{alias}.status IN ({ph})")
            params.extend(sorted(TV_STATUS_ENDED))
        if "other" in sts:
            all_known = sorted(TV_STATUS_CONTINUING | TV_STATUS_ENDED)
            ph = ",".join("?" * len(all_known))
            parts.append(f"({alias}.status IS NULL OR {alias}.status=''"
                         f" OR {alias}.status NOT IN ({ph}))")
            params.extend(all_known)
        if parts:
            conds.append("(%s)" % " OR ".join(parts))
    if not conds:
        return "1=1", ()
    return " AND ".join(f"({x})" for x in conds), tuple(params)


_TV_SORT_COLS = {"added": "s.added_at", "updated": "s.updated_at",
                 "year": "s.year", "title": "s.sort_title", "rating": None}


def _tv_order_clause(sort, order, rating_source) -> str:
    """剧集墙 ORDER BY：sort=None 保持历史默认（sort_title）；否则白名单排序，
    NULL/缺失沉底 + id 兜底（翻页稳定）。"""
    if sort is None and order is None:
        return "ORDER BY s.sort_title, s.year IS NULL, s.year, s.id"
    key, direction = normalize_sort(sort, order)
    col = _TV_SORT_COLS[key]
    if col is None:
        col = f"s.{_tv_rating_col(rating_source)}"
    return f"ORDER BY ({col} IS NULL), {col} {direction}, s.id DESC"


def get_tv_facets(library_ids=None) -> dict:
    """剧集动态分类计数（全库口径，不随筛选变化，只返 count>0 项）。

    `watched` 按整剧口径：有集且无未看集 = 已看完，其余 = 未看完。
    `status` 按 `tv_status_bucket` 三桶计数。`ratings` 只有 tmdb/custom。
    """
    from collections import Counter
    from ..regions import REGION_ORDER, REGION_UNKNOWN, country_name
    cond, params = _lib_cond("s", library_ids)
    where = (" WHERE " + cond) if cond else ""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT s.genres, s.tags, s.region, s.origin_country,"
            " s.origin_countries, s.year, s.tmdb_rating, s.custom_rating,"
            " s.status, COUNT(e.id) AS _eps, COALESCE(SUM(e.watched),0) AS _ew"
            " FROM tv_shows s LEFT JOIN tv_episodes e ON e.show_id=s.id"
            + where + " GROUP BY s.id", params).fetchall()
    gc, rc, yc, dc, tc, ic, cc, sc, stc = (Counter() for _ in range(9))
    wc = Counter()
    for r in rows:
        d = _jsonify({"genres": r["genres"], "tags": r["tags"],
                      "origin_countries": r["origin_countries"]})
        eps = int(r["_eps"] or 0)
        ew = int(r["_ew"] or 0)
        wc[1 if (eps > 0 and ew == eps) else 0] += 1
        for g in d.get("genres") or []:
            gc[g] += 1
        reg = r["region"] or REGION_UNKNOWN
        rc[reg] += 1
        codes = d.get("origin_countries") or []
        primary = r["origin_country"] or (codes[0] if codes else "")
        cc[primary or REGION_UNKNOWN] += 1
        involved = set(codes) | ({primary} if primary else set())
        for code in involved or {REGION_UNKNOWN}:
            ic[code] += 1
        y = r["year"]
        if isinstance(y, int):
            yc[y] += 1
            dc[(y // 10) * 10] += 1
        for src in TV_RATING_SOURCES:
            v = r["tmdb_rating"] if src == "tmdb" else r["custom_rating"]
            if isinstance(v, (int, float)) and v > 0:
                for step in TV_RATING_STEPS:
                    if v >= step:
                        sc[(src, step)] += 1
        for t in d.get("tags") or []:
            tc[t] += 1
        stc[tv_status_bucket(r["status"])] += 1
    order = {v: i for i, v in enumerate(REGION_ORDER)}
    return {
        "genres": [{"value": k, "count": v} for k, v in gc.most_common()],
        "regions": sorted(({"value": k, "count": v} for k, v in rc.items()),
                          key=lambda x: (order.get(x["value"], 99), -x["count"])),
        "countries": [{"code": ("" if k == REGION_UNKNOWN else k),
                       "name": (REGION_UNKNOWN if k == REGION_UNKNOWN else country_name(k)),
                       "count": v} for k, v in ic.most_common()],
        "primary_countries": [{"code": ("" if k == REGION_UNKNOWN else k),
                       "name": (REGION_UNKNOWN if k == REGION_UNKNOWN else country_name(k)),
                       "count": v} for k, v in cc.most_common()],
        "years": [{"value": k, "count": v} for k, v in sorted(yc.items(), reverse=True)],
        "decades": [{"value": k, "count": v} for k, v in sorted(dc.items(), reverse=True)],
        "tags": [{"value": k, "count": v} for k, v in tc.most_common()],
        "watched": {"watched": wc.get(1, 0), "unwatched": wc.get(0, 0)},
        "status": [{"value": k, "count": v}
                   for k, v in sorted(stc.items(), key=lambda kv: -kv[1])],
        "ratings": {src: [{"min": s, "count": sc.get((src, s), 0)} for s in TV_RATING_STEPS]
                    for src in TV_RATING_SOURCES},
    }


def suggest_tv_shows(q: str, limit: int = 8, library_ids=None) -> list[dict]:
    """剧集搜索联想：本地剧名/原名子串匹配，前缀命中优先、年份降序。

    返回 [{id, tmdb_id, title, original_title, year}]。"""
    toks = _query_terms(q)
    if not toks:
        return []
    limit = max(1, min(int(limit or 8), 20))
    conds, params = [], []
    for t in toks:
        p = f"%{_like_esc(t)}%"
        conds.append("(s.title LIKE ? ESCAPE '\\' OR s.original_title LIKE ? ESCAPE '\\')")
        params.extend([p, p])
    cond, cparams = _lib_cond("s", library_ids)
    if cond:
        conds.append(cond)
        params.extend(cparams)
    where = " AND ".join(f"({x})" for x in conds)
    prefix = f"{_like_esc(toks[0])}%"
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT s.id, s.tmdb_id, s.title, s.original_title, s.year,"
            " MIN(CASE WHEN s.title LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END) AS _pref"
            " FROM tv_shows s WHERE " + where +
            " GROUP BY s.id ORDER BY _pref, s.year DESC, s.id LIMIT ?",
            (prefix, *params, limit)).fetchall()
        return [{"id": r["id"], "tmdb_id": r["tmdb_id"], "title": r["title"],
                 "original_title": r["original_title"], "year": r["year"]}
                for r in rows]


def suggest_tv_people(q: str, limit: int = 5, library_ids=None) -> list[dict]:
    """剧集演员联想：剧集无 movie_person 式 join 表（演职员只存
    `tv_shows.person_names` 反范式串），按库内剧名下人名聚合计数。

    返回 [{name, count}]（无 tmdb_id；前端点击回填人名搜索，不跳人物页）。"""
    toks = _query_terms(q)
    if not toks:
        return []
    limit = max(1, min(int(limit or 5), 20))
    conds, params = [], []
    for t in toks:
        p = f"%{_like_esc(t)}%"
        conds.append("s.person_names LIKE ? ESCAPE '\\'")
        params.append(p)
    cond, cparams = _lib_cond("s", library_ids)
    if cond:
        conds.append(cond)
        params.extend(cparams)
    where = " AND ".join(f"({x})" for x in conds)
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT s.person_names FROM tv_shows s WHERE " + where
            + " LIMIT 500", params).fetchall()
    from collections import Counter
    cc: Counter = Counter()
    lowers = [t.casefold() for t in toks]
    for r in rows:
        for name in str(r["person_names"] or "").split(","):
            nm = " ".join(name.split())
            if not nm:
                continue
            fold = nm.casefold()
            if all(t in fold for t in lowers):
                cc[nm] += 1
    return [{"name": n, "count": c} for n, c in
            sorted(cc.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def list_shows(library_ids=None, q: str = "", limit: int = 500,
               offset: int = 0, *, genres=None, regions=None,
               countries=None, years=None, decades=None, tags=None,
               min_rating=None, rating_source=None, watched=None,
               status=None, sort=None, order=None) -> list[dict]:
    """剧集列表（海报墙）：库范围 + 本地搜索 + 结构化筛选 + 排序。

    过滤语义与电影墙一致：facet 内 OR、跨维度 AND、tags 多选 AND、
    min_rating 单阈值（>=）；`status` 为连载状态桶
   （continuing/ended/other，见 `tv_status_bucket`）；`watched=1` 为整剧
    已看完（有集且无未看集），`watched=0` 为未看完。
    `sort=None` 时保持历史默认（sort_title）排序；传 sort 后走白名单排序。
    """
    where, params = [], []
    cond, cparams = _lib_cond("s", library_ids)
    if cond:
        where.append(cond)
        params.extend(cparams)
    fwhere, fparams = _tv_structured_where(
        "s", genres=genres, regions=regions, countries=countries,
        years=years, decades=decades, tags=tags, min_rating=min_rating,
        rating_source=rating_source, watched=watched, status=status)
    if fwhere != "1=1":
        where.append(fwhere)
        params.extend(fparams)
    if (q or "").strip():
        toks = _query_terms(q)
        if toks:
            # 词级 AND：标题/原名/演职员（person_names 含前 10 演员 + 创作者）
            for t in toks:
                p = f"%{_like_esc(t)}%"
                where.append("(s.title LIKE ? ESCAPE '\\'"
                             " OR s.original_title LIKE ? ESCAPE '\\'"
                             " OR s.person_names LIKE ? ESCAPE '\\')")
                params.extend([p, p, p])
        else:
            term = f"%{_like_esc(q.strip())}%"
            where.append("(s.title LIKE ? ESCAPE '\\'"
                         " OR s.original_title LIKE ? ESCAPE '\\'"
                         " OR s.person_names LIKE ? ESCAPE '\\')")
            params.extend([term, term, term])
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    limit = max(1, min(int(limit or 500), 2000))
    offset = max(0, int(offset or 0))
    order_sql = _tv_order_clause(sort, order, rating_source)
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT s.*, COUNT(e.id) AS episode_count,"
            " COUNT(DISTINCT e.season) AS season_count,"
            " COALESCE(SUM(e.watched), 0) AS watched_count"
            " FROM tv_shows s LEFT JOIN tv_episodes e ON e.show_id=s.id"
            + wsql +
            " GROUP BY s.id " + order_sql +
            " LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        return [_show_row(r) for r in rows]


def count_shows(library_ids=None, q: str = "", *, genres=None,
                regions=None, countries=None, years=None, decades=None,
                tags=None, min_rating=None, rating_source=None,
                watched=None, status=None) -> int:
    """按同样过滤口径计剧数（分页 total 用；无过滤时退化为旧行为）。"""
    where, params = [], []
    cond, cparams = _lib_cond("tv_shows", library_ids)
    if cond:
        where.append(cond)
        params.extend(cparams)
    fwhere, fparams = _tv_structured_where(
        "tv_shows", genres=genres, regions=regions, countries=countries,
        years=years, decades=decades, tags=tags, min_rating=min_rating,
        rating_source=rating_source, watched=watched, status=status)
    if fwhere != "1=1":
        where.append(fwhere)
        params.extend(fparams)
    if (q or "").strip():
        toks = _query_terms(q)
        if toks:
            for t in toks:
                p = f"%{_like_esc(t)}%"
                where.append("(tv_shows.title LIKE ? ESCAPE '\\'"
                             " OR tv_shows.original_title LIKE ? ESCAPE '\\'"
                             " OR tv_shows.person_names LIKE ? ESCAPE '\\')")
                params.extend([p, p, p])
        else:
            term = f"%{_like_esc(q.strip())}%"
            where.append("(tv_shows.title LIKE ? ESCAPE '\\'"
                         " OR tv_shows.original_title LIKE ? ESCAPE '\\'"
                         " OR tv_shows.person_names LIKE ? ESCAPE '\\')")
            params.extend([term, term, term])
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    with _lock, _conn() as c:
        return int(c.execute("SELECT COUNT(*) FROM tv_shows" + wsql,
                             params).fetchone()[0])


def count_episodes(library_ids=None) -> int:
    cond, params = _lib_cond("tv_episodes", library_ids)
    where = (" WHERE " + cond) if cond else ""
    with _lock, _conn() as c:
        return int(c.execute("SELECT COUNT(*) FROM tv_episodes" + where,
                             params).fetchone()[0])


def get_show(show_id: int) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM tv_shows WHERE id=?", (int(show_id),)).fetchone()
        if not row:
            return None
        d = _jsonify(dict(row))
        eps = c.execute(
            "SELECT * FROM tv_episodes WHERE show_id=?"
            " ORDER BY season, episode, file_path", (int(show_id),)).fetchall()
        d["episodes"] = [dict(r) for r in eps]
        d["episode_count"] = len(d["episodes"])
        counts: dict[int, int] = {}
        for e in d["episodes"]:
            s = int(e.get("season") or 0)
            counts[s] = counts.get(s, 0) + 1
        meta: dict[int, dict] = {}
        for r in c.execute("SELECT * FROM tv_seasons WHERE show_id=?", (int(show_id),)):
            meta[int(r["season"])] = dict(r)
        seasons = []
        for s in sorted(set(counts) | set(meta)):
            m = meta.get(s, {})
            seasons.append({
                "season": s,
                "episode_count": counts.get(s, int(m.get("episode_count") or 0)),
                "name": m.get("name") or "",
                "overview": m.get("overview") or "",
                "air_date": m.get("air_date") or "",
                "poster_path": m.get("poster_path") or "",
            })
        d["seasons"] = seasons
        d["season_count"] = len(seasons)
        return d


def list_seasons(show_id: int) -> list[dict]:
    """季元数据行（轻量，不含集）。"""
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM tv_seasons WHERE show_id=? ORDER BY season",
                         (int(show_id),)).fetchall()
    return [dict(r) for r in rows]


def list_episode_versions(show_id: int, season: int, episode: int) -> list[dict]:
    """同剧同季同集的全部文件（多版本/多发布）。"""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT * FROM tv_episodes WHERE show_id=? AND season=? AND episode=?"
            " ORDER BY file_path", (int(show_id), int(season), int(episode))).fetchall()
    return [dict(r) for r in rows]


def get_show_meta(show_id: int) -> dict | None:
    """仅剧行（不带集），供刮削/刷新等轻量路径用。"""
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM tv_shows WHERE id=?", (int(show_id),)).fetchone()
        return _jsonify(dict(row)) if row else None


def episode_progress_map(show_id: int) -> dict[int, dict]:
    """整剧集断点映射 {episode_id: progress}（一次查询，避免逐集 N 次）。"""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT p.item_id, p.position, p.duration, p.updated_at"
            " FROM playback_progress p JOIN tv_episodes e ON e.id=p.item_id"
            " WHERE p.kind='episode' AND e.show_id=?", (int(show_id),)).fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        pos, dur = float(r["position"] or 0), float(r["duration"] or 0)
        out[int(r["item_id"])] = {
            "position": round(pos, 3), "duration": round(dur, 3),
            "percent": round(min(1.0, pos / dur), 4) if dur > 0 else 0.0,
            "remaining_sec": max(0, int(dur - pos)) if dur > 0 else 0,
            "last_played_at": int(r["updated_at"] or 0),
        }
    return out


def list_episodes(show_id: int) -> list[dict]:
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM tv_episodes WHERE show_id=?"
            " ORDER BY season, episode, file_path", (int(show_id),)).fetchall()]


def _move_scan_state(c, lib_id: int, old: str, new: str) -> None:
    """跟随路径搬移增量状态（否则下次扫描把已移动文件当新文件重解析）。"""
    try:
        c.execute("UPDATE OR IGNORE scan_state SET file_path=?"
                  " WHERE library_id=? AND file_path=?", (new, lib_id, old))
        c.execute("DELETE FROM scan_state WHERE library_id=? AND file_path=?",
                  (lib_id, old))
    except Exception as e:
        logger.debug("scan_state move failed %s -> %s: %s", old, new, e)


def move_tv_paths(library_id, mapping: dict) -> int:
    """整理工具：批量把集/花絮行路径 old→new（一次事务；id 不变，断点/已看不丢）。

    链式/置换改名（A→B、B→C）下逐条 UPDATE 会互相撞 `(library_id, file_path)`
    UNIQUE → 两阶段更新：先全部改到按行 id 唯一的临时值，再落到终态。"""
    if not mapping:
        return 0
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    now = int(time.time())
    n = 0
    with _lock, _conn() as c:
        pairs: list[tuple[str, int, str, str]] = []      # (table, id, old, new)
        for old, new in mapping.items():
            for t in ("tv_episodes", "extras"):
                for r in c.execute(
                        f"SELECT id FROM {t} WHERE library_id=? AND file_path=?",
                        (lib_id, old)):
                    pairs.append((t, int(r["id"]), old, new))
        for t, rid, _old, _new in pairs:
            c.execute(f"UPDATE {t} SET file_path=? WHERE id=?", (f"__move__{t}:{rid}__", rid))
        for t, rid, _old, new in pairs:
            cur = c.execute(f"UPDATE {t} SET file_path=?, updated_at=? WHERE id=?",
                            (new, now, rid))
            n += int(cur.rowcount or 0)
        for old, new in mapping.items():
            _move_scan_state(c, lib_id, old, new)
    return n


def repath_tv_episodes_prefix(library_id, old_dir: str, new_dir: str) -> int:
    """目录整体改名后批量改集行路径（前缀替换；原路径审计字段 TV 侧无）。"""
    old = os.path.normpath((old_dir or "").strip().strip("/"))
    new = os.path.normpath((new_dir or "").strip().strip("/"))
    if old in ("", ".") or old == new:
        return 0
    like = old.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
    lib_id = int(library_id)
    with _lock, _conn() as c:
        rows = [str(r["file_path"]) for r in c.execute(
            "SELECT file_path FROM tv_episodes WHERE library_id=? AND file_path LIKE ?"
            " ESCAPE '\\'", (lib_id, like))]
        cur = c.execute(
            "UPDATE tv_episodes SET file_path = ? || substr(file_path, ?), updated_at=?"
            " WHERE library_id=? AND file_path LIKE ? ESCAPE '\\'",
            (new, len(old) + 1, int(time.time()), lib_id, like))
        for p in rows:
            _move_scan_state(c, lib_id, p, new + p[len(old):])
        return int(cur.rowcount or 0)


# ---- 整理审计（v24）：每次目录整理逐条留痕，支撑「整理历史 / 撤销整理」 ----
# 约定：`from_path → to_path` 就是文件/目录实际移动的方向；撤销 = 反向搬回。

def record_organize_moves(batch_id: str, moves, library_id=None) -> int:
    """写入一批整理审计（moves: [{show_id,kind,action,from_path,to_path,library_id}]）。"""
    rows = list(moves or [])
    if not rows:
        return 0
    now = int(time.time())
    n = 0
    with _lock, _conn() as c:
        for m in rows:
            c.execute(
                "INSERT INTO organize_moves (library_id, show_id, batch_id, kind,"
                " obj, action, from_path, to_path, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (int(m.get("library_id") or library_id or DEFAULT_LIBRARY_ID),
                 int(m.get("show_id") or 0), str(batch_id),
                 str(m.get("kind") or "file"), str(m.get("obj") or "file"),
                 str(m.get("action") or ""),
                 str(m["from_path"]), str(m["to_path"]), now))
            n += 1
    return n


def list_organize_moves(batch_id=None, library_id=None, include_undone=False,
                        limit: int = 5000) -> list[dict]:
    q = "SELECT * FROM organize_moves WHERE 1=1"
    args: list = []
    if batch_id:
        q += " AND batch_id=?"
        args.append(str(batch_id))
    if library_id is not None and int(library_id or 0) > 0:
        q += " AND library_id=?"
        args.append(int(library_id))
    if not include_undone:
        q += " AND COALESCE(undone_at,0)=0"
    q += " ORDER BY id LIMIT ?"
    args.append(max(1, min(int(limit), 20000)))
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def list_organize_batches(limit: int = 50) -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT batch_id, COUNT(*) AS total,"
            " SUM(CASE WHEN COALESCE(undone_at,0)>0 THEN 1 ELSE 0 END) AS undone,"
            " COUNT(DISTINCT library_id) AS libraries,"
            " MIN(created_at) AS created_at, MAX(created_at) AS updated_at"
            " FROM organize_moves GROUP BY batch_id"
            " ORDER BY updated_at DESC, batch_id DESC LIMIT ?",
            (max(1, min(int(limit), 200)),)).fetchall()
    return [dict(r) for r in rows]


def mark_organize_undone(move_ids, undone: bool = True) -> int:
    ids = [int(x) for x in (move_ids or [])]
    if not ids:
        return 0
    now = int(time.time()) if undone else 0
    n = 0
    with _lock, _conn() as c:
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            ph = ",".join("?" for _ in chunk)
            cur = c.execute(f"UPDATE organize_moves SET undone_at=? WHERE id IN ({ph})",
                            [now] + chunk)
            n += int(cur.rowcount or 0)
    return n


def delete_scan_state_prefix(library_id, prefix: str) -> int:
    """删掉某目录前缀下的增量状态（目录级还原后清扁平残留）。"""
    p = str(prefix or "").strip("/")
    if not p:
        return 0
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    like = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
    with _lock, _conn() as c:
        cur = c.execute(
            "DELETE FROM scan_state WHERE library_id=? AND"
            " (file_path=? OR file_path LIKE ? ESCAPE '\\')", (lib_id, p, like))
        return int(cur.rowcount or 0)


def delete_scan_state_paths(library_id, paths) -> int:
    """还原后清掉扁平路径的增量状态（避免下次扫描把它们当新文件重解析）。"""
    items = [str(p) for p in (paths or []) if p]
    if not items:
        return 0
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    n = 0
    with _lock, _conn() as c:
        for i in range(0, len(items), 400):
            chunk = items[i:i + 400]
            ph = ",".join("?" for _ in chunk)
            cur = c.execute(
                f"DELETE FROM scan_state WHERE library_id=? AND file_path IN ({ph})",
                [lib_id] + chunk)
            n += int(cur.rowcount or 0)
    return n


def pair_moved_tv_paths(library_id=None) -> list[dict]:
    """从 scan_state 重建「已移动文件」的旧→新配对（v24 之前整理未留痕的兜底）。

    只读：scan_state 保留了移动前的旧路径（size/mtime），与当前 `tv_episodes/extras`
    行按 `(basename, size, mtime)` 唯一配对；歧义/无法配对的不返回（日志提示）。
    """
    lib_id = int(library_id or 0)
    with _lock, _conn() as c:
        current: dict[str, tuple] = {}
        for r in c.execute("SELECT file_path, library_id, show_id FROM tv_episodes"):
            current[str(r["file_path"])] = ("episode", int(r["library_id"] or 0),
                                            int(r["show_id"] or 0))
        for r in c.execute("SELECT file_path, library_id, show_id FROM extras"
                           " WHERE show_id IS NOT NULL"):
            current.setdefault(str(r["file_path"]), ("extra", int(r["library_id"] or 0),
                                                     int(r["show_id"] or 0)))
        q = ("SELECT file_path, size, mtime, library_id, updated_at FROM scan_state"
             " WHERE status IN ('tv_ok','extra_tv')")
        args: list = []
        if lib_id > 0:
            q += " AND library_id=?"
            args.append(lib_id)
        old = [dict(r) for r in c.execute(q, args)]
    by_key: dict[tuple, list[dict]] = {}
    for r in old:
        by_key.setdefault(
            (os.path.basename(str(r["file_path"])).casefold(), r["size"], r["mtime"]),
            []).append(r)
    moves, skipped = [], 0
    for r in old:
        old_path = str(r["file_path"])
        if old_path in current:
            continue
        key = (os.path.basename(old_path).casefold(), r["size"], r["mtime"])
        cands = [c for c in by_key.get(key, [])
                 if str(c["file_path"]) != old_path and str(c["file_path"]) in current]
        if len(cands) != 1:
            skipped += 1
            continue
        new_path = str(cands[0]["file_path"])
        kind, _lib, show_id = current[new_path]
        moves.append({"library_id": int(r["library_id"] or DEFAULT_LIBRARY_ID),
                      "show_id": show_id,
                      "kind": kind, "obj": "file", "action": "legacy",
                      "from_path": old_path, "to_path": new_path})
    if skipped:
        logger.warning("pair_moved_tv_paths: %d 条旧路径无法唯一配对（跳过，不猜测）",
                       skipped)
    return moves


def backfill_legacy_organize_moves() -> int:
    """一次性回填：v24 之前整理未留审计，从 scan_state 配对补齐 legacy 批次。

    只有审计表里还没有任何 `legacy-*` 批次时才执行（之后启动直接跳过）。"""
    with _lock, _conn() as c:
        row = c.execute("SELECT COUNT(*) FROM organize_moves"
                        " WHERE batch_id LIKE 'legacy-%'").fetchone()
        if row and int(row[0] or 0) > 0:
            return 0
        stamp = c.execute("SELECT MAX(updated_at) FROM scan_state").fetchone()
    moves = pair_moved_tv_paths()
    if not moves:
        return 0
    ts = int(stamp[0] or 0) if stamp else 0
    day = time.strftime("%Y%m%d", time.localtime(ts or time.time()))
    return record_organize_moves(f"legacy-{day}", moves)


def find_show_by_dir_prefix(show_dir: str, library_id=None) -> int | None:
    """按剧根目录找已拥有正片的剧行（花絮归属优先用它，避免 TMDB 改名后按目录名
    新建重复剧行——如 `Breaking.Bad.2008` 下花絮挂到 `绝命毒师` 而非新行）。"""
    d = str(show_dir or "").replace("\\", "/").strip("/")
    if not d:
        return None
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    like = d.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT show_id, COUNT(*) AS n FROM tv_episodes"
            " WHERE library_id=? AND file_path LIKE ? ESCAPE '\\'"
            " GROUP BY show_id ORDER BY n DESC, show_id LIMIT 1",
            (lib_id, like)).fetchone()
        return int(row["show_id"]) if row else None


def reattach_tv_extras(library_id=None) -> int:
    """按剧根前缀重挂花絮行：修复「花絮先扫→按目录名建行→正片已被 TMDB 改名」的
    重复剧行（幂等，返回改挂条数）。"""
    from ..scanner.tv_nfo_link import show_dir_of
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    with _lock, _conn() as c:
        eps = c.execute("SELECT show_id, file_path FROM tv_episodes WHERE library_id=?",
                        (lib_id,)).fetchall()
        if not eps:
            return 0
        direct = {os.path.dirname(str(r["file_path"])) for r in eps}
        roots: dict[str, dict] = {}
        for r in eps:
            root = show_dir_of(str(r["file_path"]), direct)
            if not root:
                continue
            bucket = roots.setdefault(root, {})
            sid = int(r["show_id"])
            bucket[sid] = bucket.get(sid, 0) + 1
        target = {root: max(b.items(), key=lambda kv: (kv[1], -kv[0]))[0]
                  for root, b in roots.items()}
        if not target:
            return 0
        fixed = 0
        rows = c.execute("SELECT id, file_path, show_id FROM extras"
                         " WHERE library_id=? AND show_id IS NOT NULL",
                         (lib_id,)).fetchall()
        for x in rows:
            path = str(x["file_path"])
            best = ""
            for root in target:
                if path.startswith(root + "/") and len(root) > len(best):
                    best = root
            want = target.get(best)
            if want and int(x["show_id"]) != int(want):
                c.execute("UPDATE extras SET show_id=?, updated_at=? WHERE id=?",
                          (int(want), int(time.time()), int(x["id"])))
                fixed += 1
        return fixed


def get_episode_by_path(file_path: str, library_id=None) -> dict | None:
    """按库内相对路径取集行（fs 浏览/复制等路径入口用）。"""
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM tv_episodes WHERE library_id=? AND file_path=?",
                        (lib_id, file_path)).fetchone()
        return dict(row) if row else None


def get_episode(episode_id: int) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM tv_episodes WHERE id=?",
                        (int(episode_id),)).fetchone()
        return dict(row) if row else None


def delete_episode_by_path(file_path: str, library_id=None) -> bool:
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    with _lock, _conn() as c:
        row = c.execute("SELECT id FROM tv_episodes WHERE library_id=? AND file_path=?",
                        (lib_id, file_path)).fetchone()
        if not row:
            return False
        _cascade_episode_ids(c, [int(row["id"])])
        return True


def _cascade_episode_ids(c, ids: list[int]) -> None:
    """删集并清理其播放断点/媒体探测缓存（分块防 SQLite 变量上限）。"""
    for i in range(0, len(ids), 400):
        chunk = [int(x) for x in ids[i:i + 400]]
        ph = ",".join("?" for _ in chunk)
        c.execute(f"DELETE FROM playback_progress WHERE kind='episode'"
                  f" AND item_id IN ({ph})", chunk)
        c.execute(f"DELETE FROM media_info WHERE kind='episode'"
                  f" AND item_id IN ({ph})", chunk)
        c.execute(f"DELETE FROM tv_episodes WHERE id IN ({ph})", chunk)


def delete_episodes_not_in(keep_paths, library_id=None) -> int:
    """失效集 GC：删除库内不在 keep_paths（本次完整遍历快照）中的集行。
    仅在一次成功的完整遍历后调用（调用方保证）；级联清理断点/探测缓存。"""
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    keep = set(keep_paths or ())
    with _lock, _conn() as c:
        rows = c.execute("SELECT id, file_path FROM tv_episodes WHERE library_id=?",
                         (lib_id,)).fetchall()
        gone = [int(r["id"]) for r in rows if r["file_path"] not in keep]
        if gone:
            _cascade_episode_ids(c, gone)
        return len(gone)


def prune_empty_shows(library_id=None) -> int:
    """删除既无集行也无花絮行的剧（含其季元数据行）。返回删除剧数。"""
    lib_id = int(library_id or DEFAULT_LIBRARY_ID)
    with _lock, _conn() as c:
        ids = [int(r["id"]) for r in c.execute(
            "SELECT id FROM tv_shows WHERE library_id=? AND id NOT IN"
            " (SELECT DISTINCT show_id FROM tv_episodes)"
            " AND id NOT IN (SELECT DISTINCT show_id FROM extras"
            "                WHERE show_id IS NOT NULL)", (lib_id,))]
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            ph = ",".join("?" for _ in chunk)
            c.execute(f"DELETE FROM tv_seasons WHERE show_id IN ({ph})", chunk)
            c.execute(f"DELETE FROM tv_shows WHERE id IN ({ph})", chunk)
        return len(ids)
