"""A metadata collision must require explicit directory review, never merge shows."""
from contextlib import closing
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import library_paths, store, viewing
from app.content_rules import normalize_nfo_evidence
from app.main import app
from app.routers import tv
from app.scanner import tv_match
from app.store import _base


client = TestClient(app)
TMDB_ID = 35610
TITLE = '犬夜叉'


def _detail(year=2000):
    return {'id': TMDB_ID, 'name': TITLE, 'original_name': 'Synthetic show',
            'first_air_date': f'{year}-01-01' if year is not None else '',
            'overview': 'Isolated matching fixture', 'genres': [],
            'seasons': [{'id': 100, 'season_number': 1, 'episode_count': 1}],
            'credits': {'cast': [], 'crew': []}}


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(_base, 'DB_PATH', str(tmp_path / 'matching.sqlite'))
    monkeypatch.setattr(_base, 'ensure_dirs', lambda: None)
    store.init_db()
    root = tmp_path / 'media'
    root.mkdir()
    library = store.create_library(name='matching-conflict', kind='tv', path=str(root))
    library_paths.invalidate_cache()
    existing = store.upsert_show(library['id'], TITLE, 2000)
    store.update_show_meta(existing, tmdb_id=TMDB_ID)
    incoming = store.upsert_show(library['id'], TITLE, None)
    episode = store.upsert_episode(incoming, library['id'], 'Incoming/S01E01.mkv', 1, 1)
    store.update_episode_meta(episode, title='Keep this episode', watched=1,
                              tmdb_episode_id=80101, match_source='manual',
                              content_evidence=normalize_nfo_evidence('US:TV-MA'))
    store.save_progress(episode, 321, 1200, kind='episode')
    paths = ['Incoming/S01E01.mkv']
    for number in range(5):
        path = f'Incoming/Movies/movie-{number}.mkv'
        store.upsert_tv_extra(path, incoming, kind='movie', library_id=library['id'])
        paths.append(path)
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'synthetic-file-do-not-change')
    with closing(_base._conn()) as connection, connection:
        connection.execute('INSERT INTO tv_directory_bindings VALUES(?,?,?,?,?,?)',
                           (library['id'], 'Existing', existing, 1, 0, 123))
    yield {'library': library, 'existing': existing, 'incoming': incoming,
           'episode': episode, 'root': root}
    library_paths.invalidate_cache()


def _state(catalog):
    tables = ('tv_shows', 'tv_seasons', 'tv_episodes', 'extras', 'tv_directory_bindings',
              'playback_progress', 'tmdb_cache')
    with closing(_base._conn()) as connection:
        rows = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in tables}
    return {'rows': rows, 'files': {path.relative_to(catalog['root']).as_posix(): path.read_bytes()
                                   for path in catalog['root'].rglob('*') if path.is_file()}}


def _forbid_later_work(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('a conflicting identity must be rejected before additional requests or writes')

    monkeypatch.setattr(tv.tmdb, 'tv_season', forbidden)
    monkeypatch.setattr(tv.tmdb, 'tv_aggregate_credits', forbidden)
    monkeypatch.setattr(tv.tmdb, 'download_image', forbidden)
    monkeypatch.setattr(tv.tv_persist, 'apply_tv_detail', forbidden)
    monkeypatch.setattr(tv.tv_persist, 'write_media_files', forbidden)


@pytest.mark.parametrize('offline', [False, True])
def test_match_collision_is_409_before_seasons_art_or_any_state_change(catalog, monkeypatch, offline):
    detail_calls = []
    detail = _detail()
    if offline:
        store.upsert_tmdb_cache(TMDB_ID, tv_match.tv_meta_from_detail(detail), media_type='tv')

    def fetched(tmdb_id):
        detail_calls.append(tmdb_id)
        if offline:
            raise RuntimeError('isolated offline fixture')
        return detail

    monkeypatch.setattr(tv.tmdb, 'tv_detail', fetched)
    _forbid_later_work(monkeypatch)
    before = _state(catalog)
    response = client.post(f"/api/tv/shows/{catalog['incoming']}/match", json={'tmdb_id': TMDB_ID})

    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'tv_show_identity_conflict'
    assert '同名同年' in response.json()['detail']
    assert '归属与季号' in response.json()['detail']
    assert '不会自动合并' in response.json()['detail']
    assert detail_calls == [TMDB_ID]
    assert _state(catalog) == before


def test_store_preflight_runs_before_episode_policy_is_cleared(catalog, monkeypatch):
    before = _state(catalog)

    def forbidden(*args, **kwargs):
        pytest.fail('policy evidence must not be processed for a conflicting show identity')

    monkeypatch.setattr(viewing, 'policy_metadata', forbidden)
    with pytest.raises(store.TVShowIdentityConflict):
        store.update_show_meta(catalog['incoming'], title=TITLE, year=2000, tmdb_id=TMDB_ID)
    assert _state(catalog) == before


def test_final_sqlite_identity_constraint_is_reclassified_and_rolls_back_evidence(catalog, monkeypatch):
    before = _state(catalog)
    # Simulate a writer winning after preflight. The real database constraint
    # must still reject this exact identity and roll back the earlier evidence update.
    monkeypatch.setattr(store.tv, '_ensure_show_identity_available', lambda *args: None)
    with pytest.raises(store.TVShowIdentityConflict) as raised:
        store.update_show_meta(catalog['incoming'], title=TITLE, year=2000, tmdb_id=TMDB_ID)
    assert isinstance(raised.value.__cause__, sqlite3.IntegrityError)
    assert _state(catalog) == before


def test_other_unique_constraint_is_not_reclassified(catalog):
    with closing(_base._conn()) as connection, connection:
        connection.execute('CREATE UNIQUE INDEX conflict_fixture_tmdb ON tv_shows(tmdb_id)')
    before = _state(catalog)
    with pytest.raises(sqlite3.IntegrityError, match=r'UNIQUE constraint failed: tv_shows\.tmdb_id'):
        store.update_show_meta(catalog['incoming'], tmdb_id=TMDB_ID)
    assert _state(catalog) == before


@pytest.mark.parametrize('message', ['fixture other constraint',
    'UNIQUE constraint failed: tv_shows.library_id, tv_shows.title, tv_shows.year'])
def test_other_integrity_error_is_not_reclassified(catalog, message):
    with closing(_base._conn()) as connection, connection:
        connection.execute("CREATE TRIGGER conflict_fixture_abort BEFORE UPDATE ON tv_shows"
                           f" BEGIN SELECT RAISE(ABORT,'{message}'); END")
    before = _state(catalog)
    with pytest.raises(sqlite3.IntegrityError) as raised:
        store.update_show_meta(catalog['incoming'], overview='changed')
    assert str(raised.value) == message
    assert raised.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_TRIGGER
    assert _state(catalog) == before


@pytest.mark.parametrize('year', ['2000', 2000.0, 0])
def test_preflight_obeys_sqlite_integer_affinity_and_nonnull_zero(catalog, year):
    if year == 0:
        store.update_show_meta(catalog['existing'], year=0)
    before = _state(catalog)
    with pytest.raises(store.TVShowIdentityConflict):
        store.ensure_show_identity_available(catalog['incoming'], year=year)
    assert _state(catalog) == before


@pytest.mark.parametrize('scenario', ['different_library', 'same_id', 'different_year', 'null_year'])
def test_match_legal_identities_are_allowed(catalog, monkeypatch, scenario):
    show_id = catalog['incoming']
    year = 2000
    if scenario == 'different_library':
        other_root = catalog['root'].parent / 'other-media'
        other_root.mkdir()
        other = store.create_library(name='other-conflict-library', kind='tv', path=str(other_root))
        show_id = store.upsert_show(other['id'], TITLE, None)
        library_paths.invalidate_cache()
    elif scenario == 'same_id':
        show_id = catalog['existing']
    elif scenario == 'different_year':
        year = 2001
    elif scenario == 'null_year':
        # Both rows will share a title and a NULL year, which SQLite permits.
        show_id = catalog['existing']
        year = None
    monkeypatch.setattr(tv.tmdb, 'tv_detail', lambda _: _detail(year))
    monkeypatch.setattr(tv.tmdb, 'tv_season', lambda *args: {'episodes': []})
    monkeypatch.setattr(tv.tmdb, 'tv_aggregate_credits', lambda _: {'cast': [], 'crew': []})
    monkeypatch.setattr(tv.tmdb, 'download_image', lambda *args, **kwargs: False)
    monkeypatch.setattr(tv.tv_persist, 'write_media_files', lambda *args: {'queued': False})
    store.ensure_show_identity_available(show_id, title=TITLE, year=year)

    response = client.post(f'/api/tv/shows/{show_id}/match', json={'tmdb_id': TMDB_ID})

    assert response.status_code == 200, response.text
    assert store.get_show_meta(show_id)['title'] == TITLE
    assert store.get_show_meta(show_id)['year'] == year
    assert store.get_show_meta(show_id)['tmdb_id'] == TMDB_ID
    assert store.get_show_meta(catalog['incoming']) is not None
    assert store.get_show_meta(catalog['existing']) is not None
    assert store.get_progress(catalog['episode'], kind='episode')['position'] == 321
    assert len(store.list_extras_by_show(catalog['incoming'])) == 5
