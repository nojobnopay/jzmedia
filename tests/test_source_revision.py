"""External file replacements invalidate derived media data without changing identity."""
import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import library_paths, media, store
from app.main import app
from app.playback.source import source_revision
from app.routers.stream import common, subtitles
from app.storage import smb
from app.store import _base
from _smb_fake import FakeSmbClient

client = TestClient(app)


@pytest.fixture(params=['local', 'smb'])
def source_file(request, tmp_path, monkeypatch):
    root = tmp_path / 'media'
    root.mkdir()
    kwargs = {'path': str(root)}
    if request.param == 'smb':
        monkeypatch.setattr(smb, 'smbclient', FakeSmbClient(root))
        monkeypatch.setenv('SMB_DRIVER', 'direct')
        kwargs = {'source': 'smb', 'smb': {'host': 'fixture', 'share': 'media'}}
    lib = store.create_library(name='source-revision-' + tmp_path.name, kind='movie', **kwargs)
    library_paths.invalidate_cache()
    smb.invalidate()
    path = root / 'fixture.mkv'
    path.write_bytes(b'original')
    os.utime(path, (1700000000.125, 1700000000.125))
    mid = store.upsert_movie_by_path(path.name, library_id=lib['id'])
    calls = []

    def probe(url, size=None):
        calls.append((url, size))
        return info(duration=path.stat().st_size, attachments=[{'index': 3, 'name': 'fixture.ttf'}])

    monkeypatch.setattr(media, 'probe', probe)
    yield lib, path, mid, calls
    store.delete_media_library(lib['media_library_id'])
    library_paths.invalidate_cache()
    smb.invalidate()


def info(**extra):
    return {'playable': True, 'container': 'matroska', 'duration': 10, 'width': 640,
            'height': 360, 'vcodec': 'h264', 'audio': [], 'subs': [], 'attachments': [],
            'probe_ver': media.PROBE_VERSION, **extra}


def current(fixture):
    return common._version_source(fixture[2])


def test_signature_preserves_subsecond_time_and_all_identity_parts():
    base = store.media_source_revision(2, 'A/../B/a.mkv', 12, 1700000000.125)
    assert json.loads(base)[2] == 'B/a.mkv'
    for values in [(3, 'B/a.mkv', 12, 1700000000.125), (2, 'B/b.mkv', 12, 1700000000.125),
                   (2, 'B/a.mkv', 13, 1700000000.125), (2, 'B/a.mkv', 12, 1700000000.12501)]:
        assert store.media_source_revision(*values) != base
    with pytest.raises(ValueError):
        store.media_source_revision(2, 'a.mkv', 12, float('nan'))


def test_legacy_row_reprobes_once_then_validated_cache_is_reused(source_file):
    _, _, mid, calls = source_file
    store.upsert_media_info(mid, info(duration=999))
    row, src = current(source_file)
    result = common._media_cached_or_probe(row, src)
    assert result['duration'] != 999
    assert result['source_revision'] == source_revision(src)
    assert len(calls) == 1
    row, src = current(source_file)
    assert common._media_cached_or_probe(row, src)['source_revision'] == result['source_revision']
    assert len(calls) == 1


def test_same_path_replacement_detected_by_media_api_without_scanning(source_file):
    _, path, mid, calls = source_file
    first = client.get(f'/api/stream/{mid}/media').json()
    path.write_bytes(b'new-and-longer-source')
    os.utime(path, (1600000000.25, 1600000000.25))  # A copied file can have an older mtime.
    second = client.get(f'/api/stream/{mid}/media').json()
    assert first['duration'] != second['duration']
    assert second['duration'] == path.stat().st_size
    assert len(calls) == 2
    assert store.get_movie(mid)['file_path'] == path.name
    assert store.get_media_info(mid)['source_revision'] == source_revision(current(source_file)[1])


def test_same_size_and_same_second_but_precise_mtime_change_reprobes(source_file):
    _, path, mid, calls = source_file
    common._media_cached_or_probe(*current(source_file))
    first = store.get_media_info(mid)['source_revision']
    path.write_bytes(b'replaced')
    os.utime(path, (1700000000.1255, 1700000000.1255))
    common._media_cached_or_probe(*current(source_file))
    assert len(calls) == 2
    assert store.get_media_info(mid)['source_revision'] != first


def test_probe_source_change_does_not_commit_result(source_file, monkeypatch):
    _, path, mid, _ = source_file
    original = common._media_cached_or_probe(*current(source_file))
    path.write_bytes(b'replacement-start')

    def raced(*_args, **_kwargs):
        path.write_bytes(b'replaced-again-during-ffprobe')
        return info(duration=500)

    monkeypatch.setattr(media, 'probe', raced)
    with pytest.raises(HTTPException) as error:
        common._media_cached_or_probe(*current(source_file))
    assert error.value.status_code == 409
    assert store.get_media_info(mid)['source_revision'] == original['source_revision']
    assert store.get_media_info(mid)['duration'] != 500


@pytest.mark.parametrize('change', ['delete', 'repath'])
def test_probe_does_not_commit_after_catalogue_identity_disappears_or_moves(source_file, monkeypatch, change):
    _, path, mid, _ = source_file

    def probe(*_args, **_kwargs):
        with _base._lock, _base._conn() as connection:
            if change == 'delete':
                connection.execute('DELETE FROM movies WHERE id=?', (mid,))
            else:
                path.with_name('moved.mkv').write_bytes(path.read_bytes())
                connection.execute('UPDATE movies SET file_path=? WHERE id=?', ('moved.mkv', mid))
        return info(duration=500)

    monkeypatch.setattr(media, 'probe', probe)
    with pytest.raises(HTTPException) as error:
        common._media_cached_or_probe(*current(source_file))
    assert error.value.status_code == 409
    assert store.get_media_info(mid) is None


def test_transient_probe_failure_never_falls_back_to_another_source_revision(source_file, monkeypatch):
    _, path, mid, _ = source_file
    first = common._media_cached_or_probe(*current(source_file))
    path.write_bytes(b'replacement')
    monkeypatch.setattr(media, 'probe', lambda *_a, **_k:
                        {'playable': False, 'probe_error': 'ffprobe timeout'})
    result = common._media_cached_or_probe(*current(source_file))
    assert result['playable'] is False and result['probed_at'] == 0
    assert result['source_revision'] != first['source_revision']
    assert store.get_media_info(mid)['source_revision'] == first['source_revision']


def test_invalidate_keeps_progress_identity_and_active_leases(source_file):
    _, _, mid, _ = source_file
    common._media_cached_or_probe(*current(source_file))
    store.save_progress(mid, 12, 120)
    common._sessions['revision-fixture'] = {'vid': mid, 'kind': 'movie'}
    try:
        assert store.invalidate_media_info(mid)
        assert store.get_media_info(mid) is None
        assert store.get_movie(mid)
        assert store.get_progress(mid)['position'] == 12
        assert common._sessions['revision-fixture']['vid'] == mid
    finally:
        common._sessions.pop('revision-fixture', None)


def test_embedded_and_sidecar_subtitles_use_independent_exact_revisions(source_file, monkeypatch):
    lib, path, mid, _ = source_file
    side = path.with_suffix('.srt')
    side.write_bytes(b'1\noriginal')
    os.utime(side, (1700000000.125, 1700000000.125))
    calls = []

    def convert(dest, argv_of_tmp, **_kwargs):
        calls.append(argv_of_tmp(dest + '.tmp'))
        Path(dest).write_bytes(b'converted')

    monkeypatch.setattr(subtitles, '_run_ffmpeg_to_temp', convert)
    row, src = current(source_file)
    old_embedded = subtitles._extract_embedded(src, mid, {'ff_index': 2}, 0, 'ass')
    old_side = subtitles._convert_sidecar(subtitles._sidecar_src(row, side.name), mid, 'vtt')
    os.utime(old_embedded, (1900000000, 1900000000))
    os.utime(old_side, (1900000000, 1900000000))
    path.write_bytes(b'longer-video')
    os.utime(path, (1600000000, 1600000000))
    row, src = current(source_file)
    new_embedded = subtitles._extract_embedded(src, mid, {'ff_index': 2}, 0, 'ass')
    assert new_embedded != old_embedded
    assert subtitles._convert_sidecar(subtitles._sidecar_src(row, side.name), mid, 'vtt') == old_side
    side.write_bytes(b'1\nchanged-sidecar')
    os.utime(side, (1600000000, 1600000000))
    new_side = subtitles._convert_sidecar(subtitles._sidecar_src(row, side.name), mid, 'vtt')
    assert new_side != old_side
    assert len(calls) == 4
    assert Path(old_side).exists() and Path(old_embedded).exists()
    assert lib['id'] == src.backend.library_id


def test_subtitle_source_change_during_conversion_is_not_published(source_file, monkeypatch):
    _, path, mid, _ = source_file
    created = []

    def convert(dest, _argv, **_kwargs):
        Path(dest).write_bytes(b'incomplete-old-revision')
        created.append(dest)
        path.write_bytes(b'new-source-during-conversion')

    monkeypatch.setattr(subtitles, '_run_ffmpeg_to_temp', convert)
    with pytest.raises(HTTPException) as error:
        subtitles._extract_embedded(current(source_file)[1], mid, {'ff_index': 2}, 0, 'vtt')
    assert error.value.status_code == 409
    assert created and not Path(created[0]).exists()


def test_font_urls_and_artifacts_change_with_source_revision(source_file, monkeypatch):
    _, path, mid, _ = source_file
    dumps = []

    def dump(_input, directory, attachments):
        dumps.append(directory)
        Path(directory, 'fixture.ttf').write_bytes(path.read_bytes())

    monkeypatch.setattr(subtitles, '_dump_attachments', dump)
    old_url = next(f['url'] for f in client.get(f'/api/stream/{mid}/fonts').json()['fonts']
                   if f['source'] == 'attachment')
    assert client.get(old_url).content == b'original'
    path.write_bytes(b'new-font-source')
    os.utime(path, (1600000000, 1600000000))
    new_url = next(f['url'] for f in client.get(f'/api/stream/{mid}/fonts').json()['fonts']
                   if f['source'] == 'attachment')
    assert new_url != old_url
    assert client.get(new_url).content == b'new-font-source'
    assert len(dumps) == 2 and dumps[0] != dumps[1]
    assert client.get(old_url).status_code == 409
    assert Path(dumps[0], 'fixture.ttf').exists()


def test_source_revision_changes_hls_artifact_key_without_cancelling_old_tasks(source_file):
    _, path, _, _ = source_file
    row, src = current(source_file)
    plan = {'vcopy': True, 'seg': 'fmp4'}
    first = common._plan_marker(common._source_plan(plan, row, src), 0, 0)
    path.write_bytes(b'newer-payload')
    os.utime(path, (1600000000, 1600000000))
    row, src = current(source_file)
    second = common._plan_marker(common._source_plan(plan, row, src), 0, 0)
    assert first != second
    assert common._artifact_key(plan, 0, first) != common._artifact_key(plan, 0, second)


def test_schema_37_media_cache_upgrade_preserves_rows_as_unverified(tmp_path, monkeypatch):
    database = tmp_path / 'migration.sqlite'
    monkeypatch.setattr(_base, 'DB_PATH', str(database))
    monkeypatch.setattr(_base, 'ensure_dirs', lambda: None)
    store.init_db()
    with sqlite3.connect(database) as connection:
        connection.execute('ALTER TABLE media_info DROP COLUMN source_revision')
        connection.execute('ALTER TABLE scan_state DROP COLUMN source_revision')
        connection.execute("INSERT INTO media_info(kind,item_id,duration) VALUES('episode',9,120)")
        connection.execute('PRAGMA user_version=37')
    store.init_db()
    store.init_db()
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT source_revision,duration FROM media_info').fetchone() == ('', 120)
        assert 'source_revision' in {row[1] for row in connection.execute('PRAGMA table_info(scan_state)')}
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 38
