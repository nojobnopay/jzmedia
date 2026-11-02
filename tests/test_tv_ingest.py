"""Unified TV ingestion: isolated files and stubbed upstream metadata only."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store, tv_ingest
from app.main import app
from app.metadata import state
from app.routers import tv_ingest as routes

client = TestClient(app)


@pytest.fixture()
def tv(tmp_path, monkeypatch):
    root = tmp_path / 'tv'
    root.mkdir()
    lib = store.create_library(name='ingest-' + tmp_path.name, kind='tv', path=str(root), artwork_mode='none')
    library_paths.invalidate_cache()
    sid = store.upsert_show(lib['id'], '测试剧', 2020)
    tid = 900000 + lib['id']
    store.update_show_meta(sid, tmdb_id=tid, fetched_at=1, overview='剧情', status='Returning Series')
    store.upsert_tmdb_cache(tid, {'title': '测试剧', 'year': 2020, 'tmdb_id': tid,
        'media_type': 'tv', 'overview': '剧情', 'status': 'Returning Series', 'poster_tmdb_path': ''}, media_type='tv')
    monkeypatch.setattr(tv_ingest.config, 'effective_tmdb_read_token', lambda: 'fixture-token')
    monkeypatch.setattr(tv_ingest.config, 'effective_tmdb_api_key', lambda: '')
    monkeypatch.setattr(tv_ingest.tmdb, 'tv_season', lambda *_: pytest.fail('unexpected season request'))
    monkeypatch.setattr(tv_ingest.tmdb, 'tv_detail', lambda *_: pytest.fail('unexpected series request'))
    monkeypatch.setattr(tv_ingest.tmdb, 'download_image', lambda *_a, **_k: False)
    state.reset('tmdb')
    yield lib, root, sid, tid
    for job in routes._JOBS.snapshot():
        if sid in job.get('ids', []) and not job.get('worker_finished', True):
            routes.cancel(job['job_id'])
    store.delete_media_library(lib['media_library_id'])
    library_paths.invalidate_cache()
    state.reset('tmdb')


def episode(tv, number=1, season=1, version='', **fields):
    lib, root, sid, _ = tv
    rel = f'Raw/Season {season:02d}/raw{version}-S{season:02d}E{number:02d}.mkv'
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'isolated-video')
    eid = store.upsert_episode(sid, lib['id'], rel, season, number)
    if fields:
        store.update_episode_meta(eid, **fields)
    return eid, path


def season(sn, numbers=(1,)):
    return {'id': 200 + sn, 'season_number': sn, 'name': f'第{sn}季', 'episodes': [
        {'id': 10000 + sn * 100 + n, 'season_number': sn, 'episode_number': n,
         'name': f'第{sn}季第{n}集', 'overview': '官方简介', 'runtime': 45,
         'air_date': '2020-01-01'} for n in numbers]}


def prepare(tv, **options):
    return tv_ingest.prepare(ids=[tv[2]], discovered=True, **options)


def test_new_season_and_replacement_versions_fill_in_one_batch(tv, monkeypatch):
    _, _, sid, tid = tv
    existing, _ = episode(tv, number=4, season=4, tmdb_episode_id=10404, title='原第4集', watched=1)
    replacement, _ = episode(tv, number=4, season=4, version='-V2')
    new_ids = [episode(tv, n, 5)[0] for n in range(1, 9)]
    store.set_tmdb_cache_seasons(tid, {4: season(4, (4,))})
    calls = []

    def fetch(request_tid, sn):
        calls.append((request_tid, sn))
        return season(sn, range(1, 9))

    monkeypatch.setattr(tv_ingest.tmdb, 'tv_season', fetch)
    result = prepare(tv)
    assert calls == [(tid, 5)]
    assert result['metadata_results'][0]['filled'] == 9
    assert all(store.get_episode(eid)['tmdb_episode_id'] for eid in new_ids + [replacement])
    assert store.get_episode(existing)['title'] == '原第4集'
    assert store.get_episode(existing)['watched'] == 1
    assert set(store.get_tmdb_cache_seasons(tid)) == {4, 5}
    assert result['auto_prompt_ids'] == [sid]
    assert result['plans'][0]['metadata']['status'] == 'filled'
    again = prepare(tv)
    assert calls == [(tid, 5)]
    assert again['metadata_results'][0]['filled'] == 0


def test_protected_mappings_and_local_numbers_are_unchanged(tv):
    _, _, _, tid = tv
    manual, _ = episode(tv, 1, tmdb_episode_id=999, title='手工映射', match_source='manual', absolute_number=99)
    local, _ = episode(tv, 2, title='本地特典', local_only=1, absolute_number=100)
    conflict, _ = episode(tv, 3, binding_conflict=1, needs_review=1)
    auto, _ = episode(tv, 4)
    store.set_tmdb_cache_seasons(tid, {1: season(1, range(1, 5))})
    result = prepare(tv)
    assert result['metadata_results'][0]['filled'] == 1
    assert store.get_episode(manual)['tmdb_episode_id'] == 999
    assert store.get_episode(manual)['absolute_number'] == 99
    assert store.get_episode(local)['title'] == '本地特典'
    assert store.get_episode(local)['tmdb_episode_id'] is None
    assert store.get_episode(conflict)['binding_conflict'] == 1
    assert store.get_episode(conflict)['tmdb_episode_id'] is None
    assert store.get_episode(auto)['tmdb_episode_id'] == 10104


def test_existing_id_missing_title_uses_id_and_never_rebinds(tv):
    _, _, _, tid = tv
    eid, _ = episode(tv, 2, tmdb_episode_id=10101)
    store.set_tmdb_cache_seasons(tid, {1: season(1, (1, 2))})
    result = prepare(tv)
    current = store.get_episode(eid)
    assert current['title'] == '第1季第1集'
    assert current['tmdb_episode_id'] == 10101
    assert current['episode'] == 2
    assert result['metadata_results'][0]['filled'] == 1


def test_no_official_title_remains_pending_and_does_not_auto_prompt(tv, monkeypatch):
    _, _, _, tid = tv
    eid, _ = episode(tv, title='Guess from filename', needs_review=1)
    detail = season(1)
    detail['episodes'][0]['name'] = ''
    store.set_tmdb_cache_seasons(tid, {1: detail})
    calls = []
    monkeypatch.setattr(tv_ingest.tmdb, 'tv_season', lambda *args: calls.append(args) or detail)
    result = prepare(tv, actions=['rename'])
    assert not store.get_episode(eid)['tmdb_episode_id']
    assert store.get_episode(eid)['needs_review'] == 1
    assert result['metadata_results'][0]['status'] == 'metadata_pending'
    assert result['auto_prompt'] is False
    assert store.tv_ingest.list_reviews(show_id=tv[2])
    prepare(tv, actions=['rename'])
    assert len(calls) == 1
    prepare(tv, actions=['rename'], retry_metadata=True)
    assert len(calls) == 2
    episode(tv, number=2)
    prepare(tv, actions=['rename'])
    assert len(calls) == 3


def test_discovery_cache_backfill_uses_the_same_title_guard(tv, monkeypatch):
    _, _, sid, tid = tv
    eid, _ = episode(tv, title='Guess', needs_review=1)
    sd = season(1)
    sd['episodes'][0]['name'] = ''
    store.set_tmdb_cache_seasons(tid, {1: sd})
    assert scanner.backfill_cached_episodes(sid) == 0
    assert store.get_episode(eid)['tmdb_episode_id'] is None
    sd['episodes'][0]['name'] = '正式标题'
    store.set_tmdb_cache_seasons(tid, {1: sd})
    assert scanner.backfill_cached_episodes(sid) == 1
    assert store.get_episode(eid)['title'] == '正式标题'


def test_series_gaps_are_filled_without_overwriting_local_title(tv, monkeypatch):
    _, _, sid, tid = tv
    episode(tv, tmdb_episode_id=10101, title='已有集')
    store.update_show_meta(sid, title='自定义剧名', title_auto=0, overview='', status='', fetched_at=0)
    store.upsert_tmdb_cache(tid, {'title': '旧剧名', 'year': 2020, 'tmdb_id': tid}, media_type='tv')
    calls = []
    monkeypatch.setattr(tv_ingest.tmdb, 'tv_detail', lambda value: calls.append(value) or {
        'id': tid, 'name': '官方剧名', 'first_air_date': '2020-01-01', 'overview': '新增简介',
        'status': 'Returning Series', 'seasons': []})
    result = prepare(tv)
    assert calls == [tid]
    show = store.get_show_meta(sid)
    assert show['overview'] == '新增简介' and show['title'] == '自定义剧名'
    assert result['metadata_results'][0]['series_filled'] >= 2


def test_deferred_and_ignored_versions_survive_recheck_until_new_file(tv):
    _, _, sid, tid = tv
    episode(tv)
    store.set_tmdb_cache_seasons(tid, {1: season(1, (1, 2))})
    first = prepare(tv)
    fingerprint = store.tv_ingest.get_review(sid)['fingerprint']
    store.tv_ingest.decide(first['preview_id'], [sid], 'deferred')
    second = prepare(tv)
    assert store.tv_ingest.get_review(sid)['fingerprint'] == fingerprint
    assert second['auto_prompt'] is False
    assert store.tv_ingest.get_review(sid)['state'] == 'deferred'
    store.tv_ingest.decide(second['preview_id'], [sid], 'ignored')
    store.init_db()  # Startup/migration replay must preserve review decisions.
    third = prepare(tv)
    assert not third['auto_prompt']
    assert store.tv_ingest.list_reviews(show_id=sid) == []
    assert len(store.tv_ingest.list_reviews(show_id=sid, include_ignored=True)) == 1
    episode(tv, 2)
    tv_ingest.mark_dirty(sid)
    fourth = prepare(tv)
    assert fourth['auto_prompt_ids'] == [sid]
    with pytest.raises(ValueError, match='变化'):
        store.tv_ingest.decide(third['preview_id'], [sid], 'ignored')


def _canonical_multi_episode_files(tv):
    lib, root, sid, tid = tv
    episodes = []
    for start in (1, 3, 5):
        rel = (f'测试剧 (2020)/Season 01/'
               f'测试剧-S01E{start:02d}-E{start + 1:02d}-第 {start}-{start + 1} 集.mkv')
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'isolated-multi-episode-video')
        for suffix in ('.ass', '.nfo', '.srt'):
            path.with_suffix(suffix).write_bytes(b'isolated-sidecar')
        eid = store.upsert_episode(sid, lib['id'], rel, 1, start, episode_end=start + 1)
        store.update_episode_meta(eid, tmdb_episode_id=10100 + start,
                                  title=f'第 {start} 集', overview='官方简介', watched=int(start == 1))
        episodes.append(eid)
    store.set_tmdb_cache_seasons(tid, {1: season(1, range(1, 7))})
    return episodes


@pytest.mark.parametrize('decision', ['pending', 'deferred', 'ignored'])
@pytest.mark.parametrize('real_issue', [False, True])
def test_multi_episode_recheck_replaces_legacy_title_reviews(tv, monkeypatch, decision, real_issue):
    from app.scanner import tv_organize

    _, root, sid, _ = tv
    episode_ids = _canonical_multi_episode_files(tv)
    if real_issue:
        store.update_episode_meta(episode_ids[-1], needs_review=1)
    original_files = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    keys = ('id', 'file_path', 'season', 'episode', 'episode_end', 'title',
            'tmdb_episode_id', 'watched', 'needs_review')
    original_rows = [{key: store.get_episode(eid)[key] for key in keys} for eid in episode_ids]

    def old_target(show_title, ep, ver=0):
        head = tv_organize._episode_base_title(show_title, ep, ver=ver)
        title = tv_organize._clean_name(ep.get('title'))
        return f'{head}-{title}' if title else head

    with monkeypatch.context() as legacy:
        legacy.setattr(tv_organize, '_target_stem', old_target)
        legacy.setattr(tv_organize, '_is_canonical_stem', lambda *_a, **_k: False, raising=False)
        old = prepare(tv, actions=['rename'])
        assert old['plans'][0]['groups'][0]['episodes'] == 3 - int(real_issue)
        assert old['plans'][0]['groups'][0]['files'] == 9 - 3 * int(real_issue)
        if decision != 'pending':
            store.tv_ingest.decide(old['preview_id'], [sid], decision)
        assert store.tv_ingest.get_review(sid)['state'] == decision

    with pytest.raises(ValueError, match='变化'):
        tv_ingest.validate_preview(old['preview_id'])

    monkeypatch.setattr(scanner, 'scan_all', lambda *_a, **_k: pytest.fail('unexpected whole-library scan'))
    scan_show, scanned = scanner.scan_tv_show, []

    def check_show(show_id, **options):
        scanned.append(show_id)
        return scan_show(show_id, **options)

    monkeypatch.setattr(scanner, 'scan_tv_show', check_show)
    refreshed = tv_ingest.prepare(ids=[sid], actions=['rename'])
    review = store.tv_ingest.get_review(sid)
    assert scanned == [sid]
    assert refreshed['plans'][0]['groups'] == []
    assert refreshed['auto_prompt'] is False
    assert review['state'] == ('pending' if real_issue else 'clean')
    assert review['dirty'] == 0
    listed = client.get('/api/tv/ingestion-reviews', params={'show_id': sid, 'include_ignored': True}).json()
    assert listed['count'] == int(real_issue)
    assert bool(refreshed['plans'][0]['manual']) == real_issue
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == original_files
    assert [{key: store.get_episode(eid)[key] for key in keys} for eid in episode_ids] == original_rows


def test_preview_rejects_physical_and_metadata_changes(tv):
    _, _, sid, tid = tv
    eid, path = episode(tv)
    store.set_tmdb_cache_seasons(tid, {1: season(1)})
    first = prepare(tv)
    validated = tv_ingest.validate_preview(first['preview_id'], ids=[sid])
    assert validated['plans'] and validated['total'] > 0
    path.write_bytes(b'changed-video')
    with pytest.raises(ValueError, match='变化'):
        tv_ingest.validate_preview(first['preview_id'])
    second = prepare(tv)
    store.update_episode_meta(eid, title='手工修改集名')
    with pytest.raises(ValueError, match='变化'):
        tv_ingest.validate_preview(second['preview_id'])
    third = prepare(tv)
    store.update_episode_meta(eid, watched=1)
    assert tv_ingest.validate_preview(third['preview_id'])['plans']
    tv_ingest.mark_executed(third['preview_id'], [sid])
    with pytest.raises(ValueError, match='已执行'):
        tv_ingest.validate_preview(third['preview_id'])


def test_empty_scope_and_invalid_actions_never_expand_to_all(tv):
    assert tv_ingest.prepare(library_ids=[], discovered=True)['plans'] == []
    with pytest.raises(ValueError):
        prepare(tv, actions=[])
    assert client.post('/api/jobs/tv-ingest', json={}).status_code == 422
    assert client.post('/api/jobs/tv-ingest', json={'ids': [tv[2]], 'library_id': tv[0]['id']}).status_code == 422
    assert client.post('/api/jobs/tv-ingest', json={'ids': [tv[2]], 'actions': []}).status_code == 422


def test_reentrant_scope_and_cancelled_queue_release_on_real_exit(tv):
    lid = tv[0]['id']
    cancelled, entered = threading.Event(), threading.Event()

    def waiting():
        with pytest.raises(tv_ingest.IngestionCancelled):
            with tv_ingest.operation_scope([lid], cancelled.is_set):
                entered.set()

    with tv_ingest.operation_scope([lid]):
        with tv_ingest.operation_scope([lid]):
            worker = threading.Thread(target=waiting)
            worker.start()
            cancelled.set()
            worker.join(timeout=2)
            assert not worker.is_alive() and not entered.is_set()


def test_async_routes_and_versioned_review_decision(tv):
    _, _, sid, tid = tv
    episode(tv)
    store.set_tmdb_cache_seasons(tid, {1: season(1)})
    response = client.post('/api/jobs/tv-ingest', json={'ids': [sid], 'source': 'upload'})
    assert response.status_code == 200, response.text
    jid = response.json()['job_id']
    for _ in range(100):
        job = client.get('/api/jobs/tv-ingest/' + jid).json()
        if job['state'] not in ('queued', 'running'):
            break
        time.sleep(0.01)
    assert job['state'] == 'done', job
    listed = client.get('/api/tv/ingestion-reviews', params={'show_id': sid}).json()
    assert listed['count'] == 1
    pid = job['summary']['preview_id']
    choice = client.post('/api/tv/ingestion-reviews/decision',
                         json={'preview_id': pid, 'ids': [sid], 'decision': 'ignored'})
    assert choice.status_code == 200, choice.text
    assert client.get('/api/tv/ingestion-reviews', params={'show_id': sid}).json()['count'] == 0
    assert client.get('/api/tv/ingestion-reviews', params={'show_id': sid, 'include_ignored': True}).json()['count'] == 1


def test_unconfirmed_show_never_prompts_physical_changes(tv):
    _, _, sid, tid = tv
    episode(tv)
    store.set_tmdb_cache_seasons(tid, {1: season(1)})
    store.update_show_meta(sid, needs_review=1)
    result = prepare(tv, allow_torrent=True)
    assert result['total'] == 0 and not result['auto_prompt']
    assert result['plans'][0]['groups'] == []
    assert result['metadata_results'][0]['status'] == 'match_review'
    assert store.tv_ingest.list_reviews(show_id=sid)


def test_partial_metadata_failure_keeps_successful_seasons_and_retry_bound(tv, monkeypatch):
    _, _, _, tid = tv
    episode(tv, season=1)
    episode(tv, season=2)
    calls = []

    def fetch(_tid, sn):
        calls.append(sn)
        if sn == 2:
            raise TimeoutError('upstream temporarily unavailable')
        return season(sn)

    monkeypatch.setattr(tv_ingest.tmdb, 'tv_season', fetch)
    result = prepare(tv)
    assert result['metadata_results'][0]['status'] == 'source_unavailable'
    assert calls == [1, 2]
    assert set(store.get_tmdb_cache_seasons(tid)) == {1}
    prepare(tv)
    assert calls == [1, 2]


def test_successful_physical_subset_preserves_unresolved_metadata_review(tv, monkeypatch):
    _, _, sid, tid = tv
    episode(tv)
    episode(tv, number=2)
    store.set_tmdb_cache_seasons(tid, {1: season(1)})
    monkeypatch.setattr(tv_ingest.tmdb, 'tv_season', lambda *_: season(1))
    result = prepare(tv, actions=['rename'])
    plan = tv_ingest.validate_preview(result['preview_id'])
    physical = scanner.execute_tv_organize(plan['plans'])
    assert physical['moved'] == 1 and physical['failed'] == 0
    tv_ingest.mark_executed(result['preview_id'], [sid])
    review = store.tv_ingest.get_review(sid)
    assert review['state'] == 'pending' and review['dirty'] == 1
    with pytest.raises(ValueError, match='已执行'):
        tv_ingest.validate_preview(result['preview_id'])


def test_known_external_identity_fetches_its_original_source_and_all_versions(tv, monkeypatch):
    from app.metadata import chain
    _, _, sid, _ = tv
    first, _ = episode(tv)
    second, _ = episode(tv, version='-V2')
    store.update_show_meta(sid, tmdb_id=None, match_source='tvmaze',
                          content_evidence={'source': 'tvmaze', 'source_id': '1234', 'available': []})
    calls = []
    monkeypatch.setitem(chain._DETAILERS, 'tvmaze', lambda value: calls.append(value) or {
        'title': '外源剧名', 'episodes': [{'season': 1, 'episode': 1, 'title': '外源集名', 'overview': '外源简介'}]})
    result = prepare(tv)
    assert calls == ['1234']
    assert result['metadata_results'][0]['status'] == 'external'
    assert result['metadata_results'][0]['filled'] == 2
    assert store.get_show_meta(sid)['tmdb_id'] is None
    assert store.get_show_meta(sid)['match_source'] == 'tvmaze'
    assert all(store.get_episode(eid)['title'] == '外源集名' for eid in (first, second))


def test_dirty_record_is_visible_after_previous_success_without_prepare(tv):
    _, _, sid, tid = tv
    episode(tv)
    store.set_tmdb_cache_seasons(tid, {1: season(1)})
    result = prepare(tv)
    tv_ingest.mark_executed(result['preview_id'], [sid])
    assert store.tv_ingest.list_reviews(show_id=sid) == []
    tv_ingest.mark_dirty(sid)
    response = client.get('/api/tv/ingestion-reviews', params={'show_id': sid}).json()
    assert response['count'] == 1
    assert response['items'][0]['title'] == '测试剧'
    assert response['items'][0]['needs_check'] is True


def test_cancelled_job_is_not_reused_while_old_worker_still_exits(tv):
    lib, _, sid, _ = tv
    response = {}

    def request():
        first = client.post('/api/jobs/tv-ingest', json={'ids': [sid]}).json()
        client.post('/api/jobs/tv-ingest/' + first['job_id'] + '/cancel')
        second = client.post('/api/jobs/tv-ingest', json={'ids': [sid]}).json()
        response.update(first=first, second=second)
        client.post('/api/jobs/tv-ingest/' + second['job_id'] + '/cancel')

    with tv_ingest.operation_scope([lib['id']]):
        worker = threading.Thread(target=request)
        worker.start()
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert response['first']['job_id'] != response['second']['job_id']


def test_series_only_completion_writes_owned_nfo_once_and_keeps_version_stable(tv):
    lib, root, sid, _ = tv
    episode(tv, tmdb_episode_id=10101, title='已有集')
    store.update_library(lib['id'], artwork_mode='nfo')
    library_paths.invalidate_cache()
    store.update_show_meta(sid, overview='')
    first = prepare(tv)
    assert first['metadata_results'][0]['series_filled'] >= 1
    nfo = root / 'Raw' / 'tvshow.nfo'
    assert nfo.exists() and '剧情' in nfo.read_text()
    stamp = nfo.stat().st_mtime_ns
    fingerprint = store.tv_ingest.get_review(sid)['fingerprint']
    prepare(tv)
    assert nfo.stat().st_mtime_ns == stamp
    assert store.tv_ingest.get_review(sid)['fingerprint'] == fingerprint


def test_missing_cached_artwork_is_downloaded_without_refetching_series(tv, monkeypatch):
    _, _, sid, tid = tv
    episode(tv, tmdb_episode_id=10101, title='已有集')
    store.upsert_tmdb_cache(tid, {'title': '测试剧', 'year': 2020, 'tmdb_id': tid,
        'overview': '剧情', 'status': 'Returning Series', 'poster_tmdb_path': '/fixture.jpg'},
        poster_tmdb_path='/fixture.jpg', media_type='tv')
    calls = []

    def download(path, dest, size='w500'):
        from pathlib import Path
        calls.append(path)
        Path(dest).write_bytes(b'fixture-artwork')
        return True

    monkeypatch.setattr(tv_ingest.tmdb, 'download_image', download)
    result = prepare(tv)
    assert calls == ['/fixture.jpg']
    assert store.get_show_meta(sid)['poster_path'] == f'tv/{tid}.jpg'
    assert result['metadata_results'][0]['series_filled'] >= 1


def test_unpublished_season_404_is_negative_cached_without_source_cooldown(tv, monkeypatch):
    import httpx
    episode(tv, season=5)
    calls = []

    def fetch(*args):
        calls.append(args)
        response = httpx.Response(404, request=httpx.Request('GET', 'https://example.test/season'))
        raise httpx.HTTPStatusError('not published', request=response.request, response=response)

    monkeypatch.setattr(tv_ingest.tmdb, 'tv_season', fetch)
    first = prepare(tv, actions=['rename'])
    assert first['metadata_results'][0]['status'] == 'metadata_pending'
    assert state.snapshot(['tmdb'])[0]['fails'] == 0
    prepare(tv, actions=['rename'])
    assert len(calls) == 1


def test_retry_after_cools_all_seasons_and_survives_explicit_retry(tv, monkeypatch):
    import httpx
    episode(tv, season=4)
    episode(tv, season=5)
    calls = []

    def fetch(*args):
        calls.append(args)
        response = httpx.Response(429, headers={'Retry-After': '1200'},
                                  request=httpx.Request('GET', 'https://example.test/season'))
        raise httpx.HTTPStatusError('rate limited', request=response.request, response=response)

    monkeypatch.setattr(tv_ingest.tmdb, 'tv_season', fetch)
    result = prepare(tv, actions=['rename'])
    assert result['metadata_results'][0]['status'] == 'source_unavailable'
    assert len(calls) == 1
    assert state.snapshot(['tmdb'])[0]['cooldown_until'] >= int(time.time()) + 1199
    prepare(tv, actions=['rename'], retry_metadata=True)
    assert len(calls) == 1


def test_new_show_can_bind_cached_identity_without_destroying_normalized_cache(tv, monkeypatch):
    _, _, sid, tid = tv
    episode(tv)
    store.set_tmdb_cache_seasons(tid, {1: season(1)})
    before = store.get_tmdb_cached(tid, 'tv')['payload_json']
    store.update_show_meta(sid, tmdb_id=None, fetched_at=0, overview='', status='', title_auto=1)
    monkeypatch.setattr(tv_ingest.tv_match, 'resolve_show', lambda *_a, **_k:
                        {'tmdb_id': tid, 'detail': None, 'source': 'library', 'needs_review': False})
    result = prepare(tv)
    assert store.get_show_meta(sid)['tmdb_id'] == tid
    assert store.get_show_meta(sid)['overview'] == '剧情'
    assert result['metadata_results'][0]['filled'] == 1
    assert store.get_tmdb_cached(tid, 'tv')['payload_json'] == before


def test_direct_smb_ingestion_uses_the_same_cached_pipeline(tv, tmp_path, monkeypatch):
    from app import storage
    from app.storage import smb
    from _smb_fake import FakeSmbClient
    root = tmp_path / 'remote'
    root.mkdir()
    monkeypatch.setattr(smb, 'smbclient', FakeSmbClient(root))
    monkeypatch.setenv('SMB_DRIVER', 'direct')
    smb.invalidate()
    lib = store.create_library(name='ingest-remote', kind='tv', source='smb',
                               smb={'host': 'ingest-fixture', 'share': 'tv'}, artwork_mode='none')
    library_paths.invalidate_cache()
    try:
        sid = store.upsert_show(lib['id'], '远程剧', 2020)
        tid = 900000 + lib['id']
        store.update_show_meta(sid, tmdb_id=tid, fetched_at=1, overview='简介', status='Ended')
        store.upsert_tmdb_cache(tid, {'title': '远程剧', 'year': 2020, 'tmdb_id': tid,
            'overview': '简介', 'status': 'Ended', 'poster_tmdb_path': ''}, media_type='tv')
        path = root / 'Remote/Season 05/S05E01.mkv'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'remote-fixture')
        backend = storage.backend_for(lib['id'])
        registered = scanner.scan_tv_file(backend, 'Remote/Season 05/S05E01.mkv',
                                          show_root='Remote', target_show_id=sid)
        store.set_tmdb_cache_seasons(tid, {5: season(5)})
        result = tv_ingest.prepare(ids=[sid], discovered=False)
        assert store.get_episode(registered['episode_id'])['tmdb_episode_id'] == 10501
        assert result['auto_prompt_ids'] == [sid]
        assert tv_ingest.validate_preview(result['preview_id'])['total'] > 0
    finally:
        store.delete_media_library(lib['media_library_id'])
        library_paths.invalidate_cache()
        smb.invalidate()
