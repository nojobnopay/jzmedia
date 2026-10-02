"""File manager mutations preserve TV identity and persistent scan reminders."""
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import library_paths, scanner, storage, store
from app.jobkit import JobRegistry
from app.routers import jobs
from app.routers.fs import copy as fs_copy
from app.routers.fs import routes
from app.storage import smb
from _smb_fake import FakeSmbClient


@pytest.fixture(params=['local', 'smb'])
def tv_files(request, tmp_path, monkeypatch):
    root = tmp_path / 'tv'
    root.mkdir()
    if request.param == 'smb':
        monkeypatch.setenv('SMB_DRIVER', 'direct')
        monkeypatch.setattr(smb, 'smbclient', FakeSmbClient(root))
        lib = store.create_library(name=f'changes-{tmp_path.name}', kind='tv', source='smb',
                                   smb={'host': 'nas', 'share': 'video', 'username': 'u', 'password': 'p'})
    else:
        lib = store.create_library(name=f'changes-{tmp_path.name}', kind='tv', path=str(root))
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root
    store.delete_media_library(lib['media_library_id'])
    library_paths.invalidate_cache()
    smb.invalidate()


def touch(root, rel, data=b'synthetic'):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_tv_rename_move_preserves_identity_progress_and_followers(tv_files):
    lib, root = tv_files
    lid = lib['id']
    old = 'Review Show/Season 01/Review.S01E01.mkv'
    new = 'Review Show/Season 01/Renamed.S01E01.mkv'
    touch(root, old)
    scanner.scan_all(library_id=lid)
    episode = store.get_episode_by_path(old, lid)
    eid, sid = episode['id'], episode['show_id']
    store.update_episode_meta(eid, watched=1, title='手工资料')
    store.save_progress(eid, 123, 1000, kind='episode')
    touch(root, old[:-4] + '.chs.srt')
    touch(root, old[:-4] + '.nfo', b'<episodedetails/>')
    extra_path = old[:-4] + '-trailer.mkv'
    touch(root, extra_path)
    extra_id = store.upsert_tv_extra(extra_path, sid, library_id=lid)
    storage.clear_meta_cache(lid)
    preview = routes.fs_rename({'library_id': lid, 'from': old, 'name': Path(new).name})
    followers = preview['plans'][0]['followers']
    assert {f['from'] for f in followers} == {old[:-4] + '.chs.srt', old[:-4] + '.nfo', extra_path}
    result = routes.fs_rename({'library_id': lid, 'from': old, 'name': Path(new).name, 'dry_run': False})
    assert result['moved'] == 1
    assert result['results'][0]['followed'] == 3
    assert store.get_episode_by_path(new, lid)['id'] == eid
    assert store.get_episode_by_path(old, lid) is None
    assert store.get_progress(eid, kind='episode')['position'] == 123
    assert store.get_extra(extra_id)['show_id'] == sid
    assert store.get_extra(extra_id)['file_path'] == new[:-4] + '-trailer.mkv'
    target_dir = 'Review Show/Archive'
    result = routes.fs_move({'library_id': lid, 'from': new, 'to_dir': target_dir, 'dry_run': False})
    final = target_dir + '/' + Path(new).name
    assert result['moved'] == 1
    scanner.scan_all(library_id=lid)
    current = store.get_episode_by_path(final, lid)
    assert current['id'] == eid and current['watched'] == 1 and current['title'] == '手工资料'
    assert store.get_progress(eid, kind='episode')['position'] == 123
    assert routes.fs_changes(lid)['pending']


def test_tv_delete_removes_catalogue_and_progress(tv_files):
    lib, root = tv_files
    lid = lib['id']
    rel = 'Show/Season 01/Show.S01E01.mkv'
    touch(root, rel)
    scanner.scan_all(library_id=lid)
    ep = store.get_episode_by_path(rel, lid)
    store.save_progress(ep['id'], 200, 1000, kind='episode')
    result = routes.fs_delete({'library_id': lid, 'paths': [rel], 'dry_run': False, 'confirm': True})
    assert result['deleted'] == 1
    assert not (root / rel).exists()
    assert store.get_episode_by_path(rel, lid) is None
    assert store.get_progress(ep['id'], kind='episode') is None
    assert store.get_scan_state(rel, lid) is None
    assert routes.fs_changes(lid)['actions'] == {'delete': 1}


def test_preview_failed_operations_do_not_mark_changes(tv_files):
    lib, root = tv_files
    lid = lib['id']
    touch(root, 'a.txt')
    touch(root, 'b.txt')
    routes.fs_rename({'library_id': lid, 'from': 'a.txt', 'name': 'b.txt'})
    result = routes.fs_rename({'library_id': lid, 'from': 'a.txt', 'name': 'b.txt', 'dry_run': False})
    assert result['moved'] == 0
    routes.fs_delete({'library_id': lid, 'paths': ['missing.txt'], 'dry_run': False})
    assert not routes.fs_changes(lid)['pending']
    routes.fs_mkdir({'library_id': lid, 'name': 'new'})
    assert routes.fs_changes(lid)['actions'] == {'mkdir': 1}
    store.init_db()
    assert routes.fs_changes(lid)['count'] == 1  # survives startup/reopened connections
    assert routes.fs_changes(lid)['library_name'] == lib['name']


def test_move_file_back_to_library_root(tv_files):
    lib, root = tv_files
    lid = lib['id']
    touch(root, 'folder/note.txt')
    result = routes.fs_move({'library_id': lid, 'from': 'folder/note.txt', 'to_dir': '', 'dry_run': False})
    assert result['moved'] == 1
    assert (root / 'note.txt').exists() and not (root / 'folder/note.txt').exists()
    assert routes.fs_changes(lid)['paths'] == ['note.txt']


def test_tv_copy_waits_for_scoped_scan_and_cancel_keeps_completed_changes(tv_files, monkeypatch):
    lib, root = tv_files
    lid = lib['id']
    touch(root, 'Show/Season 01/Show.S01E01.mkv')
    touch(root, 'Show/Season 01/Show.S01E02.mkv')
    scanner.scan_all(library_id=lid)
    registry = JobRegistry(prefix='copytest')
    monkeypatch.setattr(fs_copy, '_COPY_JOBS', registry)
    monkeypatch.setattr(scanner, 'scan_one', lambda *a, **k: pytest.fail('TV copy must not run movie scanner'))
    monkeypatch.setattr(scanner, 'scan_file', lambda *a, **k: pytest.fail('TV copy must not run movie scanner'))
    job = registry.create(library_id=lid, worker_finished=False)
    remote = storage.backend_for(lid).abs_path('') is None
    fn_name = '_copy_file_remote' if remote else '_copy_file'
    original = getattr(fs_copy, fn_name)

    def copy_then_cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        registry.cancel(job['job_id'])
        return result

    monkeypatch.setattr(fs_copy, fn_name, copy_then_cancel)
    worker = fs_copy._copy_worker_remote if remote else fs_copy._copy_worker
    worker(job['job_id'], ['Show/Season 01/Show.S01E01.mkv', 'Show/Season 01/Show.S01E02.mkv'],
           'Show/Copies', {}, lid)
    assert registry.get(job['job_id'])['state'] == 'cancelled'
    assert registry.get(job['job_id'])['worker_finished']
    assert (root / 'Show/Copies/Show.S01E01.mkv').exists()
    assert not (root / 'Show/Copies/Show.S01E02.mkv').exists()
    assert store.get_by_path('Show/Copies/Show.S01E01.mkv', lid) is None
    assert routes.fs_changes(lid)['pending']
    assert not routes.fs_changes(lid)['active_jobs']


def test_partial_db_failure_still_records_physical_change(tv_files, monkeypatch):
    lib, root = tv_files
    lid = lib['id']
    rel = 'Show/Season 01/Show.S01E01.mkv'
    touch(root, rel)
    scanner.scan_all(library_id=lid)
    monkeypatch.setattr(store, 'delete_episode_by_path', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('db unavailable')))
    result = routes.fs_delete({'library_id': lid, 'paths': [rel], 'dry_run': False, 'confirm': True})
    assert result['results'][0]['status'].startswith('error:')
    assert not (root / rel).exists()
    assert routes.fs_changes(lid)['pending']


def test_stale_target_record_cannot_replace_tv_identity(tv_files):
    lib, root = tv_files
    lid = lib['id']
    old = 'Show/Season 01/Show.S01E01.mkv'
    target = 'Show/Season 01/Renamed.S01E01.mkv'
    touch(root, old)
    scanner.scan_all(library_id=lid)
    ep = store.get_episode_by_path(old, lid)
    other = store.upsert_episode(ep['show_id'], lid, target, 1, 1)
    preview = routes.fs_rename({'library_id': lid, 'from': old, 'name': Path(target).name})
    assert preview['plans'][0]['status'] == 'conflict_db_occupied'
    result = routes.fs_rename({'library_id': lid, 'from': old, 'name': Path(target).name, 'dry_run': False})
    assert result['results'][0]['status'] == 'conflict_db_occupied'
    assert (root / old).exists() and not (root / target).exists()
    assert store.get_episode_by_path(old, lid)['id'] == ep['id']
    assert store.get_episode_by_path(target, lid)['id'] == other
    assert not routes.fs_changes(lid)['pending']


def test_tv_move_rolls_disk_back_when_db_update_fails(tv_files, monkeypatch):
    lib, root = tv_files
    lid = lib['id']
    old = 'Show/Season 01/Show.S01E01.mkv'
    target = 'Show/Season 01/Renamed.S01E01.mkv'
    touch(root, old)
    scanner.scan_all(library_id=lid)
    ep = store.get_episode_by_path(old, lid)
    store.save_progress(ep['id'], 123, 1000, kind='episode')

    def fail(*args, **kwargs):
        raise RuntimeError('cannot update DB')

    monkeypatch.setattr(store, 'move_tv_paths', fail)
    result = routes.fs_rename({'library_id': lid, 'from': old, 'name': Path(target).name, 'dry_run': False})
    assert result['results'][0]['status'].startswith('error:')
    assert (root / old).exists() and not (root / target).exists()
    assert store.get_episode_by_path(old, lid)['id'] == ep['id']
    assert store.get_progress(ep['id'], kind='episode')['position'] == 123


@pytest.fixture
def scan_fixture(tv_files, monkeypatch):
    lib, _root = tv_files
    registry = JobRegistry(prefix='scantest')
    monkeypatch.setattr(jobs, '_SCAN_JOBS', registry)
    monkeypatch.setattr(jobs, '_scan_tv_lib_ids', lambda *a, **k: [])
    return lib['id'], registry


def test_successful_scan_clears_only_start_revision(scan_fixture, monkeypatch):
    lid, registry = scan_fixture
    store.record_fs_change(lid, 'rename', 'before.mkv')

    def scan(**kwargs):
        store.record_fs_change(lid, 'copy', 'during.mkv')
        return [{'library_id': lid, 'status': 'tv_ok'}]

    monkeypatch.setattr(scanner, 'scan_all', scan)
    job = registry.create(library_id=lid)
    jobs._scan_worker(job['job_id'], library_id=lid)
    assert registry.get(job['job_id'])['state'] == 'done'
    assert routes.fs_changes(lid)['paths'] == ['during.mkv']
    assert registry.get(job['job_id'])['summary']['fs_changes']['cleared'] == {lid: 1}


@pytest.mark.parametrize('result', ['library_offline', 'scan_failed', 'error: broken', 'cancelled', 'raised'])
def test_failed_offline_cancelled_scan_keeps_changes(scan_fixture, monkeypatch, result):
    lid, registry = scan_fixture
    store.record_fs_change(lid, 'copy', 'pending.mkv')
    job = registry.create(library_id=lid)

    def scan(**kwargs):
        if result == 'cancelled':
            registry.cancel(job['job_id'])
            return []
        if result == 'raised':
            raise RuntimeError('scan failed')
        return [{'library_id': lid, 'status': result}]

    monkeypatch.setattr(scanner, 'scan_all', scan)
    jobs._scan_worker(job['job_id'], library_id=lid)
    assert routes.fs_changes(lid)['count'] == 1
    if result == 'cancelled':
        assert registry.get(job['job_id'])['state'] == 'cancelled'


def test_scan_waits_for_copy_worker_to_really_stop(scan_fixture, monkeypatch):
    lid, _registry = scan_fixture
    copies = JobRegistry(prefix='copytest')
    monkeypatch.setattr(fs_copy, '_COPY_JOBS', copies)
    job = copies.create(library_id=lid, worker_finished=False)
    copies.cancel(job['job_id'])
    assert routes.fs_changes(lid)['active_jobs'][0]['job_id'] == job['job_id']
    with pytest.raises(HTTPException) as exc:
        jobs.scan_start(jobs.ScanBody(library_id=lid))
    assert exc.value.status_code == 409
    copies.update(job['job_id'], worker_finished=True)
    assert routes.fs_changes(lid)['active_jobs'] == []


def test_change_during_scan_does_not_gc_moved_episode(tv_files):
    lib, root = tv_files
    lid = lib['id']
    old = 'Show/Season 01/Show.S01E01.mkv'
    new = 'Show/Season 01/Renamed.S01E01.mkv'
    touch(root, old)
    scanner.scan_all(library_id=lid)
    eid = store.get_episode_by_path(old, lid)['id']
    store.save_progress(eid, 321, 1000, kind='episode')

    def progress(done, total):
        if done == 0:
            result = routes.fs_rename({'library_id': lid, 'from': old, 'name': Path(new).name,
                                       'dry_run': False})
            assert result['moved'] == 1

    result = scanner.scan_all(library_id=lid, progress_cb=progress)
    assert any('files changed during scan' in r['status'] for r in result)
    assert store.get_episode_by_path(new, lid)['id'] == eid
    assert store.get_progress(eid, kind='episode')['position'] == 321
    scanner.scan_all(library_id=lid)
    assert store.get_episode_by_path(new, lid)['id'] == eid
    assert store.get_episode_by_path(old, lid) is None


def test_copy_failure_without_write_does_not_mark_change(tv_files, monkeypatch):
    lib, root = tv_files
    lid = lib['id']
    touch(root, 'source.txt')
    (root / 'destination').mkdir()
    storage.clear_meta_cache(lid)
    registry = JobRegistry(prefix='copytest')
    monkeypatch.setattr(fs_copy, '_COPY_JOBS', registry)
    remote = storage.backend_for(lid).abs_path('') is None

    def fail(*args, **kwargs):
        if remote:
            raise storage.StorageError('cannot read')
        raise OSError('cannot read')

    monkeypatch.setattr(fs_copy, '_copy_file_remote' if remote else '_copy_file', fail)
    job = registry.create(library_id=lid, worker_finished=False)
    (fs_copy._copy_worker_remote if remote else fs_copy._copy_worker)(
        job['job_id'], ['source.txt'], 'destination', {}, lid)
    assert registry.get(job['job_id'])['results'][0]['status'].startswith('error:')
    assert not routes.fs_changes(lid)['pending']


def test_failed_local_copy_retaining_partial_file_marks_change(tmp_path, monkeypatch):
    root = tmp_path / 'local'
    root.mkdir()
    lib = store.create_library(name=f'partial-{tmp_path.name}', kind='tv', path=str(root))
    lid = lib['id']
    library_paths.invalidate_cache()
    try:
        def partial(src, dst, *args):
            Path(dst).write_bytes(b'partial')
            raise OSError('disk full')

        monkeypatch.setattr(fs_copy, '_copy_file', partial)
        with pytest.raises(OSError):
            fs_copy._copy_file_tracked('source', str(root / 'partial.txt'), lid, None, None)
        assert routes.fs_changes(lid)['paths'] == ['partial.txt']
    finally:
        store.delete_media_library(lib['media_library_id'])
        library_paths.invalidate_cache()
