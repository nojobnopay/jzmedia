"""API scope regressions use an isolated DB and deliberately held worker starts."""
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import store
from app.jobkit import JobRegistry
from app.main import app
from app.routers import tv_ingest as routes
from app.store import _base


@pytest.fixture()
def intake(tmp_path, monkeypatch):
    monkeypatch.setattr(_base, 'DB_PATH', str(tmp_path / 'scope.sqlite3'))
    store.init_db()
    root = tmp_path / 'media'
    root.mkdir()
    lib = store.create_library(name='scope', kind='tv', path=str(root))
    held, prepared = [], []

    class HeldThread:
        def __init__(self, *, target, args, daemon):
            held.append((target, args))

        def start(self):
            pass

    def prepare(**kwargs):
        prepared.append(kwargs)
        return {'preview_id': 'checked', 'plans': [], 'params': {'ids': kwargs['ids']}}

    monkeypatch.setattr(routes, 'threading', SimpleNamespace(Thread=HeldThread))
    monkeypatch.setattr(routes, '_JOBS', JobRegistry(prefix='scope'))
    monkeypatch.setattr(routes.tv_ingest, 'prepare', prepare)
    return SimpleNamespace(lib=lib, held=held, prepared=prepared, client=TestClient(app))


def shows(intake, count):
    with _base._lock, _base._conn() as connection:
        connection.executemany('INSERT INTO tv_shows(library_id,title,sort_title,year) VALUES(?,?,?,2020)',
                              [(intake.lib['id'], f'show-{i}', f'show-{i}') for i in range(count)])
        return [row['id'] for row in connection.execute(
            'SELECT id FROM tv_shows WHERE library_id=? ORDER BY id', (intake.lib['id'],))]


def snapshot(intake, ids):
    plans = [{'show_id': sid, 'library_id': intake.lib['id']} for sid in ids]
    return store.tv_ingest.save_preview({'summary': {'plans': plans, 'params': {'ids': ids}},
                                        'fingerprints': {str(sid): 'v1' for sid in ids}})


def run_held(intake):
    target, args = intake.held.pop(0)
    target(*args)


@pytest.mark.parametrize('count', [500, 501, 1001])
def test_large_refresh_requires_and_respects_existing_snapshot(intake, count):
    ids = shows(intake, count)
    plain = intake.client.post('/api/jobs/tv-ingest', json={'ids': ids})
    assert plain.status_code == (200 if count <= 500 else 422)
    intake.held.clear()
    routes._JOBS = JobRegistry(prefix='scope-refresh')
    pid = snapshot(intake, ids)
    response = intake.client.post('/api/jobs/tv-ingest', json={
        'scope_preview_id': pid, 'ids': ids, 'actions': ['rename']})
    assert response.status_code == 200, response.text
    run_held(intake)
    assert intake.prepared[-1]['ids'] == ids
    assert 'library_ids' not in intake.prepared[-1]
    assert intake.client.get('/api/jobs/tv-ingest/' + response.json()['job_id']).json()['state'] == 'done'


def test_snapshot_scope_rejects_expansion_empty_sets_and_expired_handles(intake):
    first, second = shows(intake, 2)
    pid = snapshot(intake, [first])
    for body, status in [
        ({'scope_preview_id': pid, 'ids': [first, second]}, 409),
        ({'scope_preview_id': 'expired', 'ids': [first]}, 409),
        ({'scope_preview_id': pid, 'ids': []}, 422),
        ({'scope_preview_id': pid, 'library_id': intake.lib['id']}, 422),
        ({'pending_only': True, 'ids': [first]}, 422),
        ({'include_ignored': True, 'library_id': intake.lib['id']}, 422),
    ]:
        response = intake.client.post('/api/jobs/tv-ingest', json=body)
        assert response.status_code == status, response.text
    assert not intake.held


def test_snapshot_subset_never_reintroduces_hidden_shows(intake):
    first, second = shows(intake, 2)
    pid = snapshot(intake, [first, second])
    response = intake.client.post('/api/jobs/tv-ingest', json={'scope_preview_id': pid, 'ids': [second]})
    assert response.status_code == 200, response.text
    run_held(intake)
    assert intake.prepared[-1]['ids'] == [second]


def test_snapshot_cannot_follow_a_show_into_another_library(intake):
    sid = shows(intake, 1)[0]
    pid = snapshot(intake, [sid])
    root = Path(intake.lib['path'] + '-other')
    root.mkdir()
    another = store.create_library(name='other', kind='tv', path=str(root))
    with _base._lock, _base._conn() as connection:
        connection.execute('UPDATE tv_shows SET library_id=? WHERE id=?', (another['id'], sid))
    response = intake.client.post('/api/jobs/tv-ingest', json={'scope_preview_id': pid, 'ids': [sid]})
    assert response.status_code == 409, response.text
    assert not intake.held


@pytest.mark.parametrize('include_ignored', [False, True])
def test_pending_scope_freezes_over_500_exact_existing_reviews(intake, include_ignored):
    ids = shows(intake, 505)
    with _base._lock, _base._conn() as connection:
        connection.executemany('INSERT INTO tv_ingestion_reviews(show_id,library_id,state,dirty) VALUES(?,?,?,?)',
            [(sid, intake.lib['id'], 'pending', 1) for sid in ids[:501]]
            + [(ids[501], intake.lib['id'], 'deferred', 0),
               (ids[502], intake.lib['id'], 'ignored', 0),
               (ids[503], intake.lib['id'], 'ignored', 1)])
    expected = ids[:502] + ([ids[502]] if include_ignored else []) + [ids[503]]
    response = intake.client.post('/api/jobs/tv-ingest', json={
        'library_id': intake.lib['id'], 'pending_only': True, 'include_ignored': include_ignored})
    assert response.status_code == 200, response.text
    # A new pending show must not join a previously accepted request.
    store.tv_ingest.mark_dirty(ids[504])
    run_held(intake)
    assert intake.prepared[-1]['ids'] == sorted(expected)
    assert ids[504] not in intake.prepared[-1]['ids']


def test_empty_pending_scope_stays_empty_after_new_pending_work_arrives(intake):
    sid = shows(intake, 1)[0]
    response = intake.client.post('/api/jobs/tv-ingest', json={
        'library_id': intake.lib['id'], 'pending_only': True})
    assert response.status_code == 200, response.text
    store.tv_ingest.mark_dirty(sid)
    run_held(intake)
    job = intake.client.get('/api/jobs/tv-ingest/' + response.json()['job_id']).json()
    assert job['state'] == 'done'
    assert job['summary']['params']['ids'] == []
    assert not intake.prepared


def test_cancelled_pending_request_does_not_start_frozen_work(intake):
    sid = shows(intake, 1)[0]
    store.tv_ingest.mark_dirty(sid)
    response = intake.client.post('/api/jobs/tv-ingest', json={
        'library_id': intake.lib['id'], 'pending_only': True})
    jid = response.json()['job_id']
    assert intake.client.post(f'/api/jobs/tv-ingest/{jid}/cancel').json()['state'] == 'cancelled'
    run_held(intake)
    assert not intake.prepared
