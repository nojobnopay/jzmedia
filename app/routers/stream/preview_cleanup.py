"""Conservative, read-only inventory and bounded cleanup of preview directories."""
from contextlib import ExitStack
import json
import os
import shutil
import stat
import time

from fastapi import HTTPException

from ... import data_usage, storage, store
from ...library_mutex import library_mutation_lock
from .preview_source import raw_preview_source, source_snapshot

# Cache URLs are immutable and clients may keep a manifest while playing.
PROTECTION_SECONDS = 24 * 60 * 60


def _flags(directory=False):
    if not hasattr(os, 'O_NOATIME') or not hasattr(os, 'O_NOFOLLOW'):
        raise OSError('safe non-touching reads unavailable')
    return os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else 0)


def _signature(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_atime_ns)


def _inspect(root_fd, name):
    """Inspect only flat, regular files without following links or updating atime."""
    fd = os.open(name, _flags(True), dir_fd=root_fd)
    try:
        entries = {}
        with os.scandir(fd) as iterator:
            for entry in iterator:
                st = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(st.st_mode):
                    raise OSError('non-regular cache entry')
                entries[entry.name] = _signature(st)
        info = os.fstat(fd)
        data = None
        if 'manifest.json' in entries:
            manifest_fd = os.open('manifest.json', _flags(), dir_fd=fd)
            try:
                with os.fdopen(manifest_fd, 'rb') as stream:
                    if _signature(os.fstat(stream.fileno())) != entries['manifest.json']:
                        raise OSError('manifest changed')
                    raw = stream.read(1024 * 1024 + 1)
                if len(raw) <= 1024 * 1024:
                    data = json.loads(raw)
            except (ValueError, UnicodeError):
                data = None
        return {'signature': _signature(info), 'entries': entries, 'data': data,
                'size': sum(value[3] for value in entries.values())}
    finally:
        os.close(fd)


def _complete(pv, entry, row, src, key, interval):
    data = entry['data'] if entry else None
    if (not isinstance(data, dict) or data.get('version') != pv.VERSION
            or data.get('completion') == pv._TAIL_COMPLETION
            or not pv._manifest_owned_by(data, row)):
        return False
    try:
        revision = data.get('quality_revision', 1)
        extraction = data.get('extraction_revision', 1)
        if (revision not in range(1, pv.QUALITY_REVISION + 1)
                or type(extraction) is not int or extraction not in (1, pv.EXTRACTION_REVISION)
                or data.get('interval') != interval
                or pv._cache_key(row, src, interval, quality_revision=revision,
                                 extraction_revision=extraction) != key
                or not pv._completed_manifest(data, interval, revision)):
            return False
        return all(name in entry['entries'] and entry['entries'][name][3] > 0 for name in data['pages'])
    except (ValueError, TypeError, KeyError):
        return False


def clean(pv, dry_run):
    reasons = dict.fromkeys(('deleted', 'replaced', 'superseded', 'invalid'), 0)
    skipped = dict.fromkeys(('in_use', 'offline', 'protected', 'unknown'), 0)
    result = dict(dry_run=dry_run, candidates=0, candidate_bytes=0, reasons=reasons,
                  skipped=skipped, removed=0, freed=0, failed=0)
    libraries = store.list_libraries()
    with ExitStack() as stack:
        stack.enter_context(store.preview_scan_guard())
        # Match catalogue mutation ordering. Holding these locks also prevents a scan
        # from committing a changed catalogue while a candidate is removed.
        for lib in sorted(libraries, key=lambda item: int(item['id'])):
            stack.enter_context(library_mutation_lock(lib['id']))
        stack.enter_context(pv._lock)
        if pv._worker_thread and pv._worker_thread.is_alive():
            raise HTTPException(409, '进度缩略图正在生成，请完成后再清理')
        try:
            root = pv._root().absolute()
            if root.resolve() != root:
                raise OSError('symlink in cache root path')
            root_fd = os.open(pv._root(), _flags(True))
        except FileNotFoundError:
            return result
        except OSError:
            raise HTTPException(409, '无法安全读取缩略图目录，请检查目录权限和符号链接') from None
        stack.callback(os.close, root_fd)
        try:
            with os.scandir(root_fd) as iterator:
                names = []
                for entry in iterator:
                    if entry.is_symlink():
                        skipped['unknown'] += 1
                    elif entry.is_dir(follow_symlinks=False):
                        names.append(entry.name)
        except OSError:
            raise HTTPException(503, '读取缩略图目录失败') from None
        entries = {}
        for name in names:
            try:
                entries[name] = _inspect(root_fd, name)
            except OSError:
                skipped['unknown'] += 1
        healthy, rows, sources = {}, {}, {}
        snapshots = {int(lib['id']): store.preview_catalogue_snapshot(lib['id']) for lib in libraries}
        for lib in libraries:
            lid = int(lib['id'])
            try:
                healthy[lid] = (lib.get('effective_enabled', True)
                                and store.preview_scan_health(lid) == 'healthy'
                                and not store.fs_change_summary(lid)['pending'])
                if healthy[lid]:
                    backend = storage.backend_for(lid)
                    stat = getattr(backend, 'stat_uncached', backend.stat)
                    if not stat('').is_dir:
                        healthy[lid] = False
                    for row in store.get_library_preview_playables(lid):
                        identity = (row.get('kind', 'movie'), row['id'])
                        rows[identity] = row
                        try:
                            sources[identity] = raw_preview_source(row, backend, bypass_cache=True)
                        except storage.StorageOffline:
                            healthy[lid] = False
                            sources[identity] = None
                        except (storage.StorageError, OSError, ValueError):
                            sources[identity] = None
                else:
                    for row in store.get_library_preview_playables(lid):
                        rows[(row.get('kind', 'movie'), row['id'])] = row
            except (storage.StorageError, OSError, ValueError):
                healthy[lid] = False
        all_healthy = bool(libraries) and all(healthy.values())
        all_sources = all_healthy and all(value is not None for value in sources.values())
        key_index = {}
        for identity, src in sources.items():
            if src is None:
                continue
            row = rows[identity]
            interval = pv._interval(row)
            keys = [key for rev in range(pv.QUALITY_REVISION, 0, -1)
                    for key in pv._quality_keys(row, src, interval, rev)]
            for key in keys:
                key_index[key] = (identity, keys)
        with pv._sess_lock:
            playing = {(s.get('kind', 'movie'), s.get('vid')) for s in pv._sessions.values()}
        queued = {(item['kind'], item['id']) for item in pv._pending_queue()}
        active = playing | queued
        now = time.time()
        for name, entry in entries.items():
            if name in pv._preview_issued:
                skipped['in_use'] += 1
                continue
            info, files, data = entry['signature'], entry['entries'], entry['data']
            timestamps = [info[4], info[5], info[6]] + [t for value in files.values() for t in (value[4], value[5], value[6])]
            if any(now - t / 1e9 < PROTECTION_SECONDS for t in timestamps):
                skipped['protected'] += 1
                continue
            owner = None
            if isinstance(data, dict) and data.get('item_kind') in ('movie', 'episode', 'extra') and type(data.get('item_id')) is int and data['item_id'] > 0:
                owner = (data['item_kind'], data['item_id'])
            match = key_index.get(name)
            if match and owner is not None and owner != match[0]:
                skipped['unknown'] += 1
                continue
            if match and owner is None:
                owner = match[0]
            if owner in active:
                skipped['in_use'] += 1
                continue
            row = rows.get(owner)
            if row and not healthy.get(int(row.get('library_id') or 1), False):
                skipped['offline'] += 1
                continue
            if row and sources.get(owner) is None:
                skipped['unknown'] += 1
                continue
            if not row and not all_healthy:
                skipped['offline'] += 1
                continue
            if not row and not all_sources:
                skipped['unknown'] += 1
                continue
            reason = None
            if row:
                src = sources[owner]
                interval = pv._interval(row)
                keys = [key for rev in range(pv.QUALITY_REVISION, 0, -1)
                        for key in pv._quality_keys(row, src, interval, rev)]
                if name in keys:
                    # A partial current key can resume. Old rules are removed only
                    # when a strictly preferred full set exists.
                    if any(_complete(pv, entries.get(key), row, src, key, interval) for key in keys[:keys.index(name)]):
                        reason = 'superseded'
                elif any(_complete(pv, entries.get(key), row, src, key, interval) for key in keys):
                    reason = 'replaced'
                else:
                    skipped['unknown'] += 1
                    continue
            elif owner:
                reason = 'deleted'
            else:
                reason = 'invalid'
            if reason is None:
                continue
            result['candidates'] += 1
            result['candidate_bytes'] += entry['size']
            reasons[reason] += 1
            if dry_run:
                continue
            try:
                # Hold the database lock through final identity verification and
                # deletion, including records removed during the inventory.
                with store._lock:
                    if (store.list_libraries() != libraries or any(
                            store.preview_catalogue_snapshot(lid) != snapshot
                            for lid, snapshot in snapshots.items())):
                        skipped['unknown'] += 1
                        continue
                    # Detect use, replacement, or writes since inventory before unlink.
                    if _inspect(root_fd, name) != entry:
                        skipped['in_use'] += 1
                        continue
                    try:
                        check_libraries = [row.get('library_id') or 1] if row else [lib['id'] for lib in libraries]
                        for lid in check_libraries:
                            backend = storage.backend_for(lid)
                            fresh_stat = getattr(backend, 'stat_uncached', backend.stat)
                            if not fresh_stat('').is_dir:
                                raise storage.StorageOffline('library root is unavailable')
                    except (storage.StorageError, OSError, ValueError):
                        skipped['offline'] += 1
                        continue
                    if row:
                        try:
                            fresh = raw_preview_source(row, bypass_cache=True)
                        except storage.StorageError:
                            skipped['unknown'] += 1
                            continue
                        if source_snapshot(fresh, row) != source_snapshot(sources[owner], row):
                            skipped['unknown'] += 1
                            continue
                        preferred = keys[:keys.index(name)] if name in keys else keys
                        if not any(_complete(pv, _inspect(root_fd, key), row, fresh, key, interval)
                                   for key in preferred if key in entries):
                            skipped['unknown'] += 1
                            continue
                    shutil.rmtree(name, dir_fd=root_fd)
                    result['removed'] += 1
                    result['freed'] += entry['size']
                    pv._inventory_changed(pv._root() / name)
            except (OSError, storage.StorageError, ValueError) as error:
                result['failed'] += 1
                pv.logger.warning('obsolete preview cleanup failed key=%s: %s', name, error)
                # A failed rmtree may have removed some regular files already.
                # Count only files whose absence can be independently confirmed.
                try:
                    remaining = _inspect(root_fd, name)['entries']
                    result['freed'] += sum(info[3] for filename, info in entry['entries'].items()
                                           if filename not in remaining)
                except FileNotFoundError:
                    result['freed'] += entry['size']
                except OSError:
                    pass
        if not dry_run and (result['removed'] or result['failed']):
            data_usage.invalidate()
    return result
