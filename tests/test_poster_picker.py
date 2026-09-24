"""海报选择器（方案 A）：候选列表 / 缩略图代理 / 选定持久化（刷新不回退）。"""
import os

from fastapi.testclient import TestClient

from app import store, tmdb
from app.db import POSTER_DIR
from app.main import app

client = TestClient(app)

POSTERS = [
    {"file_path": "/lowposter.jpg", "width": 540, "height": 755,
     "iso_639_1": "en", "vote_average": 3.0},
    {"file_path": "/bigposter1.jpg", "width": 2000, "height": 3000,
     "iso_639_1": "en", "vote_average": 7.2},
    {"file_path": "/bigposter2.jpg", "width": 2000, "height": 3000,
     "iso_639_1": "zh", "vote_average": 5.0},
]


def _movie(tmdb_id):
    mid = store.upsert_movie_by_path(f"picker/{tmdb_id}.mkv")
    store.update_movie_meta(mid, title="Picker", year=2019, tmdb_id=tmdb_id)
    return mid


def _seed_cache(tid, poster="/lowposter.jpg"):
    store.upsert_tmdb_cache(tid, {"title": "Picker", "year": 2019,
                                  "media_type": "movie"}, {}, poster)


def test_posters_list_sorted_and_current(monkeypatch):
    tid = 479455
    mid = _movie(tid)
    _seed_cache(tid, "/lowposter.jpg")
    monkeypatch.setattr(tmdb, "movie_images", lambda t: {"posters": POSTERS})
    d = client.get(f"/api/movies/{mid}/posters").json()
    assert [i["file_path"] for i in d["items"]] == [
        "/bigposter1.jpg", "/bigposter2.jpg", "/lowposter.jpg"]
    assert d["items"][0]["current"] is False
    low = next(i for i in d["items"] if i["file_path"] == "/lowposter.jpg")
    assert low["current"] is True and low["width"] == 540
    assert low["thumb_url"] == f"/api/movies/{mid}/poster-thumb?path=/lowposter.jpg"


def test_posters_list_no_tmdb_id():
    mid = store.upsert_movie_by_path("picker/nomatch.mkv")
    store.update_movie_meta(mid, title="无匹配", year=2020)
    assert client.get(f"/api/movies/{mid}/posters").status_code == 404


def test_poster_thumb_proxy_cached(monkeypatch):
    tid = 479456
    mid = _movie(tid)
    calls = []

    def _fake_dl(fp, dest, size="w500"):
        calls.append((fp, size))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(b"IMG")
        return True

    monkeypatch.setattr(tmdb, "download_image", _fake_dl)
    r = client.get(f"/api/movies/{mid}/poster-thumb", params={"path": "/bigposter1.jpg"})
    assert r.status_code == 200 and r.content == b"IMG"
    r2 = client.get(f"/api/movies/{mid}/poster-thumb", params={"path": "/bigposter1.jpg"})
    assert r2.status_code == 200
    assert calls == [("/bigposter1.jpg", "w185")]   # 缓存命中不重复下载
    assert client.get(f"/api/movies/{mid}/poster-thumb",
                      params={"path": "/../../etc/passwd"}).status_code == 422


def test_poster_set_persists_and_blocks_refresh_revert(monkeypatch):
    tid = 479457
    mid = _movie(tid)
    _seed_cache(tid, "/lowposter.jpg")
    monkeypatch.setattr(tmdb, "movie_images", lambda t: {"posters": POSTERS})
    downloads = []

    def _fake_dl(fp, dest, size="w500"):
        downloads.append((fp, size))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(size.encode())
        return True

    monkeypatch.setattr(tmdb, "download_image", _fake_dl)
    orig = os.path.join(POSTER_DIR, "orig", f"{tid}.jpg")
    os.makedirs(os.path.dirname(orig), exist_ok=True)
    with open(orig, "wb") as fh:
        fh.write(b"OLD")   # 旧原图缓存必须被清掉
    r = client.post(f"/api/movies/{mid}/poster", json={"file_path": "/bigposter1.jpg"})
    assert r.status_code == 200, r.text
    assert r.json()["original_ok"] is True and r.json()["file_path"] == "/bigposter1.jpg"
    assert ("/bigposter1.jpg", "w500") in downloads
    assert ("/bigposter1.jpg", "original") in downloads
    with open(os.path.join(POSTER_DIR, "movies", f"{tid}.jpg"), "rb") as fh:
        assert fh.read() == b"w500"
    with open(orig, "rb") as fh:
        assert fh.read() == b"original"
    # 同 tmdb 行 poster_path 同步
    assert store.get_movie(mid)["poster_path"] == f"posters/movies/{tid}.jpg"
    # 刷新（TMDB 默认海报仍是低分那张）不得回退
    _seed_cache(tid, "/lowposter.jpg")
    cached = store.get_tmdb_cached(tid)
    assert cached["poster_tmdb_path"] == "/bigposter1.jpg"
    assert cached["poster_override"] == "/bigposter1.jpg"
    # poster-orig 直接返回新原图（不再重下）
    r2 = client.get(f"/api/movies/{mid}/poster-orig")
    assert r2.status_code == 200 and r2.content == b"original"


def test_poster_set_rejects_non_candidate(monkeypatch):
    tid = 479458
    mid = _movie(tid)
    _seed_cache(tid)
    monkeypatch.setattr(tmdb, "movie_images", lambda t: {"posters": POSTERS})
    monkeypatch.setattr(tmdb, "download_image",
                        lambda fp, dest, size="w500": True)
    r = client.post(f"/api/movies/{mid}/poster",
                    json={"file_path": "/notincandidates.jpg"})
    assert r.status_code == 422
    assert client.post(f"/api/movies/{mid}/poster",
                       json={"file_path": "/../evil.jpg"}).status_code == 422


def test_poster_cache_headers(monkeypatch):
    """可变海报必须 no-cache（换海报 URL 不变，浏览器不得吃旧缓存）。"""
    tid = 479459
    mid = _movie(tid)
    _seed_cache(tid, "/lowposter.jpg")
    dest = os.path.join(POSTER_DIR, "movies", f"{tid}.jpg")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(b"JPEG")
    r = client.get(f"/posters/movies/{tid}.jpg")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
    # 候选缩略图（hash 命名，内容不变）不加 no-cache
    cand_dir = os.path.join(POSTER_DIR, "cand")
    os.makedirs(cand_dir, exist_ok=True)
    with open(os.path.join(cand_dir, f"{tid}_abcdef123456.jpg"), "wb") as fh:
        fh.write(b"THUMB")
    r2 = client.get(f"/posters/cand/{tid}_abcdef123456.jpg")
    assert r2.status_code == 200 and not r2.headers.get("cache-control")
    # poster-orig（原地覆盖的 orig 图）同样 no-cache
    monkeypatch.setattr(tmdb, "download_image",
                        lambda fp, dest, size="w500": (
                            open(dest, "wb").write(b"O") or True))
    r3 = client.get(f"/api/movies/{mid}/poster-orig")
    assert r3.status_code == 200
    assert r3.headers.get("cache-control") == "no-cache"


def test_schema_has_poster_override():
    from app.store import _base
    assert _base.SCHEMA_VERSION >= 19
    with _base._conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(tmdb_cache)")}
    assert "poster_override" in cols
