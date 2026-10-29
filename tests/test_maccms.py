"""MacCMS contract parsing uses synthetic JSON, never a public provider."""

import json
from types import SimpleNamespace

import pytest

from app.online import maccms
from app.online.providers import ProviderError


SOURCE = {"id": "src_" + "1" * 32, "provider": "maccms", "name": "Synthetic",
          "api_base_url": "https://api.example/provide/vod/", "allowed_hosts": ["api.example", "cdn.example"],
          "provider_options": {"search_action": "list", "detail_action": "detail"}}
MP4 = "https://cdn.example/movie.mp4?sig=never-expose-this-signature"
HLS = "https://cdn.example/movie.m3u8?sig=other-signature"


def movie(**changes):
    return {"vod_id": "movie-1", "vod_name": "合成电影", "type_name": "剧情片", "vod_year": "2026",
            "vod_content": "<p>简介 <b>正文</b></p><script>doNotRun()</script>",
            "vod_play_from": "MP4$$$HLS", "vod_play_url": f"正片${MP4}$$$正片${HLS}", **changes}


def series(**changes):
    return movie(**{"vod_id": "show-1", "type_name": "国产剧", "vod_play_from": "main$$$backup",
                    "vod_play_url": f"第1集${MP4}#第2集${HLS}$$$第1集${HLS}#第2集${MP4}", **changes})


def assert_category(function, category="invalid_provider_response"):
    with pytest.raises(ProviderError) as caught:
        function()
    assert caught.value.category == category
    assert "signature" not in str(caught.value)


def test_all_lines_and_query_signed_mp4_hls_remain_server_only_values():
    result = maccms.parse_detail(SOURCE, movie())
    assert result.kind == "movie"
    assert [line.name for line in result.lines] == ["MP4", "HLS"]
    assert [line.entries[0].transport for line in result.lines] == ["mp4", "hls"]
    assert result.lines[0].entries[0].url == MP4
    assert "signature" not in repr(result)
    assert result.description == "简介 正文"


@pytest.mark.parametrize("category", ["喜剧片", "剧情片", "动画电影", "动作片", "电影"])
def test_explicit_film_categories_precede_the_series_keyword_heuristic(category):
    result = maccms.parse_detail(SOURCE, movie(type_name=category))
    assert result.kind == "movie"
    assert all(entry.transport for line in result.lines for entry in line.entries)


@pytest.mark.parametrize("category", ["国产剧", "连续剧", "动漫", "综艺"])
def test_explicit_series_categories_preserve_episode_semantics(category):
    assert maccms.parse_detail(SOURCE, series(type_name=category)).kind == "tv"


def test_unclassified_multiple_entries_can_supply_the_series_fallback():
    assert maccms.parse_detail(SOURCE, series(type_name="")).kind == "tv"


def test_movie_line_with_multiple_parts_does_not_offer_ambiguous_playable_choices():
    result = maccms.parse_detail(SOURCE, movie(vod_play_from="parts$$$whole",
        vod_play_url=f"上部${MP4}#下部${MP4}&part=2$$$完整${HLS}"))
    assert result.kind == "movie"
    assert [entry.label for entry in result.lines[0].entries] == ["上部", "下部"]
    assert all(entry.url == "" and entry.transport is None and entry.reason == "unsupported_media"
               for entry in result.lines[0].entries)
    assert result.lines[1].entries[0].transport == "hls"


def test_line_and_episode_identity_ignore_sort_order_and_signed_url_changes():
    original = maccms.parse_detail(SOURCE, series())
    changed = maccms.parse_detail(SOURCE, series(vod_play_from="backup$$$main",
        vod_play_url=f"第2集${MP4}&new=1#第1集${HLS}&new=1$$$第2集${HLS}&new=1#第1集${MP4}&new=1"))
    def identities(value):
        return {(line.name, entry.label): (line.id, maccms.episode_external_id(line.name, entry.label))
                for line in value.lines for entry in line.entries}
    assert original.kind == changed.kind == "tv"
    assert identities(original) == identities(changed)
    assert len(set(value[1] for value in identities(original).values())) == 4


@pytest.mark.parametrize("url,reason", [("https://cdn.example/watch.html", "unsupported_media"),
    ("javascript:alert(1)", "unsupported_media"), ("file:///tmp/movie.mp4", "unsupported_media"),
    ("https://user:password@cdn.example/movie.mp4", "unsupported_media"),
    ("https://other.example/movie.mp4", "target_host_not_allowed"),
    ("https://cdn.example/movie.mp4\n", "unsupported_media")])
def test_unsupported_or_unauthorized_media_are_disabled_without_copying_url(url, reason):
    result = maccms.parse_detail(SOURCE, movie(vod_play_from="main", vod_play_url="正片$" + url))
    entry = result.lines[0].entries[0]
    assert entry.transport is None and entry.url == "" and entry.reason == reason


@pytest.mark.parametrize("changes", [
    {"vod_play_from": "same$$$same"},
    {"vod_play_from": "main", "vod_play_url": f"第1集${MP4}#第1集${HLS}"},
    {"vod_play_from": "main", "vod_play_url": f"{MP4}#{HLS}"},
    {"vod_play_from": "main", "vod_play_url": f"{HLS}#{HLS}"},
])
def test_ambiguous_labels_are_rejected_instead_of_using_order_identity(changes):
    assert_category(lambda: maccms.parse_detail(SOURCE, movie(**changes)), "provider_ambiguous_identity")


def test_exact_duplicate_entries_preserve_the_first_position_and_identity_on_refresh():
    rows = [f"第3期${HLS}", f"第1期${MP4}", f"第3期${HLS}", f"第2期${HLS}", f"第1期${MP4}"]
    original = maccms.parse_detail(SOURCE, series(vod_play_from="main", vod_play_url="#".join(rows)))
    refreshed = maccms.parse_detail(SOURCE, series(vod_play_from="main",
        vod_play_url="#".join(raw + "&new=1" for raw in rows)))
    assert [entry.label for entry in original.lines[0].entries] == ["第3期", "第1期", "第2期"]
    assert [entry.url for entry in original.lines[0].entries] == [HLS, MP4, HLS]
    assert [entry.url for entry in refreshed.lines[0].entries] == [HLS + "&new=1", MP4 + "&new=1", HLS + "&new=1"]
    def identities(detail):
        return [(line.id, maccms.episode_external_id(line.name, entry.label))
                for line in detail.lines for entry in line.entries]
    assert identities(original) == identities(refreshed)


@pytest.mark.parametrize("first,second", [
    (f"第1期${HLS}", f"第1期${HLS}&new=1"),
    (f"第1期${HLS}", f"<b>第1期</b>${HLS}"),
    ("第1期$https://cdn.example/watch.html?part=1", "第1期$https://cdn.example/watch.html?part=2"),
    ("第1期$javascript:alert(1)", "第1期$file:///tmp/movie.mp4"),
])
def test_same_plain_label_with_different_original_entry_remains_ambiguous(first, second):
    assert_category(lambda: maccms.parse_detail(SOURCE, series(vod_play_from="main",
        vod_play_url=first + "#" + second)), "provider_ambiguous_identity")


def test_single_unlabelled_film_has_a_stable_default_label():
    result = maccms.parse_detail(SOURCE, movie(vod_play_from="main", vod_play_url=MP4))
    assert result.lines[0].entries[0].label == "正片"


@pytest.mark.parametrize("changes", [
    {"vod_id": "1,2"}, {"vod_name": ""}, {"vod_name": "x" * 513},
    {"vod_play_from": ["main"]}, {"vod_play_url": {}},
    {"vod_play_from": "one", "vod_play_url": f"正片${MP4}$$$正片${HLS}"},
    {"vod_play_from": "$$$".join(str(i) for i in range(17)), "vod_play_url": "$$$".join(MP4 for _ in range(17))},
])
def test_malformed_fields_fail_with_fixed_category(changes):
    assert_category(lambda: maccms.parse_detail(SOURCE, movie(**changes)))


@pytest.mark.parametrize("count", [501, 783, 2000])
def test_long_series_line_uses_the_existing_total_episode_budget(count):
    result = maccms.parse_detail(SOURCE, series(vod_play_from="main",
        vod_play_url="#".join(f"第{i}期${HLS}" for i in range(1, count + 1))))
    assert result.kind == "tv"
    assert len(result.lines) == 1
    assert len(result.lines[0].entries) == count
    assert result.lines[0].entries[0].label == "第1期"
    last = result.lines[0].entries[-1]
    assert last.label == f"第{count}期" and last.url == HLS and last.transport == "hls"


def test_long_series_collapses_only_exact_duplicates_without_reordering():
    unique = [f"第{i}期${HLS}" for i in range(1, 601)]
    rows = unique[:182] + unique[:182] + unique[182:] + unique[:1]
    assert len(rows) == 783
    result = maccms.parse_detail(SOURCE, series(vod_play_from="main", vod_play_url="#".join(rows)))
    assert [entry.label for entry in result.lines[0].entries] == [f"第{i}期" for i in range(1, 601)]


@pytest.mark.parametrize("counts", [(2001,), (1000, 1001)])
def test_episode_count_is_bounded_before_splitting_or_validating_media(monkeypatch, counts):
    def unexpected_entry(*_args, **_kwargs):
        pytest.fail("an over-budget detail must not validate any media URL")
    monkeypatch.setattr(maccms, "_entry", unexpected_entry)
    row = series(vod_play_from="$$$".join(f"line{i}" for i in range(len(counts))),
        vod_play_url="$$$".join("#".join(f"e{i}${MP4}" for i in range(count)) for count in counts))
    assert_category(lambda: maccms.parse_detail(SOURCE, row), "provider_entry_limit")


def test_exact_duplicates_cannot_bypass_the_raw_episode_budget(monkeypatch):
    def unexpected_entry(*_args, **_kwargs):
        pytest.fail("an over-budget detail must be rejected before deduplication")
    monkeypatch.setattr(maccms, "_entry", unexpected_entry)
    assert_category(lambda: maccms.parse_detail(SOURCE, series(vod_play_from="main",
        vod_play_url="#".join([f"第1期${HLS}"] * 2001))), "provider_entry_limit")


def test_long_series_does_not_assign_an_order_identity_to_duplicate_labels():
    entries = [f"第{i}期${HLS}" for i in range(1, 783)] + [f"第1期${MP4}"]
    assert_category(lambda: maccms.parse_detail(SOURCE, series(vod_play_from="main",
        vod_play_url="#".join(entries))), "provider_ambiguous_identity")


def test_body_byte_budget_remains_independent_of_episode_count():
    payload = b'{"code":1,"list":[]}'
    body = payload + b" " * (maccms.MAX_BODY - len(payload))
    assert len(body) == 2 * 1024**2
    assert maccms.decode(body)["list"] == []
    assert_category(lambda: maccms.decode(body + b" "), "provider_response_limit")


@pytest.mark.parametrize("body", [b"<html>private-token</html>", b"[]", b'{"code":true,"list":[]}',
    b'{"code":false,"list":[]}', b'{"code":1.0,"list":[]}', b'{"code":null,"list":[]}',
    b'{"code":[],"list":[]}', b'{"list":[]}',
    b'{"code":1,"list":[],"list":[{}]}', b'{"code":1,"list":[],"bad":NaN}',
    b'{"code":1,"list":[null]}', b'{"code":1,"list":{}}', b"\xff"])
def test_non_json_or_malformed_json_is_not_a_catalogue(body):
    assert_category(lambda: maccms.decode(body))


def test_error_response_does_not_turn_into_empty_success_or_reflect_upstream_message():
    assert_category(lambda: maccms.decode(b'{"code":0,"msg":"private-token","list":[]}'),
                    "provider_upstream_error")
    assert_category(lambda: maccms.decode(b"x" * (maccms.MAX_BODY + 1)), "provider_response_limit")
    assert_category(lambda: maccms.decode(json.dumps({"code": 1, "list": [movie()] * 101}).encode()))


def test_search_does_not_assign_item_identity_and_accepts_numeric_page_fields(monkeypatch):
    recorded = []
    def fetch(source, params):
        recorded.append((source, params))
        return {"code": 1, "page": "2", "pagecount": "3", "total": "21", "list": [movie()]}
    monkeypatch.setattr(maccms, "fetch", fetch)
    result = maccms.search(SOURCE, "合成", 2)
    assert result["page"] == 2 and result["page_count"] == 3 and result["total"] == 21
    assert result["items"][0]["external_id"] == "movie-1"
    assert "item_id" not in result["items"][0]
    serialized = json.dumps(result)
    assert result["items"][0]["_poster_url"] == ""
    assert "vod_play_url" not in serialized and "signature" not in serialized
    assert MP4 not in serialized and HLS not in serialized
    assert recorded[0][1] == {"ac": "list", "wd": "合成", "pg": "2"}


def test_valid_empty_search_is_distinct_from_provider_failure(monkeypatch):
    monkeypatch.setattr(maccms, "fetch", lambda *_: {"code": 1, "list": [], "page": 1, "pagecount": 1, "total": 0})
    assert maccms.search(SOURCE, "没有", 1)["items"] == []


def test_search_rejects_duplicate_ids_and_wrong_requested_page(monkeypatch):
    monkeypatch.setattr(maccms, "fetch", lambda *_: {"code": 1, "list": [movie(), movie()]})
    assert_category(lambda: maccms.search(SOURCE, "合成", 1))
    monkeypatch.setattr(maccms, "fetch", lambda *_: {"code": 1, "list": [movie()], "page": 2, "pagecount": 2})
    assert_category(lambda: maccms.search(SOURCE, "合成", 1))


def test_detail_requires_exactly_the_requested_record_and_configured_action(monkeypatch):
    recorded = []
    def fetch(_source, params):
        recorded.append(params)
        return {"code": 1, "list": [movie()]}
    monkeypatch.setattr(maccms, "fetch", fetch)
    source = {**SOURCE, "provider_options": {"detail_action": "videolist"}}
    assert maccms.detail(source, "movie-1").title == "合成电影"
    assert recorded == [{"ac": "videolist", "ids": "movie-1"}]
    assert_category(lambda: maccms.detail(source, "another"))


def test_api_fetch_uses_source_api_client_and_fixed_query_without_forwarding_credentials(monkeypatch):
    recorded = []
    class Client:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            recorded.append("closed")
        def request(self, method, url, **options):
            recorded.append((method, url, options))
            return SimpleNamespace(status_code=200, content=b'{"code":1,"list":[]}')
    def factory(source_id, purpose):
        assert source_id == SOURCE["id"] and purpose == "api"
        return Client()
    monkeypatch.setattr(maccms, "create_online_client", factory)
    assert maccms.fetch(SOURCE, {"ac": "list", "wd": "x"})["list"] == []
    assert recorded[0][2] == {"params": {"ac": "list", "wd": "x"}, "headers": {"Accept": "application/json"}}
    assert recorded[-1] == "closed"
