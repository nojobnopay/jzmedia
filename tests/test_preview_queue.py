"""Queue changes must affect the real preview worker, not only its persisted list."""

import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.jobkit import JobRegistry
from app.main import app
from app.routers.stream import previews as pv


@pytest.fixture(autouse=True)
def isolate_preview_queue(monkeypatch, tmp_path):
    pv.shutdown_previews()
    monkeypatch.setattr(pv, '_jobs', JobRegistry())
    monkeypatch.setattr(pv, '_active_job', None)
    monkeypatch.setattr(pv, '_worker_thread', None)
    monkeypatch.setattr(pv, '_auto_thread', None)
    monkeypatch.setattr(pv, '_auto_queue', [])
    monkeypatch.setattr(pv, '_auto_job_id', None)
    monkeypatch.setattr(pv, '_queued_finished', None)
    monkeypatch.setattr(pv, '_queue_retry_at', 0.0)
    monkeypatch.setattr(pv, '_ready_queue_check', (None, 0.0))
    monkeypatch.setattr(pv, '_reconcile_thread', None)
    monkeypatch.setattr(pv, '_submission_store', None)
    monkeypatch.setattr(pv, '_sessions', {})
    monkeypatch.setattr(pv, '_root', lambda: tmp_path / 'previews')
    monkeypatch.delenv('PREVIEW_INTERVAL', raising=False)
    pv._stop.clear()
    pv._auto_wake.clear()
    yield
    pv.shutdown_previews()
    pv._stop.clear()


class ControlledGeneration:
    """Block each extraction while the production worker and queue APIs run."""

    def __init__(self, monkeypatch):
        self.started = {}
        self.releases = {}
        self.calls = []
        self.libraries = {}
        self.sources = {}
        self.lock = threading.Lock()
        self.finish_all = threading.Event()
        monkeypatch.setattr(pv.store, 'get_library', lambda lid: {
            'id': lid, 'source': 'local', 'effective_enabled': True,
        } if lid in (1, 2) else None)
        monkeypatch.setattr(pv.store, 'get_playable', self.playable)
        monkeypatch.setattr(pv.storage, 'backend_for_library', lambda library: SimpleNamespace(
            library_id=library['id']))
        monkeypatch.setattr(pv.store, 'get_preview_playables', lambda refs, library_id=None: [
            self.playable(kind, vid) for kind, vid in refs
            if library_id is None or self.playable(kind, vid)['library_id'] == library_id])
        monkeypatch.setattr(pv.store, 'get_library_preview_playables', lambda lid: [
            self.playable(kind, vid) for kind, vid in sorted(self.libraries.keys() | self.sources.keys())
            if self.playable(kind, vid)['library_id'] == lid])
        monkeypatch.setattr(pv.store, 'preview_catalogue_snapshot', lambda lid: tuple(
            (row['kind'], row['id'], row['library_id'], row['file_path'], None, None, None)
            for row in pv.store.get_library_preview_playables(lid)))
        monkeypatch.setattr(pv, '_version_source', self.version_source)
        monkeypatch.setattr(pv, 'preview_source', lambda row, **_kwargs:
                            pv._version_source(row['id'], row['kind'])[1], raising=False)
        monkeypatch.setattr(pv, 'verify_sources', lambda _sources, stopped=None:
                            not (stopped and stopped()), raising=False)
        monkeypatch.setattr(pv, '_media_cached_or_probe', lambda *_args: {
            'playable': True, 'duration': 30,
        })
        monkeypatch.setattr(pv, '_generate', self.generate)

    def playable(self, kind, vid):
        return {'id': vid, 'kind': kind, 'library_id': self.libraries.get((kind, vid), 1),
                'title': f'影片 {vid}', 'file_path': f'/isolated/{vid}.mkv'}

    def version_source(self, vid, kind):
        row = self.playable(kind, vid)
        src = self.sources.setdefault((kind, vid), SimpleNamespace(
            input=f'/isolated/{vid}.mkv', rel=f'{vid}.mkv', size=123, mtime=1,
            backend=SimpleNamespace(library_id=row['library_id'], root='/isolated')))
        return row, src

    def event(self, events, key):
        with self.lock:
            return events.setdefault(key, threading.Event())

    def generate(self, _jid, row, _src, _info, _interval):
        key = (row['kind'], row['id'])
        release = self.event(self.releases, key)
        with self.lock:
            self.calls.append(key)
        self.event(self.started, key).set()
        if not self.finish_all.is_set():
            assert release.wait(5), f'preview {key} was never released by test'

    def wait_for(self, vid, kind='movie'):
        assert self.event(self.started, (kind, vid)).wait(2), f'preview {vid} did not start'

    def release(self, vid, kind='movie'):
        self.event(self.releases, (kind, vid)).set()

    def unblock_all(self):
        self.finish_all.set()
        with self.lock:
            for release in self.releases.values():
                release.set()

    def finish(self):
        self.unblock_all()
        pv._worker_thread.join(timeout=2)
        assert not pv._worker_thread.is_alive()

    def enqueue(self, ids, library_id=1):
        entries = [{'kind': 'movie', 'id': vid, 'library_id': library_id} for vid in ids]
        self.libraries.update({('movie', vid): library_id for vid in ids})
        pv._enqueue_auto_items(entries)

    def start(self, ids, library_id=1):
        self.libraries.update({('movie', vid): library_id for vid in ids})
        return pv._start([('movie', vid) for vid in ids], library_id=library_id)


@pytest.fixture
def generation(monkeypatch):
    controlled = ControlledGeneration(monkeypatch)
    yield controlled
    controlled.unblock_all()
    pv.shutdown_previews()


def queue(library_id=1, offset=0, limit=50):
    pv._reconcile_ready_queue()
    return pv.preview_queue(library_id=library_id, offset=offset, limit=limit)


def queue_ids(view):
    return [item['id'] for item in view['items']]


def saved_ids():
    return [item['id'] for item in json.loads(pv._queue_path().read_text())]


def test_manual_worker_updates_pending_queue_and_retires_completed_entries(generation):
    generation.enqueue(range(1, 5))
    job = generation.start(range(1, 5))
    generation.wait_for(1)
    initial = queue()
    assert queue_ids(initial) == [2, 3, 4]
    assert initial['running']['id'] == 1

    generation.release(1)
    generation.wait_for(2)
    updated = queue()
    assert queue_ids(updated) == [3, 4]
    assert updated['total'] == 2
    assert updated['running']['id'] == 2
    assert updated['running']['done'] == 1
    assert updated['revision'] != initial['revision']
    assert 1 not in saved_ids()

    generation.finish()
    completed = pv._jobs.get(job['job_id'])
    assert completed['state'] == 'done'
    assert completed['done'] == completed['total'] == 4
    assert queue()['items'] == []
    assert queue()['running'] is None
    assert saved_ids() == []


def test_clearing_all_pending_at_fourth_movie_stops_after_current_across_pages(generation):
    generation.enqueue(range(1, 131))
    generation.enqueue([1001, 1002], library_id=2)
    job = generation.start(range(1, 131))
    for vid in range(1, 4):
        generation.wait_for(vid)
        generation.release(vid)
    generation.wait_for(4)

    first = queue(limit=50)
    later = queue(offset=100, limit=50)
    assert first['total'] == later['total'] == 126
    assert first['revision'] == later['revision']
    assert len(first['items']) == 50 and len(later['items']) == 26
    assert first['running']['id'] == 4
    result = pv.preview_queue_remove_many(pv.PreviewQueueRemoveBody(
        library_id=1, revision=first['revision'], all_pending=True))
    assert result == {'removed': 126}
    assert queue()['total'] == 0
    assert queue()['running']['id'] == 4
    assert queue_ids(queue(library_id=2)) == [1001, 1002]
    assert pv._jobs.get(job['job_id'])['total'] == 4
    assert saved_ids() == [4, 1001, 1002]

    generation.finish()
    completed = pv._jobs.get(job['job_id'])
    assert generation.calls == [('movie', vid) for vid in range(1, 5)]
    assert completed['state'] == 'done'
    assert completed['done'] == completed['total'] == 4
    assert saved_ids() == [1001, 1002]


def test_manual_only_pending_items_are_visible_and_single_removal_prevents_execution(generation):
    job = generation.start([1, 2, 3])
    generation.wait_for(1)
    before = queue()
    assert queue_ids(before) == [2, 3]
    assert before['total'] == 2

    removed = pv.preview_queue_remove('movie', 2, library_id=1)
    assert removed == {'removed': True, 'cancelled': False}
    assert queue_ids(queue()) == [3]
    assert pv._jobs.get(job['job_id'])['total'] == 2

    generation.release(1)
    generation.wait_for(3)
    generation.finish()
    completed = pv._jobs.get(job['job_id'])
    assert generation.calls == [('movie', 1), ('movie', 3)]
    assert completed['state'] == 'done'
    assert completed['done'] == completed['total'] == 2


def test_single_removal_updates_both_manual_and_persistent_queues_once(generation):
    generation.enqueue([1, 2, 3])
    generation.start([1, 2, 3])
    generation.wait_for(1)
    assert queue_ids(queue()) == [2, 3]
    assert pv.preview_queue_remove('movie', 2, library_id=1) == {
        'removed': True, 'cancelled': False,
    }
    assert queue_ids(queue()) == [3]
    assert saved_ids() == [1, 3]
    generation.finish()
    assert generation.calls == [('movie', 1), ('movie', 3)]
    assert saved_ids() == []


@pytest.mark.parametrize('bulk', [False, True], ids=['single', 'bulk'])
def test_failed_persistence_restores_manual_and_persistent_pending_queues(
        monkeypatch, generation, bulk):
    generation.enqueue([1, 2, 3])
    job = generation.start([1, 2, 3])
    generation.wait_for(1)
    before = queue()
    before_items = list(pv._jobs.get(job['job_id'])['items'])

    def fail_save():
        raise OSError('isolated disk is full')

    with monkeypatch.context() as patch:
        patch.setattr(pv, '_save_auto_queue', fail_save)
        with pytest.raises(HTTPException) as error:
            if bulk:
                pv.preview_queue_remove_many(pv.PreviewQueueRemoveBody(
                    library_id=1, revision=before['revision'], all_pending=True))
            else:
                pv.preview_queue_remove('movie', 2, library_id=1)
        assert error.value.status_code == 503

    restored = queue()
    assert queue_ids(restored) == [2, 3]
    assert restored['revision'] == before['revision']
    restored_job = pv._jobs.get(job['job_id'])
    assert restored_job['items'] == before_items
    assert restored_job['total'] == 3
    assert [item['id'] for item in pv._auto_queue] == saved_ids() == [1, 2, 3]

    generation.finish()
    assert generation.calls == [('movie', 1), ('movie', 2), ('movie', 3)]
    assert saved_ids() == []


def test_clear_before_worker_claims_first_item_prevents_all_generation(generation):
    generation.enqueue([1, 2, 3])
    # The caller owns the queue lock throughout startup and removal, so the real
    # worker cannot claim an item before the removal transaction completes.
    with pv._lock:
        job = generation.start([1, 2, 3])
        before = queue()
        assert queue_ids(before) == [1, 2, 3]
        assert before['running'] is None
        assert pv.preview_queue_remove_many(pv.PreviewQueueRemoveBody(
            library_id=1, revision=before['revision'], all_pending=True)) == {'removed': 3}
    generation.finish()
    assert generation.calls == []
    completed = pv._jobs.get(job['job_id'])
    assert completed['state'] == 'done'
    assert completed['done'] == completed['total'] == 0
    assert saved_ids() == []


def test_worker_progress_invalidates_bulk_removal_revision_and_protects_current(generation):
    generation.enqueue([1, 2, 3])
    generation.start([1, 2, 3])
    generation.wait_for(1)
    before = queue()
    generation.release(1)
    generation.wait_for(2)
    with pytest.raises(HTTPException) as stale:
        pv.preview_queue_remove_many(pv.PreviewQueueRemoveBody(
            library_id=1, revision=before['revision'], all_pending=True))
    assert stale.value.status_code == 409
    current = queue()
    assert queue_ids(current) == [3]
    assert current['running']['id'] == 2
    generation.finish()
    assert generation.calls == [('movie', 1), ('movie', 2), ('movie', 3)]


def ready_manifest(vid, *, owned):
    row, src = pv._version_source(vid, 'movie')
    directory = pv._root() / pv._cache_key(row, src, pv._interval(row))
    directory.mkdir(parents=True)
    (directory / 'page_0000.jpg').write_bytes(b'isolated preview page')
    manifest = {'version': pv.VERSION, 'state': 'ready', 'pages': ['page_0000.jpg'],
                'quality_revision': pv.QUALITY_REVISION, 'duration': 30, 'count': 3,
                'interval': pv._interval(row), 'width': pv.WIDTH, 'height': pv.HEIGHT,
                'columns': pv.COLUMNS, 'rows': pv.ROWS}
    if owned:
        manifest.update(item_kind='movie', item_id=vid)
    pv._write_manifest(directory, manifest)
    return directory


@pytest.mark.parametrize('owned', [False, True], ids=['legacy-manifest', 'owned-manifest'])
def test_historical_ready_entries_retire_without_probe_or_generation(
        monkeypatch, generation, owned):
    ready_manifest(9, owned=owned)
    generation.enqueue([9])
    monkeypatch.setattr(pv, '_media_cached_or_probe', lambda *_args: pytest.fail('must not probe'))
    monkeypatch.setattr(pv, '_generate', lambda *_args: pytest.fail('must not generate'))
    assert queue()['total'] == 0
    assert pv._auto_queue == []
    assert saved_ids() == []


@pytest.mark.parametrize('owned', [False, True], ids=['legacy-manifest', 'owned-manifest'])
@pytest.mark.parametrize('revision', ['mtime', 'interval'])
def test_changed_source_or_interval_does_not_retire_stale_ready_entry(
        monkeypatch, generation, owned, revision):
    ready_manifest(9, owned=owned)
    if revision == 'mtime':
        generation.sources[('movie', 9)].mtime += 1
    else:
        monkeypatch.setenv('PREVIEW_INTERVAL', '15')
    generation.enqueue([9])
    monkeypatch.setattr(pv, '_media_cached_or_probe', lambda *_args: pytest.fail('must not probe'))
    monkeypatch.setattr(pv, '_generate', lambda *_args: pytest.fail('must not generate'))
    assert queue_ids(queue()) == [9]
    assert saved_ids() == [9]


def test_automatic_worker_retires_each_success_without_skipping_next_item(
        monkeypatch, generation):
    monkeypatch.setattr(pv, '_auto_allowed_now', lambda: True)
    monkeypatch.setattr(pv, '_playback_busy', lambda: False)
    monkeypatch.setattr(pv.data_usage, 'usage', lambda **_kwargs: {
        'ok': True, 'over_limit': False, 'categories': {'previews': 0},
    })
    monkeypatch.setenv('PREVIEW_AUTO_MODE', 'all')
    generation.enqueue([1, 2])
    pv.start_auto_previews()
    generation.wait_for(1)
    assert queue_ids(queue()) == [2]
    assert queue()['running']['auto'] is True
    generation.release(1)
    generation.wait_for(2)
    assert queue()['total'] == 0
    assert queue()['running']['id'] == 2
    assert saved_ids() == [2]
    generation.finish()
    assert generation.calls == [('movie', 1), ('movie', 2)]
    assert saved_ids() == []


@pytest.mark.parametrize('endpoint', ['queue', 'auto-status'])
@pytest.mark.parametrize('ownership', [
    {'item_kind': 'movie', 'item_id': [9]},
    {'item_kind': 'movie', 'item_id': {'id': 9}},
    {'item_kind': ['movie'], 'item_id': 9},
    {'item_kind': {'kind': 'movie'}, 'item_id': 9},
    {'item_kind': 'movie', 'item_id': True},
    {'item_kind': 'movie', 'item_id': 0},
    {'item_kind': 'movie', 'item_id': -9},
    {'item_kind': 'movie', 'item_id': '9'},
    {'item_kind': 'movie', 'item_id': 9.0},
    {'item_kind': 'unknown', 'item_id': 9},
], ids=['id-list', 'id-object', 'kind-list', 'kind-object', 'id-bool',
        'id-zero', 'id-negative', 'id-string', 'id-float', 'kind-unknown'])
def test_malformed_manifest_ownership_does_not_break_queue_or_auto_status(
        monkeypatch, generation, endpoint, ownership):
    directory = ready_manifest(9, owned=False)
    manifest = json.loads((directory / 'manifest.json').read_text())
    manifest.update(ownership)
    pv._write_manifest(directory, manifest)
    generation.enqueue([9])
    monkeypatch.setattr(pv, '_media_cached_or_probe', lambda *_args: pytest.fail('must not probe'))
    monkeypatch.setattr(pv, '_generate', lambda *_args: pytest.fail('must not generate'))
    monkeypatch.setattr(pv.data_usage, 'usage', lambda **_kwargs: {
        'ok': True, 'over_limit': False, 'categories': {'previews': 0},
    })
    if endpoint == 'queue':
        assert queue_ids(queue()) == [9]
    else:
        pv._reconcile_ready_queue()
        assert pv.preview_auto_status()['queued'] == 1
    assert [item['id'] for item in pv._auto_queue] == saved_ids() == [9]


def batch_item(vid, batch_id, library_id=1):
    return {'kind': 'movie', 'id': vid, 'library_id': library_id,
            'source': 'batch', 'immediate': True, 'batch_id': batch_id}


def configure_batch(monkeypatch, ids, library_id=1):
    ids = list(ids)
    monkeypatch.setattr(pv.store, 'get_library_preview_playables', lambda lid: [
        {**pv.store.get_playable('movie', vid), 'library_id': library_id}
        for vid in ids] if lid == library_id else [])
    monkeypatch.setattr(pv.storage, 'backend_for_library', lambda library: SimpleNamespace(
        library_id=library['id']))
    monkeypatch.setattr(pv.storage, 'clear_meta_cache', lambda _lid: None)
    monkeypatch.setattr(pv.store, 'preview_catalogue_snapshot', lambda lid: tuple(
        (row['kind'], row['id'], row['library_id'], row['file_path'], None, None, None)
        for row in pv.store.get_library_preview_playables(lid)))
    monkeypatch.setattr(pv, 'preview_source', lambda row, **_kwargs:
                        pv._version_source(row['id'], row['kind'])[1], raising=False)


def submit_batch(monkeypatch, ids, library_id=1):
    configure_batch(monkeypatch, ids, library_id)
    return pv.preview_batch(pv.PreviewBatchBody(library_id=library_id))


def wait_submission(library_id=1):
    task = pv._submissions().tasks[library_id]
    task.thread.join(timeout=2)
    assert not task.thread.is_alive(), 'inventory did not finish after storage was released'
    return task.snapshot()


def immediate_batch(monkeypatch, ids, library_id=1):
    submitted = submit_batch(monkeypatch, ids, library_id)
    assert submitted['state'] == 'accepted'
    final = wait_submission(library_id)
    return {'state': 'queued' if final['state'] == 'done' else final['state'],
            **{name: final[name] for name in ('batch_id', 'total', 'added', 'promoted',
                                            'ready', 'already_queued', 'unavailable')}}


def test_batch_enqueues_only_missing_current_quality_and_promotes_idle_without_stealing_other_work(monkeypatch, generation):
    generation.start([1])
    generation.wait_for(1)
    generation.enqueue([2])
    retry = {'kind': 'movie', 'id': 3, 'library_id': 1, 'manual_retry': True}
    other = batch_item(4, 'b' * 32)
    pv._enqueue_auto_items([retry, other])
    ready_manifest(5, owned=True)
    # Complete old 160-pixel output remains available, but still needs the current quality.
    row, src = generation.version_source(6, 'movie')
    legacy = pv._root() / pv._cache_key(row, src, 10, quality_revision=2)
    legacy.mkdir()
    (legacy / 'page_0000.jpg').write_bytes(b'old preview')
    pv._write_manifest(legacy, {'version': pv.VERSION, 'state': 'ready', 'pages': ['page_0000.jpg']})
    resolve = generation.version_source
    def source(vid, kind):
        if vid == 7:
            raise HTTPException(410, 'offline')
        return resolve(vid, kind)
    monkeypatch.setattr(pv, '_version_source', source)
    result = immediate_batch(monkeypatch, range(1, 8))
    batch_id = result['batch_id']
    assert result == {'state': 'queued', 'batch_id': batch_id, 'total': 7,
                      'added': 1, 'promoted': 1, 'ready': 1, 'already_queued': 3, 'unavailable': 1}
    assert pv._auto_queue == [batch_item(2, batch_id), retry, other, batch_item(6, batch_id)]
    assert pv._jobs.get(pv._active_job)['state'] == 'running'
    assert queue()['batches'] == [
        {'batch_id': batch_id, 'pending': 2, 'running': 0, 'remaining': 2},
        {'batch_id': 'b' * 32, 'pending': 1, 'running': 0, 'remaining': 1}]
    duplicate = immediate_batch(monkeypatch, range(1, 8))
    assert duplicate['batch_id'] is None and duplicate['added'] == duplicate['promoted'] == 0
    assert duplicate['already_queued'] == 5
    assert saved_ids() == [2, 3, 4, 6]
    generation.finish()


def test_batch_enqueue_save_failure_restores_idle_policy_and_does_not_leave_a_batch(monkeypatch, generation):
    generation.enqueue([1])
    before = [dict(item) for item in pv._auto_queue]
    with monkeypatch.context() as patch:
        patch.setattr(pv, '_save_auto_queue', lambda: (_ for _ in ()).throw(OSError('disk full')))
        result = immediate_batch(monkeypatch, [1, 2])
        assert result['state'] == 'error'
        assert '失败' in pv._submissions().latest(1)['error']
    assert pv._auto_queue == before
    assert saved_ids() == [1]
    assert queue()['batches'] == []


@pytest.mark.parametrize('entrypoint', ['immediate', 'idle'])
@pytest.mark.parametrize('damage', ['metadata', 'truncated', 'empty', 'missing'])
def test_incomplete_ready_cache_is_queued_and_not_retired_by_status(
        monkeypatch, generation, entrypoint, damage):
    directory = ready_manifest(1, owned=True)
    manifest = json.loads((directory / 'manifest.json').read_text())
    if damage == 'metadata':
        manifest.pop('count')
    elif damage == 'truncated':
        manifest.update(duration=260, count=26)
    elif damage == 'empty':
        (directory / 'page_0000.jpg').write_bytes(b'')
    else:
        (directory / 'page_0000.jpg').unlink()
    pv._write_manifest(directory, manifest)
    monkeypatch.setattr(pv.store, 'preview_items', lambda _lid: [('movie', 1)])
    if entrypoint == 'immediate':
        result = immediate_batch(monkeypatch, [1])
    else:
        result = pv.preview_queue_existing(pv.PreviewQueueBody(library_id=1))
    assert result['ready'] == 0 and result['added'] == 1
    assert queue_ids(queue()) == [1]
    assert saved_ids() == [1]


@pytest.mark.parametrize('complete', [False, True])
def test_batch_rechecks_complete_cache_before_claiming_batch_membership(
        monkeypatch, generation, complete):
    check = pv._current_ready
    calls = []
    def ready_during_request(row, src, interval):
        calls.append(row['id'])
        if len(calls) == 2:
            directory = ready_manifest(row['id'], owned=True)
            if not complete:
                manifest = json.loads((directory / 'manifest.json').read_text())
                manifest.update(duration=260, count=26)
                pv._write_manifest(directory, manifest)
        return check(row, src, interval)
    monkeypatch.setattr(pv, '_current_ready', ready_during_request)
    result = immediate_batch(monkeypatch, [1])
    assert calls == ([1, 1] if complete else [1, 1, 1])
    assert result['ready'] == int(complete)
    assert result['added'] == int(not complete)
    assert [item['id'] for item in pv._auto_queue] == ([] if complete else [1])


def test_terminal_idle_job_cannot_retire_a_new_batch_membership(monkeypatch, generation):
    monkeypatch.setattr(pv, '_auto_pause_reason', lambda: '')
    monkeypatch.setattr(pv, '_auto_allowed_now', lambda: True)
    monkeypatch.setattr(pv, '_playback_busy', lambda: False)
    attempts = []
    def generate(_jid, row, _src, _info, _interval):
        attempts.append(row['id'])
        if len(attempts) == 1:
            raise RuntimeError('isolated initial extraction failure')
    monkeypatch.setattr(pv, '_generate', generate)
    generation.enqueue([1])
    pv._dispatch_queue_once()
    pv._worker_thread.join(timeout=2)
    assert not pv._worker_thread.is_alive()
    assert pv._jobs.get(pv._auto_job_id)['state'] == 'failed'

    # The failed idle request has not been retired by the dispatcher yet.
    batch = immediate_batch(monkeypatch, [1])
    assert batch['promoted'] == 1
    assert pv._auto_queue[0]['batch_id'] == batch['batch_id']
    pv._dispatch_queue_once()
    pv._worker_thread.join(timeout=2)
    assert not pv._worker_thread.is_alive()
    assert attempts == [1, 1]
    assert pv._jobs.get(pv._auto_job_id)['state'] == 'done'
    pv._dispatch_queue_once()
    assert saved_ids() == []


def test_queued_batch_does_not_preempt_current_idle_generation_and_current_cancel_keeps_the_rest(monkeypatch, generation):
    monkeypatch.setattr(pv, '_auto_pause_reason', lambda: '')
    monkeypatch.setattr(pv, '_auto_allowed_now', lambda: True)
    monkeypatch.setattr(pv, '_playback_busy', lambda: False)
    generation.enqueue([1])
    pv._dispatch_queue_once()
    generation.wait_for(1)
    original = pv._active_job
    result = immediate_batch(monkeypatch, [2, 3])
    assert pv._active_job == original and pv._jobs.get(original)['state'] == 'running'
    generation.release(1)
    pv._worker_thread.join(timeout=2)
    pv._dispatch_queue_once()
    generation.wait_for(2)
    view = queue()
    assert view['running']['batch_id'] == result['batch_id']
    assert view['running']['source'] == 'batch' and view['running']['auto'] is False
    assert view['batches'] == [{'batch_id': result['batch_id'], 'pending': 1, 'running': 1, 'remaining': 2}]
    assert pv.preview_queue_remove('movie', 2, library_id=1) == {'removed': True, 'cancelled': True}
    assert saved_ids() == [3]
    generation.release(2)
    pv._worker_thread.join(timeout=2)
    pv._dispatch_queue_once()
    generation.wait_for(3)
    assert queue()['batches'][0]['remaining'] == 1
    generation.finish()
    assert generation.calls == [('movie', 1), ('movie', 2), ('movie', 3)]
    assert saved_ids() == []


def test_cancel_batch_only_stops_its_current_and_pending_preserving_other_sources_and_libraries(generation):
    batch = 'a' * 32
    pv._enqueue_auto_items([batch_item(1, batch), batch_item(2, batch),
                           {'kind': 'movie', 'id': 3, 'library_id': 1, 'manual_retry': True},
                           batch_item(4, 'b' * 32), {'kind': 'movie', 'id': 5, 'library_id': 1},
                           batch_item(6, batch, library_id=2)])
    generation.libraries[('movie', 6)] = 2
    pv._dispatch_queue_once()
    generation.wait_for(1)
    assert queue(library_id=2)['batches'] == [{'batch_id': batch, 'pending': 1, 'running': 0, 'remaining': 1}]
    result = pv.preview_batch_cancel(batch, pv.PreviewBatchBody(library_id=1))
    assert result == {'removed': 2, 'cancelled': True}
    assert saved_ids() == [3, 4, 5, 6]
    assert pv._auto_queue[0]['manual_retry']
    generation.release(1)
    pv._worker_thread.join(timeout=2)
    pv._dispatch_queue_once()
    generation.wait_for(3)
    assert queue()['running']['source'] == 'retry'
    generation.finish()
    assert 2 not in [vid for _kind, vid in generation.calls]
    assert pv.preview_batch_cancel(batch, pv.PreviewBatchBody(library_id=1)) == {'removed': 0, 'cancelled': False}


def test_cancel_batch_save_failure_preserves_running_job_and_all_members(monkeypatch, generation):
    batch = 'a' * 32
    pv._enqueue_auto_items([batch_item(1, batch), batch_item(2, batch)])
    pv._dispatch_queue_once()
    generation.wait_for(1)
    jid = pv._active_job
    with monkeypatch.context() as patch:
        patch.setattr(pv, '_save_auto_queue', lambda: (_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(HTTPException) as error:
            pv.preview_batch_cancel(batch, pv.PreviewBatchBody(library_id=1))
        assert error.value.status_code == 503
    assert pv._jobs.get(jid)['state'] == 'running'
    assert saved_ids() == [1, 2]
    assert [item['id'] for item in pv._auto_queue] == [1, 2]
    generation.finish()


def test_cancel_batch_before_first_worker_claim_is_atomic(monkeypatch, generation):
    result = immediate_batch(monkeypatch, [1, 2])
    with pv._lock:
        pv._dispatch_queue_once()
        jid = pv._active_job
        assert queue()['running'] is None
        assert pv.preview_batch_cancel(result['batch_id'], pv.PreviewBatchBody(library_id=1)) == {'removed': 2, 'cancelled': True}
    pv._worker_thread.join(timeout=2)
    assert generation.calls == [] and saved_ids() == []
    assert pv._jobs.get(jid)['state'] == 'cancelled'


def test_restart_restores_batch_and_legacy_retries_with_auto_off_and_rejects_bad_metadata(monkeypatch, generation):
    result = immediate_batch(monkeypatch, [1, 2])
    pv._enqueue_auto_items([{'kind': 'movie', 'id': 3, 'library_id': 1, 'manual_retry': True},
                           {'kind': 'movie', 'id': 4, 'library_id': 1}])
    saved = json.loads(pv._queue_path().read_text())
    for invalid in ({'immediate': True}, {'source': 'batch', 'immediate': True, 'batch_id': 'bad'},
                    {'source': 'batch', 'batch_id': 'c' * 32},
                    {'source': 'batch', 'immediate': True, 'manual_retry': True, 'batch_id': 'c' * 32}):
        saved.append({'kind': 'movie', 'id': 90 + len(saved), 'library_id': 1, **invalid})
    pv._queue_path().write_text(json.dumps(saved))
    monkeypatch.setenv('PREVIEW_AUTO_MODE', 'off')
    monkeypatch.setattr(pv, '_auto_queue', [])
    # Load through the production restart hook, then drive dispatch explicitly.
    with monkeypatch.context() as patch:
        patch.setattr(pv.threading, 'Thread', lambda **_kwargs: SimpleNamespace(start=lambda: None, is_alive=lambda: False, join=lambda **_kw: None))
        pv.start_auto_previews()
    assert [item['id'] for item in pv._auto_queue] == [1, 2, 3, 4]
    assert pv._auto_queue[0]['batch_id'] == result['batch_id']
    pv._dispatch_queue_once()
    generation.wait_for(1)
    assert queue()['running']['source'] == 'batch' and not queue()['running']['auto']
    generation.finish()


def test_batch_failure_attempts_once_keeps_reason_and_continues_to_next(monkeypatch, generation):
    attempted = []
    def generate(_jid, row, *_args):
        attempted.append(row['id'])
        if row['id'] == 1:
            raise RuntimeError('片源不可播放')
    monkeypatch.setattr(pv, '_generate', generate)
    immediate_batch(monkeypatch, [1, 2])
    for _ in range(3):
        pv._dispatch_queue_once()
        if pv._worker_thread:
            pv._worker_thread.join(timeout=2)
    assert attempted == [1, 2] and saved_ids() == []
    assert pv._failure_ledger().snapshot()[('movie', 1)]['error_code'] == 'unplayable'
    assert queue()['batches'] == []


def test_direct_player_single_item_can_still_be_cancelled_from_current_row(generation):
    result = generation.start([1])
    generation.wait_for(1)
    assert pv.preview_queue_remove('movie', 1, library_id=1) == {'removed': True, 'cancelled': True}
    assert pv._jobs.get(result['job_id'])['state'] == 'cancelled'
    generation.finish()


def test_batch_http_returns_before_slow_source_and_duplicate_recovers_same_progress(monkeypatch, generation):
    configure_batch(monkeypatch, [1, 2, 3])
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = generation.version_source

    def slow_source(vid, kind):
        calls.append(vid)
        if vid == 2:
            entered.set()
            assert release.wait(3), 'test did not release the simulated remote stat'
        return original(vid, kind)

    monkeypatch.setattr(pv, '_version_source', slow_source)
    monkeypatch.setattr(pv, '_auto_pause_reason', lambda: '')
    client = TestClient(app)
    started = time.monotonic()
    try:
        response = client.post('/api/stream/previews/jobs', json={'library_id': 1})
        assert response.status_code == 202
        assert time.monotonic() - started < 1
        first = response.json()
        assert first['state'] == 'accepted'
        assert entered.wait(1)
        status = client.get('/api/stream/previews/submissions/latest', params={'library_id': 1})
        assert status.status_code == 200
        progress = status.json()['submission']
        assert progress['id'] == first['submission']['id']
        assert (progress['state'], progress['phase'], progress['checked'], progress['total']) == (
            'running', 'checking', 1, 3)
        duplicate = client.post('/api/stream/previews/jobs', json={'library_id': 1}).json()
        assert duplicate['submission']['id'] == progress['id']
        assert duplicate['submission']['checked'] == 1
        assert calls == [1, 2]
        assert pv._auto_queue == []
    finally:
        release.set()
    final = wait_submission()
    assert final['state'] == 'done' and final['added'] == 3
    recovered = client.get('/api/stream/previews/submissions/latest', params={'library_id': 1}).json()['submission']
    assert recovered['id'] == final['id'] and recovered['state'] == 'done'
    assert recovered['checked'] == recovered['total'] == 3
    compatible = client.get('/api/stream/previews/auto', params={'library_id': 1}).json()['submission']
    assert compatible['id'] == final['id'] and compatible['state'] == 'done'
    assert saved_ids() == [1, 2, 3]


def test_batch_ready_recheck_reports_queueing_without_holding_global_queue_lock(monkeypatch, generation):
    configure_batch(monkeypatch, [1])
    rechecking, release = threading.Event(), threading.Event()
    calls = []

    def check(row, _src, _interval):
        calls.append(row['id'])
        if len(calls) == 2:
            rechecking.set()
            assert release.wait(3), 'test did not release the completed-cache check'
        return None, None

    monkeypatch.setattr(pv, '_current_ready', check)
    try:
        pv.preview_batch(pv.PreviewBatchBody(library_id=1))
        assert rechecking.wait(1)
        assert pv._lock.acquire(timeout=0.5), 'cache I/O held the global queue lock'
        pv._lock.release()
        progress = pv._submissions().latest(1)
        assert progress['phase'] == 'queueing'
        assert progress['checked'] == progress['total'] == 1
        assert pv.preview_queue(library_id=1, offset=0, limit=50)['total'] == 0
    finally:
        release.set()
    assert wait_submission()['added'] == 1


def test_batch_large_inventory_reads_library_configuration_and_backend_once_per_phase(monkeypatch, generation):
    configure_batch(monkeypatch, range(1, 1201))
    library_reads, backends, cleared = [], [], []
    original = pv.store.get_library

    def library(library_id):
        library_reads.append(library_id)
        return original(library_id)

    def backend(row):
        backends.append(row['id'])
        return SimpleNamespace(library_id=row['id'])

    monkeypatch.setattr(pv.store, 'get_library', library)
    monkeypatch.setattr(pv.storage, 'backend_for_library', backend)
    monkeypatch.setattr(pv.storage, 'clear_meta_cache', cleared.append)
    monkeypatch.setattr(pv, '_current_ready', lambda *_args: (None, None))
    pv.preview_batch(pv.PreviewBatchBody(library_id=1))
    final = wait_submission()
    assert final['state'] == 'done' and final['added'] == 1200
    # Request validation, collection, pre-verification, and atomic commit; this
    # stays constant even for 1200 items rather than reading once per item.
    assert library_reads == [1, 1, 1, 1]
    assert backends == cleared == [1]


def test_batch_initial_submission_save_failure_returns_503_without_starting_inventory(monkeypatch, generation):
    registry = pv._submissions()

    def disk_full():
        raise OSError('isolated disk is full')

    def unexpected(_library_id):
        pytest.fail('inventory must not run before its initial state is durable')

    monkeypatch.setattr(registry, 'save', disk_full)
    monkeypatch.setattr(pv.store, 'get_library_preview_playables', unexpected)
    with pytest.raises(HTTPException) as error:
        pv.preview_batch(pv.PreviewBatchBody(library_id=1))
    assert error.value.status_code == 503
    assert registry.latest(1) is None
    assert pv._auto_queue == []


def test_restarted_inventory_reports_interruption_and_preserves_saved_extraction_queue(monkeypatch, generation):
    generation.enqueue([9])
    interrupted = dict(id='d' * 32, library_id=1, state='running', phase='checking',
                       total=5000, checked=700, added=0, promoted=0, ready=699,
                       already_queued=0, unavailable=1, batch_id=None, error='')
    (pv._root().parent / f'.{pv._root().name}-submissions.json').write_text(json.dumps([interrupted]))
    monkeypatch.setattr(pv, '_schedule_ready_queue_reconcile', lambda: None)
    monkeypatch.setattr(pv, '_auto_pause_reason', lambda: '')
    status = pv.preview_auto_status(library_id=1)['submission']
    assert status['id'] == interrupted['id'] and status['state'] == 'error'
    assert status['checked'] == 700 and status['total'] == 5000
    assert '重启' in status['error'] and '中断' in status['error']
    assert saved_ids() == [9]
    assert pv._submissions().tasks[1].thread is None


def test_shutdown_cannot_late_enqueue_after_a_blocked_source_returns(monkeypatch, generation):
    configure_batch(monkeypatch, [1])
    entered, release = threading.Event(), threading.Event()
    original = generation.version_source

    def slow_source(vid, kind):
        entered.set()
        assert release.wait(3), 'test did not release the simulated remote stat'
        return original(vid, kind)

    monkeypatch.setattr(pv, '_version_source', slow_source)
    pv.preview_batch(pv.PreviewBatchBody(library_id=1))
    registry = pv._submissions()
    assert entered.wait(1)
    task = registry.tasks[1]
    stopper = threading.Thread(target=registry.shutdown)
    stopper.start()
    try:
        assert task.stop.wait(1)
        assert registry.latest(1)['state'] == 'cancelled'
    finally:
        release.set()
        stopper.join(timeout=2)
    assert not stopper.is_alive()
    assert wait_submission()['state'] == 'cancelled'
    assert pv._auto_queue == []


def test_batch_does_not_commit_when_library_is_removed_during_inventory(monkeypatch, generation):
    configure_batch(monkeypatch, [1])
    entered, release = threading.Event(), threading.Event()
    original = generation.version_source
    exists = [True]
    library = pv.store.get_library
    monkeypatch.setattr(pv.store, 'get_library', lambda lid: library(lid) if exists[0] else None)

    def slow_source(vid, kind):
        entered.set()
        assert release.wait(3), 'test did not release the simulated remote stat'
        return original(vid, kind)

    monkeypatch.setattr(pv, '_version_source', slow_source)
    pv.preview_batch(pv.PreviewBatchBody(library_id=1))
    try:
        assert entered.wait(1)
        exists[0] = False
    finally:
        release.set()
    assert wait_submission()['state'] == 'error'
    assert pv._auto_queue == []


def test_ready_reconciliation_checks_only_queued_items_without_enumerating_all_manifests(monkeypatch, generation):
    ready_manifest(9, owned=True)
    ready_manifest(700, owned=True)
    generation.enqueue([9, 10])
    calls = []
    original = generation.version_source

    def source(vid, kind):
        calls.append((kind, vid))
        return original(vid, kind)

    def unexpected_glob(*_args, **_kwargs):
        pytest.fail('queue reconciliation must not enumerate the whole completed cache')

    monkeypatch.setattr(pv, '_version_source', source)
    monkeypatch.setattr(Path, 'glob', unexpected_glob)
    pv._reconcile_ready_queue()
    assert calls == [('movie', 9), ('movie', 10)]
    assert saved_ids() == [10]


def test_submission_progress_remains_fast_under_queue_lock_and_skips_usage_and_reconciliation(monkeypatch, generation):
    submit_batch(monkeypatch, [1])
    final = wait_submission()
    held, release = threading.Event(), threading.Event()

    def unexpected(*_args, **_kwargs):
        pytest.fail('lightweight submission progress must not reconcile or calculate usage')

    def hold_queue_lock():
        with pv._lock:
            held.set()
            release.wait(3)

    monkeypatch.setattr(pv.data_usage, 'usage', unexpected)
    monkeypatch.setattr(pv, '_schedule_ready_queue_reconcile', unexpected)
    holder = threading.Thread(target=hold_queue_lock)
    holder.start()
    try:
        assert held.wait(1)
        started = time.monotonic()
        response = TestClient(app).get('/api/stream/previews/submissions/latest', params={'library_id': 1})
        assert time.monotonic() - started < 1
        assert response.status_code == 200
        progress = response.json()['submission']
        assert progress['id'] == final['id'] and progress['state'] == 'done'
        assert progress['checked'] == progress['total'] == 1
        assert not release.is_set()
    finally:
        release.set()
        holder.join(timeout=1)
    assert not holder.is_alive()


@pytest.mark.parametrize('changed_field', ['smb_host', 'smb_connect_host'])
def test_batch_does_not_commit_after_remote_library_connection_changes(monkeypatch, generation, changed_field):
    configure_batch(monkeypatch, [1])
    entered, release = threading.Event(), threading.Event()
    original_source = generation.version_source
    original_library = pv.store.get_library
    connection = {'source': 'smb', 'smb_host': '192.0.2.1', 'smb_connect_host': '192.0.2.2'}
    monkeypatch.setattr(pv.store, 'get_library', lambda lid: {**original_library(lid), **connection})

    def slow_source(vid, kind):
        entered.set()
        assert release.wait(3), 'test did not release the simulated remote stat'
        return original_source(vid, kind)

    monkeypatch.setattr(pv, '_version_source', slow_source)
    pv.preview_batch(pv.PreviewBatchBody(library_id=1))
    try:
        assert entered.wait(1)
        connection[changed_field] = '192.0.2.3'
    finally:
        release.set()
    result = wait_submission()
    assert result['state'] == 'error'
    assert result['batch_id'] is None
    assert pv._auto_queue == []
