"""Real loopback SOAP receiver, bounded discovery, scoped Range reads, and progress ownership."""
import socket
from dataclasses import replace
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import library_paths, store
from app.storage import smb
from _smb_fake import FakeSmbClient
from app.casting import dlna, service
from app.main import app
from app.routers import cast
from scripts.fixtures.dlna_receiver import DlnaReceiver


@pytest.fixture
def receiver(monkeypatch):
    original = dlna.lan_ip
    monkeypatch.setattr(dlna, "lan_ip", lambda value: value == "127.0.0.1" or original(value))
    with DlnaReceiver() as receiver:
        yield receiver


@pytest.fixture
def casting(receiver, tmp_path, monkeypatch):
    manager = service.CastManager()
    monkeypatch.setattr(cast, "manager", manager)
    monkeypatch.setattr(manager, "_ensure_worker", lambda: None)
    device = dlna.describe(receiver.location, "127.0.0.1")
    monkeypatch.setattr(dlna, "discover", lambda: [device])
    lib = store.create_library(name=f"cast-{tmp_path.name}", path=str(tmp_path))
    library_paths.invalidate_cache()
    path = "电影 空格%&#.mp4"
    data = bytes(range(256)) * 10
    (tmp_path / path).write_bytes(data)
    vid = store.upsert_movie_by_path(path, library_id=lib["id"])
    store.update_movie_meta(vid, title="合成影片 & <测试>")
    monkeypatch.setattr(service, "_media_cached_or_probe", lambda *_: {"playable": True, "duration": 600})
    client = TestClient(app)
    assert client.post("/api/cast/discover").status_code == 200
    yield SimpleNamespace(manager=manager, receiver=receiver, device=device, vid=vid,
                          lib=lib, path=path, data=data, client=client)
    manager.shutdown()
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def start(fixture, **extra):
    result = fixture.client.post("/api/cast/sessions", json={"device_id": fixture.device.id,
        "version_id": fixture.vid, "base_url": "http://192.168.1.10:8080", "position": 125, **extra})
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize("url", ["http://127.0.0.1/a", "http://8.8.8.8/a", "http://169.254.169.254/latest/",
    "http://localhost/a", "http://192.168.1.2@8.8.8.8/a", "http://192.168.1.2:80@192.168.1.2/a",
    "file:///etc/passwd", "https://192.168.1.2/a", "http://192.168.1.3/a",
    "http://192.168.1.2:0/a", "http://192.168.1.2:99999/a"])
def test_discovery_cannot_fetch_unrelated_or_non_lan_addresses(url):
    with pytest.raises(dlna.CastError):
        dlna.device_url(url, "192.168.1.2")


def test_description_and_soap_use_real_receiver(receiver):
    device = dlna.describe(receiver.location, "127.0.0.1")
    assert device.name.startswith("合成 DLNA 电视")
    assert device.rendering and device.connection
    dlna.action(device.transport, "SetAVTransportURI", {"InstanceID": 0,
        "CurrentURI": "http://192.168.1.2/video?x=%25&z=中文", "CurrentURIMetaData": ""})
    assert receiver.uri.endswith("&z=中文")
    receiver.reject.add("Seek")
    with pytest.raises(dlna.CastError, match="跳转"):
        dlna.action(device.transport, "Seek", {"InstanceID": 0, "Unit": "REL_TIME", "Target": "00:02:00"})


def test_description_rejects_cross_device_control_urls(monkeypatch):
    monkeypatch.setattr(dlna, "_http", lambda *_: b'<root xmlns="urn:schemas-upnp-org:device-1-0"><URLBase>http://8.8.8.8/</URLBase></root>')
    with pytest.raises(dlna.CastError):
        dlna.describe("http://192.168.1.2/device.xml", "192.168.1.2")


def test_manual_device_registration_survives_empty_discovery(casting, monkeypatch):
    monkeypatch.setattr(dlna, "discover", lambda: [])
    response = casting.client.post("/api/cast/devices", json={"location": casting.receiver.location})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["id"] == casting.device.id
    casting.manager.searched_at = 0
    discovered = casting.client.post("/api/cast/discover")
    assert discovered.status_code == 200
    assert [item["id"] for item in discovered.json()["items"]] == [casting.device.id]
    assert start(casting)["device"]["id"] == casting.device.id


@pytest.mark.parametrize("location", ["http://127.0.0.1/description.xml",
    "http://169.254.169.254/latest/meta-data/", "http://8.8.8.8/description.xml",
    "http://localhost/description.xml", "https://192.168.1.72/description.xml",
    "http://192.168.1.72@8.8.8.8/description.xml", "file:///etc/passwd"])
def test_manual_device_rejects_non_lan_locations(casting, location):
    response = casting.client.post("/api/cast/devices", json={"location": location})
    assert response.status_code == 422
    assert [item["id"] for item in casting.manager.device_list()] == [casting.device.id]


def test_manual_device_rejects_non_renderer_description(casting, monkeypatch):
    monkeypatch.setattr(dlna, "_http", lambda *_: b'<root xmlns="urn:schemas-upnp-org:device-1-0"/>')
    response = casting.client.post("/api/cast/devices", json={"location": "http://192.168.1.72:49152/description.xml"})
    assert response.status_code == 422
    assert "DLNA" in response.json()["detail"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_rejects_entity_expansion(encoding):
    with pytest.raises(dlna.CastError):
        dlna.parse_xml('<!DOCTYPE x [<!ENTITY a "data">]><x>&a;</x>'.encode(encoding))


def test_ssdp_real_unicast_response_is_bounded_and_validated(receiver, monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind(("127.0.0.1", 0))
        monkeypatch.setattr(dlna, "SSDP_TARGET", udp.getsockname())
        def respond():
            for _ in range(2):
                packet, peer = udp.recvfrom(8192)
                assert b"M-SEARCH" in packet and b"MediaRenderer:1" in packet
                udp.sendto(f"HTTP/1.1 200 OK\r\nLOCATION: {receiver.location}\r\n\r\n".encode(), peer)
                udp.sendto(b"HTTP/1.1 200 OK\r\nLOCATION: http://8.8.8.8/\r\n\r\n", peer)
        thread = threading.Thread(target=respond)
        thread.start()
        assert dlna.ssdp_locations(0.1) == [(receiver.location, "127.0.0.1")]
        thread.join(timeout=1)
        assert not thread.is_alive()


def test_play_range_head_and_control(casting):
    result = start(casting)
    sid = result["id"]
    assert result["position_known"] is False
    assert [c["name"] for c in casting.receiver.calls if c["name"] in ("SetAVTransportURI", "Play", "Seek")] == ["SetAVTransportURI", "Play", "Seek"]
    assert result["resume_pending"] and result["resume_target"] == 125
    assert casting.receiver.position == 125
    meta = ET.fromstring(casting.receiver.metadata)
    assert meta.findtext(".//{http://purl.org/dc/elements/1.1/}title") == "合成影片 & <测试>"
    assert "电影" not in casting.receiver.uri and "/api/cast/media/" in casting.receiver.uri
    response = casting.client.get(f"/api/cast/media/{sid}", headers={"Range": "bytes=10-29"})
    assert response.status_code == 206 and response.content == casting.data[10:30]
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["contentFeatures.dlna.org"].startswith("DLNA.ORG_OP=01")
    head = casting.client.head(f"/api/cast/media/{sid}")
    assert head.status_code == 200 and not head.content
    assert int(head.headers["content-length"]) == len(casting.data)
    for command, extra in (("pause", {}), ("play", {}), ("seek", {"value": 200}), ("volume", {"value": 60})):
        r = casting.client.post(f"/api/cast/sessions/{sid}/control", json={"command": command, **extra})
        assert r.status_code == 200, r.text
    assert casting.receiver.position == 200 and casting.receiver.volume == 60
    assert casting.client.post(f"/api/cast/sessions/{sid}/control", json={"command": "stop"}).json()["active"] is False


@pytest.fixture
def prepared_audio(casting, tmp_path, monkeypatch):
    from app.casting import audio
    info = {"playable": True, "duration": 600, "vcodec": "h264", "audio": [{"index": 0}, {"index": 1}]}
    monkeypatch.setattr(service, "_media_cached_or_probe", lambda *_: info)
    output = tmp_path / "selected-audio.mp4"
    output.write_bytes(b"selected second audio" * 200)
    record = {"state": "preparing", "progress": 0.25, "path": None, "error": ""}
    calls = []
    def prepare(row, source, media_info, index, valid):
        assert index in (0, 1) and media_info is info
        calls.append(("prepare", index, valid))
        return "synthetic-audio-lease"
    monkeypatch.setattr(audio.outputs, "prepare", prepare)
    monkeypatch.setattr(audio.outputs, "snapshot", lambda lease: dict(record))
    monkeypatch.setattr(audio.outputs, "release", lambda lease: calls.append(("release", lease)))
    def ready():
        record.update(state="ready", progress=1, path=str(output), duration=600, size=output.stat().st_size)
    return SimpleNamespace(cast=casting, calls=calls, record=record, output=output, ready=ready)


def test_selected_audio_waits_for_complete_file_then_preserves_full_range_and_seek(prepared_audio):
    fixture = prepared_audio
    cast_fixture = fixture.cast
    result = start(cast_fixture, audio=1)
    sid = result["id"]
    assert result["state"] == "PREPARING" and result["active"] and result["resume_pending"]
    assert result["resume_target"] == 125 and result["audio_index"] == 1
    assert result["receiver_type"] == "dlna" and result["created_at"] > 0
    assert not cast_fixture.receiver.uri
    assert cast_fixture.client.get(f"/api/cast/media/{sid}").status_code == 409
    cast_fixture.manager._poll(cast_fixture.manager.get(sid))
    assert cast_fixture.manager.get(sid).prepare_progress == 0.25
    fixture.ready()
    cast_fixture.manager._poll(cast_fixture.manager.get(sid))
    assert cast_fixture.receiver.uri.endswith(sid)
    assert cast_fixture.receiver.position == 125
    cast_fixture.manager._poll(cast_fixture.manager.get(sid))
    assert cast_fixture.manager.get(sid).position_known
    response = cast_fixture.client.get(f"/api/cast/media/{sid}", headers={"Range": "bytes=7-23"})
    assert response.status_code == 206 and response.content == fixture.output.read_bytes()[7:24]
    assert response.headers["content-type"].startswith("video/mp4")
    assert "DLNA.ORG_CI=1" in response.headers["contentFeatures.dlna.org"]
    assert int(cast_fixture.client.head(f"/api/cast/media/{sid}").headers["content-length"]) == fixture.output.stat().st_size
    assert cast_fixture.client.post(f"/api/cast/sessions/{sid}/control", json={"command": "stop"}).status_code == 200
    assert any(call[0] == "release" for call in fixture.calls)
    assert cast_fixture.client.get(f"/api/cast/media/{sid}").status_code == 410


def test_complete_audio_output_confirms_zero_start_without_another_monitor_interval(prepared_audio):
    fixture = prepared_audio
    result = start(fixture.cast, audio=1, position=0)
    fixture.ready()
    session = fixture.cast.manager.get(result["id"])
    fixture.cast.manager._poll(session)
    assert session.state == "PLAYING" and session.position_known
    assert session.position == 0 and not session.preparing and session.resume_target is None
    assert any(call["name"] == "GetTransportInfo" for call in fixture.cast.receiver.calls)


def test_stop_preparation_does_not_stop_unrelated_tv_content(prepared_audio):
    fixture = prepared_audio
    fixture.cast.receiver.uri = "http://192.168.1.5/unrelated.mp4"
    fixture.cast.receiver.state = "PLAYING"
    result = start(fixture.cast, audio=0, position=0)
    assert result["resume_pending"] and result["resume_target"] == 0
    sid = result["id"]
    before = len(fixture.cast.receiver.calls)
    seek = fixture.cast.client.post(f"/api/cast/sessions/{sid}/control", json={"command": "seek", "value": 90})
    assert seek.status_code == 409
    stopped = fixture.cast.client.post(f"/api/cast/sessions/{sid}/control", json={"command": "stop"})
    assert stopped.status_code == 200 and not stopped.json()["active"]
    assert len(fixture.cast.receiver.calls) == before
    assert fixture.cast.receiver.state == "PLAYING" and fixture.cast.receiver.uri.endswith("unrelated.mp4")
    assert fixture.cast.client.get(f"/api/cast/media/{sid}").status_code == 410


def test_newer_preparation_cancels_an_older_pending_start(prepared_audio):
    fixture = prepared_audio
    first = start(fixture.cast, audio=0)
    second = start(fixture.cast, audio=1)
    older = fixture.cast.manager.get(first["id"])
    assert older.state == "REPLACED" and not older.active and not older.resume_target
    assert second["state"] == "PREPARING" and second["active"]
    fixture.ready()
    fixture.cast.manager._poll(older)
    assert not fixture.cast.receiver.uri
    fixture.cast.manager._poll(fixture.cast.manager.get(second["id"]))
    assert fixture.cast.receiver.uri.endswith(second["id"])


def test_source_change_before_preparation_is_published_never_starts_tv(prepared_audio):
    fixture = prepared_audio
    result = start(fixture.cast, audio=1)
    fixture.ready()
    (Path(fixture.cast.lib["path"]) / fixture.cast.path).write_bytes(b"changed synthetic source")
    session = fixture.cast.manager.get(result["id"])
    fixture.cast.manager._poll(session)
    assert not session.active and session.state == "SOURCE_CHANGED"
    assert not fixture.cast.receiver.uri
    assert any(call[0] == "release" for call in fixture.calls)


@pytest.mark.parametrize("extra", [{"audio": 2}, {"sub": 0}])
def test_invalid_audio_or_dlna_subtitle_request_does_not_prepare_or_play(prepared_audio, extra):
    fixture = prepared_audio
    response = fixture.cast.client.post("/api/cast/sessions", json={"device_id": fixture.cast.device.id,
        "version_id": fixture.cast.vid, "base_url": "http://192.168.1.10:8080", **extra})
    assert response.status_code == 422
    assert not fixture.calls and not fixture.cast.receiver.uri


def test_preparation_error_is_reported_without_changing_tv(prepared_audio):
    fixture = prepared_audio
    result = start(fixture.cast, audio=1)
    fixture.record.update(state="error", error="synthetic preparation failure")
    session = fixture.cast.manager.get(result["id"])
    fixture.cast.manager._poll(session)
    assert not session.active and session.state == "ERROR" and session.error
    assert not fixture.cast.receiver.uri


def test_episode_scope_and_background_progress(casting):
    show = store.upsert_show(casting.lib["id"], "合成剧集")
    episode = store.upsert_episode(show, casting.lib["id"], casting.path, 1, 1)
    result = start(casting, kind="episode", version_id=episode)
    session = casting.manager.get(result["id"])
    casting.manager._poll(session)  # Confirm the requested start before later playback advances.
    casting.receiver.position = 321
    casting.manager._poll(session)
    assert store.get_progress(episode, "episode")["position"] == 321
    assert casting.client.get(f"/api/cast/current?version_id={episode}&kind=episode").json()["session"]["id"] == result["id"]
    assert casting.client.get(f"/api/cast/current?version_id={episode}&kind=movie").json()["session"] is None


def test_unrelated_track_cannot_overwrite_progress_or_be_stopped(casting):
    result = start(casting)
    store.save_progress(casting.vid, 88, 600)
    casting.receiver.uri = "http://192.168.1.20/another-video.mp4"
    casting.receiver.position = 400
    response = casting.client.post(f"/api/cast/sessions/{result['id']}/control", json={"command": "stop"})
    assert response.status_code == 409
    assert casting.receiver.state == "PLAYING"
    assert store.get_progress(casting.vid)["position"] == 88


def test_unknown_position_and_end_reset_do_not_forge_progress(casting):
    result = start(casting)
    session = casting.manager.get(result["id"])
    store.save_progress(casting.vid, 88, 600)
    casting.receiver.report_uri = False
    casting.receiver.position = 400
    casting.manager._poll(session)
    assert not session.position_known and store.get_progress(casting.vid)["position"] == 88
    casting.receiver.report_uri = True
    casting.receiver.state = "STOPPED"
    casting.receiver.position = 0
    casting.manager._poll(session)
    assert not session.active and store.get_progress(casting.vid)["position"] == 88


def test_end_reset_preserves_last_valid_position_for_phone_resume(casting):
    result = start(casting)
    session = casting.manager.get(result["id"])
    casting.manager._poll(session)
    casting.receiver.position = 321
    casting.manager._poll(session)
    casting.receiver.state, casting.receiver.position = "STOPPED", 0
    casting.manager._poll(session)
    assert session.public()["last_position"] == 321
    assert not session.position_known
    assert store.get_progress(casting.vid)["position"] == 321


def test_deleted_media_does_not_create_orphan_progress(casting):
    result = start(casting)
    session = casting.manager.get(result["id"])
    store.delete_movie(casting.vid)
    casting.manager._poll(session)
    assert not session.active and session.state == "MEDIA_REMOVED"
    assert session.resume_target is None
    assert casting.client.get(f"/api/cast/media/{result['id']}").status_code == 404


def test_unsupported_seek_warns_and_no_position_is_assumed(casting):
    casting.receiver.reject.add("Seek")
    result = start(casting)
    assert result["resume_pending"] and not result["position_known"]
    session = casting.manager.get(result["id"])
    for _ in range(service.RESUME_MAX_ATTEMPTS):
        session.resume_sent_at -= service.RESUME_RETRY_SECONDS
        casting.manager._poll(session)
    assert session.warning and not session.public()["resume_pending"]
    assert casting.receiver.position == 0


def test_missing_position_service_still_reports_transport(casting):
    result = start(casting)
    casting.receiver.reject.add("GetPositionInfo")
    session = casting.manager.get(result["id"])
    for _ in range(7):
        casting.manager._poll(session)
    assert session.active and session.state == "PLAYING" and not session.position_known


def test_stale_device_invalid_address_and_missing_movie_do_not_send_commands(casting):
    before = len(casting.receiver.calls)
    for payload in ({"device_id": "arbitrary"}, {"base_url": "http://10.0.2.2:8080"},
                    {"version_id": 999999999}):
        response = casting.client.post("/api/cast/sessions", json={"device_id": casting.device.id,
            "version_id": casting.vid, "base_url": "http://192.168.1.10:8080", **payload})
        assert response.status_code in (404, 422)
    casting.manager.searched_at = time.monotonic() - 121
    assert casting.client.post("/api/cast/sessions", json={"device_id": casting.device.id,
        "version_id": casting.vid, "base_url": "http://192.168.1.10:8080"}).status_code == 404
    assert len(casting.receiver.calls) == before


def test_rejects_loopback_media_url():
    with pytest.raises(HTTPException):
        service.base_url("http://127.0.0.1:8080")


def test_rejected_format_and_auth_have_no_playback_effect(casting):
    casting.receiver.sink = "http-get:*:video/mpeg:*"
    response = casting.client.post("/api/cast/sessions", json={"device_id": casting.device.id,
        "version_id": casting.vid, "base_url": "http://192.168.1.10:8080"})
    assert response.status_code == 422 and not casting.manager.sessions
    assert not any(c["name"] == "Play" for c in casting.receiver.calls)
    store.set_setting("jzmedia_token", "casting-secret-test")
    try:
        assert casting.client.post("/api/cast/discover").status_code == 401
        assert casting.client.post("/api/cast/sessions", json={}).status_code == 401
        assert casting.client.post("/api/cast/sessions/unknown/control", json={"command": "stop"}).status_code == 401
        assert casting.client.post("/api/cast/discover", headers={"X-Api-Token": "casting-secret-test"}).status_code == 200
    finally:
        store.set_setting("jzmedia_token", "")


def test_failed_play_cleans_up_loaded_receiver(casting):
    casting.receiver.reject.add("Play")
    response = casting.client.post("/api/cast/sessions", json={"device_id": casting.device.id,
        "version_id": casting.vid, "base_url": "http://192.168.1.10:8080"})
    assert response.status_code == 502
    assert casting.receiver.state == "STOPPED"
    assert not casting.manager.sessions
    assert casting.receiver.calls[-1]["name"] == "Stop"


def test_read_only_smb_cast_uses_scoped_range_and_never_exposes_credentials(casting, tmp_path, monkeypatch):
    root = tmp_path / "remote"
    root.mkdir()
    fake = FakeSmbClient(root)
    monkeypatch.setenv("SMB_DRIVER", "direct")
    monkeypatch.setattr(smb, "smbclient", fake)
    (root / casting.path).write_bytes(casting.data)
    lib = store.create_library(name=f"cast-smb-{tmp_path.name}", source="smb",
                               smb={"host": "nas", "share": "video", "username": "u", "password": "private-cast-password"})
    store.update_media_library(lib["media_library_id"], read_only=True)
    library_paths.invalidate_cache()
    smb.invalidate()
    try:
        vid = store.upsert_movie_by_path(casting.path, library_id=lib["id"])
        result = start(casting, version_id=vid)
        assert "private-cast-password" not in str(result) + casting.receiver.uri + casting.receiver.metadata
        response = casting.client.get(f"/api/cast/media/{result['id']}", headers={"Range": "bytes=100-139"})
        assert response.status_code == 206 and response.content == casting.data[100:140]
        assert response.headers["content-range"] == f"bytes 100-139/{len(casting.data)}"
    finally:
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()
        smb.invalidate()


def test_new_cast_replaces_session_and_shutdown_does_not_stop_tv(casting):
    first = start(casting)
    second = start(casting)
    assert not casting.manager.get(first["id"]).active
    assert casting.manager.get(second["id"]).active
    casting.manager.shutdown()
    assert casting.receiver.state == "PLAYING"


def test_startup_stop_has_grace_period(casting):
    casting.receiver.ready_polls = 1
    result = start(casting)
    session = casting.manager.get(result["id"])
    casting.receiver.state = "STOPPED"
    casting.manager._poll(session)
    assert session.active and session.state == "TRANSITIONING"
    session.created -= 31
    casting.manager._poll(session)
    assert not session.active
    assert session.error and session.resume_target is None


def seek_calls(casting):
    return [call for call in casting.receiver.calls if call["name"] == "Seek"]


def test_resume_waits_for_readiness_then_confirms_real_position(casting):
    store.save_progress(casting.vid, 125, 600)
    casting.receiver.ready_polls = 2
    result = start(casting)
    session = casting.manager.get(result["id"])
    assert result["resume_pending"] and result["resume_target"] == 125
    assert not result["position_known"] and not seek_calls(casting)
    casting.manager._poll(session)
    assert not seek_calls(casting) and store.get_progress(casting.vid)["position"] == 125
    casting.manager._poll(session)
    assert len(seek_calls(casting)) == 1 and casting.receiver.position == 125
    assert session.resume_target == 125 and not session.position_known
    assert session.last_position is None and store.get_progress(casting.vid)["position"] == 125
    casting.manager._poll(session)
    assert not session.public()["resume_pending"] and session.position_known
    assert session.last_position == 125 and session.public()["resume_target"] is None


def test_pending_resume_finishes_in_background_without_phone_requests(casting, monkeypatch):
    casting.receiver.ready_polls = 1
    monkeypatch.setattr(casting.manager, "_ensure_worker",
                        lambda: service.CastManager._ensure_worker(casting.manager))
    result = start(casting)
    session = casting.manager.get(result["id"])
    assert result["resume_pending"] and not seek_calls(casting)
    deadline = time.monotonic() + 5
    while session.resume_target is not None and time.monotonic() < deadline:
        threading.Event().wait(0.02)
    assert session.resume_target is None and session.position_known
    assert session.last_position == 125 and store.get_progress(casting.vid)["position"] == 125
    assert len(seek_calls(casting)) == 1
    casting.manager.shutdown()
    assert casting.receiver.state == "PLAYING"


def test_successful_but_ignored_seek_is_retried_without_saving_zero(casting):
    store.save_progress(casting.vid, 125, 600)
    casting.receiver.ignore_seeks = 1
    result = start(casting)
    session = casting.manager.get(result["id"])
    assert result["resume_pending"] and casting.receiver.position == 0
    casting.manager._poll(session)
    assert len(seek_calls(casting)) == 1 and not session.position_known
    assert store.get_progress(casting.vid)["position"] == 125
    session.resume_sent_at -= service.RESUME_RETRY_SECONDS
    casting.manager._poll(session)
    assert len(seek_calls(casting)) == 2 and not session.position_known
    casting.manager._poll(session)
    assert not session.public()["resume_pending"] and session.last_position == 125


def test_ignored_seek_has_a_retry_budget_and_recoverable_warning(casting):
    store.save_progress(casting.vid, 125, 600)
    casting.receiver.ignore_seeks = 100
    session = casting.manager.get(start(casting)["id"])
    for _ in range(service.RESUME_MAX_ATTEMPTS - 1):
        session.resume_sent_at -= service.RESUME_RETRY_SECONDS
        casting.manager._poll(session)
        assert not session.position_known and store.get_progress(casting.vid)["position"] == 125
    assert len(seek_calls(casting)) == service.RESUME_MAX_ATTEMPTS
    session.resume_sent_at -= service.RESUME_RETRY_SECONDS
    casting.manager._poll(session)
    assert session.active and session.state == "PLAYING" and session.warning == service.RESUME_WARNING
    assert session.public()["resume_pending"] is False
    casting.manager._poll(session)
    assert len(seek_calls(casting)) == service.RESUME_MAX_ATTEMPTS


def test_resume_timeout_without_readiness_never_sends_a_seek(casting):
    store.save_progress(casting.vid, 125, 600)
    casting.receiver.ready_polls = 100
    session = casting.manager.get(start(casting)["id"])
    session.resume_deadline -= service.RESUME_TIMEOUT + 1
    casting.manager._poll(session)
    assert session.warning == service.RESUME_WARNING and session.resume_target is None
    assert not seek_calls(casting) and store.get_progress(casting.vid)["position"] == 125


@pytest.mark.parametrize("command,value", [("seek", 90), ("stop", None)])
def test_manual_position_control_cancels_pending_before_another_automatic_seek(casting, command, value):
    store.save_progress(casting.vid, 125, 600)
    casting.receiver.ignore_seeks = 100
    session = casting.manager.get(start(casting)["id"])
    session.resume_sent_at -= service.RESUME_RETRY_SECONDS
    payload = {"command": command, **({"value": value} if value is not None else {})}
    response = casting.client.post(f"/api/cast/sessions/{session.id}/control", json=payload)
    assert response.status_code == 200, response.text
    assert session.resume_target is None
    assert len(seek_calls(casting)) == (2 if command == "seek" else 1)
    if command == "seek":
        assert seek_calls(casting)[-1]["args"]["Target"] == "00:01:30"
    else:
        assert not session.active and store.get_progress(casting.vid)["position"] == 125


@pytest.mark.parametrize("value", [None, 601])
def test_invalid_manual_seek_keeps_pending_start(casting, value):
    casting.receiver.ignore_seeks = 100
    session = casting.manager.get(start(casting)["id"])
    response = casting.client.post(f"/api/cast/sessions/{session.id}/control", json={"command": "seek", "value": value})
    assert response.status_code == 422 and session.resume_target == 125
    assert len(seek_calls(casting)) == 1


def test_replacing_a_loading_cast_cancels_its_pending_start(casting):
    casting.receiver.ready_polls = 100
    old = casting.manager.get(start(casting)["id"])
    new = casting.manager.get(start(casting, position=0)["id"])
    assert old.resume_target is None and not old.active and new.active
    before = len(casting.receiver.calls)
    casting.manager._poll(old)
    assert len(casting.receiver.calls) == before and casting.receiver.uri == new.uri


def test_unrelated_track_cancels_loading_resume_without_sending_seek(casting):
    store.save_progress(casting.vid, 125, 600)
    casting.receiver.ready_polls = 100
    session = casting.manager.get(start(casting)["id"])
    casting.receiver.uri = "http://192.168.1.10/unrelated.mp4"
    casting.manager._poll(session)
    assert session.resume_target is None and not session.active and session.state == "REPLACED"
    assert not seek_calls(casting) and store.get_progress(casting.vid)["position"] == 125


def test_disconnection_cancels_pending_start(casting):
    casting.receiver.ready_polls = 100
    session = casting.manager.get(start(casting)["id"])
    casting.receiver.reject.add("GetTransportInfo")
    for _ in range(6):
        casting.manager._poll(session)
    assert session.resume_target is None and not session.active and session.state == "DISCONNECTED"
    assert not seek_calls(casting)


def test_reported_playback_error_cancels_pending_without_seeking_or_saving_zero(casting):
    store.save_progress(casting.vid, 125, 600)
    casting.receiver.ready_polls = 1
    session = casting.manager.get(start(casting)["id"])
    casting.receiver.transport_status = "ERROR_OCCURRED"
    casting.manager._poll(session)
    assert session.error and session.resume_target is None and not session.position_known
    assert not seek_calls(casting) and store.get_progress(casting.vid)["position"] == 125


def test_unknown_track_cannot_receive_automatic_seek(casting):
    casting.receiver.report_uri = False
    session = casting.manager.get(start(casting)["id"])
    casting.manager._poll(session)
    assert session.resume_target == 125 and not session.position_known and not seek_calls(casting)


def test_volume_is_read_once_in_background_and_manual_updates_are_preserved(casting):
    result = start(casting)
    assert result["volume"] is None
    session = casting.manager.get(result["id"])
    casting.manager._poll(session)
    assert session.volume == 50 and session.position_known
    casting.receiver.volume = 75
    casting.manager._poll(session)
    assert session.volume == 50
    assert [call["name"] for call in casting.receiver.calls].count("GetVolume") == 1
    response = casting.client.post(f"/api/cast/sessions/{session.id}/control", json={"command": "volume", "value": 60})
    assert response.status_code == 200 and response.json()["volume"] == 60


def test_optional_volume_failure_does_not_break_resume_or_repeat(casting):
    casting.receiver.reject.add("GetVolume")
    session = casting.manager.get(start(casting)["id"])
    casting.manager._poll(session)
    casting.manager._poll(session)
    assert session.active and session.position_known and session.volume is None
    assert session.resume_target is None and not session.error
    assert [call["name"] for call in casting.receiver.calls].count("GetVolume") == 1


def test_stopping_before_background_poll_does_not_query_optional_volume(casting):
    session = casting.manager.get(start(casting)["id"])
    response = casting.client.post(f"/api/cast/sessions/{session.id}/control", json={"command": "stop"})
    assert response.status_code == 200 and not response.json()["active"]
    assert not any(call["name"] == "GetVolume" for call in casting.receiver.calls)


def test_current_returns_cached_state_without_waiting_for_device_io_lock(casting):
    session = casting.manager.get(start(casting)["id"])
    locked, release, finished = threading.Event(), threading.Event(), threading.Event()
    responses = []

    def slow_device_operation():
        with session.lock:
            locked.set()
            release.wait(5)

    def restore_on_phone():
        try:
            responses.append(casting.client.get(f"/api/cast/current?version_id={casting.vid}&kind=movie"))
        finally:
            finished.set()

    device_thread = threading.Thread(target=slow_device_operation)
    request_thread = threading.Thread(target=restore_on_phone)
    device_thread.start()
    try:
        assert locked.wait(1)
        request_thread.start()
        returned_while_locked = finished.wait(1)
    finally:
        release.set()
        device_thread.join(timeout=2)
        if request_thread.ident is not None:
            request_thread.join(timeout=2)
    assert returned_while_locked, "Restoring a cast waited for the receiver's slow I/O"
    assert responses[0].status_code == 200
    current = responses[0].json()["session"]
    assert current["id"] == session.id and current["active"]
    assert current["resume_pending"] and current["resume_target"] == 125


@pytest.mark.parametrize("value,expected", [("0", 0), ("100", 100), ("50", 50),
    (None, None), ("-1", None), ("101", None), ("50.0", None), ("NaN", None), ("١", None), ("9999", None)])
def test_volume_only_accepts_reported_in_range_ascii_integers(casting, monkeypatch, value, expected):
    original = dlna.action
    monkeypatch.setattr(dlna, "action", lambda service, name, args: {"CurrentVolume": value}
                        if name == "GetVolume" else original(service, name, args))
    session = casting.manager.get(start(casting)["id"])
    casting.manager._poll(session)
    assert session.volume == expected and session.active


def test_receiver_without_volume_control_is_not_queried(casting):
    session = casting.manager.get(start(casting)["id"])
    session.device = replace(session.device, rendering=None)
    casting.manager._poll(session)
    assert session.volume is None and not any(call["name"] == "GetVolume" for call in casting.receiver.calls)
    response = casting.client.post(f"/api/cast/sessions/{session.id}/control", json={"command": "volume", "value": 60})
    assert response.status_code == 422
