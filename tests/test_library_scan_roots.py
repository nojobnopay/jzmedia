from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi.testclient import TestClient

from app import library_paths, library_roots, scanner, store
from app.main import app
from app.library_mutex import library_mutation_lock
from app.routers import files, jobs


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "disk"
    root.mkdir()
    lib = store.create_library(name=f"volume-{tmp_path.name}", path=str(root))
    library_paths.invalidate_cache()
    (root / "original.mkv").write_bytes(b"video")
    movie = store.upsert_movie_by_path("original.mkv", library_id=lib["id"])
    store.update_movie_meta(movie, tmdb_id=904321)
    store.save_progress(movie, 700, 1800)
    yield lib["id"], root, movie
    store.delete_media_library(lib["media_library_id"])
    library_paths.invalidate_cache()


def test_cross_scan_root_review_requires_fresh_identity_and_explicit_acceptance(library, tmp_path):
    lid, root, movie = library
    client = TestClient(app)
    accept = f"/api/libraries/{lid}/accept-scan-root"
    assert all(row["status"] != "library_offline" for row in scanner.scan_all(library_id=lid))
    root.rename(tmp_path / "original-disk")
    root.mkdir()
    store.init_db()
    scan = scanner.scan_all(library_id=lid)
    assert [row["status"] for row in scan] == ["library_offline"]
    review = jobs._scan_summary(scan)["root_reviews"][str(lid)]
    preview = files.missing(library=str(lid))
    assert preview["items"] == [] and preview["root_reviews"][str(lid)] == review
    assert files.clean({"library_id": lid, "dry_run": False})["deleted"] == 0
    assert store.get_progress(movie)["position"] == 700
    root.rename(tmp_path / "other-disk")
    assert client.post(accept, json={"token": review["token"]}).status_code == 503
    root.mkdir()
    assert client.post(accept, json={"token": review["token"]}).status_code == 409
    newer = files.missing(library=str(lid))["root_reviews"][str(lid)]
    assert newer["token"] != review["token"]
    assert client.post(accept, json={"token": newer["token"]}).json() == {"library_id": lid, "accepted": True}
    assert client.post(accept, json={"token": newer["token"]}).status_code == 409
    assert store.get_movie(movie), "acceptance only records the selected root"
    assert files.clean({"ids": [movie], "dry_run": False})["deleted"] == 1
    assert not store.get_movie(movie)


def test_original_root_resumes_without_confirming_a_replacement(library, tmp_path):
    lid, root, movie = library
    detached = tmp_path / "original"
    root.rename(detached)
    missing = files.missing(library=str(lid))
    assert missing["total"] == 0 and str(lid) in missing["unavailable_libraries"]
    assert files.clean({"ids": [movie], "dry_run": False})["deleted"] == 0
    root.mkdir()
    old_review = scanner.scan_all(library_id=lid)[0]["root_review"]
    root.rmdir()
    detached.rename(root)
    assert files.missing(library=str(lid))["root_reviews"] == {}
    assert not any(row.get("root_review") for row in scanner.scan_all(library_id=lid))
    assert store.get_progress(movie)["position"] == 700
    assert TestClient(app).post(f"/api/libraries/{lid}/accept-scan-root",
                                json={"token": old_review["token"]}).status_code == 409


@pytest.mark.parametrize("known_file_present", [False, True])
def test_legacy_catalogue_establishes_baseline_only_with_existing_files(library, known_file_present):
    lid, root, movie = library
    with store._conn() as c:
        c.execute("DELETE FROM library_scan_roots WHERE library_id=?", (lid,))
    if not known_file_present:
        (root / "original.mkv").unlink()
    review = files.clean({"ids": [movie], "dry_run": False})["root_reviews"]
    assert bool(review) is not known_file_present
    assert store.get_movie(movie)
    with store._conn() as c:
        row = c.execute("SELECT * FROM library_scan_roots WHERE library_id=?", (lid,)).fetchone()
        assert bool(row["accepted"]) is known_file_present


def test_manual_clean_rechecks_root_and_file_before_deleting(library, tmp_path, monkeypatch):
    lid, root, movie = library
    original = files.routes._exists_map
    (root / "original.mkv").unlink()

    def swap(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        root.rename(tmp_path / "old")
        root.mkdir()
        return snapshot

    monkeypatch.setattr(files.routes, "_exists_map", swap)
    out = files.clean({"ids": [movie], "dry_run": False})
    assert out["deleted"] == 0 and str(lid) in out["root_reviews"]
    assert store.get_progress(movie)["position"] == 700
    root.rmdir()
    (tmp_path / "old").rename(root)

    def reappear(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        (root / "original.mkv").write_bytes(b"restored")
        return snapshot

    monkeypatch.setattr(files.routes, "_exists_map", reappear)
    assert files.clean({"ids": [movie], "dry_run": False})["deleted"] == 0
    assert store.get_movie(movie)


def test_manual_clean_observes_a_concurrent_catalogue_move_under_the_library_lock(library):
    lid, root, movie = library
    started = threading.Event()

    def clean_after_move():
        started.set()
        return files.clean({"library_id": lid, "ids": [movie], "dry_run": False})

    with ThreadPoolExecutor(max_workers=1) as pool:
        with library_mutation_lock(lid):
            (root / "original.mkv").rename(root / "moved.mkv")
            future = pool.submit(clean_after_move)
            assert started.wait(3)
            store.update_movie_local(movie, file_path="moved.mkv")
        assert future.result(timeout=5)["deleted"] == 0
    assert store.get_movie(movie)["file_path"] == "moved.mkv"


def test_manual_clean_empty_selection_never_expands_to_all_records(library):
    lid, root, mid = library
    (root / "original.mkv").unlink()
    assert files.clean({"library_id": lid, "ids": [], "dry_run": False})["deleted"] == 0
    assert files.clean({"library_id": "invalid-id", "dry_run": False})["deleted"] == 0
    assert store.get_progress(mid)["position"] == 700


@pytest.mark.parametrize("prior_stamp", ["legacy", "birth", "change"])
def test_reused_root_inode_cannot_purge_a_catalogue(library, monkeypatch, prior_stamp):
    lid, root, movie = library
    info = root.stat()
    volume_inode = f"{info.st_dev}:{info.st_ino}"
    # Filesystems can immediately reuse the freed inode for a new empty directory.
    # Give both incarnations the same dev/ino on every test filesystem.
    accepted = volume_inode if prior_stamp == "legacy" else f"{volume_inode}:{prior_stamp}:1"
    with store._conn() as c:
        c.execute("UPDATE library_scan_roots SET accepted=? WHERE library_id=?", (accepted, lid))
    (root / "original.mkv").unlink()
    monkeypatch.setattr(library_roots, "file_birthtime_ns", lambda path, st: None if prior_stamp == "change" else 2)
    scan = scanner.scan_all(library_id=lid)
    assert len(scan) == 1 and scan[0]["root_review"]["token"]
    assert files.clean({"ids": [movie], "dry_run": False})["deleted"] == 0
    assert store.get_movie(movie) and store.get_progress(movie)["position"] == 700


def test_legacy_inode_stamp_upgrades_when_a_catalogued_file_is_present(library):
    lid, root, movie = library
    info = root.stat()
    weak = f"{info.st_dev}:{info.st_ino}"
    with store._conn() as c:
        c.execute("UPDATE library_scan_roots SET accepted=? WHERE library_id=?", (weak, lid))
    assert not files.missing(library=str(lid))["root_reviews"]
    with store._conn() as c:
        assert c.execute("SELECT accepted FROM library_scan_roots WHERE library_id=?", (lid,)).fetchone()[0] != weak
    assert store.get_progress(movie)["position"] == 700


@pytest.mark.parametrize("catalogue", ["new", "existing", "accepted_empty"])
def test_active_upload_on_weak_timestamp_filesystem_can_publish_and_retry(tmp_path, monkeypatch, catalogue):
    monkeypatch.setattr(library_roots, "file_birthtime_ns", lambda path, st: None)
    root = tmp_path / "weak-root"
    root.mkdir()
    lib = store.create_library("weak-" + tmp_path.name, path=str(root))
    lid = lib["id"]
    try:
        if catalogue != "new":
            (root / "old.mkv").write_bytes(b"old")
            store.upsert_movie_by_path("old.mkv", library_id=lid)
        client = TestClient(app)
        if catalogue == "accepted_empty":
            (root / "old.mkv").unlink()
            review = files.missing(library=str(lid))["root_reviews"][str(lid)]
            assert client.post(f"/api/libraries/{lid}/accept-scan-root", json={"token": review["token"]}).status_code == 200
        for name in ("one.txt", "two.txt"):
            response = client.post("/api/uploads", params={"library_id": lid}, files={"file": (name, b"new")})
            assert response.status_code == 200, response.text
            assert (root / name).read_bytes() == b"new"
    finally:
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()
