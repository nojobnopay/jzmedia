"""Safety evidence survives metadata caching, offline refresh and NFO exchange."""
import json
import xml.etree.ElementTree as ET

import pytest

from app import nfo, store
from app.content_rules import evaluate_content, normalize_nfo_evidence
from app.metadata import external, nfo_import
from app.scanner.match import meta_from_detail
from app.scanner.tv_match import tv_meta_from_detail


@pytest.fixture()
def cache_id():
    tid = 99003901
    yield tid
    with store._lock, store._conn() as connection:
        connection.execute("DELETE FROM tmdb_cache WHERE tmdb_id=?", (tid,))
    store.delete_match_entries("tmdb", str(tid))
    store.delete_match_entries("tmdb", f"tv:{tid}")


def movie_detail(tid, certification="G", keywords=None):
    return {"id": tid, "title": "Content fixture", "adult": False, "genres": [],
            "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [
                {"certification": certification, "type": 3}, {"certification": certification, "type": 5}]}]},
            "keywords": {"keywords": [{"name": k} for k in keywords or []]}}


def test_evidence_only_cache_change_is_not_skipped(cache_id):
    initial = meta_from_detail(movie_detail(cache_id))
    assert store.upsert_tmdb_cache(cache_id, initial)
    assert not store.upsert_tmdb_cache(cache_id, initial)
    changed = meta_from_detail(movie_detail(cache_id, "R"))
    assert store.upsert_tmdb_cache(cache_id, changed)
    payload = json.loads(store.get_tmdb_cached(cache_id)["payload_json"])
    assert evaluate_content("movie", payload["content_evidence"])[0] == "block"
    assert not store.upsert_tmdb_cache(cache_id, changed)


def test_partial_refresh_preserves_previous_successful_risk_sections(cache_id):
    initial = meta_from_detail(movie_detail(cache_id, keywords=["violence"]))
    store.upsert_tmdb_cache(cache_id, initial)
    partial = movie_detail(cache_id)
    partial["keywords"] = {"success": False, "status_code": 34}
    assert not store.upsert_tmdb_cache(cache_id, meta_from_detail(partial))
    cached = json.loads(store.get_tmdb_cached(cache_id)["payload_json"])["content_evidence"]
    assert cached["keywords"] == ["violence"]
    assert evaluate_content("movie", cached)[0] == "block"


def test_cache_copy_propagates_evidence_fields(cache_id, monkeypatch):
    store.upsert_tmdb_cache(cache_id, meta_from_detail(movie_detail(cache_id)))
    mid = store.upsert_movie_by_path("content-fixture/metadata.mkv")
    store.update_movie_meta(mid, tmdb_id=cache_id)
    captured = []
    monkeypatch.setattr("app.store.tmdb_cache.update_movie_meta", lambda mid, **fields: captured.append(fields))
    try:
        assert store.copy_tmdb_to_movie(mid)
        assert captured[-1]["children_auto_status"] == "allow"
        assert captured[-1]["content_evidence"]["source_id"] == str(cache_id)
        assert "is_private" not in captured[-1] and "children_override" not in captured[-1]
    finally:
        store.delete_movie(mid)


def test_tv_normalizer_retains_ratings_and_risk_keywords(cache_id):
    detail = {"id": cache_id, "name": "Child fixture", "adult": False,
              "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-Y"}]},
              "keywords": {"results": [{"name": "cartoon violence"}]}}
    meta = tv_meta_from_detail(detail)
    assert evaluate_content("tv", meta["content_evidence"])[0] == "block"
    store.upsert_tmdb_cache(cache_id, meta, media_type="tv")
    payload = json.loads(store.get_tmdb_cached(cache_id, "tv")["payload_json"])
    assert payload["content_evidence"] == meta["content_evidence"]


@pytest.mark.parametrize("kind,renderer,parser", [
    ("movie", nfo.render_movie_nfo_bytes, nfo_import.parse_movie_bytes),
    ("tv", nfo.render_tvshow_nfo_bytes, nfo_import.parse_tvshow_bytes),
])
def test_nfo_roundtrip_keeps_country_and_every_conflicting_rating(kind, renderer, parser):
    ratings = "US:TV-Y / US:TV-14" if kind == "tv" else "US:G / GB:15"
    row = {"title": "Evidence exchange", "content_evidence": normalize_nfo_evidence(ratings),
           "is_private": 1, "children_override": "allow", "password": "not-exported"}
    rendered = renderer(row)
    assert ET.fromstring(rendered).findtext("mpaa")
    assert b"not-exported" not in rendered and b"is_private" not in rendered
    parsed = parser(rendered)
    assert evaluate_content(kind, parsed["content_evidence"])[0] == "block"
    detail = external.detail_from_nfo_movie(parsed) if kind == "movie" else external.detail_from_nfo_tvshow(parsed)
    assert detail["content_evidence"]["certifications"] == row["content_evidence"]["certifications"]


def test_episode_nfo_roundtrip_retains_its_own_rating():
    episode = {"title": "Different episode", "season": 1, "episode": 2,
               "content_evidence": normalize_nfo_evidence("US:TV-MA"), "is_private": 1}
    show = {"title": "General show", "content_evidence": normalize_nfo_evidence("US:TV-G")}
    rendered = nfo.render_episode_nfo_bytes(show, episode)
    assert ET.fromstring(rendered).findtext("mpaa") == "US:TV-MA"
    assert b"is_private" not in rendered
    parsed = nfo_import.parse_episode_bytes(rendered)
    assert evaluate_content("episode", parsed["content_evidence"])[0] == "block"
