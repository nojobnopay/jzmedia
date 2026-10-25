"""Preview inventories fetch only their candidate rows without loading whole shows."""
import pytest

from app import store
from app.store import items


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    import app.store._base as base
    monkeypatch.setattr(base, "DB_PATH", str(tmp_path / "preview-items.db"))
    store.init_db()
    libraries = []
    for number in (1, 2):
        root = tmp_path / f"media-{number}"
        root.mkdir()
        libraries.append(store.create_library(f"Preview {number}", path=str(root)))
    first, second = (library["id"] for library in libraries)
    movie = store.upsert_movie_by_path("movie.mkv", library_id=first)
    store.update_movie_meta(movie, title="电影标题")
    show = store.upsert_show(first, "剧集标题")
    episode = store.upsert_episode(show, first, "episode.mkv", 1, 3, title="第三集")
    untitled = store.upsert_episode(show, first, "untitled.mkv", 0, 1)
    extra = store.upsert_tv_extra("extras/interview.mkv", show, kind="interview", library_id=first)
    orphan = store.upsert_extra("orphan.mkv", None, kind="unknown", library_id=first)
    other = store.upsert_movie_by_path("other.mkv", library_id=second)
    return libraries, show, [("movie", movie), ("episode", episode), ("episode", untitled),
                             ("extra", extra), ("extra", orphan), ("movie", other)]


def test_preview_playables_preserve_display_names_and_filter_library(catalogue, monkeypatch):
    libraries, _show, refs = catalogue
    expected = {ref: store.get_playable(*ref) for ref in refs}
    connections = []
    original = items._conn

    def connection():
        connections.append(True)
        return original()

    def unexpected(*_args):
        pytest.fail("batch preview lookup must not load playable or whole show details")

    monkeypatch.setattr(items, "_conn", connection)
    monkeypatch.setattr(items, "get_playable", unexpected)
    monkeypatch.setattr(items, "get_show", unexpected)
    rows = store.get_preview_playables(refs + refs + [("movie", 999999)], libraries[0]["id"])
    assert len(connections) == 1
    assert len(rows) == len(refs) - 1
    for row in rows:
        actual = expected[(row["kind"], row["id"])]
        assert row["library_id"] == libraries[0]["id"]
        for field in ("file_path", "title", "kind", "display_title", "extra_kind", "show_title"):
            assert row.get(field) == actual.get(field)


def test_preview_playables_chunk_large_lists_and_ignore_invalid_ids(catalogue):
    libraries, show, refs = catalogue
    library_id = libraries[0]["id"]
    import app.store._base as base
    with base._conn() as connection:
        connection.executemany(
            "INSERT INTO tv_episodes(show_id, library_id, file_path, season, episode) VALUES(?,?,?,?,?)",
            [(show, library_id, f"batch-{number}.mkv", 2, number) for number in range(405)])
        added = [("episode", row["id"]) for row in connection.execute(
            "SELECT id FROM tv_episodes WHERE season=2")]
    result = store.get_preview_playables(added + [("movie", 2 ** 80), ("episode", -1),
                                                 ("extra", "invalid"), ("movie", None)])
    assert {(row["kind"], row["id"]) for row in result} == set(added)
    assert len(store.get_preview_playables(refs)) == len(refs)
    assert store.get_preview_playables([]) == []
