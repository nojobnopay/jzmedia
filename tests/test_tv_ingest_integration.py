"""HTTP and legacy-entry integration using temporary local media only."""
import threading
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store, tv_ingest
from app.jobkit import JobRegistry
from app.main import app
from app.routers import jobs

client = TestClient(app)


@pytest.fixture()
def show_fixture(tmp_path, monkeypatch):
    root = tmp_path / 'tv'
    root.mkdir()
    lib = store.create_library(name='ingest-api-' + tmp_path.name, kind='tv', path=str(root),
                               artwork_mode='none')
    library_paths.invalidate_cache()
    path = root / 'Example (2020)' / 'Season 01' / 'Example.S01E01.mkv'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'original video')
    scanner.scan_all(library_id=lib['id'])
    show = store.list_shows(lib['id'])[0]
    store.update_show_meta(show['id'], tmdb_id=990011, fetched_at=1, needs_review=0,
                           overview='Isolated series', status='Ended', poster_path='tv/fixture.jpg')
    episode = store.list_episodes(show['id'])[0]
    store.update_episode_meta(episode['id'], tmdb_episode_id=99001101, title='First episode')
    monkeypatch.setattr(tv_ingest, '_source_available', lambda: False)
    yield lib, root, show['id'], episode['id']
    store.delete_media_library(lib['media_library_id'])
    library_paths.invalidate_cache()


def _finished(path, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(path + '/' + job_id)
        assert response.status_code == 200, response.text
        job = response.json()
        if job['state'] not in ('running', 'queued') and job.get('worker_finished') is not False:
            return job
        time.sleep(0.01)
    pytest.fail('isolated job did not finish')


def test_uploaded_tv_file_persists_pending_ingestion(show_fixture):
    lib, root, sid, _ = show_fixture
    response = client.post('/api/uploads', params={
        'library_id': lib['id'], 'media_type': 'tv', 'mode': 'files',
        'show_id': sid, 'season': 5, 'relpath': 'Example.S05E01.mkv',
    }, files={'file': ('Example.S05E01.mkv', b'new season')})
    assert response.status_code == 200, response.text
    assert response.json()['show_id'] == sid
    assert store.tv_ingest.get_review(sid)['dirty'] == 1
    assert store.fs_change_summary(lib['id'])['actions']['upload'] == 1
    assert (root / response.json()['rel']).read_bytes() == b'new season'


def test_preview_rejects_replaced_file_before_start(show_fixture):
    _, root, sid, _ = show_fixture
    summary = tv_ingest.prepare(ids=[sid], actions=['rename'], source='manual')
    assert summary['total'] > 0
    assert summary['plans'][0]['fingerprint'] == store.tv_ingest.get_review(sid)['fingerprint']
    path = root / 'Example (2020)' / 'Season 01' / 'Example.S01E01.mkv'
    path.write_bytes(b'a replacement version with a different size')
    response = client.post('/api/jobs/tv-organize', json={
        'preview_id': summary['preview_id'], 'ids': [sid], 'actions': ['rename'], 'dry_run': False,
    })
    assert response.status_code == 409, response.text
    assert path.exists()


def test_versioned_execution_keeps_episode_identity_and_consumes_preview(show_fixture):
    lib, root, sid, eid = show_fixture
    summary = tv_ingest.prepare(ids=[sid], actions=['rename'], source='manual')
    body = {'preview_id': summary['preview_id'], 'ids': [sid],
            'actions': ['rename'], 'dry_run': False}
    response = client.post('/api/jobs/tv-organize', json=body)
    assert response.status_code == 200, response.text
    job = _finished('/api/jobs/tv-organize', response.json()['job_id'])
    assert job['state'] == 'done', job
    assert job['summary']['renamed'] + job['summary']['moved'] == 1
    episode = store.get_episode(eid)
    assert episode['show_id'] == sid
    assert episode['tmdb_episode_id'] == 99001101
    assert (root / episode['file_path']).exists()
    assert store.tv_ingest.get_review(sid)['state'] == 'clean'
    assert store.list_organize_moves(library_id=lib['id'])
    assert client.post('/api/jobs/tv-organize', json=body).status_code == 409


@pytest.mark.parametrize('explicit_ids', [True, False])
def test_cancelled_worker_holds_scope_until_its_io_returns(show_fixture, monkeypatch, explicit_ids):
    lib, _, sid, _ = show_fixture
    entered, release, second = threading.Event(), threading.Event(), threading.Event()
    registry = JobRegistry()
    monkeypatch.setattr(jobs, '_TV_JOBS', registry)

    def blocking_scrape(**kwargs):
        entered.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr('app.scanner.tv_persist.scrape_pending', blocking_scrape)
    monkeypatch.setattr('app.scanner.tv_persist.backfill_person_names', lambda **kw: [])
    ids = [sid] if explicit_ids else []
    job = registry.create(library_id=lib['id'], ids=ids, worker_finished=False)
    worker = threading.Thread(target=jobs._tv_worker,
        args=(job['job_id'], lib['id'], None, ids, False))
    worker.start()
    assert entered.wait(5)
    registry.cancel(job['job_id'])

    def next_operation():
        with tv_ingest.operation_scope([lib['id']]):
            second.set()

    waiter = threading.Thread(target=next_operation)
    waiter.start()
    try:
        assert not second.wait(0.1)
        assert registry.get(job['job_id'])['worker_finished'] is False
    finally:
        release.set()
        worker.join(5)
        waiter.join(5)
    assert second.is_set()
    assert registry.get(job['job_id'])['worker_finished'] is True


def test_ingestion_and_review_resources_require_management(tmp_path, monkeypatch):
    monkeypatch.setattr('app.store._base.DB_PATH', str(tmp_path / 'protected-ingest.db'))
    store.init_db()
    with closing(TestClient(app)) as protected:
        protected.get('/api/viewing/session')
        assert protected.post('/api/viewing/password', json={'new_password': '123456'}).status_code == 200
        requests = [
            ('POST', '/api/jobs/tv-ingest', {'ids': [1], 'source': 'detail'}),
            ('GET', '/api/jobs/tv-ingest/unknown', None),
            ('GET', '/api/tv/ingestion-reviews?show_id=1', None),
            ('POST', '/api/tv/ingestion-reviews/decision', {'preview_id': 'unknown', 'decision': 'ignored'}),
        ]
        for method, path, body in requests:
            response = protected.request(method, path, **({'json': body} if body else {}))
            assert response.status_code == 403, response.text
            assert response.json()['code'] == 'management_required'
        protected.post('/api/viewing/mode', json={'mode': 'children'})
        assert protected.get('/api/tv/ingestion-reviews?show_id=1').status_code == 403
