"""FileStation-style TV changes, using isolated local and fake SMB files only."""
import os

import pytest

from app import library_paths, scanner, storage, store
from app.storage import smb
from _smb_fake import FakeSmbClient


@pytest.fixture(params=['local', 'smb'])
def external_tv(request, tmp_path, monkeypatch):
    root = tmp_path / 'media'
    root.mkdir()
    kwargs = {'path': str(root)}
    fake = None
    if request.param == 'smb':
        fake = FakeSmbClient(root)
        monkeypatch.setattr(smb, 'smbclient', fake)
        monkeypatch.setenv('SMB_DRIVER', 'direct')
        kwargs = {'source': 'smb', 'smb': {'host': 'fixture', 'share': 'media'}}
    lib = store.create_library(name='external-tv-' + tmp_path.name, kind='tv',
                               artwork_mode='none', **kwargs)
    library_paths.invalidate_cache()
    smb.invalidate()
    sid = store.upsert_show(lib['id'], 'Synthetic', 2020)
    original_backend_for = storage.backend_for
    backend = original_backend_for(lib['id'])
    # Pin the isolated backend so tests can simulate precise traversal failures.
    monkeypatch.setattr(storage, 'backend_for', lambda lid=None:
                        backend if lid == lib['id'] else original_backend_for(lid))
    yield {'lib': lib, 'root': root, 'sid': sid, 'backend': backend, 'fake': fake}
    store.delete_media_library(lib['media_library_id'])
    library_paths.invalidate_cache()
    smb.invalidate()


def write(fixture, rel, data=b'original', mtime=1700000000.125):
    path = fixture['root'] / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def seed(fixture, rel='Synthetic (2020)/Season 01/old.S01E04.mkv', *, show_id=None,
         protection='manual'):
    path = write(fixture, rel)
    result = scanner.scan_tv_file(fixture['backend'], rel,
                                  target_show_id=show_id or fixture['sid'])
    assert result['status'] == 'tv_ok'
    eid = result['episode_id']
    store.update_episode_meta(eid, title='Retained episode title', overview='Retained description',
                              tmdb_episode_id=987654 if protection == 'manual' else None,
                              match_source='manual' if protection == 'manual' else '',
                              local_only=int(protection == 'local'), watched=1, watched_at=1700000001)
    store.save_progress(eid, 321, 1200, kind='episode')
    state = store.get_scan_state(rel, fixture['lib']['id'])
    store.upsert_media_info(eid, {'playable': True, 'duration': 1200, 'vcodec': 'h264',
                                  'audio': [], 'subs': [], 'attachments': []}, kind='episode',
                            source_revision=state.get('source_revision'))
    return eid, path, store.get_episode(eid)


def assert_identity(fixture, eid, before, rel):
    current = store.get_episode(eid)
    assert current is not None
    assert current['file_path'] == rel
    for key in ('id', 'show_id', 'season', 'episode', 'episode_end', 'tmdb_episode_id',
                'title', 'overview', 'match_source', 'local_only', 'watched', 'watched_at'):
        assert current[key] == before[key], key
    assert store.get_progress(eid, kind='episode')['position'] == 321
    return current


@pytest.mark.parametrize('cancelled', [False, True])
def test_known_copy_waits_for_actual_worker_exit_before_reconciling(external_tv, monkeypatch, cancelled):
    from app.jobkit import JobRegistry
    from app.routers.fs import copy as fs_copy

    fixture = external_tv
    eid, path, before = seed(fixture)
    path.unlink()
    write(fixture, 'Synthetic (2020)/Season 01/new.S01E04.mkv')
    registry = JobRegistry(prefix='external-copy')
    monkeypatch.setattr(fs_copy, '_COPY_JOBS', registry)
    job = registry.create(library_id=fixture['lib']['id'], worker_finished=False)
    if cancelled:
        registry.update(job['job_id'], state='cancelled')

    pending = scanner.scan_tv_show(fixture['sid'])

    assert pending['reconciliation']['incomplete']
    assert pending['reconciliation']['replaced'] == 0
    assert len(store.list_episodes(fixture['sid'])) == 1
    assert_identity(fixture, eid, before, before['file_path'])
    registry.update(job['job_id'], state='done', worker_finished=True)

    completed = scanner.scan_tv_show(fixture['sid'])

    assert completed['reconciliation']['replaced'] == 1
    assert_identity(fixture, eid, before, 'Synthetic (2020)/Season 01/new.S01E04.mkv')


def test_copy_started_during_snapshot_prevents_identity_commit(external_tv, monkeypatch):
    from app.routers.fs import copy as fs_copy

    fixture = external_tv
    eid, path, before = seed(fixture)
    path.unlink()
    write(fixture, 'Synthetic (2020)/Season 01/new.S01E04.mkv')
    checks = []

    def active(library_id=None):
        checks.append(library_id)
        return [] if len(checks) == 1 else [{'library_id': fixture['lib']['id']}]

    monkeypatch.setattr(fs_copy, 'active_copy_jobs', active)

    result = scanner.scan_tv_show(fixture['sid'])

    assert len(checks) == 2
    assert result['reconciliation']['incomplete']
    assert_identity(fixture, eid, before, before['file_path'])


@pytest.mark.parametrize('protection', ['manual', 'local'])
@pytest.mark.parametrize('data,mtime', [
    (b'replaced', 1700000000.875),  # Same size, same integer second, different fraction.
    (b'replaced', 1600000000.25),  # A copied replacement can have an older timestamp.
    (b'a longer replacement source', 1700000000.125),  # Size alone can change.
])
def test_same_path_replacement_keeps_identity_and_invalidates_probe(external_tv, protection, data, mtime):
    fixture = external_tv
    eid, path, before = seed(fixture, protection=protection)
    previous_revision = store.get_scan_state(before['file_path'], fixture['lib']['id']).get('source_revision')
    write(fixture, before['file_path'], data, mtime)

    result = scanner.scan_tv_show(fixture['sid'])

    current = assert_identity(fixture, eid, before, before['file_path'])
    assert current['missing'] == 0
    assert store.get_media_info(eid, kind='episode') is None
    assert len(store.list_episodes(fixture['sid'])) == 1
    assert result['reconciliation']['changed'] == 1
    assert result['reconciliation']['replaced'] == 0
    assert result['reconciliation']['removed'] == 0
    state = store.get_scan_state(before['file_path'], fixture['lib']['id'])
    assert state['source_revision'] != previous_revision
    assert state['size'] == path.stat().st_size


@pytest.mark.parametrize('protection', ['manual', 'local'])
@pytest.mark.parametrize('suffix', ['S01E04', 'S01E04-E05'])
def test_unique_new_path_with_the_same_complete_coverage_inherits_identity(external_tv, protection, suffix):
    fixture = external_tv
    old = f'Synthetic (2020)/Season 01/old.{suffix}.mkv'
    new = f'Synthetic (2020)/Season 01/replacement.{suffix}.mkv'
    eid, path, before = seed(fixture, old, protection=protection)
    path.unlink()
    replacement = write(fixture, new, b'a larger replacement source', 1600000000.25)

    result = scanner.scan_tv_show(fixture['sid'])

    current = assert_identity(fixture, eid, before, new)
    assert current['missing'] == 0
    assert store.get_episode_by_path(old, fixture['lib']['id']) is None
    assert store.get_scan_state(old, fixture['lib']['id']) is None
    assert store.get_media_info(eid, kind='episode') is None
    assert store.get_episode_by_path(new, fixture['lib']['id'])['id'] == eid
    assert len(store.list_episodes(fixture['sid'])) == 1
    assert result['reconciliation']['replaced'] == 1
    assert result['reconciliation']['changed'] == 0
    assert result['reconciliation']['removed'] == 0
    assert result['added'] == 0
    state = store.get_scan_state(new, fixture['lib']['id'])
    assert state['size'] == replacement.stat().st_size
    assert state['source_revision'] == store.media_source_revision(
        fixture['lib']['id'], new, replacement.stat().st_size, fixture['backend'].stat(new).mtime)
    again = scanner.scan_tv_show(fixture['sid'])
    assert again['reconciliation']['replaced'] == again['reconciliation']['changed'] == 0
    assert_identity(fixture, eid, before, new)


def test_a_second_file_is_a_version_when_the_original_still_exists(external_tv):
    fixture = external_tv
    eid, _, before = seed(fixture)
    new = 'Synthetic (2020)/Season 01/replacement.S01E04.mkv'
    write(fixture, new, b'new release')

    result = scanner.scan_tv_show(fixture['sid'])

    assert_identity(fixture, eid, before, before['file_path'])
    replacement = store.get_episode_by_path(new, fixture['lib']['id'])
    assert replacement and replacement['id'] != eid
    assert len(store.list_episodes(fixture['sid'])) == 2
    assert result['added'] == 1
    assert result['reconciliation']['replaced'] == result['reconciliation']['removed'] == 0


@pytest.mark.parametrize('old_suffix,new_suffixes', [
    ('S01E04', ['release-a.S01E04', 'release-b.S01E04']),
    ('S01E04-E05', ['replacement.S01E04']),
    ('S01E04', ['replacement.S01E04-E05']),
    ('S01E04-part1', ['replacement.S01E04-part2']),
    ('S01E04-part1', ['replacement.S01E04']),
])
def test_ambiguous_or_different_coverage_retains_missing_identity_for_review(external_tv, old_suffix, new_suffixes):
    fixture = external_tv
    old = f'Synthetic (2020)/Season 01/old.{old_suffix}.mkv'
    eid, path, before = seed(fixture, old)
    path.unlink()
    for name in new_suffixes:
        write(fixture, f'Synthetic (2020)/Season 01/{name}.mkv', b'replacement')

    result = scanner.scan_tv_show(fixture['sid'])

    assert assert_identity(fixture, eid, before, old)['missing'] == 1
    assert result['reconciliation']['replaced'] == result['reconciliation']['removed'] == 0
    assert result['reconciliation']['review'] >= 1
    assert result['reconciliation']['issues']


def test_two_missing_versions_are_not_collapsed_into_one_new_file(external_tv):
    fixture = external_tv
    first, first_path, first_before = seed(fixture)
    second, second_path, second_before = seed(fixture, 'Synthetic (2020)/Season 01/other.S01E04.mkv')
    first_path.unlink()
    second_path.unlink()
    write(fixture, 'Synthetic (2020)/Season 01/new.S01E04.mkv', b'replacement')

    result = scanner.scan_tv_show(fixture['sid'])

    assert assert_identity(fixture, first, first_before, first_before['file_path'])['missing'] == 1
    assert assert_identity(fixture, second, second_before, second_before['file_path'])['missing'] == 1
    assert result['reconciliation']['replaced'] == result['reconciliation']['removed'] == 0
    assert result['reconciliation']['review'] >= 2


def test_unconfirmed_absolute_number_does_not_transfer_identity(external_tv):
    fixture = external_tv
    eid, path, before = seed(fixture, 'Synthetic (2020)/004.mkv')
    assert before['absolute_number'] == 4
    path.unlink()
    write(fixture, 'Synthetic (2020)/replacement.S01E04.mkv')

    result = scanner.scan_tv_show(fixture['sid'])

    assert assert_identity(fixture, eid, before, before['file_path'])['missing'] == 1
    assert result['reconciliation']['replaced'] == result['reconciliation']['removed'] == 0
    assert result['reconciliation']['issues']


def test_complete_show_check_removes_only_its_missing_records(external_tv):
    fixture = external_tv
    missing_id, missing_path, before = seed(fixture)
    kept_id, _, kept = seed(fixture, 'Synthetic (2020)/Season 01/kept.S01E05.mkv')
    other_sid = store.upsert_show(fixture['lib']['id'], 'Other Synthetic', 2020)
    other_id, other_path, other = seed(fixture, 'Other Synthetic (2020)/Season 01/other.S01E04.mkv', show_id=other_sid)
    missing_path.unlink()
    other_path.unlink()

    result = scanner.scan_tv_show(fixture['sid'])

    assert store.get_episode(missing_id) is None
    assert store.get_progress(missing_id, kind='episode') is None
    assert store.get_media_info(missing_id, kind='episode') is None
    assert store.get_scan_state(before['file_path'], fixture['lib']['id']) is None
    assert_identity(fixture, kept_id, kept, kept['file_path'])
    assert_identity(fixture, other_id, other, other['file_path'])
    assert result['reconciliation']['removed'] == 1
    assert result['reconciliation']['replaced'] == 0


def test_orphan_scan_state_does_not_hide_a_file_that_needs_registration(external_tv):
    fixture = external_tv
    eid, _, before = seed(fixture)
    seed(fixture, 'Synthetic (2020)/Season 01/kept.S01E05.mkv')
    store.delete_episode_by_path(before['file_path'], fixture['lib']['id'])
    assert store.get_scan_state(before['file_path'], fixture['lib']['id']) is not None

    scanner.scan_tv_show(fixture['sid'])

    registered = store.get_episode_by_path(before['file_path'], fixture['lib']['id'])
    assert registered and registered['id'] != eid
    assert len(store.list_episodes(fixture['sid'])) == 2


@pytest.mark.parametrize('failure', ['offline_before', 'partial_walk', 'offline_after_snapshot'])
def test_failed_or_partial_enumeration_never_removes_or_replaces_records(external_tv, monkeypatch, failure):
    fixture = external_tv
    eid, path, before = seed(fixture)
    path.unlink()
    new = 'Synthetic (2020)/Season 01/new.S01E04.mkv'
    write(fixture, new)
    original = fixture['backend'].iter_tree
    walks = 0

    def fail_walk(*args, **kwargs):
        nonlocal walks
        walks += 1
        if failure == 'offline_before' or failure == 'offline_after_snapshot' and walks > 1:
            raise storage.StorageOffline('isolated disconnected storage')
        for entry in original(*args, **kwargs):
            yield entry
            if failure == 'partial_walk':
                raise storage.StorageOffline('isolated interrupted traversal')

    monkeypatch.setattr(fixture['backend'], 'iter_tree', fail_walk)
    try:
        result = scanner.scan_tv_show(fixture['sid'])
    except storage.StorageError:
        result = None
    assert_identity(fixture, eid, before, before['file_path'])
    assert store.get_episode_by_path(new, fixture['lib']['id']) is None
    if result is not None:
        assert result['reconciliation']['incomplete'] is True
        assert result['reconciliation']['replaced'] == result['reconciliation']['removed'] == 0


@pytest.mark.parametrize('bad_attributes', ['zero_size', 'zero_mtime', 'temporary_name'])
def test_incomplete_copy_keeps_the_missing_old_record(external_tv, bad_attributes):
    fixture = external_tv
    eid, path, before = seed(fixture)
    path.unlink()
    name = 'new.S01E04.tmp.mkv' if bad_attributes == 'temporary_name' else 'new.S01E04.mkv'
    write(fixture, 'Synthetic (2020)/Season 01/' + name,
          b'' if bad_attributes == 'zero_size' else b'replacement',
          0 if bad_attributes == 'zero_mtime' else 1700000000.125)

    result = scanner.scan_tv_show(fixture['sid'])

    assert_identity(fixture, eid, before, before['file_path'])
    assert result['reconciliation']['replaced'] == result['reconciliation']['removed'] == 0
    assert result['reconciliation']['issues']


@pytest.mark.parametrize('mutation_walk', [1, 2])
def test_copy_changing_during_enumeration_or_before_commit_prevents_pairing(external_tv, monkeypatch, mutation_walk):
    fixture = external_tv
    eid, path, before = seed(fixture)
    path.unlink()
    new = 'Synthetic (2020)/Season 01/new.S01E04.mkv'
    write(fixture, new)
    original = fixture['backend'].iter_tree
    walks = 0

    def changing_walk(*args, **kwargs):
        nonlocal walks
        walks += 1
        current_walk = walks
        yield from original(*args, **kwargs)
        if current_walk == mutation_walk:
            write(fixture, new, b'a replacement that is still growing', 1700000001.5)

    monkeypatch.setattr(fixture['backend'], 'iter_tree', changing_walk)
    result = scanner.scan_tv_show(fixture['sid'])

    assert_identity(fixture, eid, before, before['file_path'])
    assert store.get_episode_by_path(new, fixture['lib']['id']) is None
    assert result['reconciliation']['incomplete'] is True
    assert result['reconciliation']['replaced'] == result['reconciliation']['removed'] == 0


@pytest.mark.parametrize('cancel_after_walk', [0, 1, 2])
def test_cancelled_check_keeps_old_identity_and_progress(external_tv, monkeypatch, cancel_after_walk):
    fixture = external_tv
    eid, path, before = seed(fixture)
    path.unlink()
    new = 'Synthetic (2020)/Season 01/new.S01E04.mkv'
    write(fixture, new)
    original = fixture['backend'].iter_tree
    walks = 0
    cancelled = cancel_after_walk == 0

    def cancelling_walk(*args, **kwargs):
        nonlocal walks, cancelled
        walks += 1
        current_walk = walks
        yield from original(*args, **kwargs)
        if current_walk == cancel_after_walk:
            cancelled = True

    monkeypatch.setattr(fixture['backend'], 'iter_tree', cancelling_walk)
    result = scanner.scan_tv_show(fixture['sid'], should_stop=lambda: cancelled)

    assert_identity(fixture, eid, before, before['file_path'])
    assert store.get_episode_by_path(new, fixture['lib']['id']) is None
    assert result['reconciliation']['incomplete'] is True
    assert result['reconciliation']['replaced'] == result['reconciliation']['removed'] == 0


def test_library_scan_uses_the_same_reconciliation_and_reports_each_outcome_once(external_tv):
    fixture = external_tv
    replaced_id, replaced_path, replaced = seed(fixture)
    changed_id, _, changed = seed(fixture, 'Synthetic (2020)/Season 01/kept.S01E05.mkv')
    removed_id, removed_path, _ = seed(fixture, 'Synthetic (2020)/Season 01/deleted.S01E06.mkv')
    review_id, review_path, review = seed(fixture, 'Synthetic (2020)/Season 01/ambiguous.S01E07.mkv')
    replaced_path.unlink()
    removed_path.unlink()
    review_path.unlink()
    new = 'Synthetic (2020)/Season 01/replacement.S01E04.mkv'
    write(fixture, new, b'a different release')
    write(fixture, changed['file_path'], b'replaced', 1700000000.875)
    for name in ['version-a.S01E07.mkv', 'version-b.S01E07.mkv']:
        write(fixture, 'Synthetic (2020)/Season 01/' + name)

    rows = scanner.scan_all(library_id=fixture['lib']['id'])

    assert_identity(fixture, replaced_id, replaced, new)
    assert_identity(fixture, changed_id, changed, changed['file_path'])
    assert store.get_media_info(changed_id, kind='episode') is None
    assert store.get_episode(removed_id) is None
    assert assert_identity(fixture, review_id, review, review['file_path'])['missing'] == 1
    for status in ['replaced_episode', 'changed_episode', 'removed_episode', 'tv_reconcile_review']:
        assert sum(row.get('count', 0) for row in rows if row.get('status') == status) == 1, rows


@pytest.mark.parametrize('suffix', ['.part', '.tmp', '.download', '.part-12345'])
def test_paused_copy_with_a_nonvideo_temporary_extension_keeps_old_identity(external_tv, suffix):
    fixture = external_tv
    eid, path, before = seed(fixture)
    path.unlink()
    write(fixture, 'Synthetic (2020)/Season 01/new.S01E04.mkv' + suffix)
    result = scanner.scan_tv_show(fixture['sid'])
    assert_identity(fixture, eid, before, before['file_path'])
    assert result['reconciliation']['incomplete']
    assert result['reconciliation']['removed'] == result['reconciliation']['replaced'] == 0


@pytest.mark.parametrize('marker', ['.part1', ' part1', '-CD1', '-disc1'])
def test_unconfirmed_split_naming_is_not_treated_as_a_whole_episode(external_tv, marker):
    fixture = external_tv
    eid, path, before = seed(fixture, f'Synthetic (2020)/Season 01/old.S01E04{marker}.mkv')
    path.unlink()
    write(fixture, 'Synthetic (2020)/Season 01/new.S01E04.mkv')
    result = scanner.scan_tv_show(fixture['sid'])
    assert assert_identity(fixture, eid, before, before['file_path'])['missing'] == 1
    assert result['reconciliation']['replaced'] == result['reconciliation']['removed'] == 0
    assert result['reconciliation']['review']


def test_existing_overlapping_multi_episode_prevents_exact_single_episode_pairing(external_tv):
    fixture = external_tv
    eid, path, before = seed(fixture)
    seed(fixture, 'Synthetic (2020)/Season 01/combined.S01E04-E05.mkv')
    path.unlink()
    write(fixture, 'Synthetic (2020)/Season 01/new.S01E04.mkv')
    result = scanner.scan_tv_show(fixture['sid'])
    assert assert_identity(fixture, eid, before, before['file_path'])['missing'] == 1
    assert result['reconciliation']['replaced'] == result['reconciliation']['removed'] == 0
    assert result['reconciliation']['review']


def test_cancel_during_final_storage_verification_prevents_commit(external_tv, monkeypatch):
    fixture = external_tv
    eid, path, before = seed(fixture)
    path.unlink()
    write(fixture, 'Synthetic (2020)/Season 01/new.S01E04.mkv')
    original = fixture['backend'].exists
    cancelled = False

    def exists(path):
        nonlocal cancelled
        result = original(path)
        cancelled = True
        return result

    monkeypatch.setattr(fixture['backend'], 'exists', exists)
    result = scanner.scan_tv_show(fixture['sid'], should_stop=lambda: cancelled)
    assert_identity(fixture, eid, before, before['file_path'])
    assert result['reconciliation']['incomplete']
    assert result['reconciliation']['replaced'] == result['reconciliation']['removed'] == 0


def test_unknown_show_root_cannot_claim_a_different_shows_file(external_tv):
    fixture = external_tv
    eid, path, before = seed(fixture, 'S01E04.mkv')
    path.unlink()
    write(fixture, 'Other (2020)/Season 01/new.S01E04.mkv')
    result = scanner.scan_tv_show(fixture['sid'])
    assert_identity(fixture, eid, before, before['file_path'])
    assert result['reconciliation']['incomplete']
    assert not store.get_episode_by_path('Other (2020)/Season 01/new.S01E04.mkv', fixture['lib']['id'])


def test_unified_metadata_result_reports_replacement_without_requiring_organization(external_tv):
    from app import tv_ingest
    fixture = external_tv
    eid, path, before = seed(fixture)
    path.unlink()
    new = 'Synthetic (2020)/Season 01/new.S01E04.mkv'
    write(fixture, new)
    store.update_show_meta(fixture['sid'], tmdb_id=70000 + fixture['sid'], fetched_at=1,
                           overview='Complete metadata', status='Ended', poster_path='fixture.jpg')
    result = tv_ingest.prepare(ids=[fixture['sid']], actions=['season'])
    assert result['metadata_results'][0]['files']['replaced'] == 1
    assert result['metadata_results'][0]['files']['removed'] == 0
    assert result['total'] == 0 and result['auto_prompt'] is False
    assert_identity(fixture, eid, before, new)


@pytest.mark.parametrize('replacement', ['same', 'renamed'])
@pytest.mark.parametrize('policy', ['nfo', 'none', 'read_only'])
def test_complete_metadata_replacement_restores_sidecars_only_by_library_policy(
        external_tv, monkeypatch, replacement, policy):
    from app import tv_ingest
    fixture = external_tv
    _, path, before = seed(fixture)
    store.update_show_meta(fixture['sid'], tmdb_id=70000 + fixture['sid'], fetched_at=1,
                           overview='Complete metadata', status='Ended', poster_path='fixture.jpg')
    store.update_library(fixture['lib']['id'], artwork_mode='none' if policy == 'none' else 'nfo')
    store.update_media_library(fixture['lib']['media_library_id'], read_only=policy == 'read_only')
    library_paths.invalidate_cache()
    monkeypatch.setenv('TV_EPISODE_NFO', '1')
    new = before['file_path'] if replacement == 'same' else 'Synthetic (2020)/Season 01/new.S01E04.mkv'
    path.unlink()
    new_path = write(fixture, new, b'replacement source', 1700000001.875)

    result = tv_ingest.prepare(ids=[fixture['sid']], actions=['season'])

    assert result['metadata_results'][0]['filled'] == 0
    sidecar = new_path.with_suffix('.nfo')
    assert sidecar.exists() == (policy == 'nfo')
    if sidecar.exists():
        assert 'Retained episode title' in sidecar.read_text()


def test_replacement_sidecar_keeps_external_edits(external_tv, monkeypatch):
    from app import tv_ingest
    from app.scanner import tv_persist
    fixture = external_tv
    _, path, before = seed(fixture)
    store.update_show_meta(fixture['sid'], tmdb_id=70000 + fixture['sid'], fetched_at=1,
                           overview='Complete metadata', status='Ended', poster_path='fixture.jpg')
    store.update_library(fixture['lib']['id'], artwork_mode='nfo')
    library_paths.invalidate_cache()
    monkeypatch.setenv('TV_EPISODE_NFO', '1')
    tv_persist.write_media_files(fixture['sid'], fixture['lib']['id'])
    assert store.get_episode(before['id'])['nfo_hash']
    path.unlink()
    new_path = write(fixture, 'Synthetic (2020)/Season 01/new.S01E04.mkv')
    sidecar = new_path.with_suffix('.nfo')
    sidecar.write_text('Externally maintained NFO')

    tv_ingest.prepare(ids=[fixture['sid']], actions=['season'])

    assert sidecar.read_text() == 'Externally maintained NFO'


def test_incomplete_copy_is_durable_pending_without_metadata_writes_or_physical_plan(external_tv, monkeypatch):
    from app import tv_ingest
    fixture = external_tv
    eid, path, before = seed(fixture)
    path.unlink()
    write(fixture, 'Synthetic (2020)/Season 01/new.S01E04.mkv.part')
    monkeypatch.setattr(tv_ingest, '_metadata', lambda *_: pytest.fail('metadata must wait for stable files'))
    result = tv_ingest.prepare(ids=[fixture['sid']])
    metadata = result['metadata_results'][0]
    assert metadata['files']['incomplete'] and metadata['issues']
    assert result['total'] == 0 and result['auto_prompt'] is False
    assert store.tv_ingest.get_review(fixture['sid'])['state'] == 'pending'
    assert_identity(fixture, eid, before, before['file_path'])


def test_missing_retained_version_is_excluded_from_physical_organization(external_tv):
    fixture = external_tv
    eid, path, before = seed(fixture)
    path.unlink()
    for name in ['a', 'b']:
        write(fixture, f'Synthetic (2020)/Season 01/{name}.S01E04.mkv')
    scanner.scan_tv_show(fixture['sid'])
    store.update_show_meta(fixture['sid'], tmdb_id=70000 + fixture['sid'])
    for episode in store.list_episodes(fixture['sid']):
        if episode['id'] != eid:
            store.update_episode_meta(episode['id'], tmdb_episode_id=100 + episode['id'], title='New release')
    planned = scanner.plan_tv_organize(ids=[fixture['sid']], actions=['rename'])['plans']
    assert planned
    assert all(move['from'] != before['file_path'] for plan in planned for move in plan['file_moves'])
    assert sum(sum(plan['dir_totals'].values()) for plan in planned) == 2


def test_missing_retained_version_does_not_recreate_orphan_nfo(external_tv):
    from app.scanner import tv_nfo_link
    fixture = external_tv
    _, path, _ = seed(fixture)
    path.unlink()
    for name in ['a', 'b']:
        write(fixture, f'Synthetic (2020)/Season 01/{name}.S01E04.mkv')
    scanner.scan_tv_show(fixture['sid'])

    result = tv_nfo_link.sync_tv_nfos_for(fixture['sid'], backend=fixture['backend'], episode_nfo=True)

    assert result['ok']
    assert not path.with_suffix('.nfo').exists()
    assert (path.parent / 'a.S01E04.nfo').exists()
    assert (path.parent / 'b.S01E04.nfo').exists()


def test_retained_missing_progress_is_not_used_as_a_playback_target_and_recovers_on_return(external_tv):
    fixture = external_tv
    eid, path, before = seed(fixture)
    store.update_episode_meta(eid, watched=0)
    path.unlink()
    for name in ['a', 'b']:
        write(fixture, f'Synthetic (2020)/Season 01/{name}.S01E04.mkv')
    scanner.scan_tv_show(fixture['sid'])
    assert store.next_episode(fixture['sid'])['id'] != eid
    assert store.list_recent_played_tv(library_ids=[fixture['lib']['id']]) == []
    assert store.get_progress(eid, kind='episode')['position'] == 321
    write(fixture, before['file_path'])
    scanner.scan_tv_show(fixture['sid'])
    assert store.get_episode(eid)['missing'] == 0
    assert store.next_episode(fixture['sid'])['id'] == eid
