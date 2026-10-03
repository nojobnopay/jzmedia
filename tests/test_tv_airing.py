"""Airing queue tests use an isolated database, fake clock and stubbed TMDB only."""
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app import config, store, tmdb, tv_airing
from app.store import _base
from app.store import tv_airing as cache


@pytest.fixture()
def airing(tmp_path, monkeypatch):
    monkeypatch.setattr(_base, 'DB_PATH', str(tmp_path / 'airing.db'))
    monkeypatch.setattr(_base, 'ensure_dirs', lambda: None)
    store.init_db()
    (tmp_path / 'tv').mkdir()
    lib = store.create_library(name='airing-test', kind='tv', path=str(tmp_path / 'tv'))
    clock = [int(datetime(2026, 10, 3, 12, tzinfo=timezone.utc).timestamp())]
    monkeypatch.setattr(tv_airing, '_now', lambda: clock[0])
    monkeypatch.setattr(config, 'effective_tmdb_read_token', lambda: 'test-token')
    monkeypatch.setattr(config, 'effective_tmdb_api_key', lambda: '')
    monkeypatch.setattr(config, 'effective_tmdb_proxy', lambda: '')

    def no_network(*args, **kwargs):
        raise AssertionError('Unexpected TMDB request')

    monkeypatch.setattr(tmdb, 'tv_airing_detail', no_network)
    monkeypatch.setattr(tmdb, 'tv_season_catalog', no_network)
    tv_airing._stop.clear()
    yield lib, clock
    tv_airing._stop.clear()
    tv_airing._wake.clear()


def show(lib, tid, title=None, **fields):
    sid = store.upsert_show(lib['id'], title or f'Show {tid}', 2026)
    store.update_show_meta(sid, tmdb_id=tid, needs_review=0, **fields)
    return sid


def episode(n, aired, season=1):
    return dict(id=season * 1000 + n, season_number=season, episode_number=n,
                name=f'Episode {n}', air_date=aired)


def detail(tid, state='Returning Series', latest=None, counts=None):
    return dict(id=tid, status=state, last_air_date=(latest or {}).get('air_date', ''),
                last_episode_to_air=latest, next_episode_to_air=None,
                seasons=[dict(id=sn + 100, season_number=sn, episode_count=count,
                              air_date='2026-09-01', name=f'Season {sn}')
                         for sn, count in (counts or {1: 12}).items()])


def http_error(status, retry_after=None):
    request = httpx.Request('GET', 'https://example.invalid/tv')
    response = httpx.Response(status, request=request,
                              headers={'Retry-After': retry_after} if retry_after else {})
    return httpx.HTTPStatusError('private credential must not escape',
                                request=request, response=response)


def test_full_baseline_deduplicates_and_leaves_numbering_alone(airing, monkeypatch):
    lib, clock = airing
    sid = show(lib, 11)
    local = store.upsert_episode(sid, lib['id'], 'Show/E04.mkv', 5, 4)
    store.update_episode_meta(local, watched=1, local_only=1)
    show(lib, 11, title='Same TMDB alternate folder')
    show(lib, 12)
    unconfirmed = show(lib, 13)
    store.update_show_meta(unconfirmed, needs_review=1)
    disabled = Path(lib['path']).parent / 'disabled'
    disabled.mkdir()
    other = store.create_library(name='disabled', kind='tv', path=str(disabled))
    show(other, 14)
    store.update_library(other['id'], enabled=False)
    calls = []
    monkeypatch.setattr(tmdb, 'tv_airing_detail',
                        lambda tid: calls.append(tid) or detail(tid, 'Ended' if tid == 12 else 'Returning Series'))
    tv_airing.run_due_once(wait_between=False)
    assert calls == [11, 12]
    assert cache.snapshot(11)['next_check_at'] == clock[0] + 7 * tv_airing.DAY
    assert cache.snapshot(12)['next_check_at'] == clock[0] + 90 * tv_airing.DAY
    assert store.get_episode(local)['season'] == 5
    assert store.get_episode(local)['watched'] == 1
    assert store.list_seasons(sid) == []
    assert tv_airing.status()['checked'] == 2
    tv_airing.run_due_once(wait_between=False)
    assert calls == [11, 12]
    clock[0] += 9 * tv_airing.DAY  # Missed a weekly run: one catchup, no replay.
    tv_airing.run_due_once(wait_between=False)
    assert calls == [11, 12, 11]
    assert cache.snapshot(11)['next_check_at'] == clock[0] + 7 * tv_airing.DAY


def test_recent_baseline_finds_all_episodes_and_keeps_finale_event(airing, monkeypatch):
    lib, clock = airing
    show(lib, 21)
    fifth, sixth = episode(5, '2026-09-28'), episode(6, '2026-10-02')
    current = [detail(21, latest=sixth)]
    monkeypatch.setattr(tmdb, 'tv_airing_detail', lambda tid: current[0])
    monkeypatch.setattr(tmdb, 'tv_season_catalog', lambda tid, sn: {
        'season_number': sn, 'episodes': [episode(4, '2026-09-20'), fifth, sixth]})
    tv_airing.run_due_once(wait_between=False)
    events = tv_airing.list_events([21])
    assert {e['episode'] for e in events} == {5, 6}
    discovered = {e['event_id']: e['discovered_at'] for e in events}
    assert tv_airing.get_cached_catalogs(21)[1]['episodes'][0]['episode_number'] == 4
    clock[0] += 7 * tv_airing.DAY
    finale = episode(7, '2026-10-08')
    current[0] = detail(21, 'Ended', latest=finale)
    monkeypatch.setattr(tmdb, 'tv_season_catalog', lambda tid, sn: {
        'season_number': sn, 'episodes': [fifth, sixth, finale, episode(1, '2026-10-08', season=0)]})
    tv_airing.run_due_once(wait_between=False)
    assert cache.snapshot(21)['detail']['status'] == 'Ended'
    events = tv_airing.list_events([21])
    assert {e['episode'] for e in events} == {5, 6, 7}
    assert all(e['season'] == 1 for e in events)
    assert all(e['discovered_at'] == discovered[e['event_id']]
               for e in events if e['episode'] != 7)
    assert cache.catalog(21, 1)['next_check_at'] == clock[0] + 30 * tv_airing.DAY


def test_cross_season_week_invalidates_previous_and_current_seasons(airing):
    _lib, clock = airing
    before = detail(1, latest=episode(11, '2026-09-26'), counts={1: 12, 2: 12})
    after = detail(1, latest=episode(1, '2026-10-02', season=2), counts={1: 12, 2: 12})
    assert tv_airing._affected_seasons(before, after, clock[0]) == {1, 2}


def test_no_credentials_never_attempts_network(airing, monkeypatch):
    lib, _clock = airing
    show(lib, 31)
    monkeypatch.setattr(config, 'effective_tmdb_read_token', lambda: '')
    tv_airing.run_due_once(wait_between=False)
    assert cache.snapshot(31)['attempted_at'] == 0
    assert tv_airing.status()['configured'] is False
    catalog = tv_airing.get_catalog(31, 1)
    assert catalog['detail'] == {} and catalog['error'] == 'not_configured'


def test_auth_failure_cools_whole_batch_and_configuration_fix_resumes(airing, monkeypatch):
    lib, clock = airing
    show(lib, 41)
    show(lib, 42)
    calls = []

    def fail(tid):
        calls.append(tid)
        raise http_error(401)

    monkeypatch.setattr(tmdb, 'tv_airing_detail', fail)
    tv_airing.run_due_once(wait_between=False)
    assert calls == [41]
    assert tv_airing.status()['error'] == 'invalid_credentials'
    assert cache.snapshot(41)['error'] == 'invalid_credentials'
    assert cache.snapshot(42)['attempted_at'] == 0
    tv_airing.request_check()
    tv_airing.run_due_once(wait_between=False)
    assert calls == [41]
    monkeypatch.setattr(config, 'effective_tmdb_read_token', lambda: 'corrected-test-token')
    monkeypatch.setattr(tmdb, 'tv_airing_detail', lambda tid: calls.append(tid) or detail(tid))
    clock[0] += 1
    tv_airing.run_due_once(wait_between=False)
    assert calls[0] == 41 and sorted(calls[1:]) == [41, 42]
    assert tv_airing.status()['checked'] == 2


def test_retry_after_and_old_snapshot_are_preserved(airing, monkeypatch):
    lib, clock = airing
    show(lib, 51)
    monkeypatch.setattr(tmdb, 'tv_airing_detail', lambda tid: detail(tid))
    tv_airing.run_due_once(wait_between=False)
    checked = cache.snapshot(51)['checked_at']
    clock[0] += 7 * tv_airing.DAY

    def fail(tid):
        raise http_error(429, str(2 * tv_airing.DAY))

    monkeypatch.setattr(tmdb, 'tv_airing_detail', fail)
    tv_airing.run_due_once(wait_between=False)
    snapshot = cache.snapshot(51)
    assert snapshot['checked_at'] == checked
    assert snapshot['detail']['status'] == 'Returning Series'
    assert snapshot['next_check_at'] == clock[0] + 2 * tv_airing.DAY
    assert tv_airing.get_snapshot(51)['stale'] is True
    assert tv_airing.status()['retry_at'] == clock[0] + 2 * tv_airing.DAY


def test_catalog_failure_retries_without_waiting_for_next_show_check(airing, monkeypatch):
    lib, clock = airing
    show(lib, 61)
    calls = []
    monkeypatch.setattr(tmdb, 'tv_airing_detail',
                        lambda tid: calls.append(('show', tid)) or detail(tid, latest=episode(6, '2026-10-02')))

    def fail(tid, sn):
        calls.append(('season', sn))
        raise http_error(500)

    monkeypatch.setattr(tmdb, 'tv_season_catalog', fail)
    tv_airing.run_due_once(wait_between=False)
    assert cache.catalog(61, 1)['pending'] == 1
    assert cache.snapshot(61)['checked_at'] == clock[0]
    summary = tv_airing.status()
    assert summary['checked'] == 1 and summary['failed'] == 1
    assert summary['catalog_failed'] == 1 and summary['catalog_pending'] == 1
    assert tv_airing.get_catalog(61, 1, refresh=False)['error'] == 'unavailable'
    clock[0] += tv_airing.DAY
    monkeypatch.setattr(tmdb, 'tv_season_catalog', lambda tid, sn: calls.append(('season', sn)) or {
        'season_number': sn, 'episodes': [episode(6, '2026-10-02')]})
    tv_airing.run_due_once(wait_between=False)
    assert calls == [('show', 61), ('season', 1), ('season', 1)]
    assert cache.catalog(61, 1)['pending'] == 0
    assert tv_airing.status()['failed'] == 0


def test_expired_lease_can_resume_but_late_owner_cannot_commit(airing):
    lib, clock = airing
    show(lib, 71)
    cache.ensure_queue()
    assert cache.claim_snapshot(71, 'old', clock[0], 120)
    assert not cache.claim_snapshot(71, 'new', clock[0], 120)
    clock[0] += 121
    assert cache.claim_snapshot(71, 'new', clock[0], 120)
    assert not cache.save_snapshot(71, 'old', clock[0], clock[0] + 100,
                                   detail(71, 'Ended'), [], [])
    assert cache.save_snapshot(71, 'new', clock[0], clock[0] + 100,
                               detail(71), [], [])
    assert cache.snapshot(71)['detail']['status'] == 'Returning Series'


def test_rebound_show_is_not_overwritten_by_old_request(airing, monkeypatch):
    lib, _clock = airing
    sid = show(lib, 81)

    def rebind_while_fetching(tid):
        store.update_show_meta(sid, tmdb_id=82, status='Planned')
        return detail(tid, 'Ended')

    monkeypatch.setattr(tmdb, 'tv_airing_detail', rebind_while_fetching)
    tv_airing.run_due_once(wait_between=False)
    assert store.get_show_meta(sid)['status'] == 'Planned'
    assert cache.snapshot(81)['checked_at'] == 0


def test_catalog_invalidation_rejects_an_older_inflight_result(airing):
    lib, clock = airing
    show(lib, 91)
    cache.ensure_queue()
    rev = cache.claim_catalog(91, 1, 'catalog-owner', clock[0], 120)
    assert rev == 0
    assert cache.claim_snapshot(91, 'show-owner', clock[0], 120)
    assert cache.save_snapshot(91, 'show-owner', clock[0], clock[0] + 100,
                               detail(91), [1], [])
    assert not cache.save_catalog(91, 1, 'catalog-owner', rev, clock[0], clock[0] + 100,
                                  {'season_number': 1, 'episodes': []}, [])
    row = cache.catalog(91, 1)
    assert row['pending'] == 1 and row['lease_until'] == 0 and row['checked_at'] == 0


def test_light_requests_have_no_appended_payload_or_retry(monkeypatch):
    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, path, **kwargs):
            calls.append((path, kwargs))
            raise http_error(429)

    monkeypatch.setattr(tmdb, '_client', Client)
    monkeypatch.setattr(tmdb, '_params', lambda: {'language': 'zh-CN'})
    with pytest.raises(httpx.HTTPStatusError):
        tmdb.tv_airing_detail(101)
    assert len(calls) == 1
    assert calls[0][1] == {'params': {'language': 'zh-CN'}, 'timeout': 8.0}


def test_official_episode_correction_keeps_stable_discovery_identity(airing):
    lib, clock = airing
    show(lib, 111)
    cache.ensure_queue()
    first = episode(1, '2026-10-01')
    initial = tv_airing._events(111, [first], clock[0], clock[0])
    assert cache.claim_snapshot(111, 'first', clock[0], 120)
    assert cache.save_snapshot(111, 'first', clock[0], clock[0] + 1, detail(111), [], initial)
    discovered_at = clock[0]
    clock[0] += 2
    corrected = {**first, 'episode_number': 2, 'name': 'Corrected title'}
    changed = tv_airing._events(111, [corrected], clock[0], discovered_at)
    assert cache.claim_snapshot(111, 'second', clock[0], 120)
    assert cache.save_snapshot(111, 'second', clock[0], clock[0] + 100, detail(111), [], changed)
    events = tv_airing.list_events([111])
    assert len(events) == 1
    assert events[0]['event_id'] == initial[0]['event_id']
    assert events[0]['episode'] == 2 and events[0]['title'] == 'Corrected title'
    assert events[0]['discovered_at'] == discovered_at


def test_catalog_future_and_removed_episodes_are_reconciled(airing, monkeypatch):
    lib, clock = airing
    show(lib, 121)
    fifth, sixth = episode(5, '2026-09-28'), episode(6, '2026-10-02')
    monkeypatch.setattr(tmdb, 'tv_airing_detail', lambda tid: detail(tid, latest=sixth))
    current = [{'season_number': 1, 'episodes': [fifth, sixth]}]
    monkeypatch.setattr(tmdb, 'tv_season_catalog', lambda tid, sn: current[0])
    tv_airing.run_due_once(wait_between=False)
    initial = {ev['episode']: ev for ev in tv_airing.list_events([121])}
    assert set(initial) == {5, 6}
    clock[0] += 7 * tv_airing.DAY
    # E05 was postponed; E06 was removed from TMDB. Neither remains a recommendation.
    current[0] = {'season_number': 1, 'episodes': [{**fifth, 'air_date': '2026-10-20'}]}
    tv_airing.get_catalog(121, 1)
    assert tv_airing.list_events([121]) == []
    clock[0] += 7 * tv_airing.DAY
    current[0] = {'season_number': 1, 'episodes': [{**fifth, 'air_date': '2026-10-15'}]}
    tv_airing.get_catalog(121, 1)
    restored = tv_airing.list_events([121])
    assert len(restored) == 1 and restored[0]['episode'] == 5
    assert restored[0]['event_id'] == initial[5]['event_id']
    assert restored[0]['discovered_at'] == initial[5]['discovered_at']


def test_latest_episode_future_correction_invalidates_existing_event(airing):
    lib, clock = airing
    show(lib, 131)
    cache.ensure_queue()
    original = episode(6, '2026-10-02')
    events = tv_airing._events(131, [original], clock[0], clock[0])
    assert cache.claim_snapshot(131, 'first', clock[0], 120)
    assert cache.save_snapshot(131, 'first', clock[0], clock[0] + 1,
                               detail(131, latest=original), [], events)
    clock[0] += 2
    future = {**original, 'air_date': '2026-10-20'}
    assert cache.claim_snapshot(131, 'second', clock[0], 120)
    assert cache.save_snapshot(131, 'second', clock[0], clock[0] + 100,
                               detail(131, latest=future), [], [])
    assert tv_airing.list_events([131]) == []


def test_legacy_catalog_fallback_is_stale_and_keeps_current_error(airing, monkeypatch):
    lib, _clock = airing
    show(lib, 141)
    store.upsert_tmdb_cache(141, {'title': 'Legacy'}, media_type='tv')
    old = {'season_number': 1, 'episodes': [episode(1, '2026-09-20')]}
    assert store.set_tmdb_cache_seasons(141, {1: old})
    monkeypatch.setattr(config, 'effective_tmdb_read_token', lambda: '')
    result = tv_airing.get_catalog(141, 1)
    assert result['detail']['episodes'][0]['id'] == old['episodes'][0]['id']
    assert result['checked_at'] == 0 and result['stale'] is True
    assert result['error'] == 'not_configured' and result['source'] == 'offline_cache'
    assert cache.catalog(141, 1) is None
