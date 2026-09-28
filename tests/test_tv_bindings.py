"""Directory ownership is explicit, persistent, reversible, and catalogue-only."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import library_paths, scanner, store, tv_bindings
from app.main import app
from app.scanner import tv_persist
from app.tv_binding_rules import bound_numbers, contains, directory_path, season_suggestions

client = TestClient(app)


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    root = tmp_path / 'tv'
    root.mkdir()
    lib = store.create_library(name=f'bindings-{tmp_path.name}', kind='tv', path=str(root))
    library_paths.invalidate_cache()
    def detail(tid):
        return dict(id=tid, name=f'系列剧{tid}', original_name='Series', first_air_date='2009-12-18',
                    number_of_seasons=4, number_of_episodes=217, genres=[], origin_country=['CN'],
                    original_language='zh', seasons=[dict(season_number=i, name=n, episode_count=c,
                    air_date=f'{y}-01-01', id=i) for i,n,c,y in
                    [(1,'裂变',48,2009),(2,'纵横',51,2013),(3,'崛起',40,2017),(4,'大秦赋',78,2020)]])
    def season(tid, sn):
        counts={1:48,2:51,3:40,4:78}
        return dict(episodes=[dict(id=tid*10000+sn*100+n, season_number=sn, episode_number=n,
                   name=f'S{sn}E{n}', overview='资料', guest_stars=[], crew=[]) for n in range(1,counts[sn]+1)])
    monkeypatch.setattr('app.tmdb.tv_detail', detail)
    monkeypatch.setattr('app.tmdb.tv_season', season)
    monkeypatch.setattr('app.tmdb.search_tv', lambda *a, **k: [dict(id=32231)])
    monkeypatch.setattr('app.tmdb.tv_aggregate_credits', lambda tid: {'cast': [], 'crew': []})
    monkeypatch.setattr('app.tmdb.download_image', lambda *a, **k: False)
    yield lib, root
    store.delete_media_library(lib['media_library_id'])
    library_paths.invalidate_cache()


def touch(root, path):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b'x')
    return p


def plan(lib, dirs, **options):
    return tv_bindings.preview(lib['id'], 32231, [dict(path=p, season=s) for p,s in dirs], **options)


def test_four_directories_217_episodes_rescan_and_new_episode(lib):
    l, root = lib
    parts = [('大秦帝国之裂变',1,48),('大秦帝国之纵横',2,51),('大秦帝国之崛起',3,40),('大秦赋',4,78)]
    for name,sn,count in parts:
        for n in range(1,count+1):
            touch(root, f'{name}/{name}.E{n:02}.mp4')
    before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    p = plan(l, [(n,s) for n,s,_ in parts])
    assert p['can_apply'] and p['episodes'] == p['matched'] == 217
    assert store.count_episodes(l['id']) == 0  # preview does not register anything
    result = tv_bindings.apply(p['token'])
    assert tv_bindings.apply(p['token']) == result  # idempotent retry
    sid = result['show_id']
    assert store.count_shows(l['id']) == 1
    eps = store.list_episodes(sid)
    original_ids = {e['file_path']:e['id'] for e in eps}
    assert {s:sum(e['season']==s for e in eps) for s in range(1,5)} == {1:48,2:51,3:40,4:78}
    assert sorted(str(p.relative_to(root)) for p in root.rglob('*')) == before
    scanner.scan_all(library_id=l['id'], force=True)
    assert {e['file_path']:e['id'] for e in store.list_episodes(sid)} == original_ids
    assert store.count_shows(l['id']) == 1
    # Fill a cached season from a new file with the same episode as a second version.
    touch(root, '大秦帝国之纵横/新版本-V2-E01.mp4')
    scanner.scan_tv_show(sid)
    new = store.get_episode_by_path('大秦帝国之纵横/新版本-V2-E01.mp4', l['id'])
    assert new['show_id'] == sid and new['season'] == 2 and new['tmdb_episode_id'] == 322310201


def test_existing_ids_progress_and_undo_preserved(lib):
    l,root=lib
    touch(root,'纵横/E01.mkv')
    scanner.scan_all(library_id=l['id'])
    old=store.get_episode_by_path('纵横/E01.mkv',l['id'])
    store.update_episode_meta(old['id'],watched=1)
    client.post(f"/api/stream/progress?version_id={old['id']}&kind=episode",json={'position':123,'duration':1000})
    p=plan(l,[('纵横',2)])
    result=tv_bindings.apply(p['token'])
    fresh=store.get_episode(old['id'])
    assert fresh['season']==2 and fresh['show_id']==result['show_id']
    progress=store.get_progress(old['id'],'episode')
    assert progress['position']==123
    store.update_episode_meta(old['id'],watched=1)
    preview=store.undo_tv_binding_plan(p['token'])
    assert preview['episodes']==1 and store.get_episode(old['id'])['season']==2
    store.undo_tv_binding_plan(p['token'],False)
    restored=store.get_episode(old['id'])
    assert restored['season']==1 and restored['show_id']==old['show_id'] and restored['watched']==1
    assert store.get_progress(old['id'],'episode')['position']==123
    scanner.scan_all(library_id=l['id'],force=True)
    assert store.get_episode(old['id'])['show_id']==old['show_id']


def test_unscanned_undo_keeps_new_id_and_playback(lib):
    l,root=lib
    touch(root,'崛起/E01.mkv')
    p=plan(l,[('崛起',3)])
    tv_bindings.apply(p['token'])
    ep=store.get_episode_by_path('崛起/E01.mkv',l['id'])
    store.update_episode_meta(ep['id'],watched=1)
    store.undo_tv_binding_plan(p['token'],False)
    restored=store.get_episode(ep['id'])
    assert restored['season']==1 and restored['watched']==1
    assert store.get_show_meta(restored['show_id'])['title']=='崛起'


def test_explicit_season_conflict_and_override(lib):
    l,root=lib
    touch(root,'纵横/Show.S01E01.mkv')
    p=plan(l,[('纵横',2)])
    assert not p['can_apply'] and 'token' not in p
    p=tv_bindings.preview(l['id'],32231,[dict(path='纵横',season=2,override_season=True)])
    sid=tv_bindings.apply(p['token'])['show_id']
    scanner.scan_all(library_id=l['id'],force=True)
    assert store.list_episodes(sid)[0]['season']==2


def test_new_conflicting_file_stays_pending_through_scrape(lib):
    l,root=lib
    touch(root,'纵横/E01.mkv')
    sid=tv_bindings.apply(plan(l,[('纵横',2)])['token'])['show_id']
    touch(root,'纵横/Show.S03E02.mkv')
    scanner.scan_all(library_id=l['id'])
    ep=store.get_episode_by_path('纵横/Show.S03E02.mkv',l['id'])
    assert ep['show_id']==sid and ep['binding_conflict']==1 and ep['needs_review']==1
    tv_persist.scrape_show(store.get_show_meta(sid),force=True,download_art=False)
    assert store.get_episode(ep['id'])['tmdb_episode_id'] is None


def test_stale_files_and_database_preview_rejected(lib):
    l,root=lib
    touch(root,'纵横/E01.mkv')
    p=plan(l,[('纵横',2)])
    touch(root,'纵横/E02.mkv')
    with pytest.raises(ValueError,match='目录内容已变化'):
        tv_bindings.apply(p['token'])
    p=plan(l,[('纵横',2)])
    scanner.scan_all(library_id=l['id'])
    with pytest.raises(ValueError,match='分集或归属已变化'):
        tv_bindings.apply(p['token'])
    assert not store.list_tv_bindings(l['id'])


def test_duplicates_require_confirmation_and_never_drop_files(lib):
    l,root=lib
    touch(root,'版一/E01.mkv');touch(root,'版二/E01.mkv')
    p=plan(l,[('版一',2),('版二',2)])
    assert p['duplicate_count']==1 and not p['can_apply']
    p=plan(l,[('版一',2),('版二',2)],allow_duplicates=True)
    sid=tv_bindings.apply(p['token'])['show_id']
    assert len(store.list_episodes(sid))==2


def test_manual_binding_protected_and_undo_blocked_after_changes(lib):
    l,root=lib
    touch(root,'纵横/E01.mkv');scanner.scan_all(library_id=l['id'])
    ep=store.get_episode_by_path('纵横/E01.mkv',l['id'])
    store.update_episode_meta(ep['id'],match_source='manual',tmdb_episode_id=123)
    assert not plan(l,[('纵横',2)])['can_apply']
    p=plan(l,[('纵横',2)],replace_manual=True)
    tv_bindings.apply(p['token'])
    touch(root,'纵横/E02.mkv');scanner.scan_all(library_id=l['id'])
    with pytest.raises(ValueError,match='新增分集'):
        store.undo_tv_binding_plan(p['token'],False)


def test_library_boundary_and_api_validation(lib):
    l,root=lib
    touch(root,'纵横/E01.mkv')
    r=client.post('/api/tv/bindings/preview',json=dict(library_id=l['id'],tmdb_id=32231,
                   directories=[dict(path='../escape',season=2)]))
    assert r.status_code==409
    assert client.get('/api/tv/bindings/directories?library_id=99999999').status_code==409
    r=client.post('/api/tv/bindings/preview',json=dict(library_id=l['id'],tmdb_id=32231,
                   directories=[dict(path='纵横',season=100)]))
    assert r.status_code==422
    inv=tv_bindings.inventory(l['id'])
    assert inv['items'][0]['path']=='纵横' and inv['items'][0]['files']==1


def test_rules_pure_and_suggestions_use_season_year():
    assert contains('A','A/E01') and not contains('A','AB/E01')
    with pytest.raises(ValueError):directory_path('A/../B')
    assert bound_numbers(dict(ok=True,season=0,explicit_season=0),dict(season=2))['season']==0
    result=season_suggestions('大秦帝国之纵横.The.Qin.Empire.2.2013.1080p',list(range(1,52)),
            dict(seasons=[dict(season_number=2,name='纵横',air_date='2013-09-05',episode_count=51)]))
    assert result[0]['score']==9 and len(result[0]['reasons'])==3


def test_v28_tables_exist_and_preview_survives_new_connection(lib):
    l,root=lib
    touch(root,'纵横/E01.mkv')
    p=plan(l,[('纵横',2)])
    with store._conn() as c:
        row=c.execute('SELECT payload FROM tv_binding_history WHERE id=?',(p['token'],)).fetchone()
        assert json.loads(row['payload'])['rules'][0]['season']==2


def test_organize_four_directories_and_restore_rules(lib):
    from app.scanner import tv_organize
    l,root=lib
    for name in ('裂变','纵横','崛起','大秦赋'):
        touch(root,f'{name}/E01.mkv')
        touch(root,f'{name}/E01.chs.srt')
    token=plan(l,list(zip(('裂变','纵横','崛起','大秦赋'),range(1,5))))['token']
    sid=tv_bindings.apply(token)['show_id']
    preview=tv_organize.plan_tv_organize(ids=[sid])
    assert len(preview['plans'])==1 and len(preview['plans'][0]['dir_moves'])==4
    assert preview['conflicts']==0
    result=tv_organize.execute_tv_organize(preview['plans'])
    assert result['failed']==0 and result['renamed']==4
    for sn in range(1,5):
        assert (root/f'系列剧32231 (2009)/Season {sn:02}/E01.chs.srt').exists()
    assert all(r['path'].startswith('系列剧32231 (2009)/Season ') for r in store.list_tv_bindings(l['id']))
    scanner.scan_all(library_id=l['id'],force=True)
    assert store.count_shows(l['id'])==1
    assert {e['season'] for e in store.list_episodes(sid)}=={1,2,3,4}
    undo=tv_organize.plan_restore(batch_id=result['batch_id'])
    assert undo['conflicts']==0
    restored=tv_organize.execute_restore(undo['plans'])
    assert restored['failed']==0
    assert {r['path'] for r in store.list_tv_bindings(l['id'])}=={'裂变','纵横','崛起','大秦赋'}


def test_organize_conflict_and_torrent_guard(lib):
    from app.scanner import tv_organize
    l,root=lib
    touch(root,'裂变/E01.mkv');touch(root,'纵横/E01.mkv')
    sid=tv_bindings.apply(plan(l,[('裂变',1),('纵横',2)])['token'])['show_id']
    touch(root,'纵横/source.torrent')
    touch(root,'系列剧32231 (2009)/Season 01/existing.txt')
    p=tv_organize.plan_tv_organize(ids=[sid])
    assert p['blocked']==1 and p['conflicts']==1
    assert p['plans'][0]['dir_totals']=={'裂变':1,'纵横':1}
    assert tv_organize.execute_tv_organize(p['plans'])['skipped']==1
    assert (root/'裂变/E01.mkv').exists()


def test_nfo_maintenance_handles_all_physical_roots(lib):
    from app.scanner import tv_nfo_link
    l,root=lib
    touch(root,'裂变/E01.mkv');touch(root,'纵横/E01.mkv')
    sid=tv_bindings.apply(plan(l,[('裂变',1),('纵横',2)])['token'])['show_id']
    assert not list(root.rglob('*.nfo'))
    result=tv_nfo_link.sync_tv_nfos_for(sid,episode_nfo=True)
    assert not result['failed']
    assert (root/'裂变/tvshow.nfo').exists() and (root/'纵横/tvshow.nfo').exists()
    assert '<season>2</season>' in (root/'纵横/E01.nfo').read_text()


def test_rules_remain_when_last_episode_removed(lib):
    l,root=lib
    path=touch(root,'纵横/E01.mkv')
    sid=tv_bindings.apply(plan(l,[('纵横',2)])['token'])['show_id']
    path.unlink();scanner.scan_all(library_id=l['id'])
    assert store.get_show_meta(sid) and store.list_tv_bindings(l['id'])
    touch(root,'纵横/E02.mkv');scanner.scan_tv_show(sid)
    assert store.get_episode_by_path('纵横/E02.mkv',l['id'])['season']==2


def test_preview_same_title_as_new_target_and_undo(lib):
    l,root=lib
    touch(root,'系列剧32231 (2009)/E01.mkv')
    p=plan(l,[('系列剧32231 (2009)',2)])
    sid=tv_bindings.apply(p['token'])['show_id']
    assert store.get_show_meta(sid)['tmdb_id']==32231
    store.undo_tv_binding_plan(p['token'],False)
    assert store.get_show_meta(sid)['tmdb_id'] is None


def test_existing_target_new_directory_and_untouched_scanning(lib):
    l,root=lib
    touch(root,'Original/Season 01/E01.mkv');scanner.scan_all(library_id=l['id'])
    sid=store.list_shows(l['id'])[0]['id']
    store.update_show_meta(sid,tmdb_id=32231)
    touch(root,'纵横/E01.mkv')
    p=plan(l,[('纵横',2)])
    assert tv_bindings.apply(p['token'])['show_id']==sid
    touch(root,'Original/Season 01/E02.mkv')
    scanner.scan_tv_show(sid)
    assert store.get_episode_by_path('Original/Season 01/E02.mkv',l['id'])['show_id']==sid


def test_confirmed_keep_numbering_never_remaps_or_matches_another_season(lib):
    l, root = lib
    touch(root, 'Custom/E49.mkv')
    scanner.scan_all(library_id=l['id'])
    original = store.get_episode_by_path('Custom/E49.mkv', l['id'])
    store.update_episode_meta(original['id'], absolute_number=49)
    sid = tv_bindings.apply(plan(l, [('Custom', None)])['token'])['show_id']
    assert store.remap_absolute_episodes(sid) == 0
    detail = tv_bindings.target_detail(32231)[0]
    seasons = {sn: tv_bindings.tmdb.tv_season(32231, sn) for sn in range(1, 5)}
    tv_persist.apply_tv_detail(sid, detail, seasons, download_art=False)
    scanner.scan_all(library_id=l['id'], force=True)
    e = store.get_episode(original['id'])
    assert (e['season'], e['episode'], e['absolute_number']) == (1, 49, 49)
    assert e['tmdb_episode_id'] is None and e['needs_review']


def test_read_only_media_and_still_invalidation_on_apply_and_undo(lib, tmp_path, monkeypatch):
    l, root = lib
    store.update_media_library(l['media_library_id'], read_only=True)
    library_paths.invalidate_cache()
    touch(root, 'Custom/E01.mkv')
    scanner.scan_all(library_id=l['id'])
    e = store.get_episode_by_path('Custom/E01.mkv', l['id'])
    store.update_episode_meta(e['id'], still_path='/old.jpg', tmdb_episode_id=123)
    cache = tmp_path / 'posters'
    monkeypatch.setattr(tv_persist, 'POSTER_DIR', str(cache))
    stale = touch(cache, tv_persist.episode_still_name(e['id']))
    legacy = touch(cache, f"tv_e{e['id']}.jpg")
    p = plan(l, [('Custom', 2)])
    tv_bindings.apply(p['token'])
    assert not stale.exists() and not legacy.exists()
    stale.write_bytes(b'new still')
    result = client.post('/api/tv/bindings/undo', json={'token': p['token'], 'dry_run': False})
    assert result.status_code == 200, result.text
    assert not stale.exists()
    assert store.get_episode(e['id'])['still_path'] == '/old.jpg'
    assert [p.name for p in (root / 'Custom').iterdir()] == ['E01.mkv']


def test_artwork_only_written_to_application_cache(lib, tmp_path, monkeypatch):
    l, root = lib
    touch(root, 'Custom/E01.mkv')
    detail = tv_bindings.target_detail(32231)[0]
    detail.update(poster_path='/poster.jpg', backdrop_path='/backdrop.jpg')
    detail['seasons'][1]['poster_path'] = '/season2.jpg'
    monkeypatch.setattr('app.tmdb.tv_detail', lambda tid: detail)
    cache = tmp_path / 'posters'
    monkeypatch.setattr(tv_persist, 'POSTER_DIR', str(cache))
    def download(src, dest, size):
        from pathlib import Path
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b'image')
        return True
    monkeypatch.setattr('app.tmdb.download_image', download)
    sid = tv_bindings.apply(plan(l, [('Custom', 2)])['token'])['show_id']
    assert (cache / store.get_show_meta(sid)['poster_path']).is_file()
    assert (cache / store.get_show_meta(sid)['backdrop_path']).is_file()
    season = next(s for s in store.list_seasons(sid) if s['season'] == 2)
    assert (cache / season['poster_path']).is_file()
    assert [p.name for p in (root / 'Custom').iterdir()] == ['E01.mkv']


def test_scanned_canonical_unmatched_source_reused_and_undo_restores_seasons(lib):
    l, root = lib
    directory = '系列剧32231 (2009)'
    touch(root, f'{directory}/E01.mkv')
    scanner.scan_all(library_id=l['id'])
    source = store.get_episode_by_path(f'{directory}/E01.mkv', l['id'])
    sid = source['show_id']
    old_seasons = [s['season'] for s in store.list_seasons(sid)]
    p = plan(l, [(directory, 2)])
    assert p['can_apply'] and p['target']['show_id'] == sid
    assert tv_bindings.apply(p['token'])['show_id'] == sid
    assert store.get_show_meta(sid)['tmdb_id'] == 32231
    assert store.get_episode(source['id'])['season'] == 2
    tv_bindings.undo(p['token'], False)
    assert store.get_show_meta(sid)['tmdb_id'] is None
    assert [s['season'] for s in store.list_seasons(sid)] == old_seasons


def test_standard_rebind_endpoints_cannot_bypass_directory_preview(lib):
    l, root = lib
    touch(root, 'Custom/E01.mkv')
    sid = tv_bindings.apply(plan(l, [('Custom', 2)])['token'])['show_id']
    for suffix, body in [('match', {'tmdb_id': 114043}),
                         ('bind-external', {'source': 'tvmaze', 'source_id': '123'})]:
        response = client.post(f'/api/tv/shows/{sid}/{suffix}', json=body)
        assert response.status_code == 409
    assert store.get_show_meta(sid)['tmdb_id'] == 32231

def test_catalog_inventory_and_endpoint_never_touch_storage(lib, monkeypatch):
    l, root = lib
    touch(root, '纵横/E01.mkv')
    scanner.scan_all(library_id=l['id'])

    def storage_forbidden(_library_id):
        raise AssertionError('catalog inventory touched storage')

    monkeypatch.setattr(tv_bindings, '_backend', storage_forbidden)
    data = tv_bindings.catalog_inventory(l['id'])
    assert data['source'] == 'catalog'
    assert data['items'][0]['path'] == '纵横'
    response = client.get(f"/api/tv/bindings/directories?library_id={l['id']}")
    assert response.status_code == 200
    assert response.json()['source'] == 'catalog'


def test_storage_inventory_runs_as_a_deduplicated_background_job(lib, monkeypatch):
    l, _root = lib
    started, release = threading.Event(), threading.Event()

    def slow_inventory(library_id, show_id=None):
        assert library_id == l['id'] and show_id is None
        started.set()
        if not release.wait(3):
            raise RuntimeError('test inventory was not released')
        return {'items': [], 'shows': [], 'source': 'storage'}

    monkeypatch.setattr(tv_bindings, 'inventory', slow_inventory)
    first = client.post('/api/tv/bindings/directories/refresh',
                        json={'library_id': l['id'], 'show_id': None})
    assert first.status_code == 200 and first.json()['state'] == 'running'
    assert started.wait(1)
    second = client.post('/api/tv/bindings/directories/refresh',
                         json={'library_id': l['id'], 'show_id': None})
    assert second.json()['job_id'] == first.json()['job_id']
    assert second.json()['resumed'] is True
    # The DB-only first paint remains available while storage is blocked.
    assert client.get(f"/api/tv/bindings/directories?library_id={l['id']}").status_code == 200
    release.set()
    job_id = first.json()['job_id']
    for _ in range(100):
        status = client.get(f'/api/tv/bindings/jobs/{job_id}').json()
        if status['state'] != 'running':
            break
        time.sleep(0.01)
    assert status['state'] == 'done'
    assert status['result']['source'] == 'storage'


def test_slow_tv_scan_does_not_hold_the_global_database_lock(lib, monkeypatch):
    from app.scanner import scan as scan_module
    l, _root = lib
    entered, release, query_done = threading.Event(), threading.Event(), threading.Event()

    def slow_scan(*_args, **_kwargs):
        entered.set()
        release.wait(3)
        return {'status': 'ok'}

    class Backend:
        library_id = l['id']

    monkeypatch.setattr(scan_module, '_scan_tv_file', slow_scan)
    worker = threading.Thread(target=scan_module.scan_tv_file, args=(Backend(), 'E01.mkv'))
    reader = threading.Thread(target=lambda: (store.list_shows(l['id']), query_done.set()))
    try:
        worker.start()
        assert entered.wait(1)
        reader.start()
        assert query_done.wait(0.5), 'a slow storage scan blocked a DB-only page query'
    finally:
        release.set()
        worker.join(2)
        reader.join(2)
    assert not worker.is_alive() and not reader.is_alive()

def test_preview_and_apply_background_endpoints(lib):
    l, root = lib
    touch(root, '纵横/E01.mkv')
    payload = dict(
        library_id=l['id'], tmdb_id=32231,
        directories=[dict(path='纵横', season=2)],
    )
    started = client.post('/api/tv/bindings/preview/start', json=payload)
    assert started.status_code == 200 and started.json()['job_id']
    preview_id = started.json()['job_id']
    for _ in range(100):
        status = client.get(f'/api/tv/bindings/jobs/{preview_id}').json()
        if status['state'] != 'running':
            break
        time.sleep(0.01)
    assert status['state'] == 'done' and status['result']['can_apply']
    token = status['result']['token']
    applied = client.post('/api/tv/bindings/apply/start', json={'token': token})
    assert applied.status_code == 200 and applied.json()['job_id']
    apply_id = applied.json()['job_id']
    for _ in range(100):
        status = client.get(f'/api/tv/bindings/jobs/{apply_id}').json()
        if status['state'] != 'running':
            break
        time.sleep(0.01)
    assert status['state'] == 'done'
    episode = store.get_episode_by_path('纵横/E01.mkv', l['id'])
    assert episode['show_id'] == status['result']['show_id'] and episode['season'] == 2
