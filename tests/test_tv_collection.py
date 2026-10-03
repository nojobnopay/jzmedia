"""Web inventory uses official identity but preserves real local playback scope."""
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import store, tv_airing, tv_collection
from app.routers.tv_airing import router


@pytest.fixture()
def inventory(tmp_path, monkeypatch):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    media = store.create_media_library("inventory-" + tmp_path.name, path=str(tmp_path / "one"),
                                       video_libraries=[{"name": "A", "kind": "tv", "subpath": "A"}])
    other = store.create_media_library("other-" + tmp_path.name, path=str(tmp_path / "two"),
                                       video_libraries=[{"name": "C", "kind": "tv", "subpath": "C"}])
    a = store.video_libraries_of(media["id"])[0]
    b = store.create_video_library(media["id"], "B", "tv", "B")
    c = store.video_libraries_of(other["id"])[0]
    shows = [store.upsert_show(lib["id"], "虚构剧", 2026) for lib in (a, b, c)]
    tid = 910000 + shows[0]
    for sid in shows:
        store.update_show_meta(sid, tmdb_id=tid, status="Returning Series")
    today = datetime.now(timezone.utc).date()
    detail = {"id": tid, "status": "Returning Series", "seasons": [
        {"season_number": 0, "name": "Specials", "air_date": "", "episode_count": 2},
        {"season_number": 1, "name": "第一季", "air_date": "2020-01-01", "episode_count": 4},
        {"season_number": 2, "name": "第二季", "air_date": "2021-01-01", "episode_count": 3},
        {"season_number": 3, "name": "第三季", "air_date": str(today + timedelta(days=30)), "episode_count": 6},
    ], "last_episode_to_air": {"id": 104, "season_number": 1, "episode_number": 4,
                              "air_date": str(today), "name": "终章"}}
    catalogs = {sn: {"season_number": sn, "name": f"第{sn}季", "episodes": [
        {"id": sn * 100 + ep, "season_number": sn, "episode_number": ep,
         "name": f"第{ep}集", "air_date": str(today)} for ep in range(1, count + 1)]}
        for sn, count in [(1, 4), (2, 3)]}
    monkeypatch.setattr(tv_airing, "get_snapshot", lambda _: {
        "detail": detail, "checked_at": 123, "next_check_at": 456, "error": ""})
    monkeypatch.setattr(tv_airing, "get_cached_catalogs", lambda _: dict(catalogs))
    monkeypatch.setattr(store, "get_tmdb_cache_seasons", lambda _: {})
    monkeypatch.setattr(tv_airing, "get_catalog", lambda _, sn, **kw: {
        "detail": catalogs.get(sn, {}), "checked_at": 123, "stale": False, "error": ""})
    api = FastAPI()
    api.include_router(router)
    yield {"media": media, "libs": (a, b, c), "shows": shows, "tid": tid,
           "detail": detail, "catalogs": catalogs, "client": TestClient(api)}
    store.delete_media_library(media["id"])
    store.delete_media_library(other["id"])


def episode(inv, which=0, season=1, number=1, **fields):
    sid, lib = inv["shows"][which], inv["libs"][which]
    path = fields.pop("path", f"剧/S{season}E{number}.mkv")
    eid = store.upsert_episode(sid, lib["id"], path, season, number, "本地集")
    if fields:
        store.update_episode_meta(eid, **fields)
    return eid


def test_aggregate_same_media_and_preserve_actual_source(inventory):
    inv = inventory
    episode(inv, season=1, number=1, tmdb_episode_id=101)
    elsewhere = episode(inv, which=1, season=2, number=1, tmdb_episode_id=201)
    episode(inv, which=2, season=2, number=2, tmdb_episode_id=202)
    data = inv["client"].get(f"/api/tv/shows/{inv['shows'][0]}/collection").json()
    season = next(s for s in data["seasons"] if s["season"] == 2)
    assert season["collected_count"] == 1 and season["local_count"] == 0
    assert season["sources"] == [{"show_id": inv["shows"][1], "library_id": inv["libs"][1]["id"],
                                   "library_name": "B", "season": 2, "count": 1}]
    cat = inv["client"].get(f"/api/tv/shows/{inv['shows'][0]}/seasons/2/catalog").json()
    assert cat["items"][0]["sources"][0]["episode_id"] == elsewhere
    assert cat["items"][1]["collection_state"] == "uncollected"


def test_missing_sp_future_and_zero_do_not_use_official_total(inventory):
    data = tv_collection.Collection(inventory["shows"][0]).summary()
    assert data["missing_seasons"] == [1, 2]
    assert [s["collected_count"] for s in data["seasons"]] == [0, 0, 0, 0]
    assert data["seasons"][0]["airing_state"] == "unknown"
    assert data["seasons"][-1]["airing_state"] == "upcoming"
    assert data["seasons"][1]["official_count"] == 4


def test_versions_ranges_missing_and_local_only(inventory):
    inv = inventory
    episode(inv, episode_end=2, tmdb_episode_id=101)
    episode(inv, path="剧/V2-S1E1.mkv", tmdb_episode_id=101)
    episode(inv, number=3, tmdb_episode_id=103, missing=1)
    local_only = episode(inv, season=5, number=1, local_only=1)
    result = tv_collection.Collection(inv["shows"][0]).summary()
    season = next(s for s in result["seasons"] if s["season"] == 1)
    assert season["collected_count"] == 2 and season["local_count"] == 2
    local = next(s for s in result["seasons"] if s["season"] == 5)
    assert local["local_count"] == 1 and local["official_count"] is None
    assert local["collected_count"] == 0 and local["collection_state"] == "collected"
    assert local["collection_reason"] == ""
    assert store.get_episode(local_only)["local_only"] == 1


def test_manual_cross_season_matches_by_identity(inventory):
    inv = inventory
    eid = episode(inv, season=8, number=20, tmdb_episode_id=101, match_source="manual")
    ctx = tv_collection.Collection(inv["shows"][0])
    state, sources = ctx.episode_state(1, 1, 101)
    assert state == "collected" and sources[0]["season"] == 8
    assert sources[0]["episode_id"] == eid
    assert store.get_episode(eid)["season"] == 8


def test_uncertain_range_does_not_invent_official_coverage(inventory):
    inv = inventory
    episode(inv, season=8, number=20, episode_end=22, tmdb_episode_id=101, match_source="manual")
    ctx = tv_collection.Collection(inv["shows"][0])
    assert ctx.episode_state(1, 1, 101)[0] == "collected"
    assert ctx.episode_state(1, 2, 102)[0] == "uncertain"
    assert ctx.summary()["latest_episode"]["collection_reason"] == "numbering_unresolved"
    cards = {s["season"]: s for s in ctx.season_cards()}
    assert cards[8]["collection_reason"] == "numbering_unresolved"
    assert cards[2]["collection_reason"] == "coverage_incomplete"
    episode(inv, number=10, tmdb_episode_id=99999)
    assert tv_collection.Collection(inv["shows"][0]).summary()["missing_seasons"] == []


def test_collection_read_never_calls_network_or_storage(inventory, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("collection GET must remain local")
    monkeypatch.setattr(tv_airing, "get_catalog", forbidden)
    from app import storage, tmdb
    monkeypatch.setattr(storage, "backend_for", forbidden)
    monkeypatch.setattr(tmdb, "tv_detail", forbidden)
    r = inventory["client"].get(f"/api/tv/shows/{inventory['shows'][0]}/collection")
    assert r.status_code == 200


def test_catalog_new_season_without_local_rows_is_not_404(inventory):
    inv = inventory
    before = store.count_show_episodes(inv["shows"][0])
    r = inv["client"].get(f"/api/tv/shows/{inv['shows'][0]}/seasons/2/catalog")
    assert r.status_code == 200 and len(r.json()["items"]) == 3
    assert all(e["collection_state"] == "uncollected" and e["sources"] == [] for e in r.json()["items"])
    assert store.count_show_episodes(inv["shows"][0]) == before
    assert inv["client"].get(f"/api/tv/shows/{inv['shows'][0]}/seasons/900/catalog").status_code == 404


def test_catalog_rebind_during_request_rejects_old_result(inventory, monkeypatch):
    inv = inventory
    def rebound(*args, **kwargs):
        store.update_show_meta(inv["shows"][0], tmdb_id=111)
        return {"detail": inv["catalogs"][1]}
    monkeypatch.setattr(tv_airing, "get_catalog", rebound)
    r = inv["client"].get(f"/api/tv/shows/{inv['shows'][0]}/seasons/1/catalog")
    assert r.status_code == 409


def test_recommendations_keep_middle_missing_and_finale(inventory, monkeypatch):
    inv = inventory
    inv["detail"]["status"] = "Ended"
    events = [{"event_id": f"{inv['tid']}:{n}", "tmdb_id": inv["tid"], "season": 1,
               "episode": n, "tmdb_episode_id": 100 + n, "title": f"第{n}集",
               "air_date": inv["detail"]["last_episode_to_air"]["air_date"], "discovered_at": 123}
              for n in (3, 4)]
    monkeypatch.setattr(tv_airing, "list_events", lambda tids: events)
    episode(inv, number=4, tmdb_episode_id=104)
    result = tv_collection.updates(inv["media"]["id"])
    assert len(result["items"]) == 1
    assert [e["episode"] for e in result["items"][0]["events"]] == [3]
    episode(inv, which=1, number=3, tmdb_episode_id=103)
    assert tv_collection.updates(inv["media"]["id"])["items"] == []
    assert tv_collection.updates(999999)["items"] == []


def test_unknown_show_match_stays_uncertain(inventory):
    sid = inventory["shows"][0]
    episode(inventory, tmdb_episode_id=101)
    store.update_show_meta(sid, needs_review=1)
    data = inventory["client"].get(f"/api/tv/shows/{sid}/collection").json()
    assert not data["confirmed"] and data["missing_seasons"] == []
    assert data["seasons"][0]["collection_reason"] == "show_unconfirmed"
    assert data["seasons"][0]["collected_count"] == 0
    assert data["seasons"][0]["local_count"] == 1
    assert tv_collection.Collection(sid).episode_state(1, 1, 101)[0] == "uncertain"
    assert inventory["client"].get(f"/api/tv/shows/{sid}/seasons/1/catalog").status_code == 422


def test_invalid_dates_are_unknown():
    assert tv_collection.air_state("") == "unknown"
    assert tv_collection.air_state("2026-99-99") == "unknown"
    assert tv_collection.status_text("Canceled") == "已取消"


def test_partial_identity_cache_keeps_local_collection_without_requesting_review(inventory):
    inv = inventory
    inv["catalogs"].clear()
    episode(inv, number=1, tmdb_episode_id=101)
    episode(inv, number=4, tmdb_episode_id=104)
    ctx = tv_collection.Collection(inv["shows"][0])
    card = next(s for s in ctx.season_cards() if s["season"] == 1)
    assert card["collection_state"] == "uncertain" and card["collected_count"] == 1
    assert card["collection_reason"] == "catalog_missing" and card["local_count"] == 2
    assert all(s["collection_reason"] == "catalog_missing" for s in ctx.season_cards())
    assert ctx.episode_state(1, 1, 101)[0] == "collected"
    assert ctx.episode_state(1, 4, 104)[0] == "collected"
    assert ctx.summary()["latest_episode"]["collection_reason"] == ""
    assert ctx.summary()["missing_seasons"] == []


def test_plain_number_not_in_official_catalog_is_uncertain(inventory):
    episode(inventory, number=99)
    ctx = tv_collection.Collection(inventory["shows"][0])
    card = next(s for s in ctx.season_cards() if s["season"] == 1)
    assert card["collection_state"] == "uncertain" and card["collected_count"] == 0
    assert card["collection_reason"] == "numbering_unresolved"
    assert card["local_count"] == 1


@pytest.mark.parametrize("fields", [{"match_source": "manual"}, {}, {"absolute_number": 20}])
def test_cold_identity_does_not_guess_official_coordinates(inventory, fields):
    inv = inventory
    inv["catalogs"].clear()
    eid = episode(inv, season=2, number=1, tmdb_episode_id=101, **fields)
    ctx = tv_collection.Collection(inv["shows"][0])
    state, sources = ctx.episode_state(1, 1, 101)
    assert state == "collected" and sources[0]["episode_id"] == eid
    assert sources[0]["season"] == 2
    # Neither the official S1 coordinate nor local S2 is inferred from the ID.
    assert ctx.episode_state(1, 1)[0] == "uncertain"
    assert ctx.episode_state(2, 1, 201)[0] == "uncertain"
    cards = {s["season"]: s for s in ctx.season_cards()}
    assert cards[1]["collected_count"] == cards[2]["collected_count"] == 0
    assert cards[1]["local_count"] == 0 and cards[2]["local_count"] == 1
    assert cards[2]["collection_reason"] == "catalog_missing"


def test_cold_identity_sources_respect_media_scope_and_local_deduplication(inventory):
    inv = inventory
    inv["catalogs"].clear()
    first = episode(inv, tmdb_episode_id=101, episode_end=2)
    second = episode(inv, tmdb_episode_id=101, path="剧/another-version.mkv")
    sibling = episode(inv, which=1, season=8, number=20, tmdb_episode_id=101)
    episode(inv, which=2, tmdb_episode_id=101)
    episode(inv, number=3, tmdb_episode_id=103, missing=1)
    episode(inv, number=4, tmdb_episode_id=104, local_only=1)
    ctx = tv_collection.Collection(inv["shows"][0])
    assert {s["episode_id"] for s in ctx.episode_state(1, 1, 101)[1]} == {first, second, sibling}
    card = next(s for s in ctx.season_cards() if s["season"] == 1)
    assert card["local_count"] == 3 and card["collected_count"] == 0
    assert ctx.episode_state(1, 2, 102)[0] == "uncertain"
    assert ctx.episode_state(1, 3, 103)[0] == "uncertain"
    assert ctx.episode_state(1, 4, 104)[0] == "uncertain"


@pytest.mark.parametrize("flag", ["needs_review", "binding_conflict"])
def test_explicit_review_stays_in_affected_known_seasons(inventory, flag):
    inv = inventory
    episode(inv, tmdb_episode_id=101, **{flag: 1})
    episode(inv, which=1, season=2, tmdb_episode_id=201)
    ctx = tv_collection.Collection(inv["shows"][0])
    cards = {s["season"]: s for s in ctx.season_cards()}
    assert cards[1]["collection_reason"] == "match_review"
    assert cards[2]["collection_state"] == "collected"
    assert cards[2]["collection_reason"] == "" and cards[2]["collected_count"] == 1
    assert ctx.episode_state(1, 1, 101)[0] == "uncertain"
    result = tv_collection.catalog_result(inv["shows"][0], 1, {"detail": inv["catalogs"][1]})
    assert result["items"][0]["collection_reason"] == "match_review"
    assert ctx.summary()["latest_episode"]["collection_reason"] == "match_review"


def test_review_with_cold_identity_does_not_label_other_seasons_for_review(inventory):
    inv = inventory
    inv["catalogs"].pop(1)
    episode(inv, tmdb_episode_id=101, needs_review=1)
    episode(inv, season=2, tmdb_episode_id=201)
    ctx = tv_collection.Collection(inv["shows"][0])
    cards = {s["season"]: s for s in ctx.season_cards()}
    assert cards[1]["collection_reason"] == "match_review"
    assert cards[2]["collection_reason"] == "catalog_missing"
    assert cards[2]["collected_count"] == 1 and cards[2]["local_count"] == 1
    result = tv_collection.catalog_result(inv["shows"][0], 2, {"detail": inv["catalogs"][2]})
    assert result["items"][0]["collection_state"] == "collected"
    assert result["items"][0]["collection_reason"] == ""
    assert result["items"][1]["collection_reason"] == "catalog_missing"


def test_reviewed_cross_season_range_preserves_neutral_coverage_guard(inventory):
    inv = inventory
    episode(inv, season=8, number=20, episode_end=22, tmdb_episode_id=101,
            match_source="manual", needs_review=1)
    ctx = tv_collection.Collection(inv["shows"][0])
    cards = {s["season"]: s for s in ctx.season_cards()}
    assert cards[8]["collection_reason"] == cards[1]["collection_reason"] == "match_review"
    assert cards[2]["collection_reason"] == "coverage_incomplete"
    assert ctx.summary()["missing_seasons"] == []


def test_cold_range_confirms_only_its_first_identity(inventory):
    inv = inventory
    inv["catalogs"].clear()
    episode(inv, number=4, episode_end=6, tmdb_episode_id=104)
    ctx = tv_collection.Collection(inv["shows"][0])
    assert ctx.episode_state(1, 4, 104)[0] == "collected"
    assert ctx.episode_state(1, 5, 105)[0] == "uncertain"
    card = next(s for s in ctx.season_cards() if s["season"] == 1)
    assert card["collected_count"] == 1 and card["local_count"] == 3
    assert card["collection_reason"] == "catalog_missing"


def test_cold_identity_suppresses_missing_recommendations(inventory, monkeypatch):
    inv = inventory
    inv["catalogs"].clear()
    episode(inv, tmdb_episode_id=101)
    event = {"event_id": "fixture:new", "tmdb_id": inv["tid"], "season": 1,
             "episode": 4, "tmdb_episode_id": 104, "title": "终章",
             "air_date": inv["detail"]["last_episode_to_air"]["air_date"], "discovered_at": 123}
    monkeypatch.setattr(tv_airing, "list_events", lambda tids: [event])
    assert tv_collection.updates(inv["media"]["id"])["items"] == []


def test_unresolved_absolute_number_only_marks_its_local_season(inventory):
    inv = inventory
    episode(inv, number=20, absolute_number=20)
    cards = {s["season"]: s for s in tv_collection.Collection(inv["shows"][0]).season_cards()}
    assert cards[1]["collection_reason"] == "numbering_unresolved"
    assert cards[2]["collection_state"] == "uncertain"
    assert cards[2]["collection_reason"] == "coverage_incomplete"


def test_unofficial_local_season_does_not_claim_official_collection(inventory):
    inv = inventory
    episode(inv, season=8, number=20, tmdb_episode_id=101, match_source="manual")
    cards = {s["season"]: s for s in tv_collection.Collection(inv["shows"][0]).season_cards()}
    assert cards[1]["collected_count"] == 1 and cards[1]["local_count"] == 0
    assert cards[8]["collection_state"] == "collected" and cards[8]["collection_reason"] == ""
    assert cards[8]["local_count"] == 1 and cards[8]["collected_count"] == 0


def test_local_only_season_does_not_request_nonexistent_catalog(inventory, monkeypatch):
    inv = inventory
    episode(inv, season=8, number=20, tmdb_episode_id=101, match_source="manual")
    def forbidden(*args, **kwargs):
        raise AssertionError("unofficial season must not request TMDB")
    monkeypatch.setattr(tv_airing, "get_catalog", forbidden)
    assert inv["client"].get(f"/api/tv/shows/{inv['shows'][0]}/seasons/8/catalog").status_code == 404


def test_latest_catalog_identity_overrides_old_event_coordinate(inventory):
    inv = inventory
    episode(inv, season=8, number=20, tmdb_episode_id=101, match_source="manual")
    ctx = tv_collection.Collection(inv["shows"][0], extra_episodes=[{
        "tmdb_episode_id": 101, "season": 2, "episode": 1, "air_date": "2020-01-01"}])
    assert ctx.episode_state(1, 1, 101)[1][0]["season"] == 8
    assert ctx.episode_state(2, 1, 201)[0] == "uncollected"


def test_poster_uses_trusted_path_and_local_cache(inventory, monkeypatch, tmp_path):
    from app import posters, tmdb
    inv = inventory
    inv["detail"]["seasons"][1]["poster_path"] = "/official-season.jpg"
    image = tmp_path / "season.jpg"
    image.write_bytes(b"cached poster")
    monkeypatch.setattr(posters, "resolve", lambda *args: str(image))
    def forbidden(*args, **kwargs):
        raise AssertionError("cached or invalid poster must not download")
    monkeypatch.setattr(tmdb, "download_image", forbidden)
    url = f"/api/tv/shows/{inv['shows'][0]}/seasons/1/poster"
    assert inv["client"].get(url, params={"tmdb_id": inv["tid"]}).content == b"cached poster"
    assert inv["client"].get(url, params={"tmdb_id": inv["tid"] + 1}).status_code == 404
    inv["detail"]["seasons"][1]["poster_path"] = "https://untrusted.invalid/poster.jpg"
    assert inv["client"].get(url, params={"tmdb_id": inv["tid"]}).status_code == 404


def test_manual_check_only_queues_confirmed_identity(inventory, monkeypatch):
    calls = []
    def queue(tids):
        calls.append(tids)
        return {"status": "queued", "running": False, "pending": 1}
    monkeypatch.setattr(tv_airing, "request_check", queue)
    inv = inventory
    result = inv["client"].post("/api/tv/airing/check", json={"show_id": inv["shows"][0]})
    assert result.status_code == 200 and result.json()["status"] == "queued"
    assert calls == [[inv["tid"]]]
    store.update_show_meta(inv["shows"][0], needs_review=1)
    assert inv["client"].post("/api/tv/airing/check", json={"show_id": inv["shows"][0]}).status_code == 422
    assert len(calls) == 1
