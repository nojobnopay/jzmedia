"""Durable TV ingestion reviews; background job progress remains in jobkit."""
import json
import time
import uuid

from ._base import _conn, _lock

__all__ = []  # Exported as store.tv_ingest, avoiding generic facade names.


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _decode(row):
    if row is None:
        return None
    out = dict(row)
    for key in ('plan', 'metadata', 'params', 'payload', 'consumed'):
        if key in out:
            out[key] = json.loads(out[key])
    return out


def get_review(show_id):
    with _lock, _conn() as c:
        return _decode(c.execute('SELECT * FROM tv_ingestion_reviews WHERE show_id=?',
                                 (int(show_id),)).fetchone())


def mark_dirty(show_id):
    with _lock, _conn() as c:
        c.execute('INSERT INTO tv_ingestion_reviews(show_id,library_id)'
                  ' SELECT id,library_id FROM tv_shows WHERE id=?'
                  ' ON CONFLICT(show_id) DO UPDATE SET dirty=1,'
                  " state=CASE WHEN state='clean' THEN 'pending' ELSE state END,"
                  ' dirty_revision=dirty_revision+1', (int(show_id),))


def save_review(show_id, library_id, fingerprint, preview_id, plan, metadata, params,
                actionable, dirty_revision, retry_at=0, incomplete=False):
    now = int(time.time())
    with _lock, _conn() as c:
        before = c.execute('SELECT * FROM tv_ingestion_reviews WHERE show_id=?',
                           (int(show_id),)).fetchone()
        state = 'pending' if actionable else 'clean'
        if before and before['fingerprint'] == fingerprint and actionable:
            state = before['state'] if before['state'] in ('deferred', 'ignored') else state
        dirty = int(bool(incomplete) or bool(before and before['dirty_revision'] != dirty_revision))
        c.execute('INSERT INTO tv_ingestion_reviews(show_id,library_id,dirty,dirty_revision,'
                  'fingerprint,preview_id,state,plan,metadata,params,checked_at,retry_at)'
                  ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(show_id) DO UPDATE SET'
                  ' library_id=excluded.library_id,dirty=excluded.dirty,'
                  ' fingerprint=excluded.fingerprint,preview_id=excluded.preview_id,'
                  ' state=excluded.state,plan=excluded.plan,metadata=excluded.metadata,'
                  ' params=excluded.params,checked_at=excluded.checked_at,retry_at=excluded.retry_at',
                  (int(show_id), int(library_id), dirty, int(dirty_revision), fingerprint,
                   preview_id, state, _json(plan), _json(metadata), _json(params), now, int(retry_at)))
    return state


def list_reviews(library_ids=None, show_id=None, include_ignored=False):
    conditions, args = [], []
    if library_ids is not None:
        lids = sorted(set(int(x) for x in library_ids))
        if not lids:
            return []
        conditions.append('r.library_id IN (' + ','.join('?' for _ in lids) + ')')
        args.extend(lids)
    if show_id is not None:
        conditions.append('r.show_id=?')
        args.append(int(show_id))
    conditions.append("(r.state IN ('pending','deferred','ignored') OR r.dirty=1)" if include_ignored
                      else "(r.state IN ('pending','deferred') OR r.dirty=1)")
    with _lock, _conn() as c:
        return [_decode(r) for r in c.execute(
            'SELECT r.*,s.title FROM tv_ingestion_reviews r JOIN tv_shows s ON s.id=r.show_id'
            ' WHERE ' + ' AND '.join(conditions) + ' ORDER BY r.checked_at DESC,r.show_id', args)]


def save_preview(payload, preview_id=None):
    pid, now = preview_id or uuid.uuid4().hex, int(time.time())
    with _lock, _conn() as c:
        # Reviews retain their referenced snapshot even when deferred for months.
        c.execute('DELETE FROM tv_ingestion_previews WHERE created_at<? AND id NOT IN'
                  ' (SELECT preview_id FROM tv_ingestion_reviews)', (now - 30 * 86400,))
        c.execute('INSERT INTO tv_ingestion_previews(id,payload,created_at) VALUES(?,?,?)',
                  (pid, _json(payload), now))
    return pid


def get_preview(preview_id):
    with _lock, _conn() as c:
        row = c.execute('SELECT * FROM tv_ingestion_previews WHERE id=?', (str(preview_id),)).fetchone()
    if row is None:
        raise ValueError('预览已失效，请重新检查入库整理')
    return _decode(row)


def decide(preview_id, ids, decision):
    if decision not in ('deferred', 'ignored'):
        raise ValueError('不支持的待办操作')
    with _lock, _conn() as c:
        record = c.execute('SELECT payload FROM tv_ingestion_previews WHERE id=?',
                           (str(preview_id),)).fetchone()
        if not record:
            raise ValueError('预览已失效，请重新检查入库整理')
        payload = json.loads(record['payload'])
        versions = payload.get('fingerprints') or {}
        selected = sorted(set(int(x) for x in (ids if ids is not None else versions)))
        if not selected or any(str(sid) not in versions for sid in selected):
            raise ValueError('所选剧不属于本次预览')
        for sid in selected:
            row = c.execute('SELECT fingerprint FROM tv_ingestion_reviews WHERE show_id=?', (sid,)).fetchone()
            if not row or row['fingerprint'] != versions[str(sid)]:
                raise ValueError('整理建议已变化，请重新检查')
        for sid in selected:
            c.execute('UPDATE tv_ingestion_reviews SET state=? WHERE show_id=?', (decision, sid))
    return selected


def mark_executed(preview_id, ids=None):
    with _lock, _conn() as c:
        record = c.execute('SELECT * FROM tv_ingestion_previews WHERE id=?', (str(preview_id),)).fetchone()
        if not record:
            return
        payload = json.loads(record['payload'])
        versions = payload.get('fingerprints') or {}
        selected = set(int(x) for x in (ids if ids is not None else versions))
        consumed = set(json.loads(record['consumed'])) | selected
        c.execute('UPDATE tv_ingestion_previews SET consumed=? WHERE id=?',
                  (_json(sorted(consumed)), str(preview_id)))
        for sid in selected:
            review = c.execute('SELECT plan,metadata FROM tv_ingestion_reviews WHERE show_id=?'
                               ' AND fingerprint=?', (sid, versions.get(str(sid), ''))).fetchone()
            if not review:
                continue
            plan, metadata = json.loads(review['plan']), json.loads(review['metadata'])
            unresolved = bool(metadata.get('issues') or plan.get('manual') or plan.get('manual_more')
                              or plan.get('conflicts') or plan.get('warnings') or plan.get('blocked'))
            c.execute('UPDATE tv_ingestion_reviews SET state=?,dirty=? WHERE show_id=? AND fingerprint=?',
                      ('pending' if unresolved else 'clean', int(unresolved), sid, versions.get(str(sid), '')))


def metadata_check(tmdb_id, season):
    with _lock, _conn() as c:
        row = c.execute('SELECT * FROM tv_ingestion_metadata_checks WHERE tmdb_id=? AND season=?',
                        (int(tmdb_id), int(season))).fetchone()
    return dict(row) if row else None


def save_metadata_check(tmdb_id, season, missing_key, status, retry_at):
    with _lock, _conn() as c:
        c.execute('INSERT INTO tv_ingestion_metadata_checks VALUES(?,?,?,?,?)'
                  ' ON CONFLICT(tmdb_id,season) DO UPDATE SET missing_key=excluded.missing_key,'
                  ' status=excluded.status,retry_at=excluded.retry_at',
                  (int(tmdb_id), int(season), missing_key, status, int(retry_at)))
