"""真实抽帧覆盖采样时序、尾页、长 GOP 回退；模拟设备覆盖硬件失败和远程读量策略。"""
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from app import media
from app.jobkit import JobRegistry
from app.routers.stream import preview_extract as ex
from app.routers.stream import previews as pv


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setenv('TRANSCODER', 'sw')
    monkeypatch.delenv('PREVIEW_MODE', raising=False)
    monkeypatch.delenv('PREVIEW_THREADS', raising=False)
    monkeypatch.setattr(pv, '_jobs', JobRegistry())
    monkeypatch.setattr(pv, '_root', lambda: tmp_path / 'previews')
    monkeypatch.setattr(pv, '_sessions', {})
    monkeypatch.setattr(pv.store, 'get_library', lambda _lid: {'source': 'local'})
    pv._stop.clear()
    yield
    pv._stop.clear()


@pytest.fixture
def ffmpeg():
    ff = pv.shutil.which('ffmpeg') or media._static_bins_if_present().get('ffmpeg')
    if not ff:
        pytest.skip('ffmpeg unavailable')
    return ff


def source(path):
    return SimpleNamespace(input=str(path), rel='colors.mkv', size=123, mtime=1,
                           backend=SimpleNamespace(library_id=1, root='/isolated'))


def colors(ff, output, *, gop=20):
    args = [ff, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']
    for color, duration in [('red', 10), ('lime', 10), ('blue', 9)]:
        args += ['-f', 'lavfi', '-i', f'color={color}:s=160x90:r=10:d={duration}']
    args += ['-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0', '-c:v', 'libx264',
             '-preset', 'ultrafast', '-threads', '1', '-g', str(gop), '-keyint_min', str(gop),
             '-sc_threshold', '0', str(output)]
    subprocess.run(args, capture_output=True, check=True, timeout=30)


def cells(ff, page):
    result = subprocess.run([ff, '-loglevel', 'error', '-i', str(page), '-frames:v', '1',
                             '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                            capture_output=True, check=True, timeout=10)
    image = result.stdout
    width = pv.WIDTH * pv.COLUMNS
    return [image[((i // pv.COLUMNS * pv.HEIGHT + 44) * width +
                   i % pv.COLUMNS * pv.WIDTH + 80) * 3:][:3]
            for i in range(pv.COLUMNS * pv.ROWS)]


def assert_color(rgb, channel):
    assert rgb[channel] > 200 and max(v for i, v in enumerate(rgb) if i != channel) < 60


def generate(video, duration, interval=1, info=None):
    row = {'id': 7, 'kind': 'movie', 'library_id': 1}
    src = source(video)
    jid = pv._jobs.create()['job_id']
    pv._generate(jid, row, src, {'duration': duration, **(info or {})}, interval)
    return jid, pv._root() / pv._cache_key(row, src, interval)


def test_real_keyframes_batch_two_pages_preserves_timeline_and_pads_tail(ffmpeg, tmp_path, monkeypatch):
    video = tmp_path / 'colors.mkv'
    colors(ffmpeg, video)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    run = pv._run
    calls = []
    def capture(cmd, jid, timeout=60):
        calls.append(cmd)
        return run(cmd, jid, timeout)
    monkeypatch.setattr(pv, '_run', capture)
    jid, directory = generate(video, 29)
    manifest = pv._read_manifest(directory)
    assert manifest['state'] == 'ready' and manifest['count'] == 29
    assert len(calls) == 2 and all('-skip_frame' in cmd for cmd in calls)
    assert [int(cmd[cmd.index('-ss') + 1]) for cmd in calls] == [0, 25]
    first = cells(ffmpeg, directory / 'page_0000.jpg')
    for index, rgb in enumerate(first):
        assert_color(rgb, min(index // 10, 2))
    tail = cells(ffmpeg, directory / 'page_0001.jpg')
    for rgb in tail[:4]:
        assert_color(rgb, 2)
    assert max(tail[4]) < 10  # 尾页未使用的格子仍为黑色。
    assert pv._jobs.get(jid)['frames'] == 29
    assert not list(directory.rglob('frame_*.jpg'))


def test_real_sparse_keyframes_fall_back_to_accurate_frames(ffmpeg, tmp_path, monkeypatch):
    video = tmp_path / 'long-gop.mkv'
    colors(ffmpeg, video, gop=10000)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    run = pv._run
    calls = []
    def capture(cmd, jid, timeout=60):
        calls.append(cmd)
        return run(cmd, jid, timeout)
    monkeypatch.setattr(pv, '_run', capture)
    _, directory = generate(video, 29)
    assert sum('-skip_frame' in cmd for cmd in calls) == 1
    assert pv._read_manifest(directory)['state'] == 'ready'
    first = cells(ffmpeg, directory / 'page_0000.jpg')
    assert_color(first[12], 1)
    assert_color(first[23], 2)
    assert_color(cells(ffmpeg, directory / 'page_0001.jpg')[3], 2)


def test_real_noninteger_keyframe_timestamps_stay_within_one_interval(ffmpeg, tmp_path, monkeypatch):
    video = tmp_path / 'time-ramp.mkv'
    # 每秒红通道增加 8；关键帧每 1.5 秒一个，用像素核对实际画面时间而非只检查页数。
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', "nullsrc=s=160x90:r=10:d=29,geq=r='N*0.8':g=0:b=0",
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '0', '-threads', '1',
                    '-g', '15', '-keyint_min', '15', '-sc_threshold', '0', str(video)],
                   capture_output=True, check=True, timeout=30)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    run = pv._run
    calls = []
    def capture(cmd, jid, timeout=60):
        calls.append(cmd)
        return run(cmd, jid, timeout)
    monkeypatch.setattr(pv, '_run', capture)
    _, directory = generate(video, 29)
    assert len(calls) == 2 and all('-skip_frame' in cmd for cmd in calls)
    for page, size in [(0, 25), (1, 4)]:
        for cell, rgb in enumerate(cells(ffmpeg, directory / f'page_{page:04d}.jpg')[:size]):
            target = page * 25 + cell
            assert abs(rgb[0] / 8 - target) <= 1.4  # JPEG/色彩转换容许约 3 级误差。


def test_real_hevc_10bit_hdr_with_nonzero_start_time(ffmpeg, tmp_path, monkeypatch):
    video = tmp_path / 'hdr.mkv'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=10:d=3',
                    '-vf', 'setpts=PTS+7/TB,format=yuv420p10le', '-c:v', 'libx265',
                    '-preset', 'ultrafast', '-threads', '1', '-g', '10',
                    '-x265-params', 'pools=1:frame-threads=1:scenecut=0:colorprim=9:transfer=16:colormatrix=9',
                    '-color_primaries', 'bt2020', '-color_trc', 'smpte2084',
                    '-colorspace', 'bt2020nc', str(video)],
                   capture_output=True, check=True, timeout=30)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    _, directory = generate(video, 3, info={'hdr': 'hdr10'})
    assert pv._read_manifest(directory)['state'] == 'ready'
    assert all(max(rgb) > 20 for rgb in cells(ffmpeg, directory / 'page_0000.jpg')[:3])


def extractor(src, run, **kwargs):
    return ex.Extractor(src, pv._frame_filter({}), 10, remote=kwargs.pop('remote', False),
                        run=run, jid='preview-test', progress=kwargs.pop('progress', lambda _n: None), **kwargs)


def test_hardware_failure_retries_software_and_remembers_for_later_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, '_hardware_args', lambda: ['-hwaccel', 'vaapi'])
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-hwaccel' in cmd:
            raise RuntimeError('decoder unavailable')
        Path(cmd[-1]).write_bytes(b'jpeg')
        return '\n'.join(f'n: {i} pts: {i} pts_time:{i * 10}' for i in range(27))
    worker = extractor(source('/isolated/video.mkv'), run)
    worker.page(0, 25, 5, 5, tmp_path / 'page.jpg')
    worker.page(250, 2, 5, 5, tmp_path / 'page.jpg')
    assert len(calls) == 3
    assert '-hwaccel' in calls[0] and all('-hwaccel' not in cmd for cmd in calls[1:])
    assert worker.fast


@pytest.mark.parametrize('remote,url', [(True, '/mounted/video.mkv'), (True, 'http://127.0.0.1/video')])
def test_remote_reads_only_sampling_points_and_uses_probe_limits(remote, url, tmp_path, monkeypatch):
    monkeypatch.setenv('FFMPEG_PROBESIZE', '12345')
    calls, progress = [], []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b'jpeg')
        return ''
    worker = extractor(source(url), run, remote=remote, progress=progress.append)
    worker.page(250, 2, 5, 5, tmp_path / 'page.jpg')
    inputs = [cmd for cmd in calls if '-ss' in cmd]
    assert [cmd[cmd.index('-ss') + 1] for cmd in inputs] == ['250', '260']
    assert all('-skip_frame' not in cmd and '-t' not in cmd for cmd in inputs)
    if url.startswith('http'):
        assert all(cmd[cmd.index('-probesize') + 1] == '12345' for cmd in inputs)
    assert progress == [26, 27]


def test_explicit_accurate_mode_and_empty_keyframe_output(tmp_path, monkeypatch):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-skip_frame' not in cmd:
            Path(cmd[-1]).write_bytes(b'jpeg')
        return ''
    worker = extractor(source('/isolated/video.mkv'), run)
    worker.page(0, 1, 5, 5, tmp_path / 'page.jpg')
    assert len(calls) == 3 and not worker.fast
    calls.clear()
    monkeypatch.setenv('PREVIEW_MODE', 'accurate')
    extractor(source('/isolated/video.mkv'), run).page(0, 1, 5, 5, tmp_path / 'page.jpg')
    assert len(calls) == 2 and all('-skip_frame' not in cmd for cmd in calls)


def test_remote_all_probe_limits_can_be_disabled(tmp_path, monkeypatch):
    for name in ('FFMPEG_PROBESIZE', 'FFMPEG_ANALYZEDURATION', 'FFMPEG_RW_TIMEOUT_US'):
        monkeypatch.setenv(name, '0')
    inputs = []
    def run(cmd, _jid, timeout=60):
        if '-ss' in cmd:
            inputs.append(cmd)
        Path(cmd[-1]).write_bytes(b'jpeg')
        return ''
    extractor(source('http://127.0.0.1/video'), run, remote=True).page(0, 1, 5, 5, tmp_path / 'page.jpg')
    assert all(flag not in inputs[0] for flag in ('-probesize', '-analyzeduration', '-rw_timeout'))


def test_software_keyframe_decoder_error_falls_back_once(tmp_path):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-skip_frame' in cmd:
            raise RuntimeError('keyframe decoder unsupported')
        Path(cmd[-1]).write_bytes(b'jpeg')
        return ''
    worker = extractor(source('/isolated/video.mkv'), run)
    worker.page(0, 1, 5, 5, tmp_path / 'page.jpg')
    worker.page(250, 1, 5, 5, tmp_path / 'page.jpg')
    assert sum('-skip_frame' in cmd for cmd in calls) == 1
    assert not worker.fast


def test_accurate_hardware_decode_failure_retries_same_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, '_hardware_args', lambda: ['-hwaccel', 'qsv'])
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-hwaccel' in cmd:
            raise RuntimeError('unsupported codec')
        Path(cmd[-1]).write_bytes(b'jpeg')
        return ''
    extractor(source('http://127.0.0.1/video'), run, remote=True).page(250, 2, 5, 5, tmp_path / 'page.jpg')
    inputs = [cmd for cmd in calls if '-ss' in cmd]
    assert [cmd[cmd.index('-ss') + 1] for cmd in inputs] == ['250', '250', '260']


def test_empty_accurate_output_cannot_reuse_a_stale_frame(tmp_path):
    (tmp_path / 'frame_00.jpg').write_bytes(b'stale from interrupted page')
    def run(*_args, **_kwargs):
        return ''  # FFmpeg 在 EOF 后可能成功退出却没有输出图片。
    worker = extractor(source('http://127.0.0.1/video'), run, remote=True)
    with pytest.raises(RuntimeError, match='无法从采样位置'):
        worker.page(250, 1, 5, 5, tmp_path / 'page.jpg')
    assert not (tmp_path / 'frame_00.jpg').exists()
    assert not (tmp_path / 'page.jpg').exists()


def test_cancellation_does_not_enter_fallback(tmp_path):
    def run(*_args, **_kwargs):
        raise pv._Cancelled()
    worker = extractor(source('/isolated/video.mkv'), run)
    with pytest.raises(pv._Cancelled):
        worker.page(0, 25, 5, 5, tmp_path / 'page.jpg')
    assert worker.fast


def test_running_batch_process_yields_to_playback_and_is_reaped(monkeypatch):
    jid = pv._jobs.create(library_id=1)['job_id']
    processes = []
    popen = subprocess.Popen
    def capture(*args, **kwargs):
        proc = popen(*args, **kwargs)
        processes.append(proc)
        return proc
    monkeypatch.setattr(pv.subprocess, 'Popen', capture)
    timer = threading.Timer(0.1, lambda: pv._sessions.update({
        'playing': {'proc': SimpleNamespace(poll=lambda: None)}}))
    timer.start()
    try:
        with pytest.raises(pv._YieldToPlayback):
            pv._run([sys.executable, '-c', 'import time; time.sleep(60)'], jid)
        assert processes[0].poll() is not None
    finally:
        timer.cancel()


def test_page_interrupted_by_playback_restarts_without_committing_bad_output(monkeypatch):
    starts = []
    def page(_self, start, _size, _columns, _rows, output):
        starts.append(start)
        output.write_bytes(b'partial' if len(starts) == 1 else b'complete')
        if len(starts) == 1:
            raise pv._YieldToPlayback()
    monkeypatch.setattr(pv.Extractor, 'page', page)
    _, directory = generate('/isolated/video.mkv', 3)
    assert starts == [0, 0]
    assert (directory / 'page_0000.jpg').read_bytes() == b'complete'
    assert pv._read_manifest(directory)['state'] == 'ready'


@pytest.mark.parametrize('pref,device,expected', [
    ('sw', True, None), ('auto', True, 'vaapi'), ('auto', False, None),
    ('vaapi', False, 'vaapi'), ('qsv', False, 'qsv'), ('nvenc', False, 'cuda'),
])
def test_decode_device_selection_respects_transcoder_configuration(monkeypatch, pref, device, expected):
    monkeypatch.setenv('TRANSCODER', pref)
    monkeypatch.setattr(ex.Path, 'exists', lambda p: device and str(p).endswith('renderD128'))
    args = ex._hardware_args()
    assert (args[1] if args else None) == expected
