"""Episode cartoon conflicts can use the current parent work's genre evidence."""
import json

import pytest

from app import store
from app.content_rules import normalize_nfo_evidence, normalize_tmdb_evidence
from app.routers import viewing_refresh
from test_viewing_catalog import catalog as catalog_fixture

catalog = catalog_fixture


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store._base.DB_PATH", str(tmp_path / "episode-context.db"))
    store.init_db()


def family_show(catalog):
    show_id = catalog["Meadow"]
    store.update_show_meta(show_id, genres=["动画", "儿童"], genre_ids=[16, 10762],
                           content_evidence=normalize_nfo_evidence("US:TV-Y7-FV"))
    return show_id, catalog["Meadow_episode"]


@pytest.mark.parametrize("genres,genre_ids", [(["动画"], [16]), (["儿童"], [10762])])
def test_cartoon_episode_uses_latest_parent_genres_and_rechecks_when_removed(catalog, genres, genre_ids):
    show_id, episode_id = family_show(catalog)
    store.update_episode_meta(episode_id, content_evidence=normalize_nfo_evidence("US:TV-G", ["cartoon violence"]))
    source = store.get_episode(episode_id)["content_evidence"]
    assert store.get_episode(episode_id)["children_auto_status"] == "allow"
    assert store.get_show_meta(show_id)["children_auto_status"] == "allow"
    store.update_show_meta(show_id, genres=genres, genre_ids=genre_ids)
    assert store.get_episode(episode_id)["children_auto_status"] == "block"
    assert store.get_show_meta(show_id)["children_auto_status"] == "block"
    assert store.get_episode(episode_id)["content_evidence"] == source
    store.update_show_meta(show_id, genres=["动画", "儿童"], genre_ids=[16, 10762])
    assert store.get_episode(episode_id)["children_auto_status"] == "allow"
    assert store.get_show_meta(show_id)["children_auto_status"] == "allow"
    assert store.get_episode(episode_id)["content_evidence"] == source


@pytest.mark.parametrize("rating,keywords,adult", [
    ("US:TV-14", [], False), ("US:TV-G", ["cartoon violence and gore"], False),
    ("US:TV-G", [], True),
])
def test_family_context_does_not_erase_episode_rating_adult_or_strong_risks(catalog, rating, keywords, adult):
    show_id, episode_id = family_show(catalog)
    evidence = normalize_nfo_evidence(rating, keywords)
    if adult:
        evidence.update(adult=True, available=[*evidence["available"], "adult"])
    store.update_episode_meta(episode_id, content_evidence=evidence)
    source = store.get_episode(episode_id)["content_evidence"]
    store.update_show_meta(show_id, content_evidence=normalize_nfo_evidence("US:TV-Y7-FV"))
    assert store.get_episode(episode_id)["children_auto_status"] == "block"
    assert store.get_show_meta(show_id)["children_auto_status"] == "block"
    assert store.get_episode(episode_id)["content_evidence"] == source


def tmdb_evidence(identifier, genre_ids):
    return normalize_tmdb_evidence("tv", {"id": identifier, "adult": False,
        "genres": [{"id": value} for value in genre_ids],
        "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-Y7-FV"}]},
        "keywords": {"results": []}})


def test_evidence_refresh_uses_new_parent_context_before_writing_parent(catalog):
    show_id, episode_id = catalog["Meadow"], catalog["Meadow_episode"]
    identifier = store.get_show_meta(show_id)["tmdb_id"]
    store.update_show_meta(show_id, content_evidence=tmdb_evidence(identifier, []))
    store.update_episode_meta(episode_id, content_evidence=normalize_nfo_evidence("US:TV-G", ["cartoon violence"]))
    source = store.get_episode(episode_id)["content_evidence"]
    assert store.get_show_meta(show_id)["children_auto_status"] == "block"
    assert viewing_refresh._save_evidence("tv", identifier, tmdb_evidence(identifier, [16, 10762])) == 1
    catalog["cache"].append((identifier, "tv"))
    assert store.get_episode(episode_id)["children_auto_status"] == "allow"
    assert store.get_show_meta(show_id)["children_auto_status"] == "allow"
    assert store.get_episode(episode_id)["content_evidence"] == source
    assert viewing_refresh._save_evidence("tv", identifier, tmdb_evidence(identifier, [16])) == 1
    assert store.get_episode(episode_id)["children_auto_status"] == "block"
    assert store.get_show_meta(show_id)["children_auto_status"] == "block"
    assert store.get_episode(episode_id)["content_evidence"] == source
    assert json.loads(source)["keywords"] == ["cartoon violence"]
