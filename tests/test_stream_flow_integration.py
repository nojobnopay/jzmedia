"""Input scoping, optional heartbeat contracts and preview/producer coordination."""
from pathlib import Path
from types import SimpleNamespace
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.routers.stream import common, previews, session
from app.routers.stream import flow_control as flow, flow_input


@pytest.fixture
def inputs(monkeypatch):
    monkeypatch.setenv('TRANSCODE_LOOKAHEAD_SECONDS', '120')
    options = {'-readrate', '-readrate_initial_burst', '-readrate_catchup',
               '-reconnect', '-reconnect_on_network_error', '-reconnect_delay_max',
               '-reconnect_max_retries', '-reconnect_delay_total_max'}
    monkeypatch.setattr(flow_input, '_input_options', lambda _ff: options)
    return options


@pytest.mark.parametrize('seg', ['fmp4', 'ts'])
def test_local_input_retains_original_command_without_implicit_pacing(inputs, seg):
    cmd = ['ffmpeg', '-ss', '20', '-i', '/movie.mkv', '-i', '/subtitle.idx', 'out.m3u8']
    updated, supported = flow_input.prepare_flow_input(cmd, '/movie.mkv', {'seg': seg})
    assert supported
    assert updated == cmd


def test_existing_manual_pacing_is_preserved(inputs):
    cmd = ['ffmpeg', '-readrate', '2', '-readrate_initial_burst', '23', '-i', '/movie', 'out.m3u8']
    updated, supported = flow_input.prepare_flow_input(cmd, '/movie', {'seg': 'fmp4'})
    assert supported and updated == cmd


def test_http_reconnect_is_bounded_and_scoped_to_source(inputs):
    cmd = ['ffmpeg', '-i', 'http://example.invalid/movie', '-i', '/subtitle.idx', 'out.m3u8']
    updated, supported = flow_input.prepare_flow_input(cmd, cmd[2], {'seg': 'fmp4'})
    assert supported
    assert updated[updated.index('-reconnect') + 1] == '1'
    assert updated[updated.index('-reconnect_delay_total_max') + 1] == '10'
    assert updated.index('-reconnect') < updated.index('-i')
    assert updated[updated.index(cmd[2]) + 1:] == cmd[3:]
    inputs.remove('-reconnect')
    assert flow_input.prepare_flow_input(cmd, cmd[2], {'seg': 'fmp4'}) == (cmd, False)


def test_disabled_window_and_finite_mp4_do_not_change_command(inputs, monkeypatch):
    cmd = ['ffmpeg', '-i', '/movie', 'out']
    assert flow_input.prepare_flow_input(cmd, '/movie', {'seg': 'mp4'}) == (cmd, False)
    monkeypatch.setenv('TRANSCODE_LOOKAHEAD_SECONDS', '0')
    assert flow_input.prepare_flow_input(cmd, '/movie', {'seg': 'fmp4'}) == (cmd, False)


@pytest.fixture
def lease(tmp_path):
    saved = dict(common._sessions)
    common._sessions.clear()
    proc = SimpleNamespace(poll=lambda: None)
    task = {'sdir': str(tmp_path), 'proc': proc, 'owners': {'viewer'},
            'media_start': 100.0, 'ready': threading.Event()}
    task['ready'].set()
    sess = {'sdir': str(tmp_path), 'vid': 1, 'task': task, 'proc': proc,
            'plan': {'seg': 'fmp4'}, 'complete': False, 'last_ping': time.time(),
            'created': time.time(), 'requested_start': 103.0, 'unclaimed': True}
    common._sessions['viewer'] = sess
    flow.start_producer_control(task, proc, sess['plan'], 400)
    app = FastAPI()
    app.include_router(session.router)
    with TestClient(app) as client:
        yield sess, proc, task, client
    common._sessions.clear()
    common._sessions.update(saved)


def test_optional_heartbeat_preserves_legacy_and_accepts_source_position(lease):
    sess, _proc, _task, client = lease
    url = '/api/stream/sessions/viewer/ping'
    assert client.post(url).status_code == 200
    assert '_flow_state' not in sess
    result = client.post(url, json={'position': 137, 'paused': True, 'playback_rate': 2})
    assert result.status_code == 200
    assert result.json()['running'] and not result.json()['failed']
    assert sess['_flow_state']['position'] == 137
    assert sess['_flow_state']['paused'] and sess['_flow_state']['rate'] == 2
    assert not sess['unclaimed']
    assert client.post(url, json={'position': -1}).status_code == 422
    assert client.post(url, json={'playback_rate': 100}).status_code == 422


def test_completion_observer_race_is_not_reported_as_failed(lease):
    sess, proc, task, _client = lease
    proc.poll = lambda: 0
    waiting = session.hls_session_ping('viewer')
    assert not waiting['running'] and not waiting['complete'] and not waiting['failed']
    task['error'] = RuntimeError('do not expose raw source errors')
    failed = session.hls_session_ping('viewer')
    assert failed['failed'] and 'raw source' not in failed['error']
    sess['complete'] = True
    assert session.hls_session_ping('viewer')['complete']
    assert not session.hls_session_ping('viewer')['failed']


def test_both_segment_routes_advance_only_video_demand(lease):
    sess, _proc, task, _client = lease
    directory = Path(task['sdir'])
    (directory / 'out_video.m3u8').write_text(
        '#EXTM3U\n#EXTINF:9.5,\nvideo_seg00000.m4s\n#EXTINF:10.5,\nvideo_seg00001.m4s\n')
    for name in ('audio0_seg00001.m4s', 'video_seg00000.m4s', 'video_seg00001.m4s'):
        (directory / name).write_bytes(b'isolated')
    session.hls_session_file('viewer', 'audio0_seg00001.m4s')
    assert '_flow_state' not in sess
    session.hls_session_segment('viewer', 'video_seg00000.m4s')
    assert sess['_flow_state']['requested'] == 109.5
    session.hls_session_file('viewer', 'video_seg00001.m4s')
    assert sess['_flow_state']['requested'] == 120


def test_preview_busy_distinguishes_a_buffer_paused_producer(lease):
    sess, proc, _task, _client = lease
    assert previews._transcoding_busy() and previews._playback_busy()
    proc._jz_buffer_paused = True
    assert not previews._transcoding_busy() and not previews._playback_busy()
    proc._jz_buffer_paused = False
    sess['_flow_state'] = {'paused': True}
    assert previews._transcoding_busy() and previews._playback_busy()
    proc.poll = lambda: 0
    assert not previews._transcoding_busy() and not previews._playback_busy()
