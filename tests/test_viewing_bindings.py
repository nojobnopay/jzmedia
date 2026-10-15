"""Directory ownership and undo must never discard manual viewing restrictions."""
import json

import pytest

from app import store, viewing
from app.content_rules import normalize_content_evidence, normalize_nfo_evidence
from test_tv_bindings import lib as binding_library_fixture, plan, touch

lib = binding_library_fixture


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr('app.store._base.DB_PATH', str(tmp_path / 'bindings.db'))
    store.init_db()


def choice(show_id, private=False, override='auto'):
    with store._conn() as connection:
        connection.execute('UPDATE tv_shows SET is_private=?,children_override=? WHERE id=?',
                           (int(private), override, show_id))


def source(lib, name='Source', tmdb_id=90001):
    library, root = lib
    show_id = store.upsert_show(library['id'], name, 2009)
    store.update_show_meta(show_id, tmdb_id=tmdb_id, content_evidence=normalize_nfo_evidence('US:TV-G'))
    path = f'{name}/E01.mkv'
    touch(root, path)
    episode_id = store.upsert_episode(show_id, library['id'], path, 1, 1, title='Keep local episode')
    return show_id, episode_id


def assert_choice(show_id, private, override):
    row = store.get_show_meta(show_id)
    assert row['is_private'] == int(private) and row['children_override'] == override


@pytest.mark.parametrize('existing_target', [False, True])
def test_binding_merge_keeps_private_and_block_and_resets_old_episode_evidence(lib, existing_target):
    library, _root = lib
    sid, eid = source(lib)
    choice(sid, True, 'block')
    store.update_episode_meta(eid, tmdb_episode_id=801, content_evidence=normalize_nfo_evidence('US:TV-MA'))
    extra_id = store.upsert_tv_extra('Source/Extras/bonus.mkv', sid, library_id=library['id'])
    target_id = None
    if existing_target:
        target_id = store.upsert_show(library['id'], 'Existing target', 2009)
        store.update_show_meta(target_id, tmdb_id=32231, content_evidence=normalize_nfo_evidence('US:TV-G'))
        choice(target_id, False, 'allow')
    preview = plan(library, [('Source', 2)])
    assert preview['can_apply']
    session, _, _ = viewing.resolve(create=True)
    result = store.apply_tv_binding_plan(preview['token'])
    if existing_target:
        assert result['show_id'] == target_id
    target_id = result['show_id']
    assert_choice(target_id, True, 'block')
    assert not store.get_show_meta(sid)
    episode = store.get_episode(eid)
    assert episode['show_id'] == target_id and episode['children_auto_status'] == 'unknown'
    assert not normalize_content_evidence(episode['content_evidence'])['available']
    assert store.get_extra(extra_id)['show_id'] == target_id
    assert viewing.session_by_hash(session['token_hash'])['generation'] > session['generation']


def test_apply_reads_policy_changes_after_preview_from_source_and_destination(lib):
    library, _root = lib
    sid, _eid = source(lib)
    target = store.upsert_show(library['id'], 'Existing target', 2009)
    store.update_show_meta(target, tmdb_id=32231)
    preview = plan(library, [('Source', 2)])
    choice(sid, True, 'allow')
    choice(target, False, 'block')
    result = store.apply_tv_binding_plan(preview['token'])
    assert result['show_id'] == target
    assert_choice(target, True, 'block')


def test_private_extra_owner_is_included_in_merged_policy(lib):
    library, _root = lib
    source(lib)
    extra_owner = store.upsert_show(library['id'], 'Private extras owner', 2010)
    choice(extra_owner, True, 'block')
    extra_id = store.upsert_tv_extra('Source/Extras/bonus.mkv', extra_owner, library_id=library['id'])
    preview = plan(library, [('Source', 2)])
    result = store.apply_tv_binding_plan(preview['token'])
    assert_choice(result['show_id'], True, 'block')
    assert store.get_extra(extra_id)['show_id'] == result['show_id']


@pytest.mark.parametrize('legacy_history', [False, True])
def test_undo_retains_later_private_block_even_when_old_history_lacks_policy_fields(lib, legacy_history):
    library, _root = lib
    sid, eid = source(lib)
    preview = plan(library, [('Source', 2)])
    result = store.apply_tv_binding_plan(preview['token'])
    target = result['show_id']
    assert not store.get_show_meta(sid)
    choice(target, True, 'block')
    if legacy_history:
        record = store.get_tv_binding_plan(preview['token'])
        for row in record['payload']['before']['shows'] + record['payload']['before']['episodes']:
            for key in ('is_private', 'children_override', 'content_evidence', 'children_auto_status', 'children_reason'):
                row.pop(key, None)
        with store._conn() as connection:
            connection.execute('UPDATE tv_binding_history SET payload=? WHERE id=?',
                               (json.dumps(record['payload']), preview['token']))
    session, _, _ = viewing.resolve(create=True)
    store.undo_tv_binding_plan(preview['token'], False)
    assert_choice(sid, True, 'block')
    assert store.get_episode(eid)['show_id'] == sid
    assert store.get_episode(eid)['children_auto_status'] == 'unknown'
    assert viewing.session_by_hash(session['token_hash'])['generation'] > session['generation']


def test_undo_of_same_show_rebind_does_not_overwrite_later_manual_restrictions(lib):
    library, _root = lib
    sid, eid = source(lib, name='系列剧32231', tmdb_id=None)
    store.update_episode_meta(eid, content_evidence=normalize_nfo_evidence('US:TV-MA'))
    preview = plan(library, [('系列剧32231', 2)])
    assert preview['target']['show_id'] == sid and preview['target']['rebind']
    result = store.apply_tv_binding_plan(preview['token'])
    assert result['show_id'] == sid
    assert store.get_episode(eid)['children_auto_status'] == 'unknown'
    choice(sid, True, 'block')
    store.undo_tv_binding_plan(preview['token'], False)
    assert store.get_show_meta(sid)['tmdb_id'] is None
    assert_choice(sid, True, 'block')
    assert not normalize_content_evidence(store.get_episode(eid)['content_evidence'])['available']


def test_undo_preserves_later_restrictions_of_a_source_that_still_exists(lib):
    library, root = lib
    sid, eid = source(lib)
    touch(root, 'Outside/E02.mkv')
    store.upsert_episode(sid, library['id'], 'Outside/E02.mkv', 1, 2)
    preview = plan(library, [('Source', 2)])
    result = store.apply_tv_binding_plan(preview['token'])
    assert store.get_show_meta(sid)
    choice(sid, True, 'block')
    choice(result['show_id'], False, 'allow')
    store.undo_tv_binding_plan(preview['token'], False)
    assert_choice(sid, True, 'block')
    assert store.get_episode(eid)['show_id'] == sid


def test_scanner_episode_reparent_preserves_restrictions_and_rechecks_both_shows(lib):
    library, _root = lib
    sid, eid = source(lib)
    choice(sid, True, 'block')
    target = store.upsert_show(library['id'], 'Public destination', 2011)
    store.update_show_meta(target, tmdb_id=90002, content_evidence=normalize_nfo_evidence('US:TV-G'))
    choice(target, False, 'allow')
    store.update_episode_meta(eid, tmdb_episode_id=901, content_evidence=normalize_nfo_evidence('US:TV-MA'))
    store.save_progress(eid, 300, 1800, 'episode')
    session, _, _ = viewing.resolve(create=True)
    moved = store.upsert_episode(target, library['id'], 'Source/E01.mkv', 2, 1, title='Replacement')
    assert moved == eid
    assert_choice(target, True, 'block')
    assert store.get_episode(eid)['title'] == 'Keep local episode'
    assert store.get_episode(eid)['children_auto_status'] == 'unknown'
    assert store.get_progress(eid, 'episode')['position'] == 300
    assert store.get_show_meta(sid)['children_auto_status'] == 'allow'
    assert store.get_show_meta(target)['children_auto_status'] == 'allow'
    assert viewing.session_by_hash(session['token_hash'])['generation'] > session['generation']
    store.update_episode_meta(eid, content_evidence=normalize_nfo_evidence('US:TV-MA'))
    before = store.get_episode(eid)['content_evidence']
    store.upsert_episode(target, library['id'], 'Source/E01.mkv', 2, 1)
    assert store.get_episode(eid)['content_evidence'] == before
    assert store.get_show_meta(target)['children_auto_status'] == 'block'


def movie(library_id, path, tmdb_id):
    movie_id = store.upsert_movie_by_path(path, library_id=library_id)
    store.update_movie_meta(movie_id, tmdb_id=tmdb_id, title=path)
    return movie_id


def movie_choice(movie_id, private=False, override='auto'):
    with store._conn() as connection:
        connection.execute('UPDATE movies SET is_private=?,children_override=? WHERE id=?',
                           (int(private), override, movie_id))
    store.resync_fts(movie_id)


@pytest.mark.parametrize('operation', ['upsert', 'claim', 'repath'])
def test_movie_extra_reparent_keeps_policy_for_all_versions_and_resyncs_fts(lib, monkeypatch, operation):
    library, _root = lib
    sid = movie(library['id'], 'Source/movie.mkv', 60001)
    target = movie(library['id'], 'Target/movie.mkv', 60002)
    version = movie(library['id'], 'Target/other.mkv', 60002)
    movie_choice(sid, True, 'block')
    movie_choice(target, False, 'allow')
    extra_id = store.upsert_extra('Source/bonus.mkv', sid, library_id=library['id'])
    session, _, _ = viewing.resolve(create=True)
    synced = []
    original = store.resync_fts
    def resync(movie_id):
        synced.append(movie_id)
        original(movie_id)
    monkeypatch.setattr('app.store.search.resync_fts', resync)
    if operation == 'upsert':
        assert store.upsert_extra('Source/bonus.mkv', target, library_id=library['id']) == extra_id
    elif operation == 'claim':
        assert store.update_extra_movie(extra_id, target)
    else:
        moved = store.repath_extra_by_basename('bonus.mkv', 'Target/bonus.mkv', target, 'extra', library['id'])
        assert moved['id'] == extra_id
    assert store.get_extra(extra_id)['movie_id'] == target
    for movie_id in (target, version):
        row = store.get_movie(movie_id)
        assert row['is_private'] == 1 and row['children_override'] == 'block'
    assert {target, version} <= set(synced)
    assert viewing.session_by_hash(session['token_hash'])['generation'] > session['generation']


def test_repath_merges_restrictions_from_removed_duplicate_extras(lib):
    library, _root = lib
    public = movie(library['id'], 'Public/main.mkv', 61001)
    private = movie(library['id'], 'Private/main.mkv', 61002)
    target = movie(library['id'], 'Target/main.mkv', 61003)
    movie_choice(private, True, 'block')
    kept = store.upsert_extra('Public/bonus.mkv', public, library_id=library['id'])
    removed = store.upsert_extra('Private/bonus.mkv', private, library_id=library['id'])
    moved = store.repath_extra_by_basename('bonus.mkv', 'Target/bonus.mkv', target, 'extra', library['id'])
    assert moved['id'] == kept and not store.get_extra(removed)
    assert store.get_movie(target)['is_private'] == 1
    assert store.get_movie(target)['children_override'] == 'block'


@pytest.mark.parametrize('kind', ['movie', 'tv'])
@pytest.mark.parametrize('restriction', ['private', 'block'])
def test_unmatched_extra_rescan_keeps_restricted_parent(lib, kind, restriction):
    library, _root = lib
    if kind == 'movie':
        owner = movie(library['id'], 'Original/main.mkv', 62001)
        movie_choice(owner, restriction == 'private', 'block' if restriction == 'block' else 'auto')
        upsert, column = store.upsert_extra, 'movie_id'
    else:
        owner = store.upsert_show(library['id'], 'Original', 2010)
        choice(owner, restriction == 'private', 'block' if restriction == 'block' else 'auto')
        upsert, column = store.upsert_tv_extra, 'show_id'
    extra_id = upsert('Original/bonus.mkv', owner, library_id=library['id'])
    upsert('Original/bonus.mkv', None, library_id=library['id'])
    assert store.get_extra(extra_id)[column] == owner


@pytest.mark.parametrize('repair', [False, True])
def test_tv_extra_reparent_retains_restrictions_and_revokes_access(lib, repair):
    library, _root = lib
    target, _eid = source(lib)
    previous = store.upsert_show(library['id'], 'Old private extras owner', 2011)
    choice(previous, True, 'block')
    path = 'Source/Extras/bonus.mkv'
    extra_id = store.upsert_tv_extra(path, previous, library_id=library['id'])
    session, _, _ = viewing.resolve(create=True)
    if repair:
        assert store.reattach_tv_extras(library['id']) == 1
    else:
        assert store.upsert_tv_extra(path, target, library_id=library['id']) == extra_id
    assert store.get_extra(extra_id)['show_id'] == target
    assert_choice(target, True, 'block')
    assert viewing.session_by_hash(session['token_hash'])['generation'] > session['generation']


@pytest.mark.parametrize('kind', ['movie', 'tv'])
def test_orphan_claim_revokes_old_access_without_inventing_restrictions(lib, kind):
    library, _root = lib
    extra_id = store.upsert_extra('Orphan/bonus.mkv', None, library_id=library['id'])
    session, _, _ = viewing.resolve(create=True)
    if kind == 'movie':
        target = movie(library['id'], 'Target/main.mkv', 63001)
        assert store.update_extra_movie(extra_id, target)
        row = store.get_movie(target)
    else:
        target = store.upsert_show(library['id'], 'Claimed extras', 2010)
        store.upsert_tv_extra('Orphan/bonus.mkv', target, library_id=library['id'])
        row = store.get_show_meta(target)
    assert row['is_private'] == 0 and row['children_override'] == 'auto'
    assert viewing.session_by_hash(session['token_hash'])['generation'] > session['generation']


def test_extra_reassignment_between_movie_and_show_keeps_restrictions(lib):
    library, _root = lib
    film = movie(library['id'], 'Film/main.mkv', 64001)
    movie_choice(film, True, 'block')
    show = store.upsert_show(library['id'], 'Show', 2010)
    extra_id = store.upsert_extra('Shared/bonus.mkv', film, library_id=library['id'])
    store.upsert_tv_extra('Shared/bonus.mkv', show, library_id=library['id'])
    assert_choice(show, True, 'block')
    assert store.get_extra(extra_id)['movie_id'] is None
    target = movie(library['id'], 'New film/main.mkv', 64002)
    store.update_extra_movie(extra_id, target)
    assert store.get_extra(extra_id)['show_id'] is None
    assert store.get_movie(target)['is_private'] == 1
    assert store.get_movie(target)['children_override'] == 'block'


def test_binding_and_undo_preserve_movie_extra_owner_restrictions(lib):
    library, _root = lib
    source(lib)
    film = movie(library['id'], 'Film/main.mkv', 65001)
    movie_choice(film, True, 'block')
    extra_id = store.upsert_extra('Source/Extras/bonus.mkv', film, library_id=library['id'])
    preview = plan(library, [('Source', 2)])
    target = store.apply_tv_binding_plan(preview['token'])['show_id']
    assert_choice(target, True, 'block')
    assert store.get_extra(extra_id)['movie_id'] is None
    movie_choice(film, False, 'auto')
    store.undo_tv_binding_plan(preview['token'], False)
    assert store.get_extra(extra_id)['movie_id'] == film
    assert store.get_extra(extra_id)['show_id'] is None
    assert store.get_movie(film)['is_private'] == 1
    assert store.get_movie(film)['children_override'] == 'block'


@pytest.mark.parametrize('operation', ['binding', 'tv_extra', 'movie_extra'])
def test_source_allow_does_not_grant_children_access_to_another_work(lib, operation):
    library, _root = lib
    if operation == 'movie_extra':
        sid = movie(library['id'], 'Source/main.mkv', 66001)
        target = movie(library['id'], 'Target/main.mkv', 66002)
        movie_choice(sid, False, 'allow')
        store.update_movie_meta(target, content_evidence=normalize_nfo_evidence('US:R'))
        store.upsert_extra('Source/bonus.mkv', sid, library_id=library['id'])
        store.upsert_extra('Source/bonus.mkv', target, library_id=library['id'])
        row = store.get_movie(target)
    else:
        sid, _eid = source(lib)
        choice(sid, False, 'allow')
        target = store.upsert_show(library['id'], 'Adult target', 2009)
        store.update_show_meta(target, tmdb_id=32231, content_evidence=normalize_nfo_evidence('US:TV-MA'))
        if operation == 'binding':
            preview = plan(library, [('Source', 2)])
            assert store.apply_tv_binding_plan(preview['token'])['show_id'] == target
        else:
            store.upsert_tv_extra('Source/bonus.mkv', sid, library_id=library['id'])
            store.upsert_tv_extra('Source/bonus.mkv', target, library_id=library['id'])
        row = store.get_show_meta(target)
    assert row['children_override'] == 'auto'
    assert row['children_auto_status'] == 'block'


def test_undo_does_not_propagate_targets_allow_to_independent_source(lib):
    library, _root = lib
    sid, _eid = source(lib)
    target = store.upsert_show(library['id'], 'Existing target', 2009)
    store.update_show_meta(target, tmdb_id=32231)
    choice(target, False, 'allow')
    preview = plan(library, [('Source', 2)])
    store.apply_tv_binding_plan(preview['token'])
    store.undo_tv_binding_plan(preview['token'], False)
    assert_choice(sid, False, 'auto')
