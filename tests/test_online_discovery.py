"""Discovery uses the real request/manifest checks with a controlled upstream."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app import store
from app.main import app
from app.online import diagnostic_probes as probes, discovery_directory as directory, discovery_hints, discovery_media, maccms
from app.store import online, online_network
import test_online_diagnostics as diagnostics_tests


upstream = diagnostics_tests.env


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


def test_public_directory_refresh_accepts_only_bounded_https_vod_and_falls_back(env, monkeypatch):
    monkeypatch.setattr(directory, "_entries", ())
    monkeypatch.setattr(directory, "_retry_at", 0)
    monkeypatch.setattr(directory, "_last_success", 0)
    monkeypatch.setattr(directory, "_fingerprint", None)
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
    monkeypatch.setattr(directory, "_entries", ())
    monkeypatch.setattr(directory, "_retry_at", 0)
    monkeypatch.setattr(directory, "_last_success", 0)
    monkeypatch.setattr(directory, "_fingerprint", None)
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
    result = env.client.post(path, json={"probe_token": token, "hosts": report["missing_hosts"]}).json()
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
