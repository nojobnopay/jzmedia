"""Queue changes must affect the real preview worker, not only its persisted list."""

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.jobkit import JobRegistry
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
        monkeypatch.setattr(pv, '_version_source', self.version_source)
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
        assert pv.preview_auto_status()['queued'] == 1
    assert [item['id'] for item in pv._auto_queue] == saved_ids() == [9]


def batch_item(vid, batch_id, library_id=1):
    return {'kind': 'movie', 'id': vid, 'library_id': library_id,
            'source': 'batch', 'immediate': True, 'batch_id': batch_id}


def immediate_batch(monkeypatch, ids, library_id=1):
    monkeypatch.setattr(pv.store, 'preview_items', lambda lid: [('movie', vid) for vid in ids] if lid == library_id else [])
    return pv.preview_batch(pv.PreviewBatchBody(library_id=library_id))


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
        with pytest.raises(HTTPException) as error:
            immediate_batch(monkeypatch, [1, 2])
        assert error.value.status_code == 503
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
        result = pv.preview_batch(pv.PreviewBatchBody(library_id=1))
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
    assert calls == [1, 1]
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
