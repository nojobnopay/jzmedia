"""Public Range responses bind headers and reader lifetime to one SMB source."""
import asyncio
from contextlib import contextmanager
import os

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from starlette.requests import ClientDisconnect

from app import storage
from app.routers.blob import media_response
from app.storage import smb
from _smb_fake import FakeSmbClient


@pytest.fixture()
def remote(tmp_path, monkeypatch):
    path = tmp_path / 'movie.mkv'
    path.write_bytes(b'old-content')
    os.utime(path, (1700000000.125, 1700000000.125))
    fake = FakeSmbClient(tmp_path)
    monkeypatch.setattr(smb, 'smbclient', fake)
    monkeypatch.setenv('SMB_HANDLE_POOL', '4')
    backend = smb.SmbStorageBackend({'id': 901, 'source': 'smb', 'smb_host': 'fake', 'smb_share': 'video'})
    app = FastAPI()

    @app.api_route('/media', methods=['GET', 'HEAD'])
    def media(request: Request):
        return media_response(request, backend, path.name)

    yield path, backend, fake, TestClient(app)
    backend._handles.clear()


def replace(path):
    staging = path.with_name('replacement.mkv')
    staging.write_bytes(b'new-source-is-longer')
    os.utime(staging, (1600000000.25, 1600000000.25))
    os.replace(staging, path)


@pytest.mark.parametrize('header', [None, 'bytes=5-', 'bytes=-4', 'bytes=999-'])
def test_cached_stat_and_idle_old_handle_cannot_define_new_response(remote, header):
    path, backend, _fake, client = remote
    backend.stat(path.name)  # Keep stale metadata within its TTL.
    with backend.open_read(path.name) as old:
        assert old.read() == b'old-content'
    replace(path)
    data = path.read_bytes()
    response = client.get('/media', headers={'Range': header} if header else {})
    if header == 'bytes=999-':
        assert response.status_code == 416
        assert response.headers['content-range'] == f'bytes */{len(data)}'
    else:
        expected = data[5:] if header == 'bytes=5-' else data[-4:] if header else data
        assert response.status_code == (206 if header else 200)
        assert response.content == expected
        assert int(response.headers['content-length']) == len(expected)
        if header:
            start = 5 if header == 'bytes=5-' else len(data) - 4
            assert response.headers['content-range'] == f'bytes {start}-{len(data) - 1}/{len(data)}'
        assert old.closed


@pytest.mark.parametrize('race', ['before_open', 'after_open'])
def test_replacement_between_response_stat_and_open_returns_409(remote, monkeypatch, race):
    path, backend, _fake, client = remote
    if race == 'before_open':
        original = backend.open_media_read

        @contextmanager
        def open_raced(rel, revision=None):
            replace(path)
            with original(rel, revision) as fh:
                yield fh

        monkeypatch.setattr(backend, 'open_media_read', open_raced)
    else:
        original = backend._open_fh

        def open_raced(*args):
            fh = original(*args)
            replace(path)
            return fh

        monkeypatch.setattr(backend, '_open_fh', open_raced)
    response = client.get('/media', headers={'Range': 'bytes=0-3'})
    assert response.status_code == 409
    assert 'source changed' in response.json()['detail']
    assert 'content-range' not in response.headers
    assert not backend._handles._idle


def test_head_validates_and_releases_reader_without_reading_body(remote, monkeypatch):
    _path, backend, fake, client = remote
    original = backend._open_fh

    def open_checked(*args):
        fh = original(*args)

        def no_body(*_args):
            pytest.fail('HEAD must not read the media body')

        fh.read = no_body
        return fh

    monkeypatch.setattr(backend, '_open_fh', open_checked)
    response = client.head('/media', headers={'Range': 'bytes=2-4'})
    assert response.status_code == 206 and response.content == b''
    assert response.headers['content-length'] == '3'
    assert response.headers['content-range'] == 'bytes 2-4/11'
    assert fake.counts['open_file'] == 1
    assert len(backend._handles._idle) == 1  # The verified lease has returned.


def test_never_consumed_response_has_no_borrowed_reader(remote):
    path, backend, fake, _client = remote
    response = media_response(Request({'type': 'http', 'headers': []}), backend, path.name)
    assert not fake.counts.get('open_file')
    asyncio.run(response.background())
    assert not fake.counts.get('open_file') and not backend._handles._idle


@pytest.mark.parametrize('failure', [OSError, asyncio.CancelledError])
def test_pre_body_disconnect_or_cancellation_discards_reader(remote, monkeypatch, failure):
    path, backend, _fake, _client = remote
    handles = []
    original = backend._open_fh

    def open_tracked(*args):
        fh = original(*args)
        handles.append(fh)
        return fh

    monkeypatch.setattr(backend, '_open_fh', open_tracked)
    scope = {'type': 'http', 'method': 'GET', 'headers': [], 'asgi': {'spec_version': '2.4'}}
    response = media_response(Request(scope), backend, path.name)

    async def receive():
        return {'type': 'http.disconnect'}

    async def send(_message):
        raise failure('disconnect before the first media read')

    with pytest.raises(ClientDisconnect if failure is OSError else asyncio.CancelledError):
        asyncio.run(response(scope, receive, send))
    assert len(handles) == 1 and handles[0].closed
    assert not backend._handles._idle


def test_body_read_error_discards_reader(remote, monkeypatch):
    path, backend, _fake, _client = remote
    handles = []
    original = backend._open_fh

    def open_broken(*args):
        fh = original(*args)
        handles.append(fh)

        def fail_read(*_args):
            raise storage.StorageOffline('disconnected while reading')

        fh.read = fail_read
        return fh

    monkeypatch.setattr(backend, '_open_fh', open_broken)
    response = media_response(Request({'type': 'http', 'headers': []}), backend, path.name)

    async def consume():
        with pytest.raises(storage.StorageOffline):
            await anext(response.body_iterator)
        await response.background()

    asyncio.run(consume())
    assert len(handles) == 1 and handles[0].closed
    assert not backend._handles._idle
