"""Production catalogue/provider flow with isolated SQLite and synthetic JSON."""

from contextlib import ExitStack
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app import library_paths, store
from app.config import settings
from app.main import app
from app.online import catalog, maccms, open_catalog, providers
from app.online.models import OnlinePlaybackIdentity
from app.store import online


MP4 = "https://cdn.example/movie.mp4?sig=server-only-old"
HLS = "https://cdn.example/movie.m3u8?sig=server-only-old"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "catalog.db"))
    monkeypatch.setattr(store._base, "ensure_dirs", lambda: None)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    library_paths.invalidate_cache()
    store.init_db()
    source = online.create_online_source({"name": "合成免费来源", "provider": "maccms",
        "api_base_url": "https://api.example/provide/vod/", "allowed_hosts": ["cdn.example"], "enabled": True})
    online.update_online_settings(True)
    movie = {"vod_id": "movie-1", "vod_name": "合成电影", "type_name": "电影", "vod_year": "2026",
             "vod_content": "<p>合成描述</p>", "vod_play_from": "mp4$$$hls", "vod_play_url": f"正片${MP4}$$$正片${HLS}"}
    show = {**movie, "vod_id": "show-1", "vod_name": "合成剧集", "type_name": "国产剧",
            "vod_play_from": "main$$$backup", "vod_play_url": f"第1集${MP4}#第2集${HLS}$$$第1集${HLS}#第2集${MP4}"}
    state = SimpleNamespace(source=source, movie=movie, show=show, requests=[], version=0)

    def fetch(selected_source, params):
        assert selected_source["id"] == source["id"]
        state.requests.append(params)
        if "ids" in params:
            rows = [row for row in (state.movie, state.show) if row["vod_id"] == params["ids"]]
            return {"code": 1, "list": rows}
        return {"code": 1, "list": [state.movie, state.show], "page": int(params.get("pg", 1)),
                "pagecount": 1, "total": 2}

    monkeypatch.setattr(maccms, "fetch", fetch)
    def no_network(*_args, **_kwargs):
        pytest.fail("catalogue unit tests may not construct an external reader")
    monkeypatch.setattr(maccms, "create_online_client", no_network)
    candidate = TestClient(app)
    state.client = candidate
    try:
        assert candidate.get("/api/viewing/session").status_code == 200
        yield state
    finally:
        candidate.close()
        library_paths.invalidate_cache()


def get_detail(environment, selected_id="movie-1"):
    return environment.client.get("/api/online/catalog/detail", params={"source_id": environment.source["id"], "external_id": selected_id})


def test_public_source_list_omits_configuration_and_is_opt_in(environment):
    response = environment.client.get("/api/online/catalog/sources")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    item = response.json()["items"][0]
    assert item["id"] == environment.source["id"]
    assert not {"api_base_url", "allowed_hosts", "provider_options"} & item.keys()
    online.update_online_settings(False)
    assert environment.client.get("/api/online/catalog/sources").json() == {"enabled": False, "items": []}
    assert environment.requests == []


def test_search_returns_plain_metadata_and_detail_assigns_independent_playable_lines(environment):
    search = environment.client.get("/api/online/catalog/search", params={"source_id": environment.source["id"], "q": "合成"})
    assert search.status_code == 200, search.text
    assert [item["external_id"] for item in search.json()["items"]] == ["movie-1", "show-1"]
    with store._base._conn() as connection:
        assert connection.execute("SELECT count(*) FROM online_items").fetchone()[0] == 0
    response = get_detail(environment)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["description"] == "合成描述"
    assert len(detail["lines"]) == 2
    assert [line["entries"][0]["transport"] for line in detail["lines"]] == ["mp4", "hls"]
    first, second = (line["entries"][0]["identity"] for line in detail["lines"])
    assert first["item_id"] == second["item_id"]
    assert first["line_id"] != second["line_id"]
    assert "cdn.example" not in response.text and "server-only" not in response.text
    assert "api.example" not in response.text


def test_episode_and_line_identity_survive_refresh_sort_and_signature_changes(environment):
    before = get_detail(environment, "show-1").json()
    environment.show.update(vod_play_from="backup$$$main", vod_play_url=
        f"第2集${MP4}&new=1#第1集${HLS}&new=1$$$第2集${HLS}&new=1#第1集${MP4}&new=1")
    after = get_detail(environment, "show-1").json()
    def identities(detail):
        return {(line["label"], entry["label"]): entry["identity"] for line in detail["lines"] for entry in line["entries"]}
    assert before["item"]["id"] == after["item"]["id"]
    assert identities(before) == identities(after)
    identity = identities(after)[("main", "第1集")]
    resolved = providers.resolve(OnlinePlaybackIdentity.model_validate(identity))
    assert resolved.url == MP4 + "&new=1" and resolved.transport == "mp4"
    assert resolved.source_fingerprint
    # Resolving refreshes only the chosen source/detail, not media or the other
    # line; its URL is absent from persistent identity tables.
    with store._base._conn() as connection:
        for table in ("online_items", "online_episodes"):
            assert "server-only" not in json.dumps([tuple(row) for row in connection.execute(f"SELECT * FROM {table}")])
        for table in ("movies", "tv_episodes", "playback_progress"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_fresh_detail_never_silently_substitutes_removed_line_or_episode(environment):
    movie = get_detail(environment).json()
    identity = movie["lines"][1]["entries"][0]["identity"]
    environment.movie.update(vod_play_from="mp4", vod_play_url="正片$" + MP4)
    with pytest.raises(providers.ProviderError) as caught:
        providers.resolve(OnlinePlaybackIdentity.model_validate(identity))
    assert caught.value.category == "provider_line_not_found"
    show = get_detail(environment, "show-1").json()
    identity = show["lines"][0]["entries"][0]["identity"]
    environment.show.update(vod_play_from="main", vod_play_url="第2集$" + HLS)
    with pytest.raises(providers.ProviderError) as caught:
        providers.resolve(OnlinePlaybackIdentity.model_validate(identity))
    assert caught.value.category == "provider_episode_not_found"


def test_multiple_movie_lines_require_explicit_stable_selection(environment):
    selected = get_detail(environment).json()["lines"][0]["entries"][0]["identity"]
    del selected["line_id"]
    with pytest.raises(providers.ProviderError) as caught:
        providers.resolve(OnlinePlaybackIdentity.model_validate(selected))
    assert caught.value.category == "provider_line_required"


def test_unsupported_web_player_remains_metadata_with_disabled_entry(environment):
    environment.movie.update(vod_play_from="web", vod_play_url="正片$https://cdn.example/watch.html")
    response = get_detail(environment)
    assert response.status_code == 200
    entry = response.json()["lines"][0]["entries"][0]
    assert entry == {"label": "正片", "identity": None, "transport": None, "supported": False, "reason": "unsupported_media"}


@pytest.mark.parametrize("path", ["sources", "search?source_id=src_" + "1" * 32 + "&q=test", "detail?source_id=src_" + "1" * 32 + "&external_id=1"])
def test_all_catalogue_reads_require_device_session_even_without_protection(environment, path):
    stranger = TestClient(app)
    try:
        response = stranger.get("/api/online/catalog/" + path)
        assert response.status_code == 401
        assert response.json()["code"] == "viewing_session_required"
        assert environment.requests == []
    finally:
        stranger.close()


def test_children_cannot_search_or_read_even_if_management_is_unlocked(environment):
    client = environment.client
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    assert client.post("/api/viewing/mode", json={"mode": "children"}).status_code == 200
    assert client.get("/api/online/catalog/sources").status_code == 403
    assert get_detail(environment).status_code == 403
    assert environment.requests == []


def test_browsing_is_not_administration_but_configuration_remains_protected(environment):
    client = environment.client
    assert client.post("/api/viewing/password", json={"new_password": "123456"}).status_code == 200
    assert client.get("/api/online/catalog/sources").status_code == 200
    assert get_detail(environment).status_code == 200
    assert client.get("/api/online/sources").status_code == 403


@pytest.mark.parametrize("query", [{"q": ""}, {"q": "a", "page": 0}, {"q": "a", "page": 1001},
    {"q": "a", "url": "https://private-token.invalid/"}, {"q": "\nprivate-token"}])
def test_invalid_search_is_fixed_422_without_network_or_input_reflection(environment, query):
    response = environment.client.get("/api/online/catalog/search", params={"source_id": environment.source["id"], **query})
    assert response.status_code == 422
    assert "private-token" not in response.text
    assert environment.requests == []


@pytest.mark.parametrize("gate", ["feature", "source", "changed"])
def test_source_or_feature_change_during_fetch_does_not_publish_results(environment, monkeypatch, gate):
    original = maccms.fetch
    def changed(source, params):
        result = original(source, params)
        if gate == "feature":
            online.update_online_settings(False)
        elif gate == "source":
            online.update_online_source(source["id"], {"enabled": False})
        else:
            online.update_online_source(source["id"], {"api_base_url": "https://api.example/replaced/"})
        return result
    monkeypatch.setattr(maccms, "fetch", changed)
    response = get_detail(environment)
    assert response.status_code == (409 if gate == "changed" else 503)
    with store._base._conn() as connection:
        assert connection.execute("SELECT count(*) FROM online_items").fetchone()[0] == 0


def test_atomic_identity_save_rejects_duplicate_episode_input_before_writes(environment):
    rows = [{"external_id": "same", "title": "one"}, {"external_id": "same", "title": "two"}]
    with pytest.raises(ValueError):
        online.save_online_catalog(environment.source["id"], "new", "tv", "new", rows)
    with store._base._conn() as connection:
        assert connection.execute("SELECT count(*) FROM online_items").fetchone()[0] == 0


def test_admission_is_global_and_per_owner_with_no_waiting_queue():
    with ExitStack() as stack:
        for index in range(4):
            stack.enter_context(catalog._admit(f"owner-{index}"))
        with pytest.raises(providers.ProviderError) as caught:
            with catalog._admit("fifth"):
                pytest.fail("over-budget caller must not run")
        assert caught.value.category == "provider_catalog_busy"
    with catalog._admit("same"):
        with pytest.raises(providers.ProviderError):
            with catalog._admit("same"):
                pytest.fail("same-owner caller must not run concurrently")
    with catalog._admit("same"):
        pass


def test_open_catalog_is_constant_opt_in_and_has_attribution_without_metadata_network(environment):
    source = online.create_online_source({"name": "开放样例", "provider": "open_catalog",
        "api_base_url": open_catalog.API_BASE_URL, "allowed_hosts": [open_catalog.MEDIA_HOST], "enabled": True})
    response = environment.client.get("/api/online/catalog/search", params={"source_id": source["id"]})
    assert response.status_code == 200 and response.json()["items"][0]["external_id"] == open_catalog.EXTERNAL_ID
    detail = environment.client.get("/api/online/catalog/detail", params={"source_id": source["id"], "external_id": open_catalog.EXTERNAL_ID})
    assert detail.status_code == 200, detail.text
    assert len(detail.json()["attribution"]) == 4
    assert "原项目" in detail.json()["description"]
    assert "web_videos" not in detail.text
    identity = detail.json()["lines"][0]["entries"][0]["identity"]
    resolved = providers.resolve(OnlinePlaybackIdentity.model_validate(identity))
    assert resolved.url == open_catalog.MEDIA_URL and resolved.duration_seconds == 596
    assert environment.requests == []


def test_open_catalog_never_fetches_noncanonical_admin_endpoint(environment):
    source = online.create_online_source({"name": "旧元数据配置", "provider": "open_catalog",
        "api_base_url": "https://untrusted.example/", "enabled": True})
    response = environment.client.get("/api/online/catalog/search", params={"source_id": source["id"]})
    assert response.status_code == 503 and response.json()["code"] == "provider_not_ready"
    assert environment.requests == []


def test_public_fingerprint_change_detection_never_contains_config_in_error(environment):
    selected = get_detail(environment).json()["lines"][0]["entries"][0]["identity"]
    resolved = providers.resolve(OnlinePlaybackIdentity.model_validate(selected))
    catalog.assert_source_version(environment.source["id"], resolved.source_fingerprint)
    online.update_online_source(environment.source["id"], {"provider_options": {"detail_action": "videolist"}})
    with pytest.raises(catalog.CatalogError) as caught:
        catalog.assert_source_version(environment.source["id"], resolved.source_fingerprint)
    assert str(caught.value) == "online_source_changed"
