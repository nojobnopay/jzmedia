"""P5 failure, seek retry, lease status and completion must not bypass the gate."""
import io
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from app.routers.stream import common, dovi_runtime as runtime, session


def test_throughput_requires_two_active_windows_and_excludes_window_pause():
    meter = runtime.Throughput()
    assert not meter.sample(0, 0)
    assert not meter.sample(10, 10)
    assert not meter.sample(10, 110, paused=True)
    assert meter.sample(20, 120)
    assert meter.speed == 1
    assert not meter.sample(40, 130)
    assert meter.slow_windows == 0


def test_pause_does_not_turn_realtime_generation_into_failure():
    meter = runtime.Throughput()
    meter.sample(0, 0)
    assert not meter.sample(0, 1000, paused=True)
    assert not meter.sample(15, 1010)
    assert meter.speed == 1.5


@pytest.fixture
def producer(tmp_path, monkeypatch):
    plan = {'color_pipeline': 'dovi_p5_sdr', 'height': 1080,
            'seg': 'fmp4', 'vcopy': False, 'sub': 'none', 'audios': []}
    key = ('movie', 971, 'test')
    task = {'key': key, 'sdir': str(tmp_path), 'vid': 971, 'owners': {'dovi-test'},
            'plan_key': common._plan_marker(plan, 0, 0), 'ready': threading.Event(),
            'done': threading.Event(), 'slot': threading.BoundedSemaphore(1),
            'cancelled': False, 'dovi': True}
    assert task['slot'].acquire(blocking=False)
    monkeypatch.setattr(common, '_tasks', {key: task})
    monkeypatch.setattr(common, '_sessions', {'dovi-test': {'plan': plan, 'task': task,
                                                       'sdir': str(tmp_path), 'last_ping': 0}})
    monkeypatch.setattr(runtime, 'start_producer_control', lambda *a: None)
    monkeypatch.setattr(runtime, 'wake_producer_control', lambda *a: None)
    commands, processes = [], []

    def command(source, p, **kwargs):
        commands.append((dict(p), kwargs))
        return ['ffmpeg', '-hardware-only']

    monkeypatch.setattr(runtime.playback, 'build_cmd', command)
    monkeypatch.setattr(runtime, 'prepare_flow_input', lambda cmd, *a: (cmd, True))

    class Proc:
        pid = 999999998

        def __init__(self, *a, **kw):
            self.returncode = 0
            self.stderr = io.BytesIO()
            self.stdout = io.BytesIO()
            self.environment = kw['env']
            self.popen_options = kw
            processes.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(runtime.subprocess, 'Popen', Proc)

    class Reader:
        def join(self, timeout=None):
            return None

        def is_alive(self):
            return False

    class Audit:
        error = None

        def __init__(self, **kwargs):
            self.timestamp_offset = kwargs['timestamp_offset']
            task.setdefault('audit_instances', []).append(self)

        def start_reader(self, stream, *, progress_stream):
            self.stderr = stream
            self.stdout = progress_stream
            return Reader()

        def join_readers(self, timeout=None):
            return None

        def readers_alive(self):
            return False

        def finish(self, rc):
            return None

    monkeypatch.setattr(runtime, 'Audit', Audit)

    class Publisher:
        video_end = 1
        complete = False

        def __init__(self, work, public, audit, **kwargs):
            self.public = Path(public)

        def poll(self, final=False):
            self.complete = final
            if final:
                (self.public / 'video_seg00000.m4s').write_bytes(b'verified')
                (self.public / 'out_video.m3u8').write_text(
                    '#EXTM3U\n#EXTINF:1,\nvideo_seg00000.m4s\n#EXT-X-ENDLIST\n')

    monkeypatch.setattr(runtime, 'Publisher', Publisher)
    return task, plan, commands, processes


def run(producer, start=0, duration=1):
    task, plan, _commands, _processes = producer
    runtime._run(task, 'dovi-test', {'id': 971}, SimpleNamespace(input='unused'),
                 {'duration': duration, 'width': 3840, 'height': 1604}, plan, start)


def test_only_verified_success_writes_completion_proof_and_releases_slot(producer):
    task, _plan, commands, processes = producer
    slot = task['slot']
    run(producer)
    assert task['complete'] is True
    assert task['done'].is_set() and task['ready'].is_set()
    assert (Path(task['sdir']) / 'dovi-verified.json').read_text() == task['plan_key']
    assert len(commands) == 1 and 'force_sw' not in commands[0][1]
    assert slot.acquire(blocking=False)
    assert processes[0].environment['VK_DRIVER_FILES'].endswith('intel_icd.json')
    assert processes[0].environment['XDG_CACHE_HOME'].startswith(task['sdir'])
    assert processes[0].popen_options['stdout'] == runtime.subprocess.PIPE
    assert processes[0].popen_options['stderr'] == runtime.subprocess.PIPE
    assert task['audit_instances'][0].stdout is processes[0].stdout
    assert task['audit_instances'][0].stderr is processes[0].stderr
    assert processes[0].stdout.closed and processes[0].stderr.closed
    assert task['audit_instances'][0].timestamp_offset == '0.000000'
    assert not list(Path(task['sdir']).glob('.dovi-*'))


@pytest.mark.parametrize('start,attempts', [(0, [15]), (3.125, [15, 30]), (123.456, [15, 30])])
def test_metadata_failure_never_publishes_and_only_seek_retries(producer, monkeypatch, start, attempts):
    class InvalidAudit(runtime.Audit):
        error = 'dovi_audit_metadata_missing'

    monkeypatch.setattr(runtime, 'Audit', InvalidAudit)
    task, _plan, commands, _processes = producer
    run(producer, start)
    assert [p['dovi_preroll'] for p, _kw in commands] == attempts
    assert [audit.timestamp_offset for audit in task['audit_instances']] == [
        f'{min(start, preroll):.6f}' for preroll in attempts]
    assert task['failure_code'] == 'dovi_metadata_invalid'
    assert task['recommended_height'] is None
    assert not task.get('complete')
    assert not (Path(task['sdir']) / 'complete.json').exists()
    assert not list(Path(task['sdir']).glob('video_seg*'))


def test_a_late_eof_audit_failure_cannot_mark_a_finished_process_complete(producer, monkeypatch):
    class BadFinalPublisher:
        video_end = 0
        complete = False

        def __init__(self, *args, **kwargs):
            pass

        def poll(self, final=False):
            if final:
                raise runtime.PublishError('dovi_audit_missing_frames')

    monkeypatch.setattr(runtime, 'Publisher', BadFinalPublisher)
    task, _plan, _commands, _processes = producer
    run(producer)
    assert task['failure_code'] == 'dovi_pipeline_unavailable'
    assert not (Path(task['sdir']) / 'complete.json').exists()


def test_slow_1080_only_recommends_720_and_ping_exposes_failure_without_retry(producer, monkeypatch):
    task, plan, _commands, _processes = producer
    runtime._fail(task, 'dovi_too_slow', plan)
    assert task['recommended_height'] == 720
    sess = common._sessions['dovi-test']
    sess['proc'] = None
    monkeypatch.setattr(session, '_get_session', lambda sid: sess)
    response = session.hls_session_ping('dovi-test')
    assert response['failed'] is True
    assert response['failure_code'] == 'dovi_too_slow'
    assert response['recommended_height'] == 720
    runtime._fail(task, 'dovi_too_slow', dict(plan, height=720))
    assert task['recommended_height'] is None


def test_completion_marker_without_publication_proof_is_not_reusable(tmp_path):
    plan = {'color_pipeline': 'dovi_p5_sdr', 'seg': 'fmp4'}
    marker = common._plan_marker(plan, 0, 0)
    (tmp_path / 'complete.json').write_text(marker)
    (tmp_path / 'out_video.m3u8').write_text('#EXTM3U\n#EXTINF:1,\nvideo_seg00000.m4s\n#EXT-X-ENDLIST\n')
    (tmp_path / 'video_seg00000.m4s').write_bytes(b'unverified')
    assert not common._session_complete(str(tmp_path), marker)
    (tmp_path / 'dovi-verified.json').write_text(marker)
    assert not common._session_complete(str(tmp_path), marker)


def test_clean_early_video_eof_cannot_complete_a_longer_source(producer):
    task, _plan, _commands, _processes = producer
    run(producer, duration=120)
    assert task['failure_code'] == 'dovi_source_incomplete'
    assert not task.get('complete')
    assert not (Path(task['sdir']) / 'complete.json').exists()
    assert not (Path(task['sdir']) / 'out_video.m3u8').exists()


def test_demand_guard_still_measures_buffer_without_flow_control(producer, monkeypatch):
    task, _plan, _commands, _processes = producer
    sess = common._sessions['dovi-test']
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: 100)
    task['media_start'] = 120
    runtime.record_demand(sess, 126, False, 1)
    assert runtime.buffer_ahead(task, 10, 102) == 2
    runtime.record_demand(sess, 126, True, 1)
    assert runtime.buffer_ahead(task, 10, 102) is None


def test_private_producer_directory_is_recorded_for_orphan_cleanup(producer):
    task, _plan, _commands, processes = producer
    run(producer)
    proc = processes[0]
    common._write_session_meta(task['sdir'], 'test', 971, proc, 'vaapi', 1)
    record = json.loads((Path(task['sdir']) / 'session.json').read_text())
    assert record['workdir'] == str(Path(task['sdir']) / '.dovi-dovi-test-1')


def test_last_owner_keeps_directory_reserved_until_publisher_has_stopped(producer, monkeypatch):
    task, plan, _commands, _processes = producer
    entered, release = threading.Event(), threading.Event()

    class SlowPublisher:
        video_end = 1
        complete = False

        def __init__(self, work, public, audit, **kwargs):
            self.public = Path(public)

        def poll(self, final=False):
            entered.set()
            assert release.wait(2)
            if final:
                self.complete = True

    monkeypatch.setattr(runtime, 'Publisher', SlowPublisher)
    worker = threading.Thread(target=run, args=(producer,))
    task['dovi_thread'] = worker
    worker.start()
    assert entered.wait(2)
    closer = threading.Thread(target=common._drop_session, args=('dovi-test',))
    closer.start()
    deadline = time.monotonic() + 2
    while not task.get('cancelled') and time.monotonic() < deadline:
        time.sleep(0.01)
    assert task['cancelled']
    assert common._tasks.get(task['key']) is task
    assert not task['done'].is_set()
    release.set()
    worker.join(2)
    closer.join(2)
    assert not worker.is_alive() and not closer.is_alive()
    assert task['key'] not in common._tasks
    assert task['done'].is_set()
    assert not (Path(task['sdir']) / 'complete.json').exists()


@pytest.mark.parametrize('undrained_stream', ['stderr', 'stdout'])
def test_reader_that_has_not_reached_eof_reserves_outputs_until_drained(
        producer, monkeypatch, undrained_stream):
    drained = threading.Event()

    class UndrainedAudit(runtime.Audit):
        error = None

        def join_readers(self, timeout=None):
            if timeout is None:
                assert drained.wait(2)

        def readers_alive(self):
            return not drained.is_set()

        def finish(self, rc):
            raise AssertionError('Undrained log must not be finalized')

    monkeypatch.setattr(runtime, 'Audit', UndrainedAudit)
    task, _plan, commands, processes = producer
    slot = task['slot']
    try:
        run(producer, start=60)
        assert len(commands) == 1  # A live reader also prevents the 30-second retry.
        assert task['failure_code'] == 'dovi_pipeline_unavailable'
        assert not (Path(task['sdir']) / 'complete.json').exists()
        assert not task['done'].is_set() and not slot.acquire(blocking=False)
        assert common._tasks.get(task['key']) is task
        assert not getattr(processes[0], undrained_stream).closed
        assert list(Path(task['sdir']).glob('.dovi-*'))
    finally:
        drained.set()
        task['dovi_reaper'].join(2)
    assert not task['dovi_reaper'].is_alive()
    assert task['done'].is_set() and slot.acquire(blocking=False)
    assert processes[0].stdout.closed and processes[0].stderr.closed
    assert not list(Path(task['sdir']).glob('.dovi-*'))


def test_failed_kill_reserves_directory_and_slot_until_process_really_exits(producer, monkeypatch):
    exited = threading.Event()
    task, _plan, commands, processes = producer
    task['owners'].clear()
    slot = task['slot']

    class StalledProcess:
        pid = 999999998
        returncode = None

        def __init__(self, *args, **kwargs):
            self.stderr = io.BytesIO()
            self.stdout = io.BytesIO()
            processes.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            assert exited.wait(2)
            self.returncode = -9
            return self.returncode

    class InvalidAudit(runtime.Audit):
        error = 'dovi_audit_metadata_missing'

    monkeypatch.setattr(runtime.subprocess, 'Popen', StalledProcess)
    monkeypatch.setattr(runtime, 'Audit', InvalidAudit)
    monkeypatch.setattr(common, '_kill_proc', lambda proc: None)
    try:
        run(producer, start=60)
        assert len(commands) == 1  # No second producer while the first is alive.
        assert task['cancelled'] and common._tasks.get(task['key']) is task
        assert not task['done'].is_set() and not slot.acquire(blocking=False)
        assert not processes[0].stderr.closed
        assert not processes[0].stdout.closed
        assert list(Path(task['sdir']).glob('.dovi-*'))
        common._drop_session('dovi-test')
        assert common._tasks.get(task['key']) is task
        assert not task['done'].is_set()
    finally:
        exited.set()
        task['dovi_reaper'].join(2)
    assert not task['dovi_reaper'].is_alive()
    assert task['done'].is_set() and slot.acquire(blocking=False)
    assert task['key'] not in common._tasks
    assert processes[0].stderr.closed
    assert processes[0].stdout.closed
    assert not list(Path(task['sdir']).glob('.dovi-*'))


@pytest.mark.parametrize('name', ['.dovi-1', '.dovi-1/video_seg00000.m4s',
                                  '../video_seg00000.m4s', '.audio-prefix-123.tmp',
                                  'dovi-publication.json', 'dovi-verified.json'])
def test_private_or_proof_files_cannot_bypass_session_file_routes(name):
    from fastapi import HTTPException
    for handler in (session.hls_session_file, session.hls_session_segment):
        with pytest.raises(HTTPException) as caught:
            handler('unused', name)
        assert caught.value.status_code == 422
