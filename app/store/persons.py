"""store.persons（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。"""
import json as _json
import time
from ._base import _attach_media_libraries, _conn, _lock, _row_to_dict
__all__ = ['upsert_person', 'get_person_raw', 'person_exists', 'persons_missing_avatar', 'update_person_bio', 'get_person', 'get_person_tv_works', 'link_person', 'clear_movie_persons', 'get_movie_person_links', 'copy_person_links', 'find_sibling_with_persons']

def upsert_person(tmdb_id: int, name: str, avatar: str | None = None,
                  profile_tmdb_path: str | None = None,
                  fetched_at: int | None = None) -> int:
    """人物镜像 upsert（以 tmdb_id 为键）。
    avatar 非 None 时更新（含 '-' 标记“确认无照片”，避免回填反复重试）；
    profile_tmdb_path 非 None 时更新（远端原图路径，用于感知远端换头像）；
    fetched_at 非 None 时更新（credits 来源时间）。"""
    now = int(time.time())
    with _lock, _conn() as c:
        c.execute("INSERT OR IGNORE INTO persons(tmdb_id, name) VALUES(?, ?)", (tmdb_id, name))
        c.execute("UPDATE persons SET name=? WHERE tmdb_id=?", (name, tmdb_id))
        if avatar is not None:
            c.execute("UPDATE persons SET avatar=? WHERE tmdb_id=?", (avatar, tmdb_id))
        if profile_tmdb_path is not None:
            c.execute("UPDATE persons SET profile_tmdb_path=? WHERE tmdb_id=?",
                      (profile_tmdb_path or "", tmdb_id))
        if fetched_at is not None:
            c.execute("UPDATE persons SET fetched_at=? WHERE tmdb_id=?",
                      (int(fetched_at) if fetched_at else now, tmdb_id))
        row = c.execute("SELECT id FROM persons WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        return int(row["id"])


def get_person_raw(tmdb_id: int) -> dict | None:
    """人物镜像原始行（含 fetched_at/bio_fetched_at 水位，不含作品列表）。"""
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM persons WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        return dict(row) if row else None


def person_exists(tmdb_id: int) -> bool:
    """人物行是否存在（评审 B8/R07-B1：GET/refresh 统一口径）。"""
    with _lock, _conn() as c:
        return c.execute("SELECT 1 FROM persons WHERE tmdb_id=?",
                         (int(tmdb_id),)).fetchone() is not None


def persons_missing_avatar(movie_id: int) -> bool:
    """该影片是否还有未确认头像的关联人物（avatar 为空即未尝试过）。"""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM movie_person mp JOIN persons p ON p.id=mp.person_id "
            "WHERE mp.movie_id=? AND (p.avatar IS NULL OR p.avatar='')",
            (movie_id,)).fetchone()
        return int(row["n"]) > 0


def update_person_bio(tmdb_id: int, biography: str = "",
                      birthday: str = "", place_of_birth: str = "",
                      lang: str = "") -> None:
    """人物详情缓存写入（简介/生日/出生地）。无论有无结果都刷新 bio_fetched_at，
    空简介不再每次访问重试，只经手动刷新入口更新。"""
    now = int(time.time())
    with _lock, _conn() as c:
        c.execute("UPDATE persons SET biography=?, birthday=?, place_of_birth=?,"
                  " bio_fetched_at=?, bio_lang=? WHERE tmdb_id=?",
                  (biography or "", birthday or "", place_of_birth or "",
                   now, lang or "", tmdb_id))


def get_person(tmdb_id: int, library_ids: list[int] | None = None) -> dict | None:
    """人物详情＋库内作品（参演/执导分开，同 tmdb 去重，年份倒序）。

    `library_ids` 非空时只统计这些视频库的作品（媒体库在路由层展开为其视频库 id）；
    None/空列表=全库（与读接口 `_library_scope` 口径一致，无效 id 由调用方传 [-1] 哨兵）。"""
    with _lock, _conn() as c:
        prow = c.execute("SELECT * FROM persons WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        if not prow:
            return None
        p = dict(prow)
        acting, directing = [], []
        seen = set()
        sql = ("SELECT m.*, mp.role, mp.character_name, mp.cast_order FROM movies m "
               "JOIN movie_person mp ON mp.movie_id=m.id "
               "JOIN persons p ON p.id=mp.person_id "
               "WHERE p.tmdb_id=?")
        params: list = [tmdb_id]
        if library_ids:
            ids = [int(x) for x in library_ids]
            sql += " AND m.library_id IN (%s)" % ",".join("?" * len(ids))
            params += ids
        sql += " ORDER BY mp.role, m.year IS NULL, m.year DESC"
        for r in c.execute(sql, params):
            d = _row_to_dict(r)
            key = d.get("tmdb_id") or -d["id"]
            role = r["role"]
            gkey = (role, key)
            if gkey in seen:
                continue
            seen.add(gkey)
            item = {"id": d["id"], "title": d.get("title", ""), "year": d.get("year"),
                    "library_id": d["library_id"],
                    "poster_path": d.get("poster_path", ""),
                    "tmdb_rating": d.get("tmdb_rating"),
                    "original_language": d.get("original_language", ""),
                    "character_name": r["character_name"] or ""}
            (acting if role == "actor" else directing).append(item)
        _attach_media_libraries(c, acting + directing)
        p["acting"] = acting
        p["directing"] = directing
        p["tv_works"] = get_person_tv_works(tmdb_id, library_ids=library_ids)
        return p


def get_person_tv_works(person_tmdb_id: int,
                        library_ids: list[int] | None = None) -> list[dict]:
    """人物参演的库内剧集（TV 点击进人物页用）：按 TMDB 人物 id 扫三级 credits
    （全剧聚合 `tmdb_cache(tv).credits.cast` → 季常驻 `tv_seasons.cast` →
    集客串 `tv_episodes.episode_credits.guests`），命中即收录所属剧。

    纯本地；返回 [{show_id, title, year, poster_path, original_language,
    character}]（character 取聚合首角色，去重按 show，年份倒序）。
    剧集数量级小（通常 < 千），内存过滤即可。"""
    try:
        pid = int(person_tmdb_id)
    except (TypeError, ValueError):
        return []
    if pid <= 0:
        return []
    with _lock, _conn() as c:
        sql = ("SELECT id, library_id, title, year, poster_path, tmdb_id,"
               " tmdb_rating, original_language FROM tv_shows")
        params: list = []
        if library_ids:
            ids = [int(x) for x in library_ids]
            sql += " WHERE library_id IN (%s)" % ",".join("?" * len(ids))
            params += ids
        shows = [dict(r) for r in c.execute(sql, params).fetchall()]
        if not shows:
            return []
        _attach_media_libraries(c, shows)
        cache_rows = {int(r["tmdb_id"]): (r["credits"] or "")
                      for r in c.execute("SELECT tmdb_id, credits FROM tmdb_cache"
                                         " WHERE media_type='tv'").fetchall()
                      if r["tmdb_id"] is not None}
        season_rows = c.execute('SELECT show_id, "cast" FROM tv_seasons').fetchall()
        season_cast: dict[int, list] = {}
        for r in season_rows:
            try:
                items = _json.loads(r["cast"]) if r["cast"] else []
            except (TypeError, ValueError):
                items = []
            if isinstance(items, list) and items:
                season_cast.setdefault(int(r["show_id"]), []).extend(items)
        guest_rows = c.execute(
            "SELECT show_id, episode_credits FROM tv_episodes"
            " WHERE episode_credits IS NOT NULL AND episode_credits != ''"
            " AND episode_credits != '{}'").fetchall()
        guest_cast: dict[int, list] = {}
        for r in guest_rows:
            try:
                ec = _json.loads(r["episode_credits"])
            except (TypeError, ValueError):
                continue
            guests = (ec or {}).get("guests") if isinstance(ec, dict) else None
            if guests:
                guest_cast.setdefault(int(r["show_id"]), []).extend(guests)
    out, seen = [], set()
    for s in shows:
        sid = int(s["id"])
        if sid in seen:
            continue
        character, matched = "", False
        tid = s.get("tmdb_id")
        if tid is not None:
            try:
                cr = _json.loads(cache_rows.get(int(tid)) or "{}")
            except (TypeError, ValueError):
                cr = {}
            for entry in (cr.get("cast") or []):
                if not isinstance(entry, dict):
                    continue
                try:
                    hit = int(entry.get("id") or 0) == pid
                except (TypeError, ValueError):
                    continue
                if hit:
                    matched = True
                    character = str(entry.get("character") or "")
                    break
        if not matched:  # 季常驻/集客串（无角色名也算参演）
            for entry in season_cast.get(sid, []) + guest_cast.get(sid, []):
                if not isinstance(entry, dict):
                    continue
                try:
                    hit = int(entry.get("id") or 0) == pid
                except (TypeError, ValueError):
                    continue
                if hit:
                    matched = True
                    character = str(entry.get("character") or "")
                    break
        if matched:
            seen.add(sid)
            out.append({"show_id": sid, "title": s.get("title") or "",
                        "library_id": s["library_id"],
                        "media_library_id": s["media_library_id"],
                        "media_library_name": s["media_library_name"],
                        "tmdb_rating": s["tmdb_rating"],
                        "year": s.get("year"),
                        "poster_path": s.get("poster_path") or "",
                        "original_language": s.get("original_language") or "",
                        "character": character})
    out.sort(key=lambda w: (w.get("year") is None, -(w.get("year") or 0)))
    return out


def link_person(movie_id: int, person_id: int, role: str,
                character_name: str = "", cast_order: int = 99) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO movie_person(movie_id, person_id, role,"
                  " character_name, cast_order) VALUES(?, ?, ?, ?, ?)",
                  (movie_id, person_id, role, character_name, cast_order))


def clear_movie_persons(movie_id: int) -> None:
    """清空单片演职员关联（手动换绑到不同 tmdb_id 时调用，避免旧阵容残留）。"""
    with _lock, _conn() as c:
        c.execute("DELETE FROM movie_person WHERE movie_id=?", (movie_id,))


def get_movie_person_links(movie_id: int) -> list[dict]:
    """单片现有演职员关联（含 person.tmdb_id），供 sync 幂等比对。"""
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT p.tmdb_id AS person_tmdb_id, mp.role, mp.character_name, mp.cast_order"
            " FROM movie_person mp JOIN persons p ON p.id=mp.person_id"
            " WHERE mp.movie_id=?", (movie_id,))]


def copy_person_links(src_movie_id: int, dst_movie_id: int) -> int:
    """同 tmdb_id 多版本复用：把源行的 movie_person 原样复制到目标行（人物已存在，无需调网）。
    返回复制条数。"""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT person_id, role, character_name, cast_order FROM movie_person"
            " WHERE movie_id=?", (src_movie_id,)).fetchall()
        n = 0
        for r in rows:
            c.execute("INSERT OR IGNORE INTO movie_person(movie_id, person_id, role,"
                      " character_name, cast_order) VALUES(?, ?, ?, ?, ?)",
                      (dst_movie_id, r["person_id"], r["role"],
                       r["character_name"] or "", r["cast_order"]))
            n += 1
        return n


def find_sibling_with_persons(tmdb_id: int, exclude_movie_id: int) -> int | None:
    """找同 tmdb_id 下已有演职员关联的兄弟行，供新版本免网络复用人物。"""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT m.id FROM movies m WHERE m.tmdb_id=? AND m.id!=?"
            " AND EXISTS(SELECT 1 FROM movie_person mp WHERE mp.movie_id=m.id)"
            " ORDER BY m.updated_at DESC LIMIT 1", (tmdb_id, exclude_movie_id)).fetchone()
        return int(row["id"]) if row else None

