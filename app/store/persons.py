"""store.persons（自 app/store.py 拆分，评审 B9/R02-Q3；对外经 app.store 门面使用）。"""
import time
from ._base import _conn, _lock, _row_to_dict
__all__ = ['upsert_person', 'get_person_raw', 'person_exists', 'persons_missing_avatar', 'update_person_bio', 'get_person', 'link_person', 'clear_movie_persons', 'get_movie_person_links', 'copy_person_links', 'find_sibling_with_persons']

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


def get_person(tmdb_id: int) -> dict | None:
    """人物详情＋库内作品（参演/执导分开，同 tmdb 去重，年份倒序）。"""
    with _lock, _conn() as c:
        prow = c.execute("SELECT * FROM persons WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        if not prow:
            return None
        p = dict(prow)
        acting, directing = [], []
        seen = set()
        for r in c.execute(
                "SELECT m.*, mp.role, mp.character_name, mp.cast_order FROM movies m "
                "JOIN movie_person mp ON mp.movie_id=m.id "
                "JOIN persons p ON p.id=mp.person_id "
                "WHERE p.tmdb_id=? ORDER BY mp.role, m.year IS NULL, m.year DESC",
                (tmdb_id,)):
            d = _row_to_dict(r)
            key = d.get("tmdb_id") or -d["id"]
            role = r["role"]
            gkey = (role, key)
            if gkey in seen:
                continue
            seen.add(gkey)
            item = {"id": d["id"], "title": d.get("title", ""), "year": d.get("year"),
                    "poster_path": d.get("poster_path", ""),
                    "tmdb_rating": d.get("tmdb_rating"),
                    "original_language": d.get("original_language", ""),
                    "character_name": r["character_name"] or ""}
            (acting if role == "actor" else directing).append(item)
        p["acting"] = acting
        p["directing"] = directing
        return p


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

