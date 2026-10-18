"""P5 producers publish only audited fMP4, and never retry as ordinary SDR."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from fastapi import HTTPException

from ... import playback
from ...playback import dovi
from ...playback.dovi_audit import Audit, AuditError
from .dovi_publish import Publisher, PublishError
from . import common
from .flow_control import start_producer_control, wake_producer_control
from .flow_input import prepare_flow_input

_MESSAGES = {
    'dovi_metadata_invalid': '杜比视界元数据缺失或损坏，已停止转换；请复制直链到支持杜比视界的播放器，或选择兼容版本',
    'dovi_pipeline_unavailable': '杜比视界硬件转换失败，已停止播放；请复制直链到外部播放器，或选择兼容版本',
    'dovi_too_slow': '服务器无法实时转换此杜比视界视频；请复制直链到外部播放器，或选择兼容版本',
    'dovi_source_incomplete': '视频在声明片尾之前结束，片源可能不完整；已停止转换，请检查原文件或选择其他版本',
}


class Throughput:
    """Two complete active ten-second windows; STOP time never lowers speed."""
    def __init__(self):
        self.last_clock = None
        self.active = 0.0
        self.origin = None
        self.slow_windows = 0
        self.speed = None

    def sample(self, output_seconds, now, paused=False):
        if self.last_clock is not None and not paused:
            self.active += max(0.0, now - self.last_clock)
        self.last_clock = now
        if self.origin is None:
            self.origin = output_seconds
            self.active = 0.0
        if self.active < 10:
            return False
        self.speed = max(0.0, output_seconds - self.origin) / self.active
        self.slow_windows = self.slow_windows + 1 if self.speed < 1.25 else 0
        self.active = 0.0
        self.origin = output_seconds
        return self.slow_windows >= 2


def diagnostics(task):
    if not task or not task.get('dovi'):
        return {}
    return {'color_pipeline': 'dovi_p5_sdr', **{k: task.get(k) for k in (
        'decoder', 'tone_mapper', 'encoder', 'processing_speed',
        'failure_code', 'failure_reason', 'recommended_height')}}


def record_demand(sess, position, paused=None, playback_rate=None):
    """P5 safety does not depend on SIGSTOP/window-control availability."""
    if (sess.get('plan') or {}).get('color_pipeline') != 'dovi_p5_sdr':
        return
    with common._sess_lock:
        previous = sess.get('_dovi_demand') or {}
        if position is None:
            return
        sess['_dovi_demand'] = {'position': float(position),
            'paused': paused if isinstance(paused, bool) else previous.get('paused', False),
            'rate': float(playback_rate or previous.get('rate', 1)), 'observed': time.monotonic()}


def buffer_ahead(task, video_end, now):
    with common._sess_lock:
        active = []
        for sid in task.get('owners', ()):
            demand = (common._sessions.get(sid) or {}).get('_dovi_demand')
            if demand and not demand['paused']:
                active.append(demand['position'] + min(15, max(0, now - demand['observed'])) * demand['rate'])
        if active:
            return max(0.0, float(task.get('media_start') or 0) + float(video_end) - max(active))
        if any((common._sessions.get(sid) or {}).get('_dovi_demand') for sid in task.get('owners', ())):
            return None  # Every observed playback owner is paused.
        return (task.get('_flow') or {}).get('ahead_seconds')


def _fail(task, code, plan):
    with common._sess_lock:
        task.update(failure_code=code, failure_reason=_MESSAGES[code],
                    recommended_height=720 if code == 'dovi_too_slow'
                    and int(plan.get('height') or 0) == 1080 else None,
                    error=HTTPException(422, _MESSAGES[code]), complete=False)


def _clear_outputs(directory):
    for path in Path(directory).iterdir():
        if path.is_dir() and path.name.startswith('.dovi-'):
            shutil.rmtree(path)
        elif path.is_file() and path.name != 'session.json':
            path.unlink()


def _release_stopped_task(task, public):
    """No process, publisher or log writer may outlive this reservation."""
    for path in task.get('dovi_work_dirs', ()):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    with common._sess_lock:
        common._release_task_slot(task)
        task['ready'].set()
        task['done'].set()
        if common._tasks.get(task['key']) is task:
            common._clear_session_meta(str(public))
            if not task['owners']:
                common._tasks.pop(task['key'])
    common._wake_session_maintenance()


def _close_process_pipes(proc):
    for name in ('stderr', 'stdout'):
        stream = getattr(proc, name, None)
        if stream is not None:
            stream.close()


def _await_stalled_process(task, public, proc, audit):
    """Keep the reservation until the process and both pipe readers stop."""
    try:
        proc.wait()
        if proc.poll() is None:
            return
        if audit is not None:
            audit.join_readers()
            if audit.readers_alive():
                return
        _close_process_pipes(proc)
    except (OSError, ValueError):
        return
    _release_stopped_task(task, public)


def _run(task, sid, row, src, info, plan, start):
    public = Path(task['sdir'])
    proc = None
    audit = None
    try:
        attempts = (15, 30) if start > 0 else (15,)
        for number, preroll in enumerate(attempts, 1):
            with common._sess_lock:
                if task.get('cancelled'):
                    return
                _clear_outputs(public)
                work = public / f'.dovi-{sid}-{number}'
                work.mkdir()
                task.setdefault('dovi_work_dirs', []).append(work)
                common._write_master(str(public), info, plan, 4)
            attempt_plan = dict(plan, dovi_preroll=preroll)
            command = playback.build_cmd(src.input, attempt_plan, start=start, seg_time=4)
            command, flow_supported = prepare_flow_input(command, src.input, plan)
            audit = Audit(timestamp_offset=f'{min(max(0, float(start)), preroll):.6f}')
            expected_renditions = ['video'] + [f'audio{i}' for i, _a in enumerate(plan.get('audios') or [])]
            publisher = Publisher(work, public, audit, expected_renditions=expected_renditions)
            env = dovi.playback_env(plan)
            env['XDG_CACHE_HOME'] = str(work / 'cache')
            env['MESA_SHADER_CACHE_MAX_SIZE'] = '16M'
            with common._sess_lock:
                if task.get('cancelled'):
                    return
                proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, cwd=str(work), env=env)
                proc._jz_work_dir = str(work)
                task.update(proc=proc, backend='vaapi', attempt=number,
                    flow_input_supported=flow_supported, dovi_audit=audit,
                    dovi_publisher=publisher)
                for owner in task['owners']:
                    if owner in common._sessions:
                        common._sessions[owner].update(proc=proc, backend='vaapi', attempt=number)
                common._write_session_meta(str(public), sid, int(row['id']), proc, 'vaapi', number)
            audit.start_reader(proc.stderr, progress_stream=proc.stdout)
            start_producer_control(task, proc, plan, float(info.get('duration') or 0))
            deadline = time.monotonic() + 45
            meter = Throughput()
            failure = None
            while True:
                if task.get('cancelled'):
                    return
                if audit.error:
                    failure = 'dovi_metadata_invalid'
                    break
                try:
                    publisher.poll()
                except (AuditError, PublishError, ValueError, OSError):
                    failure = 'dovi_metadata_invalid' if audit.error else 'dovi_pipeline_unavailable'
                    break
                if not task['ready'].is_set():
                    if (common._seg_count(str(public), 'video_') >= common._min_segs(plan)
                            and all((public / f'out_{name}.m3u8').is_file() for name in expected_renditions)):
                        with common._sess_lock:
                            if task.get('cancelled') or common._tasks.get(task['key']) is not task:
                                return
                            (public / 'plan.json').write_text(task['plan_key'], encoding='utf-8')
                            task['ready'].set()
                        wake_producer_control(task)
                    elif time.monotonic() >= deadline:
                        failure = 'dovi_pipeline_unavailable'
                        break
                if proc.poll() is not None:
                    audit.join_readers(timeout=3)
                    try:
                        if audit.readers_alive():
                            raise AuditError('dovi_audit_reader_not_drained')
                        audit.finish(proc.returncode)
                        publisher.poll()
                        expected = max(0.0, float(info.get('duration') or 0) - start)
                        if expected and float(publisher.video_end) + 1 < expected:
                            failure = 'dovi_source_incomplete'
                            break
                        publisher.poll(final=True)
                    except (AuditError, PublishError, ValueError):
                        failure = 'dovi_metadata_invalid' if audit.error else 'dovi_pipeline_unavailable'
                        break
                    if proc.returncode == 0 and publisher.complete and not audit.error:
                        with common._sess_lock:
                            if not task.get('cancelled') and common._tasks.get(task['key']) is task:
                                proof = public / 'dovi-verified.json.tmp'
                                proof.write_text(task['plan_key'], encoding='utf-8')
                                os.replace(proof, public / 'dovi-verified.json')
                                common._write_complete_marker(str(public), task['plan_key'])
                                task['complete'] = True
                                for owner in task['owners']:
                                    if owner in common._sessions:
                                        common._sessions[owner]['complete'] = True
                                task['ready'].set()
                        return
                    failure = 'dovi_pipeline_unavailable'
                    break
                # Published media time, rather than raw encoder speed, includes audit cost.
                paused = bool(getattr(proc, '_jz_buffer_paused', False))
                too_slow = meter.sample(float(publisher.video_end), time.monotonic(), paused)
                task['processing_speed'] = meter.speed
                ahead = buffer_ahead(task, publisher.video_end, time.monotonic())
                if (task['ready'].is_set() and too_slow and ahead is not None
                        and ahead < 8 and not paused):
                    failure = 'dovi_too_slow'
                    break
                time.sleep(0.1)
            common._kill_proc(proc)
            audit.join_readers(timeout=3)
            if proc.poll() is None or audit.readers_alive():
                _fail(task, 'dovi_pipeline_unavailable', plan)
                return
            _close_process_pipes(proc)
            # Only a seek's metadata recovery can retry. Already exposed media is immutable.
            if (failure == 'dovi_metadata_invalid' and number < len(attempts)
                    and not task['ready'].is_set()):
                proc = None
                audit = None
                continue
            _fail(task, failure or 'dovi_pipeline_unavailable', plan)
            return
    except (AuditError, ValueError):
        _fail(task, 'dovi_metadata_invalid', plan)
    except Exception:
        _fail(task, 'dovi_pipeline_unavailable', plan)
    finally:
        common._kill_proc(proc)
        if audit is not None:
            audit.join_readers(timeout=3)
        stopped = ((proc is None or proc.poll() is not None)
                   and (audit is None or not audit.readers_alive()))
        if stopped and proc is not None:
            _close_process_pipes(proc)
        # Keep diagnostics bounded and avoid retaining FFmpeg source URLs or RPU dumps.
        try:
            (public / 'ffmpeg.log').write_text(json.dumps(diagnostics(task), ensure_ascii=False), encoding='utf-8')
        except OSError:
            pass
        if stopped:
            _release_stopped_task(task, public)
        else:
            with common._sess_lock:
                task['cancelled'] = True
                task['ready'].set()
                observer = threading.Thread(target=_await_stalled_process,
                    args=(task, public, proc, audit), daemon=True)
                task['dovi_reaper'] = observer
            observer.start()


def start_task(task, sid, row, src, info, plan, start):
    task.update(dovi=True, watched=True, decoder='hevc_vaapi',
                tone_mapper='libplacebo_vulkan', encoder='h264_vaapi',
                failure_code=None, failure_reason=None, recommended_height=None)
    thread = threading.Thread(target=_run, args=(task, sid, row, src, info, plan, start), daemon=True)
    task['dovi_thread'] = thread
    thread.start()
    if not task['ready'].wait(timeout=100):
        raise HTTPException(504, _MESSAGES['dovi_pipeline_unavailable'])
    if task.get('error'):
        raise task['error']
    if task.get('cancelled'):
        raise HTTPException(503, 'transcode stopped')
