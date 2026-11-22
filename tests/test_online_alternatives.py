"""Viewer-facing comparison of equivalent lines using only synthetic media."""

from contextlib import closing

from fastapi.testclient import TestClient
import pytest

from app import store, viewing
from app.main import app
from app.network.targets import TargetPolicy
from app.online import alternatives, catalog, diagnostic_probes, maccms, service
from app.online.providers import ProviderError
from app.online.sessions import SessionRegistry
from app.store import online
import test_online_catalog as catalogue_fixture
from test_online_catalog import get_detail
from test_online_sessions import Client


environment = catalogue_fixture.environment


PATH = "/api/online/playback/alternatives"


def programme(state):
    state.show.update(vod_name="合成剧 第二季", vod_director="合成导演甲", vod_play_from="main", vod_play_url=(
        "第1集$https://cdn.example/a.mp4#第2集$https://cdn.example/b.mp4"))
    detail = get_detail(state, "show-1").json()
    current = detail["lines"][0]["entries"][1]["identity"]
    return current, {**state.show, "vod_id": "copy", "vod_play_from": "mirror",
                     "vod_play_url": "EP02$https://cdn.example/alternative.mp4?sig=secret"}


def source(name):
    return online.create_online_source({"name": name, "provider": "maccms", "enabled": True,
                                       "api_base_url": "https://index.example/api/", "allowed_hosts": ["cdn.example"]})


def upstream(monkeypatch, sources, samples, slow=None):
    def fetch(selected, params):
        if selected["id"] == slow:
            raise ProviderError("provider_timeout")
        rows = sources[selected["id"]]
        return {"code": 1, "list": [r for r in rows if "ids" not in params or r["vod_id"] == params["ids"]]}
    monkeypatch.setattr(maccms, "fetch", fetch)

    def sample(selected, entry, policy):
        assert policy.purpose == "media" and policy.source_id == selected["id"]
        samples.append((selected["name"], entry.url))
        if "broken" in entry.url:
            raise diagnostic_probes.ProbeFailure("invalid_media_sample")
        return {"media_ms": 60, "sample_bytes": 65536, "sample_kbps": 600 if "fast" in entry.url else 300}
    monkeypatch.setattr(alternatives.discovery_media, "sample_media", sample)


def test_stall_compares_actual_episode_and_returns_ranked_playable_identities(environment, monkeypatch):
    state = environment
    current, row = programme(state)
    second = source("另一片库")
    state.show.update(vod_play_from="main$$$backup$$$failed", vod_play_url=state.show["vod_play_url"]
                      + "$$$第2集$https://cdn.example/slow.mp4$$$第2集$https://cdn.example/broken.mp4")
    row.update(vod_play_from="other$$$mirror", vod_play_url=(
        "第3集$https://cdn.example/wrong.mp4$$$EP02$https://cdn.example/fast.mp4?sig=secret"))
    samples = []
    upstream(monkeypatch, {state.source["id"]: [state.show], second["id"]: [row]}, samples)
    response = state.client.post(PATH, json=current)
    assert response.status_code == 200, response.text
    assert "secret" not in response.text and "cdn.example" not in response.text
    results = response.json()["items"]
    assert [(r["source_name"], r["throughput_kbps"]) for r in results] == [(second["name"], 600), (state.source["name"], 300)]
    assert response.json()["partial"]
    assert all("wrong" not in url for _, url in samples)
    assert {r["identity"]["episode_id"] for r in results}.isdisjoint({current["episode_id"]})
    assert catalog.resolve(results[0]["identity"]).url == "https://cdn.example/fast.mp4?sig=secret"


@pytest.mark.parametrize("override", [
    {"vod_name": "合成剧 第一季"}, {"vod_name": "合成剧"}, {"vod_year": "2023"}, {"vod_year": ""},
    {"type_name": "电影"}, {"vod_director": "合成导演乙"}, {"vod_play_url": "第02集预告$https://cdn.example/special.mp4"},
    {"vod_play_url": "EP02$https://cdn.example/a.mp4#第2集$https://cdn.example/b.mp4"},
])
def test_different_or_ambiguous_versions_are_not_probed(environment, monkeypatch, override):
    current, copy = programme(environment)
    second = source("未匹配")
    samples = []
    upstream(monkeypatch, {environment.source["id"]: [environment.show], second["id"]: [{**copy, **override}]}, samples)
    response = environment.client.post(PATH, json=current)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == [] and not samples


def test_active_verified_session_survives_origin_catalogue_outage(environment, monkeypatch):
    current, copy = programme(environment)
    second = source("备用")
    registry = SessionRegistry()
    monkeypatch.setattr(service, "registry", registry)

    def reader(source_id, purpose, **_kwargs):
        assert purpose == "media"
        return Client(source_id, TargetPolicy(source_id, ["cdn.example"], resolver=lambda *_: ("8.8.8.8",)))

    monkeypatch.setattr(service, "create_online_client", reader)
    created = environment.client.post("/api/online/sessions", json=current)
    assert created.status_code == 201, created.text
    samples = []
    upstream(monkeypatch, {second["id"]: [copy]}, samples, slow=environment.source["id"])
    response = environment.client.post(PATH, json=current)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["identity"]["source_id"] == second["id"]
    assert response.json()["partial"] and len(samples) == 1
    with closing(TestClient(app)) as stranger:
        stranger.get("/api/viewing/session")
        assert stranger.post(PATH, json=current).json() == {"partial": True, "items": []}
    assert len(samples) == 1
    assert environment.client.delete("/api/online/sessions/" + created.json()["session_id"]).status_code == 204
    assert environment.client.post(PATH, json=current).json() == {"partial": True, "items": []}


def test_viewing_api_token_and_source_policy_are_enforced(environment, monkeypatch):
    current, row = programme(environment)
    second = source("待停用")
    samples = []
    upstream(monkeypatch, {environment.source["id"]: [environment.show], second["id"]: [row]}, samples)
    client = environment.client
    with closing(TestClient(app)) as stranger:
        assert stranger.post(PATH, json=current).status_code == 401
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    store.set_setting("jzmedia_token", "viewer-token")
    assert client.post(PATH, json=current).status_code == 401
    headers = {"x-api-token": "viewer-token"}
    assert client.post(PATH, json={**current, "url": "https://bad.invalid/"}, headers=headers).status_code == 422
    assert client.post(PATH, json=current, headers=headers).status_code == 200
    assert len(samples) == 1
    online.update_online_source(second["id"], {"enabled": False})
    assert client.post(PATH, json=current, headers=headers).json()["items"] == []
    assert client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    assert client.post(PATH, json=current, headers=headers).status_code == 403
    assert not viewing.management_path(PATH, "POST") and viewing.management_path(PATH + "/other", "POST")
    assert len(samples) == 1


def test_revocation_during_media_probe_discards_result(environment, monkeypatch):
    current, row = programme(environment)
    second = source("备用")
    upstream(monkeypatch, {environment.source["id"]: [environment.show], second["id"]: [row]}, [])

    def revoke(*args):
        with store._base._conn() as connection:
            connection.execute("UPDATE viewing_sessions SET generation=generation+1")
        return {"media_ms": 10, "sample_bytes": 10000, "sample_kbps": None}

    monkeypatch.setattr(alternatives.discovery_media, "sample_media", revoke)
    response = environment.client.post(PATH, json=current)
    assert response.status_code == 401 and response.json()["code"] == "online_access_revoked"


def test_failed_second_movie_line_retains_first_and_excludes_duplicate_media(environment, monkeypatch):
    movie = environment.movie
    current = get_detail(environment).json()["lines"][0]["entries"][0]["identity"]
    movie.update(vod_play_from="mp4$$$alias$$$alternate$$$slow$$$trailer", vod_play_url=(
        "正片$https://cdn.example/main.mp4$$$HD$https://cdn.example/main.mp4"
        "$$$1080p$https://cdn.example/backup.mp4$$$正片$https://cdn.example/slow.mp4"
        "$$$预告$https://cdn.example/trailer.mp4"))
    samples = []

    def sample(source, entry, policy):
        from app.network.guard import NetworkOperationError
        samples.append(entry.url)
        if entry.url.endswith("/slow.mp4"):
            raise NetworkOperationError("operation_timeout")
        return {"media_ms": 65, "sample_bytes": 65536, "sample_kbps": 400}

    monkeypatch.setattr(alternatives.discovery_media, "sample_media", sample)
    response = environment.client.post(PATH, json=current)
    assert response.status_code == 200, response.text
    assert samples == ["https://cdn.example/backup.mp4", "https://cdn.example/slow.mp4"]
    result = response.json()
    assert result["partial"] and len(result["items"]) == 1
    assert result["items"][0]["line_label"] == "alternate"


def test_slower_than_realtime_hls_is_rejected_and_verified_headroom_breaks_close_rates(environment, monkeypatch):
    state = environment
    current, row = programme(state)
    state.show.update(vod_play_from="main$$$overloaded", vod_play_url=state.show["vod_play_url"]
                      + "$$$第2集$https://cdn.example/heavy.m3u8")
    second = source("其它码率")
    row.update(vod_play_from="mp4$$$hls", vod_play_url=(
        "EP02$https://cdn.example/unknown.mp4$$$EP02$https://cdn.example/stable.m3u8"))
    upstream(monkeypatch, {state.source["id"]: [state.show], second["id"]: [row]}, [])

    def sample(source, entry, policy):
        rate, ratio = {"heavy.m3u8": (12000, 0.72), "unknown.mp4": (4600, None),
                       "stable.m3u8": (4100, 1.6)}[entry.url.rsplit("/", 1)[1]]
        return {"media_ms": 150, "sample_bytes": 200000, "sample_kbps": rate, "realtime_ratio": ratio}

    monkeypatch.setattr(alternatives.discovery_media, "sample_media", sample)
    response = state.client.post(PATH, json=current)
    assert response.status_code == 200, response.text
    assert [row["line_label"] for row in response.json()["items"]] == ["hls", "mp4"]


def test_newly_measured_fourth_source_is_tried_and_changed_route_loses_the_hint(environment, monkeypatch):
    from app.store import online_network

    current, row = programme(environment)
    earlier = [source(f"原先靠前 {i}") for i in range(3)]
    for priority, saved in enumerate(earlier):
        online.update_online_source(saved["id"], {"priority": priority})
    # The settings test samples a different film. Only the episode probe below
    # is allowed to qualify the source as an actual viewing recommendation.
    directory = {**environment.movie, "vod_play_from": "sample",
                 "vod_play_url": "正片$https://cdn.example/settings-film.mp4"}
    monkeypatch.setattr(diagnostic_probes, "_payload", lambda *_args: {"code": 1, "list": [directory]})
    def setting_sample(_source, entry, _policy, *, host_review):
        host_review.reference(entry.url)
        assert entry.url.endswith("/settings-film.mp4")
        return {"media_ms": 100, "sample_kbps": 10000, "sample_bytes": 120000, "realtime_ratio": None}
    monkeypatch.setattr(alternatives.discovery_media, "sample_media", setting_sample)
    client = environment.client
    report = client.post("/api/online/discovery/ikun/probe", json={})
    assert report.status_code == 200 and report.json()["status"] == "measured", report.text
    enabled = client.post("/api/online/discovery/ikun/enable", json={"probe_token": report.json()["probe_token"]})
    assert enabled.status_code == 200, enabled.text
    other = enabled.json()
    ordered = [s["id"] for s in online.list_online_sources() if s["id"] != current["source_id"]]
    assert ordered == [*(s["id"] for s in earlier), other["id"]]

    samples = []
    target = {**row, "vod_play_url": "EP02$https://cdn.example/episode-fast.mp4"}
    catalogues = {environment.source["id"]: [environment.show], **{s["id"]: [] for s in earlier}, other["id"]: [target]}
    upstream(monkeypatch, catalogues, samples)
    response = client.post(PATH, json=current)
    assert response.status_code == 200, response.text
    assert [(r["identity"]["source_id"], r["throughput_kbps"]) for r in response.json()["items"]] == [(other["id"], 600)]
    assert samples == [(other["name"], "https://cdn.example/episode-fast.mp4")]

    proxy = online_network.create_network_profile({"name": "新测试出口", "mode": "proxy", "proxy_url": "http://proxy.example:8080"})
    online_network.update_source_network_policy(other["id"], {"media": {"mode": "proxy", "profile_id": proxy["id"]}})
    assert client.post(PATH, json=current).json()["items"] == []
    assert len(samples) == 1


TASKS = "/api/online/playback/alternative-tasks"


def start_task(client, identity):
    response = client.post(TASKS, json=identity)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running" and not body["items"]
    return TASKS + "/" + body["task_id"]


def advance_task(client, task):
    response = client.post(task + "/advance", json={})
    assert response.status_code == 200, response.text
    return response.json()


def fake_sessions(monkeypatch):
    registry = SessionRegistry()
    monkeypatch.setattr(service, "registry", registry)
    monkeypatch.setattr(service, "create_online_client", lambda sid, *_args, **_kwargs:
                        Client(sid, TargetPolicy(sid, ["cdn.example"], resolver=lambda *_: ("8.8.8.8",))))
    return registry


def test_incremental_tasks_publish_first_line_and_validate_after_cancel(environment, monkeypatch):
    state = environment
    current = get_detail(state).json()["lines"][0]["entries"][0]["identity"]
    movie = state.movie
    movie.update(vod_play_from="mp4$$$a$$$b", vod_play_url=(
        "正片$https://cdn.example/main.mp4$$$HD$https://cdn.example/slow.mp4$$$正片$https://cdn.example/fast.mp4"))
    samples = []
    upstream(monkeypatch, {state.source["id"]: [movie]}, samples)
    registry = fake_sessions(monkeypatch)
    playing = state.client.post("/api/online/sessions", json=current)
    assert playing.status_code == 201
    task = start_task(state.client, current)
    one = advance_task(state.client, task)
    assert one["status"] == "running" and len(one["items"]) == len(samples) == 1
    assert [s.identity.model_dump(exclude_none=True) for s in registry.sessions()] == [
        {k: v for k, v in current.items() if v is not None}]
    first = one["items"][0]
    done = advance_task(state.client, task)
    assert done["status"] == "done" and len(samples) == 2
    assert done["items"][1] == first
    assert state.client.delete(task).status_code == 204
    assert state.client.post(task + "/advance").status_code == 404
    target = done["items"][0]
    headers = {"X-Online-Recommendation": target["recommendation_token"]}
    with closing(TestClient(app)) as stranger:
        stranger.get("/api/viewing/session")
        assert stranger.post("/api/online/sessions", json=target["identity"], headers=headers).status_code == 409
    # Same candidate remains usable after cancellation, without transferring
    # or deleting the original session.
    prepared = state.client.post("/api/online/sessions", json=target["identity"], headers=headers)
    assert prepared.status_code == 201, prepared.text
    assert len(registry.sessions()) == 2
    state.client.delete("/api/online/sessions/" + prepared.json()["session_id"])
    movie["vod_play_url"] = movie["vod_play_url"].replace("正片$https://cdn.example/fast", "预告$https://cdn.example/fast")
    stale = state.client.post("/api/online/sessions", json=target["identity"], headers=headers)
    assert stale.status_code == 409 and stale.json()["code"] == "online_recommendation_changed"
    assert [s.id for s in registry.sessions()] == [playing.json()["session_id"]]
    movie["vod_play_url"] = movie["vod_play_url"].replace("预告$https://cdn.example/fast", "正片$https://cdn.example/fast")
    # Endpoint reconfiguration and restoration in the same second still
    # invalidates a recommendation even though all public fields now match.
    online.update_online_source(state.source["id"], {"enabled": False})
    online.update_online_source(state.source["id"], {"enabled": True})
    changed = state.client.post("/api/online/sessions", json=target["identity"], headers=headers)
    assert changed.status_code == 409 and changed.json()["code"] == "online_recommendation_changed"


def test_task_matching_rejects_conflicting_cast_region_language(environment, monkeypatch):
    current, copy = programme(environment)
    environment.show.update(vod_director="", vod_actor="演员甲", vod_area="美国", vod_lang="英语")
    copy.update(vod_director="", vod_actor="演员乙", vod_area="日本", vod_lang="日语")
    second = source("同名作品")
    samples = []
    upstream(monkeypatch, {environment.source["id"]: [environment.show], second["id"]: [copy]}, samples)
    task = start_task(environment.client, current)
    assert advance_task(environment.client, task)["status"] == "running"
    last = advance_task(environment.client, task)
    assert last["status"] == "done" and not last["items"] and not samples
    copy["vod_area"] = "美国"
    task = start_task(environment.client, current)
    advance_task(environment.client, task)
    assert len(advance_task(environment.client, task)["items"]) == 1


def test_network_error_retries_with_owned_recent_proof_and_verifies_cached_target(environment, monkeypatch):
    from app.online import cache

    state = environment
    current, copy = programme(state)
    second = source("备用")
    fake_sessions(monkeypatch)
    playing = state.client.post("/api/online/sessions", json=current)
    assert playing.status_code == 201
    assert state.client.delete("/api/online/sessions/" + playing.json()["session_id"]).status_code == 204
    upstream(monkeypatch, {second["id"]: [copy]}, [], slow=state.source["id"])
    task = start_task(state.client, current)
    advance_task(state.client, task)
    target = advance_task(state.client, task)["items"][0]
    # The saved identity alone is insufficient for an offline package.
    cached = {"id": "oc_" + "b" * 32, "detail": {"item": {"id": target["identity"]["item_id"],
              "source_id": second["id"], "kind": "tv", "title": "相同编号的新剧"}, "year": copy["vod_year"],
              "lines": [{"id": target["identity"]["line_id"], "entries": [
                  {"supported": True, "label": "EP02", "identity": target["identity"]}]}]}}
    monkeypatch.setattr(cache, "lookup", lambda *_: cached)
    headers = {"X-Online-Recommendation": target["recommendation_token"]}
    response = state.client.post("/api/online/sessions", json=target["identity"], headers=headers)
    assert response.status_code == 409 and response.json()["code"] == "online_recommendation_changed"
    cached["detail"]["item"]["title"] = copy["vod_name"]
    monkeypatch.setattr(cache, "package_files", lambda _cid: {
        "entry_name": "movie.mp4", "transport": "mp4", "files": ["movie.mp4"]})
    matching = state.client.post("/api/online/sessions", json=target["identity"],
                                 headers={**headers, "X-Online-Playback-Context": "cache"})
    assert matching.status_code == 201 and matching.json()["cached"] is True, matching.text
    # The old run has already closed so a device slot is available, but a
    # retried confirmation of this same recommendation must not allocate it.
    duplicate = state.client.post("/api/online/sessions", json=target["identity"], headers=headers)
    assert duplicate.status_code == 429 and duplicate.json()["code"] == "session_limit"
    assert len(service.registry.sessions()) == 1
    state.client.delete("/api/online/sessions/" + matching.json()["session_id"])
    with closing(TestClient(app)) as stranger:
        stranger.get("/api/viewing/session")
        assert stranger.delete(task).status_code == 404
        other_task = start_task(stranger, current)
        empty = advance_task(stranger, other_task)
        assert empty["status"] == "partial" and not empty["items"]


def test_two_devices_wait_and_rotate_without_losing_the_probe(environment, monkeypatch):
    import time
    from app.network.guard import NetworkOperation

    state = environment
    current, copy = programme(state)
    second = source("另一片库")
    samples = []
    upstream(monkeypatch, {state.source["id"]: [state.show], second["id"]: [copy]}, samples)
    with closing(TestClient(app)) as other:
        other.get("/api/viewing/session")
        one = start_task(state.client, current)
        two = start_task(other, current)
        with catalog._admit("existing-background", NetworkOperation(time.monotonic()+10), background=True):
            assert advance_task(state.client, one)["status"] == "queued"
            assert advance_task(other, two)["status"] == "queued"
        assert advance_task(other, two)["status"] == "queued"
        assert advance_task(state.client, one)["status"] == "running"
        assert advance_task(state.client, one)["status"] == "queued"
        assert advance_task(other, two)["status"] == "running"
        assert advance_task(state.client, one)["status"] == "done"
        assert advance_task(other, two)["status"] == "done"
    assert len(samples) == 2


def test_task_does_not_offer_a_tiny_sample_without_a_rate(environment, monkeypatch):
    state = environment
    current, copy = programme(state)
    second = source("小样片")
    upstream(monkeypatch, {state.source["id"]: [state.show], second["id"]: [copy]}, [])
    monkeypatch.setattr(alternatives.discovery_media, "sample_media", lambda *_: {
        "media_ms": 12, "sample_bytes": 512, "sample_kbps": None, "realtime_ratio": None})
    task = start_task(state.client, current)
    advance_task(state.client, task)
    result = advance_task(state.client, task)
    assert result["status"] == "done" and not result["items"]


def test_cancel_during_sample_aborts_operation_and_releases_background_slot(environment, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from app.network.guard import current_operation

    state = environment
    current, copy = programme(state)
    second = source("等待取消")
    upstream(monkeypatch, {state.source["id"]: [state.show], second["id"]: [copy]}, [])
    sampling, stopped = threading.Event(), threading.Event()

    def stalled(*_):
        op = current_operation()
        sampling.set()
        try:
            assert op.cancelled.wait(5), "cancel did not reach the media operation"
            op.check()
        finally:
            stopped.set()

    monkeypatch.setattr(alternatives.discovery_media, "sample_media", stalled)
    url = start_task(state.client, current)
    advance_task(state.client, url)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(state.client.post, url + "/advance", json={})
        assert sampling.wait(5)
        assert state.client.delete(url).status_code == 204
        assert stopped.wait(2)
        assert pending.result(timeout=5).status_code in {404, 409, 410, 499}
    assert not catalog.admission_stats()["background"]
