"""Online contracts retain stable identity and exclude server media processing."""

import pytest
from pydantic import ValidationError

from app.online.models import (OnlineEpisode, OnlineItem, OnlinePlaybackDescriptor,
                               OnlinePlaybackIdentity, OnlineSettings, OnlineSettingsPatch,
                               OnlineSource, OnlineSourceCreate, OnlineSourcePatch,
                               normalize_external_id)


SOURCE_ID = "src_" + "1" * 32
ITEM_ID = "itm_" + "2" * 32
EPISODE_ID = "ep_" + "3" * 32
SESSION_ID = "4" * 32


def source_values(**changes):
    return {"name": "样例源", "provider": "maccms",
            "api_base_url": "https://api.example.invalid/provide/vod/", **changes}


def movie_identity(**changes):
    return {"source_id": SOURCE_ID, "item_id": ITEM_ID, "kind": "movie", **changes}


def descriptor_values(**changes):
    return {"identity": movie_identity(), "session_id": SESSION_ID,
            "transport": "hls", "seekable": True, "expires_at": 1700000000, **changes}


def test_online_settings_are_default_off_and_strict():
    assert OnlineSettings().enabled is False
    assert OnlineSettingsPatch(enabled=True).enabled is True
    for data in ({"enabled": 1}, {"enabled": "true"}, {"enabled": None}, {"unknown": True}):
        with pytest.raises(ValidationError):
            OnlineSettings.model_validate(data)
    with pytest.raises(ValidationError):
        OnlineSettingsPatch.model_validate({})


def test_source_configuration_normalizes_hosts_and_preserves_endpoint_path():
    source = OnlineSourceCreate.model_validate(source_values(
        name="  样例源  ", api_base_url="https://API.EXAMPLE.INVALID:443/provide/vod/",
        allowed_hosts=["CDN.EXAMPLE.INVALID.", "cdn.example.invalid"],
    ))
    assert source.name == "样例源"
    assert source.api_base_url == "https://api.example.invalid:443/provide/vod/"
    assert source.allowed_hosts == ["cdn.example.invalid", "api.example.invalid"]
    assert source.enabled is False and source.priority == 100


def test_source_default_hosts_include_api_and_idn_hosts_normalize():
    source = OnlineSourceCreate.model_validate(source_values(api_base_url="https://例子.example/api"))
    assert source.api_base_url == "https://xn--fsqu00a.example/api"
    assert source.allowed_hosts == ["xn--fsqu00a.example"]


@pytest.mark.parametrize("url", [
    "file:///tmp/catalog.json", "ftp://api.example.invalid/", "//api.example.invalid/",
    "https://user:secret@api.example.invalid/", "https://@api.example.invalid/",
    "https://api.example.invalid/?token=secret", "https://api.example.invalid/?",
    "https://api.example.invalid/#fragment", "https://api.example.invalid/#",
    "https://api.example.invalid:0/", "https://api.example.invalid:65536/",
    "https://api.example.invalid/with space", "https://api.example.invalid/\\path",
    "https://api.example.invalid/%0d%0apath", "https://api.example.invalid/%2e%2e/catalog",
    "https://localhost/", "https://api.localhost/", "http://127.0.0.1/",
    "http://192.168.1.1/", "http://169.254.169.254/", "http://[::1]/",
    "http://127.1/", "http://2130706433/", "http://0x7f000001/",
    "https://api.example.invalid/\n",
])
def test_invalid_api_addresses_are_not_configuration(url):
    with pytest.raises(ValidationError):
        OnlineSourceCreate.model_validate(source_values(api_base_url=url))


@pytest.mark.parametrize("host", [
    "*.example.invalid", "https://cdn.example.invalid", "cdn.example.invalid:443",
    "cdn.example.invalid/path", "user@cdn.example.invalid", "127.0.0.1", "::1",
    "localhost", "api.local", "bad_host.example.invalid", "", "a" * 64 + ".invalid",
    "cdn.example.invalid..",
])
def test_allowed_hosts_are_exact_valid_hosts(host):
    with pytest.raises(ValidationError):
        OnlineSourceCreate.model_validate(source_values(allowed_hosts=[host]))


@pytest.mark.parametrize("changes", [
    {"enabled": 1}, {"enabled": "true"}, {"priority": True}, {"priority": -1},
    {"priority": 1001}, {"name": " "}, {"name": "line\nbreak"},
    {"allowed_hosts": "cdn.example.invalid"}, {"allowed_hosts": [1]},
    {"provider": "spider"}, {"headers": {"Authorization": "secret"}},
])
def test_source_fields_reject_coercions_or_unsupported_capabilities(changes):
    with pytest.raises(ValidationError):
        OnlineSourceCreate.model_validate(source_values(**changes))


def test_source_record_keeps_opaque_identity_when_endpoint_changes():
    original = OnlineSource.model_validate({"id": SOURCE_ID, "created_at": 1, "updated_at": 1,
                                           **source_values()})
    changed = OnlineSource.model_validate({**original.model_dump(), "name": "新名称",
                                          "api_base_url": "https://mirror.example.invalid/api",
                                          "updated_at": 2})
    assert original.id == changed.id
    assert changed.allowed_hosts == ["api.example.invalid", "mirror.example.invalid"]
    with pytest.raises(ValidationError):
        OnlineSource.model_validate({**original.model_dump(), "id": "https://provider.invalid/id"})


def test_source_patch_only_contains_explicit_changes():
    assert OnlineSourcePatch.model_validate({}).model_dump(exclude_unset=True) == {}
    patch = OnlineSourcePatch.model_validate({"enabled": False, "allowed_hosts": []})
    assert patch.model_dump(exclude_unset=True) == {"allowed_hosts": [], "enabled": False}
    for data in ({"provider": "open_catalog"}, {"enabled": None}, {"name": None},
                 {"allowed_hosts": None}, {"priority": True}):
        with pytest.raises(ValidationError):
            OnlineSourcePatch.model_validate(data)


@pytest.mark.parametrize(("value", "expected"), [(123, "123"), (0, "0"), (" 123 ", "123"),
                                                ("S01E01", "S01E01"), ("001", "001")])
def test_provider_external_ids_have_explicit_normalization(value, expected):
    assert normalize_external_id(value) == expected


@pytest.mark.parametrize("value", [True, False, 1.0, None, "", "  ", "line\nbreak", "a" * 513])
def test_invalid_external_ids_are_not_stable_identity(value):
    with pytest.raises(ValueError):
        normalize_external_id(value)


def test_item_and_episode_identity_are_independent_of_title_or_sort_order():
    item = OnlineItem(id=ITEM_ID, source_id=SOURCE_ID, external_id=123, kind="tv",
                      title="旧标题", created_at=1, updated_at=1)
    episode = OnlineEpisode(id=EPISODE_ID, item_id=item.id, external_id="ep-stable-key",
                            title="第一集", season=1, episode=1, sort_order=5,
                            created_at=1, updated_at=1)
    replacement = OnlineEpisode.model_validate({**episode.model_dump(), "title": "修正标题",
                                               "season": 2, "episode": 3, "sort_order": 1,
                                               "updated_at": 2})
    assert item.external_id == "123"
    assert episode.id == replacement.id and episode.external_id == replacement.external_id
    assert replacement.sort_order == 1


@pytest.mark.parametrize("changes", [{"season": -1}, {"episode": True}, {"sort_order": -1},
                                     {"id": ITEM_ID}, {"item_id": SOURCE_ID}, {"created_at": False}])
def test_episode_identity_and_numbering_are_strict(changes):
    with pytest.raises(ValidationError):
        OnlineEpisode.model_validate({"id": EPISODE_ID, "item_id": ITEM_ID, "external_id": "ep",
                                      "created_at": 1, "updated_at": 1, **changes})


def test_playback_identity_keeps_movie_and_episode_context_explicit():
    assert OnlinePlaybackIdentity.model_validate(movie_identity()).episode_id is None
    identity = OnlinePlaybackIdentity.model_validate(movie_identity(kind="episode", episode_id=EPISODE_ID))
    assert identity.episode_id == EPISODE_ID
    for data in (movie_identity(episode_id=EPISODE_ID), movie_identity(kind="episode"),
                 movie_identity(source_id=ITEM_ID), movie_identity(item_id=EPISODE_ID)):
        with pytest.raises(ValidationError):
            OnlinePlaybackIdentity.model_validate(data)


def test_playback_descriptor_only_exposes_same_origin_passthrough_contract():
    descriptor = OnlinePlaybackDescriptor.model_validate(descriptor_values(duration_seconds=90))
    payload = descriptor.model_dump(mode="json")
    assert payload["media_url"] == f"/api/online/sessions/{SESSION_ID}/media"
    assert payload["processing"] == "passthrough"
    assert payload["duration_seconds"] == 90.0 and payload["time_offset_seconds"] == 0.0
    assert not {"upstream_url", "headers", "file_path"} & payload.keys()


@pytest.mark.parametrize("changes", [
    {"processing": "transcode"}, {"processing": "remux"}, {"processing": "burnin"},
    {"transport": "flv"}, {"seekable": "true"}, {"seekable": 1},
    {"session_id": "https://upstream.invalid/movie.m3u8"}, {"session_id": "short"},
    {"session_id": SESSION_ID + "\n"},
    {"duration_seconds": 0}, {"duration_seconds": -1}, {"duration_seconds": float("inf")},
    {"duration_seconds": float("nan")}, {"duration_seconds": True},
    {"time_offset_seconds": 10}, {"time_offset_seconds": False}, {"expires_at": 0},
    {"media_url": "https://upstream.invalid/movie.m3u8"},
    {"upstream_url": "https://upstream.invalid/movie.m3u8"}, {"headers": {"Cookie": "secret"}},
    {"file_path": "/media/movie.mkv"},
])
def test_playback_contract_rejects_server_processing_or_leaked_upstream_data(changes):
    with pytest.raises(ValidationError):
        OnlinePlaybackDescriptor.model_validate(descriptor_values(**changes))
