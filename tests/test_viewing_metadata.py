"""Stored policy survives rescans, new versions and corrected metadata identities."""
import json

import pytest

from app import store
from app.content_rules import normalize_content_evidence, normalize_nfo_evidence
from app.metadata import external, nfo_import
from app.scanner import persist, tv_persist
from app.scanner.match import meta_from_detail

from test_viewing_catalog import catalog as catalog_fixture, mode, policy, request_ok

catalog = catalog_fixture


def test_privacy_and_children_choices_inherit_to_same_library_versions(catalog):
    result = policy(catalog, "movie", [catalog["Lantern"]], is_private=True, children_override="block")
    assert result["updated"] == 2
    for key in ("Lantern", "LanternCopy"):
        row = store.get_movie(catalog[key])
        assert row["is_private"] == 1 and row["children_override"] == "block"
    new_id = store.upsert_movie_by_path("new-lantern.mkv", catalog["movies"])
    store.update_movie_meta(new_id, tmdb_id=catalog["Lantern_tid"], title="New version")
    new = store.get_movie(new_id)
    assert new["is_private"] == 1 and new["children_override"] == "block"
    other_id = store.upsert_movie_by_path("other-lantern.mkv", catalog["other"])
    store.update_movie_meta(other_id, tmdb_id=catalog["Lantern_tid"], title="Other library")
    other = store.get_movie(other_id)
    assert other["is_private"] == 0 and other["children_override"] == "auto"
    mode(catalog, "private")
    detail = request_ok(catalog["client"], "get", f"/api/movies/{new_id}")
    assert {v["id"] for v in detail["versions"]} == {catalog["Lantern"], catalog["LanternCopy"], new_id}


def test_rebinding_private_file_preserves_local_choice_and_clears_old_auto_evidence(catalog):
    mid = catalog["Vault"]
    policy(catalog, "movie", [mid], children_override="block")
    assert store.get_movie(mid)["children_auto_status"] == "allow"
    store.update_movie_meta(mid, tmdb_id=catalog["Vault_tid"] + 500, title="Corrected identity")
    row = store.get_movie(mid)
    assert row["is_private"] == 1 and row["children_override"] == "block"
    assert row["children_auto_status"] == "unknown"
    store.update_movie_meta(mid, content_evidence=normalize_nfo_evidence("US:R"))
    row = store.get_movie(mid)
    assert row["children_auto_status"] == "block" and row["is_private"] == 1


def test_changing_genres_rechecks_existing_evidence_without_losing_it(catalog):
    mid = catalog["Lantern"]
    before = store.get_movie(mid)["content_evidence"]
    store.update_movie_meta(mid, genres=["家庭"], genre_ids=[10751])
    row = store.get_movie(mid)
    assert row["content_evidence"] == before and row["children_auto_status"] == "allow"
    store.update_movie_meta(mid, genres=["动作"], genre_ids=[28])
    assert store.get_movie(mid)["children_auto_status"] == "block"
    sid = catalog["Meadow"]
    before = store.get_show_meta(sid)["content_evidence"]
    store.update_show_meta(sid, genres=["儿童"], genre_ids=[10762])
    row = store.get_show_meta(sid)
    assert row["content_evidence"] == before and row["children_auto_status"] == "allow"


def test_evidence_only_refresh_reaches_all_versions_without_overwriting_policy(catalog, monkeypatch):
    tid = catalog["Lantern_tid"]
    detail = {"id": tid, "title": "Refreshed", "adult": False, "genres": [],
              "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [{"certification": "G"}]}]},
              "keywords": {"keywords": []}}
    store.upsert_tmdb_cache(tid, meta_from_detail(detail))
    catalog["cache"].append((tid, "movie"))
    for mid in (catalog["Lantern"], catalog["LanternCopy"]):
        store.copy_tmdb_to_movie(mid)
    policy(catalog, "movie", [catalog["Lantern"]], children_override="allow", is_private=True)
    detail["keywords"] = {"keywords": [{"name": "violence"}]}
    monkeypatch.setattr(persist.tmdb, "movie_detail", lambda _: detail)
    result, _jobs = persist.refresh_tmdb_id_fast(tid)
    assert result["changed"] is True
    assert set(result["affected_ids"]) == {catalog["Lantern"], catalog["LanternCopy"]}
    for mid in result["affected_ids"]:
        row = store.get_movie(mid)
        assert row["children_auto_status"] == "block"
        assert row["is_private"] == 1 and row["children_override"] == "allow"
    detail.pop("keywords")
    second, _jobs = persist.refresh_tmdb_id_fast(tid)
    assert second["changed"] is False
    assert store.get_movie(catalog["Lantern"])["children_auto_status"] == "block"


def test_tv_refresh_preserves_private_choice_and_partial_evidence(catalog):
    sid = catalog["Archive"]
    tid = store.get_show_meta(sid)["tmdb_id"]
    detail = {"id": tid, "name": "Archive fixture", "adult": False,
              "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-Y"}]},
              "keywords": {"results": []}}
    catalog["cache"].append((tid, "tv"))
    tv_persist.apply_tv_detail(sid, detail, download_art=False, library_id=catalog["tv"])
    row = store.get_show_meta(sid)
    assert row["children_auto_status"] == "allow" and row["is_private"] == 1
    detail["keywords"] = {"results": [{"name": "violent content"}]}
    tv_persist.apply_tv_detail(sid, detail, download_art=False, library_id=catalog["tv"])
    assert store.get_show_meta(sid)["children_auto_status"] == "block"
    detail.pop("keywords")
    tv_persist.apply_tv_detail(sid, detail, download_art=False, library_id=catalog["tv"])
    row = store.get_show_meta(sid)
    assert row["children_auto_status"] == "block" and row["is_private"] == 1
    evidence = row["content_evidence"]
    evidence = json.loads(evidence) if isinstance(evidence, str) else evidence
    assert evidence["keywords"] == ["violent content"]


def test_restricted_metadata_never_automatically_marks_private(catalog):
    mid = catalog["Ember"]
    row = store.get_movie(mid)
    assert row["children_auto_status"] == "block" and row["is_private"] == 0
    mode(catalog, "private")
    assert catalog["client"].get(f"/api/movies/{mid}").status_code == 404


def test_explicit_episode_rating_vetoes_show_and_survives_partial_nfo(catalog):
    sid = catalog["Meadow"]
    eid = catalog["Meadow_episode"]
    assert store.get_show_meta(sid)["children_auto_status"] == "allow"
    parsed = nfo_import.parse_episode_bytes(
        b"<episodedetails><title>Episode title</title><season>1</season><episode>1</episode>"
        b"<mpaa>US:TV-MA</mpaa></episodedetails>")
    assert external._fill_episode(store.get_episode(eid), parsed, "nfo")
    assert store.get_episode(eid)["children_auto_status"] == "block"
    assert store.get_show_meta(sid)["children_auto_status"] == "block"
    # The normal show refresh cannot overwrite an explicit episode warning.
    store.update_show_meta(sid, content_evidence=normalize_nfo_evidence("US:TV-G"))
    assert store.get_show_meta(sid)["children_auto_status"] == "block"
    missing = nfo_import.parse_episode_bytes(
        b"<episodedetails><title>Episode title</title><episode>1</episode></episodedetails>")
    external._fill_episode(store.get_episode(eid), missing, "nfo")
    assert store.get_episode(eid)["children_auto_status"] == "block"
    mode(catalog, "children")
    assert catalog["client"].get(f"/api/tv/shows/{sid}").status_code == 404
    mode(catalog, "normal")
    policy(catalog, "show", [sid], children_override="allow")
    mode(catalog, "children")
    assert catalog["client"].get(f"/api/tv/episodes/{eid}").status_code == 200


def test_correcting_episode_rating_recomputes_show_but_unknown_episodes_do_not_block(catalog):
    sid = catalog["Meadow"]
    eid = catalog["Meadow_episode"]
    store.update_episode_meta(eid, content_evidence=normalize_nfo_evidence("US:TV-MA"))
    assert store.get_show_meta(sid)["children_auto_status"] == "block"
    store.update_episode_meta(eid, content_evidence=normalize_nfo_evidence("US:TV-G"))
    assert store.get_show_meta(sid)["children_auto_status"] == "allow"
    store.update_episode_meta(eid, content_evidence={})
    assert store.get_episode(eid)["children_auto_status"] == "unknown"
    assert store.get_show_meta(sid)["children_auto_status"] == "allow"


def test_show_rematch_clears_old_episode_evidence_without_changing_local_choices_or_display(catalog):
    sid, eid = catalog["Archive"], catalog["Archive_episode"]
    policy(catalog, "show", [sid], children_override="allow")
    store.update_show_meta(sid, genres=["动作"], genre_ids=[28])
    store.update_episode_meta(eid, tmdb_episode_id=80101, title="Keep episode title",
                              overview="Keep episode description", content_evidence=normalize_nfo_evidence("US:TV-MA"))
    second = store.upsert_episode(sid, catalog["tv"], "another-archive.mkv", 1, 2, title="Second local episode")
    store.update_episode_meta(second, content_evidence=normalize_nfo_evidence("US:TV-G"))
    store.update_episode_meta(catalog["Meadow_episode"], content_evidence=normalize_nfo_evidence("US:TV-MA"))
    before = store.get_show_meta(sid)
    episode_before = store.get_episode(eid)
    store.update_show_meta(sid, tmdb_id=before["tmdb_id"] + 10000)
    after = store.get_show_meta(sid)
    assert after["children_auto_status"] == "unknown"
    assert not normalize_content_evidence(after["content_evidence"])["available"]
    for key in ("title", "overview", "genres", "genre_ids", "is_private", "children_override"):
        assert after[key] == before[key]
    for item_id in (eid, second):
        episode = store.get_episode(item_id)
        assert episode["children_auto_status"] == "unknown"
        assert not normalize_content_evidence(episode["content_evidence"])["available"]
        assert "TV-MA" not in episode["children_reason"]
    episode_after = store.get_episode(eid)
    for key in ("title", "overview", "tmdb_episode_id", "season", "episode", "file_path"):
        assert episode_after[key] == episode_before[key]
    assert store.get_episode(catalog["Meadow_episode"])["children_auto_status"] == "block"


def test_show_rematch_accepts_new_evidence_without_old_episode_veto(catalog):
    sid, eid = catalog["Meadow"], catalog["Meadow_episode"]
    store.update_episode_meta(eid, content_evidence=normalize_nfo_evidence("US:TV-MA"))
    old = store.get_show_meta(sid)
    assert old["children_auto_status"] == "block"
    store.update_show_meta(sid, tmdb_id=old["tmdb_id"] + 10000,
                           content_evidence=normalize_nfo_evidence("US:TV-Y"))
    assert store.get_show_meta(sid)["children_auto_status"] == "allow"
    assert store.get_episode(eid)["children_auto_status"] == "unknown"
    mode(catalog, "children")
    assert catalog["client"].get(f"/api/tv/episodes/{eid}").status_code == 200


def test_same_show_and_episode_identity_refresh_preserves_episode_risk(catalog):
    sid, eid = catalog["Meadow"], catalog["Meadow_episode"]
    store.update_episode_meta(eid, tmdb_episode_id=80201, content_evidence=normalize_nfo_evidence("US:TV-MA"))
    before = store.get_episode(eid)
    tid = store.get_show_meta(sid)["tmdb_id"]
    store.update_show_meta(sid, tmdb_id=tid, content_evidence=normalize_nfo_evidence("US:TV-G"))
    store.update_episode_meta(eid, tmdb_episode_id=80201, title="Refreshed same episode")
    assert store.get_episode(eid)["content_evidence"] == before["content_evidence"]
    assert store.get_episode(eid)["children_auto_status"] == "block"
    assert store.get_show_meta(sid)["children_auto_status"] == "block"


@pytest.mark.parametrize("new_identity", [80302, None])
def test_episode_rematch_without_new_evidence_resets_old_rating_and_rechecks_parent(catalog, new_identity):
    sid, eid = catalog["Meadow"], catalog["Meadow_episode"]
    store.update_episode_meta(eid, tmdb_episode_id=80301, title="Keep local title",
                              content_evidence=normalize_nfo_evidence("US:TV-MA"))
    assert store.get_show_meta(sid)["children_auto_status"] == "block"
    mode(catalog, "children")
    assert catalog["client"].get(f"/api/tv/shows/{sid}").status_code == 404
    store.update_episode_meta(eid, tmdb_episode_id=new_identity)
    episode = store.get_episode(eid)
    assert episode["children_auto_status"] == "unknown"
    assert not normalize_content_evidence(episode["content_evidence"])["available"]
    assert episode["title"] == "Keep local title"
    assert store.get_show_meta(sid)["children_auto_status"] == "allow"
    assert catalog["client"].get(f"/api/tv/episodes/{eid}").status_code == 200


def test_episode_rematch_uses_new_evidence_and_keeps_other_episode_veto(catalog):
    sid, eid = catalog["Meadow"], catalog["Meadow_episode"]
    store.update_episode_meta(eid, tmdb_episode_id=80401, content_evidence=normalize_nfo_evidence("US:TV-MA"))
    other = store.upsert_episode(sid, catalog["tv"], "other-risk-episode.mkv", 1, 2, title="Other episode")
    store.update_episode_meta(other, content_evidence=normalize_nfo_evidence("US:TV-14"))
    store.update_episode_meta(eid, tmdb_episode_id=80402, content_evidence=normalize_nfo_evidence("US:TV-G"))
    assert store.get_episode(eid)["children_auto_status"] == "allow"
    assert store.get_episode(other)["children_auto_status"] == "block"
    assert store.get_show_meta(sid)["children_auto_status"] == "block"
