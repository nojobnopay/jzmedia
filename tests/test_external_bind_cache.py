"""Explicit candidate confirmation restores real provider detail, never the old title."""

from fastapi.testclient import TestClient
import pytest

from app import library_paths, scanner, store
from app.main import app
from app.metadata import chain, external
from app.scanner import tv_persist

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "bind.db"))
    store.init_db()
    library_paths.invalidate_cache()
    monkeypatch.setattr(scanner, "write_target", lambda row: {})
    monkeypatch.setattr(external, "_download_to_rel", lambda *args, **kwargs: "")
    monkeypatch.setattr(external, "_write_movie_nfo", lambda *args, **kwargs: False)
    monkeypatch.setattr(tv_persist, "write_media_files", lambda *args, **kwargs: {"nfo": False})
    yield
    library_paths.invalidate_cache()


def movie():
    mid = store.upsert_movie_by_path("旧名称 (1999).mkv", library_id=1)
    store.update_movie_meta(mid, title="旧名称", title_auto=1, needs_review=1)
    return mid


def show():
    sid = store.upsert_show(1, "旧剧名称", 1999)
    store.update_show_meta(sid, needs_review=1)
    return sid


def cache(source="nfo", source_id="trusted-record", kind="movie"):
    detail = {"title": "真实候选标题", "original_title": "Trusted Title", "year": 2002,
              "overview": "真实资料的完整简介", "genres": ["剧情"], "countries": ["CN"],
              "people": {"cast": [{"name": "真实演员"}]},
              "episodes": [{"season": 1, "episode": 1, "title": "真实分集名"}]}
    store.upsert_external(source, source_id, kind, title=detail["title"], year=detail["year"],
                          payload=detail)
    return detail


@pytest.mark.parametrize("source", ["nfo", "douban", "imdb"])
def test_cached_movie_confirmation_restores_payload_and_keeps_path(source):
    mid = movie()
    detail = cache(source)
    result = client.post(f"/api/movies/{mid}/bind-external", json={"source": source, "source_id": "trusted-record"})
    assert result.status_code == 200, result.text
    row = store.get_movie(mid)
    assert row["title"] == detail["title"] and row["overview"] == detail["overview"]
    assert row["year"] == 2002 and row["match_source"] == source and row["needs_review"] == 0
    assert row["file_path"] == "旧名称 (1999).mkv" and "真实演员" in row["person_names"]
    # Explicit external binding still follows the store's FTS synchronization rule.
    assert any(item["id"] == mid for item in store.search_fts("真实演员", grouped=False))


def test_cached_tv_confirmation_fills_real_episode_detail():
    sid = show()
    ep = store.upsert_episode(sid, 1, "旧剧名称/Season 01/原文件.mkv", 1, 1)
    detail = cache(kind="tv")
    result = client.post(f"/api/tv/shows/{sid}/bind-external", json={"source": "nfo", "source_id": "trusted-record"})
    assert result.status_code == 200, result.text
    row = store.get_show_meta(sid)
    assert row["title"] == detail["title"] and row["overview"] == detail["overview"]
    assert row["match_source"] == "nfo" and row["needs_review"] == 0
    episode = store.get_episode(ep)
    assert episode["title"] == "真实分集名" and episode["season"] == 1 and episode["episode"] == 1
    assert episode["file_path"] == "旧剧名称/Season 01/原文件.mkv"


@pytest.mark.parametrize("kind", ["movie", "tv"])
@pytest.mark.parametrize("source", ["nfo", "douban", "unknown"])
def test_index_only_or_unknown_source_is_not_a_successful_match(kind, source):
    item_id = movie() if kind == "movie" else show()
    get = store.get_movie if kind == "movie" else store.get_show_meta
    before = get(item_id)
    store.upsert_match_entry(source, "index-only", kind, "提示候选", "", 2002, None, "", {})
    path = f"/api/movies/{item_id}" if kind == "movie" else f"/api/tv/shows/{item_id}"
    result = client.post(path + "/bind-external", json={"source": source, "source_id": "index-only"})
    assert result.status_code == 422
    assert get(item_id) == before and chain.candidate_for(source, "index-only", kind) is None


@pytest.mark.parametrize("kind,other", [("movie", "tv"), ("tv", "movie")])
def test_wrong_kind_cache_is_rejected_even_for_a_live_detail_provider(monkeypatch, kind, other):
    item_id = movie() if kind == "movie" else show()
    get = store.get_movie if kind == "movie" else store.get_show_meta
    before = get(item_id)
    cache(source="wikidata", kind=other)
    monkeypatch.setitem(chain._DETAILERS, "wikidata", lambda sid: pytest.fail("wrong kind must not fetch"))
    path = f"/api/movies/{item_id}" if kind == "movie" else f"/api/tv/shows/{item_id}"
    assert client.post(path + "/bind-external", json={"source": "wikidata", "source_id": "trusted-record"}).status_code == 422
    assert get(item_id) == before


@pytest.mark.parametrize("kind", ["movie", "tv"])
def test_detail_failure_does_not_reuse_old_title_or_clear_review(monkeypatch, kind):
    item_id = movie() if kind == "movie" else show()
    get = store.get_movie if kind == "movie" else store.get_show_meta
    before = get(item_id)
    def unavailable(sid):
        raise RuntimeError("offline")
    monkeypatch.setitem(chain._DETAILERS, "wikidata", unavailable)
    assert chain.candidate_for("wikidata", "Q999", kind).title == ""
    path = f"/api/movies/{item_id}" if kind == "movie" else f"/api/tv/shows/{item_id}"
    result = client.post(path + "/bind-external", json={"source": "wikidata", "source_id": "Q999"})
    assert result.status_code == 502 and get(item_id) == before
    assert store.get_external("wikidata", "Q999") is None


def test_cached_detail_is_a_valid_offline_fallback(monkeypatch):
    mid = movie()
    cache(source="wikidata")
    monkeypatch.setitem(chain._DETAILERS, "wikidata", lambda sid: {})
    result = client.post(f"/api/movies/{mid}/bind-external", json={"source": "wikidata", "source_id": "trusted-record"})
    assert result.status_code == 200
    assert store.get_movie(mid)["overview"] == "真实资料的完整简介"


def test_tvmaze_is_not_a_movie_detail_provider():
    assert chain.candidate_for("tvmaze", "42", "movie") is None
    assert chain.candidate_for("tvmaze", "42", "tv") is not None
