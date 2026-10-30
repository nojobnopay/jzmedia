"""Saved record artwork is private metadata, never an upstream lookup on history reads."""

from contextlib import closing
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest

from app import store, viewing
from app.online import artwork, service
from app.online.models import OnlinePlaybackIdentity
from app.store import online, online_artwork, online_network, online_state
import test_online_catalog


environment = test_online_catalog.environment
get_detail = test_online_catalog.get_detail


URL = "https://cdn.example/poster.jpg?signature=private-poster"


def access(environment):
    session, _, expired = viewing.resolve(environment.client.cookies.get(viewing.COOKIE))
    assert not expired
    with closing(store._base._conn()) as connection:
        revision = connection.execute("SELECT revision FROM protection_settings WHERE id=1").fetchone()[0]
    return service.PlaybackAccess(session["token_hash"], session["generation"], revision)


def favorite(environment, item_id):
    path = "/api/online/state/items/" + item_id
    state = environment.client.get(path)
    assert state.status_code == 200, state.text
    response = environment.client.put(path + "/favorite", json={"favorite": True},
        headers={"X-Online-State-Scope": state.json()["scope_token"]})
    assert response.status_code == 200, response.text


def records(environment):
    response = environment.client.get("/api/online/state/library?section=favorites")
    assert response.status_code == 200, response.text
    assert "private-poster" not in response.text and "cdn.example" not in response.text
    assert "poster_url" not in response.text and "configuration_fingerprint" not in response.text
    return response.json()["items"]


def saved(item_id):
    with closing(store._base._conn()) as connection:
        return dict(connection.execute("SELECT * FROM online_item_artwork WHERE item_id=?", (item_id,)).fetchone())


def prepare(environment):
    environment.movie["vod_pic"] = URL
    response = get_detail(environment)
    assert response.status_code == 200, response.text
    detail = response.json()
    favorite(environment, detail["item"]["id"])
    return detail


def seed_progress(environment, detail):
    selected = access(environment)
    identity = OnlinePlaybackIdentity.model_validate(detail["lines"][0]["entries"][0]["identity"])
    current = online_state.item_state(selected, identity.item_id)
    session = SimpleNamespace(id="isolated-artwork-progress-session", owner=selected.owner,
                              context=selected, identity=identity, cancelled=Event())
    writer = online_state.claim(selected, current["scope_token"], session, 0)
    online_state.save_progress(selected, writer["scope_token"], {
        "writer_id": writer["writer_id"], "writer_revision": writer["writer_revision"], "seq": 1,
        "position": 12, "duration": 24, "paused": True, "completed": False,
    })
    return selected


def test_detail_remembers_private_poster_and_record_reads_renew_leases_without_provider(environment, monkeypatch):
    detail = prepare(environment)
    snapshot = saved(detail["item"]["id"])
    assert snapshot["poster_url"] == URL and len(snapshot["configuration_fingerprint"]) == 64
    assert records(environment)[0]["poster_path"] == detail["poster_path"]
    calls = list(environment.requests)
    monkeypatch.setattr(artwork, "registry", artwork.ArtworkRegistry())
    row = records(environment)[0]
    assert row["poster_path"] and row["poster_path"] != detail["poster_path"]
    assert row["episode_label"] == ""
    assert environment.requests == calls, "A restart or record read must not refetch catalogue details"
    lease = artwork.registry.lookup(environment.source["id"], row["poster_path"].rsplit("/", 1)[1])
    assert lease.url == URL


@pytest.mark.parametrize("url", ["", "file:///private", "https://unlisted.example/p.jpg",
    "https://user:password@cdn.example/p.jpg", "https://cdn.example/p.jpg#fragment", "https://cdn.example/\n"])
def test_missing_or_disallowed_poster_is_a_placeholder_not_a_detail_failure(environment, url):
    detail = prepare(environment)
    environment.movie["vod_pic"] = url
    response = get_detail(environment)
    assert response.status_code == 200, response.text
    assert response.json()["poster_path"] == ""
    assert saved(detail["item"]["id"])["poster_url"] == ""
    assert records(environment)[0]["poster_path"] == ""


def test_legacy_favorite_has_empty_presentation_until_user_opens_detail(environment):
    item = online.upsert_online_item(environment.source["id"], "movie-1", "movie", "Earlier title")
    favorite(environment, item["id"])
    assert records(environment)[0]["poster_path"] == ""
    assert records(environment)[0]["episode_label"] == ""
    assert environment.requests == []
    environment.movie["vod_pic"] = URL
    response = get_detail(environment)
    assert response.json()["item"]["id"] == item["id"]
    assert records(environment)[0]["poster_path"]


def test_changed_network_invalidates_snapshot_and_late_old_write_cannot_overwrite_new(environment):
    detail = prepare(environment)
    item_id = detail["item"]["id"]
    previous = saved(item_id)
    online_network.update_online_network_default({"mode": "direct"})
    assert records(environment)[0]["poster_path"] == ""
    assert len(environment.requests) == 1
    environment.movie["vod_pic"] = URL + "&revision=2"
    assert get_detail(environment).status_code == 200
    current = saved(item_id)
    assert current["configuration_fingerprint"] != previous["configuration_fingerprint"]
    assert online_artwork.remember(access(environment), environment.source, item_id, URL,
                                   previous["configuration_fingerprint"]) is False
    assert saved(item_id) == current
    assert records(environment)[0]["poster_path"]


def test_snapshot_cannot_be_promoted_to_configuration_changed_during_registration(environment, monkeypatch):
    prepare(environment)
    register = artwork.register_many
    def changed(source, urls, **kwargs):
        online_network.update_online_network_default({"mode": "direct"})
        return register(source, urls, **kwargs)
    monkeypatch.setattr(artwork, "register_many", changed)
    assert records(environment)[0]["poster_path"] == ""


def test_record_enrichment_rechecks_viewing_scope_after_registration(environment, monkeypatch):
    prepare(environment)
    owner = access(environment).owner
    register = artwork.register_many
    def revoked(source, urls, **kwargs):
        result = register(source, urls, **kwargs)
        with closing(store._base._conn()) as connection, connection:
            connection.execute("UPDATE viewing_sessions SET generation=generation+1 WHERE token_hash=?", (owner,))
        return result
    monkeypatch.setattr(artwork, "register_many", revoked)
    response = environment.client.get("/api/online/state/library?section=favorites")
    assert response.status_code == 401 and response.json()["code"] == "online_access_revoked"


@pytest.mark.parametrize("stage", ["snapshot_lookup", "registration"])
def test_record_read_restarts_once_after_history_is_cleared_during_enrichment(environment, monkeypatch, stage):
    detail = prepare(environment)
    selected = seed_progress(environment, detail)
    read, register = online_state.library, artwork.register_many
    snapshots, deletions = [], []

    def clear_history():
        deletions.append(online_state.delete_history(selected, snapshots[-1]["scope_token"], detail["item"]["id"]))

    def observed(*args, **kwargs):
        result = read(*args, **kwargs)
        snapshots.append(result)
        if len(snapshots) == 1 and stage == "snapshot_lookup":
            clear_history()
        return result

    def cleared(source, urls, **kwargs):
        result = register(source, urls, **kwargs)
        if not deletions:
            clear_history()
        return result

    monkeypatch.setattr(online_state, "library", observed)
    monkeypatch.setattr(artwork, "register_many", cleared)
    calls = list(environment.requests)
    response = environment.client.get("/api/online/state/library?section=history")
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(snapshots) == 2 and snapshots[0]["total"] == 1
    assert result["items"] == [] and result["total"] == 0
    assert len(deletions) == 1 and deletions[0]["deleted"] == 1
    assert result["scope_token"] == deletions[0]["scope_token"] != snapshots[0]["scope_token"]
    assert environment.requests == calls, "Retrying a record read never refetches a provider"


def test_record_read_does_not_retry_a_second_scope_conflict(environment, monkeypatch):
    detail = prepare(environment)
    selected = access(environment)
    read = online_state.library
    snapshots = []

    def repeatedly_cleared(*args, **kwargs):
        result = read(*args, **kwargs)
        snapshots.append(result)
        online_state.delete_history(selected, result["scope_token"], detail["item"]["id"])
        return result

    monkeypatch.setattr(online_state, "library", repeatedly_cleared)
    response = environment.client.get("/api/online/state/library?section=favorites")
    assert response.status_code == 409 and response.json()["code"] == "online_state_scope_changed"
    assert len(snapshots) == 2, "A racing epoch can cause only one additional read"


@pytest.mark.parametrize("stage", ["before_retry", "retry_transaction"])
def test_record_read_retry_preserves_access_revocation(environment, monkeypatch, stage):
    detail = prepare(environment)
    selected = access(environment)
    read, confirm = online_state.library, online_artwork.confirm_scope
    snapshots = []

    def revoke():
        with closing(store._base._conn()) as connection, connection:
            connection.execute("UPDATE viewing_sessions SET generation=generation+1 WHERE token_hash=?", (selected.owner,))

    def observed(*args, **kwargs):
        if snapshots and stage == "retry_transaction":
            revoke()
        result = read(*args, **kwargs)
        snapshots.append(result)
        return result

    def cleared(access_value, scope_token):
        if len(snapshots) == 1:
            online_state.delete_history(selected, scope_token, detail["item"]["id"])
        try:
            confirm(access_value, scope_token)
        except online_state.OnlineStateError:
            if stage == "before_retry":
                revoke()
            raise

    monkeypatch.setattr(online_state, "library", observed)
    monkeypatch.setattr(online_artwork, "confirm_scope", cleared)
    response = environment.client.get("/api/online/state/library?section=favorites")
    assert response.status_code == 401 and response.json()["code"] == "online_access_revoked"
    assert len(snapshots) == 1, "The retry cannot return rows under a revoked viewing identity"


def test_disabled_source_retains_record_but_does_not_register_poster(environment):
    prepare(environment)
    online.update_online_source(environment.source["id"], {"enabled": False})
    row = records(environment)[0]
    assert not row["available"] and row["poster_path"] == ""
    assert len(environment.requests) == 1


def test_optional_snapshot_storage_failure_does_not_block_detail(environment, monkeypatch):
    def broken(*_args, **_kwargs):
        raise sqlite3.OperationalError("isolated snapshot failure")
    monkeypatch.setattr(online_artwork, "remember", broken)
    assert get_detail(environment).status_code == 200


def test_snapshot_from_another_item_or_revoked_scope_is_not_accepted(environment):
    detail = prepare(environment)
    item_id = detail["item"]["id"]
    snapshot = saved(item_id)
    other = online.create_online_source({"name": "Other", "provider": "maccms",
        "api_base_url": "https://other.example/api/", "enabled": True})
    other_item = online.upsert_online_item(other["id"], "movie", "movie", "Other")
    assert not online_artwork.remember(access(environment), environment.source, other_item["id"], URL,
                                       snapshot["configuration_fingerprint"])
    selected = access(environment)
    with closing(store._base._conn()) as connection, connection:
        connection.execute("UPDATE viewing_sessions SET generation=generation+1 WHERE token_hash=?", (selected.owner,))
    with pytest.raises(service.PlaybackError, match="online_access_revoked"):
        artwork.remember_item(selected, environment.source, item_id, URL, snapshot["configuration_fingerprint"])
