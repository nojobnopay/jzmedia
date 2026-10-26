"""Isolated foreground receiver protocol, scoped commands, and safe handoff."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
import time
import threading

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app import store, viewing
from app.casting import dlna, service, tv_app
from app.main import app
from app.routers import tv_cast


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    import app.store._base as base
    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "app-casting.db"))
    token = viewing.current.set(None)
    store.init_db()
    manager = tv_app.TvAppManager()
    monkeypatch.setattr(tv_app, "manager", manager)
    monkeypatch.setattr(tv_cast, "manager", manager)
    from app.routers import cast
    monkeypatch.setattr(cast, "tv_manager", manager, raising=False)
    vid = store.upsert_movie_by_path("synthetic-app.mp4")
    monkeypatch.setattr(store, "get_media_info", lambda *_: {"duration": 600, "audio": [{}, {}], "subs": [{}, {}]})
    client = TestClient(app)
    yield SimpleNamespace(manager=manager, client=client, vid=vid)
    client.close()
    manager.shutdown()
    viewing.current.reset(token)


def register(fixture, suffix="one"):
    response = fixture.client.post("/api/cast/apps/register", json={
        "device_id": "installation-uuid-" + suffix, "name": "合成电视 " + suffix, "protocol_version": 1})
    assert response.status_code == 200, response.text
    return response.json()


def poll(fixture, device, **body):
    return fixture.client.post(f"/api/cast/apps/{device['receiver_id']}/poll", json=body,
                               headers={"X-Receiver-Token": device["receiver_token"]})


def ack(fixture, device, command, accepted=True):
    response = fixture.client.post(f"/api/cast/apps/{device['receiver_id']}/ack", json={
        "command_id": command["id"], "accepted": accepted}, headers={"X-Receiver-Token": device["receiver_token"]})
    assert response.status_code == 200, response.text
    return response


def start(fixture, device, **kwargs):
    return fixture.manager.play(device["receiver_id"], fixture.vid, "movie", kwargs.get("position", 125),
                                kwargs.get("audio", 1), kwargs.get("sub", -1))


def test_register_lists_only_public_metadata_and_requires_receiver_secret(fixture):
    device = register(fixture)
    data = fixture.client.get("/api/cast/apps").json()
    assert data["items"][0]["receiver_type"] == "jzmedia_tv"
    assert data["items"][0]["volume_control"] is False
    assert device["receiver_token"] not in str(data)
    assert "installation-uuid" not in str(data)
    assert fixture.client.post(f"/api/cast/apps/{device['receiver_id']}/poll", json={}).status_code == 404


@pytest.fixture
def names(fixture, monkeypatch):
    cache = service.CastManager()
    monkeypatch.setattr(service, "manager", cache)

    def unexpected_network(*_args, **_kwargs):
        pytest.fail("Displaying App receiver names must not discover or contact DLNA devices")

    monkeypatch.setattr(dlna, "discover", unexpected_network)
    monkeypatch.setattr(dlna, "_http", unexpected_network)

    def discovered(*devices):
        cache.devices = {device.id: device for device in devices}
        cache.searched_at = time.monotonic()

    def receiver(address="192.168.1.25", suffix="one"):
        return fixture.manager.register("installation-names-" + suffix, "MiTV-MFTP0 · jzmedia-tv", address)

    device = dlna.Device("dlna-bedroom", "主卧小米电视", "192.168.1.25", "MiTV-MFTP0",
                         dlna.Service("AVTransport", "http://192.168.1.25/control"))
    return SimpleNamespace(cache=cache, discovered=discovered, register=receiver, device=device)


def test_app_receiver_uses_unique_cached_dlna_name_for_list_and_session_without_changing_identity(fixture, names):
    names.discovered(names.device)
    receiver = names.register()
    item = fixture.client.get("/api/cast/apps").json()["items"][0]
    assert item["name"] == "主卧小米电视"
    assert item["app_name"] == "MiTV-MFTP0 · jzmedia-tv"
    assert item["id"] == receiver["receiver_id"] != names.device.id
    session = start(fixture, receiver)
    assert session["device"] == item
    assert fixture.client.get(f"/api/cast/sessions/{session['id']}").json()["device"] == item
    assert fixture.client.get("/api/cast/current", params={"version_id": fixture.vid}).json()["session"]["device"] == item
    command = poll(fixture, receiver).json()["command"]
    assert command["session_id"] == session["id"]
    ack(fixture, receiver, command)
    assert fixture.manager.get(session["id"]).accepted


def test_confirmed_dlna_name_survives_cache_expiry_but_not_a_new_registration(fixture, names):
    names.discovered(names.device)
    receiver = names.register()
    assert fixture.manager.device_list()[0]["name"] == "主卧小米电视"
    session = start(fixture, receiver)
    names.cache.searched_at -= 121
    assert names.cache.device_list() == []
    assert fixture.manager.device_list()[0]["name"] == "主卧小米电视"
    assert fixture.manager.get(session["id"]).public()["device"]["name"] == "主卧小米电视"
    names.register()
    assert fixture.manager.device_list()[0]["name"] == "MiTV-MFTP0 · jzmedia-tv"


def test_fresh_dlna_rename_updates_an_existing_app_session(fixture, names):
    from dataclasses import replace
    names.discovered(names.device)
    receiver = names.register()
    session = start(fixture, receiver)
    names.discovered(replace(names.device, name="卧室电视"))
    assert fixture.manager.get(session["id"]).public()["device"]["name"] == "卧室电视"
    assert fixture.manager.device_list()[0]["name"] == "卧室电视"


def test_multiple_online_apps_at_one_ip_revoke_an_ambiguous_name(fixture, names):
    names.discovered(names.device)
    first = names.register()
    assert fixture.manager.device_list()[0]["name"] == "主卧小米电视"
    session = start(fixture, first)
    second = names.register(suffix="two")
    items = fixture.manager.device_list()
    assert len(items) == 2
    assert all(item["name"] == item["app_name"] == "MiTV-MFTP0 · jzmedia-tv" for item in items)
    assert fixture.manager.get(session["id"]).public()["device"]["name"] == "MiTV-MFTP0 · jzmedia-tv"
    fixture.manager.unregister(second["receiver_id"], second["receiver_token"])
    assert fixture.manager.device_list()[0]["name"] == "主卧小米电视"


def test_multiple_dlna_ids_at_one_ip_are_not_chosen_arbitrarily(fixture, names):
    from dataclasses import replace
    names.discovered(names.device)
    names.register()
    assert fixture.manager.device_list()[0]["name"] == "主卧小米电视"
    names.discovered(names.device, replace(names.device, id="dlna-other", name="另一台电视"))
    assert fixture.manager.device_list()[0]["name"] == "MiTV-MFTP0 · jzmedia-tv"


@pytest.mark.parametrize("address", ["192.168.1.26", "127.0.0.1", "::1", "8.8.8.8", "169.254.1.25", "testclient"])
def test_name_matching_requires_a_unique_real_lan_address(fixture, names, address):
    from dataclasses import replace
    # Even equal loopback/proxy/public addresses must not become identity.
    names.discovered(replace(names.device, address=address if address != "192.168.1.26" else names.device.address))
    names.register(address)
    item = fixture.manager.device_list()[0]
    assert item["name"] == item["app_name"] == "MiTV-MFTP0 · jzmedia-tv"


def test_ipv4_mapped_registration_matches_the_same_dlna_address(fixture, names):
    names.discovered(names.device)
    names.register("::ffff:192.168.1.25")
    assert fixture.manager.device_list()[0]["name"] == "主卧小米电视"


def test_forwarded_address_does_not_override_the_registration_peer(fixture, names):
    names.discovered(names.device)
    response = fixture.client.post("/api/cast/apps/register", headers={"X-Forwarded-For": "192.168.1.25"}, json={
        "device_id": "installation-names-forwarded", "name": "MiTV-MFTP0 · jzmedia-tv"})
    assert response.status_code == 200
    item = fixture.manager.device_list()[0]
    assert item["address"] == "testclient"
    assert item["name"] == item["app_name"] == "MiTV-MFTP0 · jzmedia-tv"


def test_explicit_position_tracks_and_no_progress_deletion(fixture):
    device = register(fixture)
    previous = store.save_progress(fixture.vid, 350, 600)
    session = start(fixture, device, position=0, audio=1, sub=0)
    assert session["state"] == "WAITING_FOR_APP" and session["resume_pending"]
    assert session["resume_target"] == 0 and not session["position_known"]
    command = poll(fixture, device).json()["command"]
    assert command["payload"]["position"] == 0
    assert command["payload"]["audio"] == 1 and command["payload"]["sub"] == 0
    assert store.get_progress(fixture.vid) == previous
    ack(fixture, device, command)
    poll(fixture, device, session_id=session["id"], state="PLAYING", position=2, duration=600)
    actual = fixture.manager.get(session["id"]).public()
    assert actual["position"] == actual["last_position"] == 2 and not actual["resume_pending"]
    assert store.get_progress(fixture.vid) == previous, "TV owns its normal progress writes; receiver reports do not overwrite them"


def test_unified_mobile_routes_dispatch_app_session_and_controls(fixture):
    device = register(fixture)
    response = fixture.client.post("/api/cast/sessions", json={
        "receiver_type": "jzmedia_tv", "device_id": device["receiver_id"],
        "version_id": fixture.vid, "position": 91.5, "audio": 1, "sub": -1})
    assert response.status_code == 200, response.text
    session = response.json()
    assert session["id"].startswith("tv_") and session["receiver_type"] == "jzmedia_tv"
    current = fixture.client.get("/api/cast/current", params={"version_id": fixture.vid}).json()["session"]
    assert current["id"] == session["id"] and current["resume_target"] == 91.5
    command = poll(fixture, device).json()["command"]
    ack(fixture, device, command)
    poll(fixture, device, session_id=session["id"], state="PLAYING", position=93, duration=600)
    assert fixture.client.get(f"/api/cast/sessions/{session['id']}").json()["position"] == 93
    assert fixture.client.post(f"/api/cast/sessions/{session['id']}/control",
                               json={"command": "volume", "value": 20}).status_code == 422
    with ThreadPoolExecutor() as pool:
        future = pool.submit(fixture.client.post, f"/api/cast/sessions/{session['id']}/control",
                             json={"command": "pause"})
        for _ in range(100):
            pending = poll(fixture, device, session_id=session["id"], state="PLAYING", position=94).json()["command"]
            if pending:
                break
            time.sleep(.01)
        assert pending["action"] == "pause"
        ack(fixture, device, pending)
        assert future.result(timeout=2).status_code == 200


def test_command_repeated_until_ack_but_ack_is_idempotent(fixture):
    device = register(fixture)
    start(fixture, device)
    first = poll(fixture, device).json()["command"]
    repeated = poll(fixture, device).json()["command"]
    assert repeated["id"] == first["id"] and repeated["payload"] == first["payload"]
    assert 0 < repeated["valid_for_seconds"] <= first["valid_for_seconds"]
    ack(fixture, device, first)
    ack(fixture, device, first)
    assert poll(fixture, device).json()["command"] is None


def test_app_final_zero_is_real_progress_and_unknown_is_null(fixture):
    device = register(fixture)
    session = start(fixture, device)
    ack(fixture, device, poll(fixture, device).json()["command"])
    poll(fixture, device, session_id=session["id"], state="PLAYING", position=130, duration=600)
    # App snapshots precede resource release. Unlike a DLNA receiver reset,
    # explicit final zero means the user sought to the start; unknown is null.
    poll(fixture, device, session_id=session["id"], state="STOPPED", position=0, duration=600)
    assert fixture.manager.get(session["id"]).last_position == 0
    newer = start(fixture, device)
    ack(fixture, device, poll(fixture, device).json()["command"])
    poll(fixture, device, session_id=newer["id"], state="PLAYING", position=140, duration=600)
    poll(fixture, device, session_id=newer["id"], state="STOPPED", position=None)
    assert fixture.manager.get(newer["id"]).last_position == 140


def test_cancel_undelivered_play_never_reaches_tv(fixture):
    device = register(fixture)
    session = start(fixture, device)
    assert fixture.manager.control(session["id"], "stop")["state"] == "CANCELLED"
    assert poll(fixture, device).json()["command"] is None


def test_stop_waits_for_actual_receiver_ack_and_keeps_last_position(fixture):
    device = register(fixture)
    session = start(fixture, device)
    command = poll(fixture, device).json()["command"]
    ack(fixture, device, command)
    poll(fixture, device, session_id=session["id"], state="PLAYING", position=145, duration=600)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(fixture.manager.control, session["id"], "stop")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            command = poll(fixture, device, session_id=session["id"], state="PAUSED_PLAYBACK", position=146).json()["command"]
            if command and command["action"] == "stop":
                break
            time.sleep(.01)
        assert command and command["action"] == "stop"
        assert not result.done(), "HTTP acceptance alone must not resume the phone"
        ack(fixture, device, command)
        stopped = result.result(timeout=2)
    assert not stopped["active"] and stopped["last_position"] == 146


def test_control_timeout_does_not_claim_tv_stopped(fixture, monkeypatch):
    device = register(fixture)
    session = start(fixture, device)
    ack(fixture, device, poll(fixture, device).json()["command"])
    monkeypatch.setattr(tv_app, "CONTROL_TIMEOUT", .01)
    with pytest.raises(HTTPException) as error:
        fixture.manager.control(session["id"], "stop")
    assert error.value.status_code == 504
    assert fixture.manager.get(session["id"]).active


def test_foreground_exit_and_expiry_remove_device(fixture):
    device = register(fixture)
    session = start(fixture, device)
    poll(fixture, device, foreground=False)
    assert fixture.client.get("/api/cast/apps").json()["items"] == []
    assert fixture.manager.get(session["id"]).state == "DISCONNECTED"
    second = register(fixture, "two")
    fixture.manager.receivers[second["receiver_id"]].seen -= tv_app.ONLINE_TTL + 1
    assert fixture.manager.device_list() == []


def test_reregister_rotates_secret_and_does_not_replay_old_commands(fixture):
    first = register(fixture)
    old = start(fixture, first)
    second = register(fixture)
    assert first["receiver_token"] != second["receiver_token"]
    assert poll(fixture, first).status_code == 404
    assert poll(fixture, second).json()["command"] is None
    assert fixture.manager.get(old["id"]).state == "DISCONNECTED"


def test_expired_play_is_not_executed_when_app_polls_late(fixture):
    device = register(fixture)
    session = start(fixture, device)
    fixture.manager.sessions[session["id"]].created -= tv_app.COMMAND_TTL + 1
    assert poll(fixture, device).json()["command"] is None
    assert fixture.manager.get(session["id"]).state == "EXPIRED"


def test_late_ack_cannot_resurrect_replaced_or_expired_play(fixture):
    device = register(fixture)
    first = start(fixture, device)
    command = poll(fixture, device).json()["command"]
    start(fixture, device, position=200)
    response = fixture.client.post(f"/api/cast/apps/{device['receiver_id']}/ack", json={
        "command_id": command["id"], "accepted": True}, headers={"X-Receiver-Token": device["receiver_token"]})
    assert response.status_code == 409
    assert fixture.manager.get(first["id"]).state == "REPLACED"


def test_rejected_play_and_invalid_track_are_explicit(fixture):
    device = register(fixture)
    with pytest.raises(HTTPException) as error:
        start(fixture, device, audio=8)
    assert error.value.status_code == 422
    session = start(fixture, device)
    command = poll(fixture, device).json()["command"]
    ack(fixture, device, command, False)
    assert not fixture.manager.get(session["id"]).active
    assert fixture.manager.get(session["id"]).state == "ERROR"


@contextmanager
def context(mode):
    session, secret, _ = viewing.resolve(create=True)
    with viewing._connection() as connection:
        connection.execute("UPDATE viewing_sessions SET mode=? WHERE token_hash=?", (mode, session["token_hash"]))
    token = viewing.current.set(viewing.context_for(viewing.session_by_hash(viewing.token_hash(secret))))
    try:
        yield
    finally:
        viewing.current.reset(token)


def test_phone_cannot_send_private_content_to_normal_tv(fixture):
    viewing.set_password("isolated-password")
    with viewing._connection() as connection:
        connection.execute("UPDATE movies SET is_private=1 WHERE id=?", (fixture.vid,))
    with context("normal"):
        receiver = fixture.manager.register("private-uuid-123456", "normal TV", "192.168.1.20")
    with context("private"), pytest.raises(HTTPException) as error:
        fixture.manager.play(receiver["receiver_id"], fixture.vid, "movie", 125)
    assert error.value.status_code == 403


def test_session_status_not_shared_across_protected_phone_sessions(fixture):
    viewing.set_password("isolated-password")
    with context("normal"):
        receiver = fixture.manager.register("public-uuid-123456", "normal TV", "192.168.1.20")
    with context("normal"):
        session = fixture.manager.play(receiver["receiver_id"], fixture.vid, "movie", 125)
        assert fixture.manager.current(fixture.vid, "movie")["id"] == session["id"]
    with context("normal"):
        assert fixture.manager.current(fixture.vid, "movie") is None
        with pytest.raises(HTTPException):
            fixture.manager.get(session["id"])


def test_phone_authorization_revocation_delivers_stop_not_media(fixture):
    viewing.set_password("isolated-password")
    with viewing._connection() as connection:
        connection.execute("UPDATE movies SET is_private=1 WHERE id=?", (fixture.vid,))
    with context("private"):
        receiver = fixture.manager.register("protected-tv-uuid", "private TV", "192.168.1.20")
        with context("private"):
            session = fixture.manager.play(receiver["receiver_id"], fixture.vid, "movie", 125)
            scope = fixture.manager.sessions[session["id"]].scope
        with viewing._connection() as connection:
            connection.execute("UPDATE viewing_sessions SET generation=generation+1 WHERE token_hash=?", (scope.key,))
        result = fixture.manager.poll(receiver["receiver_id"], receiver["receiver_token"], tv_cast.PollBody())
        assert result["command"]["action"] == "stop"
        assert not fixture.manager.sessions[session["id"]].active


@pytest.mark.parametrize("revoked", [False, True])
def test_old_phone_poll_does_not_change_replacement_play(fixture, monkeypatch, revoked):
    device = register(fixture)
    old = start(fixture, device)
    checking, proceed = threading.Event(), threading.Event()
    caller = threading.get_ident()
    checks = [0]
    original = tv_app.Scope.valid

    def validity(scope):
        if threading.get_ident() != caller:
            checks[0] += 1
            if checks[0] == 2:  # Receiver auth passes; the captured sender is revoked.
                checking.set()
                assert proceed.wait(2)
                return not revoked
        return original(scope)

    monkeypatch.setattr(tv_app.Scope, "valid", validity)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fixture.manager.poll, device["receiver_id"], device["receiver_token"],
                             tv_cast.PollBody(session_id=old["id"], state="PLAYING", position=130))
        assert checking.wait(2)
        try:
            replacement = start(fixture, device, position=240)
        finally:
            proceed.set()
        result = future.result(timeout=2)
    assert result["command"]["session_id"] == replacement["id"]
    assert result["command"]["action"] == "play"
    assert fixture.manager.get(old["id"]).state == "REPLACED"
    assert fixture.manager.get(replacement["id"]).active
