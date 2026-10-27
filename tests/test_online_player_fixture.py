"""Formal player fixtures use temporary data and fixed loopback targets only."""

from contextlib import closing
from pathlib import Path
import re
import time

from fastapi.testclient import TestClient
import pytest

from app import store
from app.network.targets import TargetPolicyError
from scripts.online_p1.fixtures import MARKER, MARKER_TEXT, fixture_root
from scripts.online_p1.server import FIXTURE_TOKEN, FixtureTargetPolicy, isolated_fixture


@pytest.fixture
def prepared(tmp_path):
    # Protocol tests do not generate or decode video. Browser checks separately
    # use the same P0 generator to prepare actual 24-second media.
    root = tmp_path / 'jzmedia-online-p1-test'
    root.mkdir()
    (root / MARKER).write_text(MARKER_TEXT)
    for episode in ('ep-1', 'ep-2'):
        folder = root / 'media' / episode
        variants = folder / 'hls' / 'variants'
        variants.mkdir(parents=True)
        (folder / 'movie.mp4').write_bytes(b'0123456789' * 100)
        (folder / 'hls' / 'master.m3u8').write_text(
            '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nvariants/low.m3u8\n')
        (folder / 'hls' / 'init.mp4').write_bytes(b'init-bytes')
        (variants / 'part-000.m4s').write_bytes(b'segment-bytes')
        (variants / 'low.m3u8').write_text(
            '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MAP:URI="../init.mp4"\n'
            '#EXTINF:4,\npart-000.m4s\n#EXT-X-ENDLIST\n')
    scenarios = root / 'media' / 'scenarios'
    scenarios.mkdir()
    (scenarios / 'unsupported.mp4').write_bytes(b'unsupported-fixture')
    (scenarios / 'encrypted.m3u8').write_text(
        '#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
        '#EXTINF:4,\nsecret.ts\n#EXT-X-ENDLIST\n')
    return root


@pytest.fixture
def client(prepared):
    with isolated_fixture(prepared) as app, TestClient(app) as browser:
        catalog = browser.get('/api/p1/catalog').json()
        browser.headers['X-Api-Token'] = catalog['api_token']
        assert browser.get('/api/viewing/session').status_code == 200
        yield browser


def create(client, key):
    item = next(row for row in client.get('/api/p1/catalog').json()['items'] if row['key'] == key)
    response = client.post('/api/online/sessions', json=item['identity'])
    assert response.status_code == 201, response.text
    return response.json()


def test_fixture_root_guard_refuses_production_or_unmarked_paths(tmp_path):
    for path in (Path('/data'), tmp_path, Path('/tmp/arbitrary-directory')):
        with pytest.raises(ValueError):
            fixture_root(path)
    root = tmp_path / 'jzmedia-online-p1-empty'
    root.mkdir()
    with pytest.raises(ValueError, match='generate'):
        fixture_root(root, require_media=True)


def test_catalog_has_stable_formal_identities_and_only_temporary_empty_local_tables(client):
    first = client.get('/api/p1/catalog').json()
    assert first == client.get('/api/p1/catalog').json()
    assert [row['key'] for row in first['items']] == ['movie-mp4', 'movie-hls', 'episode-1', 'episode-2']
    assert [row['identity']['kind'] for row in first['items']] == ['movie', 'movie', 'episode', 'episode']
    assert first['api_token'] == FIXTURE_TOKEN
    assert all('fixture.invalid' not in str(row) for row in first['items'])
    metrics = client.get('/api/p1/metrics').json()
    assert metrics['local_rows'] == {'movies': 0, 'tv_episodes': 0, 'playback_progress': 0}


def test_real_formal_mp4_route_uses_owned_session_and_byte_passthrough(client):
    descriptor = create(client, 'movie-mp4')
    assert descriptor['processing'] == 'passthrough' and descriptor['time_offset_seconds'] == 0
    assert 'fixture.invalid' not in str(descriptor) and 'fixture_signature' not in str(descriptor)
    response = client.get(descriptor['media_url'], headers={'Range': 'bytes=0-9'})
    assert response.status_code == 206 and response.content == b'0123456789'
    assert client.delete(f"/api/online/sessions/{descriptor['session_id']}").status_code == 204
    assert client.get(descriptor['media_url']).status_code == 404
    metrics = client.get('/api/p1/metrics').json()
    assert metrics['registry']['sessions'] == 0
    assert descriptor['session_id'] in metrics['created_sessions']
    assert descriptor['session_id'] in metrics['released_sessions']
    assert not any(row['app_credentials'] for row in metrics['origin_calls'])


def test_formal_hls_rewrites_nested_map_and_accepts_registered_segment_mime(client):
    descriptor = create(client, 'movie-hls')
    master = client.get(descriptor['media_url'])
    assert master.status_code == 200 and 'fixture.invalid' not in master.text
    variant = next(line for line in master.text.splitlines() if line and not line.startswith('#'))
    media = client.get(variant)
    assert media.status_code == 200
    initial = re.search(r'#EXT-X-MAP:URI="([^"]+)"', media.text)[1]
    assert client.get(initial).content == b'init-bytes'
    segment = next(line for line in media.text.splitlines() if line and not line.startswith('#'))
    response = client.get(segment)
    assert response.status_code == 200 and response.content == b'segment-bytes'
    assert response.headers['content-type'] == 'video/iso.segment'


def test_scenarios_are_fixed_one_shot_and_can_expire_active_sessions(client):
    assert client.post('/api/p1/scenario', json={'next_mode': 'provider_error'}).status_code == 200
    identity = client.get('/api/p1/catalog').json()['items'][0]['identity']
    failure = client.post('/api/online/sessions', json=identity)
    assert failure.status_code == 503 and failure.json()['code'] == 'provider_not_ready'
    descriptor = create(client, 'movie-mp4')
    assert client.post('/api/p1/scenario', json={'expire_sessions': True}).status_code == 200
    assert client.get(descriptor['media_url']).status_code == 404
    assert client.post('/api/p1/scenario', json={'next_mode': 'arbitrary', 'url': 'http://localhost'}).status_code == 422


def test_delayed_provider_is_bounded_and_still_uses_fixed_identity(client):
    assert client.post('/api/p1/scenario', json={'delay_ms': 20}).status_code == 200
    started = time.monotonic()
    descriptor = create(client, 'episode-1')
    assert .015 <= time.monotonic() - started < 2
    assert descriptor['identity']['kind'] == 'episode'
    assert client.post('/api/p1/scenario', json={'delay_ms': 2001}).status_code == 422


def test_target_test_seam_cannot_reach_another_host_or_port():
    policy = FixtureTargetPolicy('src_' + 'a' * 32, 12345)
    assert policy.resolve('http://fixture.invalid:12345/files/asset.mp4').addresses == ('127.0.0.1',)
    for url in ('http://fixture.invalid:12346/files/asset.mp4', 'http://other.invalid:12345/files/asset.mp4',
                'https://fixture.invalid:12345/files/asset.mp4', 'http://127.0.0.1:12345/files/asset.mp4'):
        with pytest.raises(TargetPolicyError):
            policy.resolve(url)


def test_fixture_restores_database_global_and_removes_only_its_state(prepared):
    original = store._base.DB_PATH
    with isolated_fixture(prepared) as app, TestClient(app) as client:
        assert Path(store._base.DB_PATH).is_relative_to(prepared)
        assert client.get('/api/p1/health').json()['synthetic'] is True
        with closing(store._base._conn()) as connection:
            assert connection.execute('SELECT COUNT(*) FROM online_sources').fetchone()[0] == 1
    assert store._base.DB_PATH == original
    assert not list(prepared.glob('state-*'))
    assert (prepared / 'media' / 'ep-1' / 'movie.mp4').exists()
