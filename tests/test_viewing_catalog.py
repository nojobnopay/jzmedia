"""Real HTTP catalogue requests must not reveal entries outside the device mode."""
import itertools

import pytest
from fastapi.testclient import TestClient

from app import library_paths, store, viewing
from app.content_rules import normalize_nfo_evidence
from app.main import app


PASSWORD = "family-test-42"
_ids = itertools.count(1982000000)


def request_ok(client, method, path, **kwargs):
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 200, (path, response.status_code, response.text)
    return response.json()


def mode(catalog, value):
    body = {"mode": value}
    if value == "private" or catalog["mode"] == "children":
        body["password"] = PASSWORD
    result = request_ok(catalog["client"], "post", "/api/viewing/mode", json=body)
    catalog["mode"] = result["mode"]
    return result


def policy(catalog, kind, ids, **fields):
    request_ok(catalog["client"], "post", "/api/viewing/unlock", json={"password": PASSWORD})
    return request_ok(catalog["client"], "patch", "/api/viewing/policy",
                      json={"kind": kind, "ids": ids, **fields})


@pytest.fixture()
def catalog(tmp_path):
    with viewing._connection() as connection:
        connection.execute("UPDATE protection_settings SET password_hash='' WHERE id=1")
        connection.execute("DELETE FROM viewing_sessions")
        connection.execute("DELETE FROM protection_attempts")
    root = tmp_path / "catalog"
    root.mkdir()
    media = store.create_media_library(
        name=f"viewing-{tmp_path.name}", path=str(root), video_libraries=[
            {"name": "movies", "kind": "movie", "subpath": "movies"},
            {"name": "tv", "kind": "tv", "subpath": "tv"},
            {"name": "other", "kind": "movie", "subpath": "other"}])
    libraries = {row["name"]: row["id"] for row in store.video_libraries_of(media["id"])}
    library_paths.invalidate_cache()
    client = TestClient(app)
    data = {"client": client, "media": media["id"], "root": root, "mode": "normal",
            "people": [], "cache": [], **libraries}

    def movie(key, rating, tid=None):
        identifier = store.upsert_movie_by_path(f"{key}.mkv", libraries["movies"])
        (root / "movies" / f"{key}.mkv").write_bytes(b"isolated video fixture")
        tid = tid or next(_ids)
        store.update_movie_meta(identifier, title=f"Mode {key}", tmdb_id=tid,
                                tags=[f"tag_{key}"], year=2020,
                                poster_path=f"posters/movies/{tid}.jpg",
                                content_evidence=normalize_nfo_evidence(rating))
        data[key] = identifier
        data[key + "_tid"] = tid
        store.save_progress(identifier, 300, 3600)
        return identifier

    movie("Lantern", "US:G")
    movie("LanternCopy", "US:G", data["Lantern_tid"])
    movie("Ember", "US:R")
    movie("Mist", "")
    movie("Vault", "US:G")
    for key, rating in (("Meadow", "US:TV-Y"), ("Storm", "US:TV-14"), ("Archive", "US:TV-Y")):
        sid = store.upsert_show(libraries["tv"], f"Mode {key}", 2020)
        store.update_show_meta(sid, tmdb_id=next(_ids), tags=[f"tag_{key}"],
                               content_evidence=normalize_nfo_evidence(rating))
        store.upsert_season(sid, libraries["tv"], 1, name="Season 1")
        eid = store.upsert_episode(sid, libraries["tv"], f"{key}.mkv", 1, 1, title=f"{key} episode")
        (root / "tv" / f"{key}.mkv").write_bytes(b"isolated episode fixture")
        store.save_progress(eid, 180, 1800, "episode")
        data[key], data[key + "_episode"] = sid, eid
    data["private_extra"] = store.upsert_extra("private-extra.mkv", data["Vault"], library_id=libraries["movies"])
    data["public_extra"] = store.upsert_extra("public-extra.mkv", data["Lantern"], library_id=libraries["movies"])
    data["mixed"] = store.create_collection("Mode Mixed", member_ids=[data["Lantern"], data["Vault"], data["Ember"]],
                                            media_library_id=media["id"])["id"]
    data["private_collection"] = store.create_collection("Mode Private collection", member_ids=[data["Vault"]],
                                                         media_library_id=media["id"])["id"]
    for key, name, movies in (("shared_actor", "Mode Shared Actor", [data["Lantern"], data["Vault"]]),
                              ("private_actor", "Mode Secret Actor", [data["Vault"]])):
        tid = next(_ids)
        pid = store.upsert_person(tid, name)
        data["people"].append(tid)
        data[key] = tid
        for mid in movies:
            store.link_person(mid, pid, "actor")
            store.resync_fts(mid)
    request_ok(client, "post", "/api/viewing/password", json={"new_password": PASSWORD})
    policy(data, "movie", [data["Vault"]], is_private=True)
    policy(data, "show", [data["Archive"]], is_private=True)
    request_ok(client, "post", "/api/viewing/lock")
    try:
        yield data
    finally:
        client.close()
        with viewing._connection() as connection:
            connection.execute("UPDATE protection_settings SET password_hash='' WHERE id=1")
            connection.execute("DELETE FROM viewing_sessions")
            connection.execute("DELETE FROM protection_attempts")
        store.delete_media_library(media["id"])
        with store._lock, store._conn() as connection:
            for tid in data["people"]:
                connection.execute("DELETE FROM persons WHERE tmdb_id=?", (tid,))
            for tid, kind in data["cache"]:
                connection.execute("DELETE FROM tmdb_cache WHERE tmdb_id=? AND media_type=?", (tid, kind))
        for tid, kind in data["cache"]:
            store.delete_match_entries("tmdb", str(tid) if kind == "movie" else f"{kind}:{tid}")
        library_paths.invalidate_cache()


@pytest.mark.parametrize("selected,visible", [
    ("normal", {"Lantern", "Ember", "Mist"}), ("children", {"Lantern"}), ("private", {"Vault"}),
])
def test_movie_listing_search_suggest_and_pagination_share_scope(catalog, selected, visible):
    mode(catalog, selected)
    client = catalog["client"]
    expected_tids = {catalog[k + "_tid"] for k in visible}
    for endpoint in ("/api/movies", "/api/search"):
        response = request_ok(client, "get", endpoint, params={"library": catalog["movies"], "q": "Mode"})
        assert {item["tmdb_id"] for item in response["items"]} == expected_tids
        paged = request_ok(client, "get", endpoint, params={"library": catalog["movies"], "q": "Mode", "limit": 1})
        assert len(paged["items"]) == 1 and paged["has_more"] == (len(visible) > 1)
    suggested = request_ok(client, "get", "/api/search/suggest", params={"library": catalog["movies"], "q": "Mode"})
    assert len(suggested["items"]) == len(visible)
    private_response = client.get(f"/api/movies/{catalog['Vault']}")
    assert private_response.status_code == (200 if selected == "private" else 404)
    public_response = client.get(f"/api/movies/{catalog['Lantern']}")
    assert public_response.status_code == (404 if selected == "private" else 200)
    if selected != "private":
        assert {v["id"] for v in public_response.json()["versions"]} == {catalog["Lantern"], catalog["LanternCopy"]}
    assert private_response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("selected,count,private_visible", [("normal", 2, False), ("children", 1, False), ("private", 1, True)])
def test_collection_covers_members_and_facet_counts_use_visible_members(catalog, selected, count, private_visible):
    mode(catalog, selected)
    client = catalog["client"]
    collection = request_ok(client, "get", f"/api/collections/{catalog['mixed']}")
    assert collection["member_count"] == count
    assert len(collection["members"]) == count
    assert (catalog["Vault"] in {m["id"] for m in collection["members"]}) == private_visible
    assert (str(catalog["Vault_tid"]) in collection["cover"]) == private_visible
    response = client.get(f"/api/collections/{catalog['private_collection']}")
    assert response.status_code == (200 if private_visible else 404)
    facets = request_ok(client, "get", "/api/facets", params={"library": catalog["movies"]})
    by_id = {item["id"]: item["count"] for item in facets["collections"]}
    assert by_id[catalog["mixed"]] == count
    tags = {entry["value"] for entry in facets["tags"]}
    assert ("tag_Vault" in tags) == private_visible
    if selected == "children":
        assert tags == {"tag_Lantern", "tag_LanternCopy"}


@pytest.mark.parametrize("selected,visible", [("normal", {"Meadow", "Storm"}), ("children", {"Meadow"}), ("private", {"Archive"})])
def test_tv_counts_seasons_episodes_search_and_progress_inherit_show(catalog, selected, visible):
    mode(catalog, selected)
    client = catalog["client"]
    shows = request_ok(client, "get", "/api/tv/shows", params={"library": catalog["tv"], "limit": 1})
    assert shows["total"] == len(visible) and len(shows["items"]) == 1
    assert shows["has_more"] == (len(visible) > 1)
    stats = request_ok(client, "get", "/api/tv/stats", params={"library": catalog["tv"]})
    assert stats["shows"] == stats["seasons"] == stats["episodes"] == len(visible)
    recent = request_ok(client, "get", "/api/tv/recent-played", params={"library": catalog["tv"]})
    assert {row["show_id"] for row in recent["items"]} == {catalog[k] for k in visible}
    suggested = request_ok(client, "get", "/api/tv/suggest", params={"library": catalog["tv"], "q": "Mode"})
    assert len(suggested["items"]) == len(visible)
    search = request_ok(client, "get", "/api/tv-client/search", params={"kind": "show", "media_library": catalog["media"], "q": "Mode"})
    assert search["total"] == len(visible)
    for key in ("Meadow", "Storm", "Archive"):
        for path in (f"/api/tv/shows/{catalog[key]}", f"/api/tv/shows/{catalog[key]}/seasons/1",
                     f"/api/tv/episodes/{catalog[key + '_episode']}"):
            assert client.get(path).status_code == (200 if key in visible else 404), path


@pytest.mark.parametrize("selected", ["normal", "children", "private"])
def test_people_recent_played_and_extra_progress_do_not_cross_modes(catalog, selected):
    mode(catalog, selected)
    client = catalog["client"]
    person = request_ok(client, "get", f"/api/persons/{catalog['shared_actor']}")
    expected = catalog["Vault"] if selected == "private" else catalog["Lantern"]
    assert {row["id"] for row in person["acting"]} == {expected}
    assert client.get(f"/api/persons/{catalog['private_actor']}").status_code == (200 if selected == "private" else 404)
    actors = request_ok(client, "get", "/api/tv-client/actors", params={"media_library": catalog["media"], "q": "Secret"})
    assert actors["total"] == (1 if selected == "private" else 0)
    recent = request_ok(client, "get", "/api/movies/recent-played", params={"library": catalog["movies"]})
    assert (catalog["Vault"] in {r["id"] for r in recent["items"]}) == (selected == "private")
    for item, kind in ((catalog["Vault"], "movie"), (catalog["private_extra"], "extra")):
        response = client.get("/api/stream/progress", params={"version_id": item, "kind": kind})
        assert response.status_code == (200 if selected == "private" else 404)


def test_management_unlock_does_not_change_normal_browsing_scope(catalog):
    policy(catalog, "movie", [catalog["Mist"]], children_override="allow")
    response = catalog["client"].get(f"/api/movies/{catalog['Vault']}")
    assert response.status_code == 404
    mode(catalog, "children")
    payload = request_ok(catalog["client"], "get", "/api/movies", params={"library": catalog["movies"]})
    assert {row["tmdb_id"] for row in payload["items"]} == {catalog["Lantern_tid"], catalog["Mist_tid"]}


def test_parent_choice_cannot_make_private_content_visible_to_children(catalog):
    policy(catalog, "movie", [catalog["Vault"]], children_override="allow")
    mode(catalog, "children")
    assert catalog["client"].get(f"/api/movies/{catalog['Vault']}").status_code == 404
    denied = catalog["client"].patch("/api/viewing/policy", json={"kind": "movie", "ids": [catalog["Vault"]], "is_private": False})
    assert denied.status_code == 403
    assert catalog["client"].post("/api/viewing/mode", json={"mode": "normal"}).status_code == 403
    assert catalog["client"].get("/api/fs/list", params={"library": catalog["movies"]}).status_code == 403


def test_pg_family_animation_admission_keeps_parent_blocks_and_privacy(catalog):
    for key in ("Mist", "Vault"):
        store.update_movie_meta(catalog[key], genres=["动画", "家庭", "动作"], genre_ids=[16, 10751, 28],
                                content_evidence=normalize_nfo_evidence("US:PG / GB:U", ["cartoon violence"]))
        assert store.get_movie(catalog[key])["children_auto_status"] == "allow"
    # A parent's ban keeps all versions hidden even when their evidence passes.
    policy(catalog, "movie", [catalog["Lantern"]], children_override="block")
    store.update_movie_meta(catalog["Ember"], genres=["动画", "家庭"], genre_ids=[16, 10751])
    mode(catalog, "children")
    result = request_ok(catalog["client"], "get", "/api/movies", params={"library": catalog["movies"]})
    assert {item["id"] for item in result["items"]} == {catalog["Mist"]}
    assert catalog["client"].get(f"/api/movies/{catalog['Mist']}").status_code == 200
    for key in ("Vault", "Lantern", "LanternCopy", "Ember"):
        assert catalog["client"].get(f"/api/movies/{catalog[key]}").status_code == 404


def test_device_modes_do_not_change_another_device(catalog):
    other = TestClient(app)
    try:
        mode(catalog, "private")
        assert other.get(f"/api/movies/{catalog['Vault']}").status_code == 404
        assert other.get(f"/api/movies/{catalog['Ember']}").status_code == 200
    finally:
        other.close()


@pytest.mark.parametrize("selected", ["normal", "children", "private"])
def test_playback_version_selection_uses_the_same_visibility(catalog, selected, monkeypatch):
    monkeypatch.setattr("app.routers.stream.media._source_or_none", lambda row: None)
    mode(catalog, selected)
    client = catalog["client"]
    visible = catalog["Vault"] if selected == "private" else catalog["Lantern"]
    hidden = catalog["Lantern"] if selected == "private" else catalog["Vault"]
    result = request_ok(client, "get", "/api/stream/versions", params={"movie_id": visible})
    expected = {catalog["Vault"]} if selected == "private" else {catalog["Lantern"], catalog["LanternCopy"]}
    assert {row["version_id"] for row in result["versions"]} == expected
    for method in ("get", "post"):
        kwargs = {"params" if method == "get" else "json": {"movie_id": hidden}}
        assert getattr(client, method)("/api/stream/versions", **kwargs).status_code == 404


@pytest.mark.parametrize("selected,show_key", [("normal", "Meadow"), ("children", "Meadow"), ("private", "Archive")])
def test_tv_only_person_navigation_stays_available_for_visible_works(catalog, selected, show_key):
    tid = next(_ids)
    name = "Only Television Actor"
    store.upsert_person(tid, name, avatar="-")
    catalog["people"].append(tid)
    for key in ("Meadow", "Archive"):
        show_tid = store.get_show_meta(catalog[key])["tmdb_id"]
        store.upsert_tmdb_cache(show_tid, {"title": key}, {"cast": [{"id": tid, "name": name}], "crew": []}, media_type="tv")
        catalog["cache"].append((show_tid, "tv"))
    mode(catalog, selected)
    result = request_ok(catalog["client"], "get", f"/api/persons/{tid}")
    assert result["acting"] == []
    assert {row["show_id"] for row in result["tv_works"]} == {catalog[show_key]}
    ensured = request_ok(catalog["client"], "post", "/api/persons/ensure", json={"tmdb_id": tid, "name": name})
    assert {row["show_id"] for row in ensured["tv_works"]} == {catalog[show_key]}


@pytest.mark.parametrize("selected", ["normal", "children", "private"])
def test_tv_updates_remains_a_browsing_endpoint(catalog, selected):
    mode(catalog, selected)
    result = request_ok(catalog["client"], "get", "/api/tv/updates", params={"media_library": catalog["media"]})
    assert isinstance(result, dict)


def test_tv_actor_creation_only_accepts_visible_local_cast(catalog, monkeypatch):
    visible_tid, hidden_tid = next(_ids), next(_ids)
    for key, person_tid in (("Meadow", visible_tid), ("Archive", hidden_tid)):
        show_tid = store.get_show_meta(catalog[key])["tmdb_id"]
        store.upsert_tmdb_cache(show_tid, {"title": key}, {"cast": [{"id": person_tid, "name": key}]}, media_type="tv")
        catalog["cache"].append((show_tid, "tv"))
        catalog["people"].append(person_tid)
    mode(catalog, "children")
    client = catalog["client"]
    assert client.get(f"/api/persons/{visible_tid}").status_code == 404
    created = request_ok(client, "post", "/api/persons/ensure", json={"tmdb_id": visible_tid, "name": "Meadow"})
    assert {row["show_id"] for row in created["tv_works"]} == {catalog["Meadow"]}
    for tid in (hidden_tid, next(_ids)):
        assert client.post("/api/persons/ensure", json={"tmdb_id": tid, "name": "Unavailable"}).status_code == 404
    monkeypatch.setattr("app.routers.persons._fetch_and_cache_bio", lambda _: None)
    assert client.post(f"/api/persons/{visible_tid}/refresh").status_code == 200
    assert client.post(f"/api/persons/{hidden_tid}/refresh").status_code == 404


@pytest.mark.parametrize("selected", ["normal", "children", "private"])
def test_watched_controls_remain_available_only_for_visible_items(catalog, selected):
    mode(catalog, selected)
    client = catalog["client"]
    visible_movie, hidden_movie = ("Vault", "Lantern") if selected == "private" else ("Lantern", "Vault")
    visible_show, hidden_show = ("Archive", "Meadow") if selected == "private" else ("Meadow", "Archive")
    assert client.patch(f"/api/movies/{catalog[visible_movie]}", json={"watched": True}).status_code == 200
    assert client.patch(f"/api/movies/{catalog[hidden_movie]}", json={"watched": True}).status_code == 404
    for key, expected in ((visible_show, 200), (hidden_show, 404)):
        for path in (f"/api/tv/shows/{catalog[key]}/watched",
                     f"/api/tv/shows/{catalog[key]}/seasons/1/watched",
                     f"/api/tv/episodes/{catalog[key + '_episode']}/watched"):
            assert client.post(path, json={"watched": True}).status_code == expected
    assert not store.get_movie(catalog[hidden_movie])["watched"]
    assert not store.get_episode(catalog[hidden_show + "_episode"])["watched"]
