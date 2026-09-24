"""P0 回归：两处相对导入错误导致端点 500/失效（B9 拆分遗留）。

- `/api/movies/{id}/poster-orig`：`from .. import tmdb`（应为 `...`）→ 每次 500；
- `/api/stream/backends`：`from .. import transcode` → 永远返回 software 兜底。
"""
import os

from fastapi.testclient import TestClient

from app import store, tmdb, transcode
from app.db import POSTER_DIR
from app.main import app

client = TestClient(app)


def _movie(rel="p0/海报 (2020).mkv", tmdb_id=778001):
    mid = store.upsert_movie_by_path(rel)
    store.update_movie_meta(mid, title="海报", year=2020, tmdb_id=tmdb_id)
    return mid


def test_poster_orig_returns_original(monkeypatch):
    mid = _movie()
    store.upsert_tmdb_cache(778001, {"title": "海报", "year": 2020,
                                     "media_type": "movie"}, {}, "/p1.jpg")
    dest = os.path.join(POSTER_DIR, "orig", "778001.jpg")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        os.remove(dest)
    calls = []

    def _fake_download(remote, dst, size="w500"):
        calls.append((remote, dst, size))
        with open(dst, "wb") as fh:
            fh.write(b"\xff\xd8ORIG")
        return True

    monkeypatch.setattr(tmdb, "download_poster", _fake_download)
    try:
        r = client.get(f"/api/movies/{mid}/poster-orig")
        assert r.status_code == 200, r.text
        assert r.content == b"\xff\xd8ORIG"
        assert calls == [("/p1.jpg", dest, "original")]
        # 已缓存 → 不再下载
        r2 = client.get(f"/api/movies/{mid}/poster-orig")
        assert r2.status_code == 200 and len(calls) == 1
    finally:
        if os.path.exists(dest):
            os.remove(dest)


def test_poster_orig_404_without_cache():
    mid = _movie(rel="p0/无图 (2021).mkv", tmdb_id=778002)
    store.upsert_tmdb_cache(778002, {"title": "无图", "year": 2021,
                                     "media_type": "movie"}, {}, "")
    r = client.get(f"/api/movies/{mid}/poster-orig")
    assert r.status_code == 404
    assert "poster" in r.text


def test_stream_backends_uses_real_impl(monkeypatch):
    sentinel = {"name": "vaapi", "hw": True, "device": "/dev/dri/renderD128",
                "reason": "", "env": "auto"}
    calls = []

    def _fake_info(refresh=False):
        calls.append(refresh)
        return sentinel

    monkeypatch.setattr(transcode, "backend_info", _fake_info)
    r = client.get("/api/stream/backends")
    assert r.status_code == 200 and r.json() == sentinel
    r2 = client.get("/api/stream/backends?refresh=1")
    assert r2.status_code == 200 and r2.json() == sentinel
    assert calls == [False, True]
