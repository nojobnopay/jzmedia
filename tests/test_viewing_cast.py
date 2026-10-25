"""Protected DLNA sessions use isolated SQLite data and mocked SOAP only."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import store, viewing
from app.casting import dlna, service
from app.main import app
from app.routers import cast


PASSWORD = "cast-password"


@contextmanager
def _owner_context(owner):
    token = viewing.current.set(viewing.context_for(viewing.session_by_hash(owner.key)))
    try:
        yield
    finally:
        viewing.current.reset(token)


@pytest.fixture
def protected_cast(tmp_path, monkeypatch):
    import app.store._base as base

    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "viewing-cast.db"))
    context_token = viewing.current.set(None)
    store.init_db()
    viewing.set_password(PASSWORD)
    movie_id = store.upsert_movie_by_path("synthetic-private.mp4")
    public_id = store.upsert_movie_by_path("synthetic-public.mp4")
    with viewing._connection() as c:
        c.execute("UPDATE movies SET is_private=1 WHERE id=?", (movie_id,))

    clock = [10000.0]
    monkeypatch.setattr(viewing.time, "time", lambda: clock[0])
    manager = service.CastManager()
    monkeypatch.setattr(cast, "manager", manager)
    client = TestClient(app)
    receivers = {}
    calls = []

    def soap(transport, name, arguments):
        calls.append((transport.url, name, arguments))
        receiver = receivers[transport.url]
        assert arguments == {"InstanceID": 0}
        if name == "GetTransportInfo":
            return {"CurrentTransportState": receiver.state, "CurrentTransportStatus": "OK"}
        if name == "GetPositionInfo":
            if receiver.unavailable:
                raise dlna.CastError("Position reporting unavailable")
            return {"TrackURI": receiver.uri, "RelTime": "00:02:00", "TrackDuration": "00:10:00"}
        assert name == "Stop", f"Unexpected SOAP action: {name}"
        return {}

    monkeypatch.setattr(dlna, "action", soap)
    monkeypatch.setattr(dlna, "_http", lambda *args, **kwargs: pytest.fail("Network access is forbidden"))

    def owner(mode="private"):
        issued = client.get("/api/viewing/session", headers={"X-Viewing-Client": "tv"})
        assert issued.status_code == 200, issued.text
        secret = issued.json()["session_token"]
        headers = {"X-Viewing-Client": "tv", "X-Viewing-Session": secret}
        if mode != "normal":
            changed = client.post("/api/viewing/mode", headers=headers,
                                  json={"mode": mode, "password": PASSWORD})
            assert changed.status_code == 200, changed.text
        return SimpleNamespace(key=viewing.token_hash(secret), headers=headers)

    def session(owner, *, private=True):
        sid = f"cast-{len(manager.sessions) + 1}"
        transport = dlna.Service("urn:schemas-upnp-org:service:AVTransport:1",
                                 f"http://192.168.1.50/{sid}/control")
        device = dlna.Device(sid, f"Synthetic {sid}", "192.168.1.50", "Fixture", transport)
        version_id = movie_id if private else public_id
        access = viewing.session_by_hash(owner.key)
        result = service.CastSession(
            id=sid, device=device, version_id=version_id, kind="movie",
            row=store.get_playable("movie", version_id),
            uri=f"http://192.168.1.10/api/cast/media/{sid}", mime="video/mp4",
            duration=600, title="Synthetic protected movie", viewing_owner=owner.key,
            viewing_generation=access["generation"],
        )
        manager.sessions[sid] = result
        receivers[transport.url] = SimpleNamespace(state="PLAYING", uri=result.uri, unavailable=False)
        return result

    def receiver_uri(session, value):
        receiver = receivers[session.device.transport.url]
        receiver.uri = {"matching": session.uri, "missing": "", "different": session.uri + "-new"}.get(value, "")
        receiver.unavailable = value == "unavailable"

    yield SimpleNamespace(manager=manager, client=client, owner=owner, session=session,
                          clock=clock, calls=calls, receivers=receivers, receiver_uri=receiver_uri,
                          movie_id=movie_id)
    client.close()
    manager.shutdown()
    viewing.current.reset(context_token)


def test_current_finds_own_cast_without_exposing_another_device(protected_cast):
    fixture = protected_cast
    first, second, stranger = (fixture.owner() for _ in range(3))
    first_cast = fixture.session(first)
    second_cast = fixture.session(second)
    # The newest matching cast belongs to another device; find the older own cast.
    for owner, expected in ((first, first_cast.id), (second, second_cast.id), (stranger, None)):
        response = fixture.client.get(f"/api/cast/current?version_id={fixture.movie_id}", headers=owner.headers)
        assert response.status_code == 200, response.text
        result = response.json()["session"]
        assert (result["id"] if result else None) == expected
    assert fixture.client.get(f"/api/cast/sessions/{second_cast.id}", headers=first.headers).status_code == 404
    assert not fixture.calls


@pytest.mark.parametrize("revocation", ["expiry", "generation", "deleted"])
def test_current_ignores_casts_with_invalid_viewing_lease(protected_cast, revocation):
    fixture = protected_cast
    owner = fixture.owner()
    session = fixture.session(owner)
    with _owner_context(owner):
        assert fixture.manager.current(fixture.movie_id, "movie")["id"] == session.id
        if revocation == "expiry":
            fixture.clock[0] += viewing.IDLE_SECONDS
        else:
            with viewing._connection() as c:
                query = ("DELETE FROM viewing_sessions WHERE token_hash=?" if revocation == "deleted" else
                         "UPDATE viewing_sessions SET generation=generation+1 WHERE token_hash=?")
                c.execute(query, (owner.key,))
        assert fixture.manager.current(fixture.movie_id, "movie") is None
    assert not fixture.calls


def test_matching_playback_renews_only_its_private_owner_and_not_management(protected_cast):
    fixture = protected_cast
    owner, other = fixture.owner(), fixture.owner()
    session = fixture.session(owner)
    before = viewing.session_by_hash(owner.key)
    other_before = viewing.session_by_hash(other.key)
    fixture.clock[0] += viewing.IDLE_SECONDS - 1
    fixture.manager._poll(session)
    fixture.clock[0] += 2  # The management grant expires while playback continues.
    fixture.manager._poll(session)
    after = viewing.session_by_hash(owner.key)
    assert after["last_activity"] == fixture.clock[0]
    assert after["playback_until"] == fixture.clock[0] + 90
    assert after["management_until"] == before["management_until"]
    assert after["mode"] == "private" and after["generation"] == before["generation"]
    assert not viewing.public_session(after)["management_unlocked"]
    assert viewing.session_by_hash(other.key) == other_before
    assert session.active and session.state == "PLAYING"
    assert store.get_progress(fixture.movie_id)["position"] == 120
    assert not any(name == "Stop" for _, name, _ in fixture.calls)


@pytest.mark.parametrize(("state", "uri"), [
    ("PAUSED_PLAYBACK", "matching"), ("STOPPED", "matching"), ("TRANSITIONING", "matching"),
    ("PLAYING", "missing"), ("PLAYING", "different"), ("PLAYING", "unavailable"),
])
def test_poll_requires_playing_and_exact_track_uri_to_renew(protected_cast, state, uri):
    fixture = protected_cast
    owner = fixture.owner()
    session = fixture.session(owner)
    before = viewing.session_by_hash(owner.key)
    fixture.receivers[session.device.transport.url].state = state
    fixture.receiver_uri(session, uri)
    fixture.clock[0] += 60
    fixture.manager._poll(session)
    assert viewing.session_by_hash(owner.key) == before
    assert not any(name == "Stop" for _, name, _ in fixture.calls)


def test_normal_mode_playback_does_not_renew_protection_authorization(protected_cast):
    fixture = protected_cast
    owner = fixture.owner("normal")
    response = fixture.client.post("/api/viewing/unlock", headers=owner.headers, json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    session = fixture.session(owner, private=False)
    before = viewing.session_by_hash(owner.key)
    fixture.clock[0] += 60
    fixture.manager._poll(session)
    assert viewing.session_by_hash(owner.key) == before
    assert session.active and session.state == "PLAYING"


def test_paused_cast_expires_fifteen_minutes_after_last_reported_playback(protected_cast):
    fixture = protected_cast
    owner = fixture.owner()
    session = fixture.session(owner)
    fixture.clock[0] += 600
    fixture.manager._poll(session)
    fixture.receivers[session.device.transport.url].state = "PAUSED_PLAYBACK"
    fixture.clock[0] += viewing.IDLE_SECONDS - 1
    fixture.manager._poll(session)
    assert session.active and session.state == "PAUSED_PLAYBACK"
    fixture.clock[0] += 1
    fixture.manager._poll(session)
    assert not session.active and session.state == "PROTECTION_LOCKED"
    assert [name for _, name, _ in fixture.calls].count("Stop") == 1


@pytest.mark.parametrize("preparing", [False, True])
def test_selected_audio_revocation_releases_independent_lease(protected_cast, monkeypatch, preparing):
    from app.casting import audio
    fixture = protected_cast
    owner = fixture.owner()
    session = fixture.session(owner)
    session.audio_index, session.audio_lease = 1, "synthetic-selected-audio"
    session.audio_validator = lambda: True
    session.preparing = preparing
    session.resume_target = 125
    released = []
    monkeypatch.setattr(audio.outputs, "release", released.append)
    monkeypatch.setattr(audio.outputs, "snapshot", lambda _: pytest.fail("Revoked output must not be read"))
    fixture.clock[0] += viewing.IDLE_SECONDS
    fixture.manager._poll(session)
    assert not session.active and session.state == "PROTECTION_LOCKED"
    assert session.audio_lease is None and session.resume_target is None
    assert released == ["synthetic-selected-audio"]
    stopped = [name for _, name, _ in fixture.calls if name == "Stop"]
    assert stopped == ([] if preparing else ["Stop"])


@pytest.mark.parametrize("revocation", ["expiry", "password"])
@pytest.mark.parametrize("uri", ["matching", "missing", "different", "unavailable"])
def test_revoked_casts_stop_only_when_receiver_still_reports_their_uri(protected_cast, revocation, uri):
    fixture = protected_cast
    owners = [fixture.owner(), fixture.owner()]
    sessions = [fixture.session(owner) for owner in owners]
    for session in sessions:
        session.resume_target = 125
        session.resume_deadline = service.time.monotonic() + service.RESUME_TIMEOUT
        fixture.receiver_uri(session, uri)
    if revocation == "expiry":
        fixture.clock[0] += viewing.IDLE_SECONDS
    else:
        response = fixture.client.post("/api/viewing/password", headers=owners[0].headers,
                                       json={"current_password": PASSWORD, "new_password": "new-cast-password"})
        assert response.status_code == 200, response.text
    before = [viewing.session_by_hash(owner.key) for owner in owners]
    for session in sessions:
        assert not viewing.check_lease(session.viewing_owner, session.viewing_generation)
        fixture.manager._poll(session)
        assert not session.active
        assert session.resume_target is None
        assert session.state == ("REPLACED" if uri == "different" else "PROTECTION_LOCKED")
        with pytest.raises(HTTPException) as error:
            fixture.manager.get(session.id)
        assert error.value.status_code == 404
    assert [viewing.session_by_hash(owner.key) for owner in owners] == before
    stopped = [url for url, name, _ in fixture.calls if name == "Stop"]
    assert stopped == ([session.device.transport.url for session in sessions] if uri == "matching" else [])
    assert store.get_progress(fixture.movie_id) is None


def test_locking_one_device_revokes_only_its_cast(protected_cast):
    fixture = protected_cast
    first, second = fixture.owner(), fixture.owner()
    first_cast, second_cast = fixture.session(first), fixture.session(second)
    other_before = viewing.session_by_hash(second.key)
    response = fixture.client.post("/api/viewing/lock", headers=first.headers)
    assert response.status_code == 200 and response.json()["mode"] == "normal"
    assert not viewing.check_lease(first.key, first_cast.viewing_generation)
    assert viewing.check_lease(second.key, second_cast.viewing_generation)
    assert viewing.session_by_hash(second.key) == other_before
    fixture.clock[0] += 60
    fixture.manager._poll(first_cast)
    fixture.manager._poll(second_cast)
    assert not first_cast.active and first_cast.state == "PROTECTION_LOCKED"
    assert second_cast.active and second_cast.state == "PLAYING"
    assert [url for url, name, _ in fixture.calls if name == "Stop"] == [first_cast.device.transport.url]
    other_after = viewing.session_by_hash(second.key)
    assert other_after["last_activity"] == fixture.clock[0]
    assert other_after["management_until"] == other_before["management_until"]
    result = fixture.client.get(f"/api/cast/current?version_id={fixture.movie_id}", headers=second.headers)
    assert result.status_code == 200 and result.json()["session"]["id"] == second_cast.id
