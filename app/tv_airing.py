"""Low frequency TV airing checks, independent of scraping and media storage."""
import hashlib
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from . import config, tmdb
from .log import get_logger
from .store import tv_airing as cache
from .store.tmdb_cache import get_tmdb_cache_seasons


DAY = 86400
CHECK_INTERVAL = 7 * DAY
ENDED_INTERVAL = 90 * DAY
POLL_INTERVAL = 300
LEASE_SECONDS = 120
REQUEST_INTERVAL = 1.0
_ENDED = {'Ended', 'Canceled', 'Cancelled'}
_stop = threading.Event()
_wake = threading.Event()
_thread = None
_lifecycle_lock = threading.Lock()
_run_lock = threading.Lock()
_network_lock = threading.Lock()
_current = 0
logger = get_logger('tv_airing')


def _now():
    return int(time.time())


def _credentials():
    token = config.effective_tmdb_read_token()
    key = config.effective_tmdb_api_key()
    fingerprint = hashlib.sha256(('\0'.join((token, key, config.effective_tmdb_proxy())))
                                 .encode()).hexdigest()
    return bool(token or key), fingerprint


def _gate(now):
    configured, fingerprint = _credentials()
    if not configured:
        return 'not_configured', 0, fingerprint
    state = cache.control()
    if state.get('fingerprint') == fingerprint and int(state.get('retry_at') or 0) > now:
        return state.get('error') or 'unavailable', int(state['retry_at']), fingerprint
    return '', 0, fingerprint


def _prepare_configuration():
    configured, fingerprint = _credentials()
    state = cache.control()
    if configured and state.get('fingerprint') != fingerprint:
        cache.reset_configuration_error(state.get('error'), _now())
        cache.set_control(fingerprint)


def _date(value):
    try:
        return date.fromisoformat(str(value or ''))
    except (ValueError, TypeError):
        return None


def _today(now):
    return datetime.fromtimestamp(now, timezone.utc).date()


def _int(value):
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def _events(tid, episodes, now, baseline_at):
    today = _today(now)
    oldest = max(today - timedelta(days=30), _today(baseline_at or now) - timedelta(days=7))
    result = []
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        sn, en = _int(episode.get('season_number')), _int(episode.get('episode_number'))
        aired = _date(episode.get('air_date'))
        if sn <= 0 or en <= 0 or aired is None or not oldest <= aired <= today:
            continue
        eid = _int(episode.get('id'))
        identity = f'ep:{eid}' if eid > 0 else f's:{sn}:e:{en}'
        result.append(dict(event_id=f'tv:{int(tid)}:{identity}', tmdb_id=int(tid),
                           tmdb_episode_id=eid or None, season=sn, episode=en,
                           title=str(episode.get('name') or ''), air_date=aired.isoformat(),
                           discovered_at=now))
    return result


def _affected_seasons(previous, detail, now):
    latest = detail.get('last_episode_to_air') or {}
    if not previous:
        aired = _date(latest.get('air_date'))
        # Recent baseline episodes need the complete current season to catch E05
        # even when TMDB's single latest-episode field points at E06.
        if aired and _today(now) - timedelta(days=7) <= aired <= _today(now):
            return {_int(latest.get('season_number'))}
        return set()
    old = {_int(s.get('season_number')): s for s in previous.get('seasons') or []
           if isinstance(s, dict)}
    affected = set()
    for season in detail.get('seasons') or []:
        if not isinstance(season, dict):
            continue
        sn = _int(season.get('season_number'))
        if sn not in old or any(season.get(k) != old[sn].get(k)
                                for k in ('episode_count', 'air_date', 'id')):
            affected.add(sn)
    before = previous.get('last_episode_to_air') or {}
    keys = ('id', 'season_number', 'episode_number', 'air_date')
    if latest and any(latest.get(k) != before.get(k) for k in keys):
        affected.add(_int(latest.get('season_number')))
        if before:
            affected.add(_int(before.get('season_number')))
    return {sn for sn in affected if sn >= 0}


def _error(exc, now):
    code, stop_batch, retry_at = 'unavailable', False, now + DAY
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in (401, 403):
            code, stop_batch = 'invalid_credentials', True
        elif status_code == 429:
            code, stop_batch = 'rate_limited', True
            value = exc.response.headers.get('retry-after', '')
            try:
                retry_at = max(retry_at, now + max(0, int(value)))
            except ValueError:
                try:
                    retry_at = max(retry_at, int(parsedate_to_datetime(value).timestamp()))
                except (ValueError, TypeError, OverflowError):
                    pass
        elif status_code == 404:
            code = 'not_found'
        elif status_code >= 500:
            stop_batch = True
    elif isinstance(exc, httpx.TimeoutException):
        code, stop_batch = 'timeout', True
    elif isinstance(exc, httpx.TransportError):
        stop_batch = True
    elif isinstance(exc, (ValueError, TypeError, KeyError)):
        code = 'invalid_response'
    return code, retry_at, stop_batch


def get_snapshot(tid):
    row = cache.snapshot(tid)
    if not row:
        return dict(detail={}, checked_at=0, next_check_at=0, error='', stale=True)
    return {**row, 'stale': bool(row['error'] or not row['checked_at']
                               or _now() >= row['next_check_at']),
            'refreshing': row['lease_until'] > _now()}


def get_cached_catalogs(tid):
    """Read-only raw official episode catalogues, keyed by integer season number."""
    return cache.cached_catalogs(tid)


def _catalog_result(row, now, error='', retry_at=0):
    out = dict(detail={}, checked_at=0, next_check_at=0, error='', stale=True, refreshing=False)
    if row:
        out.update(row)
        out['stale'] = bool(row['pending'] or not row['checked_at'] or now >= row['next_check_at'])
        out['refreshing'] = row['lease_until'] > now
    if error:
        out.update(error=error, stale=True, retry_at=retry_at)
    return out


def _catalog_response(tid, sn, row, now, error='', retry_at=0):
    result = _catalog_result(row, now, error, retry_at)
    if not result['detail']:
        old = get_tmdb_cache_seasons(tid).get(sn)
        if isinstance(old, dict) and isinstance(old.get('episodes'), list):
            result.update(detail=old, checked_at=0, stale=True, source='offline_cache')
    return result


def get_catalog(tid, sn, refresh=True):
    tid, sn, now = int(tid), int(sn), _now()
    if tid <= 0 or sn < 0:
        return _catalog_result(None, now, 'invalid_request')
    if refresh:
        _prepare_configuration()
    row = cache.catalog(tid, sn)
    current = _catalog_response(tid, sn, row, now)
    if not refresh or (row and now < row['next_check_at']):
        return current
    error, retry_at, fingerprint = _gate(now)
    if error:
        return _catalog_response(tid, sn, row, now, error, retry_at)
    owner = uuid.uuid4().hex
    try:
        with _network_lock:
            now = _now()
            revision = cache.claim_catalog(tid, sn, owner, now, LEASE_SECONDS)
            if revision is None:
                return _catalog_response(tid, sn, cache.catalog(tid, sn), now)
            error, retry_at, fingerprint = _gate(_now())
            if error:
                cache.fail_catalog(tid, sn, owner, _now(), retry_at or _now() + DAY, error)
                return _catalog_response(tid, sn, cache.catalog(tid, sn), _now(), error, retry_at)
            detail = tmdb.tv_season_catalog(tid, sn)
        if (not isinstance(detail, dict) or _int(detail.get('season_number')) != sn
                or not isinstance(detail.get('episodes'), list)):
            raise ValueError('invalid season catalogue')
        now = _now()
        snapshot = cache.snapshot(tid) or {}
        state = (snapshot.get('detail') or {}).get('status')
        ttl = (30 if state in _ENDED else 7) * DAY
        events = _events(tid, detail['episodes'], now, snapshot.get('baseline_at') or now)
        cache.save_catalog(tid, sn, owner, revision, now, now + ttl, detail, events)
    except Exception as exc:
        now = _now()
        code, retry_at, stop_batch = _error(exc, now)
        cache.fail_catalog(tid, sn, owner, now, retry_at, code)
        if stop_batch:
            cache.set_control(fingerprint, retry_at, code)
        logger.warning('TV catalogue check failed tmdb_id=%s season=%s code=%s', tid, sn, code)
    return _catalog_response(tid, sn, cache.catalog(tid, sn), _now())


def _check_one(tid):
    global _current
    now = _now()
    error, _, fingerprint = _gate(now)
    if error:
        return False
    owner = uuid.uuid4().hex
    try:
        with _network_lock:
            now = _now()
            if not cache.claim_snapshot(tid, owner, now, LEASE_SECONDS):
                return False
            _current = tid
            previous = cache.snapshot(tid) or {}
            error, retry_at, fingerprint = _gate(_now())
            if error:
                cache.fail_snapshot(tid, owner, _now(), retry_at or _now() + DAY, error)
                return False
            detail = tmdb.tv_airing_detail(tid)
        if (not isinstance(detail, dict) or _int(detail.get('id')) != int(tid)
                or not isinstance(detail.get('seasons'), list)):
            raise ValueError('invalid airing snapshot')
        now = _now()
        interval = ENDED_INTERVAL if detail.get('status') in _ENDED else CHECK_INTERVAL
        seasons = _affected_seasons(previous.get('detail') or {}, detail, now)
        events = _events(tid, [detail.get('last_episode_to_air')], now,
                         previous.get('baseline_at') or now)
        return cache.save_snapshot(tid, owner, now, now + interval, detail, seasons, events)
    except Exception as exc:
        now = _now()
        code, retry_at, stop_batch = _error(exc, now)
        cache.fail_snapshot(tid, owner, now, retry_at, code)
        if stop_batch:
            cache.set_control(fingerprint, retry_at, code)
        logger.warning('TV airing check failed tmdb_id=%s code=%s', tid, code)
        return False
    finally:
        _current = 0


def list_events(tids):
    return cache.list_events(tids, (_today(_now()) - timedelta(days=30)).isoformat())


def status():
    now = _now()
    configured, _ = _credentials()
    error, retry_at, _ = _gate(now)
    result = cache.summary(now)
    result.update(configured=configured, current=_current, error=error, retry_at=retry_at,
                  running=bool(result['running'] or _run_lock.locked()))
    return result


def request_check(tids=None):
    _prepare_configuration()
    cache.mark_due(tids, _now())
    _wake.set()
    return {**status(), 'status': 'queued'}


def run_due_once(*, wait_between=True):
    """One restart-safe queue pass; clock and TMDB calls are injectable in tests."""
    if not _run_lock.acquire(blocking=False):
        return
    try:
        cache.ensure_queue()
        _prepare_configuration()
        if _gate(_now())[0]:
            return
        for tid in cache.due_ids(_now()):
            if _stop.is_set() or _gate(_now())[0]:
                break
            _check_one(tid)
            if wait_between and _stop.wait(REQUEST_INTERVAL):
                break
        # Pending catalogue failures remain queued independently of show snapshots.
        for tid, sn in cache.pending_catalogs(_now()):
            if _stop.is_set() or _gate(_now())[0]:
                break
            get_catalog(tid, sn)
            if wait_between and _stop.wait(REQUEST_INTERVAL):
                break
        cache.prune_events((_today(_now()) - timedelta(days=30)).isoformat())
    finally:
        _run_lock.release()


def _loop():
    while not _stop.is_set():
        _wake.clear()
        try:
            run_due_once()
        except Exception:
            logger.exception('TV airing queue pass failed')
        _wake.wait(POLL_INTERVAL)


def start():
    global _thread
    with _lifecycle_lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _wake.clear()
        _thread = threading.Thread(target=_loop, daemon=True, name='jzmedia-tv-airing')
        _thread.start()


def shutdown():
    global _thread
    with _lifecycle_lock:
        _stop.set()
        _wake.set()
        if _thread is not None:
            _thread.join(timeout=20)
            if not _thread.is_alive():
                _thread = None
