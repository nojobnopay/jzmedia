"""New scans retain explicit identities and local NFO fallback during ingestion."""
import pytest

from app import library_paths, scanner, store, tv_ingest


@pytest.fixture()
def fresh_tv(tmp_path, monkeypatch):
    root = tmp_path / 'tv'
    root.mkdir()
    lib = store.create_library(name='matching-' + tmp_path.name, kind='tv',
                               path=str(root), artwork_mode='none')
    library_paths.invalidate_cache()
    monkeypatch.setattr(tv_ingest.tmdb, 'search_tv', lambda *_a, **_k: [])
    monkeypatch.setattr(tv_ingest.tv_match, '_external_show_candidate', lambda *_a, **_k: (None, 0, ''))
    monkeypatch.setattr(tv_ingest, '_source_available', lambda: False)
    yield lib, root
    store.delete_media_library(lib['media_library_id'])
    library_paths.invalidate_cache()


def scan(fresh_tv, folder='NeverMatched (2020)', count=1):
    lib, root = fresh_tv
    for number in range(1, count + 1):
        path = root / folder / 'Season 01' / f'episode-S01E{number:02d}.mkv'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'isolated-video')
    results = scanner.scan_all(library_id=lib['id'])
    assert sum(row['status'] == 'tv_ok' for row in results) == count
    shows = store.list_shows(lib['id'])
    assert len(shows) == 1
    return shows[0]


def nfos(fresh_tv, folder='NeverMatched (2020)', count=1):
    _, root = fresh_tv
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'tvshow.nfo').write_text(
        '<tvshow><title>Local NFO series</title><plot>Local series plot</plot>'
        '<year>2020</year></tvshow>')
    season = directory / 'Season 01'
    season.mkdir(exist_ok=True)
    for number in range(1, count + 1):
        (season / f'episode-S01E{number:02d}.nfo').write_text(
            f'<episodedetails><title>Local episode {number}</title>'
            f'<plot>Local plot {number}</plot><season>1</season>'
            f'<episode>{number}</episode></episodedetails>')


@pytest.mark.parametrize('lookup_failed', [False, True])
def test_fresh_scan_replays_local_nfo_without_overwriting_protected_episodes(
        fresh_tv, monkeypatch, lookup_failed):
    nfos(fresh_tv, count=3)
    show = scan(fresh_tv, count=3)
    assert not show['tmdb_id'] and not show['overview']
    store.update_show_meta(show['id'], title='Pinned local series title', title_auto=0)
    first, second, third = store.list_episodes(show['id'])
    store.update_episode_meta(first['id'], title='Manual title', overview='Manual plot',
                              tmdb_episode_id=999, match_source='manual', watched=1)
    store.save_progress(first['id'], 120, 1200, kind='episode')
    store.update_episode_meta(third['id'], title='Local title', overview='Local plot', local_only=1)
    if lookup_failed:
        def unavailable(*_a, **_k):
            raise TimeoutError('isolated metadata source unavailable')
        monkeypatch.setattr(tv_ingest.tv_match, 'resolve_show', unavailable)

    summary = tv_ingest.prepare(ids=[show['id']], discovered=True)

    current = store.get_show_meta(show['id'])
    assert current['match_source'] == 'nfo' and current['tmdb_id'] is None
    assert current['title'] == 'Pinned local series title' and current['title_auto'] == 0
    assert current['overview'] == 'Local series plot'
    assert summary['metadata_results'][0]['status'] == 'external'
    assert summary['metadata_results'][0]['filled'] == 1
    assert store.get_episode(second['id'])['title'] == 'Local episode 2'
    assert store.get_episode(second['id'])['overview'] == 'Local plot 2'
    manual = store.get_episode(first['id'])
    assert manual['title'] == 'Manual title' and manual['overview'] == 'Manual plot'
    assert manual['tmdb_episode_id'] == 999 and manual['match_source'] == 'manual'
    assert manual['watched'] == 1
    assert store.get_progress(first['id'], kind='episode')['position'] == 120
    local = store.get_episode(third['id'])
    assert local['title'] == 'Local title' and local['overview'] == 'Local plot'
    assert local['local_only'] == 1 and local['tmdb_episode_id'] is None
    assert tv_ingest.prepare(ids=[show['id']], discovered=True)['metadata_results'][0]['filled'] == 0


@pytest.mark.parametrize('nfo_state', ['missing', 'invalid', 'unconfirmed', 'unavailable'])
def test_missing_invalid_or_unconfirmed_nfo_does_not_confirm_a_match(fresh_tv, monkeypatch, nfo_state):
    show = scan(fresh_tv)
    _, root = fresh_tv
    if nfo_state == 'invalid':
        (root / 'NeverMatched (2020)' / 'tvshow.nfo').write_text('not XML')
    elif nfo_state == 'unconfirmed':
        nfos(fresh_tv)
        store.update_show_meta(show['id'], needs_review=1)
    elif nfo_state == 'unavailable':
        def unavailable(*_a, **_k):
            raise TimeoutError('isolated metadata source unavailable')
        monkeypatch.setattr(tv_ingest.tv_match, 'resolve_show', unavailable)

    summary = tv_ingest.prepare(ids=[show['id']], discovered=True)

    expected = {'unconfirmed': 'match_review', 'unavailable': 'source_unavailable'}
    assert summary['metadata_results'][0]['status'] == expected.get(nfo_state, 'unmatched')
    current = store.get_show_meta(show['id'])
    assert not current['tmdb_id'] and not current['overview']
    assert current['match_source'] != 'nfo'


@pytest.mark.parametrize('source,source_id', [('tvdb', '42'), ('imdb', 'tt12345')])
def test_new_scan_uses_explicit_directory_identity_before_title_search(
        fresh_tv, monkeypatch, source, source_id):
    show = scan(fresh_tv, folder=f'AmbiguousTitle {{{source}-{source_id}}}')
    assert show['match_source'] == 'hint'
    tid = 920000 + fresh_tv[0]['id']
    lookups = []

    def find(value, kind):
        lookups.append((value, kind))
        return [{'id': tid}]

    monkeypatch.setattr(tv_ingest.tmdb, 'find_by_external_id', find)
    monkeypatch.setattr(tv_ingest.tmdb, 'search_tv',
                        lambda *_a, **_k: pytest.fail('explicit identity must precede title search'))
    monkeypatch.setattr(tv_ingest.tmdb, 'tv_detail', lambda value: {
        'id': value, 'name': 'Official title', 'overview': 'Official plot',
        'first_air_date': '2020-01-01', 'status': 'Ended', 'seasons': []})

    tv_ingest.prepare(ids=[show['id']], discovered=True)

    assert lookups == [(source_id, source + '_id')]
    current = store.get_show_meta(show['id'])
    assert current['tmdb_id'] == tid and current['match_source'] == 'hint'
    assert current['overview'] == 'Official plot' and not current['needs_review']


def test_previous_match_identifiers_are_not_reused_as_directory_hints(fresh_tv, monkeypatch):
    show = scan(fresh_tv)
    store.update_show_meta(show['id'], tvdb_id=42, imdb_id='tt12345', match_source='manual')
    monkeypatch.setattr(tv_ingest.tmdb, 'find_by_external_id',
                        lambda *_a, **_k: pytest.fail('previous match identifiers are not directory hints'))

    summary = tv_ingest.prepare(ids=[show['id']], discovered=True)

    assert summary['metadata_results'][0]['status'] == 'unmatched'
    current = store.get_show_meta(show['id'])
    assert current['tmdb_id'] is None and current['match_source'] == 'manual'
