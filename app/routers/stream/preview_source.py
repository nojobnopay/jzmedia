"""Metadata-only preview snapshots with the playback chain's source identity."""
from fastapi import HTTPException

from ... import library_paths, storage
from ...playback.source import source_revision


class InventoryChanged(RuntimeError):
    """The inventory can no longer safely commit the result it collected."""

    def __init__(self):
        super().__init__("盘点期间文件或目录发生变化，未提交本次结果；请完成扫描后重新提交")


def raw_preview_source(row, backend=None, *, fresh=False, bypass_cache=False):
    """Read file attributes without resolving a media URL or starting a proxy.

    Bulk callers clear metadata once per library and pass their shared backend.
    A single explicit refresh may instead request ``fresh=True``.
    """
    library_id = row.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
    if backend is None:
        backend = storage.backend_for(library_id)
    elif backend.library_id != int(library_id):
        raise storage.StorageInvalidPath("preview backend belongs to another library")
    if fresh:
        storage.clear_meta_cache(library_id)
    rel = backend.norm(row["file_path"])
    # Local stat deliberately permits outside symlink metadata; previews
    # must retain playback's strict path boundary even without a read URL.
    backend.abs_path(rel)
    stat = getattr(backend, 'stat_uncached', backend.stat) if bypass_cache else backend.stat
    info = stat(rel)
    if info.is_dir:
        raise storage.StorageNotFound("source is not a media file")
    return storage.MediaSource(backend=backend, rel=rel, input="", local_path=None,
                               size=info.size, mtime=info.mtime)


def preview_source(row, backend=None, *, fresh=False):
    """Expose metadata snapshots to HTTP callers without losing storage semantics."""
    try:
        return raw_preview_source(row, backend, fresh=fresh)
    except storage.StorageNotFound:
        raise HTTPException(410, "source file is missing") from None
    except storage.StorageOffline:
        raise HTTPException(503, "source storage is offline") from None
    except storage.StorageError:
        raise HTTPException(500, "source storage is unavailable") from None


def source_snapshot(src, row):
    """Use the canonical path/size/full-precision-mtime identity, never media bytes."""
    return source_revision(src, row=row)


def verify_sources(sources, stopped=None):
    """Freshly verify ``(row, source, expected_revision)`` triples outside locks.

    The expected revision must be captured when collecting the inventory, so
    mutable source objects cannot silently alter the expected identity later.
    Storage failures retain their HTTP status; callers decide how to report an
    interrupted inventory. Return False when cancellation prevents validation.
    """
    cleared = set()
    for row, src, expected in sources:
        if stopped and stopped():
            return False
        library_id = row.get("library_id") or library_paths.DEFAULT_LIBRARY_ID
        if library_id not in cleared:
            storage.clear_meta_cache(library_id)
            cleared.add(library_id)
        current = preview_source(row, backend=src.backend)
        if stopped and stopped():
            return False
        if source_snapshot(current, row) != expected:
            raise InventoryChanged()
    return not (stopped and stopped())
