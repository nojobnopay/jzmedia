"""Host inspection proposes exact names; only explicit atomic approval trusts them."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import socket

import pytest

from app import store
from app.online import catalog, maccms, service, source_hosts
from app.online.providers import ProviderError
from app.store import online
from test_online_catalog import environment as environment


def endpoint(environment, action="host-check"):
    return f"/api/online/sources/{environment.source['id']}/{action}"


def inspected(environment):
    environment.show.update(vod_play_from="main", vod_play_url=
        "第1集$https://NEW.EXAMPLE/ep-1.m3u8?signature=server-only-private"
        "#第2集$https://new.example/ep-2.m3u8?signature=server-only-private",
        vod_pic="https://images.example/poster.jpg?secret=server-only-private")
    response = environment.client.post(endpoint(environment), json={"external_id": "show-1"})
    assert response.status_code == 200, response.text
    return response.json()


def test_inspection_reads_one_detail_and_returns_only_missing_exact_hosts(environment, monkeypatch):
    def no_dns(*_args, **_kwargs):
        pytest.fail("inspection must not resolve or contact suggested hostnames")
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    original = online.get_online_source(environment.source["id"])
    report = inspected(environment)
    assert report["source_id"] == environment.source["id"] and report["external_id"] == "show-1"
    assert report["missing_hosts"] == ["images.example", "new.example"]
    assert report["allowed_hosts"] == original["allowed_hosts"]
    assert report["rejected_count"] == 0
    assert len(report["configuration_fingerprint"]) == 64
    assert "server-only-private" not in str(report) and "https://" not in str(report)
    assert environment.requests == [{"ac": "detail", "ids": "show-1"}]
    assert online.get_online_source(environment.source["id"]) == original


@pytest.mark.parametrize("value", [
    "file:///tmp/private.mp4", "javascript:alert(1)", "//cdn.example/ep.m3u8",
    "http://127.0.0.1/ep.mp4", "http://192.168.1.1/ep.mp4", "http://[::1]/ep.mp4",
    "http://224.0.0.1/ep.mp4", "http://192.0.0.9/ep.mp4", "http://0x7f000001/ep.mp4",
    "http://2130706433/ep.mp4", "http://localhost/ep.mp4", "http://nas/ep.mp4",
    "https://user:private@cdn.example/ep.mp4", "https://cdn.example/ep.mp4#fragment",
    "https://cdn.example/ep%0d%0a.mp4", "https://cdn.example:0/ep.mp4",
    "https://cdn.example/watch.html", "https://cdn.example/ep.mp4\n",
])
def test_candidate_rejects_non_media_or_unsafe_addresses_without_io(environment, value):
    with pytest.raises((ValueError, TypeError)):
        source_hosts._candidate(environment.source["id"], value, media=True)
    assert environment.requests == []


def test_report_counts_rejected_entries_without_adding_them_to_candidates(environment):
    environment.show.update(vod_play_from="main", vod_play_url=
        "第1集$https://new.example/ep.mp4"
        "#第2集$http://127.0.0.1/private.mp4"
        "#第3集$https://cdn.example/watch.html"
        "#第4集$https://user:private@evil.example/ep.mp4",
        vod_pic="file:///etc/private")
    response = environment.client.post(endpoint(environment), json={"external_id": "show-1"})
    assert response.status_code == 200
    assert response.json()["missing_hosts"] == ["new.example"]
    assert response.json()["rejected_count"] == 4
    assert "127.0.0.1" not in response.text and "private" not in response.text


def test_source_edit_during_inspection_does_not_return_stale_candidates(environment, monkeypatch):
    original = maccms.fetch

    def fetch(source, params):
        result = original(source, params)
        online.update_online_source(source["id"], {"priority": 7})
        return result

    monkeypatch.setattr(maccms, "fetch", fetch)
    response = environment.client.post(endpoint(environment), json={"external_id": "show-1"})
    assert response.status_code == 409
    assert response.json()["code"] == "online_source_changed"
    assert catalog._active == 0 and catalog._owners == set()


def test_explicit_approval_only_appends_selected_hosts_and_revokes_after_commit(environment, monkeypatch):
    report = inspected(environment)
    original = online.get_online_source(environment.source["id"])
    revocations = []
    monkeypatch.setattr(service.registry, "revoke_source", lambda source_id: revocations.append(
        (source_id, online.get_online_source(source_id)["allowed_hosts"])))
    response = environment.client.post(endpoint(environment, "allow-hosts"), json={
        "hosts": ["NEW.EXAMPLE.", "new.example"], "expected_fingerprint": report["configuration_fingerprint"]})
    assert response.status_code == 200
    saved = response.json()
    assert saved["allowed_hosts"] == [*original["allowed_hosts"], "new.example"]
    assert {key: value for key, value in saved.items() if key not in {"allowed_hosts", "updated_at"}} == {
        key: value for key, value in original.items() if key not in {"allowed_hosts", "updated_at"}}
    assert revocations == [(saved["id"], saved["allowed_hosts"])]
    assert environment.requests == [{"ac": "detail", "ids": "show-1"}], "approval performs no network request"


def test_approval_rejects_stale_configuration_without_overwriting_other_edits(environment):
    report = inspected(environment)
    updated = online.update_online_source(environment.source["id"], {"priority": 2, "allowed_hosts": [
        *environment.source["allowed_hosts"], "other-admin.example"]})
    response = environment.client.post(endpoint(environment, "allow-hosts"), json={
        "hosts": ["new.example"], "expected_fingerprint": report["configuration_fingerprint"]})
    assert response.status_code == 409
    assert online.get_online_source(environment.source["id"]) == updated


def test_approving_an_existing_host_is_a_noop_without_revoke(environment, monkeypatch):
    report = inspected(environment)
    original = online.get_online_source(environment.source["id"])
    revocations = []
    monkeypatch.setattr(service.registry, "revoke_source", revocations.append)
    response = environment.client.post(endpoint(environment, "allow-hosts"), json={
        "hosts": [original["allowed_hosts"][0]], "expected_fingerprint": report["configuration_fingerprint"]})
    assert response.status_code == 200 and response.json() == original
    assert revocations == []


def test_two_approvals_of_the_same_snapshot_cannot_clobber_each_other(environment):
    report = inspected(environment)
    access = service.capture_access(SimpleNamespace(headers={}, cookies=dict(environment.client.cookies)))

    def approve(host):
        try:
            return source_hosts.allow(access, environment.source["id"], {
                "hosts": [host], "expected_fingerprint": report["configuration_fingerprint"]})
        except source_hosts.HostCheckError as error:
            return error.category

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(approve, ["one.example", "two.example"]))
    assert sum(result == "online_source_changed" for result in results) == 1
    saved = online.get_online_source(environment.source["id"])
    assert sum(host in saved["allowed_hosts"] for host in ["one.example", "two.example"]) == 1


def test_validation_rejects_unsafe_approval_bodies_without_reflecting_input(environment):
    report = inspected(environment)
    original = online.get_online_source(environment.source["id"])
    for value in ["http://private.example/private-secret", "127.0.0.1", "localhost", "nas", "*.example"]:
        response = environment.client.post(endpoint(environment, "allow-hosts"), json={
            "hosts": [value], "expected_fingerprint": report["configuration_fingerprint"]})
        assert response.status_code == 422 and value not in response.text
    assert online.get_online_source(environment.source["id"]) == original
    assert environment.client.post(endpoint(environment), json={"external_id": "private-secret\n"}).status_code == 422


def test_inspection_and_approval_require_management_unlock_and_write_token(environment):
    client = environment.client
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    assert client.post(endpoint(environment), json={"external_id": "show-1"}).status_code == 403
    assert environment.requests == []
    assert client.post("/api/viewing/unlock", json={"password": "123456"}).status_code == 200
    store.set_setting("jzmedia_token", "host-admin-token")
    assert client.post(endpoint(environment), json={"external_id": "show-1"}).status_code == 401
    response = client.post(endpoint(environment), json={"external_id": "show-1"}, headers={"X-Api-Token": "host-admin-token"})
    assert response.status_code == 200
    assert client.post("/api/viewing/mode", json={"mode": "children"}, headers={"X-Api-Token": "host-admin-token"}).status_code == 200
    assert client.post(endpoint(environment), json={"external_id": "show-1"}, headers={"X-Api-Token": "host-admin-token"}).status_code == 403


def test_management_revoked_while_api_is_loading_cannot_publish_hosts(environment, monkeypatch):
    client = environment.client
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    assert client.post("/api/viewing/unlock", json={"password": "123456"}).status_code == 200
    original = maccms.fetch

    def fetch(source, params):
        result = original(source, params)
        with online._connection(write=True) as connection:
            connection.execute("UPDATE viewing_sessions SET management_until=0")
        return result

    monkeypatch.setattr(maccms, "fetch", fetch)
    response = client.post(endpoint(environment), json={"external_id": "show-1"})
    assert response.status_code == 403
    assert response.json()["code"] == "online_management_revoked"


@pytest.mark.parametrize("disable", ["source", "feature"])
def test_disabled_configuration_is_not_probed(environment, disable):
    if disable == "source":
        online.update_online_source(environment.source["id"], {"enabled": False})
    else:
        online.update_online_settings(False)
    response = environment.client.post(endpoint(environment), json={"external_id": "show-1"})
    assert response.status_code == 503
    assert environment.requests == []


def test_provider_failure_releases_catalogue_slots(environment, monkeypatch):
    def failed(*_args):
        raise ProviderError("provider_timeout")
    monkeypatch.setattr(maccms, "fetch", failed)
    response = environment.client.post(endpoint(environment), json={"external_id": "show-1"})
    assert response.status_code == 504 and response.json()["code"] == "provider_timeout"
    assert catalog._active == 0 and catalog._owners == set()
