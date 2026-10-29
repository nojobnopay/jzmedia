"""The formal P2 path parses isolated MacCMS HTTP JSON, never provider stubs."""

from contextlib import closing
import base64
from pathlib import Path
from threading import Event
import re

from fastapi.testclient import TestClient
import pytest

from app import store
from app.online import health
from scripts.online_p2.fixtures import MARKER, MARKER_TEXT, fixture_root
from scripts.online_p2.server import FIXTURE_TOKEN, isolated_fixture


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / "jzmedia-online-p2-test"
    root.mkdir()
    (root / MARKER).write_text(MARKER_TEXT)
    for episode in ("ep-1", "ep-2"):
        folder = root / "media" / episode
        variants = folder / "hls" / "variants"
        variants.mkdir(parents=True)
        (folder / "movie.mp4").write_bytes(b"0123456789" * 100)
        (folder / "hls" / "master.m3u8").write_text(
            '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nvariants/low.m3u8\n')
        (folder / "hls" / "init.mp4").write_bytes(b"init-bytes")
        (variants / "part-000.m4s").write_bytes(b"segment-bytes")
        (variants / "low.m3u8").write_text(
            '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MAP:URI="../init.mp4"\n'
            '#EXTINF:4,\npart-000.m4s\n#EXT-X-ENDLIST\n')
    (root / "media" / "poster.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQ0AAAAASUVORK5CYII="))
    return root


@pytest.fixture
def client(prepared):
    with isolated_fixture(prepared) as app, TestClient(app) as browser:
        browser.headers["X-Api-Token"] = FIXTURE_TOKEN
        assert browser.get("/api/viewing/session").status_code == 200
        yield browser


def add_source(client, *, name="隔离MacCMS"):
    bootstrap = client.get("/api/p2/bootstrap").json()
    response = client.post("/api/online/sources", json={"provider": "maccms", "name": name,
        "api_base_url": bootstrap["api_base_url"], "allowed_hosts": bootstrap["allowed_hosts"], "enabled": True})
    assert response.status_code == 201, response.text
    assert client.patch("/api/online/settings", json={"enabled": True}).status_code == 200
    return response.json()


def detail(client, source, external_id="movie-1"):
    response = client.get("/api/online/catalog/detail", params={"source_id": source["id"], "external_id": external_id})
    assert response.status_code == 200, response.text
    return response.json()


def entry_for(value, transport):
    return next(entry for line in value["lines"] for entry in line["entries"] if entry["transport"] == transport)


def test_health_observes_actual_catalogue_and_media_reads_without_probing(client):
    source = add_source(client)
    path = "/api/online/catalog/health"
    params = {"source_id": source["id"]}
    initial = client.get(path, params=params)
    assert initial.status_code == 200
    assert initial.json()["purposes"]["api"]["state"] == "unknown"
    value = detail(client, source)
    response = client.get(path, params=params)
    assert response.json()["purposes"]["api"]["state"] == "success"
    assert response.json()["purposes"]["media"]["state"] == "unknown"
    descriptor = client.post("/api/online/sessions", json=entry_for(value, "mp4")["identity"]).json()
    assert client.get(descriptor["media_url"], headers={"Range": "bytes=0-31"}).content == (b"0123456789" * 100)[:32]
    before = len(client.get("/api/p2/metrics").json()["origin_calls"])
    observed = client.get(path, params=params)
    assert observed.status_code == 200
    assert observed.json()["cache_scope"] == "process"
    media = observed.json()["purposes"]["media"]
    assert media["state"] == "success" and media["mode"] == "direct" and media["stage"] == "bytes"
    assert "fixture.invalid" not in observed.text and "proxy_url" not in observed.text
    assert len(client.get("/api/p2/metrics").json()["origin_calls"]) == before
    assert client.delete(f"/api/online/sessions/{descriptor['session_id']}").status_code == 204


def test_health_requires_a_viewing_device_and_rejects_extra_targets(client):
    source = add_source(client)
    stranger = TestClient(client.app)
    try:
        response = stranger.get("/api/online/catalog/health", params={"source_id": source["id"]})
        assert response.status_code == 401
    finally:
        stranger.close()
    response = client.get("/api/online/catalog/health", params={"source_id": source["id"], "url": "https://private.invalid/secret"})
    assert response.status_code == 422 and "private.invalid" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/online/catalog/health", params={"source_id": "src_" + "f" * 32}).status_code == 404


def test_health_bookkeeping_failure_does_not_change_catalogue_or_streamed_bytes(client, monkeypatch):
    observed_stages = []
    media_observed = Event()

    def broken_finish(observation, error=None):
        observed_stages.append(observation.stage)
        if observation.stage == "bytes":
            media_observed.set()
        raise RuntimeError("health bookkeeping failed")

    monkeypatch.setattr(health.registry, "finish", broken_finish)
    source = add_source(client)
    value = detail(client, source)
    descriptor_response = client.post("/api/online/sessions", json=entry_for(value, "mp4")["identity"])
    assert descriptor_response.status_code == 201, descriptor_response.text
    descriptor = descriptor_response.json()
    response = client.get(descriptor["media_url"], headers={"Range": "bytes=0-31"})
    assert response.status_code == 206
    assert response.headers["Content-Range"] == "bytes 0-31/1000"
    assert response.content == (b"0123456789" * 100)[:32]
    assert media_observed.wait(2), "The byte producer finishes its observation after delivering the body"
    assert "catalogue" in observed_stages and "bytes" in observed_stages
    assert client.delete(f"/api/online/sessions/{descriptor['session_id']}").status_code == 204
    assert client.get("/api/p2/metrics").json()["registry"]["sessions"] == 0


def test_fixture_guard_refuses_unmarked_and_production_paths(tmp_path):
    for path in (Path("/data"), tmp_path, Path("/tmp/arbitrary-directory")):
        with pytest.raises(ValueError):
            fixture_root(path)
    root = tmp_path / "jzmedia-online-p2-empty"
    root.mkdir()
    with pytest.raises(ValueError, match="generate"):
        fixture_root(root, require_media=True)


def test_initial_feature_and_local_media_are_empty(client):
    response = client.get("/api/online/catalog/sources")
    assert response.status_code == 200
    assert response.json()["enabled"] is False and response.json()["items"] == []
    assert len(response.json()["scope_token"]) == 64
    metrics = client.get("/api/p2/metrics").json()
    assert metrics["catalog_rows"] == {"online_sources": 0, "online_items": 0, "online_episodes": 0}
    assert metrics["local_rows"] == {"movies": 0, "tv_episodes": 0, "playback_progress": 0}


def test_taxonomy_fixture_exposes_real_parent_ids_and_origin_side_year_pagination(client):
    source = add_source(client)
    public = client.get("/api/online/catalog/sources").json()
    assert public["items"][0]["capabilities"]["year_filter"] is False
    assert client.post("/api/p2/scenario", json={"taxonomy": True}).status_code == 200
    public = client.get("/api/online/catalog/sources").json()
    assert public["items"][0]["capabilities"]["year_filter"] is True
    query = {"source_id": source["id"], "category_id": "71", "year": "2026"}
    response = client.get("/api/online/catalog/browse", params=query)
    assert response.status_code == 200, response.text
    first = response.json()
    assert len(first["categories"]) == 49
    categories = {item["id"]: item for item in first["categories"]}
    assert categories["71"]["parent_id"] == "58"
    assert categories["58"]["parent_id"] == ""
    assert first["total"] == 24 and first["page_count"] == 2 and len(first["items"]) == 20
    assert {item["year"] for item in first["items"]} == {"2026"}
    assert all(item["category_id"] == "71" and item["remarks"] == "更新至第2集" for item in first["items"])
    assert first["items"][0]["actors"] == ["合成演员甲", "合成演员乙"]
    second = client.get("/api/online/catalog/browse", params={**query, "page": 2}).json()
    assert second["page"] == 2 and len(second["items"]) == 4
    assert first["items"][0]["external_id"] not in {item["external_id"] for item in second["items"]}
    parent = client.get("/api/online/catalog/browse", params={**query, "category_id": "58"}).json()
    assert [item["external_id"] for item in parent["items"]] == ["short-overview"]
    custom = client.get("/api/online/catalog/browse", params={**query, "year": "1988"}).json()
    assert [item["external_id"] for item in custom["items"]] == ["urban-1988-1"]
    assert client.get("/api/online/catalog/browse", params={**query, "year": "1900"}).json()["items"] == []
    observed = client.get("/api/p2/metrics").json()["origin_calls"]
    assert any(call["category_id"] == "71" and call["year"] == "2026" and call["page"] == "2" for call in observed)


def test_related_fixture_sorts_explicit_seasons_without_persisting_candidates(client):
    source = add_source(client)
    public = client.get("/api/online/catalog/sources").json()
    assert public["items"][0]["capabilities"]["related"] is False
    assert client.post("/api/p2/scenario", json={"related": True, "clear_related_cache": True}).status_code == 200
    assert client.get("/api/online/catalog/sources").json()["items"][0]["capabilities"]["related"] is True
    detail(client, source, "academy-s2")
    before = client.get("/api/p2/metrics").json()
    params = {"source_id": source["id"], "external_id": "academy-s2"}
    response = client.get("/api/online/catalog/related", params=params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "ready" and result["current_season"] == 2
    assert [(item["external_id"], item["season"]) for item in result["series"]] == [
        ("academy-s1", 1), ("academy-s3", 3), ("academy-s4", 4)]
    assert all(item["reason"] == "inferred_season" for item in result["series"])
    cards = result["series"] + result["related"] + result["name_related"]
    assert len(cards) <= 12 and len({item["external_id"] for item in cards}) == len(cards)
    assert "academy-s2" not in {item["external_id"] for item in cards}
    assert all(item["poster_path"].startswith("/api/online/catalog/artwork/") for item in cards)
    assert not any(value in response.text for value in ("fixture.invalid", "fixture_signature", "vod_play_url"))
    after = client.get("/api/p2/metrics").json()
    upstream = after["origin_calls"][len(before["origin_calls"]):]
    calls = [call for call in upstream if call["kind"] == "api"]
    assert 1 <= len(calls) <= 3, "Recommendations have one bounded detail/search/category budget"
    assert sum(bool(call["external_id"]) for call in calls) <= 1, "No per-card detail requests"
    assert after["catalog_rows"] == before["catalog_rows"], "Candidates do not become watched items or episodes"
    assert after["local_rows"] == {"movies": 0, "tv_episodes": 0, "playback_progress": 0}
    assert client.get("/api/online/catalog/related", params=params).json() == result
    assert len(client.get("/api/p2/metrics").json()["origin_calls"]) == len(after["origin_calls"])


@pytest.mark.parametrize("external_id,current_season", [
    ("restart-s2", 2), ("academy-unseasoned", None), ("echo-s2", 2), ("lonely-trailer", None),
])
def test_related_fixture_does_not_invent_a_series_or_first_season(client, external_id, current_season):
    source = add_source(client)
    assert client.post("/api/p2/scenario", json={"related": True, "clear_related_cache": True}).status_code == 200
    response = client.get("/api/online/catalog/related", params={"source_id": source["id"], "external_id": external_id})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "ready" and result["current_season"] == current_season
    assert result["series"] == []
    assert external_id not in {item["external_id"] for key in ("related", "name_related") for item in result[key]}
    if external_id == "lonely-trailer":
        assert result["related"] == [] and result["name_related"] == []
    assert client.get("/api/p2/metrics").json()["catalog_rows"]["online_items"] == 0


def test_detail_fixture_keeps_long_source_order_and_nonstandard_labels_with_bounded_media(client):
    source = add_source(client)
    assert client.post("/api/p2/scenario", json={"detail_mode": True}).status_code == 200
    capability = client.get("/api/online/catalog/sources").json()["items"][0]["capabilities"]
    assert capability["year_filter"] is True and capability["related"] is False
    catalogue = client.get("/api/online/catalog/browse", params={
        "source_id": source["id"], "category_id": "31", "year": "2025", "page": 2}).json()
    assert catalogue["total"] == 27 and catalogue["page_count"] == 2
    assert "long-show" in {item["external_id"] for item in catalogue["items"]}
    value = detail(client, source, "long-show")
    assert len(value["description"]) > 1000
    assert len(value["actors"]) == 24
    assert len(value["lines"]) == 2
    for line in value["lines"]:
        entries = line["entries"]
        assert len(entries) == 123
        assert entries[0]["label"] == "第1集" and entries[104]["label"] == "第105集"
        assert entries[100]["label"] == "番外·特别篇" and entries[122]["label"] == "终章（下）"
        assert entries[51]["supported"] is False and entries[51]["identity"] is None
    identity = value["lines"][0]["entries"][104]["identity"]
    assert identity["line_id"] != value["lines"][1]["entries"][104]["identity"]["line_id"]
    created = client.post("/api/online/sessions", json=identity)
    assert created.status_code == 201, created.text
    session = created.json()
    response = client.get(session["media_url"], headers={"Range": "bytes=0-31"})
    assert response.status_code == 206 and response.content == (b"0123456789" * 100)[:32]
    assert client.delete(f"/api/online/sessions/{session['session_id']}").status_code == 204
    assert client.post("/api/p2/scenario", json={"revision": 2, "reverse_lines": True}).status_code == 200
    refreshed = detail(client, source, "long-show")
    preserved = next(line for line in refreshed["lines"] if line["id"] == identity["line_id"])
    assert preserved["entries"][104]["identity"] == identity
    assert client.get("/api/p2/metrics").json()["local_rows"] == {
        "movies": 0, "tv_episodes": 0, "playback_progress": 0}


def test_detail_fixture_exposes_short_and_unavailable_items_without_changing_default_scenarios(client):
    source = add_source(client)
    assert client.post("/api/p2/scenario", json={"detail_mode": True}).status_code == 200
    short = detail(client, source, "short-show")
    assert [entry["label"] for entry in short["lines"][0]["entries"]] == ["序章", "正篇", "后记"]
    unavailable = detail(client, source, "unavailable-show")
    assert all(not entry["supported"] and entry["identity"] is None
               for line in unavailable["lines"] for entry in line["entries"])
    assert client.post("/api/p2/scenario", json={"detail_mode": False}).status_code == 200
    assert client.get("/api/online/catalog/sources").json()["items"][0]["capabilities"]["year_filter"] is False
    assert len(detail(client, source, "show-1")["lines"][0]["entries"]) == 2


def test_real_maccms_search_detail_and_owned_mp4_passthrough(client):
    source = add_source(client)
    public = client.get("/api/online/catalog/sources").json()
    assert public["items"][0]["id"] == source["id"]
    assert "api_base_url" not in str(public) and "fixture.invalid" not in str(public)
    searched = client.get("/api/online/catalog/search", params={"source_id": source["id"], "q": "合成", "page": 1})
    assert searched.status_code == 200
    assert len(searched.json()["items"]) == 3
    value = detail(client, source)
    assert len(value["lines"]) == 2
    assert "fixture.invalid" not in str(value) and "fixture_signature" not in str(value)
    identity = entry_for(value, "mp4")["identity"]
    created = client.post("/api/online/sessions", json=identity)
    assert created.status_code == 201, created.text
    descriptor = created.json()
    assert descriptor["processing"] == "passthrough" and descriptor["time_offset_seconds"] == 0
    response = client.get(descriptor["media_url"], headers={"Range": "bytes=0-9"})
    assert response.status_code == 206 and response.content == b"0123456789"
    assert client.delete(f"/api/online/sessions/{descriptor['session_id']}").status_code == 204
    metrics = client.get("/api/p2/metrics").json()
    assert metrics["registry"]["sessions"] == 0
    assert {call["purpose"] for call in metrics["client_calls"]} == {"api", "media"}
    assert {call["mode"] for call in metrics["client_calls"]} == {"direct"}
    assert not any(call["app_credentials"] for call in metrics["origin_calls"])
    assert any(call["action"] == "list" for call in metrics["origin_calls"])
    assert any(call["action"] == "detail" for call in metrics["origin_calls"])


def test_real_catalogue_categories_and_png_poster_use_the_owned_loopback_image_reader(client, prepared):
    source = add_source(client)
    response = client.get("/api/online/catalog/browse", params={"source_id": source["id"], "category_id": "2"})
    assert response.status_code == 200
    result = response.json()
    assert result["categories"] == [{"id": "1", "name": "电影", "parent_id": ""},
                                    {"id": "2", "name": "剧集", "parent_id": ""}]
    assert [item["external_id"] for item in result["items"]] == ["show-1"]
    poster = result["items"][0]["poster_path"]
    assert poster.startswith("/api/online/catalog/artwork/")
    assert "fixture.invalid" not in response.text
    image = client.get(poster)
    assert image.status_code == 200
    assert image.content == (prepared / "media" / "poster.png").read_bytes()
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "no-store"
    metrics = client.get("/api/p2/metrics").json()
    assert {call["purpose"] for call in metrics["client_calls"]} == {"api", "image"}
    assert not any(call["app_credentials"] for call in metrics["origin_calls"])


def test_real_maccms_hls_rewrites_nested_assets(client):
    source = add_source(client)
    identity = entry_for(detail(client, source), "hls")["identity"]
    created = client.post("/api/online/sessions", json=identity)
    assert created.status_code == 201, created.text
    master = client.get(created.json()["media_url"])
    variant = next(line for line in master.text.splitlines() if line and not line.startswith("#"))
    media = client.get(variant)
    initial = re.search(r'#EXT-X-MAP:URI="([^"]+)"', media.text)[1]
    assert client.get(initial).content == b"init-bytes"
    segment = next(line for line in media.text.splitlines() if line and not line.startswith("#"))
    assert client.get(segment).content == b"segment-bytes"
    assert "fixture.invalid" not in media.text


def test_line_reordering_and_signed_url_refresh_keep_episode_identities(client):
    source = add_source(client)
    first = detail(client, source, "show-1")
    before = {(line["label"], entry["label"]): entry["identity"] for line in first["lines"] for entry in line["entries"]}
    assert len(before) == 4
    assert client.post("/api/p2/scenario", json={"revision": 2, "reverse_lines": True}).status_code == 200
    second = detail(client, source, "show-1")
    after = {(line["label"], entry["label"]): entry["identity"] for line in second["lines"] for entry in line["entries"]}
    assert before == after
    assert first["item"]["id"] == second["item"]["id"]
    assert len({identity["episode_id"] for identity in after.values()}) == 4
    created = client.post("/api/online/sessions", json=entry_for(second, "mp4")["identity"])
    assert created.status_code == 201, created.text
    assert client.get(created.json()["media_url"], headers={"Range": "bytes=0-9"}).status_code == 206


def test_unsupported_web_lines_are_not_playable_identities(client):
    source = add_source(client)
    value = detail(client, source, "unsupported-1")
    entries = [entry for line in value["lines"] for entry in line["entries"]]
    assert entries and all(not entry["supported"] and entry["identity"] is None for entry in entries)
    assert "fixture.invalid" not in str(value)


@pytest.mark.parametrize("mode", ["malformed", "xml", "status_error"])
def test_controlled_upstream_errors_are_sanitized_and_retryable(client, mode):
    source = add_source(client)
    assert client.post("/api/p2/scenario", json={"next_mode": mode}).status_code == 200
    response = client.get("/api/online/catalog/search", params={"source_id": source["id"], "q": "合成", "page": 1})
    assert response.status_code in {502, 503}, response.text
    assert "fixture.invalid" not in response.text and "upstream failure" not in response.text
    assert client.get("/api/online/catalog/search", params={"source_id": source["id"], "q": "none", "page": 1}).json()["items"] == []


def test_disable_source_revokes_owned_playback_and_no_arbitrary_url_api(client):
    source = add_source(client)
    identity = entry_for(detail(client, source), "mp4")["identity"]
    created = client.post("/api/online/sessions", json=identity).json()
    assert client.patch(f"/api/online/sources/{source['id']}", json={"enabled": False}).status_code == 200
    assert client.get(created["media_url"], headers={"Range": "bytes=0-9"}).status_code == 404
    rejected = client.post("/api/online/sessions", json={**identity, "url": "http://127.0.0.1/private.mp4"})
    assert rejected.status_code == 422 and "127.0.0.1" not in rejected.text
    assert client.get("/api/p2/metrics").json()["registry"]["sessions"] == 0


def test_fixture_restores_database_global_and_only_removes_its_state(prepared):
    original = store._base.DB_PATH
    with isolated_fixture(prepared) as app, TestClient(app) as client:
        assert Path(store._base.DB_PATH).is_relative_to(prepared)
        assert client.get("/api/p2/health").json()["synthetic"] is True
        with closing(store._base._conn()) as connection:
            assert connection.execute("SELECT COUNT(*) FROM online_sources").fetchone()[0] == 0
    assert store._base.DB_PATH == original
    assert not list(prepared.glob("state-*"))
    assert (prepared / "media" / "ep-1" / "movie.mp4").exists()
