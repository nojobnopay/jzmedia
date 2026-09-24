"""TV 演职员头像代理：白名单/缓存/失败降级 + cast 均带 profile_path。"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from app import library_paths, store
from app.db import POSTER_DIR
from app.main import app
from app.routers import tv as tv_router

client = TestClient(app)


@pytest.fixture()
def lib(tmp_path):
    root = tmp_path / "tvavatar"
    root.mkdir()
    row = store.create_library(name=f"tvavatar-{tmp_path.name}", kind="tv",
                               path=str(root))
    library_paths.invalidate_cache()
    yield row
    store.delete_media_library(row["media_library_id"])
    library_paths.invalidate_cache()


def _show_with_cast(lid, title="头像剧", year=2022, tmdb_id=902):
    sid = store.upsert_show(lid, title, year)
    store.update_show_meta(sid, tmdb_id=tmdb_id)
    credits = {"cast": [
        {"id": 1, "name": "主演甲", "character": "角甲",
         "profile_path": "/aaa111.jpg", "order": 0},
        {"id": 2, "name": "主演乙", "character": "",
         "profile_path": None, "order": 1},
    ], "crew": []}
    store.upsert_tmdb_cache(tmdb_id, {"title": title, "year": year}, credits, "",
                            media_type="tv")
    e1 = store.upsert_episode(sid, lid, f"{title}/Season 01/{title}-S01E01.mkv",
                              1, 1, "一")
    return sid, e1


def test_avatar_path_whitelist():
    for bad in ("", "../x.jpg", "../../etc/passwd", "http://e.com/a.jpg",
                "/short.jpg", "/abc.jpx", "abc123.jpg"):
        r = client.get("/api/tv/cast-avatar", params={"path": bad})
        assert r.status_code == 422, bad


def test_avatar_download_and_cache(lib, monkeypatch):
    calls = []

    def _fake_dl(path, dest, size="h632"):
        calls.append((path, dest, size))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"fakejpeg")
        return True

    monkeypatch.setattr(tv_router.tmdb, "download_image", _fake_dl)
    r = client.get("/api/tv/cast-avatar", params={"path": "/aaa111.jpg"})
    assert r.status_code == 200, r.text
    assert r.content == b"fakejpeg"
    assert calls and calls[0][0] == "/aaa111.jpg" and calls[0][2] == "h632"
    dest = os.path.join(POSTER_DIR, "tvcast",
                        "h632_" + __import__("hashlib").sha1(b"/aaa111.jpg").hexdigest()[:12] + ".jpg")
    assert os.path.isfile(dest)
    # 二次命中缓存，不再下载
    r2 = client.get("/api/tv/cast-avatar", params={"path": "/aaa111.jpg"})
    assert r2.status_code == 200 and len(calls) == 1


def test_avatar_download_failure_is_502(monkeypatch):
    monkeypatch.setattr(tv_router.tmdb, "download_image", lambda *a, **k: False)
    r = client.get("/api/tv/cast-avatar", params={"path": "/bbbb22.jpg"})
    assert r.status_code == 502


def test_cast_payloads_carry_profile_path(lib):
    lid = lib["id"]
    sid, e1 = _show_with_cast(lid)
    d = client.get(f"/api/tv/shows/{sid}").json()
    assert d["cast"][0]["profile_path"] == "/aaa111.jpg"
    assert d["cast"][1]["profile_path"] in ("", None)
    s = client.get(f"/api/tv/shows/{sid}/seasons/1").json()
    assert s["cast_source"] == "aggregate"
    assert s["cast"][0]["profile_path"] == "/aaa111.jpg"
    one = client.get(f"/api/tv/episodes/{e1}").json()
    assert one["cast"][0]["profile_path"] == "/aaa111.jpg"
