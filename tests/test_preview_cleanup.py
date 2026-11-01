import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import storage
from app.routers.stream import preview_cleanup as cleanup
from app.routers.stream import previews as pv


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / 'previews'
    root.mkdir()
    monkeypatch.setattr(pv, '_root', lambda: root)
    monkeypatch.setattr(pv, '_worker_thread', None)
    monkeypatch.setattr(pv, '_auto_queue', [])
    monkeypatch.setattr(pv, '_active_job', None)
    monkeypatch.setattr(pv, '_preview_issued', set())
    monkeypatch.setattr(pv, '_cache_removing', set())
    monkeypatch.setattr(pv, '_sessions', {})
    monkeypatch.setattr(pv, '_inventory_changed', lambda *_args: None)
    monkeypatch.setattr(cleanup.data_usage, 'invalidate', lambda: None)
    monkeypatch.setattr(cleanup.time, 'time', lambda: time.time_ns() / 1e9 + 3 * 86400)
    monkeypatch.setattr(cleanup.store, 'list_libraries', lambda: [{'id': 1, 'source': 'local', 'effective_enabled': True}])
    monkeypatch.setattr(cleanup.store, 'preview_scan_health', lambda _: 'healthy')
    monkeypatch.setattr(cleanup.store, 'fs_change_summary', lambda _: {'pending': False})
    row = {'id': 7, 'kind': 'movie', 'library_id': 1, 'file_path': 'film.mkv'}
    monkeypatch.setattr(cleanup.store, 'get_library_preview_playables', lambda _: [row])
    monkeypatch.setattr(pv.store, 'get_library', lambda _: {'source': 'local'})
    backend = SimpleNamespace(library_id=1, root='/test', stat=lambda _: SimpleNamespace(is_dir=True))
    src = SimpleNamespace(backend=backend, rel='film.mkv', size=1, mtime=2)
    monkeypatch.setattr(cleanup.storage, 'backend_for', lambda _: backend)
    monkeypatch.setattr(cleanup.storage, 'clear_meta_cache', lambda *_: None)
    monkeypatch.setattr(cleanup, 'raw_preview_source', lambda *_args, **_kwargs: src)
    monkeypatch.setattr(cleanup, 'source_snapshot', lambda s, _: (s.rel, s.size, s.mtime))
    monkeypatch.setattr(pv, '_interval', lambda *_args, **_kwargs: 10)
    return root, row, src


def make(root, key, owner=7, state='ready', revision=3):
    path = root / key
    path.mkdir()
    data = dict(version=1, item_kind='movie', item_id=owner, state=state, interval=10,
                duration=20, count=2, width=480 if revision == 3 else 160,
                height=270 if revision == 3 else 90, columns=5, rows=5,
                quality_revision=revision, pages=['page_0000.jpg'])
    if revision == 3:
        data['extraction_revision'] = 2
    (path / 'manifest.json').write_text(json.dumps(data))
    (path / 'page_0000.jpg').write_bytes(b'image')
    return path


def run(dry=True):
    return pv.preview_cache_clean_obsolete(pv.PreviewCacheCleanBody(dry_run=dry))


def test_inventory_and_cleanup_are_selective_and_idempotent(env):
    root, row, src = env
    current = pv._cache_key(row, src, 10)
    old_rule = pv._cache_key(row, src, 10, quality_revision=2)
    old_source = pv._cache_key(row, src._replace(mtime=1) if hasattr(src, '_replace') else SimpleNamespace(**{**src.__dict__, 'mtime': 1}), 10)
    keep = make(root, current)
    obsolete = [make(root, old_rule, revision=2), make(root, old_source), make(root, 'a' * 32, owner=9)]
    (root / 'auto-queue.json').write_text('[]')
    before = {p: p.stat().st_atime_ns for directory in [keep, *obsolete] for p in [directory, *directory.iterdir()]}
    check = run()
    assert check['candidates'] == 3
    assert check['reasons'] == {'deleted': 1, 'replaced': 1, 'superseded': 1, 'invalid': 0}
    assert all(p.stat().st_atime_ns == stamp for p, stamp in before.items())
    assert all(p.exists() for p in obsolete)
    result = run(False)
    assert result['removed'] == 3 and result['failed'] == 0 and result['freed'] == check['candidate_bytes']
    assert keep.exists() and (root / 'auto-queue.json').exists()
    assert run(False)['removed'] == 0


def test_incomplete_and_unknown_libraries_protect_ownership(env, monkeypatch):
    root, row, src = env
    old = make(root, 'a' * 32)
    orphan = make(root, 'b' * 32, owner=9)
    monkeypatch.setattr(cleanup.store, 'preview_scan_health', lambda _: 'unknown')
    result = run(False)
    assert result['skipped']['offline'] == 2 and result['removed'] == 0
    assert old.exists() and orphan.exists()


def test_partial_current_does_not_replace_old_source(env):
    root, row, src = env
    make(root, pv._cache_key(row, src, 10), state='partial')
    old = make(root, 'a' * 32)
    assert run(False)['removed'] == 0
    assert old.exists()


def test_usage_recent_symlink_and_running_worker_are_protected(env, monkeypatch):
    root, row, src = env
    old = make(root, 'a' * 32, owner=9)
    pv._preview_issued.add(old.name)
    outside = root.parent / 'outside'
    outside.mkdir()
    (root / 'link').symlink_to(outside, target_is_directory=True)
    result = run(False)
    assert result['skipped']['in_use'] == 1 and result['skipped']['unknown'] == 1
    assert old.exists() and outside.exists()
    monkeypatch.setattr(pv, '_worker_thread', SimpleNamespace(is_alive=lambda: True))
    with pytest.raises(HTTPException) as error:
        run()
    assert error.value.status_code == 409


def test_full_replacement_must_match_key_rule(env):
    root, row, src = env
    current = make(root, pv._cache_key(row, src, 10))
    data = json.loads((current / 'manifest.json').read_text())
    data['extraction_revision'] = 1
    (current / 'manifest.json').write_text(json.dumps(data))
    old = make(root, 'a' * 32)
    assert run(False)['removed'] == 0
    assert old.exists()


def test_library_offline_in_any_source_protects_every_item(env, monkeypatch):
    root, row, src = env
    second = {**row, 'id': 8}
    monkeypatch.setattr(cleanup.store, 'get_library_preview_playables', lambda _: [row, second])
    def read(item, *_args, **_kwargs):
        if item['id'] == 8:
            raise storage.StorageOffline('offline')
        return src
    monkeypatch.setattr(cleanup, 'raw_preview_source', read)
    old = make(root, 'a' * 32)
    assert run(False)['skipped']['offline'] == 1
    assert old.exists()


def test_protection_window_and_queue(env, monkeypatch):
    root, row, src = env
    queued = make(root, 'a' * 32)
    recently_written = make(root, 'b' * 32, owner=9)
    monkeypatch.setattr(pv, '_auto_queue', [{'kind': 'movie', 'id': 7}])
    now_ns = time.time_ns()
    os.utime(recently_written, ns=(now_ns + 3 * 86400 * 10**9, now_ns + 3 * 86400 * 10**9))
    result = run(False)
    assert result['skipped']['in_use'] == 1 and result['skipped']['protected'] == 1
    assert queued.exists() and recently_written.exists()


def test_catalogue_change_during_confirmation_prevents_deletion(env, monkeypatch):
    root, row, src = env
    orphan = make(root, 'a' * 32, owner=9)
    snapshots = iter([(), (('movie', 9),)])
    monkeypatch.setattr(cleanup.store, 'preview_catalogue_snapshot', lambda _: next(snapshots))
    result = run(False)
    assert result['removed'] == 0 and result['skipped']['unknown'] == 1
    assert orphan.exists()


def test_partial_delete_failure_counts_bytes_and_continues(env, monkeypatch):
    root, row, src = env
    first = make(root, 'a' * 32, owner=9)
    second = make(root, 'b' * 32, owner=10)
    original = cleanup.shutil.rmtree
    def fail_one(name, *, dir_fd):
        if name == first.name:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=dir_fd)
            try:
                os.unlink('page_0000.jpg', dir_fd=fd)
            finally:
                os.close(fd)
            raise PermissionError('simulated failure')
        original(name, dir_fd=dir_fd)
    monkeypatch.setattr(cleanup.shutil, 'rmtree', fail_one)
    second_size = sum(p.stat().st_size for p in second.iterdir())
    result = run(False)
    assert result['failed'] == 1 and result['removed'] == 1
    assert result['freed'] == second_size + 5
    quarantined = list((root / cleanup._TRASH).glob('*/' + first.name))
    assert len(quarantined) == 1 and not first.exists() and not second.exists()
    monkeypatch.setattr(cleanup.shutil, 'rmtree', original)
    recovered = cleanup._recover(pv, root)
    assert recovered['removed'] == 1 and not quarantined[0].exists()


def test_confirmation_rechecks_queue_and_issued_player_cache(env, monkeypatch):
    root, row, src = env
    make(root, pv._cache_key(row, src, 10))
    obsolete = make(root, 'a' * 32)
    assert run()['candidates'] == 1
    pv._preview_issued.add(obsolete.name)
    assert run(False)['skipped']['in_use'] == 1 and obsolete.exists()
    pv._preview_issued.clear()
    monkeypatch.setattr(pv, '_auto_queue', [{'kind': 'movie', 'id': 7}])
    assert run(False)['skipped']['in_use'] == 2 and obsolete.exists()


def test_invalid_and_symlinked_cache_directories(env):
    root, row, src = env
    invalid = root / 'broken-cache'
    invalid.mkdir()
    (invalid / 'manifest.json').write_text('{bad json')
    unsafe = root / ('b' * 32)
    unsafe.mkdir()
    (unsafe / 'manifest.json').symlink_to(invalid / 'manifest.json')
    result = run(False)
    assert result['reasons']['invalid'] == 1 and result['removed'] == 1
    assert result['skipped']['unknown'] == 1
    assert not invalid.exists() and unsafe.exists()


def test_library_going_offline_during_cleanup_skips_orphan(env, monkeypatch):
    root, row, src = env
    orphan = make(root, 'a' * 32, owner=9)
    calls = []
    def library_stat(_):
        calls.append(1)
        if len(calls) > 1:
            raise storage.StorageOffline('disconnected')
        return SimpleNamespace(is_dir=True)
    monkeypatch.setattr(src.backend, 'stat', library_stat)
    result = run(False)
    assert result['skipped']['offline'] == 1 and result['removed'] == 0
    assert orphan.exists()


@pytest.mark.parametrize('stage', ['inventory', 'final_stat', 'source', 'rename', 'erase'])
def test_slow_cleanup_io_leaves_database_and_queue_available(env, monkeypatch, stage):
    root, row, src = env
    if stage == 'source':
        make(root, pv._cache_key(row, src, 10))
        make(root, 'a' * 32)
    else:
        make(root, 'a' * 32, owner=9)
        make(root, 'b' * 32, owner=10)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(pv, '_schedule_ready_queue_reconcile', lambda: None)

    def wait():
        entered.set()
        assert release.wait(5), 'test must release blocked storage'

    original_stat = src.backend.stat
    calls = []
    def stat(path):
        calls.append(path)
        if (stage == 'inventory' and len(calls) == 1
                or stage == 'final_stat' and len(calls) == 2):
            wait()
        return original_stat(path)
    monkeypatch.setattr(src.backend, 'stat', stat)
    original_source = cleanup.raw_preview_source
    source_calls = []
    def source(*args, **kwargs):
        source_calls.append(1)
        if stage == 'source' and len(source_calls) == 2:
            wait()
        return original_source(*args, **kwargs)
    monkeypatch.setattr(cleanup, 'raw_preview_source', source)
    original_rename, original_erase = cleanup.os.rename, cleanup.shutil.rmtree
    def rename(*args, **kwargs):
        if stage == 'rename':
            wait()
        return original_rename(*args, **kwargs)
    def erase(*args, **kwargs):
        if stage == 'erase':
            assert not pv._cache_removing, 'all keys must detach before recursive deletion'
            wait()
        return original_erase(*args, **kwargs)
    monkeypatch.setattr(cleanup.os, 'rename', rename)
    monkeypatch.setattr(cleanup.shutil, 'rmtree', erase)

    with ThreadPoolExecutor(max_workers=3) as pool:
        work = pool.submit(run, False)
        try:
            assert entered.wait(3)
            assert pool.submit(cleanup.store.get_movie, 999999).result(timeout=2) is None
            assert pool.submit(pv.preview_queue, None, 0, 50).result(timeout=2)['total'] == 0
        finally:
            release.set()
        assert work.result(timeout=3)['failed'] == 0


def test_manifest_source_resolution_does_not_lock_queue(env, monkeypatch):
    _, row, src = env
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(pv, '_schedule_ready_queue_reconcile', lambda: None)
    def source(*_args):
        entered.set()
        assert release.wait(5)
        return row, src
    monkeypatch.setattr(pv, '_version_source', source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        manifest = pool.submit(pv.preview_manifest, 7)
        try:
            assert entered.wait(3)
            assert pool.submit(pv.preview_queue, None, 0, 50).result(timeout=2)['total'] == 0
        finally:
            release.set()
        assert manifest.result(timeout=3)['state'] == 'missing'


def test_issued_manifest_wins_over_cleanup_commit(env, monkeypatch):
    root, row, src = env
    key = pv._cache_key(row, src, 10, quality_revision=2)
    old = make(root, key, revision=2)
    make(root, pv._cache_key(row, src, 10))
    # A generation/filter change can select an older usable set after inventory.
    entered, release = threading.Event(), threading.Event()
    original_stat = src.backend.stat
    calls = []
    def stat(path):
        calls.append(1)
        if len(calls) == 2:
            entered.set()
            assert release.wait(5)
        return original_stat(path)
    monkeypatch.setattr(src.backend, 'stat', stat)
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(run, False)
        try:
            assert entered.wait(3)
            # Exercise the actual image issuance path, which shares the manifest guard.
            assert pool.submit(pv.preview_image, key, 'page_0000.jpg').result(timeout=2)
        finally:
            release.set()
        assert work.result(timeout=3)['removed'] == 0
    assert old.exists() and key in pv._preview_issued


def test_cleanup_reservation_wins_over_issuance(env, monkeypatch):
    root, row, src = env
    key = pv._cache_key(row, src, 10, quality_revision=2)
    make(root, key, revision=2)
    current_key = pv._cache_key(row, src, 10)
    make(root, current_key)
    monkeypatch.setattr(pv, '_version_source', lambda *_args: (row, src))
    original = cleanup.os.rename
    entered, release = threading.Event(), threading.Event()
    def rename(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(cleanup.os, 'rename', rename)
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(run, False)
        try:
            assert entered.wait(3)
            assert key in pv._cache_removing
            with pytest.raises(HTTPException) as error:
                pv.preview_image(key, 'page_0000.jpg')
            assert error.value.status_code == 404
            assert pool.submit(pv.preview_manifest, 7).result(timeout=2)['key'] == current_key
            with pytest.raises(HTTPException) as error:
                pv._start([('movie', 7)])
            assert error.value.status_code == 409
        finally:
            release.set()
        assert work.result(timeout=3)['removed'] == 1
    assert not pv._cache_removing


@pytest.mark.parametrize('mutation', ['source', 'health', 'queue', 'worker'])
def test_inventory_changes_before_reservation_preserve_cache(env, monkeypatch, mutation):
    root, row, src = env
    make(root, pv._cache_key(row, src, 10))
    old = make(root, 'a' * 32)
    original = cleanup.raw_preview_source
    calls = []
    def source(*args, **kwargs):
        calls.append(1)
        value = original(*args, **kwargs)
        if len(calls) == 2:
            if mutation == 'source':
                return SimpleNamespace(**{**src.__dict__, 'mtime': 99})
            if mutation == 'health':
                monkeypatch.setattr(cleanup.store, 'preview_scan_health', lambda _: 'unknown')
            elif mutation == 'queue':
                pv._auto_queue.append({'kind': 'movie', 'id': 7, 'library_id': 1})
            elif mutation == 'worker':
                monkeypatch.setattr(pv, '_worker_thread', SimpleNamespace(is_alive=lambda: True))
        return value
    monkeypatch.setattr(cleanup, 'raw_preview_source', source)
    assert run(False)['removed'] == 0
    assert old.exists() and not pv._cache_removing


def test_failed_quarantine_releases_reservations_and_preserves_directory(env, monkeypatch):
    root, _, _ = env
    old = make(root, 'a' * 32, owner=9)
    def fail(*_args, **_kwargs):
        raise PermissionError('simulated rename failure')
    monkeypatch.setattr(cleanup.os, 'rename', fail)
    result = run(False)
    assert result['failed'] == 1 and result['removed'] == 0
    assert old.exists() and not pv._cache_removing


def test_dry_run_does_not_recover_and_restart_recovery_needs_no_catalogue(env, monkeypatch):
    root, _, _ = env
    trash_batch = root / cleanup._TRASH / ('f' * 32)
    trash_batch.mkdir(parents=True)
    old = make(trash_batch, 'a' * 32, owner=9)
    assert run()['candidates'] == 0 and old.exists()
    monkeypatch.setattr(cleanup.store, 'list_libraries', lambda: pytest.fail('recovery must not scan libraries'))
    assert cleanup._recover(pv, root)['removed'] == 1
    assert not old.exists() and not trash_batch.exists()


@pytest.mark.parametrize('revision', [1, 2, 3])
def test_source_read_policy_change_does_not_reuse_older_smb_bytes(env, monkeypatch, revision):
    root, row, src = env
    old_key = pv._cache_key(row, src, 10, quality_revision=revision)
    make(root, old_key, revision=revision)
    monkeypatch.setattr(src.backend, 'read_cache_revision', 1, raising=False)
    monkeypatch.setattr(pv, '_version_source', lambda *_args: (row, src))
    key = pv._cache_key(row, src, 10, quality_revision=revision)
    assert key != old_key
    assert pv.preview_manifest(7)['state'] == 'missing'
    assert pv._current_ready(row, src, 10) == (None, None)
    make(root, pv._cache_key(row, src, 10))
    assert pv.preview_manifest(7)['state'] == 'ready'
