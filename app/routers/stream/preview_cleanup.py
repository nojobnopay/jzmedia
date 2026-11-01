"""Conservative, read-only inventory and bounded cleanup of preview directories."""
from contextlib import ExitStack
import json
import os
import shutil
import stat
import threading
import time
import uuid

from fastapi import HTTPException

from ... import data_usage, storage, store
from ...library_mutex import library_mutation_lock
from .preview_source import raw_preview_source, source_snapshot

# Cache URLs are immutable and clients may keep a manifest while playing.
PROTECTION_SECONDS = 24 * 60 * 60
_TRASH = '.cleanup-trash'
_cleaning = threading.Lock()


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


def _catalogue(pv):
    """Read a consistent catalogue without retaining any lock during storage I/O."""
    libraries = store.list_libraries()
    with ExitStack() as stack:
        stack.enter_context(store.preview_scan_guard())
        for lib in sorted(libraries, key=lambda item: int(item['id'])):
            stack.enter_context(library_mutation_lock(lib['id']))
        stack.enter_context(store._lock)
        if store.list_libraries() != libraries:
            raise HTTPException(409, '视频库配置已变化，请重新盘点')
        snapshots = {int(lib['id']): store.preview_catalogue_snapshot(lib['id']) for lib in libraries}
        healthy, blockers = {}, {}
        for lib in libraries:
            lid = int(lib['id'])
            if not lib.get('effective_enabled', True):
                healthy[lid], blockers[lid] = False, 'source_unavailable'
            elif (store.preview_scan_health(lid) != 'healthy'
                  or store.fs_change_summary(lid)['pending']):
                healthy[lid], blockers[lid] = False, 'scan_required'
            else:
                healthy[lid] = True
        rows = {(row.get('kind', 'movie'), row['id']): row for lib in libraries
                for row in store.get_library_preview_playables(lib['id'])}
        intervals = {int(lib['id']): pv._interval({'library_id': lib['id']}, library=lib)
                     for lib in libraries}
    return libraries, snapshots, healthy, rows, intervals, blockers


def _active(pv):
    """Caller holds the preview lock; no source metadata is resolved here."""
    with pv._sess_lock:
        playing = {(s.get('kind', 'movie'), s.get('vid')) for s in pv._sessions.values()}
    return playing | {(item['kind'], item['id']) for item in pv._pending_queue()}


def _open_root(root):
    if root.resolve() != root:
        raise OSError('symlink in cache root path')
    return os.open(root, _flags(True))


def _erase(pv, parent_fd, name, entry, result):
    try:
        shutil.rmtree(name, dir_fd=parent_fd)
        result['removed'] += 1
        result['freed'] += entry['size']
    except OSError as error:
        result['failed'] += 1
        pv.logger.warning('obsolete preview deletion failed key=%s: %s', name, error)
        try:
            remaining = _inspect(parent_fd, name)['entries']
            result['freed'] += sum(info[3] for filename, info in entry['entries'].items()
                                   if filename not in remaining)
        except FileNotFoundError:
            result['freed'] += entry['size']
        except OSError:
            pass


def _recover(pv, root):
    """Only detached, private cleanup batches are eligible for crash recovery."""
    result = dict(removed=0, freed=0, failed=0)
    try:
        with ExitStack() as stack:
            root_fd = _open_root(root)
            stack.callback(os.close, root_fd)
            trash_fd = os.open(_TRASH, _flags(True), dir_fd=root_fd)
            stack.callback(os.close, trash_fd)
            for batch in os.listdir(trash_fd):
                if not pv._KEY_RE.fullmatch(batch):
                    continue
                try:
                    batch_fd = os.open(batch, _flags(True), dir_fd=trash_fd)
                    try:
                        for name in os.listdir(batch_fd):
                            entry = _inspect(batch_fd, name)
                            _erase(pv, batch_fd, name, entry, result)
                    finally:
                        os.close(batch_fd)
                    os.rmdir(batch, dir_fd=trash_fd)
                except OSError as error:
                    pv.logger.warning('recover preview cleanup batch failed batch=%s: %s', batch, error)
    except FileNotFoundError:
        pass
    except OSError as error:
        pv.logger.warning('recover preview cleanup failed: %s', error)
    if result['removed'] or result['freed']:
        data_usage.invalidate()
    return result


def start_recovery(pv):
    """Restart recovery never enumerates libraries or blocks the startup thread."""
    root = pv._root().absolute()

    def run():
        if not _cleaning.acquire(blocking=False):
            return
        try:
            _recover(pv, root)
        finally:
            _cleaning.release()

    threading.Thread(target=run, name='preview-cleanup-recovery', daemon=True).start()


def clean(pv, dry_run):
    if not _cleaning.acquire(blocking=False):
        raise HTTPException(409, '缩略图缓存正在盘点或清理，请稍后重试')
    try:
        return _clean(pv, dry_run)
    finally:
        _cleaning.release()


def _clean(pv, dry_run):
    reasons = dict.fromkeys(('deleted', 'replaced', 'superseded', 'invalid'), 0)
    skipped = dict.fromkeys(('in_use', 'offline', 'protected', 'unknown'), 0)
    skipped_details = dict.fromkeys(('replacement_missing', 'scan_required', 'recently_used',
                                    'in_use', 'source_unavailable', 'unknown'), 0)
    result = dict(dry_run=dry_run, candidates=0, candidate_bytes=0, reasons=reasons,
                  skipped=skipped, skipped_details=skipped_details, removed=0, freed=0, failed=0)

    def skip(category, detail, count=1):
        # Keep the original counters while exposing one actionable primary reason
        # per skipped directory. Details never relax an existing safety check.
        skipped[category] += count
        skipped_details[detail] += count

    with pv._lock:
        if pv._worker_thread and pv._worker_thread.is_alive():
            raise HTTPException(409, '进度缩略图正在生成，请完成后再清理')
        issued, active = set(pv._preview_issued), _active(pv)
    libraries, snapshots, healthy, rows, intervals, blockers = _catalogue(pv)
    with ExitStack() as stack:
        try:
            root = pv._root().absolute()
            root_fd = _open_root(root)
        except FileNotFoundError:
            return result
        except OSError:
            raise HTTPException(409, '无法安全读取缩略图目录，请检查目录权限和符号链接') from None
        stack.callback(os.close, root_fd)
        if not dry_run:
            recovery = _recover(pv, root)
            result.update(recovered=recovery['removed'], recovered_bytes=recovery['freed'])
        try:
            with os.scandir(root_fd) as iterator:
                names = []
                for entry in iterator:
                    if entry.name == _TRASH:
                        continue
                    if entry.is_symlink():
                        skip('unknown', 'unknown')
                    elif entry.is_dir(follow_symlinks=False):
                        names.append(entry.name)
        except OSError:
            raise HTTPException(503, '读取缩略图目录失败') from None
        entries = {}
        for name in names:
            try:
                entries[name] = _inspect(root_fd, name)
            except OSError:
                skip('unknown', 'unknown')
        sources, backends = {}, {}
        for lib in libraries:
            lid = int(lib['id'])
            try:
                if healthy[lid]:
                    backend = backends[lid] = storage.backend_for(lid)
                    fresh_stat = getattr(backend, 'stat_uncached', backend.stat)
                    if not fresh_stat('').is_dir:
                        healthy[lid] = False
                        blockers[lid] = 'source_unavailable'
                    for identity, row in rows.items():
                        if (row.get('library_id') or 1) != lid:
                            continue
                        try:
                            sources[identity] = raw_preview_source(row, backend, bypass_cache=True)
                        except storage.StorageOffline:
                            healthy[lid] = False
                            blockers[lid] = 'source_unavailable'
                            sources[identity] = None
                        except (storage.StorageError, OSError, ValueError):
                            sources[identity] = None
            except (storage.StorageError, OSError, ValueError):
                healthy[lid] = False
                blockers[lid] = 'source_unavailable'
        all_healthy = bool(libraries) and all(healthy.values())
        all_sources = all_healthy and all(value is not None for value in sources.values())
        unowned_blocker = ('scan_required' if 'scan_required' in blockers.values()
                           else 'source_unavailable' if blockers else 'unknown')
        key_index, owner_keys = {}, {}
        for identity, src in sources.items():
            if src is None:
                continue
            row = rows[identity]
            interval = intervals[int(row.get('library_id') or 1)]
            keys = [key for rev in range(pv.QUALITY_REVISION, 0, -1)
                    for key in pv._quality_keys(row, src, interval, rev)]
            owner_keys[identity] = keys
            for key in keys:
                key_index[key] = (identity, keys)
        now = time.time()
        candidates = []
        for name, entry in entries.items():
            if name in issued:
                skip('in_use', 'in_use')
                continue
            info, files, data = entry['signature'], entry['entries'], entry['data']
            timestamps = [info[4], info[5], info[6]] + [t for value in files.values() for t in (value[4], value[5], value[6])]
            if any(now - t / 1e9 < PROTECTION_SECONDS for t in timestamps):
                skip('protected', 'recently_used')
                continue
            owner = None
            if isinstance(data, dict) and data.get('item_kind') in ('movie', 'episode', 'extra') and type(data.get('item_id')) is int and data['item_id'] > 0:
                owner = (data['item_kind'], data['item_id'])
            match = key_index.get(name)
            if match and owner is not None and owner != match[0]:
                skip('unknown', 'unknown')
                continue
            if match and owner is None:
                owner = match[0]
            if owner in active:
                skip('in_use', 'in_use')
                continue
            row = rows.get(owner)
            if row and not healthy.get(int(row.get('library_id') or 1), False):
                skip('offline', blockers.get(int(row.get('library_id') or 1), 'unknown'))
                continue
            if row and sources.get(owner) is None:
                skip('unknown', 'source_unavailable')
                continue
            if not row and not all_healthy:
                skip('offline', unowned_blocker)
                continue
            if not row and not all_sources:
                skip('unknown', 'source_unavailable')
                continue
            reason = None
            if row:
                src = sources[owner]
                interval = intervals[int(row.get('library_id') or 1)]
                keys = owner_keys[owner]
                if name in keys:
                    # A partial current key can resume. Old rules are removed only
                    # when a strictly preferred full set exists.
                    if any(_complete(pv, entries.get(key), row, src, key, interval) for key in keys[:keys.index(name)]):
                        reason = 'superseded'
                elif any(_complete(pv, entries.get(key), row, src, key, interval) for key in keys):
                    reason = 'replaced'
                else:
                    skip('unknown', 'replacement_missing')
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
            candidates.append((name, entry, owner, row))
        if dry_run or not candidates:
            return result

        # Recheck remote roots and sources once, entirely outside global locks.
        # Catalogue snapshots are then compared once for the whole commit, rather
        # than querying every library for every obsolete cache directory.
        root_health = {}
        for lid, backend in backends.items():
            try:
                fresh_stat = getattr(backend, 'stat_uncached', backend.stat)
                root_health[lid] = fresh_stat('').is_dir
            except (storage.StorageError, OSError, ValueError):
                root_health[lid] = False
        valid = []
        refreshed = {}
        for name, entry, owner, row in candidates:
            lids = [int(row.get('library_id') or 1)] if row else list(healthy)
            if not all(root_health.get(lid, False) for lid in lids):
                skip('offline', 'source_unavailable')
                continue
            if row:
                try:
                    if owner not in refreshed:
                        refreshed[owner] = raw_preview_source(row, backends[lids[0]], bypass_cache=True)
                    fresh = refreshed[owner]
                except (storage.StorageError, OSError, ValueError):
                    skip('unknown', 'source_unavailable')
                    continue
                try:
                    if source_snapshot(fresh, row) != source_snapshot(sources[owner], row):
                        raise ValueError('source changed')
                    interval = intervals[lids[0]]
                    keys = owner_keys[owner]
                    preferred = keys[:keys.index(name)] if name in keys else keys
                    complete = any(_complete(pv, _inspect(root_fd, key), row, fresh, key, interval)
                                   for key in preferred if key in entries)
                except (storage.StorageError, OSError, ValueError):
                    skip('unknown', 'unknown')
                    continue
                if not complete:
                    skip('unknown', 'replacement_missing')
                    continue
            valid.append((name, entry, owner))
        if not valid:
            return result

        with ExitStack() as commit:
            commit.enter_context(store.preview_scan_guard())
            for lib in sorted(libraries, key=lambda item: int(item['id'])):
                commit.enter_context(library_mutation_lock(lib['id']))
            commit.enter_context(pv._lock)
            commit.enter_context(store._lock)
            catalogue_changed = (store.list_libraries() != libraries or any(
                store.preview_catalogue_snapshot(lid) != snapshot for lid, snapshot in snapshots.items()))
            scan_changed = not catalogue_changed and any(
                healthy[lid] and (store.preview_scan_health(lid) != 'healthy'
                                 or store.fs_change_summary(lid)['pending']) for lid in healthy)
            if catalogue_changed or scan_changed:
                skip('unknown', 'unknown' if catalogue_changed else 'scan_required', len(valid))
                return result
            active = _active(pv)
            reserved = []
            for name, entry, owner in valid:
                if (name in pv._preview_issued or owner in active or pv._cache_removing
                        or pv._worker_thread and pv._worker_thread.is_alive()):
                    skip('in_use', 'in_use')
                    continue
                reserved.append((name, entry))
            pv._cache_removing.update(name for name, _ in reserved)

        # Reservations are the logical removal point. All serving/mutating preview
        # entry points honor them until rename has detached the old directory.
        try:
            if reserved:
                try:
                    os.mkdir(_TRASH, mode=0o700, dir_fd=root_fd)
                except FileExistsError:
                    pass
                trash_fd = os.open(_TRASH, _flags(True), dir_fd=root_fd)
                stack.callback(os.close, trash_fd)
                batch = uuid.uuid4().hex
                os.mkdir(batch, mode=0o700, dir_fd=trash_fd)
                batch_fd = os.open(batch, _flags(True), dir_fd=trash_fd)
                stack.callback(os.close, batch_fd)
                detached = []
                for name, entry in reserved:
                    moved = False
                    try:
                        if _inspect(root_fd, name) != entry:
                            skip('in_use', 'in_use')
                            continue
                        os.rename(name, name, src_dir_fd=root_fd, dst_dir_fd=batch_fd)
                        moved = True
                    except OSError as error:
                        result['failed'] += 1
                        pv.logger.warning('quarantine preview cache failed key=%s: %s', name, error)
                    finally:
                        with pv._lock:
                            pv._cache_removing.discard(name)
                    if moved:
                        pv._inventory_changed(root / name)
                        detached.append((name, entry))
                # Detach all keys before the first potentially slow rmtree so
                # workers may resume while quarantined files are being erased.
                pv._auto_wake.set()
                for name, entry in detached:
                    _erase(pv, batch_fd, name, entry, result)
                try:
                    os.rmdir(batch, dir_fd=trash_fd)
                except OSError:
                    pass  # A partial delete stays quarantined for restart/retry.
        except OSError as error:
            result['failed'] += len(reserved)
            pv.logger.warning('prepare preview quarantine failed: %s', error)
        finally:
            with pv._lock:
                pv._cache_removing.difference_update(name for name, _ in reserved)
            pv._auto_wake.set()
        if not dry_run and (result['removed'] or result['failed']):
            data_usage.invalidate()
    return result
