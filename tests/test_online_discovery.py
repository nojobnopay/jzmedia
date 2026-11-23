"""Discovery uses the real request/manifest checks with a controlled upstream."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import json
from threading import Event
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
import pytest

from app import store
from app.main import app
from app.network import guard
from app.network.client import NetworkRequestError
from app.online import diagnostic_probes as probes, discovery, discovery_directory as directory, discovery_hints, discovery_media, maccms
from app.store import online, online_network
import test_online_diagnostics as diagnostics_tests


upstream = diagnostics_tests.env


@pytest.fixture(autouse=True)
def fresh_directory_cache(monkeypatch):
    monkeypatch.setattr(directory, "_entries", ())
    monkeypatch.setattr(directory, "_retry_at", 0)
    monkeypatch.setattr(directory, "_last_success", 0)
    monkeypatch.setattr(directory, "_fingerprint", None)


@pytest.fixture
def env(upstream):
    upstream.rows = [dict(upstream.detail, vod_play_url="正片$https://cdn.example/sample.m3u8?signature=private-test")]
    upstream.detail = upstream.rows[0]
    upstream.client = TestClient(app)
    upstream.client.get("/api/viewing/session").raise_for_status()
    yield upstream
    upstream.client.close()


def probe(env, key="ikun"):
    return env.client.post(f"/api/online/discovery/{key}/probe", json={})


def enable(env, key, token=None):
    return env.client.post(f"/api/online/discovery/{key}/enable", json={"probe_token": token} if token else {})


def test_measured_media_and_selected_hosts_are_applied_once(env, monkeypatch):
    body = b"\x47\x00\x14\xff" * (discovery_media.SAMPLE_BYTES // 2)
    env.answers[("direct", "segment")] = (200, "video/mp2t", body)
    listed = env.client.get("/api/online/discovery").json()["items"]
    assert {v["id"] for v in listed} >= {"ikun", "modu", "wujin", env.source["id"]}
    assert not env.requests
    # Three seconds before the first chunk, then half a second for the tail:
    # the full-segment projection must pay those three seconds only once.
    ticks = iter((0, 0, 3, 3.5, 3.5))
    monkeypatch.setattr(discovery_media, "monotonic", lambda: next(ticks))
    report = probe(env).json()
    assert report["status"] == "measured", report
    assert report["sample_bytes"] == discovery_media.SAMPLE_BYTES
    assert report["sample_kbps"] > 0 and report["media_ms"] >= 0
    expected_seconds = 3.5 + discovery_media.SAMPLE_BYTES * .5 / (discovery_media.SAMPLE_BYTES - 8192)
    assert report["realtime_ratio"] == round(8 / expected_seconds, 3)
    assert report["allowed_hosts"] == ["cdn.example"]
    assert "private-test" not in str(report)
    assert len(online.list_online_sources()) == 1
    assert [r[0] for r in env.requests] == ["api", "media", "media"]
    token = report["probe_token"]
    assert enable(env, "modu", token).status_code == 409
    assert enable(env, "ikun", None).status_code == 409
    with ThreadPoolExecutor(2) as pool:
        sources = list(pool.map(lambda _: enable(env, "ikun", token).json(), range(2)))
    assert sources[0]["id"] == sources[1]["id"]
    assert sources[0]["enabled"] and set(sources[0]["allowed_hosts"]) == {"ikunzyapi.com", "cdn.example"}
    assert len(online.list_online_sources()) == 2
    assert probe(env).status_code == 409  # a fixed candidate cannot bypass the saved policy
    listed = env.client.get("/api/online/discovery").json()["items"]
    assert sources[0]["id"] in {v["id"] for v in listed} and "ikun" not in {v["id"] for v in listed}


def test_existing_policy_preserved_partial_media_and_no_fake_rate(env):
    source = online.update_online_source(env.source["id"], {"api_base_url": "https://ikunzyapi.com/api.php/provide/vod/",
                                                            "enabled": False, "allowed_hosts": ["other.example"]})
    sourced = env.client.get("/api/online/discovery").json()["items"]
    assert "ikun" not in {c["id"] for c in sourced}
    assert probe(env, "ikun").status_code == 409
    report = probe(env, source["id"]).json()
    assert report["status"] == "catalog_only" and report["reason"] == "target_host_not_allowed"
    assert not report["allowed_hosts"] and report["probe_token"]
    assert [r[0] for r in env.requests] == ["api"]
    assert enable(env, source["id"]).status_code == 409
    saved = enable(env, source["id"], report["probe_token"]).json()
    assert saved["id"] == source["id"] and saved["allowed_hosts"] == source["allowed_hosts"]
    report = probe(env, "wujin").json()
    assert report["status"] == "measured" and report["sample_kbps"] is None
    assert report["sample_bytes"] == 1200 and report["realtime_ratio"] is None


def test_application_and_saved_routes_used_and_changed_receipt_dropped(env):
    proxy = online_network.create_network_profile({"name": "测试代理", "mode": "proxy", "proxy_url": "http://proxy.example:8080"})
    online_network.update_online_network_default({"mode": "proxy", "profile_id": proxy["id"]})
    report = probe(env).json()
    assert report["status"] == "measured", report
    assert [r[1] for r in env.requests] == ["proxy", "proxy", "proxy"]
    online_network.update_online_network_default({"mode": "direct"})
    assert enable(env, "ikun", report["probe_token"]).json()["code"] == "online_source_changed"
    online_network.update_source_network_policy(env.source["id"], {"media": {"mode": "proxy", "profile_id": proxy["id"]}})
    env.requests.clear()
    assert probe(env, env.source["id"]).json()["status"] == "measured"
    assert [r[1] for r in env.requests] == ["direct", "proxy", "proxy"]


def test_candidate_receipt_session_token_and_disabled_gates(env):
    report = probe(env).json()
    other = TestClient(app)
    try:
        other.get("/api/viewing/session").raise_for_status()
        receipt = other.post("/api/online/discovery/ikun/enable", json={"probe_token": report["probe_token"]})
        assert receipt.status_code == 409
    finally:
        other.close()
    online.update_online_settings(False)
    env.requests.clear()
    assert probe(env).status_code == 409 and not env.requests
    store.set_setting("jzmedia_token", "secret")
    assert env.client.get("/api/online/discovery").status_code == 401
    assert enable(env, env.source["id"]).status_code == 401
    assert probe(env).status_code == 401


def test_probe_never_persists_failed_media_host(env):
    env.answers[("direct", "segment")] = (200, "video/mp2t", b"<html>not media</html>")
    result = probe(env).json()
    assert result["status"] == "catalog_only" and result["reason"] == "invalid_media_sample"
    assert result["sample_bytes"] == 0 and result["sample_kbps"] is None and result["allowed_hosts"] == []
    env.rows = [dict(env.detail, vod_play_url="正片$http://127.0.0.1/private.m3u8")]
    env.requests.clear()
    assert probe(env).json()["status"] == "catalog_only"
    assert [r[0] for r in env.requests] == ["api"]
    env.new = 502
    failed = probe(env).json()
    assert failed["status"] == "failed" and failed["probe_token"] is None and failed["catalog_ms"] is None


def test_refresh_exit_only_applies_to_public_directory(env, monkeypatch):
    path = "/api/online/discovery/refresh"
    proxy = online_network.create_network_profile({"name": "公开目录代理", "mode": "proxy",
                                                  "proxy_url": "http://proxy.example:8080"})
    rule = {"mode": "proxy", "profile_id": proxy["id"]}
    for mode in ("direct", "proxy"):
        env.answers[(mode, "segment")] = (200, "text/plain", json.dumps({"api_site": {
            "demo": {"name": "示例", "api": "https://example.org/api.php/provide/vod/"}}}).encode())
    for body, mode in (({"directory_route": rule}, "proxy"), ({}, "direct")):
        response = env.client.post(path, json=body)
        assert response.json()["directory_status"] == "updated"
        assert env.requests[-1][:2] == ("subscription", mode)
    online_network.update_online_network_default(rule)
    assert env.client.post(path, json={"directory_route": {"mode": "direct"}}).json()["directory_status"] == "updated"
    assert env.requests[-1][:2] == ("subscription", "direct")
    env.requests.clear()
    env.answers.clear()
    assert probe(env).json()["status"] == "measured"
    assert [r[:2] for r in env.requests] == [("api", "proxy"), ("media", "proxy"), ("media", "proxy")]
    assert online_network.get_online_network_default()["rule"] == rule

    # Removing a profile during a refresh invalidates the in-flight result and
    # a later attempt with the stale selection, even with a cached public list.
    online_network.update_online_network_default({"mode": "direct"})
    def remove_selected(policy, excluded):
        online_network.delete_network_profile(proxy["id"])
        return (("Changed", "https://changed.example/api.php/provide/vod/"),)
    monkeypatch.setattr(directory, "_fetch", remove_selected)
    for _ in range(2):
        assert env.client.post(path, json={"directory_route": rule}).status_code == 409
    assert not any(name == "Changed" for name, _ in directory.entries())
    assert env.client.post(path, json={"directory_route": {"mode": "proxy", "proxy_url": "http://example.org"}}).status_code == 422


@pytest.mark.parametrize("transport_failure", [True, False])
def test_cancelled_refresh_does_not_publish_or_suppress_a_retry(monkeypatch, transport_failure):
    request = guard.NetworkOperation(time.monotonic() + 5)
    rows = (("公开来源", "https://new.example/api.php/provide/vod"),)
    def fetch(policy, excluded):
        if not request.cancelled.is_set():
            request.abort()
            if transport_failure:
                raise NetworkRequestError("network_request_failed")
        return rows

    monkeypatch.setattr(directory, "_fetch", fetch)
    with guard.operation_context(request), pytest.raises(guard.NetworkOperationError, match="operation_cancelled"):
        directory.refresh(None, "route", set(), lambda: None)
    assert not directory.entries()
    assert directory.refresh(None, "route", set(), lambda: None) == ("updated", rows)


def test_concurrent_refreshes_return_their_own_exit_results(env, monkeypatch):
    proxy = online_network.create_network_profile({"name": "目录", "mode": "proxy", "proxy_url": "http://proxy.example"})
    direct_fetching, proxy_waiting, proxy_published = Event(), Event(), Event()
    refresh = directory.refresh

    def fetch(policy, excluded):
        if policy.mode == "direct":
            direct_fetching.set()
            assert proxy_waiting.wait(5)
        return ((policy.mode, f"https://{policy.mode}.example/api.php/provide/vod"),)

    def overlap(policy, *args):
        if policy.mode == "proxy":
            proxy_waiting.set()
        result = refresh(policy, *args)
        if policy.mode == "proxy":
            proxy_published.set()
        else:
            assert proxy_published.wait(5)
        return result

    monkeypatch.setattr(directory, "_fetch", fetch)
    monkeypatch.setattr(directory, "refresh", overlap)
    path = "/api/online/discovery/refresh"
    with ThreadPoolExecutor(2) as pool:
        direct = pool.submit(env.client.post, path, json={})
        assert direct_fetching.wait(5)
        routed = pool.submit(env.client.post, path, json={"directory_route": {"mode": "proxy", "profile_id": proxy["id"]}})
        for mode, future in (("direct", direct), ("proxy", routed)):
            report = future.result().json()
            assert report["directory_status"] == "updated"
            assert [r["name"] for r in report["items"] if r["id"].startswith("feed-")] == [mode]
    assert [name for name, _ in directory.entries()] == ["proxy"]


def test_public_directory_refresh_accepts_only_bounded_https_vod_and_falls_back(env, monkeypatch):
    cases = {"first": {"name": "示例新源", "api": "https://extra.example/api.php/provide/vod/"}}
    for i, url in enumerate(("https://extra.example/api.php/provide/vod", "http://other.example/api.php/provide/vod",
                             "https://127.0.0.1/api.php/provide/vod", "https://user@x.example/api.php/provide/vod",
                             "https://x.example/proxy/https://other.example/api.php/provide/vod",
                             "https://x.example/api.php/provide/vod?token=profile", "https://x.example/spider.js",
                             "https://x.example/api.php/provide/vod#params")):
        cases[str(i)] = {"name": "其他", "api": url}
    cases["spider"] = {"name": "爬虫", "api": "https://another.example/api.php/provide/vod", "ext": "spider.js"}
    payload = json.dumps({"api_site": cases}).encode()
    env.answers[("direct", "segment")] = (200, "text/plain", payload)
    path = "/api/online/discovery/refresh"
    fresh = env.client.post(path, json={}).json()
    dynamic = [item for item in fresh["items"] if item["id"].startswith("feed-")]
    assert fresh["directory_status"] == "updated" and [d["name"] for d in dynamic] == ["示例新源"]
    assert len(env.requests) == 1 and env.requests[0][:3] == ("subscription", "direct", directory.URL)
    assert env.client.post(path, json={}).json()["directory_status"] == "cached" and len(env.requests) == 1
    env.answers.clear()
    key = dynamic[0]["id"]
    report = probe(env, key).json()
    assert report["status"] == "measured"
    for empty in ({}, {"api_site": {}}, {"api_site": {"script": {"name": "爬虫", "api": "plugin.js"}}}):
        monkeypatch.setattr(directory, "_retry_at", 0)
        env.answers[("direct", "segment")] = (200, "text/plain", json.dumps(empty).encode())
        cached = env.client.post(path, json={}).json()
        assert cached["directory_status"] == "cached" and cached["items"] == fresh["items"]
        requests = len(env.requests)
        assert env.client.post(path, json={}).json() == cached and len(env.requests) == requests
    # A refresh can move an operator's name to a new endpoint; an old token cannot select it.
    monkeypatch.setattr(directory, "_retry_at", 0)
    cases["first"]["api"] = "https://replaced.example/api.php/provide/vod/"
    del cases["0"]
    env.answers[("direct", "segment")] = (200, "text/plain", json.dumps({"api_site": cases}).encode())
    replacement = env.client.post(path, json={}).json()
    new_key = next(d["id"] for d in replacement["items"] if d["name"] == "示例新源")
    assert new_key != key and enable(env, new_key, report["probe_token"]).status_code == 409
    assert enable(env, key, report["probe_token"]).status_code in (404, 409)
    assert len(online.list_online_sources()) == 1
    monkeypatch.setattr(directory, "_retry_at", 0)
    env.answers[("direct", "segment")] = (503, "text/plain", b"down")
    stale = env.client.post(path, json={}).json()
    assert stale["directory_status"] == "cached" and stale["items"] == replacement["items"]
    monkeypatch.setattr(directory, "_retry_at", 0)
    monkeypatch.setattr(directory, "_last_success", directory.time.monotonic() - 2 * 24 * 3600)
    fallback = env.client.post(path, json={}).json()
    assert fallback["directory_status"] == "fallback"
    assert "modu" in {i["id"] for i in fallback["items"]} and new_key not in {i["id"] for i in fallback["items"]}


def test_mp4_requires_byte_range_proof_and_bounds_probe(monkeypatch):
    size = discovery_media.SAMPLE_BYTES
    body = b"\x00\x00\x00\x18ftyp" + b"x" * (size - 8)
    observed = []
    status = 206

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        @contextmanager
        def stream(self, method, url, *, headers):
            observed.append(headers)
            with closing(httpx.Response(status, stream=httpx.ByteStream(body),
                                        headers={"Content-Length": str(size), "Content-Type": "video/mp4",
                                                 "Content-Range": f"bytes 0-{size - 1}/{size * 8}"})) as response:
                yield response

    monkeypatch.setattr(probes, "client_for", lambda *_: Client())
    entry = maccms.Entry("正片", "https://cdn.example/film.mp4", "mp4")
    result = discovery_media.sample_media({"allowed_hosts": ["cdn.example"]}, entry, None)
    assert result["sample_bytes"] == size and result["sample_kbps"] > 0 and result["realtime_ratio"] is None
    assert observed == [{"Range": f"bytes=0-{size - 1}"}]
    status = 200
    with pytest.raises(probes.ProbeFailure, match="upstream_ignored_range"):
        discovery_media.sample_media({"allowed_hosts": ["cdn.example"]}, entry, None)


@pytest.mark.parametrize("segment_size,wall,steady,expected", [
    (65536, 1.5, 0.6, 2.667),           # Complete one 4-second segment.
    (524288, 4.5, 1.0, .723),            # Prefix of a slow segment.
    (524288, 2.0, .0002, None),          # Too little post-first-chunk time to project.
    (4194304, 4.5, 1.0, None),           # Advertised range runs beyond the validated resource.
])
def test_hls_realtime_estimate_uses_segment_range_not_underlying_file(monkeypatch, segment_size, wall, steady, expected):
    offset = 4096
    sample_size = min(segment_size, discovery_media.SAMPLE_BYTES)
    total_file_size = 4 * 1024 * 1024
    body = b"\x47\x00\x14\xff" * (sample_size // 4)

    class Client:
        @contextmanager
        def stream(self, method, url, *, headers):
            assert headers["Range"] == f"bytes={offset}-{offset + sample_size - 1}"
            meta = {"Content-Length": str(sample_size), "Content-Type": "video/mp2t",
                    "Content-Range": f"bytes {offset}-{offset + sample_size - 1}/{total_file_size}"}
            with closing(httpx.Response(206, headers=meta, stream=httpx.ByteStream(body))) as response:
                yield response

    ticks = iter((0, wall - steady, wall))
    monkeypatch.setattr(discovery_media, "monotonic", lambda: next(ticks))
    sample = discovery_media._timed_prefix(Client(), "segment", "https://cdn.example/all.ts",
                                          (offset, offset + segment_size - 1), 4)
    assert sample["realtime_ratio"] == expected


def test_settings_probe_prioritizes_only_recent_valid_sample_after_enable(env, monkeypatch):
    body = b"\x47\x00\x14\xff" * (discovery_media.SAMPLE_BYTES // 4)
    env.answers[("direct", "segment")] = (200, "video/mp2t", body)
    for index in range(4):
        online.create_online_source({"provider": "maccms", "name": f"原来源{index}", "priority": 1,
                                     "enabled": True, "api_base_url": f"https://origin{index}.example/api.php/provide/vod"})
    initial = online.list_online_sources()
    assert discovery_hints.prioritize_measured(initial) == initial
    candidate = probe(env).json()
    chosen = enable(env, "ikun", candidate["probe_token"]).json()
    saved = online.list_online_sources()
    assert chosen not in saved[:3] and discovery_hints.prioritize_measured(saved)[0] == chosen
    env.requests.clear()
    online_network.update_source_network_policy(chosen["id"], {"media": {"mode": "direct"}})
    assert discovery_hints.prioritize_measured(saved) == saved
    assert not env.requests
    online.update_online_source(chosen["id"], {"enabled": False})
    report = probe(env, chosen["id"]).json()
    assert report["status"] == "measured"
    assert enable(env, chosen["id"], report["probe_token"]).json()["enabled"]
    saved = online.list_online_sources()
    assert discovery_hints.prioritize_measured(saved)[0]["id"] == chosen["id"]
    with monkeypatch.context() as patch:
        patch.setattr(discovery_hints, "_TTL", -1)
        assert discovery_hints.prioritize_measured(saved) == saved
    env.answers[("direct", "segment")] = (404, "text/plain", b"down")
    assert probe(env, chosen["id"]).json()["status"] == "catalog_only"
    assert discovery_hints.prioritize_measured(saved) == saved


def test_empty_feed_keeps_previously_reviewed_selection(env, monkeypatch):
    path = "/api/online/discovery/refresh"
    url = "https://catalogue.example/api.php/provide/vod"
    env.answers[("direct", "segment")] = (200, "text/plain", json.dumps({
        "api_site": {"one": {"name": "新目录", "api": url}}}).encode())
    fresh = env.client.post(path, json={}).json()
    key = next(row["id"] for row in fresh["items"] if row["api_base_url"] == url)
    env.answers.clear()
    report = probe(env, key).json()
    monkeypatch.setattr(directory, "_retry_at", 0)
    env.answers[("direct", "segment")] = (200, "text/plain", b'{"api_site":{}}')
    assert env.client.post(path, json={}).json()["directory_status"] == "cached"
    saved = enable(env, key, report["probe_token"]).json()
    assert saved["enabled"] and saved["api_base_url"] == url


def test_feed_includes_later_candidates_up_to_twenty_four():
    sites = {str(i): {"name": f"片源{i}", "api": f"https://vod{i}.example/api.php/provide/vod"} for i in range(30)}
    candidates = directory._decode(json.dumps({"api_site": sites}).encode(), "src_" + "0" * 32, set())
    assert [name for name, _ in candidates] == [f"片源{i}" for i in range(24)]


def test_feed_keeps_valid_rows_alongside_invalid_unicode():
    valid = {"name": "完整片源", "api": "https://valid.example/api.php/provide/vod"}
    raw = {"api_site": {"broken": {"api": "https://bad.example/api.php/provide/vod", "name": "\ud800"},
                        "other": {"api": "https://x.example/api.php/provide/vod", "name": "REPLACE_INVALID_UTF8"},
                        "valid": valid}}
    payload = json.dumps(raw).encode().replace(b"REPLACE_INVALID_UTF8", b"broken\xffname")
    assert directory._decode(payload, "src_" + "0" * 32, set()) == ((valid["name"], valid["api"]),)


def test_multipart_movie_cannot_be_made_playable_by_provisional_host(env):
    env.rows = [dict(env.detail, type_name="电影", vod_play_url="上$https://cdn.example/1.m3u8#下$https://cdn.example/2.m3u8")]
    report = probe(env).json()
    assert report["status"] == "catalog_only" and not report["allowed_hosts"]
    assert [r[0] for r in env.requests] == ["api"]


def test_bad_detail_rows_do_not_hide_a_playable_sample_on_same_page(env):
    valid = env.rows[0]
    broken = dict(valid, vod_id="998", vod_play_from="two$$$names")
    too_long = dict(valid, vod_id="999", vod_play_url="#".join(["a$https://cdn.example/1.m3u8"] * (maccms.MAX_LINE_ENTRIES + 1)))
    env.rows = [broken, too_long, valid]
    report = probe(env).json()
    assert report["status"] == "measured", report
    assert [r[0] for r in env.requests] == ["api", "media", "media"]


def test_disabled_saved_selection_rechecks_config_and_network_before_activation(env):
    sid = env.source["id"]
    online.update_online_source(sid, {"enabled": False})
    report = probe(env, sid).json()
    token = report["probe_token"]
    assert report["status"] == "measured" and token
    online.update_online_source(sid, {"api_base_url": "https://replacement.example/api.php/provide/vod"})
    assert enable(env, sid, token).json()["code"] == "online_source_changed"
    assert not online.get_online_source(sid)["enabled"]
    token = probe(env, sid).json()["probe_token"]
    online_network.update_source_network_policy(sid, {"media": {"mode": "direct"}})
    assert enable(env, sid, token).json()["code"] == "online_source_changed"
    assert not online.get_online_source(sid)["enabled"]
    newer = probe(env, sid).json()["probe_token"]
    with ThreadPoolExecutor(2) as pool:
        enabled = list(pool.map(lambda _: enable(env, sid, newer), range(2)))
    assert all(response.status_code == 200 and response.json()["id"] == sid for response in enabled)


def test_tries_another_title_after_unreachable_media_and_skips_empty_catalog(env, monkeypatch):
    original = discovery_media.sample_media
    attempts = []

    def sample(source, entry, policy, **kwargs):
        attempts.append(entry.url)
        if "unavailable" in entry.url:
            raise probes.ProbeFailure("http_404")
        return original(source, entry, policy, **kwargs)

    monkeypatch.setattr(discovery_media, "sample_media", sample)
    first = dict(env.rows[0], vod_play_url="#".join(
        f"第{number}集$https://cdn.example/unavailable{number}.m3u8" for number in range(1, 5)))
    second = dict(env.rows[0], vod_id="234")
    env.rows = [first, second]
    result = probe(env).json()
    assert result["status"] == "measured" and len(attempts) == 2
    env.rows = []
    empty = probe(env).json()
    assert empty["status"] == "empty" and not empty["probe_token"] and len(attempts) == 2


@pytest.mark.parametrize("alternate", ["fourth_title", "second_line"])
def test_failed_first_host_tries_another_origin_before_same_cdn(env, monkeypatch, alternate):
    first = env.rows[0]
    env.rows = [dict(first, vod_id=str(i), vod_play_url=f"正片$https://bad.example/{i}.m3u8") for i in range(3)]
    if alternate == "fourth_title":
        env.rows.append(dict(first, vod_id="4"))
    else:
        env.rows[0]["vod_play_from"] += "$$$alternate"
        env.rows[0]["vod_play_url"] += "$$$" + first["vod_play_url"]
    original = discovery_media.sample_media
    calls = []

    def sample(source, entry, policy, **kwargs):
        calls.append(entry.url)
        if "bad.example" in entry.url:
            raise probes.ProbeFailure("target_dns_failed", stage="manifest")
        return original(source, entry, policy, **kwargs)

    monkeypatch.setattr(discovery_media, "sample_media", sample)
    result = probe(env).json()
    assert result["attempts"] == [{"stage": "manifest", "reason": "target_dns_failed"}, {"stage": "media", "reason": "ok"}]
    assert len(calls) == 2 and result["allowed_hosts"] == ["cdn.example"] and result["status"] == "measured"
    assert "private-test" not in json.dumps(result)


def test_ready_page_links_before_supplemental_detail_and_bounded_empty_lookups(env):
    no_links = [dict(env.detail, vod_id=str(i), vod_play_url="") for i in range(6)]
    env.rows = [*no_links, env.detail]
    assert probe(env).json()["status"] == "measured"
    assert [r[0] for r in env.requests] == ["api", "media", "media"]
    env.requests.clear()
    env.rows = no_links
    report = probe(env).json()
    assert report["reason"] == "no_media_sample" and not report["attempts"]
    assert len(env.requests) == 4 and all(req[0] == "api" for req in env.requests)


@pytest.mark.parametrize(("ratios", "selected", "tried"), [
    ([.4, 1, 2], 1, 2),
    ([.4, None, 1.2], 2, 3),
    ([.4, .8, .6], 1, 3),
    ([.4, None, "http_404"], 0, 3),
    ([None, 1.2], 0, 1),
])
def test_slow_sample_keeps_best_evidence_and_only_its_hosts(env, monkeypatch, ratios, selected, tried):
    env.rows = [dict(env.detail, vod_id=str(i + 1), vod_pic=f"https://cover{i}.example/p.jpg",
                     vod_play_url=f"正片$https://cdn{i}.example/show.m3u8") for i in range(len(ratios))]
    outcomes = iter(ratios)

    def sample(source, entry, policy, *, host_review):
        host_review.reference(entry.url)
        ratio = next(outcomes)
        if ratio == "http_404":
            raise probes.ProbeFailure(ratio, stage="media")
        return {"realtime_ratio": ratio, "sample_bytes": 32768, "sample_kbps": 4096, "media_ms": 100}

    monkeypatch.setattr(discovery_media, "sample_media", sample)
    report = probe(env).json()
    assert report["status"] == "measured" and report["reason"] == "ok"
    assert [attempt["reason"] for attempt in report["attempts"]] == [
        value if value == "http_404" else "ok" for value in ratios[:tried]]
    assert report["realtime_ratio"] == ratios[selected]
    assert report["allowed_hosts"] == [f"cdn{selected}.example"]
    assert report["poster_hosts"] == [f"cover{selected}.example"]
    saved = enable(env, "ikun", report["probe_token"]).json()
    assert set(saved["allowed_hosts"]) == {"ikunzyapi.com", f"cdn{selected}.example"}


@pytest.mark.parametrize("first_is_slow", [True, False])
@pytest.mark.parametrize("deadline_stage", ["media", "detail"])
def test_tight_deadline_preserves_actual_attempts_and_the_parent_budget(env, monkeypatch, deadline_stage, first_is_slow):
    clock = SimpleNamespace(now=1000)
    clock.monotonic = lambda: clock.now
    monkeypatch.setattr(discovery, "time", clock)
    monkeypatch.setattr(guard, "time", clock)
    operation = guard.NetworkOperation(clock.now + 12)
    first = dict(env.detail, vod_id="1")
    second = dict(env.detail, vod_id="2", vod_play_url="" if deadline_stage == "detail"
                  else "正片$https://cdn.example/second.m3u8")
    calls = []

    def reach_cutoff():
        clock.now = operation.deadline - 0.1
        guard.check_operation()

    def sample(*args, **kwargs):
        calls.append("media")
        if len(calls) == 2:
            reach_cutoff()
        if first_is_slow:
            return {"realtime_ratio": .6, "sample_bytes": 32768, "sample_kbps": 65, "media_ms": 4000}
        raise probes.ProbeFailure("http_404", stage="manifest")

    def detail(*args):
        calls.append("detail")
        reach_cutoff()

    monkeypatch.setattr(discovery_media, "sample_media", sample)
    monkeypatch.setattr(probes, "_payload", detail)
    report = {"status": "catalog_only", "attempts": []}
    candidate = discovery.Candidate(env.source["id"], "已配置来源", env.source, ())
    try:
        discovery._sample_rows(candidate, [first, second], None, None, report, (), operation)
        operation.check()
    finally:
        operation.close()
    assert calls == ["media", deadline_stage]
    first_reason = "ok" if first_is_slow else "http_404"
    expected_attempts = [first_reason, "provider_timeout"] if deadline_stage == "media" else [first_reason]
    assert [a["reason"] for a in report["attempts"]] == expected_attempts
    assert report["reason"] == first_reason
    if first_is_slow:
        assert report["status"] == "measured" and report["realtime_ratio"] == .6


def test_measured_source_offers_only_the_selected_titles_poster(env, monkeypatch):
    first = dict(env.detail, vod_id="1", vod_pic="https://unused.example/p.jpg",
                 vod_play_url="正片$https://cdn.example/file.txt")
    bad = dict(env.detail, vod_id="2", vod_pic="https://failed.example/p.jpg",
               vod_play_url="正片$https://bad.example/1.m3u8")
    valid = dict(env.detail, vod_id="3", vod_pic="https://cover.example/p.jpg")
    env.rows = [first, bad, valid]
    original = discovery_media.sample_media

    def sample(source, entry, policy, **kwargs):
        if "bad.example" in entry.url:
            raise probes.ProbeFailure("network_timeout", stage="manifest")
        return original(source, entry, policy, **kwargs)

    monkeypatch.setattr(discovery_media, "sample_media", sample)
    result = probe(env).json()
    assert result["status"] == "measured" and result["poster_hosts"] == ["cover.example"]
    for host in ("unused.example", "failed.example"):
        rejected = env.client.post("/api/online/discovery/ikun/enable", json={
            "probe_token": result["probe_token"], "poster_hosts": [host]})
        assert rejected.status_code == 422


def test_extended_retest_only_widens_the_explicitly_requested_budgets(env, monkeypatch):
    from app.routers import online_discovery as router
    requests, samples = [], []
    read, sample = router._read, discovery_media.sample_media

    async def observe_read(*args, deadline_seconds):
        requests.append(deadline_seconds)
        return await read(*args, deadline_seconds=deadline_seconds)

    def delayed_sample(source, entry, policy, **kwargs):
        remaining = guard.current_operation().remaining()
        samples.append((remaining, policy.connect_timeout_seconds, policy.read_timeout_seconds))
        if remaining < 5:
            raise probes.ProbeFailure("network_timeout", stage="media")
        return sample(source, entry, policy, **kwargs)

    monkeypatch.setattr(router, "_read", observe_read)
    monkeypatch.setattr(discovery_media, "sample_media", delayed_sample)
    env.rows = [dict(env.detail, vod_id=str(i), vod_play_url=f"正片$https://cdn.example/{i}.m3u8") for i in range(5)]
    first = probe(env).json()
    assert first["status"] == "catalog_only" and first["attempts"] == [{"stage": "media", "reason": "provider_timeout"}] * 3
    path = "/api/online/discovery/ikun/probe"
    extended = env.client.post(path, json={"extended": True}).json()
    assert extended["status"] == "measured" and extended["sample_bytes"] == len(diagnostics_tests.SEGMENT)
    assert requests == [12, 20] and len(samples) == 4
    assert all(0 < row[0] <= 4 for row in samples[:3]) and 5 < samples[3][0] <= 8
    assert {(connect, read) for _, connect, read in samples[:3]} == {(3, 4)}
    assert samples[3][1:] == (5, 8)
    assert env.client.post(path, json={"extended": "true"}).status_code == 422
    assert requests == [12, 20]

    profile = online_network.create_network_profile({"name": "short timeouts", "mode": "direct",
                                                     "connect_timeout_seconds": 1.5, "read_timeout_seconds": 2})
    online_network.update_online_network_default({"mode": "direct", "profile_id": profile["id"]})
    limited = env.client.post(path, json={"extended": True}).json()
    assert limited["status"] == "measured" and samples[-1][1:] == (1.5, 2)


def test_mixed_sample_failures_keep_stage_counts_and_majority_reason(env, monkeypatch):
    first = env.rows[0]
    env.rows = [dict(first, vod_id=str(i), vod_play_url=f"第1集$https://cdn{i}.example/v.m3u8?token=never-print")
                for i in range(4)]
    original = probes.create_client

    def factory(policy, *, targets):
        client = original(policy, targets=targets)
        previous = client.stream

        @contextmanager
        def stream(method, url, **kwargs):
            if "cdn2.example" in url:
                raise discovery_media.NetworkRequestError("network_timeout")
            with previous(method, url, **kwargs) as response:
                response.headers["content-length"] = str(discovery_media.hls.MANIFEST_BYTES + 1)
                yield response

        client.stream = stream
        return client

    monkeypatch.setattr(probes, "create_client", factory)
    report = probe(env).json()
    assert report["status"] == "catalog_only" and report["reason"] == "manifest_limit"
    assert report["attempts"] == [{"stage": "manifest", "reason": reason}
                                  for reason in ("manifest_limit", "manifest_limit", "provider_timeout")]
    assert "never-print" not in json.dumps(report)


def test_catalog_and_media_statuses_keep_their_stage(env):
    env.new = 403
    report = probe(env).json()
    assert report["status"] == "failed" and report["reason"] == "http_403" and report["attempts"] == []
    env.new = 200
    env.answers[("direct", "segment")] = (404, "text/plain", b"not found")
    report = probe(env).json()
    assert report["status"] == "catalog_only" and report["attempts"] == [{"stage": "media", "reason": "http_404"}]


def test_later_large_range_does_not_block_sampling_the_first_segment(env):
    playlist = (b"#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:8\n#EXTINF:8,\nfirst.ts\n"
                b"#EXTINF:8,\n#EXT-X-BYTERANGE:41943040@0\nsecond.ts\n#EXT-X-ENDLIST\n")
    env.answers[("direct", "manifest")] = (200, "application/vnd.apple.mpegurl", playlist)
    result = probe(env).json()
    assert result["status"] == "measured" and result["attempts"] == [{"stage": "media", "reason": "ok"}]
    assert env.requests[-1][2].endswith("/first.ts")


@pytest.mark.parametrize("select_poster", [True, False])
def test_new_source_manifest_and_segment_domains_selective_poster(env, select_poster):
    env.rows = [dict(env.rows[0], vod_pic="https://covers.example/poster.jpg")]
    env.answers[("direct", "manifest")] = (200, "application/vnd.apple.mpegurl",
        diagnostics_tests.PLAYLIST.replace(b"first.ts", b"https://segments.example/video.ts"))
    report = probe(env).json()
    assert report["status"] == "measured" and report["allowed_hosts"] == ["cdn.example", "segments.example"]
    assert report["poster_hosts"] == ["covers.example"] and not report["missing_hosts"]
    assert all("covers.example" not in req[2] for req in env.requests)
    path = "/api/online/discovery/ikun/enable"
    body = {"probe_token": report["probe_token"], "poster_hosts": ["covers.example"] if select_poster else []}
    assert env.client.post(path, json={**body, "poster_hosts": ["arbitrary.example"]}).status_code == 422
    source = env.client.post(path, json=body).json()
    expected = {"ikunzyapi.com", "cdn.example", "segments.example"}
    assert set(source["allowed_hosts"]) == (expected | {"covers.example"} if select_poster else expected)


def test_saved_source_confirms_only_observed_media_hosts_before_retest(env):
    sid = env.source["id"]
    path = f"/api/online/discovery/{sid}/probe"
    env.answers[("direct", "manifest")] = (200, "application/vnd.apple.mpegurl",
        diagnostics_tests.PLAYLIST.replace(b"first.ts", b"https://segments.example/video.ts"))
    report = probe(env, sid).json()
    assert report["missing_hosts"] == ["segments.example"] and not report["allowed_hosts"]
    assert all("segments.example" not in req[2] for req in env.requests)
    token = report["probe_token"]
    assert env.client.post(path, json={"hosts": ["arbitrary.example"], "probe_token": token}).status_code == 422
    result = env.client.post(path, json={"probe_token": token, "hosts": report["missing_hosts"], "extended": True}).json()
    assert result["status"] == "measured" and result["allowed_hosts"] == report["missing_hosts"]
    assert "segments.example" not in online.get_online_source(sid)["allowed_hosts"]
    assert "segments.example" in enable(env, sid, result["probe_token"]).json()["allowed_hosts"]


@pytest.mark.parametrize("kind", ["map", "segment"])
def test_oversize_whole_hls_range_rejected_before_prefix_fetch(env, kind):
    ref = (b'#EXT-X-MAP:URI="init.mp4",BYTERANGE="41943040@0"\n#EXTINF:8,\ns.ts' if kind == "map" else
           b"#EXTINF:8,\n#EXT-X-BYTERANGE:41943040@0\ns.ts")
    playlist = b"#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:8\n" + ref + b"\n#EXT-X-ENDLIST\n"
    env.answers[("direct", "manifest")] = (200, "application/vnd.apple.mpegurl", playlist)
    report = probe(env).json()
    assert report["status"] == "catalog_only" and report["reason"] == "response_byte_limit", report
    assert len(env.requests) == 2


def test_consumed_selection_rejects_later_admin_edits_and_aba(env):
    sid = env.source["id"]
    online.update_online_source(sid, {"enabled": False})
    token = probe(env, sid).json()["probe_token"]
    assert enable(env, sid, token).json()["enabled"]
    assert enable(env, sid, token).status_code == 200
    online.update_online_source(sid, {"enabled": False})
    assert enable(env, sid, token).status_code == 409
    assert not online.get_online_source(sid)["enabled"]
    online.update_online_source(sid, {"enabled": True})
    assert enable(env, sid, token).status_code == 409


def test_saved_official_liangzi_page_uses_source_media_policy(env, monkeypatch):
    from app.network.targets import TargetPolicy
    source = online.update_online_source(env.source["id"], {
        "api_base_url": "https://cj.lziapi.com/api.php/provide/vod/", "allowed_hosts": ["cdn.example"]})
    env.rows = [dict(env.rows[0], vod_play_from="liangzi", vod_play_url="正片$https://cdn.example/share/1")]
    old_factory = probes.create_client

    def factory(policy, *, targets):
        client = old_factory(policy, targets=targets)
        client.snapshot = policy
        client.targets = TargetPolicy(policy.source_id, list(targets.allowed_hosts), resolver=lambda *_: ["8.8.8.8"])
        previous = client.stream

        @contextmanager
        def stream(method, url, **kwargs):
            if url != "https://cdn.example/share/1":
                with previous(method, url, **kwargs) as response:
                    yield response
                return
            assert policy.purpose == "media"
            env.requests.append((policy.purpose, policy.mode, url, {}, targets.allowed_hosts))
            body = b'<script>var main="/film.m3u8";</script>'
            with closing(httpx.Response(200, headers={"Content-Type": "text/html"}, stream=httpx.ByteStream(body),
                                        request=httpx.Request("GET", url))) as response:
                yield response
        client.stream = stream
        return client

    monkeypatch.setattr(probes, "create_client", factory)
    result = probe(env, source["id"]).json()
    assert result["status"] == "measured", result
    assert [req[2] for req in env.requests[1:]] == ["https://cdn.example/share/1", "https://cdn.example/film.m3u8",
                                                     "https://cdn.example/first.ts"]


def test_busy_scan_reports_retry_delay_without_fetching_catalog(env):
    from app.online import discovery
    for _ in range(3):
        assert discovery._SLOTS.acquire(blocking=False)
    try:
        response = probe(env)
        assert response.status_code == 429 and response.headers["retry-after"] == "1"
        assert not env.requests
    finally:
        for _ in range(3):
            discovery._SLOTS.release()


def test_existing_sources_keep_their_legacy_configuration_fingerprint_until_edited(env):
    sid = env.source["id"]
    state = online_network.load_online_network_state(sid)
    assert state["source_revision"] == 0
    previous_format = {key: value for key, value in state.items() if key != "source_revision"}
    assert online_network.online_network_fingerprint(previous_format) == online_network.online_network_fingerprint(state)
    online.update_online_source(sid, {"name": "临时名称"})
    online.update_online_source(sid, {"name": env.source["name"]})
    assert online_network.load_online_network_state(sid)["source_revision"] == 2


def test_builtin_candidate_does_not_silently_persist_unused_template_or_poster_hosts(env):
    env.rows = [dict(env.rows[0], vod_pic="https://p.lzp261118.com/poster.jpg")]
    result = probe(env, "liangzi").json()
    assert result["status"] == "measured" and result["poster_hosts"] == ["p.lzp261118.com"]
    saved = enable(env, "liangzi", result["probe_token"]).json()
    assert set(saved["allowed_hosts"]) == {"lzzyapi.com", "cdn.example"}


def _proxy_rule():
    profile = online_network.create_network_profile({"name": "试读出口", "mode": "proxy", "proxy_url": "http://proxy.example"})
    return {"mode": "proxy", "profile_id": profile["id"]}


@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("mode", ["direct", "proxy"])
def test_preview_route_requires_consent_and_atomically_applies_only_api_and_media(env, monkeypatch, saved, mode):
    proxy, direct = _proxy_rule(), {"mode": "direct", "profile_id": None}
    route, original = (proxy, direct) if mode == "proxy" else (direct, proxy)
    online_network.update_online_network_default(original)
    sid = env.source["id"]
    policy = online_network.update_source_network_policy(sid, {"image": original, "subtitle": direct})
    key = sid if saved else "ikun"
    path = f"/api/online/discovery/{key}"
    revoked, hints = [], []
    monkeypatch.setattr(discovery.service.registry, "revoke_source", revoked.append)
    monkeypatch.setattr(discovery_hints, "remember", lambda state, observation: hints.append(state))
    response = env.client.post(path + "/probe", json={"probe_route": route})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "measured" and report["tested_route"] == route
    assert [r[:2] for r in env.requests] == [("api", mode), ("media", mode), ("media", mode)]
    assert online_network.get_source_network_policy(sid) == policy
    token = {"probe_token": report["probe_token"]}
    for extra in ({}, {"apply_route": False}):
        assert env.client.post(path + "/enable", json={**token, **extra}).status_code == 422
    assert len(online.list_online_sources()) == 1 and not hints and not revoked
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: env.client.post(path + "/enable", json={**token, "apply_route": True}), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    target = responses[0].json()["id"]
    assert responses[1].json()["id"] == target and (target == sid) == saved
    rule = online_network.get_source_network_policy(target)
    assert rule["revision"] == (policy["revision"] if saved else 0) + 1
    assert rule["policy"]["api"] == rule["policy"]["media"] == route
    for purpose in ("default", "image", "subtitle", "subscription"):
        assert rule["policy"][purpose] == (policy["policy"][purpose] if saved else {"mode": "inherit", "profile_id": None})
    assert online_network.get_online_network_default()["rule"] == original
    assert revoked == ([sid] if saved else []) and len(hints) == 1
    assert hints[0] == online_network.load_online_network_state(target)
    assert env.client.post(path + "/enable", json=token).status_code == 422


def test_preview_route_host_consent_bound_to_exact_route_and_profile_revision(env):
    proxy = _proxy_rule()
    sid = env.source["id"]
    path = f"/api/online/discovery/{sid}"
    env.answers[("proxy", "manifest")] = (200, "application/vnd.apple.mpegurl",
        diagnostics_tests.PLAYLIST.replace(b"first.ts", b"https://segments.example/video.ts"))
    current = online_network.get_source_network_policy(sid)
    def probe_route(body):
        return env.client.post(path + "/probe", json={"probe_route": proxy, **body})

    report = probe_route({}).json()
    assert report["status"] == "catalog_only" and report["missing_hosts"] == ["segments.example"]
    consent = {"probe_token": report["probe_token"], "hosts": report["missing_hosts"]}
    assert env.client.post(path + "/enable", json={"probe_token": report["probe_token"], "apply_route": True}).status_code == 422
    count = len(env.requests)
    assert env.client.post(path + "/probe", json=consent).status_code == 409
    assert probe_route({**consent, "probe_route": {"mode": "direct"}}).status_code == 409
    assert len(env.requests) == count
    measured = probe_route(consent).json()
    assert measured["status"] == "measured" and measured["allowed_hosts"] == ["segments.example"]
    assert online_network.get_source_network_policy(sid) == current
    assert "segments.example" not in online.get_online_source(sid)["allowed_hosts"]
    online_network.update_network_profile(proxy["profile_id"], {"name": "已编辑"})
    assert probe_route(consent).status_code == 409
    body = {"probe_token": measured["probe_token"], "apply_route": True}
    assert env.client.post(path + "/enable", json=body).status_code == 409
    assert online_network.get_source_network_policy(sid) == current
    assert "segments.example" not in online.get_online_source(sid)["allowed_hosts"]
    fresh = probe_route({}).json()
    confirmed = probe_route({"probe_token": fresh["probe_token"], "hosts": fresh["missing_hosts"]}).json()
    assert confirmed["tested_route"] == proxy and confirmed["status"] == "measured"
    applied = env.client.post(path + "/enable", json={**body, "probe_token": confirmed["probe_token"]})
    assert "segments.example" in applied.json()["allowed_hosts"]
    assert online_network.get_source_network_policy(sid)["policy"]["media"] == proxy


@pytest.mark.parametrize("saved", [True, False])
def test_route_receipt_guards_inflight_edits_expiration_and_transaction_rollback(env, monkeypatch, saved):
    proxy = _proxy_rule()
    sid = env.source["id"]
    if saved:
        online.update_online_source(sid, {"enabled": False})
    path = "/api/online/discovery/" + (sid if saved else "ikun")
    probe_body = {"probe_route": proxy}
    before = online.list_online_sources()
    before_policy = online_network.get_source_network_policy(sid)
    read = discovery._read_catalogue
    def change_profile(*args):
        result = read(*args)
        online_network.update_network_profile(proxy["profile_id"], {"name": "已编辑"})
        return result
    with monkeypatch.context() as patch:
        patch.setattr(discovery, "_read_catalogue", change_profile)
        assert env.client.post(path + "/probe", json=probe_body).status_code == 409
    report = env.client.post(path + "/probe", json=probe_body).json()
    body = {"probe_token": report["probe_token"], "apply_route": True}
    update = online_network._update_source_policy
    def abort(*args):
        update(*args)
        raise online.OnlineNotFound("rollback")
    with monkeypatch.context() as patch:
        patch.setattr(online_network, "_update_source_policy", abort)
        assert env.client.post(path + "/enable", json=body).status_code == 409
    assert online.list_online_sources() == before and online_network.get_source_network_policy(sid) == before_policy
    with monkeypatch.context() as patch:
        patch.setattr(discovery._RECEIPTS[report["probe_token"]], "expires", 0)
        assert env.client.post(path + "/enable", json=body).status_code == 409
    assert env.client.post(path + "/enable", json=body).status_code == 200
    online_network.update_network_profile(proxy["profile_id"], {"name": "使用中变更"})
    assert env.client.post(path + "/enable", json=body).status_code == 409


def test_preview_rejects_invalid_routes_and_prior_profile_changes_hidden_by_override(env):
    proxy = _proxy_rule()
    sid = env.source["id"]
    path = f"/api/online/discovery/{sid}"
    for invalid in ({"mode": "inherit"}, {"mode": "proxy"}, {"mode": "direct", "profile_id": proxy["profile_id"]},
                    {"mode": "proxy", "proxy_url": "http://test.example"}):
        assert env.client.post(path + "/probe", json={"probe_route": invalid}).status_code == 422
    assert not env.requests
    online_network.update_source_network_policy(sid, {"api": proxy})
    report = env.client.post(path + "/probe", json={"probe_route": {"mode": "direct"}}).json()
    online_network.update_network_profile(proxy["profile_id"], {"read_timeout_seconds": 30})
    assert env.client.post(path + "/enable", json={"probe_token": report["probe_token"], "apply_route": True}).status_code == 409
    assert online_network.get_source_network_policy(sid)["policy"]["api"] == proxy
    plain = probe(env, sid).json()
    assert plain["tested_route"] is None
    assert env.client.post(path + "/enable", json={"probe_token": plain["probe_token"], "apply_route": True}).status_code == 422


def test_reapplying_same_rules_preserves_session_and_superseded_preview_cannot_enable(env, monkeypatch):
    sid = env.source["id"]
    route = _proxy_rule()
    path = f"/api/online/discovery/{sid}"
    old = online_network.update_source_network_policy(sid, {"media": route, "api": route})
    token = env.client.post(path + "/probe", json={"probe_route": route}).json()["probe_token"]
    revoked = []
    monkeypatch.setattr(discovery.service.registry, "revoke_source", revoked.append)
    assert env.client.post(path + "/enable", json={"probe_token": token, "apply_route": True}).status_code == 200
    assert online_network.get_source_network_policy(sid) == old and not revoked

    alternate = _proxy_rule()
    path = "/api/online/discovery/ikun"
    token = env.client.post(path + "/probe", json={"probe_route": alternate}).json()["probe_token"]
    online_network.delete_network_profile(alternate["profile_id"])
    assert env.client.post(path + "/enable", json={"probe_token": token, "apply_route": True}).status_code == 409
    assert env.client.post(path + "/probe", json={"probe_route": alternate}).status_code == 409
    assert len(online.list_online_sources()) == 1
