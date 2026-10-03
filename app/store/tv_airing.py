"""Durable airing cache. No filesystem access or changes to local episode numbering."""
import json
from datetime import date, datetime, timezone

from ._base import _conn, _lock


_ELIGIBLE = """SELECT DISTINCT s.tmdb_id FROM tv_shows s
 JOIN libraries l ON l.id=s.library_id
 JOIN media_libraries m ON m.id=l.media_library_id
 WHERE l.kind='tv' AND l.enabled=1 AND m.enabled=1
 AND s.tmdb_id>0 AND COALESCE(s.needs_review,0)=0"""


def eligible_ids() -> list[int]:
    with _lock, _conn() as c:
        return [int(r[0]) for r in c.execute(_ELIGIBLE + ' ORDER BY s.tmdb_id')]


def _eligible(c, tid):
    return c.execute('SELECT 1 FROM (' + _ELIGIBLE + ') WHERE tmdb_id=?',
                     (int(tid),)).fetchone() is not None


def ensure_queue() -> None:
    with _lock, _conn() as c:
        c.execute('INSERT OR IGNORE INTO tv_airing_snapshots(tmdb_id) ' + _ELIGIBLE)


def _decode(row):
    if not row:
        return None
    out = dict(row)
    try:
        out['detail'] = json.loads(out.pop('payload', '{}'))
    except (ValueError, TypeError):
        out['detail'] = {}
    return out


def snapshot(tid):
    with _lock, _conn() as c:
        return _decode(c.execute('SELECT * FROM tv_airing_snapshots WHERE tmdb_id=?',
                                 (int(tid),)).fetchone())


def due_ids(now):
    with _lock, _conn() as c:
        return [int(r[0]) for r in c.execute(
            'SELECT tmdb_id FROM tv_airing_snapshots WHERE next_check_at<=?'
            ' AND lease_until<=? AND tmdb_id IN (' + _ELIGIBLE + ')'
            ' ORDER BY checked_at>0, next_check_at, tmdb_id', (now, now))]


def mark_due(tids, now):
    ensure_queue()
    selected = set(eligible_ids()) if tids is None else set(map(int, tids))
    with _lock, _conn() as c:
        for tid in selected:
            # A manual click must not defeat a failed request's backoff.
            c.execute('UPDATE tv_airing_snapshots SET next_check_at=? WHERE tmdb_id=?'
                      " AND (error='' OR next_check_at<=?) AND lease_until<=?",
                      (now, tid, now, now))


def claim_snapshot(tid, owner, now, ttl):
    with _lock, _conn() as c:
        c.execute('BEGIN IMMEDIATE')
        if not _eligible(c, tid):
            return False
        changed = c.execute(
            'UPDATE tv_airing_snapshots SET lease_owner=?, lease_until=?, attempted_at=?'
            ' WHERE tmdb_id=? AND lease_until<=? AND next_check_at<=?',
            (owner, now + ttl, now, int(tid), now, now))
        return changed.rowcount == 1


def _add_events(c, events):
    for event in events:
        c.execute('INSERT INTO tv_airing_events'
                  '(event_id,tmdb_id,tmdb_episode_id,season,episode,title,air_date,discovered_at)'
                  ' VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET'
                  ' season=excluded.season,episode=excluded.episode,title=excluded.title,'
                  'air_date=excluded.air_date,active=1',
                  tuple(event[k] for k in ('event_id', 'tmdb_id', 'tmdb_episode_id',
                                          'season', 'episode', 'title', 'air_date',
                                          'discovered_at')))


def _number(value, fallback=0):
    try:
        return int(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _reconcile_events(c, tid, episodes, now, complete_season=None):
    """Correct known events even when an upstream change makes them ineligible.

    Inactive records preserve discovery identity across postponed dates and season
    renumbering; missing entries are authoritative only for a complete catalogue.
    """
    rows = [dict(r) for r in c.execute('SELECT * FROM tv_airing_events WHERE tmdb_id=?',
                                     (int(tid),))]
    by_id, by_coord = {}, {}
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        sn = _number(episode.get('season_number'), complete_season if complete_season is not None else -1)
        en = _number(episode.get('episode_number'))
        eid = _number(episode.get('id'))
        if eid > 0:
            by_id[eid] = (episode, sn, en)
        by_coord[(sn, en)] = (episode, sn, en)
    today = datetime.fromtimestamp(now, timezone.utc).date()
    for old in rows:
        hit = (by_id.get(old['tmdb_episode_id']) if old['tmdb_episode_id']
               else by_coord.get((old['season'], old['episode'])))
        if hit is None:
            if complete_season is not None and old['season'] == complete_season:
                c.execute('UPDATE tv_airing_events SET active=0 WHERE event_id=?', (old['event_id'],))
            continue
        episode, sn, en = hit
        aired = str(episode.get('air_date') or '')
        try:
            active = int(sn > 0 and en > 0 and date.fromisoformat(aired) <= today)
        except ValueError:
            active = 0
        c.execute('UPDATE tv_airing_events SET season=?,episode=?,title=?,air_date=?,active=?'
                  ' WHERE event_id=?', (sn, en, str(episode.get('name') or ''), aired,
                                       active, old['event_id']))


def save_snapshot(tid, owner, now, next_at, detail, seasons, events):
    with _lock, _conn() as c:
        c.execute('BEGIN IMMEDIATE')
        if not _eligible(c, tid):
            c.execute('UPDATE tv_airing_snapshots SET lease_owner="",lease_until=0'
                      ' WHERE tmdb_id=? AND lease_owner=?', (int(tid), owner))
            return False
        changed = c.execute(
            'UPDATE tv_airing_snapshots SET payload=?, checked_at=?, next_check_at=?, '
            'baseline_at=CASE WHEN baseline_at=0 THEN ? ELSE baseline_at END,'
            'error="",lease_owner="",lease_until=0'
            ' WHERE tmdb_id=? AND lease_owner=? AND lease_until>?',
            (json.dumps(detail, ensure_ascii=False), now, next_at, now, int(tid), owner, now))
        if changed.rowcount != 1:
            return False
        # Update only the status mirror used by existing wall filters. Local identity,
        # season offsets, metadata fetch timestamps and progress are untouched.
        c.execute('UPDATE tv_shows SET status=?, last_air_date=? WHERE tmdb_id=?'
                  ' AND COALESCE(needs_review,0)=0',
                  (str(detail.get('status') or ''), str(detail.get('last_air_date') or ''),
                   int(tid)))
        for sn in sorted(set(seasons)):
            c.execute('INSERT INTO tv_airing_catalogs(tmdb_id,season,next_check_at,pending,revision)'
                      ' VALUES(?,?,?,1,1) ON CONFLICT(tmdb_id,season) DO UPDATE SET'
                      ' pending=1, revision=revision+1, next_check_at=CASE'
                      " WHEN error<>'' AND next_check_at>? THEN next_check_at ELSE ? END",
                      (int(tid), int(sn), now, now, now))
        _reconcile_events(c, tid, [detail.get('last_episode_to_air')], now)
        _add_events(c, events)
        return True


def fail_snapshot(tid, owner, now, retry_at, error):
    with _lock, _conn() as c:
        c.execute('UPDATE tv_airing_snapshots SET error=?,next_check_at=?,lease_owner="",'
                  'lease_until=0 WHERE tmdb_id=? AND lease_owner=? AND lease_until>?',
                  (error, retry_at, int(tid), owner, now))


def catalog(tid, season):
    with _lock, _conn() as c:
        return _decode(c.execute('SELECT * FROM tv_airing_catalogs WHERE tmdb_id=? AND season=?',
                                 (int(tid), int(season))).fetchone())


def cached_catalogs(tid):
    with _lock, _conn() as c:
        rows = c.execute('SELECT * FROM tv_airing_catalogs WHERE tmdb_id=? AND checked_at>0',
                         (int(tid),)).fetchall()
    return {int(row['season']): _decode(row)['detail'] for row in rows}


def pending_catalogs(now):
    with _lock, _conn() as c:
        return [(int(r[0]), int(r[1])) for r in c.execute(
            'SELECT tmdb_id,season FROM tv_airing_catalogs WHERE pending=1'
            ' AND next_check_at<=? AND lease_until<=? AND tmdb_id IN (' + _ELIGIBLE + ')'
            ' ORDER BY next_check_at,tmdb_id,season', (now, now))]


def claim_catalog(tid, season, owner, now, ttl):
    with _lock, _conn() as c:
        c.execute('BEGIN IMMEDIATE')
        if not _eligible(c, tid):
            return None
        c.execute('INSERT OR IGNORE INTO tv_airing_catalogs(tmdb_id,season) VALUES(?,?)',
                  (int(tid), int(season)))
        result = c.execute('UPDATE tv_airing_catalogs SET lease_owner=?,lease_until=?'
                           ' WHERE tmdb_id=? AND season=? AND lease_until<=? AND next_check_at<=?',
                           (owner, now + ttl, int(tid), int(season), now, now))
        if result.rowcount != 1:
            return None
        return int(c.execute('SELECT revision FROM tv_airing_catalogs WHERE tmdb_id=? AND season=?',
                             (int(tid), int(season))).fetchone()[0])


def save_catalog(tid, season, owner, revision, now, next_at, detail, events):
    with _lock, _conn() as c:
        c.execute('BEGIN IMMEDIATE')
        if not _eligible(c, tid):
            return False
        changed = c.execute('UPDATE tv_airing_catalogs SET payload=?,checked_at=?,next_check_at=?, '
                            'pending=0,error="",lease_owner="",lease_until=0'
                            ' WHERE tmdb_id=? AND season=? AND lease_owner=?'
                            ' AND lease_until>? AND revision=?',
                            (json.dumps(detail, ensure_ascii=False), now, next_at, int(tid),
                             int(season), owner, now, revision))
        if changed.rowcount == 1:
            _reconcile_events(c, tid, detail.get('episodes') or [], now, int(season))
            _add_events(c, events)
            return True
        # A newer snapshot invalidated this response while it was in flight.
        c.execute('UPDATE tv_airing_catalogs SET lease_owner="",lease_until=0'
                  ' WHERE tmdb_id=? AND season=? AND lease_owner=?',
                  (int(tid), int(season), owner))
        return False


def fail_catalog(tid, season, owner, now, retry_at, error):
    with _lock, _conn() as c:
        c.execute('UPDATE tv_airing_catalogs SET error=?,next_check_at=?,pending=1,'
                  'lease_owner="",lease_until=0 WHERE tmdb_id=? AND season=?'
                  ' AND lease_owner=? AND lease_until>?',
                  (error, retry_at, int(tid), int(season), owner, now))


def control():
    with _lock, _conn() as c:
        row = c.execute('SELECT * FROM tv_airing_control WHERE id=1').fetchone()
        return dict(row) if row else {}


def set_control(fingerprint, retry_at=0, error=''):
    with _lock, _conn() as c:
        c.execute('INSERT INTO tv_airing_control(id,fingerprint,retry_at,error) VALUES(1,?,?,?)'
                  ' ON CONFLICT(id) DO UPDATE SET fingerprint=excluded.fingerprint,'
                  'retry_at=excluded.retry_at,error=excluded.error', (fingerprint, retry_at, error))


def reset_configuration_error(error, now):
    if not error:
        return
    with _lock, _conn() as c:
        for table in ('tv_airing_snapshots', 'tv_airing_catalogs'):
            c.execute(f'UPDATE {table} SET next_check_at=?,error=""'
                      ' WHERE error=? AND lease_until<=?', (now, error, now))


def list_events(tids, min_date):
    selected = list(dict.fromkeys(int(tid) for tid in tids))
    if not selected:
        return []
    out = []
    with _lock, _conn() as c:
        for offset in range(0, len(selected), 500):
            chunk = selected[offset:offset + 500]
            marks = ','.join('?' for _ in chunk)
            out.extend(dict(r) for r in c.execute(
                f'SELECT * FROM tv_airing_events WHERE active=1 AND tmdb_id IN ({marks}) AND air_date>=?',
                (*chunk, min_date)))
    return sorted(out, key=lambda r: (r['air_date'], r['season'], r['episode']), reverse=True)


def prune_events(min_date):
    cutoff = int(datetime.combine(date.fromisoformat(min_date), datetime.min.time(), timezone.utc).timestamp())
    with _lock, _conn() as c:
        c.execute('DELETE FROM tv_airing_events WHERE (active=1 AND air_date<?)'
                  ' OR (active=0 AND discovered_at<?)', (min_date, cutoff))


def summary(now):
    with _lock, _conn() as c:
        rows = c.execute('SELECT e.tmdb_id, a.checked_at,a.next_check_at,a.error,a.lease_until'
                         ' FROM (' + _ELIGIBLE + ') e LEFT JOIN tv_airing_snapshots a'
                         ' ON a.tmdb_id=e.tmdb_id').fetchall()
        catalogs = c.execute('SELECT tmdb_id,pending,next_check_at,error,lease_until'
                             ' FROM tv_airing_catalogs WHERE tmdb_id IN (' + _ELIGIBLE + ')').fetchall()
    checked = [int(r['checked_at'] or 0) for r in rows]
    next_times = [int(r['next_check_at'] or 0) for r in rows]
    next_times.extend(int(r['next_check_at'] or 0) for r in catalogs if r['pending'])
    pending_ids = {r['tmdb_id'] for r in rows if int(r['next_check_at'] or 0) <= now}
    pending_ids.update(r['tmdb_id'] for r in catalogs if r['pending'] and r['next_check_at'] <= now)
    failed_ids = {r['tmdb_id'] for r in rows if r['error']}
    failed_ids.update(r['tmdb_id'] for r in catalogs if r['error'])
    return dict(total=len(rows), checked=sum(bool(n) for n in checked),
                pending=len(pending_ids), failed=len(failed_ids),
                catalog_pending=sum(bool(r['pending']) for r in catalogs),
                catalog_failed=sum(bool(r['error']) for r in catalogs),
                last_checked_at=max(checked, default=0),
                next_check_at=min(next_times, default=0),
                running=any(int(r['lease_until'] or 0) > now for r in [*rows, *catalogs]))
