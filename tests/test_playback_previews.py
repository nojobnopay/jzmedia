import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import media, store
from app.jobkit import JobRegistry
from app.routers.stream import previews as pv


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    pv.shutdown_previews()
    monkeypatch.setattr(pv, '_jobs', JobRegistry())
    monkeypatch.setattr(pv, '_active_job', None)
    monkeypatch.setattr(pv, '_worker_thread', None)
    monkeypatch.setattr(pv, '_auto_thread', None)
    monkeypatch.setattr(pv, '_auto_queue', [])
    monkeypatch.setattr(pv, '_auto_job_id', None)
    monkeypatch.setattr(pv, '_queued_finished', None)
    monkeypatch.setattr(pv, '_queue_retry_at', 0.0)
    monkeypatch.setattr(pv, '_failure_store', None)
    monkeypatch.setattr(pv, '_root', lambda: tmp_path / 'previews')
    monkeypatch.setenv('TRANSCODER', 'sw')
    monkeypatch.delenv('PREVIEW_MODE', raising=False)
    monkeypatch.delenv('PREVIEW_THREADS', raising=False)
    pv._stop.clear()
    pv._auto_wake.clear()
    yield
    pv.shutdown_previews()
    pv._stop.clear()


def source(path='film.mkv', mtime=1):
    return SimpleNamespace(input=str(path), rel='film.mkv', size=123, mtime=mtime,
                           backend=SimpleNamespace(library_id=1, root='/test'))


def row(kind='movie'):
    return dict(id=7, kind=kind, library_id=1)


def test_cache_identity_includes_kind_file_revision_and_interval():
    key = pv._cache_key(row(), source(), 10)
    assert key != pv._cache_key(row('episode'), source(), 10)
    assert key != pv._cache_key(row('extra'), source(), 10)
    assert key != pv._cache_key(row(), source(mtime=2), 10)
    assert key != pv._cache_key(row(), source(), 20)


def cached_preview(*, quality_revision=1, state='ready'):
    key = pv._cache_key(row(), source(), 10, quality_revision=quality_revision)
    directory = pv._root() / key
    directory.mkdir(parents=True)
    for page in range(2):
        (directory / f'page_{page:04d}.jpg').write_bytes(f'quality-{quality_revision}-{page}'.encode())
    data = dict(version=pv.VERSION, interval=10, duration=265, width=pv.WIDTH,
                height=pv.HEIGHT, columns=pv.COLUMNS, rows=pv.ROWS, count=27,
                pages=['page_0000.jpg', 'page_0001.jpg'], state=state,
                item_kind='movie', item_id=7)
    if quality_revision != 1:
        data['quality_revision'] = quality_revision
    pv._write_manifest(directory, data)
    return key, directory


def test_quality_upgrade_has_new_cache_url_and_preserves_original_identity():
    # 已部署的第一代 key 固定下来，避免升级后旧完整成品失联。
    assert pv._cache_key(row(), source(), 10, quality_revision=1) == '8f1116440320183d0e21f28203c9f69f'
    assert pv._cache_key(row(), source(), 10) != pv._cache_key(row(), source(), 10, quality_revision=1)


def test_quality_upgrade_keeps_legacy_ready_until_new_generation_resumes_and_finishes(monkeypatch):
    monkeypatch.setattr(pv.store, 'get_library', lambda _lid: {'source': 'local'})
    monkeypatch.setattr(pv, '_version_source', lambda *_args: (row(), source()))
    monkeypatch.setattr(pv, '_start', lambda *_args: pytest.fail('GET must not generate'))
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: 'ffmpeg')
    legacy_key, legacy_directory = cached_preview()
    legacy_manifest = (legacy_directory / 'manifest.json').read_bytes()
    old = pv.preview_manifest(7)
    assert old['state'] == 'ready' and old['key'] == legacy_key
    assert all(f'/{legacy_key}/' in url for url in old['pages'])

    calls, interrupted = [], True
    def page(_self, start, _size, _columns, _rows, output):
        if start == 250 and interrupted:
            raise pv._Cancelled()
        calls.append(start)
        output.write_bytes(f'new-{start}'.encode())
    monkeypatch.setattr(pv.Extractor, 'page', page)
    jid = pv._jobs.create()['job_id']
    with pytest.raises(pv._Cancelled):
        pv._generate(jid, row(), source(), {'duration': 265}, 10)
    assert calls == [0]  # 旧 ready 不能使新版生成短路。
    current_key = pv._cache_key(row(), source(), 10)
    partial = pv._read_manifest(pv._root() / current_key)
    assert partial['quality_revision'] == pv.QUALITY_REVISION
    assert partial['pages'] == ['page_0000.jpg'] and partial['state'] == 'partial'
    assert (pv._root() / current_key / 'page_0000.jpg').read_bytes() == b'new-0'
    visible = pv.preview_manifest(7)
    assert visible['key'] == legacy_key and visible['pages'] == old['pages']
    assert (legacy_directory / 'page_0000.jpg').read_bytes() == b'quality-1-0'
    assert (legacy_directory / 'manifest.json').read_bytes() == legacy_manifest

    interrupted = False
    calls.clear()
    pv._generate(jid, row(), source(), {'duration': 265}, 10)
    assert calls == [250]  # 只恢复新版已完成页；不混用旧版第二页。
    current = pv.preview_manifest(7)
    assert current['state'] == 'ready' and current['key'] == current_key
    assert current['quality_revision'] == pv.QUALITY_REVISION
    assert all(f'/{current_key}/' in url for url in current['pages'])
    assert (pv._root() / current_key / 'page_0001.jpg').read_bytes() == b'new-250'
    assert not legacy_directory.exists()
    calls.clear()
    pv._generate(jid, row(), source(), {'duration': 265}, 10)
    assert calls == []


def test_ready_quality_cache_removes_only_matching_older_quality(monkeypatch):
    _, legacy_directory = cached_preview()
    _, current_directory = cached_preview(quality_revision=pv.QUALITY_REVISION)
    current_manifest = (current_directory / 'manifest.json').read_bytes()
    others = []
    for other_row, other_source, other_interval in [
        ({**row(), 'id': 8}, source(), 10),
        (row('episode'), source(), 10),
        (row(), source(mtime=2), 10),
        (row(), source(), 20),
    ]:
        key = pv._cache_key(other_row, other_source, other_interval, quality_revision=1)
        directory = pv._root() / key
        directory.mkdir()
        (directory / 'page_0000.jpg').write_bytes(b'unrelated')
        others.append(directory)
    invalidated = []
    monkeypatch.setattr(pv.data_usage, 'invalidate', lambda: invalidated.append(True))
    monkeypatch.setattr(pv.Extractor, 'page', lambda *_a: pytest.fail('ready must not decode'))

    pv._generate(None, row(), source(), {'duration': 265}, 10)

    assert not legacy_directory.exists()
    assert (current_directory / 'manifest.json').read_bytes() == current_manifest
    assert (current_directory / 'page_0000.jpg').read_bytes() == f'quality-{pv.QUALITY_REVISION}-0'.encode()
    assert all((directory / 'page_0000.jpg').read_bytes() == b'unrelated' for directory in others)
    assert invalidated


@pytest.mark.parametrize('change', ['missing_page', 'duplicate_page', 'count', 'quality_revision'])
def test_incomplete_ready_quality_cache_keeps_legacy(change):
    _, legacy_directory = cached_preview()
    _, directory = cached_preview(quality_revision=pv.QUALITY_REVISION)
    data = pv._read_manifest(directory)
    if change == 'missing_page':
        data['pages'].pop()
    elif change == 'duplicate_page':
        data['pages'][1] = data['pages'][0]
    elif change == 'count':
        data['count'] -= 1
    else:
        data['quality_revision'] -= 1
    pv._write_manifest(directory, data)

    pv._generate(None, row(), source(), {'duration': 265}, 10)

    assert (legacy_directory / 'page_0000.jpg').read_bytes() == b'quality-1-0'


def test_old_quality_cleanup_failure_keeps_new_cache_ready(monkeypatch, caplog):
    _, legacy_directory = cached_preview()
    _, current_directory = cached_preview(quality_revision=pv.QUALITY_REVISION)
    def fail_remove(_path):
        raise PermissionError('test cleanup denied')
    monkeypatch.setattr(pv.shutil, 'rmtree', fail_remove)

    pv._generate(None, row(), source(), {'duration': 265}, 10)

    assert legacy_directory.exists()
    assert pv._read_manifest(current_directory)['state'] == 'ready'
    assert 'remove old preview quality cache failed' in caplog.text


@pytest.mark.parametrize('change', ['file_revision', 'interval', 'kind'])
def test_legacy_ready_fallback_matches_current_source_and_sampling(monkeypatch, change):
    monkeypatch.setattr(pv.store, 'get_library', lambda _lid: {'source': 'local'})
    cached_preview()
    kind = 'episode' if change == 'kind' else 'movie'
    current_source = source(mtime=2) if change == 'file_revision' else source()
    monkeypatch.setattr(pv, '_version_source', lambda *_args: (row(kind), current_source))
    monkeypatch.setattr(pv, '_start', lambda *_args: pytest.fail('GET must not generate'))
    if change == 'interval':
        monkeypatch.setenv('PREVIEW_INTERVAL', '20')
    result = pv.preview_manifest(7, kind=kind)
    assert result['state'] == 'missing' and result['pages'] == []


def test_partial_legacy_cache_is_not_displayed_as_completed_quality_fallback(monkeypatch):
    monkeypatch.setattr(pv.store, 'get_library', lambda _lid: {'source': 'local'})
    monkeypatch.setattr(pv, '_version_source', lambda *_args: (row(), source()))
    cached_preview(state='partial')
    result = pv.preview_manifest(7)
    assert result['state'] == 'missing' and result['pages'] == []


def test_legacy_ready_joins_quality_upgrade_queue_and_is_retired_only_after_current_ready(monkeypatch):
    monkeypatch.setenv('PREVIEW_AUTO_MODE', 'all')
    monkeypatch.setattr(pv.store, 'get_library', lambda _lid: {
        'id': 1, 'source': 'local', 'effective_enabled': True})
    monkeypatch.setattr(pv.store, 'preview_items', lambda _lid: [('movie', 7)])
    monkeypatch.setattr(pv, '_version_source', lambda *_args: (row(), source()))
    monkeypatch.setattr(pv, '_ready_queue_check', (None, 0.0))
    cached_preview()
    result = pv.preview_queue_existing(pv.PreviewQueueBody(library_id=1))
    assert result['added'] == 1 and result['ready'] == 0
    pv._reconcile_ready_queue()
    assert pv._auto_queue == [{'kind': 'movie', 'id': 7, 'library_id': 1}]
    assert json.loads(pv._queue_path().read_text()) == pv._auto_queue

    cached_preview(quality_revision=pv.QUALITY_REVISION)
    monkeypatch.setattr(pv, '_ready_queue_check', (None, 0.0))
    pv._reconcile_ready_queue()
    assert pv._auto_queue == []
    assert json.loads(pv._queue_path().read_text()) == []


def test_manifest_missing_never_starts_job(monkeypatch):
    monkeypatch.setattr(pv, '_version_source', lambda *a: (row(), source()))
    monkeypatch.setattr(pv, '_start', lambda *a: pytest.fail('GET must not generate'))
    assert pv.preview_manifest(7)['state'] == 'missing'


def test_manifest_reports_current_movie_progress_and_ready_state(monkeypatch):
    monkeypatch.setattr(pv, '_version_source', lambda *a: (row(), source()))
    jid = pv._jobs.create(items=[('movie', 7)], total=1)['job_id']
    pv._active_job = jid
    pv._jobs.update(jid, current_kind='movie', current_id=7, frames=37, frame_total=100)
    running = pv.preview_manifest(7)
    assert (running['state'], running['frames'], running['frame_total']) == ('running', 37, 100)
    key = pv._cache_key(row(), source(), pv._interval(row()))
    directory = pv._root() / key
    directory.mkdir(parents=True)
    pv._write_manifest(directory, dict(version=pv.VERSION, state='ready', pages=[]))
    ready = pv.preview_manifest(7)
    assert ready['state'] == 'ready'
    assert 'frames' not in ready


def test_cancelled_generation_resumes_completed_pages(monkeypatch):
    calls = []
    stop_at_second_page = True
    def page(_self, start, _size, _columns, _rows, output):
        if start == 250 and stop_at_second_page:
            raise pv._Cancelled()
        calls.append(start)
        output.write_bytes(b'jpeg')
    monkeypatch.setattr(pv.Extractor, 'page', page)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: 'ffmpeg')
    jid = pv._jobs.create()['job_id']
    with pytest.raises(pv._Cancelled):
        pv._generate(jid, row(), source(), {'duration': 265}, 10)
    key = pv._cache_key(row(), source(), 10)
    partial = pv._read_manifest(pv._root() / key)
    assert partial['state'] == 'partial'
    assert partial['pages'] == ['page_0000.jpg']
    stop_at_second_page = False
    calls.clear()
    pv._generate(jid, row(), source(), {'duration': 265}, 10)
    assert calls == [250]
    assert pv._read_manifest(pv._root() / key)['state'] == 'ready'
    calls.clear()
    pv._generate(jid, row(), source(), {'duration': 265}, 10)
    assert calls == []


def test_cancel_terminates_and_reaps_running_process(monkeypatch):
    jid = pv._jobs.create()['job_id']
    processes = []
    popen = subprocess.Popen
    def capture(*a, **kw):
        proc = popen(*a, **kw)
        processes.append(proc)
        return proc
    monkeypatch.setattr(pv.subprocess, 'Popen', capture)
    timer = threading.Timer(0.4, lambda: pv._jobs.cancel(jid))
    timer.start()
    try:
        with pytest.raises(pv._Cancelled):
            pv._run([sys.executable, '-c', 'import time; time.sleep(60)'], jid)
        assert processes and processes[0].poll() is not None
    finally:
        timer.cancel()


def test_image_route_rejects_paths_and_serves_only_jpeg(tmp_path):
    for key, name in [('../secret', 'page_0000.jpg'), ('a' * 32, '../secret'), ('a' * 32, 'manifest.json')]:
        with pytest.raises(HTTPException) as e:
            pv.preview_image(key, name)
        assert e.value.status_code == 404
    directory = pv._root() / ('a' * 32)
    directory.mkdir(parents=True)
    (directory / 'page_0000.jpg').write_bytes(b'jpeg')
    response = pv.preview_image('a' * 32, 'page_0000.jpg')
    assert response.media_type == 'image/jpeg'
    assert 'immutable' in response.headers['cache-control']


def test_cache_eviction_preserves_recent_and_active(monkeypatch):
    monkeypatch.setenv('PREVIEW_CACHE_GB', '0.1')
    for key, old in [('a' * 32, True), ('b' * 32, False), ('c' * 32, True)]:
        d = pv._root() / key
        d.mkdir(parents=True)
        with (d / 'page_0000.jpg').open('wb') as f:
            f.truncate(60 * 1024 * 1024)
        if old:
            pv.os.utime(d, (time.time() - 1000,) * 2)
    pv._prune_cache(protect='c' * 32)
    assert not (pv._root() / ('a' * 32)).exists()
    assert (pv._root() / ('b' * 32)).exists()
    assert (pv._root() / ('c' * 32)).exists()


def test_library_scope_and_unknown_library(tmp_path):
    (tmp_path / 'media').mkdir()
    lib = store.create_library('preview scope', path=str(tmp_path / 'media'))
    mid = store.upsert_movie_by_path('preview/film.mkv', library_id=lib['id'])
    other = store.upsert_movie_by_path('preview/other.mkv')
    try:
        assert ('movie', mid) in store.preview_items(lib['id'])
        assert ('movie', other) not in store.preview_items(lib['id'])
        with pytest.raises(HTTPException) as e:
            pv.preview_batch(pv.PreviewBatchBody(library_id=99999999))
        assert e.value.status_code == 404
    finally:
        store.delete_media_library(lib['media_library_id'])
        store.delete_movie(other)


def test_hdr_and_dolby_vision_filter_policy():
    assert 'tonemap=' in pv._frame_filter({'hdr': 'hdr10'})
    assert 'tonemap=' not in pv._frame_filter({'dv_profile': 8, 'dv_bl_compat': 2, 'hdr': 'hdr10'})
    with pytest.raises(RuntimeError, match='Dolby Vision'):
        pv._frame_filter({'dv_profile': 5})


def test_real_ffmpeg_preview_timeline_and_partial_page(tmp_path):
    bins = media._static_bins_if_present()
    ff = pv.shutil.which('ffmpeg') or bins.get('ffmpeg')
    if not ff:
        pytest.skip('ffmpeg unavailable')
    video = tmp_path / 'colors.mkv'
    cmd = [ff, '-hide_banner', '-loglevel', 'error', '-y']
    for color in ['red', 'lime', 'blue']:
        cmd += ['-f', 'lavfi', '-i', f'color={color}:s=160x90:r=10:d=1']
    cmd += ['-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0', '-c:v', 'ffv1', str(video)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=20)
    src = source(video)
    jid = pv._jobs.create()['job_id']
    pv._generate(jid, row(), src, {'duration': 3}, 1)
    directory = pv._root() / pv._cache_key(row(), src, 1)
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert manifest['state'] == 'ready' and manifest['count'] == 3
    for index in range(3):
        result = subprocess.run([ff, '-loglevel', 'error', '-i', str(directory / 'page_0000.jpg'),
                                 '-vf', f'crop=2:2:{index * 160 + 80}:44,format=rgb24',
                                 '-frames:v', '1', '-f', 'rawvideo', '-'], capture_output=True, check=True, timeout=10)
        rgb = result.stdout[:3]
        assert rgb[index] > 200 and max(v for i, v in enumerate(rgb) if i != index) < 60


def test_concurrent_requests_deduplicate_and_do_not_spawn_second_worker(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(pv, '_worker', lambda *args: release.wait(3))
    try:
        first = pv._start([('movie', 1)])
        assert pv._start([('movie', 1)])['job_id'] == first['job_id']
        with pytest.raises(HTTPException) as e:
            pv._start([('movie', 2)])
        assert e.value.status_code == 409
    finally:
        release.set()
        pv._worker_thread.join(timeout=2)


def test_batch_waits_for_online_transcoding_and_can_cancel(monkeypatch):
    monkeypatch.setattr(pv, '_sessions', {'busy': {'proc': SimpleNamespace(poll=lambda: None)}})
    jid = pv._jobs.create(library_id=1)['job_id']
    timer = threading.Timer(0.2, lambda: pv._jobs.cancel(jid))
    timer.start()
    try:
        with pytest.raises(pv._Cancelled):
            pv._wait_for_transcodes(jid)
        assert pv._jobs.get(jid)['waiting'] is True
    finally:
        timer.cancel()


def test_scan_only_queues_new_items_and_persists_them(monkeypatch, tmp_path):
    monkeypatch.setenv('PREVIEW_AUTO_MODE', 'all')
    (tmp_path / 'media').mkdir()
    lib = store.create_library('preview automatic', path=str(tmp_path / 'media'))
    existing = store.upsert_movie_by_path('preview/existing.mkv', library_id=lib['id'])
    try:
        before = pv.auto_scan_snapshot([store.get_library(lib['id'])])
        new = store.upsert_movie_by_path('preview/new.mkv', library_id=lib['id'])
        assert pv.enqueue_scan_previews(before) == 1
        assert pv.enqueue_scan_previews(before) == 0
        saved = json.loads((pv._root() / 'auto-queue.json').read_text())
        assert saved == [{'kind': 'movie', 'id': new, 'library_id': lib['id']}]
        assert saved[0]['id'] != existing
    finally:
        store.delete_media_library(lib['media_library_id'])


def test_manual_generation_preempts_background_job(monkeypatch):
    def worker(jid, _items, _batch):
        while pv._jobs.get(jid)['state'] == 'running':
            time.sleep(0.01)
    monkeypatch.setattr(pv, '_worker', worker)
    background = pv._start([('movie', 7)], auto=True)
    manual = pv._start([('movie', 8)])
    try:
        assert pv._jobs.get(background['job_id'])['state'] == 'cancelled'
        assert manual['auto'] is False
        assert manual['job_id'] != background['job_id']
    finally:
        pv._jobs.cancel(manual['job_id'])
        pv._worker_thread.join(timeout=2)


def test_preview_cleanup_requires_confirmation_and_preserves_queue():
    directory = pv._root() / ('a' * 32)
    directory.mkdir(parents=True)
    (directory / 'page_0000.jpg').write_bytes(b'jpeg')
    (pv._root() / 'auto-queue.json').write_text('[]')
    check = pv.preview_cache_clean(pv.PreviewCacheCleanBody(dry_run=True))
    assert check['candidate_bytes'] == 4 and directory.is_dir()
    result = pv.preview_cache_clean(pv.PreviewCacheCleanBody(dry_run=False))
    assert result['freed'] == 4 and not directory.exists()
    assert (pv._root() / 'auto-queue.json').is_file()


def test_automatic_queue_runs_when_idle_and_survives_restart(monkeypatch):
    monkeypatch.setenv('PREVIEW_AUTO_MODE', 'all')
    monkeypatch.setattr(pv, '_auto_allowed_now', lambda: True)
    monkeypatch.setattr(pv, '_playback_busy', lambda: False)
    monkeypatch.setattr(pv.store, 'get_library', lambda _lid: {'effective_enabled': True, 'source': 'local'})
    monkeypatch.setattr(pv.data_usage, 'usage', lambda **_kw: {
        'ok': True, 'over_limit': False, 'categories': {'previews': 0}})
    processed = []
    def worker(jid, items, _batch):
        processed.extend(items)
        pv._jobs.update(jid, state='done')
        pv._auto_wake.set()
    monkeypatch.setattr(pv, '_worker', worker)
    pv._auto_queue.append({'kind': 'movie', 'id': 7, 'library_id': 1})
    pv._save_auto_queue()
    pv.start_auto_previews()
    deadline = time.monotonic() + 2
    while pv._auto_queue and time.monotonic() < deadline:
        time.sleep(0.01)
    # 队列退役和持久化同属一个锁内转换，等写盘完成再核对内存及磁盘。
    with pv._lock:
        assert processed == [('movie', 7)]
        assert pv._auto_queue == []
        assert json.loads((pv._root() / 'auto-queue.json').read_text()) == []


def test_existing_local_videos_join_queue_once_and_skip_ready(monkeypatch):
    monkeypatch.setenv('PREVIEW_AUTO_MODE', 'all')
    monkeypatch.setattr(pv.store, 'get_library', lambda _lid: {'id': 1, 'source': 'local', 'effective_enabled': True})
    monkeypatch.setattr(pv.store, 'preview_items', lambda _lid: [('movie', 1), ('movie', 2), ('episode', 3)])
    monkeypatch.setattr(pv, '_version_source', lambda vid, kind: ({'id': vid, 'kind': kind}, source()))
    monkeypatch.setattr(pv, '_cache_key', lambda movie, *_args: str(movie['id']))
    monkeypatch.setattr(pv, '_read_manifest', lambda directory: {'state': 'ready'} if directory.name == '2' else None)
    body = pv.PreviewQueueBody(library_id=1)
    first = pv.preview_queue_existing(body)
    second = pv.preview_queue_existing(body)
    assert first == {'total': 3, 'added': 2, 'ready': 1, 'unavailable': 0, 'already_queued': 0}
    assert second == {'total': 3, 'added': 0, 'ready': 1, 'unavailable': 0, 'already_queued': 2}
    assert json.loads((pv._root() / 'auto-queue.json').read_text()) == [
        {'kind': 'movie', 'id': 1, 'library_id': 1},
        {'kind': 'episode', 'id': 3, 'library_id': 1},
    ]


def test_queue_view_and_removal_keep_following_item(monkeypatch):
    monkeypatch.setattr(pv.store, 'get_library', lambda lid: {'id': lid} if lid == 1 else None)
    monkeypatch.setattr(pv.store, 'get_playable', lambda _kind, vid: {
        'library_id': 1, 'title': f'影片 {vid}', 'file_path': f'/media/{vid}.mkv'})
    pv._enqueue_auto_items([
        {'kind': 'movie', 'id': 7, 'library_id': 1},
        {'kind': 'movie', 'id': 8, 'library_id': 1},
    ])
    jid = pv._jobs.create(items=[('movie', 7)], total=1, auto=True)['job_id']
    pv._active_job = pv._auto_job_id = jid
    pv._jobs.update(jid, current_kind='movie', current_id=7, frames=3, frame_total=10)
    view = pv.preview_queue(library_id=1, offset=0, limit=50)
    assert view['total'] == 1 and view['items'][0]['title'] == '影片 8'
    assert view['running']['title'] == '影片 7' and view['running']['frames'] == 3
    assert pv.preview_queue_remove('movie', 7, library_id=1) == {'removed': True, 'cancelled': True}
    assert pv._jobs.get(jid)['state'] == 'cancelled'
    assert pv._auto_queue == [{'kind': 'movie', 'id': 8, 'library_id': 1}]
    assert not pv._auto_head_matches(pv._jobs.get(jid))
    assert json.loads((pv._root() / 'auto-queue.json').read_text()) == pv._auto_queue
    assert pv.preview_queue_remove('movie', 8, library_id=1) == {'removed': True, 'cancelled': False}
    assert pv.preview_queue(library_id=1, offset=0, limit=50)['total'] == 0


def test_bulk_queue_removal_spans_pages_and_protects_running_item(monkeypatch):
    monkeypatch.setattr(pv.store, 'get_library', lambda lid: {'id': lid} if lid in (1, 2) else None)
    monkeypatch.setattr(pv.store, 'get_playable', lambda _kind, vid: {
        'library_id': 1, 'title': f'影片 {vid}', 'file_path': f'/media/{vid}.mkv'})
    pv._enqueue_auto_items([
        {'kind': 'movie', 'id': 1, 'library_id': 1},
        {'kind': 'movie', 'id': 2, 'library_id': 1},
        {'kind': 'movie', 'id': 3, 'library_id': 1},
        {'kind': 'movie', 'id': 4, 'library_id': 2},
    ])
    jid = pv._jobs.create(items=[('movie', 1)], total=1, auto=True)['job_id']
    pv._active_job = pv._auto_job_id = jid
    pv._jobs.update(jid, current_kind='movie', current_id=1)
    first = pv.preview_queue(library_id=1, offset=0, limit=1)
    second = pv.preview_queue(library_id=1, offset=1, limit=1)
    assert first['total'] == second['total'] == 2
    assert first['revision'] == second['revision']
    assert [first['items'][0]['id'], second['items'][0]['id']] == [2, 3]
    result = pv.preview_queue_remove_many(pv.PreviewQueueRemoveBody(
        library_id=1, revision=first['revision'], all_pending=True,
        exclude=[{'kind': 'movie', 'id': 3}]))
    assert result == {'removed': 1}
    assert pv._jobs.get(jid)['state'] == 'running'
    assert [item['id'] for item in pv._auto_queue] == [1, 3, 4]
    with pytest.raises(HTTPException) as stale:
        pv.preview_queue_remove_many(pv.PreviewQueueRemoveBody(
            library_id=1, revision=first['revision'], items=[{'kind': 'movie', 'id': 3}]))
    assert stale.value.status_code == 409
    current = pv.preview_queue(library_id=1, offset=0, limit=50)
    assert pv.preview_queue_remove_many(pv.PreviewQueueRemoveBody(
        library_id=1, revision=current['revision'], items=[{'kind': 'movie', 'id': 3}])) == {'removed': 1}
    assert [item['id'] for item in pv._auto_queue] == [1, 4]
    assert json.loads((pv._root() / 'auto-queue.json').read_text()) == pv._auto_queue


def test_bulk_queue_removal_rolls_back_when_persistence_fails(monkeypatch):
    monkeypatch.setattr(pv.store, 'get_library', lambda _lid: {'id': 1})
    pv._enqueue_auto_items([{'kind': 'movie', 'id': 9, 'library_id': 1}])
    revision = pv._queue_revision(pv._auto_queue)
    monkeypatch.setattr(pv, '_save_auto_queue', lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(HTTPException) as error:
        pv.preview_queue_remove_many(pv.PreviewQueueRemoveBody(
            library_id=1, revision=revision, all_pending=True))
    assert error.value.status_code == 503
    assert pv._auto_queue == [{'kind': 'movie', 'id': 9, 'library_id': 1}]
