"""Fresh source revision checks shared by probes, subtitles and derived artifacts."""
import hashlib
import os

from fastapi import HTTPException

from .. import storage, store


def source_revision(src, *, row=None, fresh=False):
    """Return the full stat identity; never hash media bytes or transient read URLs."""
    row = row or {}
    if isinstance(src, str):
        try:
            stat = os.stat(src)
        except OSError as exc:
            raise HTTPException(410, 'source file is missing or unavailable') from exc
        return store.media_source_revision(row.get('library_id'), row.get('file_path') or os.path.realpath(src),
                                           stat.st_size, stat.st_mtime)
    if src is None or not getattr(src, 'backend', None):
        raise HTTPException(409, 'source revision unavailable; reopen this media')
    backend = src.backend
    if fresh:
        storage.clear_meta_cache(getattr(backend, 'library_id', None))
        try:
            stat = backend.stat(src.rel)
        except storage.StorageNotFound as exc:
            raise HTTPException(410, 'source file is missing') from exc
        except storage.StorageOffline as exc:
            raise HTTPException(503, 'source storage is offline') from exc
        except storage.StorageError as exc:
            raise HTTPException(503, 'source storage is unavailable') from exc
        if stat.is_dir:
            raise HTTPException(410, 'source is not a media file')
        size, mtime = stat.size, stat.mtime
    else:
        size, mtime = src.size, src.mtime
    return store.media_source_revision(getattr(backend, 'library_id', row.get('library_id')),
                                       src.rel, size, mtime)


def assert_source_revision(src, revision, *, row=None):
    if source_revision(src, row=row, fresh=True) != revision:
        raise HTTPException(409, 'source changed during processing; reopen this media')


def source_cache_key(src, *, row=None):
    revision = source_revision(src, row=row)
    policy = getattr(getattr(src, 'backend', None), 'read_cache_revision', 0)
    if policy:
        revision += f'|read:{policy}'
    return hashlib.sha256(revision.encode()).hexdigest()[:32]
