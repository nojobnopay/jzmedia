"""Drive the assistant's actual catalogue, manifest and byte checks against controlled responses."""

from contextlib import contextmanager
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from app import store
from app.config import settings
from app.online import diagnostic_probes, diagnostics, maccms, service
from app.store import online, online_network


OLD = "https://old.example/api.php/provide/vod/"
NEW = "https://new.example/api.php/provide/vod/"
HELP = "https://operator.example/help"
MEDIA = "https://cdn.example/episode.m3u8?signature=private-test"
PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:8\n#EXTINF:8.0,\nfirst.ts\n#EXT-X-ENDLIST\n"
SEGMENT = b"\x47\x00\x14\xff" * 300


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "diagnose.db"))
    monkeypatch.setattr(store._base, "ensure_dirs", lambda: None)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    store.init_db()
    online.update_online_settings(True)
    source = online.create_online_source({"name": "测试来源", "enabled": True, "provider": "maccms",
                                          "api_base_url": OLD, "allowed_hosts": ["cdn.example"]})
    row = {"vod_id": "123", "vod_name": "原节目", "type_name": "国产剧", "vod_play_from": "first",
           "vod_play_url": f"第一集$https://cdn.example/unselected.mp4#第二集${MEDIA}"}
    item = online.upsert_online_item(source["id"], "123", "tv", "原节目")
    ep = online.upsert_online_episode(item["id"], maccms.episode_external_id("first", "第二集"), "第二集")
    context = {"source_id": source["id"], "external_id": "123", "item_id": item["id"],
               "line_id": maccms.Line("first", ()).id, "episode_id": ep["id"]}
    setup = SimpleNamespace(source=source, context=context, old=200, new=200, rows=[row], detail=row,
                            answers={}, requests=[], directories=[], plans=[], claimed=True)
    template = {"id": "liangzi"}
    monkeypatch.setattr(diagnostics.operator_directory, "recognize_template", lambda s: template if setup.claimed else None)

    def directory(_template, snapshot, *, deadline):
        setup.directories.append(snapshot)
        return {"origin": "operator_page", "page_url": HELP, "section": "JSON 接口",
                "reason": "published", "checked_at": 1000, "candidates": [{"url": NEW, "role": "primary"}]}

    def planner(question, evidence, options, defaults):
        setup.plans.append((question, evidence, options))
        return getattr(setup, "selected", defaults)

    monkeypatch.setattr(diagnostics.operator_directory, "lookup_candidates", directory)
    monkeypatch.setattr(diagnostics, "choose_tools", planner)

    class Client:
        def __init__(self, policy, targets):
            self.policy, self.targets = policy, targets

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def request(self, method, url, *, params, headers):
            self.targets._canonical_url(url)
            setup.requests.append((self.policy.purpose, self.policy.mode, url, params, self.targets.allowed_hosts))
            status = setup.old if url == OLD else setup.new
            if isinstance(status, list):
                status = status.pop(0)
            if not isinstance(status, int):
                return httpx.Response(200, content=status)
            rows = [setup.detail] if "ids" in params else setup.rows
            return httpx.Response(status, json={"code": 1, "list": rows})

        @contextmanager
        def stream(self, method, url, *, headers=None):
            self.targets._canonical_url(url)
            setup.requests.append((self.policy.purpose, self.policy.mode, url, headers, self.targets.allowed_hosts))
            kind = "manifest" if ".m3u8" in url else "segment"
            default = ((200, "application/vnd.apple.mpegurl", PLAYLIST) if kind == "manifest" else
                       (200, "video/mp2t", SEGMENT))
            status, mime, body = setup.answers.get((self.policy.mode, kind), default)
            response = httpx.Response(status, headers={"content-type": mime, "content-length": str(len(body))},
                                      stream=httpx.ByteStream(body), request=httpx.Request(method, url))
            try:
                yield response
            finally:
                response.close()

    monkeypatch.setattr(diagnostic_probes, "create_client", lambda snapshot, *, targets: Client(snapshot, targets))
    return setup


def run(env, context=None, *, saved=False):
    result = diagnostics.diagnose(env.source, context or {}, verify_saved=saved)
    assert "private-test" not in json.dumps(result, ensure_ascii=False)
    return result


def test_verified_update_uses_same_effective_route_and_checks_original_episode_without_writing(env):
    env.old = 410
    profile = online_network.create_network_profile({"name": "目录代理", "mode": "proxy", "proxy_url": "http://proxy.example:3128"})
    online_network.update_source_network_policy(env.source["id"], {"api": {"mode": "proxy", "profile_id": profile["id"]}})
    before = online_network.load_online_network_state(env.source["id"])
    result = run(env, env.context)
    assert result["status"] == "update_available" and result["candidate_url"] == NEW and result["operator_id"] == "liangzi"
    assert result["network_fingerprint"] == online_network.online_network_fingerprint(before)
    assert online_network.load_online_network_state(env.source["id"]) == before
    assert [req[2] for req in env.requests] == [OLD, OLD, NEW, NEW, MEDIA, "https://cdn.example/first.ts"]
    assert env.requests[2][-1] == frozenset([urlsplit(NEW).hostname])
    assert env.directories[0].mode == env.requests[0][1] == env.requests[2][1]
    assert any(row.get("page_url") == HELP and row["checked_at"] == 1000 for row in result["evidence"])
    assert result["evidence"][-1]["tool"] == "probe_media"


@pytest.mark.parametrize("response", [b"<html>OK</html>", b'{"code":1,"list":[]}',
                                      b'{"code":1,"list":[{"vod_id":"x","vod_name":""}]}', 503])
def test_candidate_needs_a_real_nonempty_catalogue(env, response):
    env.old, env.new = 404, response
    result = run(env)
    assert result["status"] == "inconclusive" and not result["candidate_url"]


@pytest.mark.parametrize("old", [403, 401, 429, 407, b'{"code":0,"list":[]}'])
def test_access_or_provider_business_failure_does_not_suggest_a_new_domain(env, old):
    env.old = old
    result = run(env)
    assert result["status"] == "inconclusive" and not env.directories
    assert [req[2] for req in env.requests] == [OLD]


def test_retry_recovers_without_update_and_model_can_select_additional_real_tools(env):
    env.old, env.selected = [503, 200], ["operator_directory", "source_history"]
    context = {"question": "目录? https://unsafe.test/secret 服务原片名"}
    result = run(env, context)
    assert result["status"] == "healthy" and result["candidate_url"] is None
    assert env.plans[0][0] == "目录" and "secret" not in str(env.plans)
    assert env.directories and [req[2] for req in env.requests] == [OLD, OLD]
    assert "source_history" in [row["tool"] for row in result["evidence"]]


def test_nonmatching_candidate_identity_or_unapproved_media_precludes_even_catalogue_repair(env):
    env.old = 404
    env.detail = {**env.detail, "vod_name": "不同节目"}
    assert run(env, env.context)["status"] == "inconclusive"
    assert all(req[0] == "api" for req in env.requests)
    env.detail.update(vod_name="原节目", vod_play_url="第二集$https://unknown.example/denied.m3u8")
    assert run(env, env.context)["status"] == "inconclusive"
    assert all(req[0] == "api" for req in env.requests)


def proxy_media(source):
    profile = online_network.create_network_profile({"name": "测试代理", "mode": "proxy", "proxy_url": "http://proxy.example:3128"})
    online_network.update_source_network_policy(source["id"], {"media": {"mode": "proxy", "profile_id": profile["id"]}})


def test_proxy_failure_requires_explicit_comparison_and_matching_segment_to_propose(env):
    proxy_media(env.source)
    env.answers[("proxy", "segment")] = (503, "text/html", b"failure")
    first = run(env, env.context)
    assert first["status"] == "media_problem" and first["can_compare_direct"] and not first["candidate_url"]
    assert all(req[1] == "proxy" for req in env.requests if req[0] == "media")
    env.requests.clear()
    second = run(env, {**env.context, "compare_direct": True})
    assert second["status"] == "media_route_available" and not second["candidate_url"]
    assert [req[2] for req in env.requests if req[1] == "proxy"] == [MEDIA, "https://cdn.example/first.ts"]
    assert [req[2] for req in env.requests if req[1] == "direct" and req[0] == "media"] == [MEDIA, "https://cdn.example/first.ts"]
    assert online_network.get_source_network_policy(env.source["id"])["policy"]["media"]["mode"] == "proxy"
    env.answers[("direct", "manifest")] = (200, "application/vnd.apple.mpegurl", PLAYLIST.replace(b"first.ts", b"other.ts"))
    assert run(env, {**env.context, "compare_direct": True})["status"] == "media_problem"


def test_sample_rejects_fake_success_and_checks_allowlist_before_following_manifest(env):
    env.answers[("direct", "segment")] = (200, "application/octet-stream", b"Authentication required")
    result = run(env, env.context)
    assert result["status"] == "media_problem" and "媒体字节" in result["answer"]
    env.answers[("direct", "manifest")] = (200, "application/vnd.apple.mpegurl", PLAYLIST.replace(b"first.ts", b"https://bad.example/s.ts"))
    env.requests.clear()
    result = run(env, env.context)
    assert result["status"] == "media_problem" and "允许列表" in result["answer"]
    assert all("bad.example" not in req[2] for req in env.requests)


def test_retest_and_private_context_do_not_call_model_and_device_error_keeps_its_scope(env):
    result = run(env, {**env.context, "error_code": "online_decode_unsupported"}, saved=True)
    assert result["status"] == "device_problem" and not env.plans and not env.directories
    run(env, {**env.context, "allow_model": False})
    assert not env.plans
    env.old = 410
    assert run(env, env.context, saved=True)["status"] == "inconclusive"
    assert not env.directories and all(req[2] != NEW for req in env.requests)


def test_cache_and_disabled_or_changed_sources_do_not_read_the_network(env):
    assert run(env, {"cache_only": True})["status"] == "inconclusive"
    assert "缓存文件" in run(env, {"error_code": "online_cache_corrupt"})["answer"]
    online.update_online_settings(False)
    assert run(env)["status"] == "disabled"
    online.update_online_source(env.source["id"], {"name": "new"})
    assert run(env)["status"] == "inconclusive" and not env.requests


def test_candidate_media_must_be_allowed_both_before_and_after_confirmation(env):
    env.old = 404
    for host in ("old.example", "new.example"):
        env.detail.update(vod_play_url=f"第二集$https://{host}/cannot-be-read.m3u8")
        result = run(env, env.context)
        assert result["status"] == "inconclusive"
    assert all(req[0] == "api" for req in env.requests)


def test_known_failed_source_must_check_candidate_even_if_model_selects_only_history(env):
    env.old, env.selected = 410, ["source_history"]
    result = run(env)
    assert result["status"] == "update_available" and len(env.directories) == 1
    env.claimed = False
    env.directories.clear()
    assert run(env)["status"] == "inconclusive" and not env.directories


def test_episode_and_item_from_another_source_cannot_drive_any_probe(env):
    other = online.create_online_source({"name": "同名", "enabled": True, "provider": "maccms", "api_base_url": NEW})
    item = online.upsert_online_item(other["id"], "123", "tv", "原节目")
    result = run(env, {**env.context, "item_id": item["id"]})
    assert result["status"] == "inconclusive" and not env.requests
    result = run(env, {"item_id": item["id"], "external_id": "123"})
    assert result["status"] == "inconclusive" and not env.requests


@pytest.mark.parametrize("stop", ["model", "page", "candidate", "direct"])
def test_guard_stops_future_external_tools_when_permissions_or_configuration_change(env, stop):
    context = {}
    if stop == "direct":
        proxy_media(env.source)
        env.answers[("proxy", "segment")] = (503, "text/html", b"failure")
        context = {**env.context, "compare_direct": True}
    elif stop in {"page", "candidate"}:
        env.old = 410

    def guard():
        stopped = {
            "model": bool(env.requests), "page": bool(env.plans), "candidate": bool(env.directories),
            "direct": any(req[1] == "proxy" and "first.ts" in req[2] for req in env.requests),
        }
        if stopped[stop]:
            raise service.PlaybackError("online_access_revoked", 401)

    with pytest.raises(service.PlaybackError, match="online_access_revoked"):
        diagnostics.diagnose(env.source, context, guard=guard)
    if stop == "model":
        assert not env.plans
    if stop == "page":
        assert not env.directories
    if stop == "candidate":
        assert all(req[2] != NEW for req in env.requests)
    if stop == "direct":
        assert all(req[1] == "proxy" for req in env.requests if req[0] == "media")
