"""File previews are scoped reads, including unregistered files on read-only SMB."""
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app import library_paths, storage, store
from app.main import app
from app.storage import smb
from _smb_fake import FakeSmbClient


@pytest.fixture(params=['local', 'smb'])
def preview_library(request, tmp_path, monkeypatch):
    root = tmp_path / 'library'
    root.mkdir()
    fake = None
    if request.param == 'smb':
        fake = FakeSmbClient(root)
        monkeypatch.setenv('SMB_DRIVER', 'direct')
        monkeypatch.setattr(smb, 'smbclient', fake)
        lib = store.create_library(name=f'preview-{tmp_path.name}', source='smb',
                                   smb={'host': 'nas', 'share': 'video',
                                        'username': 'u', 'password': 'secret'})
    else:
        lib = store.create_library(name=f'preview-{tmp_path.name}', path=str(root))
    store.update_media_library(lib['media_library_id'], read_only=True)
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root, fake
    store.delete_media_library(lib['media_library_id'])
    library_paths.invalidate_cache()
    smb.invalidate()


def preview(lib, path, **params):
    return TestClient(app).get('/api/fs/blob', params={
        'library': lib['id'], 'path': path, **params})


def test_required_scope_and_exact_file_path(preview_library, tmp_path, monkeypatch):
    lib, root, _ = preview_library
    (root / 'a.txt').write_text('scoped', encoding='utf-8')
    (root / 'nested').mkdir()
    (root / 'nested' / 'only.txt').write_text('nested', encoding='utf-8')

    def no_default(*args, **kwargs):
        pytest.fail('preview must not fall back to a default library')

    monkeypatch.setattr(storage, 'backend_for', no_default)
    client = TestClient(app)
    for params in ({'path': 'a.txt'}, {'library': lib['id']},
                   {'library': 0, 'path': 'a.txt'}, {'library': -1, 'path': 'a.txt'},
                   {'library': 'abc', 'path': 'a.txt'}, {'library': lib['id'], 'path': ''}):
        assert client.get('/api/fs/blob', params=params).status_code == 422
    assert preview({'id': 2147483647}, 'a.txt').status_code == 404
    assert preview(lib, 'only.txt').status_code == 404  # no basename search
    assert preview(lib, 'nested').status_code == 404
    assert preview(lib, 'nested/only.txt').content == b'nested'
    assert preview(lib, 'a.txt', mode='other').status_code == 422

    other_root = tmp_path / 'other'
    other_root.mkdir()
    (other_root / 'a.txt').write_text('other library', encoding='utf-8')
    other = store.create_library(name=f'other-{tmp_path.name}', path=str(other_root))
    try:
        assert preview(lib, 'a.txt').content == b'scoped'
        assert preview(other, 'a.txt').content == b'other library'
    finally:
        store.delete_media_library(other['media_library_id'])


@pytest.mark.parametrize('path', [
    '/', '.', '..', '../outside.txt', 'nested/../../outside.txt', '/etc/passwd',
    '//host/share/secret', 'C:/secret.txt', 'C:secret.txt', r'C:\secret.txt',
    r'..\outside.txt', r'nested\..\outside.txt', r'\\host\share\secret',
    'http://example.com/file', 'file:///etc/passwd', 'smb://nas/share/file',
    'a.txt\x00.jpg', ' a.txt', 'a.txt ', 'nested//a.txt', './a.txt',
])
def test_rejects_ambiguous_or_escaping_paths(preview_library, path):
    lib, _, _ = preview_library
    assert preview(lib, path).status_code == 422


def test_local_symlink_cannot_escape(preview_library, tmp_path):
    lib, root, fake = preview_library
    if fake is not None:
        pytest.skip('SMB does not expose POSIX symlink semantics')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.txt').write_text('secret', encoding='utf-8')
    (root / 'link').symlink_to(outside, target_is_directory=True)
    (root / 'secret.txt').symlink_to(outside / 'secret.txt')
    for path in ('link/secret.txt', 'secret.txt'):
        assert preview(lib, path).status_code == 422
        assert preview(lib, path, mode='text').status_code == 422
    (root / 'inside.txt').write_text('inside', encoding='utf-8')
    (root / 'safe.txt').symlink_to(root / 'inside.txt')
    assert preview(lib, 'safe.txt', mode='text').text == 'inside'


def test_ranges_and_download_on_read_only_library(preview_library):
    lib, root, _ = preview_library
    data = bytes(range(256)) * 8
    (root / 'video.mp4').write_bytes(data)
    assert store.get_library(lib['id'])['read_only']
    response = preview(lib, 'video.mp4')
    assert response.status_code == 200 and response.content == data
    assert response.headers['content-disposition'].startswith('attachment;')
    assert response.headers['content-type'] == 'video/mp4'
    client = TestClient(app)
    params = {'library': lib['id'], 'path': 'video.mp4', 'inline': 1}
    for value, expected, content_range in [
        ('bytes=100-199', data[100:200], 'bytes 100-199/2048'),
        ('bytes=-20', data[-20:], 'bytes 2028-2047/2048'),
    ]:
        response = client.get('/api/fs/blob', params=params, headers={'Range': value})
        assert response.status_code == 206
        assert response.content == expected
        assert response.headers['content-range'] == content_range
        assert response.headers['accept-ranges'] == 'bytes'
        assert response.headers['content-type'] == 'video/mp4'
        assert 'attachment' not in response.headers.get('content-disposition', '')
    response = client.get('/api/fs/blob', params=params, headers={'Range': 'bytes=3000-'})
    assert response.status_code == 416
    assert response.headers['content-range'] == 'bytes */2048'


@pytest.mark.parametrize(('name', 'mime'), [
    ('poster.JPG', 'image/jpeg'), ('poster.png', 'image/png'),
    ('poster.webp', 'image/webp'), ('poster.gif', 'image/gif'),
    ('script.pdf', 'application/pdf'), ('clip.mp4', 'video/mp4'),
    ('clip.mkv', 'video/x-matroska'), ('clip.webm', 'video/webm'),
    ('clip.m2ts', 'video/mp2t'),
])
def test_safe_inline_types(preview_library, name, mime):
    lib, root, _ = preview_library
    (root / name).write_bytes(b'synthetic preview')
    response = preview(lib, name, inline=1)
    assert response.status_code == 200
    assert response.headers['content-type'] == mime
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'attachment' not in response.headers.get('content-disposition', '')


@pytest.mark.parametrize('name', ['unsafe.html', 'unsafe.svg', 'unsafe.xml', 'unknown.bin'])
def test_active_content_is_downloaded_or_plain_text(preview_library, name):
    lib, root, _ = preview_library
    text = '<script>alert("test")</script>'
    (root / name).write_text(text, encoding='utf-8')
    response = preview(lib, name, inline=1)
    assert response.status_code == 200
    assert response.headers['content-disposition'].startswith('attachment;')
    assert response.headers['x-content-type-options'] == 'nosniff'
    response = preview(lib, name, mode='text', inline=1)
    assert response.headers['content-type'] == 'text/plain; charset=utf-8'
    assert response.text == text


@pytest.mark.parametrize(('data', 'expected', 'truncated'), [
    ('中文字幕'.encode('utf-8'), '中文字幕', False),
    ('中文字幕'.encode('gbk'), '中文字幕', False),
    (b'a' * 65536, 'a' * 65536, False),
    (b'a' * 65536 + b'more', 'a' * 65536, True),
    (b'a' * 65535 + '中文字'.encode('utf-8'), 'a' * 65535, True),
])
def test_text_limit_and_encoding(preview_library, data, expected, truncated):
    lib, root, _ = preview_library
    (root / 'text.nfo').write_bytes(data)
    response = preview(lib, 'text.nfo', mode='text')
    assert response.status_code == 200
    assert response.text == expected
    assert response.headers['content-type'] == 'text/plain; charset=utf-8'
    assert response.headers['x-preview-limit'] == '65536'
    assert response.headers['x-preview-truncated'] == str(truncated).lower()


@pytest.mark.parametrize(('error_type', 'status'), [
    (storage.StorageNotFound, 404), (storage.StorageDenied, 403),
    (storage.StorageOffline, 503), (storage.StorageError, 500),
])
def test_stat_errors(preview_library, monkeypatch, error_type, status):
    lib, _, _ = preview_library
    backend = storage.backend_for_library(store.get_library(lib['id']))

    def fail_stat(path):
        raise error_type('synthetic read error')

    monkeypatch.setattr(backend, 'stat', fail_stat)
    monkeypatch.setattr(storage, 'backend_for_library', lambda library: backend)
    assert preview(lib, 'file.txt').status_code == status
    assert preview(lib, 'file.txt', mode='text').status_code == status


@pytest.mark.parametrize(('error_type', 'status'), [
    (storage.StorageNotFound, 404), (storage.StorageDenied, 403),
    (storage.StorageOffline, 503), (storage.StorageError, 500),
])
def test_open_errors_after_successful_stat(preview_library, monkeypatch, error_type, status):
    lib, root, _ = preview_library
    (root / 'file.txt').write_text('read me', encoding='utf-8')
    backend = storage.backend_for_library(store.get_library(lib['id']))
    backend.stat('file.txt')  # cached metadata must not hide unreadable content

    @contextmanager
    def fail_open(path):
        raise error_type('synthetic open error')
        yield  # pragma: no cover

    def fail_read(*args, **kwargs):
        raise error_type('synthetic read error')

    monkeypatch.setattr(backend, 'open_read', fail_open)
    monkeypatch.setattr(backend, 'read', fail_read)
    monkeypatch.setattr(storage, 'backend_for_library', lambda library: backend)
    assert preview(lib, 'file.txt').status_code == status
    assert preview(lib, 'file.txt', mode='text').status_code == status


def test_remote_offline(preview_library):
    lib, root, fake = preview_library
    if fake is None:
        pytest.skip('remote offline mapping')
    (root / 'file.txt').write_text('read me', encoding='utf-8')
    backend = storage.backend_for_library(store.get_library(lib['id']))
    backend.stat('file.txt')
    fake.offline = True
    assert preview(lib, 'file.txt').status_code == 503
    assert preview(lib, 'file.txt', mode='text').status_code == 503


def test_remote_seek_failure_discards_handle(preview_library, monkeypatch):
    lib, root, fake = preview_library
    if fake is None:
        pytest.skip('remote handle pool lifecycle')
    (root / 'file.txt').write_bytes(b'content')
    backend = storage.backend_for_library(store.get_library(lib['id']))
    outcomes = []

    class BrokenHandle:
        def seek(self, offset):
            raise storage.StorageOffline('disconnected handle')

    @contextmanager
    def open_read(path):
        try:
            yield BrokenHandle()
        except storage.StorageOffline:
            outcomes.append('discard')
            raise
        else:
            outcomes.append('recycle')

    monkeypatch.setattr(backend, 'open_read', open_read)
    assert preview(lib, 'file.txt').status_code == 503
    assert outcomes == ['discard']


def test_unmounted_source_does_not_read_leftover_local_files(preview_library, monkeypatch):
    lib, root, fake = preview_library
    if fake is not None:
        pytest.skip('mount driver uses local paths')
    (root / 'file.txt').write_text('mount point residue', encoding='utf-8')
    backend = storage.LocalStorageBackend(store.get_library(lib['id']), driver='mount')
    monkeypatch.setattr(storage, 'backend_for_library', lambda library: backend)
    monkeypatch.setattr('os.path.ismount', lambda path: False)
    assert preview(lib, 'file.txt').status_code == 503
    assert preview(lib, 'missing.txt').status_code == 503
    monkeypatch.setattr('os.path.ismount', lambda path: True)
    assert preview(lib, 'file.txt').content == b'mount point residue'
    assert preview(lib, 'missing.txt').status_code == 404


def test_preview_does_not_change_catalogue_progress_or_scan_reminders(preview_library):
    lib, root, _ = preview_library
    lid = lib['id']
    for name in ('unregistered.mp4', 'movie.mp4', 'episode.mp4', 'extra.mp4'):
        (root / name).write_bytes(b'synthetic media')
    mid = store.upsert_movie_by_path('movie.mp4', library_id=lid)
    sid = store.upsert_show(lid, 'Preview show')
    eid = store.upsert_episode(sid, lid, 'episode.mp4', 1, 1)
    extra_id = store.upsert_extra('extra.mp4', mid, library_id=lid)
    for kind, item_id in [('movie', mid), ('episode', eid), ('extra', extra_id)]:
        store.save_progress(item_id, 50, 100, kind=kind)
    store.record_fs_change(lid, 'copy', 'unregistered.mp4')
    tables = ('movies', 'tv_shows', 'tv_episodes', 'extras', 'playback_progress',
              'scan_state', 'fs_changes')

    def snapshot():
        with store._conn() as connection:
            return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
                    for table in tables}

    before = snapshot()
    for name in ('unregistered.mp4', 'movie.mp4', 'episode.mp4', 'extra.mp4'):
        assert preview(lib, name, inline=1).content == b'synthetic media'
    assert snapshot() == before
