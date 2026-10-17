"""TV avatar requests must work with protection enabled and respect device mode."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db, library_paths, store, tmdb, viewing
from app.main import app


PASSWORD = "avatar-test-password"
IMAGE = b"isolated-avatar-image"


def _cache_path(directory, profile):
    digest = hashlib.sha1(profile.encode()).hexdigest()[:12]
    return directory / "tvcast" / f"h632_{digest}.jpg"


@pytest.fixture
def avatars(tmp_path, monkeypatch):
    import app.store._base as base

    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "avatars.db"))
    context_token = viewing.current.set(None)
    store.init_db()
    directory = tmp_path / "posters"
    directory.mkdir()
    monkeypatch.setattr(db, "POSTER_DIR", str(directory))
    static = next(route.app for route in app.routes if getattr(route, "name", "") == "posters")
    monkeypatch.setattr(static, "directory", str(directory))
    monkeypatch.setattr(static, "all_directories", [str(directory)])
    (tmp_path / "media").mkdir()
    media = store.create_media_library(
        name="Avatar fixtures", path=str(tmp_path / "media"), video_libraries=[
            {"name": "movies", "kind": "movie", "subpath": "movies"},
            {"name": "tv", "kind": "tv", "subpath": "tv"}])
    libraries = {row["name"]: row["id"] for row in store.video_libraries_of(media["id"])}
    library_paths.invalidate_cache()
    profiles = {}
    for index, audience in enumerate(("family", "adult", "private")):
        people = {}
        for offset, source in enumerate(("movie", "show", "season", "episode")):
            profile = f"/{audience}{source}Avatar.jpg"
            profiles[profile] = audience
            people[source] = {"id": 730000 + index * 10 + offset,
                              "name": f"{audience} {source}", "profile_path": profile}
        movie = store.upsert_movie_by_path(f"{audience}.mkv", libraries["movies"])
        person = people["movie"]
        pid = store.upsert_person(person["id"], person["name"],
                                  profile_tmdb_path=person["profile_path"])
        store.link_person(movie, pid, "actor")
        store.resync_fts(movie)
        show = store.upsert_show(libraries["tv"], f"Avatar {audience}", 2020)
        show_tmdb = 740000 + index
        store.update_show_meta(show, tmdb_id=show_tmdb)
        store.upsert_tmdb_cache(show_tmdb, {"title": f"Avatar {audience}"},
                                {"cast": [people["show"]]}, media_type="tv")
        store.upsert_season(show, libraries["tv"], 1,
                            cast_json=json.dumps([people["season"]]))
        episode = store.upsert_episode(show, libraries["tv"], f"{audience}/S01E01.mkv", 1, 1)
        store.update_episode_meta(episode, episode_credits=json.dumps({"guests": [people["episode"]]}))
        with store._conn() as connection:
            for table, identifier in (("movies", movie), ("tv_shows", show)):
                connection.execute(f"UPDATE {table} SET is_private=?,children_override=? WHERE id=?",
                                   (int(audience == "private"),
                                    "allow" if audience == "family" else "block", identifier))
    downloads = []

    def download(profile, destination, size="w500"):
        downloads.append((profile, size))
        Path(destination).write_bytes(IMAGE)
        return True

    monkeypatch.setattr(tmdb, "download_image", download)
    viewing.set_password(PASSWORD)
    client = TestClient(app, raise_server_exceptions=False)
    issued = client.get("/api/viewing/session", headers={"X-Viewing-Client": "tv"})
    assert issued.status_code == 200, issued.text
    client.headers.update({"X-Viewing-Client": "tv",
                           "X-Viewing-Session": issued.json()["session_token"]})
    try:
        yield SimpleNamespace(client=client, profiles=profiles, directory=directory, downloads=downloads)
    finally:
        client.close()
        library_paths.invalidate_cache()
        viewing.current.reset(context_token)


@pytest.mark.parametrize("mode,visible", [
    ("normal", {"family", "adult"}), ("children", {"family"}), ("private", {"private"}),
])
@pytest.mark.parametrize("cached", [False, True], ids=["download", "cached"])
def test_avatar_proxy_and_cached_artwork_follow_device_mode(avatars, mode, visible, cached):
    fixture = avatars
    changed = fixture.client.post("/api/viewing/mode", json={"mode": mode, "password": PASSWORD})
    assert changed.status_code == 200, changed.text
    if cached:
        for profile in fixture.profiles:
            destination = _cache_path(fixture.directory, profile)
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(IMAGE)

    for profile, audience in fixture.profiles.items():
        expected = 200 if audience in visible else 404
        response = fixture.client.get("/api/tv/cast-avatar", params={"path": profile})
        assert response.status_code == expected, (mode, profile, response.text)
        destination = _cache_path(fixture.directory, profile)
        artwork = fixture.client.get("/posters/" + destination.relative_to(fixture.directory).as_posix())
        assert artwork.status_code == expected, (mode, profile, artwork.text)
        if expected == 200:
            assert response.content == artwork.content == IMAGE
            assert response.headers["content-type"] == "image/jpeg"
            assert response.headers["cache-control"] == "no-store"

    expected_downloads = {(profile, "h632") for profile, audience in fixture.profiles.items()
                          if audience in visible} if not cached else set()
    assert set(fixture.downloads) == expected_downloads
    assert len(fixture.downloads) == len(expected_downloads)
    unknown = fixture.client.get("/api/tv/cast-avatar", params={"path": "/unknownAvatar.jpg"})
    assert unknown.status_code == 404
    assert set(fixture.downloads) == expected_downloads
