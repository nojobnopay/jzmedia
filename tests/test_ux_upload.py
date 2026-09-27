"""UX upload scope and TV ingestion, using isolated local/remote temporary libraries."""
import io

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app import library_paths, store, storage
from app.main import app
from app.routers.movies.common import _stream_upload_backend
from app.storage import smb
from _smb_fake import FakeSmbClient

client = TestClient(app)


@pytest.fixture(params=['local', 'smb'])
def target(request, tmp_path, monkeypatch):
    root = tmp_path / 'tv'
    root.mkdir()
    kwargs = {'path': str(root)}
    if request.param == 'smb':
        monkeypatch.setattr(smb, 'smbclient', FakeSmbClient(root))
        monkeypatch.setenv('SMB_DRIVER', 'direct')
        kwargs = {'source': 'smb', 'smb': {'host': 'nas', 'share': 'video'}}
    lib = store.create_library(name=f'upload-{tmp_path.name}', kind='tv', **kwargs)
    library_paths.invalidate_cache()
    smb.invalidate()
    yield lib, root
    store.delete_media_library(lib['media_library_id'])
    library_paths.invalidate_cache()
    smb.invalidate()


def upload(lib, name='Show/Season 01/Show-S01E01.mkv', **params):
    return client.post('/api/uploads', params={'library_id': lib['id'], 'media_type': 'tv',
        'relpath': name, 'mode': 'dir', **params}, files={'file': (name.split('/')[-1], b'uploaded')})


def test_folder_upload_registers_tv_only_preserving_path(target):
    lib, root = target
    r = upload(lib)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['status'] == 'tv_ok' and d['media_type'] == 'tv' and d['movie_id'] is None
    assert store.get_episode(d['episode_id'])['show_id'] == d['show_id']
    assert (root / d['rel']).read_bytes() == b'uploaded'
    assert store.get_by_path(d['rel'], library_id=lib['id']) is None
    duplicate = upload(lib)
    assert duplicate.status_code == 409
    assert (root / d['rel']).read_bytes() == b'uploaded'
    assert upload(lib, 'Show/Season 01/Show-S01E02.mkv').json()['show_id'] == d['show_id']


def test_loose_tv_requires_show_and_season(target):
    lib, root = target
    assert upload(lib, 'S02E01.mkv', mode='files').status_code == 422
    assert not list(root.iterdir())
    r = upload(lib, 'S02E01.mkv', mode='files', show_title='新剧', season=2)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['season'] == 2 and d['episode'] == 1
    assert d['rel'] == '新剧/Season 02/S02E01.mkv'
    assert upload(lib, 'S01E02.mkv', mode='files', show_id=d['show_id'], season=2).status_code == 422
    r2 = upload(lib, 'S02E02.mkv', mode='files', show_id=d['show_id'], season=2)
    assert r2.json()['show_id'] == d['show_id']
    assert r2.json()['rel'] == '新剧/Season 02/S02E02.mkv'


def test_unrecognized_file_is_saved_but_never_false_episode(target):
    lib, root = target
    r = upload(lib, 'Show/unknown.mkv')
    assert r.status_code == 200, r.text
    assert r.json()['status'] == 'skipped_tv_unknown'
    assert r.json()['episode_id'] is None
    assert (root / 'Show/unknown.mkv').exists()
    assert store.count_episodes([lib['id']]) == 0


def test_wrong_type_disabled_readonly_and_unknown_rejected_before_write(target):
    lib, root = target
    assert upload(lib, media_type='movie').status_code == 422
    assert upload(lib, library_id=999999).status_code == 404
    store.update_library(lib['id'], enabled=False)
    library_paths.invalidate_cache()
    assert upload(lib).status_code == 409
    store.update_library(lib['id'], enabled=True)
    store.update_media_library(lib['media_library_id'], read_only=True)
    library_paths.invalidate_cache()
    assert upload(lib).status_code == 409
    store.update_media_library(lib['media_library_id'], read_only=False, enabled=False)
    library_paths.invalidate_cache()
    assert upload(lib).status_code == 409
    assert not list(root.iterdir())


def test_tv_scope_and_path_traversal(target, tmp_path):
    lib, root = target
    other = tmp_path / 'other'; other.mkdir()
    foreign = store.create_library(name='foreign-'+tmp_path.name, kind='tv', path=str(other))
    try:
        sid = store.upsert_show(foreign['id'], 'foreign')
        assert upload(lib, 'S01E01.mkv', mode='files', show_id=sid, season=1).status_code == 422
        for path in ['../S01E01.mkv', '/Show/S01E01.mkv', 'Show/../S01E01.mkv']:
            assert upload(lib, path).status_code == 422
        assert upload(lib, 'S01E01.mkv', mode='files', show_title='../outside', season=1).status_code == 422
        assert not list(root.iterdir())
    finally:
        store.delete_media_library(foreign['media_library_id'])
        library_paths.invalidate_cache()


def test_remote_commit_refuses_competing_writer_and_cleans_partial(target):
    lib, root = target
    backend = storage.backend_for(lib['id'])

    class CompetingInput:
        file = None
        def __init__(self):
            self.file = self
            self.data = io.BytesIO(b'new bytes')
        def read(self, size):
            chunk = self.data.read(size)
            if chunk:
                (root / 'race.mkv').write_bytes(b'competing writer')
            return chunk

    with pytest.raises(HTTPException) as error:
        _stream_upload_backend(CompetingInput(), backend, 'race.mkv')
    assert error.value.status_code == 409
    assert (root / 'race.mkv').read_bytes() == b'competing writer'
    assert not list(root.glob('*.part-*'))

    class BrokenInput:
        file = None
        def __init__(self): self.file = self
        def read(self, size): raise OSError('connection interrupted')

    with pytest.raises((HTTPException, OSError)):
        _stream_upload_backend(BrokenInput(), backend, 'cancel.mkv')
    assert not (root / 'cancel.mkv').exists()
    assert not list(root.glob('*.part-*'))
