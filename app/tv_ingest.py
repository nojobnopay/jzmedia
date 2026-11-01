"""One TV ingestion pipeline: discover, fill metadata, persist policy, preview.

Network waits use operation locks, never SQLite or filesystem mutation locks.
Physical changes remain the organiser's explicit, version-checked operation.
"""
from contextlib import contextmanager
import hashlib
import json
import os
import threading
import time
import uuid

from . import config, scanner, storage, store, tmdb
from .library_mutex import library_mutation_lock
from .metadata import state as provider_state
from .scanner import tv_match, tv_organize, tv_persist

DAY = 86400
_scope_guard = threading.Lock()
_scope_locks = {}


class IngestionCancelled(RuntimeError):
    """A queued or running ingestion operation was cooperatively cancelled."""


def _stop(should_stop):
    if should_stop and should_stop():
        raise IngestionCancelled('入库检查已取消')


@contextmanager
def operation_scope(library_ids, should_stop=None):
    """Reentrant per-library coordination shared by old and new operations."""
    with _scope_guard:
        locks = [_scope_locks.setdefault(int(lid), threading.RLock())
                 for lid in sorted(set(library_ids or []))]
    acquired = []
    try:
        for lock in locks:
            while not lock.acquire(timeout=0.1):
                _stop(should_stop)
            acquired.append(lock)
            _stop(should_stop)
        yield
    finally:
        for lock in reversed(acquired):
            lock.release()


def mark_dirty(show_id):
    store.tv_ingest.mark_dirty(show_id)


def mark_executed(preview_id, ids=None):
    store.tv_ingest.mark_executed(preview_id, ids)


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest()


def resolve_scope(ids=None, library_id=None, media_library_id=None, library_ids=None):
    """Strictly resolve TV scope; an empty/unknown scope never expands to all."""
    if ids is not None:
        if not ids:
            raise ValueError('请选择至少一部剧')
        shows = [store.get_show_meta(int(sid)) for sid in sorted(set(ids))]
        if any(s is None for s in shows):
            raise ValueError('所选剧集不存在')
        lids = sorted({int(s['library_id']) for s in shows})
    else:
        if library_id is not None:
            lids = [int(library_id)]
        elif media_library_id is not None:
            if not store.get_media_library(int(media_library_id)):
                raise ValueError('媒体库不存在')
            lids = [int(l['id']) for l in store.list_libraries()
                    if int(l.get('media_library_id') or 0) == int(media_library_id)
                    and l.get('kind') == 'tv' and l.get('enabled', 1) and l.get('media_enabled', 1)]
        elif library_ids is not None:
            lids = sorted(set(int(x) for x in library_ids))
        else:
            raise ValueError('请指定剧集或视频库范围')
        shows = store.list_shows_for_scrape(library_ids=lids, force=True) if lids else []
    for lid in lids:
        lib = store.get_library(lid)
        if not lib or lib.get('kind') != 'tv':
            raise ValueError('请选择剧集视频库')
        if not lib.get('enabled', 1) or not lib.get('media_enabled', 1):
            raise ValueError('视频库已停用')
    return sorted(shows, key=lambda s: int(s['id'])), lids


def _issue(code, message, **extra):
    return {'code': code, 'message': message, **extra}


def _protected(episode):
    return bool(episode.get('local_only') or episode.get('binding_conflict')
                or episode.get('match_source') == 'manual')


def _episode_signature(e):
    return [e.get(k) for k in ('id', 'show_id', 'file_path', 'season', 'episode',
            'episode_end', 'tmdb_episode_id', 'local_only', 'binding_conflict', 'match_source')]


def _needed(episode):
    return not _protected(episode) and (not episode.get('tmdb_episode_id')
                                      or not str(episode.get('title') or '').strip())


def _series_fields(show, detail, source='tmdb', review=0):
    """Fill series gaps without re-numbering or applying all existing episodes."""
    cached_meta = detail.get('_ingest_cached_meta')
    meta = cached_meta if isinstance(cached_meta, dict) else tv_match.tv_meta_from_detail(detail)
    tid = int(detail['id'])
    cached = store.get_tmdb_cached(tid, 'tv') or {}
    credits = cached.get('credits') or tv_match.tv_credits(detail)
    if not cached_meta:
        store.upsert_tmdb_cache(tid, meta, credits, meta.get('poster_tmdb_path') or '', media_type='tv')
    fields = {}
    for name in ('original_title', 'year', 'overview', 'imdb_id', 'tvdb_id',
                 'tmdb_rating', 'genres', 'genre_ids', 'origin_country', 'origin_countries',
                 'original_language', 'region', 'status', 'first_air_date', 'last_air_date',
                 'number_of_seasons', 'number_of_episodes', 'episode_run_time', 'networks', 'created_by'):
        if not show.get(name) and meta.get(name):
            fields[name] = meta[name]
    if not show.get('tmdb_id'):
        fields.update(tmdb_id=tid, match_source=source, needs_review=int(review))
    from .content_rules import content_fields
    fields.update(content_fields('tv', meta.get('content_evidence'), meta.get('genres'), meta.get('genre_ids')))
    if not show.get('person_names'):
        names = tv_persist.person_names_from_credits(credits, meta.get('created_by'))
        if names:
            fields['person_names'] = names
    if show.get('title_auto') and not show.get('fetched_at') and meta.get('title'):
        from .scanner.parse import normalize_title
        fields.update(title=meta['title'], sort_title=normalize_title(meta['title']), title_auto=0)
    fields['fetched_at'] = int(time.time())
    store.update_show_meta(show['id'], **fields)
    for season in detail.get('seasons') or []:
        _upsert_season(show, season)
    return len([key for key in fields if key != 'fetched_at'])


def _fill_art(show, detail):
    """Application artwork cache only; media-directory policy is applied later."""
    from .db import POSTER_DIR
    tid = int(detail['id'])
    changed = 0
    for field, remote, name, size in (
            ('poster_path', 'poster_path', tv_persist.tv_poster_name(tid), 'w500'),
            ('backdrop_path', 'backdrop_path', tv_persist.tv_backdrop_name(tid), 'w780')):
        if not show.get(field) and detail.get(remote):
            dest = os.path.join(POSTER_DIR, name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if tv_persist._download(detail[remote], dest, size):
                with library_mutation_lock(show['library_id']):
                    current = store.get_show_meta(show['id'])
                    if current and current.get('tmdb_id') == tid and not current.get(field):
                        store.update_show_meta(show['id'], **{field: name})
                        changed += 1
    for season in detail.get('seasons') or []:
        sn = season.get('season_number')
        if sn is None or not season.get('poster_path'):
            continue
        current = store.get_season(show['id'], int(sn)) or {}
        if current.get('poster_path'):
            continue
        name = tv_persist.tv_season_poster_name(tid, int(sn))
        dest = os.path.join(POSTER_DIR, name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if tv_persist._download(season['poster_path'], dest, 'w300'):
            with library_mutation_lock(show['library_id']):
                current = store.get_show_meta(show['id'])
                if current and current.get('tmdb_id') == tid:
                    store.upsert_season(show['id'], show['library_id'], int(sn), poster_path=name)
                    changed += 1
    return changed


def _upsert_season(show, season):
    if season.get('season_number') is None:
        return
    store.upsert_season(show['id'], show['library_id'], int(season['season_number']),
                        name=season.get('name') or '', overview=season.get('overview') or '',
                        air_date=str(season.get('air_date') or '')[:10],
                        episode_count=int(season.get('episode_count') or len(season.get('episodes') or [])),
                        tmdb_season_id=season.get('id'),
                        cast_json=(json.dumps(tv_match.tv_season_credits(season), ensure_ascii=False)
                                   if isinstance(season.get('credits'), dict) else None))
    current = store.get_show_meta(show['id']) or {}
    if int(season['season_number']) > int(current.get('number_of_seasons') or 0):
        store.update_show_meta(show['id'], number_of_seasons=int(season['season_number']))


def _cached_detail(tid):
    cached = store.get_tmdb_cached(tid, 'tv') or {}
    try:
        meta = json.loads(cached.get('payload_json') or '{}')
    except (ValueError, TypeError):
        return None
    if not meta:
        return None
    # tmdb_cache stores normalized data, not the raw TMDB series response.
    return {'id': tid, 'name': meta.get('title'), 'original_name': meta.get('original_title'),
            'first_air_date': meta.get('first_air_date') or meta.get('premiered'),
            'overview': meta.get('overview'), 'status': meta.get('status'),
            'seasons': meta.get('seasons') or [], 'origin_country': meta.get('origin_countries') or [],
            'poster_path': meta.get('poster_tmdb_path'), 'backdrop_path': meta.get('backdrop_tmdb_path'),
            '_ingest_cached_meta': meta}


def _ensure_series(show, retry_metadata, fetched):
    """Fill missing series data from cache, then fetch only unresolved series gaps."""
    tid, now = int(show['tmdb_id']), int(time.time())
    cached = store.get_tmdb_cached(tid, 'tv') or {}
    try:
        meta = json.loads(cached.get('payload_json') or '{}')
    except (ValueError, TypeError):
        meta = {}
    fields = {k: v for k, v in meta.items() if k in store.TV_META_FIELDS
              and k not in ('title', 'sort_title', 'tmdb_id', 'match_source', 'needs_review',
                            'watched', 'watched_at', 'custom_rating', 'overview_override', 'tags')
              and not show.get(k) and v not in (None, '', [], 0)}
    if fields:
        with library_mutation_lock(show['library_id']):
            current = store.get_show_meta(show['id'])
            if current and current.get('tmdb_id') == tid:
                store.update_show_meta(show['id'], **{k: v for k, v in fields.items() if not current.get(k)})
        show = store.get_show_meta(show['id'])
    art_count = 0
    if _source_available() and (meta.get('poster_tmdb_path') or meta.get('backdrop_tmdb_path')):
        art_count = _fill_art(show, {'id': tid, 'poster_path': meta.get('poster_tmdb_path'),
                                   'backdrop_path': meta.get('backdrop_tmdb_path'),
                                   'seasons': meta.get('seasons') or []})
        show = store.get_show_meta(show['id'])
    missing = [k for k in ('overview', 'status', 'year') if not show.get(k)]
    if not show.get('fetched_at') and not meta:
        missing.append('series_detail')
    if not show.get('poster_path') and 'poster_tmdb_path' not in meta:
        missing.append('artwork')
    if not missing:
        return len(fields) + art_count, None, 0
    key, check = _digest(missing), store.tv_ingest.metadata_check(tid, -1)
    if not retry_metadata and check and check['missing_key'] == key and check['retry_at'] > now:
        return len(fields) + art_count, check['status'], check['retry_at']
    if not _source_available():
        return len(fields) + art_count, 'not_configured', now + 600
    if not provider_state.available('tmdb'):
        return len(fields) + art_count, 'source_unavailable', provider_state.snapshot(['tmdb'])[0]['cooldown_until']
    try:
        if (tid, -1) not in fetched:
            detail = tmdb.tv_detail(tid)
            if not isinstance(detail, dict) or int(detail.get('id') or 0) != tid:
                raise ValueError('剧集资料条目已变化')
            fetched[(tid, -1)] = detail
        detail = fetched[(tid, -1)]
        with library_mutation_lock(show['library_id']):
            current = store.get_show_meta(show['id'])
            if not current or current.get('tmdb_id') != tid or current.get('needs_review'):
                raise ValueError('剧集匹配已变化，请重新检查')
            count = _series_fields(current, detail)
        count += _fill_art(store.get_show_meta(show['id']), detail)
        provider_state.note_ok('tmdb')
        current = store.get_show_meta(show['id'])
        pending = any(not current.get(k) for k in missing if k not in ('series_detail', 'artwork'))
        status, retry_at = ('metadata_pending', now + DAY) if pending else ('complete', 0)
        remaining = [k for k in ('overview', 'status', 'year') if not current.get(k)]
        store.tv_ingest.save_metadata_check(tid, -1, _digest(remaining) if pending else key, status, retry_at)
        return len(fields) + art_count + count, status if pending else None, retry_at
    except Exception as exc:
        retry_at = _source_failure('tmdb', exc)
        store.tv_ingest.save_metadata_check(tid, -1, key, 'source_unavailable', retry_at)
        return len(fields) + art_count, 'source_unavailable', retry_at


def _apply_candidates(show, seasons):
    """Exact matching only, protecting explicit mappings and filled fields."""
    from .tv_binding_rules import binding_for
    index, _ = tv_persist._episode_index(seasons)
    by_id = {int(e['id']): e for e in index.values() if e.get('id')}
    rules = store.list_tv_bindings(show_id=show['id'])
    filled = 0
    for episode in store.list_episodes(show['id']):
        if not _needed(episode):
            continue
        key = (int(episode['season']), int(episode['episode']))
        old_id = episode.get('tmdb_episode_id')
        candidate = by_id.get(int(old_id)) if old_id else index.get(key)
        if (not candidate or not candidate.get('id')
                or not str(candidate.get('name') or '').strip()):
            continue
        if binding_for(episode['file_path'], rules) and any(
                (key[0], n) not in index for n in range(key[1], max(key[1], episode.get('episode_end') or 0) + 1)):
            continue
        if old_id and int(candidate['id']) != int(old_id):
            continue
        fields = {}
        mapping = {'title': 'name', 'overview': 'overview', 'still_path': 'still_path',
                   'air_date': 'air_date', 'runtime': 'runtime', 'tmdb_rating': 'vote_average'}
        for name, source in mapping.items():
            value = candidate.get(source)
            # Filename-derived names are replaced on the first official match.
            if value not in (None, '') and (not old_id or not episode.get(name)):
                fields[name] = str(value).strip() if name == 'title' else value
        if not old_id:
            fields['tmdb_episode_id'] = int(candidate['id'])
            credits = tv_match.tv_episode_credits(candidate)
            if 'guest_stars' in candidate or 'crew' in candidate:
                fields['episode_credits'] = json.dumps(credits, ensure_ascii=False)
        if str(fields.get('title') or episode.get('title') or '').strip():
            fields['needs_review'] = 0
        # Storage/numbering/manual choices can change while the network is pending.
        with library_mutation_lock(show['library_id']):
            current_show = store.get_show_meta(show['id'])
            current = store.get_episode(episode['id'])
            if (not current_show or current_show.get('tmdb_id') != show.get('tmdb_id') or current_show.get('needs_review')
                    or not current or _episode_signature(current) != _episode_signature(episode)
                    or _protected(current)):
                continue
            if fields:
                store.update_episode_meta(episode['id'], **fields)
                filled += 1
    return filled


def _source_available():
    return bool(config.effective_tmdb_read_token() or config.effective_tmdb_api_key())


def _source_failure(source, exc):
    """Keep upstream throttling in the existing persisted provider cooldown."""
    now, retry_at = int(time.time()), 0
    response = getattr(exc, 'response', None)
    if response is not None and response.status_code in (401, 403, 429):
        retry_at = now + 600
        if response.status_code == 429:
            value = response.headers.get('retry-after', '')
            try:
                retry_at = max(retry_at, now + int(value))
            except (TypeError, ValueError):
                from email.utils import parsedate_to_datetime
                try:
                    retry_at = max(retry_at, int(parsedate_to_datetime(value).timestamp()))
                except (TypeError, ValueError, OverflowError):
                    pass
    provider_state.note_fail(source, provider_state.safe_error(exc), retry_at=retry_at)
    return max(now + 600, retry_at)


def _fetch_season(show, season, missing_key, retry_metadata, fetched):
    tid, now = int(show['tmdb_id']), int(time.time())
    key = (tid, season)
    if key in fetched:
        return fetched[key]
    check = store.tv_ingest.metadata_check(tid, season)
    if (not retry_metadata and check and check['missing_key'] == missing_key
            and check['retry_at'] > now):
        return None, check['status'], check['retry_at']
    if not _source_available():
        return None, 'not_configured', now + 600
    if not provider_state.available('tmdb'):
        status = provider_state.snapshot(['tmdb'])[0]
        return None, 'source_unavailable', status['cooldown_until']
    try:
        detail = tmdb.tv_season(tid, season)
        if (not isinstance(detail, dict) or not isinstance(detail.get('episodes'), list)
                or int(detail.get('season_number', season)) != season):
            raise ValueError('资料来源返回无效季资料')
        provider_state.note_ok('tmdb')
        detail = dict(detail, season_number=season)
        fetched[key] = detail, 'ok', 0
    except Exception as exc:
        response = getattr(exc, 'response', None)
        if response is not None and response.status_code == 404:
            provider_state.note_ok('tmdb')
            fetched[key] = None, 'metadata_pending', now + DAY
            store.tv_ingest.save_metadata_check(tid, season, missing_key, 'metadata_pending', now + DAY)
            return fetched[key]
        retry_at = _source_failure('tmdb', exc)
        fetched[key] = None, 'source_unavailable', retry_at
        store.tv_ingest.save_metadata_check(tid, season, missing_key, 'source_unavailable', retry_at)
    return fetched[key]


def _external_fill(show, retry_metadata=False):
    """Reuse the known external identity without upgrading it to another provider."""
    from .content_rules import normalize_content_evidence
    from .metadata import chain, external
    evidence = normalize_content_evidence(show.get('content_evidence'))
    source = show.get('match_source') or evidence.get('source')
    candidate = chain.candidate_for(source, evidence.get('source_id') or '', 'tv')
    result = {'show_id': show['id'], 'filled': 0, 'status': 'external', 'issues': [], 'retry_at': 0}
    missing = [e for e in store.list_episodes(show['id']) if not _protected(e)
               and (not e.get('title') or not e.get('overview'))]
    if not missing:
        return result
    if candidate is None:
        result.update(status='metadata_pending', issues=[_issue('metadata_pending', '已保留外部资料，原资料来源身份尚不完整')])
        return result
    detail = chain.detail_for(candidate, fetch=False)
    key = _digest([(e['season'], e['episode']) for e in missing])
    previous = store.tv_ingest.get_review(show['id']) or {}
    cached_items = {(int(e.get('season') or 0), int(e.get('episode') or 0)): e
                    for e in detail.get('episodes') or []}
    need_refresh = any((int(e['season']), int(e['episode'])) not in cached_items for e in missing)
    if need_refresh and source in chain._DETAILERS:
        if not provider_state.available(source):
            result.update(status='source_unavailable', issues=[_issue('source_unavailable', '原资料来源暂不可用')])
            return result
        previous_keys = [issue.get('missing_key') for issue in previous.get('metadata', {}).get('issues', [])]
        if not retry_metadata and key in previous_keys and previous.get('retry_at', 0) > time.time():
            result.update(status='metadata_pending', issues=[_issue('metadata_pending', '原资料来源尚无新增分集资料')],
                          retry_at=previous['retry_at'])
            return result
        try:
            detail = chain._DETAILERS[source](candidate.source_id)
            if not isinstance(detail, dict) or not detail.get('title'):
                raise ValueError('原资料来源未返回有效资料')
            provider_state.note_ok(source)
            store.upsert_external(source, candidate.source_id, 'tv', title=detail['title'], payload=detail)
        except Exception as exc:
            retry_at = _source_failure(source, exc)
            result.update(status='source_unavailable', issues=[_issue('source_unavailable', '原资料来源请求失败')],
                          retry_at=retry_at)
            return result
    items = {(int(e.get('season') or 0), int(e.get('episode') or 0)): e
             for e in detail.get('episodes') or []}
    filled = 0
    for episode in store.list_episodes(show['id']):
        if _protected(episode):
            continue
        item = items.get((int(episode['season']), int(episode['episode'])))
        if source == 'nfo' and item is None:
            from .metadata import nfo_import
            parsed = nfo_import.read_any_for_backend(storage.backend_for(show['library_id']), episode['file_path'])
            if parsed and parsed.get('_kind') == 'episode':
                item = parsed
        if item:
            with library_mutation_lock(show['library_id']):
                current = store.get_episode(episode['id'])
                if current and not _protected(current) and _episode_signature(current) == _episode_signature(episode):
                    filled += int(external._fill_episode(current, item, source))
    result['filled'] = filled
    remaining = [e for e in store.list_episodes(show['id']) if not _protected(e)
                 and (not e.get('title') or not e.get('overview'))]
    if remaining:
        remaining_key = _digest([(e['season'], e['episode']) for e in remaining])
        result.update(status='metadata_pending', issues=[_issue('metadata_pending', '原资料来源尚无新增分集资料', missing_key=remaining_key)],
                      retry_at=int(time.time()) + DAY)
    return result


def _metadata(show, retry_metadata, fetched, should_stop):
    sid = int(show['id'])
    result = {'show_id': sid, 'filled': 0, 'status': 'complete', 'issues': [], 'retry_at': 0}
    if show.get('needs_review'):
        result.update(status='match_review', issues=[_issue('match_review', '请先核对剧集匹配')])
        return result
    if not show.get('tmdb_id') and show.get('match_source') in ('nfo', 'tvmaze', 'bgm', 'wikidata', 'imdb', 'douban'):
        return _external_fill(show, retry_metadata)
    if not show.get('tmdb_id'):
        # Existing matching gates remain the authority for identifying a new show.
        try:
            resolved = tv_match.resolve_show(show.get('title') or '', show.get('year'),
                                            library_id=show['library_id'])
            if resolved.get('candidate') is not None and not resolved.get('tmdb_id'):
                from .metadata import chain, external
                candidate = resolved['candidate']
                detail = chain.detail_for(candidate)
                protected = any(_protected(e) for e in store.list_episodes(sid))
                out = external.apply_external_show(sid, detail, source=candidate.source,
                    source_id=candidate.source_id, library_id=show['library_id'],
                    needs_review=int(resolved.get('needs_review') or 0),
                    write_nfo=False, fill_episodes=not protected)
                result.update(status='external', filled=out.get('episodes_filled', 0), series_filled=1)
                if resolved.get('needs_review'):
                    result.update(status='match_review', issues=[_issue('match_review', '外部剧集匹配需要核对')])
                return result
            tid = int(resolved.get('tmdb_id') or 0)
            if not tid:
                result.update(status='unmatched', issues=[_issue('unmatched', '尚未找到可靠的剧集资料，请核对匹配')])
                return result
            detail = resolved.get('detail') or _cached_detail(tid)
            if detail is None:
                detail = tmdb.tv_detail(tid)
            with library_mutation_lock(show['library_id']):
                current = store.get_show_meta(sid)
                if not current or current.get('tmdb_id'):
                    raise ValueError('剧集匹配已变化，请重新检查')
                count = _series_fields(current, detail, resolved.get('source') or 'tmdb', resolved.get('needs_review'))
            show = store.get_show_meta(sid)
            result['series_filled'] = count + (_fill_art(show, detail) if _source_available() else 0)
            if show.get('needs_review'):
                result.update(status='match_review', issues=[_issue('match_review', '剧集匹配需要核对')])
                return result
        except Exception as exc:
            result.update(status='source_unavailable', issues=[_issue('source_unavailable', provider_state.safe_error(exc))],
                          retry_at=int(time.time()) + 600)
            return result
    tid = int(show['tmdb_id'])
    count, series_status, retry_at = _ensure_series(show, retry_metadata, fetched)
    result['series_filled'] = count + result.get('series_filled', 0)
    if series_status:
        result.update(status=series_status, retry_at=retry_at)
        result['issues'].append(_issue(series_status, '剧集资料尚未补齐，可稍后重试'))
    seasons = store.get_tmdb_cache_seasons(tid)
    try:
        from . import tv_airing
        seasons.update(tv_airing.get_cached_catalogs(tid))
    except (TypeError, ValueError):
        pass
    result['filled'] += _apply_candidates(show, seasons)
    missing = [e for e in store.list_episodes(sid) if _needed(e)]
    wanted = sorted({int(e['season']) for e in missing})
    for sn in wanted:
        _stop(should_stop)
        items = [e for e in missing if int(e['season']) == sn]
        missing_key = _digest(sorted({(int(e['episode']), int(e.get('episode_end') or 0),
                                       int(e.get('tmdb_episode_id') or 0)) for e in items}))
        detail, status, retry_at = _fetch_season(show, sn, missing_key, retry_metadata, fetched)
        if detail:
            if not store.get_tmdb_cached(tid, 'tv'):
                store.upsert_tmdb_cache(tid, {'title': show.get('title'), 'tmdb_id': tid}, media_type='tv')
            store.set_tmdb_cache_seasons(tid, {sn: detail})
            seasons[sn] = detail
            with library_mutation_lock(show['library_id']):
                current = store.get_show_meta(sid)
                if current and current.get('tmdb_id') == tid:
                    _upsert_season(current, detail)
            if detail.get('poster_path'):
                result['series_filled'] += _fill_art(show, {'id': tid, 'seasons': [detail]})
            result['filled'] += _apply_candidates(show, seasons)
            remains = [e for e in store.list_episodes(sid) if int(e['season']) == sn and _needed(e)]
            status = 'metadata_pending' if remains else 'complete'
            retry_at = int(time.time()) + DAY if remains else 0
            remaining_key = _digest(sorted({(int(e['episode']), int(e.get('episode_end') or 0),
                                            int(e.get('tmdb_episode_id') or 0)) for e in remains})) if remains else missing_key
            store.tv_ingest.save_metadata_check(tid, sn, remaining_key, status, retry_at)
        if status not in ('ok', 'complete'):
            result['status'] = status
            result['retry_at'] = max(result['retry_at'], retry_at)
            message = {'metadata_pending': '官方资料暂缺，稍后可重试',
                       'not_configured': '尚未配置 TMDB，已保留本地资料',
                       'source_unavailable': '资料来源暂不可用，可稍后重试'}.get(status, '分集资料尚未补齐')
            result['issues'].append(_issue(status, message, season=sn))
    for e in store.list_episodes(sid):
        if e.get('binding_conflict'):
            result['issues'].append(_issue('binding_conflict', '文件季号与已确认的目录归属冲突', episode_id=e['id']))
    if (result['filled'] or result.get('series_filled')) and result['status'] == 'complete':
        result['status'] = 'filled'
    return result


def _media(show, metadata):
    lib = store.get_library(show['library_id']) or {}
    if lib.get('read_only') or lib.get('artwork_mode', 'nfo') == 'none':
        return
    # Hash ownership and remote episode-NFO defaults remain in the existing writer.
    files = metadata.get('files') or {}
    if metadata.get('filled') or metadata.get('series_filled') or files.get('replaced') or files.get('changed'):
        with library_mutation_lock(show['library_id']):
            result = tv_persist.write_media_files(show['id'], show['library_id'])
        if result.get('nfo_failed') or (result.get('artwork') or {}).get('reason') == 'error':
            metadata['issues'].append(_issue('media_write_failed', '部分 NFO 或海报未写入，可重试资料文件维护'))


def _facts(show, raw_plan):
    """Stable filesystem + numbering/naming facts, excluding playback and clocks."""
    from .scanner.tv_nfo_link import show_dirs_for
    sid, lid = int(show['id']), int(show['library_id'])
    episodes = store.list_episodes(sid)
    rules = store.list_tv_bindings(show_id=sid)
    roots = set(show_dirs_for(sid, episodes)) | {r['path'] for r in rules}
    roots = sorted(p for p in roots if p and not any(p.startswith(q + '/') for q in roots if p != q))
    backend = storage.backend_for(lid)
    storage.clear_meta_cache(lid)
    files = []
    for root in roots:
        for entry in backend.iter_tree(root):
            if '.part-' not in entry.name and '.tmp-' not in entry.name:
                files.append([entry.rel, bool(entry.is_dir), int(entry.size) if not entry.is_dir else 0,
                              float(entry.mtime) if not entry.is_dir else 0])
    destinations = set()
    for key in ('dir_moves', 'file_moves', 'dir_renames'):
        destinations.update(p['to'] for p in raw_plan.get(key) or [])
    if raw_plan.get('root_move'):
        destinations.add(raw_plan['root_move']['to'])
    occupied = []
    for path in sorted(destinations):
        try:
            st = backend.stat(path)
            occupied.append([path, st.size, st.mtime, st.is_dir])
        except storage.StorageNotFound:
            occupied.append([path, None])
    lib = store.get_library(lid) or {}
    keys = ('id', 'file_path', 'season', 'episode', 'episode_end', 'absolute_number',
            'tmdb_episode_id', 'title', 'needs_review', 'local_only', 'match_source', 'binding_conflict')
    return {'show': {k: show.get(k) for k in ('id', 'library_id', 'title', 'year', 'tmdb_id', 'needs_review')},
            'episodes': [{k: e.get(k) for k in keys} for e in sorted(episodes, key=lambda e: e['id'])],
            'bindings': [{k: r.get(k) for k in ('path', 'show_id', 'season', 'override_season')}
                         for r in rules], 'files': sorted(files), 'destinations': occupied,
            'policy': {k: lib.get(k) for k in ('read_only', 'enabled', 'media_enabled', 'naming_profile',
                                              'artwork_mode', 'path', 'storage_identity')}}


def _empty_plan(show):
    return {'show_id': show['id'], 'library_id': show['library_id'], 'title': show.get('title'),
            'show_dir': '', 'file_moves': [], 'dir_moves': [], 'dir_renames': [], 'rmdirs': [],
            'root_move': None, 'manual': [], 'conflicts': [], 'warnings': [], 'untouched': [],
            'kept': [], 'dir_totals': {}}


def _plan(ids, options):
    result = tv_organize.plan_tv_organize(ids=ids, **options) if ids else {'plans': [], 'counts': {}, 'total': 0}
    plans, counts = [], {}
    for raw in result['plans']:
        show = store.get_show_meta(int(raw['show_id']))
        if show and show.get('needs_review'):
            continue  # A torrent opt-in must never bypass an unconfirmed identity.
        plans.append(raw)
        for move in (raw.get('file_moves') or []) + (raw.get('dir_moves') or []) + (raw.get('dir_renames') or []):
            action = move.get('action')
            counts[action] = counts.get(action, 0) + 1
        if raw.get('root_move'):
            counts['root'] = counts.get('root', 0) + 1
    return {'plans': plans, 'counts': counts, 'total': sum(counts.values())}


def reconciliation_from_scan(results):
    return {int(sid): files for row in results if row.get('status') == 'tv_reconciliation'
            for sid, files in (row.get('reconciliation') or {}).get('by_show', {}).items()}


def prepare(*, ids=None, library_ids=None, discovered=False, source='manual', file_results=None,
            retry_metadata=False, actions=None, allow_torrent=False, allow_absolute_shows=None,
            should_stop=None, progress_cb=None):
    shows, lids = resolve_scope(ids=ids, library_ids=library_ids)
    opts = {'actions': [a for a in tv_organize.TV_ORGANIZE_ACTIONS if actions is None or a in actions],
            'allow_torrent': bool(allow_torrent),
            'allow_absolute_shows': sorted(set(allow_absolute_shows or []))}
    if actions == [] or any(a not in tv_organize.TV_ORGANIZE_ACTIONS for a in actions or []):
        raise ValueError('不支持的整理项目')
    params = {'ids': [int(s['id']) for s in shows], **opts}
    pid, results, revisions, discovery = uuid.uuid4().hex, [], {}, {}

    def progress(phase, done, total):
        _stop(should_stop)
        if progress_cb:
            progress_cb(phase, done, total)

    with operation_scope(lids, should_stop):
        fetched = {}
        for i, show in enumerate(shows):
            sid = int(show['id'])
            before = store.tv_ingest.get_review(sid) or {}
            revisions[sid] = int(before.get('dirty_revision') or 0)
            progress('discover', i, len(shows))
            result = {'show_id': sid, 'filled': 0, 'status': 'complete', 'issues': [], 'retry_at': 0}
            try:
                discovery[sid] = scanner.scan_tv_show(sid, should_stop=should_stop) if not discovered else {
                    'reconciliation': (file_results or {}).get(sid, {})}
                progress('metadata', i, len(shows))
                files = discovery[sid].get('reconciliation') or {}
                result['files'] = files
                if discovery[sid].get('unknown') and not any(
                        issue.get('code') == 'file_numbering_review' for issue in files.get('issues', [])):
                    files.setdefault('issues', []).append(_issue('unknown_files',
                        '部分新文件未识别季集号，请核对文件名', count=discovery[sid]['unknown']))
                    files['review'] = max(int(files.get('review') or 0), discovery[sid]['unknown'])
                if files.get('incomplete'):
                    result.update(status='files_pending', files=files, issues=files.get('issues') or [])
                    results.append(result)
                    continue
                result = _metadata(store.get_show_meta(sid), retry_metadata, fetched, should_stop)
                result['files'] = files
                result['issues'].extend(result['files'].get('issues') or [])
                progress('media', i, len(shows))
                _media(store.get_show_meta(sid), result)
            except IngestionCancelled:
                mark_dirty(sid)
                raise
            except Exception as exc:
                result['status'] = 'failed'
                result['issues'].append(_issue('check_failed', provider_state.safe_error(exc)))
                result['retry_at'] = int(time.time()) + 600
            results.append(result)
        progress('preview', len(shows), len(shows))
        incomplete_shows = {int(row['show_id']) for row in results if row.get('files', {}).get('incomplete')}
        planned = _plan([sid for sid in params['ids'] if sid not in incomplete_shows], opts)
        raw_by_id = {int(p['show_id']): p for p in planned['plans']}
        public, fingerprints, facts, raw_plans = [], {}, {}, []
        for show, metadata in zip(shows, results):
            sid = int(show['id'])
            show = store.get_show_meta(sid)
            if show is None:
                continue
            raw = raw_by_id.get(sid) or _empty_plan(show)
            if metadata.get('files', {}).get('incomplete'):
                raw = _empty_plan(show)
            plan = tv_organize.summarize_plan(raw)
            plan['metadata'] = metadata
            try:
                with library_mutation_lock(show['library_id']):
                    snapshot = _facts(show, raw)
            except storage.StorageError:
                snapshot = None
            # Metadata completion counts are presentation, not a new suggestion.
            stable_metadata = {'status': metadata['status'] if metadata['issues'] else 'complete',
                               'issues': metadata['issues']}
            fingerprint = _digest({'facts': snapshot, 'plan': raw, 'metadata': stable_metadata, 'options': opts})
            plan['fingerprint'] = fingerprint
            actionable = bool(raw.get('file_moves') or raw.get('dir_moves') or raw.get('dir_renames')
                              or raw.get('root_move') or raw.get('manual') or raw.get('conflicts')
                              or raw.get('warnings') or metadata['issues'])
            state = store.tv_ingest.save_review(sid, show['library_id'], fingerprint, pid,
                plan, metadata, params, actionable, revisions[sid], metadata.get('retry_at') or 0,
                incomplete=bool(metadata['issues']))
            executable = bool(not raw.get('blocked') and (raw.get('file_moves') or raw.get('dir_moves')
                              or raw.get('dir_renames') or raw.get('root_move')))
            plan.update(state=state, auto_prompt=bool(executable and state == 'pending'))
            public.append(plan)
            fingerprints[str(sid)] = fingerprint
            facts[str(sid)] = snapshot
            raw_plans.append(raw)
        auto_ids = [p['show_id'] for p in public if p['auto_prompt']]
        summary = {'preview_id': pid, 'plans': public, 'counts': planned['counts'], 'total': planned['total'],
                   'metadata_results': results, 'auto_prompt': bool(auto_ids), 'auto_prompt_ids': auto_ids,
                   'params': params, 'source': source}
        store.tv_ingest.save_preview({'summary': summary, 'plans': raw_plans,
            'fingerprints': fingerprints, 'facts': facts, 'options': opts}, pid)
        return summary


def validate_preview(preview_id, ids=None, actions=None, allow_torrent=False, allow_absolute_shows=None):
    """Read-only validation; caller holds operation and mutation locks through execute."""
    record = store.tv_ingest.get_preview(preview_id)
    payload = record['payload']
    saved_ids = {int(p['show_id']) for p in payload['plans']}
    selected = saved_ids if ids is None else set(int(sid) for sid in ids)
    if not selected or not selected <= saved_ids:
        raise ValueError('所选剧不属于本次预览')
    if selected & set(record['consumed']):
        raise ValueError('这份预览已执行，请重新检查整理结果')
    options = payload['options']
    if (actions is not None and set(actions) != set(options['actions'])) or bool(allow_torrent) != options['allow_torrent']:
        raise ValueError('整理选项已变化，请重新预览')
    if set(allow_absolute_shows or []) != set(options['allow_absolute_shows']):
        raise ValueError('集号风险选项已变化，请重新预览')
    fresh = _plan(sorted(saved_ids), options)
    current = {int(p['show_id']): p for p in fresh['plans']}
    for sid in sorted(selected):
        show = store.get_show_meta(sid)
        if not show:
            raise ValueError('剧集已变化，请重新预览')
        old = next(p for p in payload['plans'] if int(p['show_id']) == sid)
        now = current.get(sid) or _empty_plan(show)
        try:
            facts = _facts(show, now)
        except storage.StorageError as exc:
            raise ValueError('目录不可用，请重新检查入库整理') from exc
        if payload['facts'].get(str(sid)) is None or facts != payload['facts'][str(sid)] or now != old:
            raise ValueError('文件、资料或目录计划已变化，请重新预览')
    plans = [p for p in payload['plans'] if int(p['show_id']) in selected]
    counts = {}
    for p in plans:
        for key, count in tv_organize.summarize_plan(p)['counts'].items():
            counts[key] = counts.get(key, 0) + count
    return {'preview_id': preview_id, 'plans': plans, 'counts': counts, 'total': sum(counts.values()),
            'params': payload['summary']['params']}
