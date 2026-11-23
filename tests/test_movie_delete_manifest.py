"""Movie deletion confirmation is bound to the visible disk and catalogue manifest."""
import os

import pytest
from fastapi.testclient import TestClient

from app import library_paths, storage, store
from app.main import app
from app.storage import smb
from _smb_fake import FakeSmbClient

client = TestClient(app)
URL = "/api/movies/batch-delete"


@pytest.fixture(params=["local", "smb"])
def library(tmp_path, monkeypatch, request):
    monkeypatch.setattr(store._base, "DB_PATH", str(tmp_path / "movies.db"))
    store.init_db()
    root = tmp_path / "files"
    root.mkdir()
    if request.param == "smb":
        monkeypatch.setattr(smb, "smbclient", FakeSmbClient(root))
        monkeypatch.setenv("SMB_DRIVER", "direct")
        lib = store.create_library("test SMB", source="smb", smb={"host": "fake", "share": "movies"})
    else:
        lib = store.create_library("test local", path=str(root))
    library_paths.invalidate_cache()
    smb.invalidate()
    yield root, lib["id"]
    smb.invalidate()
    library_paths.invalidate_cache()


def put(root, rel, data=b"movie"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def movie(lid, rel, title, tmdb_id=None):
    mid = store.upsert_movie_by_path(rel, library_id=lid)
    store.update_movie_meta(mid, title=title, tmdb_id=tmdb_id)
    return mid


def preview(ids):
    response = client.post(URL, json={"ids": ids})
    assert response.status_code == 200, response.text
    return response.json()


def confirm(ids, key):
    return client.post(URL, json={"ids": ids, "dry_run": False, "confirm": True, "preview_id": key})


def test_root_shared_directory_and_nested_other_film(library):
    root, lid = library
    own = "Alien.mkv"
    others = ["Inception/Inception.mkv", "Alien Resurrection.mkv", "Alien Resurrection.zh.srt",
              "research.txt", "movie.nfo", "Alien handwritten.nfo", "Alien family.jpg",
              "Subs/Alien Resurrection.en.srt", "Alien Covenant.en.srt", "Alien Covenant-trailer.mkv"]
    expected = {own, "Alien.zh.srt", "Subs/Alien.en.srt"}
    for rel in [*expected, *others]:
        put(root, rel)
    mid = movie(lid, own, "Alien")
    other_id = movie(lid, others[0], "Inception")
    data = preview([mid])
    assert {f["rel"] for f in data["plans"][0]["files"]} == expected
    response = confirm([mid], data["preview_id"])
    assert response.status_code == 200 and response.json()["deleted_files"] == len(expected)
    assert all((root / rel).is_file() for rel in others)
    assert store.get_movie(other_id)
    # A sole root-level feature also cannot claim the remaining tree.
    second = movie(lid, others[1], "Alien Resurrection")
    assert others[0] not in [f["rel"] for f in preview([second])["plans"][0]["files"]]


def test_new_or_changed_file_invalidates_entire_batch(library):
    root, lid = library
    first = put(root, "Arrival/Arrival.mkv")
    a = movie(lid, "Arrival/Arrival.mkv", "Arrival")
    put(root, "Dune/Dune.mkv")
    b = movie(lid, "Dune/Dune.mkv", "Dune")
    data = preview([a, b])
    new = put(root, "Dune/Dune.en.srt")
    assert confirm([a, b], data["preview_id"]).status_code == 409
    data = preview([a, b])
    before = new.stat()
    replacement = put(root, "replacement.tmp", b"other")
    replacement.replace(new)
    os.utime(new, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert new.stat().st_size == before.st_size
    assert confirm([a, b], data["preview_id"]).status_code == 409
    assert confirm([a, b], "").status_code == 409
    assert first.is_file() and new.is_file() and store.get_movie(a) and store.get_movie(b)


def test_unrelated_file_added_after_preview_survives(library):
    root, lid = library
    rel = "Arrival/Arrival.mkv"
    put(root, rel)
    mid = movie(lid, rel, "Arrival")
    data = preview([mid])
    notes = put(root, "Arrival/import-notes.txt", b"second task")
    result = confirm([mid], data["preview_id"]).json()
    assert result["deleted_movies"] == result["deleted_files"] == 1
    assert notes.read_bytes() == b"second task"


def test_multi_directory_versions_and_extras_only_in_selected_library(library):
    root, lid = library
    paths = ["1080p/Title.mkv", "4k/Title.mkv"]
    extras = ["trailer/clip-a.mkv", "trailer/clip-b.mkv"]
    ids = []
    for rel, extra in zip(paths, extras):
        put(root, rel)
        put(root, extra)
        mid = movie(lid, rel, "Title", 145)
        store.upsert_extra(extra, mid, library_id=lid)
        ids.append(mid)
    other = movie(1, "Other/Title.mkv", "Title", 145)
    store.upsert_extra(extras[1], other, library_id=1)
    foreign_rel = "Other/Title.mkv"
    put(root, foreign_rel)
    movie_url = f"/api/movies/{ids[0]}"
    assert client.get(movie_url + "/blob", params={"name": foreign_rel}).status_code == 403
    assert client.request("DELETE", movie_url + "/files", json={"name": foreign_rel}).status_code == 403
    assert client.get(movie_url + "/blob", params={"name": paths[1]}).status_code == 200
    data = preview([ids[0]])
    assert len(data["plans"]) == 1 and len(data["plans"][0]["version_ids"]) == 2
    assert {f["rel"] for f in data["plans"][0]["files"]} == set(paths + extras)
    result = confirm([ids[0]], data["preview_id"]).json()
    assert result["deleted_movies"] == 1 and result["deleted_files"] == 4
    assert all(not (root / path).exists() for path in paths + extras)
    assert (root / foreign_rel).is_file()
    assert all(not store.get_movie(mid) for mid in ids)
    assert store.get_movie(other) and store.list_extras_by_movie(other)
    assert all(row["library_id"] != lid for row in store.list_all_extras())


def test_partial_failure_keeps_a_movie_for_retry(library, monkeypatch):
    root, lid = library
    versions = ["4k/Film.mkv", "HD/Film.mkv"]
    ids = [movie(lid, path, "Film", 605) for path in versions]
    sub = "HD/Film.zh.srt"
    for rel in [*versions, sub]:
        put(root, rel)
    initial = preview([ids[0]])
    cls = type(storage.backend_for(lid))
    original = cls.delete

    def deny_one(self, path, *args, **kwargs):
        if path == sub:
            raise storage.StorageDenied("denied")
        return original(self, path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(cls, "delete", deny_one)
        result = confirm([ids[0]], initial["preview_id"]).json()
    assert result["deleted_movies"] == result["deleted_files"] == 0
    plan = result["results"][0]
    assert plan["failed_files"] == [{"rel": sub, "error": "denied"}]
    assert {f["rel"]: f["status"] for f in plan["files"]} == {sub: "failed", **dict.fromkeys(versions, "retained")}
    assert plan["retry_ids"] == [ids[0]]
    assert all(store.get_movie(mid) for mid in ids)
    updated = preview(plan["retry_ids"])
    assert confirm(plan["retry_ids"], updated["preview_id"]).json()["deleted_movies"] == 1
    assert all(not store.get_movie(mid) for mid in ids)


def test_other_movies_extra_hidden_from_current_view_vetoes_sidecar_guess(library):
    from app import viewing
    from app.routers.movies.routes import movie_files
    from app.routers.movies.scope import _movie_delete_scope

    root, lid = library
    selected = movie(lid, "Film.mkv", "Film")
    private = movie(lid, "Private.mkv", "Private")
    for name in ["Film.mkv", "Private.mkv", "Film.trailer.mkv"]:
        put(root, name)
    # A subtitle may have been registered as an extra via upload.
    for name in ["Film.en.srt", "Film.zh.srt"]:
        put(root, name)
    store.upsert_extra("Film.en.srt", private, library_id=lid)
    with store._lock, store._conn() as conn:
        conn.execute("UPDATE movies SET is_private=1 WHERE id=?", (private,))
    ctx = viewing.current.set(viewing.ContentAccessContext(mode="normal", management=True))
    try:
        assert not store.get_movie(private)
        files = _movie_delete_scope(selected)["files"]
        assert {f["rel"] for f in files} == {"Film.mkv", "Film.zh.srt"}
        listing = movie_files(selected)
        assert {f["rel"] for f in listing["subtitles"]} == {"Film.zh.srt"}
    finally:
        viewing.current.reset(ctx)


def test_missing_version_is_reported_and_its_record_survives(library):
    root, lid = library
    live, missing = "HD/Film.mkv", "4k/Film.mkv"
    ids = [movie(lid, path, "Film", 606) for path in [live, missing]]
    put(root, live)
    initial = preview([ids[0]])
    result = confirm([ids[0]], initial["preview_id"]).json()
    assert result["deleted_movies"] == 0 and result["deleted_files"] == 1
    plan = result["results"][0]
    assert plan["status"] == "deleted_with_errors"
    assert plan["retained_versions"] == [{"rel": missing, "id": ids[1]}]
    assert plan["retry_ids"] == [ids[1]]
    assert not store.get_movie(ids[0]) and store.get_movie(ids[1])
    put(root, missing)
    updated = preview(plan["retry_ids"])
    assert confirm(plan["retry_ids"], updated["preview_id"]).json()["deleted_movies"] == 1


@pytest.mark.parametrize("operation", ["delete", "rename", "move"])
def test_subtitle_operation_preserves_unselected_handwritten_nfo(library, operation):
    root, lid = library
    put(root, "Notes/notes.en.srt")
    handwritten = put(root, "Notes/handwritten.nfo", b"manual metadata")
    path = "Notes/notes.en.srt"
    (root / "Subs").mkdir()
    body = {"delete": {"paths": [path]}, "rename": {"from": path, "name": "notes.zh.srt"},
            "move": {"from": path, "to_dir": "Subs"}}[operation]
    if operation == "delete":
        body["preview_id"] = client.post("/api/fs/delete", json={"library_id": lid, **body}).json()["preview_id"]
    r = client.post(f"/api/fs/{operation}", json={"library_id": lid, **body, "dry_run": False})
    assert r.status_code == 200 and r.json()["deleted" if operation == "delete" else "moved"] == 1
    assert handwritten.read_bytes() == b"manual metadata"


def test_moving_registered_feature_preserves_unrelated_old_and_destination_nfos(library):
    root, lid = library
    rel = "Film/Film.mkv"
    mid = movie(lid, rel, "Film")
    put(root, rel)
    put(root, "Film/Film.nfo")
    old = put(root, "Film/handwritten.nfo", b"manual metadata")
    new = put(root, "Destination/research.nfo", b"destination notes")
    result = client.post("/api/fs/move", json={"library_id": lid, "from": rel, "to_dir": "Destination", "dry_run": False})
    assert result.status_code == 200 and result.json()["moved"] == 1
    assert store.get_movie(mid)["file_path"] == "Destination/Film.mkv"
    assert old.read_bytes() == b"manual metadata" and new.read_bytes() == b"destination notes"
    assert not (root / "Film/Film.nfo").exists()

def test_single_movie_delete_never_falls_back_to_another_path(library):
    root, lid = library
    mid = movie(lid, "Film/Film.mkv", "Film")
    put(root, "Film/Film.mkv")
    subtitle = put(root, "Film/Film.srt")
    backup = put(root, "Film/extras/Film.srt", b"bonus track")
    url = f"/api/movies/{mid}/files"
    plan = client.request("DELETE", url, json={"name": "Film.srt"}).json()
    canonical = plan["plans"][0]["rel"]
    assert canonical == "Film/Film.srt"
    subtitle.rename(root / "Film" / "moved.srt")
    for name in (canonical, "Film.srt", "Film/extras/Film.srt"):
        result = client.request("DELETE", url, json={"name": name, "dry_run": False, "preview_id": plan["preview_id"]})
        assert result.status_code == 409
    assert backup.read_bytes() == b"bonus track"


def test_file_manager_rejects_changed_selection_before_deleting_any_file(library):
    root, lid = library
    first = put(root, "first.txt")
    old = put(root, "second.txt", b"first")
    body = {"library_id": lid, "paths": ["first.txt", "second.txt"]}
    url = "/api/fs/delete"
    plan = client.post(url, json=body).json()
    assert client.post(url, json={**body, "dry_run": False}).status_code == 409
    replacement = put(root, "third.txt", b"other")
    os.utime(replacement, ns=(old.stat().st_atime_ns, old.stat().st_mtime_ns))
    replacement.replace(old)
    r = client.post(url, json={**body, "dry_run": False, "preview_id": plan["preview_id"]})
    assert r.status_code == 409
    assert old.read_bytes() == b"other" and first.is_file()


def test_loose_root_subtitle_supports_read_preview_and_scoped_deletion(library):
    root, lid = library
    mid = movie(lid, "Film.mkv", "Film")
    put(root, "Film.mkv")
    put(root, "Film.zh.srt", b"1\n00:00:01,000 --> 00:00:03,000\nHello\n")
    foreign = put(root, "Other.srt")
    put(root, "Film Returns.mkv")
    put(root, "Film Returns.zh.srt")
    sequel_id = movie(lid, "Film Returns.mkv", "Film Returns")
    sequel = client.get(f"/api/movies/{sequel_id}/blob", params={"name": "Film Returns.zh.srt"})
    assert sequel.status_code == 200
    url = f"/api/movies/{mid}"
    files = client.get(url + "/files").json()
    assert [entry["rel"] for entry in files["subtitles"]] == ["Film.zh.srt"]
    for inline in (0, 1):
        r = client.get(url + "/blob", params={"name": "Film.zh.srt", "inline": inline})
        assert r.status_code == 200 and b"Hello" in r.content
    for other in ("Other.srt", "Film Returns.mkv", "Film Returns.zh.srt"):
        assert client.get(url + "/blob", params={"name": other}).status_code == 403
    plan = client.request("DELETE", url + "/files", json={"name": "Film.zh.srt"}).json()
    deleted = client.request("DELETE", url + "/files", json={"name": "Film.zh.srt", "dry_run": False,
                                                           "preview_id": plan["preview_id"]})
    assert deleted.json()["deleted"] == 1 and foreign.is_file()


@pytest.mark.parametrize("prefix", ["", "Inbox/"])
def test_shared_directory_sidecars_follow_longest_local_feature(library, prefix):
    root, lid = library
    short = prefix + "Film.mkv"
    long = prefix + "Film Returns.mkv"
    subtitle = prefix + "Film Returns.zh.srt"
    subdir_subtitle = prefix + "Subs/Film Returns.en.srt"
    own_subtitle = prefix + "Film.zh.srt"
    unrelated = prefix + "Director Cut.zh.srt"  # Same film's alternate version lives elsewhere.
    attached = prefix + "mixed interview.mp4"
    for rel in (short, long, subtitle, subdir_subtitle, own_subtitle, unrelated, attached, "Alt/Director Cut.mkv"):
        put(root, rel)
    mid = movie(lid, short, "Film", 710)
    movie(lid, "Alt/Director Cut.mkv", "Film", 710)
    sequel = movie(lid, long, "Film Returns")
    store.upsert_extra(attached, mid, library_id=lid)
    url = f"/api/movies/{mid}"
    files = client.get(url + "/files").json()
    assert {item["rel"] for item in files["feature"]} == {short}
    assert {item["rel"] for item in files["subtitles"]} == {own_subtitle}
    assert {item["rel"] for item in files["extras"]} == {attached}
    assert client.get(url + "/blob", params={"name": attached}).status_code == 200
    for rel in (long, subtitle, unrelated):
        assert client.get(url + "/blob", params={"name": rel}).status_code == 403
        assert client.request("DELETE", url + "/files", json={"name": rel}).status_code == 403
    plan = preview([sequel])
    assert {f["rel"] for f in plan["plans"][0]["files"]} == {long, subtitle, subdir_subtitle}
    assert confirm([sequel], plan["preview_id"]).json()["deleted_files"] == 3
    assert all((root / rel).exists() for rel in (short, own_subtitle, unrelated, attached))


def test_movie_file_delete_accepts_original_basename_only_for_previewed_revision(library):
    root, lid = library
    put(root, "Film/Film.mkv")
    mid = movie(lid, "Film/Film.mkv", "Film")
    old = put(root, "Film/Film.en.srt", b"first")
    name = "Film.en.srt"
    url = f"/api/movies/{mid}/files"
    plan = client.request("DELETE", url, json={"name": name}).json()
    previous = old.stat()
    replacement = put(root, "replacement.tmp", b"other")
    os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    replacement.replace(old)
    result = client.request("DELETE", url, json={"name": name, "dry_run": False, "preview_id": plan["preview_id"]})
    assert result.status_code == 409 and old.read_bytes() == b"other"
    fresh = client.request("DELETE", url, json={"name": name}).json()
    result = client.request("DELETE", url, json={"name": name, "dry_run": False, "preview_id": fresh["preview_id"]})
    assert result.json()["deleted"] == 1 and not old.exists()


def test_new_sibling_movie_invalidates_an_existing_single_file_preview(library):
    root, lid = library
    put(root, "Film/Film.mkv")
    mid = movie(lid, "Film/Film.mkv", "Film")
    sidecar = put(root, "Film/Film Returns.zh.srt")
    name = "Film Returns.zh.srt"
    url = f"/api/movies/{mid}/files"
    plan = client.request("DELETE", url, json={"name": name}).json()
    # The alternate feature appears externally between the preview and confirmation.
    put(root, "Film/Film Returns.mkv")
    r = client.request("DELETE", url, json={"name": name, "dry_run": False, "preview_id": plan["preview_id"]})
    assert r.status_code == 409 and sidecar.is_file()
    assert client.request("DELETE", url, json={"name": name}).status_code == 403
