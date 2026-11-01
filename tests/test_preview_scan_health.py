"""A cleanup must not infer complete scans from per-file scan state."""
import pytest

from app import library_paths, scanner, store


def test_scan_health_completion_cancel_and_configuration(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    lib = store.create_library(name=f"health-{tmp_path.name}", kind="movie", path=str(root))
    lid = lib["id"]
    library_paths.invalidate_cache()
    try:
        assert store.preview_scan_health(lid) == "unknown"
        scanner.scan_all(library_id=lid)
        assert store.preview_scan_health(lid) == "healthy"
        scanner.scan_all(library_id=lid, should_stop=lambda: True)
        assert store.preview_scan_health(lid) == "unknown"
        scanner.scan_all(library_id=lid)
        assert store.preview_scan_health(lid) == "healthy"
        store.record_fs_change(lid, "move", "example.mkv")
        assert store.preview_scan_health(lid) == "unknown"
        store.clear_fs_changes(lid, store.fs_change_summary(lid)["revision"])
        other = tmp_path / "different"
        other.mkdir()
        store.update_media_library(lib["media_library_id"], path=str(other))
        assert store.preview_scan_health(lid) == "unknown"
        store.update_media_library(lib["media_library_id"], path=str(root))
        assert store.preview_scan_health(lid) == "unknown"
    finally:
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()


def test_interrupted_or_failed_scan_stays_unknown(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    lib = store.create_library(name=f"failure-{tmp_path.name}", kind="movie", path=str(root))
    lid = lib["id"]
    try:
        scanner.scan_all(library_id=lid)
        assert store.preview_scan_health(lid) == "healthy"
        monkeypatch.setattr(scanner.scan, "_scan_all", lambda *a, **k: [
            {"library_id": lid, "status": "library_offline"}])
        scanner.scan_all(library_id=lid)
        assert store.preview_scan_health(lid) == "unknown"

        def interrupted(*args, **kwargs):
            raise RuntimeError("interrupted")

        monkeypatch.setattr(scanner.scan, "_scan_all", interrupted)
        with pytest.raises(RuntimeError):
            scanner.scan_all(library_id=lid)
        assert store.preview_scan_health(lid) == "unknown"
    finally:
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()


def test_overlapping_scans_do_not_leave_health_evidence(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    lib = store.create_library(name=f"overlap-{tmp_path.name}", kind="movie", path=str(root))
    lid = lib["id"]
    try:
        first = store.begin_preview_scan(lid)
        second = store.begin_preview_scan(lid)
        store.finish_preview_scan(lid, second, True)
        assert store.preview_scan_health(lid) == "unknown"
        store.finish_preview_scan(lid, first, True)
        assert store.preview_scan_health(lid) == "unknown"
        fresh = store.begin_preview_scan(lid)
        store.finish_preview_scan(lid, fresh, True)
        assert store.preview_scan_health(lid) == "healthy"
    finally:
        store.delete_media_library(lib["media_library_id"])
        library_paths.invalidate_cache()
