"""Replacement-safe SMB handle leases and revision-bound Range reads."""
import json
import os
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from app import storage
from app.storage import httpproxy, smb
from _smb_fake import FakeSmbClient


@pytest.fixture
def remote(tmp_path, monkeypatch):
    path = tmp_path / 'movie.mkv'
    path.write_bytes(b'old-content')
    os.utime(path, (1700000000.125, 1700000000.125))
    fake = FakeSmbClient(tmp_path)
    monkeypatch.setattr(smb, 'smbclient', fake)
    monkeypatch.setenv('SMB_HANDLE_POOL', '4')
    backend = smb.SmbStorageBackend({'id': 901, 'source': 'smb', 'smb_host': 'fake', 'smb_share': 'video'})
    monkeypatch.setattr(httpproxy, '_backend_for', lambda *_: backend)
    httpproxy.shutdown()
    yield path, backend, fake
    httpproxy.shutdown()
    backend._handles.clear()


def replace(path, data=b'new-source-is-longer', mtime=1600000000.25):
    staging = path.with_name('replacement.mkv')
    staging.write_bytes(data)
    os.utime(staging, (mtime, mtime))
    os.replace(staging, path)


@pytest.mark.parametrize(('data', 'mtime'), [
    (b'new-source-is-longer', 1600000000.25),
    (b'new-content', 1700000000.1255),
])
def test_external_replacement_never_reuses_an_idle_old_handle(remote, data, mtime):
    path, backend, fake = remote
    with backend.open_read(path.name) as old:
        assert old.read() == b'old-content'
    replace(path, data, mtime)
    with backend.open_read(path.name) as current:
        assert current.read() == data
        assert current is not old
    assert old.closed
    with backend.open_read(path.name) as current:
        assert current.read() == data
    assert fake.counts['open_file'] == 2  # Matching revisions still reuse the new handle.


@pytest.mark.parametrize('invalidated', ['replacement', 'evict', 'clear'])
def test_borrowed_old_handle_cannot_reenter_pool_after_invalidation(remote, invalidated):
    path, backend, _fake = remote
    with backend.open_read(path.name) as old:
        if invalidated == 'replacement':
            replace(path)
        elif invalidated == 'evict':
            backend._handles.evict(path.name)
        else:
            backend._handles.clear()
        with backend.open_read(path.name) as current:
            expected = path.read_bytes()
            assert current.read() == expected
        assert not old.closed  # Invalidating a lease does not interrupt its active reader.
    assert old.closed
    with backend.open_read(path.name) as after:
        assert after is current and after.read() == expected


def test_revision_bound_proxy_rejects_old_url_and_reports_new_range_length(remote):
    path, backend, fake = remote
    old_src = storage.media_source(901, path.name, backend=backend)
    assert parse_qs(urlsplit(old_src.input).query)['revision']
    with urllib.request.urlopen(old_src.input, timeout=5) as response:
        assert response.read() == b'old-content'
    replace(path)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(old_src.input, timeout=5)
    assert error.value.code == 409
    backend._meta.clear()
    new_src = storage.media_source(901, path.name, backend=backend)
    assert new_src.input != old_src.input
    req = urllib.request.Request(new_src.input, headers={'Range': 'bytes=0-3'})
    with urllib.request.urlopen(req, timeout=5) as response:
        assert response.status == 206
        assert response.headers['Content-Length'] == '4'
        assert response.headers['Content-Range'] == f'bytes 0-3/{path.stat().st_size}'
        assert response.read() == b'new-'
    with urllib.request.urlopen(urllib.request.Request(new_src.input, method='HEAD'), timeout=5) as response:
        assert response.headers['Content-Length'] == str(path.stat().st_size)
        assert response.read() == b''
    assert fake.counts['open_file'] == 2


@pytest.mark.parametrize('race', ['before_open', 'after_open', 'invalidate'])
def test_source_change_between_stat_and_open_is_rejected_before_response_headers(remote, monkeypatch, race):
    path, backend, _fake = remote
    url = backend.get_read_url(path.name)
    original = backend._open_fh

    def open_raced(*args):
        if race == 'before_open':
            replace(path)
        fh = original(*args)
        if race == 'after_open':
            replace(path)
        elif race == 'invalidate':
            backend._handles.evict(path.name)
        return fh

    monkeypatch.setattr(backend, '_open_fh', open_raced)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(url, timeout=5)
    assert error.value.code == 409
    assert not backend._handles._idle


def test_proxy_rejects_malformed_revision_without_opening_source(remote):
    path, backend, fake = remote
    url = backend.get_read_url(path.name).split('?')[0] + '?revision=bad'
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(url, timeout=5)
    assert error.value.code == 400
    assert not fake.counts.get('open_file')


def test_old_smb_preview_and_derived_cache_namespaces_are_not_reused(remote, tmp_path, monkeypatch):
    from app.playback.source import source_cache_key
    from app.routers.stream import common, previews

    path, backend, _fake = remote
    stat = backend.stat_uncached(path.name)
    src = storage.MediaSource(backend, path.name, '', None, stat.size, stat.mtime)
    row = {'id': 901, 'kind': 'movie', 'file_path': path.name, 'library_id': backend.library_id}
    root = tmp_path / 'previews'
    root.mkdir()
    monkeypatch.setattr(previews, '_root', lambda: root)
    monkeypatch.setattr(backend, 'read_cache_revision', 0)
    old_derived = source_cache_key(src)
    old_plan = common._source_plan({}, row, src)
    for revision in range(1, previews.QUALITY_REVISION + 1):
        width, height = previews._quality_dimensions(revision)
        for key in previews._quality_keys(row, src, 20, revision):
            directory = root / key
            directory.mkdir()
            (directory / 'page_0000.jpg').write_bytes(b'old-source-preview')
            (directory / 'manifest.json').write_text(json.dumps({
                'version': 1, 'state': 'ready', 'quality_revision': revision,
                'extraction_revision': 1, 'duration': 20, 'interval': 20, 'count': 1,
                'width': width, 'height': height, 'columns': 5, 'rows': 5,
                'pages': ['page_0000.jpg'], 'item_kind': 'movie', 'item_id': row['id'],
            }))
    assert previews._current_ready(row, src, 20)[1] is not None
    monkeypatch.setattr(backend, 'read_cache_revision', 1)
    assert source_cache_key(src) != old_derived
    assert common._source_plan({}, row, src) != old_plan
    assert previews._current_ready(row, src, 20) == (None, None)
    assert not any((root / key).exists() for revision in range(1, previews.QUALITY_REVISION + 1)
                   for key in previews._quality_keys(row, src, 20, revision))
