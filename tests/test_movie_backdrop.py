"""Landscape artwork is independent of the fast, local movie detail response."""
from pathlib import Path

from fastapi.testclient import TestClient

from app import store, tmdb
from app.db import POSTER_DIR
from app.main import app

client = TestClient(app)


def movie(tid, backdrop="/landscape.jpg"):
    mid = store.upsert_movie_by_path(f"backdrops/{tid}.mkv")
    store.update_movie_meta(mid, title="Backdrop", year=2020, tmdb_id=tid)
    store.upsert_tmdb_cache(tid, {"title": "Backdrop", "media_type": "movie",
                                  "backdrop_tmdb_path": backdrop}, {}, "/poster.jpg")
    return mid


def test_detail_does_not_download_backdrop_and_image_is_cached(monkeypatch):
    mid = movie(990801)
    calls = []

    def download(path, dest, size):
        calls.append((path, size))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"landscape")
        return True

    monkeypatch.setattr(tmdb, "download_image", download)
    assert client.get(f"/api/movies/{mid}").status_code == 200
    assert calls == []
    for _ in range(2):
        response = client.get(f"/api/movies/{mid}/backdrop")
        assert response.status_code == 200
        assert response.content == b"landscape"
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"] == "no-cache"
    assert calls == [("/landscape.jpg", "w780")]


def test_local_backdrop_works_without_remote_metadata(monkeypatch):
    mid = movie(990802, "")
    dest = Path(POSTER_DIR) / "backdrops/movie_990802.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"local")

    def unexpected(*args, **kwargs):
        raise AssertionError("Cached backdrop must not use the network")

    monkeypatch.setattr(tmdb, "download_image", unexpected)
    response = client.get(f"/api/movies/{mid}/backdrop")
    assert response.status_code == 200
    assert response.content == b"local"


def test_missing_artwork_does_not_fall_back_to_portrait(monkeypatch):
    mid = movie(990803, "")
    monkeypatch.setattr(tmdb, "download_image", lambda *a, **kw: False)
    assert client.get(f"/api/movies/{mid}/backdrop").status_code == 404
    assert client.get("/api/movies/999999999/backdrop").status_code == 404
    unmatched = store.upsert_movie_by_path("backdrops/unmatched.mkv")
    assert client.get(f"/api/movies/{unmatched}/backdrop").status_code == 404


def test_download_failure_leaves_movie_detail_available(monkeypatch):
    mid = movie(990804)
    monkeypatch.setattr(tmdb, "download_image", lambda *a, **kw: False)
    assert client.get(f"/api/movies/{mid}/backdrop").status_code == 502

    def fail(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(tmdb, "download_image", fail)
    assert client.get(f"/api/movies/{mid}/backdrop").status_code == 502
    assert client.get(f"/api/movies/{mid}").status_code == 200
