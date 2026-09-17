"""title_auto 回归网（2026-09 用户报障）：文件名自动标题必须能被 TMDB 标题覆盖，
手工标题必须永久保护；存量行迁移时按缓存离线愈合。"""
import sqlite3

from fastapi.testclient import TestClient

from app import scanner, store
from app.main import app
from app.store import _base

client = TestClient(app)

ZH_TITLE = "菊次郎的夏天"
ZH_ORIG = "菊次郎の夏"


def _seed_cache(tmdb_id: int = 4291, title: str = ZH_TITLE, orig: str = ZH_ORIG):
    store.upsert_tmdb_cache(tmdb_id, {
        "title": title, "original_title": orig, "year": 1999, "overview": "",
        "tmdb_id": tmdb_id, "imdb_id": "", "tmdb_rating": 8.0,
        "genres": [], "genre_ids": [], "original_language": "ja",
        "origin_countries": ["JP"], "origin_country": "JP", "region": "日本",
        "media_type": "movie", "collection_tmdb_id": None,
        "collection_name": "", "collection_poster_path": "",
    }, {"cast": [], "crew": []}, "")


def _mk(media_root, rel, data=b"x"):
    p = media_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_scan_matched_replaces_auto_title_with_tmdb(media_root, monkeypatch):
    """本次报障用例：上传文件 → 自动匹配 → 标题必须是 TMDB 标题，而不是文件名。"""
    _seed_cache()
    monkeypatch.setattr(scanner.tmdb, "search_movie", lambda q, year=None: [
        {"id": 4291, "title": ZH_TITLE, "original_title": ZH_ORIG,
         "release_date": "1999-06-05", "vote_average": 8.0}])
    rel = "incoming/Kikujiro.1999.BD1080p.mkv"
    _mk(media_root, rel)
    r = scanner.scan_one(str(media_root / rel))
    assert r["status"] in ("ok", "ok_needs_review"), r
    row = store.get_by_path(rel)
    assert row["title"] == ZH_TITLE
    assert row["original_title"] == ZH_ORIG
    assert int(row["title_auto"] or 0) == 0


def test_manual_title_not_overwritten(media_root):
    mid = store.upsert_movie_by_path("keep/Manual.2001.mkv")
    store.update_movie_meta(mid, title="我的自定义标题", tmdb_id=7001, title_auto=0)
    _seed_cache(7001, title="TMDB 标题", orig="TMDB Original")
    store.copy_tmdb_to_movie(mid, old_title="TMDB 标题")
    row = store.get_movie(mid)
    assert row["title"] == "我的自定义标题"


def test_auto_title_overwritten_and_flag_cleared(media_root):
    mid = store.upsert_movie_by_path("auto/Kikujiro.1999.mkv")
    store.update_movie_meta(mid, title="Kikujiro", tmdb_id=7002, title_auto=1)
    _seed_cache(7002, title=ZH_TITLE, orig=ZH_ORIG)
    store.copy_tmdb_to_movie(mid, old_title=None)
    row = store.get_movie(mid)
    assert row["title"] == ZH_TITLE and int(row["title_auto"] or 0) == 0
    # 之后刷新不再误覆盖（已转为受保护标题）
    store.copy_tmdb_to_movie(mid, old_title="别的旧标题")
    assert store.get_movie(mid)["title"] == ZH_TITLE


def test_patch_title_marks_manual(media_root):
    mid = store.upsert_movie_by_path("manual/Patch.Me.2002.mkv")
    store.update_movie_meta(mid, title="Kikujiro", title_auto=1)
    r = client.patch(f"/api/movies/{mid}", json={"title": "菊次郎的夏天"})
    assert r.status_code == 200
    row = store.get_movie(mid)
    assert row["title"] == "菊次郎的夏天" and int(row["title_auto"] or 0) == 0


def test_migration_heals_auto_title_from_cache(tmp_path, monkeypatch):
    """迁移 v11：与路径反解一致的行视为自动标题；已匹配的用缓存标题离线愈合。"""
    dbp = tmp_path / "legacy.db"
    from test_migrations import LEGACY_SCHEMA
    with sqlite3.connect(dbp) as c:
        c.executescript(LEGACY_SCHEMA)
        # 模拟“v10 时代的库”：缓存表已有 title（v5 迁移会给老库补）
        c.execute("ALTER TABLE tmdb_cache ADD COLUMN title TEXT DEFAULT ''")
        c.execute("INSERT INTO movies(file_path, title, original_title, tmdb_id)"
                  " VALUES('电影/日本/Kikujiro (1999)/Kikujiro (1999).mp4', 'Kikujiro', '菊次郎の夏', 4291)")
        from app.scanner.parse import parse_filename, normalize_title
        auto_title = normalize_title(parse_filename("NoMatch.2020.mkv").get("title") or "")
        c.execute("INSERT INTO movies(file_path, title) VALUES('待整理/NoMatch.2020.mkv', ?)",
                  (auto_title,))
        c.execute("INSERT INTO tmdb_cache(tmdb_id, title, fetched_at) VALUES(4291, ?, 1)", (ZH_TITLE,))
        c.execute("PRAGMA user_version = 0")
    monkeypatch.setattr(_base, "DB_PATH", str(dbp))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    _base.init_db()
    with sqlite3.connect(dbp) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT title, title_auto FROM movies WHERE tmdb_id=4291").fetchone()
        assert row["title"] == ZH_TITLE and int(row["title_auto"] or 0) == 0
        un = c.execute("SELECT title_auto FROM movies WHERE file_path LIKE '待整理/%'").fetchone()
        assert int(un["title_auto"] or 0) == 1
