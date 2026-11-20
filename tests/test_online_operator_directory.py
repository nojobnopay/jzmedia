"""The operator directory accepts only published catalogue fields and uses the source API route."""

import json
import time

import httpx
import pytest

from app.network import client as network_client, guard, policy, targets
from app.online import operator_directory as directory
from app.online.source_templates import list_source_templates


OPERATOR = {item["id"]: item for item in list_source_templates()["items"]}
NEW_API = "https://new.example/api.php/provide/vod/"
BACKUP = "https://backup.example/api.php/provide/vod/"
PAGE = OPERATOR["liangzi"]["homepage"]


def row(label, url, copy=None):
    return (f'<div class="api-row"><span class="api-label">{label}</span>'
            f'<span class="api-box primary">{url}</span>'
            f'<button class="copy-btn" data-text="{url if copy is None else copy}">复制</button></div>')


def group(title, *rows):
    return f'<div class="api-group"><div class="api-group-title">{title} <span>推荐</span></div>{"".join(rows)}</div>'


def overview(*groups):
    return ('<html><div id="tab-home"><h3>接口总览</h3>' + "".join(groups) + '</div></html>').encode()


@pytest.mark.parametrize("operator", ["baofeng", "liangzi"])
def test_catalogue_accepts_new_official_domain_only_from_visible_labeled_json_group(operator):
    page = overview('<script>const url="https://ads.example/";</script>',
                    '<template>' + group('JSON 采集接口', row('主接口', BACKUP)) + '</template>',
                    group("M3U8 解析接口", row("主接口", "https://player.example/?url=")),
                    group("JSON 采集接口", row("备用接口", BACKUP), row("主接口", NEW_API)),
                    group("XML 采集接口", row("主接口", BACKUP + "at/xml/")))
    assert directory.parse_candidates(operator, page) == [
        {"url": NEW_API, "role": "primary"}, {"url": BACKUP, "role": "backup"}]


@pytest.mark.parametrize("entry", [
    group("JSON 采集接口", row("主接口", NEW_API, BACKUP)),
    group("JSON 采集接口", row("主接口", NEW_API), row("主接口", BACKUP)),
    group("JSON 采集接口", row("备用接口", BACKUP)),
    group("XML 采集接口", row("主接口", NEW_API)),
    group("JSON 采集接口", row("主接口", "https://127.0.0.1/api.php/provide/vod/")),
    group("JSON 采集接口", row("主接口", NEW_API + "?token=private")),
    group("JSON 采集接口", row("主接口", NEW_API + "at/xml/")),
    group("JSON 采集接口", row("主接口", NEW_API.replace('https:', 'http:'))),
    group("JSON 采集接口", row("主接口", "https://user:secret@example.com/api.php/provide/vod/")),
])
def test_ambiguous_unsafe_or_different_slots_have_no_candidate(entry):
    with pytest.raises(ValueError):
        directory.parse_candidates("liangzi", overview(entry))


def test_feifan_requires_matching_video_list_and_detail_examples_in_its_own_tab():
    first = "http://directory.example/api.php/provide/vod/"
    template = ('<div id="APPjson"><h1>app json接口</h1><div><p class="jiaocheng">'
                '1,视频列表地址 {first}?ac=list<br>2,视频详情地址 {second}?ac=detail</p></div></div>')
    assert directory.parse_candidates("feifan", template.format(first=first, second=first).encode()) == [
        {"role": "primary", "url": first}]
    with pytest.raises(ValueError):
        directory.parse_candidates("feifan", template.format(first=first, second=BACKUP).encode())


def test_recognition_uses_canonical_exact_reviewed_endpoint_not_source_name():
    source = {"provider": "maccms", "name": "自定义片库", "api_base_url": "https://cj.lziapi.com:443/api.php/provide/vod"}
    assert directory.recognize_template(source)["id"] == "liangzi"
    for addr in (NEW_API, "https://cj.lziapi.com/unrelated/", None, "invalid"):
        assert directory.recognize_template({**source, "name": "量子资源", "api_base_url": addr}) is None
    assert directory.recognize_template({**source, "provider": "open_catalog"}) is None


@pytest.fixture(params=["direct", "proxy"])
def api_route(monkeypatch, request):
    profile_id = "np_" + "1" * 32
    state = {"application": {"rule": {"mode": "direct"}, "revision": 2},
             "source": {"source_id": "src_" + "a" * 32, "policy": {}, "revision": 3}, "profiles": {}}
    if request.param == "proxy":
        state["source"]["policy"]["api"] = {"mode": "proxy", "profile_id": profile_id}
        state["profiles"][profile_id] = {
            "id": profile_id, "name": "test proxy", "mode": "proxy", "proxy_url": "http://127.0.0.1:12345",
            "connect_timeout_seconds": 5.0, "read_timeout_seconds": 15.0, "max_connections": 4,
            "ca_file": "", "ca_dir": "", "credentials_encrypted": "", "revision": 1, "created_at": 1, "updated_at": 1}
    monkeypatch.setattr(targets, "_system_addresses", lambda host, port: ("8.8.8.8",))
    return policy.resolve_policy_state(state, "api")


@pytest.mark.parametrize("scenario", ["published", "redirection", "cross_origin", "script_only", "invalid_encoding",
                                      "limit", "stream_limit", "http_failure", "timeout"])
def test_lookup_uses_fixed_page_and_bounded_same_route_and_never_outputs_untrusted_text(api_route, monkeypatch, scenario):
    sent = []
    good = overview(group("JSON 采集接口", row("主接口", NEW_API)))
    def handler(request):
        sent.append(str(request.url))
        assert str(request.url) == PAGE
        if scenario == "timeout":
            raise httpx.ReadTimeout("do not echo secret-token")
        if scenario in {"redirection", "cross_origin"}:
            location = "/member/secret-post" if scenario == "redirection" else "https://somewhere.example/"
            return httpx.Response(302, headers={"Location": location})
        data = b"<script>secret-token</script>" if scenario == "script_only" else good
        if scenario == "invalid_encoding":
            data = bytes([255])
        if scenario == "stream_limit":
            data = good + b' ' * directory._MAX_BYTES
        headers = {"Content-Type": "text/html; charset=utf-8"}
        if scenario == "limit":
            headers["Content-Length"] = str(directory._MAX_BYTES + 1)
        return httpx.Response(403 if scenario == "http_failure" else 200,
                              headers=headers, stream=httpx.ByteStream(data))

    def transport(snapshot, approved):
        assert snapshot is api_route and approved.allowed_hosts == {"lzizy.net"}
        return httpx.MockTransport(handler)

    monkeypatch.setattr(network_client, "PinnedTransport", transport)
    result = directory.lookup_candidates({**OPERATOR["liangzi"], "homepage": "https://wrong.example/"}, api_route,
                                         deadline=time.monotonic() + 3)
    assert sent == [PAGE]
    assert result["page_url"] == PAGE and result["section"] == "接口总览 / JSON 采集接口"
    assert abs(result["checked_at"] - time.time()) < 5
    assert "secret-token" not in json.dumps(result)
    assert "127.0.0.1" not in json.dumps(result)
    if scenario == "published":
        assert result["origin"] == "operator_page" and result["candidates"][0]["url"] == NEW_API
    else:
        assert result["origin"] == "release_template"
        assert result["candidates"] == [{"url": url, "role": role} for url, role in
                                        zip(OPERATOR["liangzi"]["known_api_urls"], ("primary", "backup"))]
        assert result["reason"] == ("page_layout_changed" if scenario in {"script_only", "invalid_encoding"} else "page_unavailable")


@pytest.mark.parametrize("cancel", ["parent", "child"])
def test_directory_disconnect_interrupts_the_page_socket_but_its_timeout_is_local(api_route, monkeypatch, cancel):
    parent = guard.NetworkOperation(time.monotonic() + 30)
    shutdowns = []
    class PageSocket:
        def shutdown(self, how):
            shutdowns.append(how)

    def respond(request):
        child = guard.current_operation()
        assert child is not parent and 0 < child.remaining() <= 6
        child.bind_socket(PageSocket())
        if cancel == "parent":
            parent.abort()
        else:
            child.abort("operation_timeout")
        return httpx.Response(200, headers={"Content-Type": "text/html"}, stream=httpx.ByteStream(b""))

    monkeypatch.setattr(network_client, "PinnedTransport", lambda *_args: httpx.MockTransport(respond))
    with guard.operation_context(parent):
        if cancel == "parent":
            with pytest.raises(guard.NetworkOperationError, match="operation_cancelled"):
                directory.lookup_candidates(OPERATOR["liangzi"], api_route, deadline=parent.deadline)
        else:
            evidence = directory.lookup_candidates(OPERATOR["liangzi"], api_route, deadline=parent.deadline)
            assert evidence["origin"] == "release_template"
            parent.check()
    assert len(shutdowns) == 1
    parent.close()
