"""Optimistic catalogue guards shared by preview admission and reconciliation."""
from fastapi import HTTPException

from ... import store
from ...library_mutex import library_mutation_lock
from .preview_source import InventoryChanged


_SOURCE_FIELDS = ("id", "path", "source", "media_library_id", "storage_identity", "effective_enabled",
                  "smb_host", "smb_connect_host", "smb_share", "smb_subpath", "smb_domain",
                  "smb_username", "smb_password", "smb_options", "nfs_export", "nfs_options",
                  "nfs_password", "media_mount_point")


class PreviewCatalogue:
    def __init__(self, library_id, *, refs=None):
        self.library_id = library_id
        # Only database reads share mutation locking; network/cache I/O stays outside.
        with library_mutation_lock(library_id), store._lock:
            self.library = store.get_library(library_id)
            if not self.library:
                raise HTTPException(404, "视频库不存在")
            self.identity = self._identity(self.library)
            self.rows = (store.get_library_preview_playables(library_id) if refs is None
                         else store.get_preview_playables(refs, library_id=library_id))
            self.snapshot = store.preview_catalogue_snapshot(library_id)

    @staticmethod
    def _identity(library):
        return tuple(library.get(name) for name in _SOURCE_FIELDS)

    def assert_current(self):
        """Call in the short library mutation / preview queue commit section."""
        with store._lock:
            current = store.get_library(self.library_id)
            if (not current or self._identity(current) != self.identity
                    or store.preview_catalogue_snapshot(self.library_id) != self.snapshot):
                raise InventoryChanged()


def assert_item_current(row, library, *, identity=None):
    """Verify one worker result using the same identity rules as bulk admission."""
    if row is not None:
        kind, item_id, library_id = row.get("kind", "movie"), row["id"], row.get("library_id") or 1
    else:
        kind, item_id, library_id = identity
    current_library = store.get_library(library_id)
    current = store.get_preview_playables([(kind, item_id)], library_id=library_id)
    current_row = current[0] if current else None

    def library_identity(value):
        return PreviewCatalogue._identity(value) if value is not None else None

    def row_identity(value):
        return (value.get("library_id"), value.get("file_path")) if value is not None else None

    if (library_identity(current_library) != library_identity(library)
            or row_identity(current_row) != row_identity(row)):
        raise InventoryChanged()
