"""Persistent directory rules and transactional, reversible TV ownership changes."""
import hashlib
import json
import time
import uuid

from ._base import _conn, _lock
from ..tv_binding_rules import binding_for, contains

__all__ = ['list_tv_bindings', 'tv_binding_for', 'tv_binding_snapshot',
           'save_tv_binding_preview', 'get_tv_binding_plan', 'apply_tv_binding_plan',
           'undo_tv_binding_plan', 'list_tv_binding_history', 'repath_tv_bindings',
           'tv_binding_digest']

# Playback state is intentionally excluded: watching during a preview/after a
# change must neither invalidate it nor be rolled back by undo.
_EP_KEYS = ('id', 'file_path', 'show_id', 'season', 'episode', 'episode_end',
            'absolute_number', 'match_source', 'local_only', 'binding_conflict',
            'tmdb_episode_id')
_META_KEYS = ('season', 'episode', 'episode_end', 'absolute_number', 'tmdb_episode_id',
              'title', 'overview', 'still_path', 'air_date', 'runtime', 'tmdb_rating',
              'episode_credits', 'local_only', 'needs_review', 'match_source',
              'binding_conflict')
_EMPTY_META = dict(tmdb_episode_id=None, title='', overview='', still_path='',
                   air_date='', runtime=0, tmdb_rating=None, episode_credits='{}',
                   local_only=0, needs_review=1, match_source='', binding_conflict=0)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def list_tv_bindings(library_id=None, show_id=None):
    cond, args = [], []
    for key, val in (('library_id', library_id), ('show_id', show_id)):
        if val is not None:
            cond.append(f'{key}=?')
            args.append(int(val))
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute('SELECT * FROM tv_directory_bindings'
                + (' WHERE ' + ' AND '.join(cond) if cond else '') + ' ORDER BY path', args)]


def tv_binding_for(library_id, path):
    return binding_for(path, list_tv_bindings(library_id))


def _snapshot(c, library_id, paths):
    def under(path):
        return any(contains(p, path) for p in paths)
    eps = [dict(r) for r in c.execute('SELECT * FROM tv_episodes WHERE library_id=? ORDER BY id',
                                    (library_id,)) if under(r['file_path'])]
    extras = [dict(r) for r in c.execute('SELECT * FROM extras WHERE library_id=? ORDER BY id',
                                       (library_id,)) if under(r['file_path'])]
    rules = [dict(r) for r in c.execute('SELECT * FROM tv_directory_bindings WHERE library_id=?'
                                      ' ORDER BY path', (library_id,))
             if under(r['path']) or any(contains(r['path'], p) for p in paths)]
    ids = {r['show_id'] for r in eps + extras + rules if r.get('show_id')}
    shows = [dict(r) for r in c.execute('SELECT * FROM tv_shows WHERE library_id=? ORDER BY id',
                                      (library_id,)) if r['id'] in ids]
    seasons = [dict(r) for r in c.execute('SELECT * FROM tv_seasons WHERE library_id=? ORDER BY id',
                                        (library_id,)) if r['show_id'] in ids]
    return dict(episodes=eps, extras=extras, rules=rules, shows=shows, seasons=seasons)


def tv_binding_snapshot(library_id, paths):
    with _lock, _conn() as c:
        return _snapshot(c, int(library_id), paths)


def tv_binding_digest(snapshot):
    obj = {'episodes': [{k: r.get(k) for k in _EP_KEYS} for r in snapshot['episodes']],
           'extras': [{k: r.get(k) for k in ('id', 'file_path', 'show_id')} for r in snapshot['extras']],
           'rules': snapshot['rules'],
           'shows': [{k: r.get(k) for k in ('id', 'tmdb_id', 'title', 'year')}
                     for r in snapshot['shows']]}
    return hashlib.sha256(_json(obj).encode()).hexdigest()


def save_tv_binding_preview(payload):
    token, now = uuid.uuid4().hex, int(time.time())
    with _lock, _conn() as c:
        c.execute("DELETE FROM tv_binding_history WHERE state='preview' AND created_at<?", (now-3600,))
        c.execute('INSERT INTO tv_binding_history VALUES(?,?,?,?,?,?)',
                  (token, payload['library_id'], 'preview', _json(payload), now, now))
    return token


def get_tv_binding_plan(token):
    with _lock, _conn() as c:
        row = c.execute('SELECT * FROM tv_binding_history WHERE id=?', (token,)).fetchone()
    if not row:
        raise ValueError('预览或历史不存在，请重新预览')
    out = dict(row)
    out['payload'] = json.loads(out['payload'])
    return out


def _insert(c, table, values):
    keys = list(values)
    return c.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                     [values[k] for k in keys]).lastrowid


def _update(c, table, rid, values):
    c.execute(f"UPDATE {table} SET {','.join(k+'=?' for k in values)} WHERE id=?",
              [*values.values(), rid])


def _source_show(c, library_id, identity):
    row = c.execute('SELECT id FROM tv_shows WHERE library_id=? AND title=? AND year IS ?'
                    ' ORDER BY id LIMIT 1', (library_id, identity['title'], identity.get('year'))).fetchone()
    if row:
        return int(row['id'])
    now = int(time.time())
    return _insert(c, 'tv_shows', dict(library_id=library_id, title=identity['title'],
                   sort_title=identity['title'], year=identity.get('year'), title_auto=1,
                   added_at=now, updated_at=now))


def _prune(c, library_id, candidates):
    ids = [r[0] for r in c.execute('SELECT id FROM tv_shows WHERE library_id=?'
           ' AND id NOT IN (SELECT show_id FROM tv_episodes)'
           ' AND id NOT IN (SELECT show_id FROM extras WHERE show_id IS NOT NULL)'
           ' AND id NOT IN (SELECT show_id FROM tv_directory_bindings)', (library_id,))]
    for sid in set(ids) & set(candidates):
        c.execute('DELETE FROM tv_seasons WHERE show_id=?', (sid,))
        c.execute('DELETE FROM tv_shows WHERE id=?', (sid,))


def apply_tv_binding_plan(token):
    with _lock, _conn() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM tv_binding_history WHERE id=?', (token,)).fetchone()
        if not row:
            raise ValueError('预览不存在，请重新预览')
        p = json.loads(row['payload'])
        if row['state'] == 'applied':
            return p['result']
        if row['state'] != 'preview' or int(time.time()) - row['created_at'] > 900:
            raise ValueError('预览已失效，请重新预览')
        lid, paths = p['library_id'], [r['path'] for r in p['rules']]
        current = _snapshot(c, lid, paths)
        if tv_binding_digest(current) != p['snapshot_digest']:
            raise ValueError('分集或归属已变化，请重新预览')
        target = p['target']
        target_id = target.get('show_id')
        if target_id:
            dest = c.execute('SELECT * FROM tv_shows WHERE id=? AND library_id=?', (target_id, lid)).fetchone()
            expected_tmdb = None if target.get('rebind') else target.get('tmdb_id')
            if not dest or dest['tmdb_id'] != expected_tmdb:
                raise ValueError('目标剧集已变化，请重新预览')
        else:
            conflict = c.execute('SELECT id FROM tv_shows WHERE library_id=? AND '
                                 '(tmdb_id=? OR (title=? AND year IS ?))',
                                 (lid, target['tmdb_id'], target['title'], target.get('year'))).fetchone()
            if conflict:
                raise ValueError('目标剧集已经存在，请重新预览并选择已有剧集')
        if target_id:
            signature = [{k: r[k] for k in _EP_KEYS} for r in c.execute(
                'SELECT * FROM tv_episodes WHERE show_id=? ORDER BY id', (target_id,))]
            if signature != p['target_signature']:
                raise ValueError('目标剧集的分集已变化，请重新预览')
            if target.get('rebind'):
                refs = [r[0] for r in c.execute('SELECT file_path FROM extras WHERE show_id=?', (target_id,))]
                refs += [r[0] for r in c.execute('SELECT path FROM tv_directory_bindings WHERE show_id=?', (target_id,))]
                if any(not any(contains(path, ref) for path in paths) for ref in refs):
                    raise ValueError('目标剧集目录已变化，请重新预览')
        # Register newly discovered files with their original directory identity
        # first, in the same transaction. Undo can then restore ownership without
        # deleting their IDs or any playback state accumulated afterwards.
        existing = {e['file_path']: e for e in current['episodes']}
        now = int(time.time())
        for item in p['items']:
            if item['file_path'] in existing:
                continue
            source = _source_show(c, lid, item['source_show'])
            _insert(c, 'tv_episodes', dict(library_id=lid, show_id=source,
                    file_path=item['file_path'], **item['original'], added_at=now, updated_at=now))
        before = _snapshot(c, lid, paths)
        if target.get('rebind'):
            _update(c, 'tv_shows', target_id, dict(**p['show_fields'], title_auto=0,
                    match_source='directory', updated_at=now, fetched_at=now))
        if not target_id:
            # A newly registered source may already have the canonical title.
            staged = c.execute('SELECT id FROM tv_shows WHERE library_id=? AND title=? AND year IS ?',
                               (lid, target['title'], target.get('year'))).fetchone()
            fields = dict(**p['show_fields'], title_auto=0, match_source='directory',
                          updated_at=now, fetched_at=now)
            if staged:
                target_id = staged['id']
                _update(c, 'tv_shows', target_id, fields)
            else:
                target_id = _insert(c, 'tv_shows', dict(library_id=lid, added_at=now, **fields))
        for item in p['items']:
            ep = c.execute('SELECT * FROM tv_episodes WHERE library_id=? AND file_path=?',
                           (lid, item['file_path'])).fetchone()
            changed = (ep['show_id'] != target_id or any(ep[k] != item['numbers'][k]
                       for k in ('season', 'episode', 'episode_end', 'absolute_number')))
            protected = ep['local_only'] or ep['match_source'] == 'manual'
            fields = dict(show_id=target_id, **item['numbers'], binding_conflict=0, updated_at=now)
            if changed or not protected:
                fields.update(_EMPTY_META)
                fields.update(item['metadata'])
            _update(c, 'tv_episodes', ep['id'], fields)
            c.execute('DELETE FROM scan_state WHERE library_id=? AND file_path=?', (lid, item['file_path']))
        for extra in before['extras']:
            _update(c, 'extras', extra['id'], dict(show_id=target_id, updated_at=now))
        for rule in p['rules']:
            c.execute('INSERT INTO tv_directory_bindings VALUES(?,?,?,?,?,?)'
                      ' ON CONFLICT(library_id,path) DO UPDATE SET show_id=excluded.show_id,'
                      ' season=excluded.season,override_season=excluded.override_season,'
                      ' updated_at=excluded.updated_at',
                      (lid, rule['path'], target_id, rule['season'], int(rule['override_season']), now))
        for fields in p['season_fields']:
            old = c.execute('SELECT id FROM tv_seasons WHERE show_id=? AND season=?',
                            (target_id, fields['season'])).fetchone()
            if old:
                _update(c, 'tv_seasons', old['id'], dict(**fields, updated_at=now))
            else:
                _insert(c, 'tv_seasons', dict(show_id=target_id, library_id=lid, **fields, updated_at=now))
        _prune(c, lid, [s['id'] for s in before['shows']])
        result = dict(ok=True, show_id=target_id, history_id=token, episodes=len(p['items']),
                      directories=len(paths), extras=len(before['extras']))
        p.update(before=before, after_digest=tv_binding_digest(_snapshot(c, lid, paths)), result=result)
        c.execute("UPDATE tv_binding_history SET state='applied',payload=?,updated_at=? WHERE id=?",
                  (_json(p), now, token))
        return result


def undo_tv_binding_plan(token, dry_run=True):
    with _lock, _conn() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM tv_binding_history WHERE id=?', (token,)).fetchone()
        if not row or row['state'] != 'applied':
            raise ValueError('这条归属变更已撤销或不存在')
        p = json.loads(row['payload'])
        lid, paths = p['library_id'], [r['path'] for r in p['rules']]
        if tv_binding_digest(_snapshot(c, lid, paths)) != p['after_digest']:
            raise ValueError('确认后已有新增分集、路径或归属变化，请重新设置归属；不能直接撤销旧记录')
        result = dict(ok=True, directories=paths, episodes=len(p['before']['episodes']),
                      title=p['target']['title'],
                      show_id=p['before']['episodes'][0]['show_id'] if p['before']['episodes'] else None)
        if dry_run:
            return result
        before = p['before']
        for show in before['shows']:
            if not c.execute('SELECT id FROM tv_shows WHERE id=?', (show['id'],)).fetchone():
                clash = c.execute('SELECT id FROM tv_shows WHERE library_id=? AND title=? AND year IS ?',
                                  (lid, show['title'], show['year'])).fetchone()
                if clash:
                    raise ValueError('原剧名已有其他记录占用，请重新设置归属')
                _insert(c, 'tv_shows', show)
                for season in before['seasons']:
                    if season['show_id'] == show['id']:
                        _insert(c, 'tv_seasons', {k: v for k, v in season.items() if k != 'id'})
        for show in before['shows']:
            if show['id'] == p['result']['show_id'] and show.get('tmdb_id') != p['target']['tmdb_id']:
                _update(c, 'tv_shows', show['id'], {k: v for k, v in show.items()
                        if k not in ('id', 'watched', 'watched_at', 'added_at')})
                c.execute('DELETE FROM tv_seasons WHERE show_id=?', (show['id'],))
                for season in before['seasons']:
                    if season['show_id'] == show['id']:
                        _insert(c, 'tv_seasons', {k: v for k, v in season.items() if k != 'id'})
        for ep in before['episodes']:
            _update(c, 'tv_episodes', ep['id'], dict(show_id=ep['show_id'],
                    **{k: ep[k] for k in _META_KEYS}, updated_at=int(time.time())))
            c.execute('DELETE FROM scan_state WHERE library_id=? AND file_path=?', (lid, ep['file_path']))
        for extra in before['extras']:
            _update(c, 'extras', extra['id'], dict(show_id=extra['show_id']))
        for path in paths:
            c.execute('DELETE FROM tv_directory_bindings WHERE library_id=? AND path=?', (lid, path))
        for rule in before['rules']:
            c.execute('INSERT OR REPLACE INTO tv_directory_bindings VALUES(?,?,?,?,?,?)',
                      tuple(rule[k] for k in ('library_id','path','show_id','season','override_season','updated_at')))
        _prune(c, lid, [p['result']['show_id']])
        c.execute("UPDATE tv_binding_history SET state='undone',updated_at=? WHERE id=?",
                  (int(time.time()), token))
        return result


def list_tv_binding_history(library_id):
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM tv_binding_history WHERE library_id=? AND state!='preview'"
                         ' ORDER BY created_at DESC,id DESC LIMIT 30', (int(library_id),)).fetchall()
    return [dict(id=r['id'], state=r['state'], created_at=r['created_at'],
                 title=(p := json.loads(r['payload']))['target']['title'],
                 directories=[d['path'] for d in p['rules']], episodes=len(p['items'])) for r in rows]


def repath_tv_bindings(c, library_id, old, new):
    rows = c.execute('SELECT * FROM tv_directory_bindings WHERE library_id=?', (library_id,)).fetchall()
    for r in rows:
        if contains(old, r['path']):
            c.execute('UPDATE tv_directory_bindings SET path=?,updated_at=? WHERE library_id=? AND path=?',
                      (new+r['path'][len(old):], int(time.time()), library_id, r['path']))
