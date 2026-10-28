"""Persistent online state uses device/mode scopes and revocable write claims."""

from contextlib import closing
from types import SimpleNamespace
import time

from fastapi.testclient import TestClient
import pytest

from app import library_paths, store, viewing
from app.config import settings
from app.main import app
from app.network.targets import TargetPolicy
from app.online import service
from app.online.models import OnlinePlaybackIdentity
from app.online.sessions import SessionRegistry
from app.store import online, online_state as state


MAIN = "ln_" + "1" * 32
OTHER = "ln_" + "2" * 32
HEADER = "X-Online-State-Scope"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "online-state.db"))
    monkeypatch.setattr(store._base, "ensure_dirs", lambda: None)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    library_paths.invalidate_cache()
    store.init_db()
    source = online.create_online_source({"name": "Isolated source", "provider": "maccms",
        "api_base_url": "https://api.example/provide/vod/", "allowed_hosts": ["cdn.example"], "enabled": True})
    online.update_online_settings(True)
    movie = online.upsert_online_item(source["id"], "movie-1", "movie", "Online movie")
    show = online.upsert_online_item(source["id"], "show-1", "tv", "Online show")
    episodes = [online.upsert_online_episode(show["id"], "episode-" + str(index), "Episode " + str(index),
                                            sort_order=index) for index in (1, 2)]
    registry = SessionRegistry()
    monkeypatch.setattr(service, "registry", registry)
    candidate, stranger = TestClient(app), TestClient(app)
    assert candidate.get("/api/viewing/session").status_code == 200
    assert stranger.get("/api/viewing/session").status_code == 200
    env = SimpleNamespace(source=source, movie=movie, show=show, episodes=episodes,
                          client=candidate, stranger=stranger, registry=registry)

    def access(client=candidate):
        token = client.cookies.get(viewing.COOKIE)
        session, _, expired = viewing.resolve(token)
        assert not expired
        with closing(store._base._conn()) as connection:
            revision = connection.execute("SELECT revision FROM protection_settings WHERE id=1").fetchone()[0]
        return service.PlaybackAccess(session["token_hash"], session["generation"], revision)

    def scope(client=candidate, item=movie, **selection):
        response = client.get("/api/online/state/items/" + item["id"], params=selection)
        assert response.status_code == 200, response.text
        return response.json()

    def media(client=candidate, *, item=movie, episode=None, line=MAIN):
        selected = access(client)

        class Reader:
            def __init__(self):
                self.snapshot = SimpleNamespace(source_id=source["id"])
                self.targets = TargetPolicy(source["id"], ["cdn.example"], resolver=lambda *_args: ("8.8.8.8",))

            def close(self):
                pass

        identity = OnlinePlaybackIdentity(source_id=source["id"], item_id=item["id"],
            kind="episode" if episode else "movie", episode_id=episode["id"] if episode else None, line_id=line)
        return registry.create(identity, selected.owner, Reader(), "https://cdn.example/isolated.mp4", "mp4", context=selected)

    env.access, env.scope, env.media = access, scope, media
    try:
        yield env
    finally:
        registry.close_all()
        candidate.close()
        stranger.close()
        library_paths.invalidate_cache()


def claim(env, *, client=None, item=None, episode=None, line=MAIN, expected=None):
    client, item = client or env.client, item or env.movie
    selection = {"line_id": line}
    if episode:
        selection["episode_id"] = episode["id"]
    selected = env.scope(client, item, **selection)
    session = env.media(client, item=item, episode=episode, line=line)
    response = client.post("/api/online/state/progress/claim", headers={HEADER: selected["scope_token"]}, json={
        "session_id": session.id, "expected_revision": expected if expected is not None
        else (selected["selected_progress"] or {}).get("writer_revision", 0)})
    assert response.status_code == 200, response.text
    return response.json(), session


def packet(writer, **changes):
    return {"writer_id": writer["writer_id"], "writer_revision": writer["writer_revision"], "seq": 1,
            "position": 12, "duration": 24, "paused": False, "completed": False, **changes}


def save(env, writer, *, client=None, status=200, **changes):
    response = (client or env.client).post("/api/online/state/progress", json=packet(writer, **changes),
                                          headers={HEADER: writer["scope_token"]})
    assert response.status_code == status, response.text
    return response


def set_mode(env, mode, client=None):
    access = env.access(client or env.client)
    with closing(store._base._conn()) as connection, connection:
        connection.execute("UPDATE protection_settings SET password_hash='isolated-active-protection' WHERE id=1")
        connection.execute("UPDATE viewing_sessions SET mode=?,generation=generation+1,last_activity=? WHERE token_hash=?",
                           (mode, time.time(), access.owner))


def favorite(env, value=True, *, client=None, scope=None, status=200):
    client = client or env.client
    scope = scope or env.scope(client)["scope_token"]
    response = client.put("/api/online/state/items/" + env.movie["id"] + "/favorite",
                          json={"favorite": value}, headers={HEADER: scope})
    assert response.status_code == status, response.text
    return response


def test_cookie_is_required_even_without_protection(environment):
    anonymous = TestClient(app)
    try:
        for path in ("/api/online/state/library", "/api/online/state/items/" + environment.movie["id"]):
            response = anonymous.get(path)
            assert response.status_code == 401 and response.headers["cache-control"] == "no-store"
    finally:
        anonymous.close()


def test_favorites_are_device_scoped_idempotent_and_separate_from_local_media(environment):
    env = environment
    favorite(env)
    favorite(env)
    assert env.scope()["favorite"] is True
    assert env.scope(env.stranger)["favorite"] is False
    assert env.client.get("/api/online/state/library", params={"section": "favorites"}).json()["total"] == 1
    assert env.stranger.get("/api/online/state/library", params={"section": "favorites"}).json()["total"] == 0
    with closing(store._base._conn()) as connection:
        for table in ("movies", "tv_episodes", "playback_progress"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("operation", ["favorite", "delete", "claim", "progress"])
def test_mutations_require_a_frozen_scope_intent(environment, operation):
    env = environment
    writer, session = claim(env)
    if operation == "favorite":
        response = env.client.put("/api/online/state/items/" + env.movie["id"] + "/favorite", json={"favorite": True})
    elif operation == "delete":
        response = env.client.delete("/api/online/state/items/" + env.movie["id"] + "/history")
    elif operation == "claim":
        response = env.client.post("/api/online/state/progress/claim", json={"session_id": session.id, "expected_revision": 0})
    else:
        response = env.client.post("/api/online/state/progress", json=packet(writer))
    assert response.status_code == 409 and response.json()["code"] == "online_state_scope_changed"


def test_private_and_normal_records_survive_mode_changes_without_mixing(environment):
    env = environment
    normal_scope = env.scope()["scope_token"]
    normal_writer, _ = claim(env)
    save(env, normal_writer)
    favorite(env)
    set_mode(env, "private")
    assert env.scope()["favorite"] is False
    assert env.scope()["last_progress"] is None
    private_writer, _ = claim(env)
    save(env, private_writer, position=18)
    favorite(env)
    set_mode(env, "normal")
    assert env.scope()["favorite"] is True
    assert env.scope()["last_progress"]["position"] == 12
    assert env.scope()["scope_token"] != normal_scope
    set_mode(env, "private")
    assert env.scope()["last_progress"]["position"] == 18


@pytest.mark.parametrize("operation", ["favorite", "delete", "progress"])
def test_delayed_old_mode_writes_cannot_mutate_new_mode(environment, operation):
    env = environment
    writer, _ = claim(env)
    save(env, writer)
    old_scope = writer["scope_token"]
    set_mode(env, "private")
    if operation == "favorite":
        favorite(env, scope=old_scope, status=409)
    elif operation == "delete":
        response = env.client.delete("/api/online/state/items/" + env.movie["id"] + "/history",
                                     headers={HEADER: old_scope})
        assert response.status_code == 409 and response.json()["code"] == "online_state_scope_changed"
    else:
        response = save(env, writer, seq=2, position=20, status=409)
        assert response.json()["code"] == "online_state_scope_changed"
    assert env.scope()["favorite"] is False and env.scope()["last_progress"] is None


def test_scope_intent_changes_with_owner_even_when_generation_matches(environment):
    env = environment
    own, other = env.scope(), env.scope(env.stranger)
    assert own["generation"] == other["generation"] == 0
    assert own["scope_token"] != other["scope_token"]
    favorite(env, client=env.stranger, scope=own["scope_token"], status=409)


def test_children_cannot_list_write_or_read_even_with_previous_scope(environment):
    env = environment
    old = env.scope()["scope_token"]
    favorite(env)
    set_mode(env, "children")
    for method, path, kwargs in (
        ("GET", "/api/online/state/library", {}),
        ("GET", "/api/online/state/items/" + env.movie["id"], {}),
        ("PUT", "/api/online/state/items/" + env.movie["id"] + "/favorite", {"json": {"favorite": False}})):
        response = env.client.request(method, path, headers={HEADER: old}, **kwargs)
        assert response.status_code == 403 and response.json()["code"] == "online_children_unavailable"


def test_private_deadline_is_rechecked_inside_the_store_transaction(environment):
    env = environment
    set_mode(env, "private")
    access, intent = env.access(), env.scope()["scope_token"]
    with closing(store._base._conn()) as connection, connection:
        connection.execute("UPDATE viewing_sessions SET last_activity=0,playback_until=0 WHERE token_hash=?", (access.owner,))
    with pytest.raises(state.OnlineStateError) as caught:
        state.favorite(access, intent, env.movie["id"], True)
    assert caught.value.status == 401


def test_claim_does_not_create_recent_watching_or_reveal_the_writer_in_reads(environment):
    env = environment
    writer, _ = claim(env)
    assert writer["progress"] is None
    state_response = env.scope(line_id=MAIN)
    assert state_response["last_progress"] is None
    assert state_response["selected_progress"]["updated_at"] == 0
    assert state_response["selected_progress"]["writer_revision"] == 1
    assert env.client.get("/api/online/state/library", params={"section": "history"}).json()["total"] == 0
    save(env, writer)
    state_response = env.client.get("/api/online/state/items/" + env.movie["id"])
    assert writer["writer_id"] not in state_response.text and "cdn.example" not in state_response.text
    assert "owner_hash" not in state_response.text and "writer_session_id" not in state_response.text


def test_claim_retries_same_session_without_resetting_sequence_and_new_session_requires_cas(environment):
    env = environment
    writer, session = claim(env)
    save(env, writer)
    retried = env.client.post("/api/online/state/progress/claim", headers={HEADER: writer["scope_token"]},
                             json={"session_id": session.id, "expected_revision": 0})
    assert retried.status_code == 200 and retried.json()["writer_id"] == writer["writer_id"]
    assert retried.json()["progress"]["position"] == 12
    newer = env.media()
    conflict = env.client.post("/api/online/state/progress/claim", headers={HEADER: writer["scope_token"]},
                              json={"session_id": newer.id, "expected_revision": 0})
    assert conflict.status_code == 409 and conflict.json()["code"] == "online_progress_writer_conflict"
    accepted = env.client.post("/api/online/state/progress/claim", headers={HEADER: writer["scope_token"]},
                              json={"session_id": newer.id, "expected_revision": 1})
    assert accepted.status_code == 200 and accepted.json()["writer_revision"] == 2
    save(env, writer, seq=2, status=409)
    save(env, accepted.json(), position=4)


def test_progress_duplicate_is_idempotent_and_seq_allows_a_backward_seek(environment):
    env = environment
    writer, _ = claim(env)
    first = save(env, writer).json()["progress"]
    duplicate = save(env, writer).json()["progress"]
    assert first == duplicate
    save(env, writer, position=13, status=409)
    lowered = save(env, writer, seq=2, position=3, paused=True).json()["progress"]
    assert lowered["position"] == 3 and lowered["paused"] is True
    save(env, writer, seq=1, status=409)


def test_completion_keeps_actual_end_and_cannot_be_cleared_by_late_writes(environment):
    env = environment
    writer, _ = claim(env)
    save(env, writer, position=0, completed=True, status=422)
    finished = save(env, writer, position=24, paused=True, completed=True).json()["progress"]
    assert finished["position"] == finished["duration"] == 24 and finished["completed"] is True
    save(env, writer, seq=2, position=23, status=409)
    assert env.client.get("/api/online/state/library", params={"section": "continue"}).json()["total"] == 0
    assert env.client.get("/api/online/state/library", params={"section": "history"}).json()["total"] == 1
    env.registry.close_all()
    replay, _ = claim(env)
    assert replay["progress"]["completed"] is True
    assert save(env, replay, position=2).json()["progress"]["completed"] is False


def test_media_session_release_allows_final_write_but_writer_expiry_does_not(environment):
    env = environment
    writer, session = claim(env)
    env.registry.close(session.id, session.owner)
    save(env, writer, position=7, paused=True)
    with closing(store._base._conn()) as connection, connection:
        connection.execute("UPDATE online_progress SET writer_expires_at=0")
    save(env, writer, seq=2, position=8, status=409)


def test_active_writer_keeps_saving_beyond_two_hours_without_reclaiming(environment, monkeypatch):
    env = environment
    writer, _ = claim(env)
    started = writer["expires_at"] - state.WRITER_TTL_SECONDS
    clock = [started]
    monkeypatch.setattr(state, "time", SimpleNamespace(time=lambda: clock[0]))
    for seq in (1, 2, 3):
        elapsed = seq * (state.WRITER_TTL_SECONDS - 1)
        clock[0] = started + elapsed
        result = save(env, writer, seq=seq, position=elapsed, duration=4 * state.WRITER_TTL_SECONDS).json()
        assert result["progress"]["position"] == elapsed
        assert result["progress"]["writer_revision"] == writer["writer_revision"]
        with closing(store._base._conn()) as connection:
            row = connection.execute("SELECT writer_id,writer_expires_at FROM online_progress").fetchone()
        assert row["writer_id"] == writer["writer_id"]
        assert row["writer_expires_at"] == clock[0] + state.WRITER_TTL_SECONDS
    assert result["progress"]["updated_at"] > writer["expires_at"]


@pytest.mark.parametrize("seq", [1, 2])
def test_an_idle_expired_writer_cannot_revive_with_a_duplicate_or_new_packet(environment, monkeypatch, seq):
    env = environment
    writer, _ = claim(env)
    clock = [writer["expires_at"] - state.WRITER_TTL_SECONDS + 1]
    monkeypatch.setattr(state, "time", SimpleNamespace(time=lambda: clock[0]))
    save(env, writer)
    clock[0] += state.WRITER_TTL_SECONDS
    response = save(env, writer, seq=seq, status=409)
    assert response.json()["code"] == "online_progress_writer_conflict"
    with closing(store._base._conn()) as connection:
        row = connection.execute("SELECT position,writer_expires_at FROM online_progress").fetchone()
    assert row["position"] == 12 and row["writer_expires_at"] == clock[0]


def test_replaced_writer_cannot_extend_or_reactivate_its_lease(environment, monkeypatch):
    env = environment
    previous, _ = claim(env)
    current, _ = claim(env)
    clock = [current["expires_at"] - 1]
    monkeypatch.setattr(state, "time", SimpleNamespace(time=lambda: clock[0]))
    assert save(env, previous, status=409).json()["code"] == "online_progress_writer_conflict"
    with closing(store._base._conn()) as connection:
        assert connection.execute("SELECT writer_expires_at FROM online_progress").fetchone()[0] == current["expires_at"]
    save(env, current, position=18)
    clock[0] += 1
    save(env, previous, seq=2, status=409)
    selected = env.scope(line_id=MAIN)["selected_progress"]
    assert selected["position"] == 18 and selected["writer_revision"] == current["writer_revision"]


def test_history_clear_blocks_a_pending_initial_claim_and_returns_a_fresh_scope(environment):
    env = environment
    old_scope = env.scope()["scope_token"]
    session = env.media()
    clear = env.client.delete("/api/online/state/items/" + env.movie["id"] + "/history",
                              headers={HEADER: old_scope})
    assert clear.status_code == 200 and clear.json()["deleted"] == 0
    new_scope = clear.json()["scope_token"]
    assert new_scope != old_scope and new_scope == env.scope()["scope_token"]
    stale = env.client.post("/api/online/state/progress/claim", headers={HEADER: old_scope},
                            json={"session_id": session.id, "expected_revision": 0})
    assert stale.status_code == 409 and stale.json()["code"] == "online_state_scope_changed"
    assert env.scope(line_id=MAIN)["selected_progress"] is None
    fresh = env.client.post("/api/online/state/progress/claim", headers={HEADER: new_scope},
                            json={"session_id": session.id, "expected_revision": 0})
    assert fresh.status_code == 200 and fresh.json()["scope_token"] == new_scope


def test_history_clear_invalidates_other_old_writers_even_if_they_obtain_a_new_scope(environment):
    env = environment
    movie_writer, _ = claim(env)
    show_writer, _ = claim(env, item=env.show, episode=env.episodes[0])
    save(env, movie_writer)
    save(env, show_writer)
    clear = env.client.delete("/api/online/state/items/" + env.movie["id"] + "/history",
                              headers={HEADER: movie_writer["scope_token"]})
    assert clear.status_code == 200 and clear.json()["deleted"] == 1
    save(env, show_writer, seq=2, status=409)
    response = env.client.post("/api/online/state/progress", json=packet(show_writer, seq=2),
                               headers={HEADER: clear.json()["scope_token"]})
    assert response.status_code == 409 and response.json()["code"] == "online_progress_writer_conflict"
    assert env.scope(item=env.show)["last_progress"]["position"] == 12


def test_history_scope_epochs_are_independent_between_normal_and_private(environment):
    env = environment
    favorite(env)
    normal = env.scope()["scope_token"]
    first = env.client.delete("/api/online/state/items/" + env.movie["id"] + "/history", headers={HEADER: normal})
    assert first.status_code == 200
    set_mode(env, "private")
    private = env.scope()["scope_token"]
    second = env.client.delete("/api/online/state/items/" + env.movie["id"] + "/history", headers={HEADER: private})
    assert second.status_code == 200 and second.json()["scope_token"] != private
    with closing(store._base._conn()) as connection:
        rows = {row["scope"]: row["revision"] for row in connection.execute(
            "SELECT scope,revision FROM online_state_scopes WHERE owner_hash=?", (env.access().owner,))}
    assert rows == {"normal": 1, "private": 1}
    set_mode(env, "normal")
    assert env.scope()["favorite"] is True


def test_known_other_devices_session_cannot_be_claimed_or_written(environment):
    env = environment
    writer, session = claim(env)
    other_scope = env.scope(env.stranger)["scope_token"]
    response = env.stranger.post("/api/online/state/progress/claim", headers={HEADER: other_scope},
                                 json={"session_id": session.id, "expected_revision": 0})
    assert response.status_code == 404
    response = env.stranger.post("/api/online/state/progress", headers={HEADER: other_scope}, json=packet(writer))
    assert response.status_code == 409


def test_lines_and_episodes_have_separate_progress_but_item_library_deduplicates(environment):
    env = environment
    main, _ = claim(env)
    save(env, main, position=7)
    other, _ = claim(env, line=OTHER)
    save(env, other, position=13)
    assert env.scope(line_id=MAIN)["selected_progress"]["position"] == 7
    assert env.scope(line_id=OTHER)["selected_progress"]["position"] == 13
    env.registry.close_all()
    for index, episode in enumerate(env.episodes):
        writer, _ = claim(env, item=env.show, episode=episode)
        save(env, writer, position=4 + index)
    assert env.scope(item=env.show)["last_progress"]["identity"]["episode_id"] == env.episodes[1]["id"]
    assert env.client.get("/api/online/state/library", params={"section": "continue"}).json()["total"] == 2


@pytest.mark.parametrize("disable", ["source", "feature"])
def test_disabled_source_preserves_records_and_allows_cleanup_and_final_flush(environment, disable):
    env = environment
    writer, _ = claim(env)
    favorite(env)
    if disable == "source":
        online.update_online_source(env.source["id"], {"enabled": False})
    else:
        online.update_online_settings(False)
    save(env, writer, paused=True)
    selected = env.scope()
    assert selected["favorite"] is True and selected["available"] is False
    assert selected["last_progress"]["position"] == 12
    favorite(env, True, status=503)
    favorite(env, False)
    response = env.client.delete("/api/online/state/items/" + env.movie["id"] + "/history",
                                 headers={HEADER: selected["scope_token"]})
    assert response.status_code == 200 and response.json()["deleted"] == 1
    save(env, writer, seq=2, status=409)


def test_quota_rejects_new_favorites_without_deleting_existing_records(environment, monkeypatch):
    env = environment
    monkeypatch.setattr(state, "MAX_FAVORITES", 1)
    favorite(env)
    selected = env.scope(item=env.show)
    response = env.client.put("/api/online/state/items/" + env.show["id"] + "/favorite",
                              headers={HEADER: selected["scope_token"]}, json={"favorite": True})
    assert response.status_code == 429 and response.json()["code"] == "online_favorite_limit"
    assert env.scope()["favorite"] is True
    favorite(env)


def test_progress_quota_never_evicts_a_live_claim_or_saved_history(environment, monkeypatch):
    env = environment
    monkeypatch.setattr(state, "MAX_PROGRESS", 1)
    writer, _ = claim(env)
    other = env.media(line=OTHER)
    response = env.client.post("/api/online/state/progress/claim", headers={HEADER: writer["scope_token"]},
                              json={"session_id": other.id, "expected_revision": 0})
    assert response.status_code == 429 and response.json()["code"] == "online_progress_limit"
    save(env, writer)
    assert env.scope(line_id=MAIN)["selected_progress"]["position"] == 12


@pytest.mark.parametrize("extra", [{"owner": "forged"}, {"mode": "private"}, {"url": "https://secret.example/movie.mp4"}])
def test_unknown_fields_fail_without_echoing_input(environment, extra):
    env = environment
    response = env.client.put("/api/online/state/items/" + env.movie["id"] + "/favorite",
                              json={"favorite": True, **extra}, headers={HEADER: env.scope()["scope_token"]})
    assert response.status_code == 422
    assert "forged" not in response.text and "secret.example" not in response.text


def test_query_and_item_relationships_are_strict(environment):
    env = environment
    for query in ({"owner": "forged"}, {"section": "unknown"}, {"limit": 101}, {"offset": 1001}):
        response = env.client.get("/api/online/state/library", params=query)
        assert response.status_code == 422
    response = env.client.get("/api/online/state/items/" + env.movie["id"], params={"episode_id": env.episodes[0]["id"]})
    assert response.status_code == 422
    other_show = online.upsert_online_item(env.source["id"], "show-other", "tv", "Other")
    response = env.client.get("/api/online/state/items/" + other_show["id"], params={"episode_id": env.episodes[0]["id"]})
    assert response.status_code == 404


def test_global_write_token_is_required_but_state_reads_need_no_management_grant(environment):
    env = environment
    store.set_setting("jzmedia_token", "fixture-write-token")
    selected = env.scope()
    response = env.client.put("/api/online/state/items/" + env.movie["id"] + "/favorite",
                              headers={HEADER: selected["scope_token"]}, json={"favorite": True})
    assert response.status_code == 401
    response = env.client.put("/api/online/state/items/" + env.movie["id"] + "/favorite",
                              headers={HEADER: selected["scope_token"], "X-Api-Token": "fixture-write-token"}, json={"favorite": True})
    assert response.status_code == 200
    set_mode(env, "normal")
    assert env.client.get("/api/online/state/library").status_code == 200
    assert env.client.get("/api/online/sources", headers={"X-Api-Token": "fixture-write-token"}).status_code == 403


def test_allowlist_is_exact_for_state_methods_and_paths():
    item = "itm_" + "1" * 32
    paths = {"/api/online/state/library": "GET", "/api/online/state/items/" + item: "GET",
             "/api/online/state/items/" + item + "/favorite": "PUT",
             "/api/online/state/items/" + item + "/history": "DELETE",
             "/api/online/state/progress/claim": "POST", "/api/online/state/progress": "POST"}
    for path, method in paths.items():
        assert not viewing.management_path(path, method)
        assert viewing.management_path(path + "/extra", method)
        for other in {"GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"} - {method}:
            assert viewing.management_path(path, other)
