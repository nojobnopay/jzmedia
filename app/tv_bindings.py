"""TV directory ownership: read-only inventory, evidence, preview and confirmation.

Confirmation edits the catalogue only. Media files and NFOs are never written here.
"""
import concurrent.futures
import hashlib
import json
import os
from collections import defaultdict

from . import library_paths, storage, store, tmdb
from .library_mutex import library_mutation_lock
from .log import get_logger
from .scanner import tv_match, tv_parse, tv_persist
from .scanner.scan import (_video_entries, _tv_special_hints, resolve_tv_numbers,
                           tv_plan, scan_skip_dirs)
from .scanner.classify import is_sample, is_sidecar
from .tv_binding_rules import (binding_for, bound_numbers, contains, directory_path,
                               release_identity, season_suggestions)

logger = get_logger('tv_bindings')
_MAX_ENTRIES = 50000


def _library(library_id):
    lib = library_paths.get_library(library_id)
    if not lib or lib.get('kind') != 'tv':
        raise ValueError('请选择有效的剧集视频库')
    if not lib.get('effective_enabled', lib.get('enabled')):
        raise ValueError('视频库已停用')
    return lib


def _backend(library_id):
    return storage.backend_for_library(_library(library_id))


def _public_shows(shows):
    return [dict(id=s['id'], title=s['title'], tmdb_id=s.get('tmdb_id'),
                 year=s.get('year')) for s in shows.values()]


def _tree(backend, path=''):
    entries = []
    for e in backend.iter_tree(path, skip_dirs=scan_skip_dirs()):
        entries.append(e)
        if len(entries) > _MAX_ENTRIES:
            raise ValueError('目录项目过多，请按更小的目录分别处理')
    return entries


def _fingerprint(entries):
    rows = sorted((e.rel, bool(e.is_dir), e.size, e.mtime) for e in entries)
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def catalog_inventory(library_id, show_id=None):
    """Return known directories from SQLite without touching media storage.

    This is the dialog's first paint. A background refresh can later replace it
    with `inventory()`, which also discovers files not scanned into the catalog.
    """
    _library(library_id)
    shows = {s['id']: s for s in store.list_shows(library_id)}
    rules = store.list_tv_bindings(library_id)
    groups = {}

    def add(root, episode=None, rule=None):
        if not root:
            return
        item = groups.setdefault(root, dict(path=root, files=0, episodes=set(),
                    seasons=set(), show_ids=set(), samples=[], binding=rule))
        if rule and not item.get('binding'):
            item['binding'] = rule
        if episode:
            item['files'] += 1
            start = int(episode['episode'])
            item['episodes'].update(range(start,
                max(start, int(episode.get('episode_end') or 0)) + 1))
            item['seasons'].add(int(episode['season']))
            item['show_ids'].add(int(episode['show_id']))
            if len(item['samples']) < 2:
                item['samples'].append(os.path.basename(episode['file_path']))

    for rule in rules:
        add(rule['path'], rule=rule)
    from .scanner.tv_nfo_link import _direct_dirs, show_dir_of
    for sid in shows:
        episodes = store.list_episodes(sid)
        direct = _direct_dirs(episodes)
        for episode in episodes:
            rule = binding_for(episode['file_path'], rules)
            root = rule['path'] if rule else show_dir_of(episode['file_path'], direct)
            add(root, episode, rule)

    out = []
    for item in groups.values():
        for key in ('episodes', 'seasons', 'show_ids'):
            item[key] = sorted(item[key])
        item['selected'] = bool(show_id and (int(show_id) in item['show_ids']
                                or (item.get('binding') or {}).get('show_id') == int(show_id)))
        item['shows'] = [dict(id=sid, title=shows.get(sid, {}).get('title', ''),
                              tmdb_id=shows.get(sid, {}).get('tmdb_id'))
                         for sid in item['show_ids']]
        item['query'], item['year'] = release_identity(os.path.basename(item['path']))
        out.append(item)
    return dict(items=sorted(out, key=lambda item: item['path']),
                shows=_public_shows(shows), source='catalog')


def inventory(library_id, show_id=None):
    backend = _backend(library_id)
    storage.clear_meta_cache(library_id)
    entries = _tree(backend)
    vids = _video_entries(entries)
    plans, rules = tv_plan(entries, vids), store.list_tv_bindings(library_id)
    known = store.tv_binding_snapshot(library_id, [e.name for e in entries if '/' not in e.rel])
    by_path = {e['file_path']: e for e in known['episodes']}
    shows = {s['id']: s for s in store.list_shows(library_id)}
    groups = {}
    for e in vids:
        rule = binding_for(e.rel, rules)
        root = rule['path'] if rule else plans[e.rel]['show_root']
        if not root or is_sample(e.name) or is_sidecar(e.rel, backend=backend, tv=True):
            continue
        n = resolve_tv_numbers(e.name, os.path.basename(os.path.dirname(e.rel)), root,
                               plans[e.rel]['episode_hint'])
        item = groups.setdefault(root, dict(path=root, files=0, episodes=set(),
                    seasons=set(), show_ids=set(), samples=[], binding=rule))
        item['files'] += 1
        if n['ok']:
            item['seasons'].add(n['season'])
            item['episodes'].update(range(n['episode'], max(n['episode'], n['episode_end'])+1))
        old = by_path.get(e.rel)
        if old:
            item['show_ids'].add(old['show_id'])
        if len(item['samples']) < 2:
            item['samples'].append(e.name)
    out = []
    for item in groups.values():
        for k in ('episodes', 'seasons', 'show_ids'):
            item[k] = sorted(item[k])
        item['selected'] = bool(show_id and (show_id in item['show_ids']
                                    or (item['binding'] or {}).get('show_id') == show_id))
        item['shows'] = [dict(id=sid, title=shows.get(sid, {}).get('title', ''),
                             tmdb_id=shows.get(sid, {}).get('tmdb_id')) for sid in item['show_ids']]
        item['query'], item['year'] = release_identity(os.path.basename(item['path']))
        out.append(item)
    return dict(items=sorted(out, key=lambda i: i['path']),
                shows=_public_shows(shows), source='storage')


def target_detail(tmdb_id):
    detail, offline = tv_persist._detail_with_fallback(int(tmdb_id))
    if int(detail.get('id') or 0) != int(tmdb_id):
        raise ValueError('资料条目与所选目标不一致')
    return detail, bool(offline)


def suggest(library_id, paths, tmdb_id=None):
    # Explicitly invoked by the user. Search each selected directory, without a
    # series-first-air-year filter: a sequel's year can be a season's air year.
    _backend(library_id)
    paths = [directory_path(p) for p in paths]
    if not paths or len(paths) > 12:
        raise ValueError('每次请选择 1–12 个目录获取建议')
    candidates, errors = {}, []
    if tmdb_id:
        candidates[int(tmdb_id)] = None
    else:
        for path in paths:
            query, _ = release_identity(os.path.basename(path))
            try:
                for hit in tmdb.search_tv(query)[:5]:
                    candidates.setdefault(int(hit['id']), None)
            except Exception as e:
                logger.warning('binding search failed query=%s: %s', query, e)
                errors.append(f'“{query}”搜索失败，可手动搜索或选择已有剧集')
    out = []
    backend = _backend(library_id)
    counts = {}
    for path in paths:
        counts[path] = []
        for e in _video_entries(_tree(backend, path)):
            if is_sample(e.name) or is_sidecar(e.rel, backend=backend, tv=True):
                continue
            n = resolve_tv_numbers(e.name, os.path.basename(os.path.dirname(e.rel)), path)
            if n['ok']:
                counts[path].extend(range(n['episode'], max(n['episode'], n['episode_end'])+1))
    for tid in list(candidates)[:8]:
        try:
            d, offline = target_detail(tid)
        except Exception as e:
            logger.warning('binding candidate detail failed id=%s: %s', tid, e)
            errors.append(f'候选 {tid} 的季资料暂不可用')
            continue
        out.append(dict(tmdb_id=tid, title=d.get('name') or '',
                   year=(d.get('first_air_date') or '')[:4], offline=offline,
                   seasons=[{k: s.get(k) for k in ('season_number', 'name', 'episode_count', 'air_date')}
                            for s in d.get('seasons') or []],
                   directories=[dict(path=p, suggestions=season_suggestions(p, counts[p], d)) for p in paths]))
    return dict(items=out, warnings=errors)


def preview(library_id, tmdb_id, directories, target_show_id=None,
            replace_manual=False, allow_duplicates=False):
    backend = _backend(library_id)
    if not directories or len(directories) > 50:
        raise ValueError('每次请选择 1–50 个目录')
    rules = []
    for row in directories:
        path = directory_path(row['path'])
        sn = row.get('season')
        if sn is not None and not 0 <= sn <= 99:
            raise ValueError('季号必须在 0–99 之间')
        if any(contains(r['path'], path) or contains(path, r['path']) for r in rules):
            raise ValueError('所选目录重复或互相嵌套，请分别处理')
        rules.append(dict(path=path, season=sn, override_season=bool(row.get('override_season'))))
    paths = [r['path'] for r in rules]
    for old in store.list_tv_bindings(library_id):
        if old['path'] not in paths and any(contains(p, old['path']) or contains(old['path'], p) for p in paths):
            raise ValueError('所选目录包含或位于已有归属目录内，请选择原归属目录调整')
    detail, offline = target_detail(tmdb_id)
    season_index = {int(s['season_number']): s for s in detail.get('seasons') or []}
    if any(r['season'] is not None and r['season'] not in season_index for r in rules):
        raise ValueError('目标条目没有所选季，请重新选择')
    targets = [s for s in store.list_shows(library_id) if s.get('tmdb_id') == tmdb_id]
    if target_show_id:
        target_show = store.get_show_meta(target_show_id)
        if (not target_show or target_show['library_id'] != library_id
                or target_show.get('tmdb_id') != tmdb_id):
            raise ValueError('目标剧必须属于当前视频库且匹配同一条目')
    else:
        if len(targets) > 1:
            raise ValueError('当前库有多个同条目剧集，请选择一个已有剧作为合并目标')
        target_show = targets[0] if targets else None
    meta = tv_match.tv_meta_from_detail(detail)
    snapshot = store.tv_binding_snapshot(library_id, paths)
    rebind = False
    if not target_show:
        # A scanned, as-yet unmatched canonical directory is the natural target.
        # Reuse it only when this preview covers all of that show's media/rules.
        candidates = [s for s in snapshot['shows'] if not s.get('tmdb_id')
                      and s['title'] == meta['title'] and s.get('year') == meta['year']]
        if len(candidates) == 1:
            candidate = candidates[0]
            refs = [e['file_path'] for e in store.list_episodes(candidate['id'])]
            refs += [e['file_path'] for e in store.list_extras_by_show(candidate['id'])]
            refs += [r['path'] for r in store.list_tv_bindings(show_id=candidate['id'])]
            if all(any(contains(p, ref) for p in paths) for ref in refs):
                target_show, rebind = candidate, True
    target = dict(show_id=(target_show or {}).get('id'), tmdb_id=tmdb_id,
                  title=(target_show or {}).get('title') or meta['title'], year=meta['year'],
                  rebind=rebind)
    existing = {e['file_path']: e for e in snapshot['episodes']}
    storage.clear_meta_cache(library_id)
    entries, items, problems, warnings, groups = [], [], [], [], []
    for rule in rules:
        path = rule['path']
        if not backend.stat(path).is_dir:
            raise ValueError('所选路径不是目录')
        tree = _tree(backend, path)
        entries.extend(tree)
        vids = _video_entries(tree)
        hints = _tv_special_hints(vids)
        samples, eps = [], set()
        group_count = 0
        source = tv_parse.parse_show_dir(os.path.basename(path))
        for entry in vids:
            if is_sample(entry.name) or is_sidecar(entry.rel, backend=backend, tv=True):
                continue
            raw = resolve_tv_numbers(entry.name, os.path.basename(os.path.dirname(entry.rel)),
                                     path, hints.get(entry.rel))
            if not raw['ok']:
                warnings.append(f'集号无法识别，保留文件：{entry.rel}')
                continue
            numbers = bound_numbers(raw, rule)
            old = existing.get(entry.rel)
            if numbers['binding_conflict']:
                problems.append(f'季号冲突：{entry.rel} 明确为第 {raw["season"]} 季；需勾选覆盖明确季号')
            nums = dict(season=numbers['season'], episode=numbers['episode'],
                        episode_end=numbers['episode_end'], absolute_number=numbers['absolute'])
            if old and rule['season'] is None:
                nums = {k: old[k] for k in nums}
            change = old and (old['show_id'] != target['show_id'] or any(old[k] != v for k, v in nums.items()))
            if change and (old['local_only'] or old.get('match_source') == 'manual') and not replace_manual:
                problems.append(f'已有手工分集绑定或本地集确认：{entry.rel}；需明确允许替换')
            original = dict(season=raw['season'], episode=raw['episode'], episode_end=raw['episode_end'],
                            absolute_number=raw['absolute'])
            items.append(dict(file_path=entry.rel, numbers=nums, original=original,
                              source_show=source, metadata={}))
            eps.update(range(nums['episode'], max(nums['episode'], nums['episode_end'])+1))
            group_count += 1
            if len(samples) < 3:
                samples.append(dict(file=entry.name,
                       before={k: (old or original).get(k) for k in ('season', 'episode', 'episode_end')}, after=nums))
        groups.append(dict(path=path, season=rule['season'], files=group_count,
                           distinct=len(eps), samples=samples))
    if not items:
        problems.append('没有可识别的分集，无法确认归属')
    # Include untouched target episodes when detecting overlaps. A second version
    # or multi-episode file is reported, never silently discarded.
    occupied = defaultdict(list)
    changed_paths = {i['file_path'] for i in items}
    target_eps = store.list_episodes(target['show_id']) if target['show_id'] else []
    for e in [e for e in target_eps if e['file_path'] not in changed_paths] + [dict(file_path=i['file_path'], **i['numbers']) for i in items]:
        for n in range(e['episode'], max(e['episode'], e['episode_end'])+1):
            occupied[(e['season'], n)].append(e['file_path'])
    duplicates = [dict(season=k[0], episode=k[1], files=v) for k, v in sorted(occupied.items()) if len(v)>1]
    if duplicates and not allow_duplicates:
        problems.append(f'有 {len(duplicates)} 个集号重叠；核对后勾选保留全部版本才能确认')
    seasons, failures = {}, []
    cached = store.get_tmdb_cache_seasons(tmdb_id)
    for sn in sorted({i['numbers']['season'] for i in items}):
        try:
            seasons[sn] = tmdb.tv_season(tmdb_id, sn) if not offline else cached[sn]
        except Exception as e:
            logger.warning('binding season unavailable id=%s season=%s: %s', tmdb_id, sn, e)
            if sn in cached:
                seasons[sn] = cached[sn]
                warnings.append(f'第 {sn} 季使用缓存资料')
            else:
                failures.append(sn)
    for sn in failures:
        warnings.append(f'第 {sn} 季资料暂不可用；归属可保存，分集资料标记待补全')
    matched = 0
    for item in items:
        nums = item['numbers']
        es = {int(e['episode_number']): e for e in seasons.get(nums['season'], {}).get('episodes', [])}
        ep = es.get(nums['episode'])
        if ep and all(n in es for n in range(nums['episode'], max(nums['episode'], nums['episode_end'])+1)):
            item['metadata'] = dict(tmdb_episode_id=ep.get('id'), title=ep.get('name') or '',
                        overview=ep.get('overview') or '', still_path=ep.get('still_path') or '',
                        air_date=ep.get('air_date') or '', runtime=ep.get('runtime') or 0,
                        tmdb_rating=ep.get('vote_average'), needs_review=0, match_source='directory',
                        episode_credits=json.dumps(tv_match.tv_episode_credits(ep), ensure_ascii=False))
            matched += 1
    if matched < len(items):
        warnings.append(f'{len(items)-matched} 个文件没有完整的对应分集资料，将保留待确认；请核对剪辑版本和编号')
    show_fields = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                   for k, v in meta.items() if k in store.tv.TV_META_FIELDS}
    show_fields['sort_title'] = meta['title']
    for key, path in (('poster_path', tv_persist.tv_poster_name(tmdb_id)),
                      ('backdrop_path', tv_persist.tv_backdrop_name(tmdb_id))):
        if os.path.isfile(os.path.join(tv_persist.POSTER_DIR, path)):
            show_fields[key] = path
    season_fields = [dict(season=sn, name=s.get('name') or '', overview=s.get('overview') or '',
                     air_date=s.get('air_date') or '', episode_count=s.get('episode_count') or 0,
                     tmdb_season_id=s.get('id'), cast=json.dumps(tv_match.tv_season_credits(seasons.get(sn, {})), ensure_ascii=False))
                     for sn, s in season_index.items()]
    public = dict(target=target, groups=groups, episodes=len(items), matched=matched,
                  extras=len(snapshot['extras']), conflicts=problems, warnings=warnings,
                  duplicates=duplicates[:30], duplicate_count=len(duplicates), offline=offline,
                  can_apply=not problems)
    if not problems:
        payload = dict(library_id=library_id, target=target, rules=rules, items=items,
                  show_fields=show_fields, season_fields=season_fields, detail=detail, seasons=seasons,
                  snapshot_digest=store.tv_binding_digest(snapshot), fingerprint=_fingerprint(entries),
                  target_episode_ids=sorted(e['id'] for e in target_eps),
                  target_signature=[{k: e.get(k) for k in store.tv_bindings._EP_KEYS}
                                    for e in sorted(target_eps, key=lambda e: e['id'])], public=public)
        public['token'] = store.save_tv_binding_preview(payload)
    return public


def apply(token):
    record = store.get_tv_binding_plan(token)
    if record['state'] == 'applied':
        return record['payload']['result']
    if record['state'] != 'preview':
        raise ValueError('预览已经失效')
    p = record['payload']
    with library_mutation_lock(p['library_id']):
        backend = _backend(p['library_id'])
        storage.clear_meta_cache(p['library_id'])
        entries = []
        for rule in p['rules']:
            entries.extend(_tree(backend, rule['path']))
        if _fingerprint(entries) != p['fingerprint']:
            raise ValueError('目录内容已变化，请重新预览')
        if p['target']['show_id']:
            if sorted(e['id'] for e in store.list_episodes(p['target']['show_id'])) != p['target_episode_ids']:
                raise ValueError('目标剧新增了分集，请重新预览核对重复集号')
        result = store.apply_tv_binding_plan(token)
    _invalidate_stills(store.get_tv_binding_plan(token)['payload']['before']['episodes'])
    # Only application-side cache/artwork. Deliberately do not call
    # match_show/write_media_files: they also write to the media directories.
    try:
        meta = tv_match.tv_meta_from_detail(p['detail'])
        store.upsert_tmdb_cache(p['target']['tmdb_id'], meta, tv_match.tv_credits(p['detail']),
                               meta.get('poster_tmdb_path') or '', media_type='tv')
        store.set_tmdb_cache_seasons(p['target']['tmdb_id'], p['seasons'], media_type='tv')
        _cache_art(result['show_id'], p)
    except Exception as e:
        logger.warning('binding cache update failed show=%s: %s', result['show_id'], e)
    return result


def _invalidate_stills(episodes):
    # IDs intentionally survive ownership changes; ID-keyed pictures must not.
    for ep in episodes:
        current = store.get_episode(ep['id'])
        if current and (current.get('still_path'), current.get('tmdb_episode_id')) == (
                ep.get('still_path'), ep.get('tmdb_episode_id')):
            continue
        tv_persist._STILL_FAIL.pop(ep['id'], None)
        for name in (tv_persist.episode_still_name(ep['id']), f"tv_e{ep['id']}.jpg"):
            try:
                os.unlink(os.path.join(tv_persist.POSTER_DIR, name))
            except FileNotFoundError:
                continue
            except OSError as e:
                logger.warning('binding still cache cleanup failed episode=%s: %s', ep['id'], e)


def _cache_art(show_id, payload):
    tid, detail = payload['target']['tmdb_id'], payload['detail']
    jobs = [('poster_path', None, detail.get('poster_path'), tv_persist.tv_poster_name(tid), 'w500'),
            ('backdrop_path', None, detail.get('backdrop_path'), tv_persist.tv_backdrop_name(tid), 'w780')]
    for season in detail.get('seasons') or []:
        sn = int(season['season_number'])
        jobs.append(('season', sn, season.get('poster_path'), tv_persist.tv_season_poster_name(tid, sn), 'w300'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(tv_persist._download, src, os.path.join(tv_persist.POSTER_DIR, path), size):
                   (key, sn, path) for key, sn, src, path, size in jobs if src}
        for future, (key, sn, path) in futures.items():
            try:
                if not future.result():
                    continue
                if key == 'season':
                    store.upsert_season(show_id, payload['library_id'], sn, poster_path=path)
                else:
                    store.update_show_meta(show_id, **{key: path})
            except Exception as e:
                logger.warning('binding artwork cache failed show=%s key=%s: %s', show_id, key, e)


def undo(token, dry_run=True):
    before = []
    if not dry_run:
        payload = store.get_tv_binding_plan(token)['payload']
        before = store.tv_binding_snapshot(payload['library_id'],
                                          [r['path'] for r in payload['rules']])['episodes']
    if dry_run:
        result = store.undo_tv_binding_plan(token, True)
    else:
        payload = store.get_tv_binding_plan(token)['payload']
        with library_mutation_lock(payload['library_id']):
            result = store.undo_tv_binding_plan(token, False)
    if not dry_run:
        _invalidate_stills(before)
    return result
