import json
from pathlib import Path
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
    monkeypatch.setattr(pv, '_root', lambda: tmp_path / 'previews')
    pv._stop.clear()
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


def test_manifest_missing_never_starts_job(monkeypatch):
    monkeypatch.setattr(pv, '_version_source', lambda *a: (row(), source()))
    monkeypatch.setattr(pv, '_start', lambda *a: pytest.fail('GET must not generate'))
    assert pv.preview_manifest(7)['state'] == 'missing'


def test_cancelled_generation_resumes_completed_pages(monkeypatch):
    calls = []
    stop_at_second_page = True
    def run(cmd, jid, timeout=60):
        if '-ss' in cmd:
            timestamp = int(cmd[cmd.index('-ss') + 1])
            if timestamp == 250 and stop_at_second_page:
                raise pv._Cancelled()
            calls.append(timestamp)
        Path(cmd[-1]).write_bytes(b'jpeg')
    monkeypatch.setattr(pv, '_run', run)
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
    assert calls == [250, 260]
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
