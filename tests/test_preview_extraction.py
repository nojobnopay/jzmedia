"""真实抽帧覆盖采样时序、尾页、长 GOP 回退；模拟设备覆盖硬件失败和远程读量策略。"""
from pathlib import Path
import math
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
    return [image[((i // pv.COLUMNS * pv.HEIGHT + pv.HEIGHT // 2) * width +
                   i % pv.COLUMNS * pv.WIDTH + pv.WIDTH // 2) * 3:][:3]
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
    assert len(calls) == 4
    inputs = [cmd for cmd in calls if '-ss' in cmd]
    assert len(inputs) == 2 and all('-skip_frame' in cmd for cmd in inputs)
    assert [int(cmd[cmd.index('-ss') + 1]) for cmd in inputs] == [0, 25]
    first = cells(ffmpeg, directory / 'page_0000.jpg')
    for index, rgb in enumerate(first):
        assert_color(rgb, min(index // 10, 2))
    tail = cells(ffmpeg, directory / 'page_0001.jpg')
    for rgb in tail[:4]:
        assert_color(rgb, 2)
    assert max(tail[4]) < 10  # 尾页未使用的格子仍为黑色。
    assert pv._jobs.get(jid)['frames'] == 29
    assert not list(directory.rglob('frame_*.png'))


def test_real_sparse_keyframes_fall_back_when_nearby_seek_window_is_insufficient(ffmpeg, tmp_path, monkeypatch):
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
    # GOP 超过逐点读取窗口时，仍要精确解码，不能复用很远的画面。
    assert 1 <= sum('-skip_frame' in cmd for cmd in calls) <= 2
    assert any('-noaccurate_seek' in cmd for cmd in calls)
    inputs = [cmd for cmd in calls if '-ss' in cmd]
    assert any('-noaccurate_seek' not in cmd and '-skip_frame' not in cmd for cmd in inputs)
    assert pv._read_manifest(directory)['state'] == 'ready'
    first = cells(ffmpeg, directory / 'page_0000.jpg')
    assert_color(first[12], 1)
    assert_color(first[23], 2)
    assert_color(cells(ffmpeg, directory / 'page_0001.jpg')[3], 2)


@pytest.mark.parametrize('force_points', [False, True])
@pytest.mark.parametrize('codec', ['libx264', 'libx265'])
def test_real_noninteger_keyframe_timestamps_stay_within_one_interval(ffmpeg, tmp_path, monkeypatch,
                                                                    force_points, codec):
    video = tmp_path / 'time-ramp.mkv'
    # 每秒红通道增加 8；关键帧每 1.5 秒一个，用像素核对实际画面时间而非只检查页数。
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', "nullsrc=s=160x90:r=10:d=29,geq=r='N*0.8':g=0:b=0",
                    '-c:v', codec, '-preset', 'ultrafast', '-crf', '0', '-threads', '1',
                    '-g', '15', '-keyint_min', '15', '-sc_threshold', '0',
                    *(['-x265-params', 'pools=1:frame-threads=1:scenecut=0'] if codec == 'libx265' else []),
                    str(video)],
                   capture_output=True, check=True, timeout=30)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    run = pv._run
    calls = []
    def capture(cmd, jid, timeout=60):
        calls.append(cmd)
        if force_points and '-skip_frame' in cmd:
            raise RuntimeError('window extraction unavailable')
        return run(cmd, jid, timeout)
    monkeypatch.setattr(pv, '_run', capture)
    _, directory = generate(video, 29)
    if force_points:
        assert sum('-noaccurate_seek' in cmd and '-skip_frame' not in cmd for cmd in calls) == 29
        assert sum('-skip_frame' in cmd for cmd in calls) == 1
        inputs = [cmd for cmd in calls if '-ss' in cmd]
        assert all('-noaccurate_seek' in cmd and '-skip_frame' not in cmd for cmd in inputs[1:])
    else:
        assert len(calls) == 4
        assert sum('-skip_frame' in cmd for cmd in calls) == 2
    for page, size in [(0, 25), (1, 4)]:
        for cell, rgb in enumerate(cells(ffmpeg, directory / f'page_{page:04d}.jpg')[:size]):
            target = page * 25 + cell
            assert abs(rgb[0] / 8 - target) <= 1.4  # JPEG/色彩转换容许约 3 级误差。


def test_real_hybrid_page_only_decodes_one_uncovered_sample(ffmpeg, tmp_path, monkeypatch):
    video = tmp_path / 'one-gap.mkv'
    # 89～111 秒之间没有关键帧，仅 100 秒这个采样点超过 ±10 秒容差。
    keyframes = [0, *range(9, 90, 10), 111, *range(119, 250, 10)]
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', "nullsrc=s=160x90:r=1:d=250,geq=r='N*0.8':g=0:b=0",
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '0', '-threads', '1',
                    '-g', '1000', '-keyint_min', '1', '-sc_threshold', '0',
                    '-force_key_frames', ','.join(map(str, keyframes)), str(video)],
                   capture_output=True, check=True, timeout=30)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    run, calls = pv._run, []
    def capture(cmd, jid, timeout=60):
        calls.append(cmd)
        return run(cmd, jid, timeout)
    monkeypatch.setattr(pv, '_run', capture)
    _, directory = generate(video, 250, interval=10)
    assert pv._read_manifest(directory)['state'] == 'ready'
    assert len(calls) == 3  # 一次批量、一次补缺、一次拼图。
    repaired = [cmd for cmd in calls if '-ss' in cmd and '-skip_frame' not in cmd]
    assert [float(cmd[cmd.index('-ss') + 1]) for cmd in repaired] == [100]
    expected_times = [0, *range(9, 90, 10), 90, 111, *range(119, 240, 10)]
    for rgb, expected in zip(cells(ffmpeg, directory / 'page_0000.jpg'), expected_times):
        assert abs(rgb[0] - expected * 0.8) <= 5.2
        assert max(rgb[1:]) < 8


@pytest.mark.parametrize('pts,interval,expected_channels', [
    ((480, 0, 240), 10, [1, 2, 0]),
    ((509, 293, 546), 20, [1, 0, 2]),
])
def test_real_unordered_decoder_pts_preserve_image_pairing_and_sample_colors(
        ffmpeg, tmp_path, monkeypatch, pts, interval, expected_channels):
    video = tmp_path / 'three-colors.mkv'
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']
    for color in ('red', 'lime', 'blue'):
        command += ['-f', 'lavfi', '-i', f'color={color}:s=160x90:r=1:d=1']
    command += ['-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0,setpts=N/TB',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1', '-g', '1', str(video)]
    subprocess.run(command, capture_output=True, check=True, timeout=30)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    run, calls, observed = pv._run, [], []
    def capture(cmd, jid, timeout=60):
        calls.append(cmd)
        if '-skip_frame' in cmd:
            # 模拟 HEVC 的解码输出：三张不同画面按 n 输出，但原始 PTS 乱序。
            # 第二组即现场触发 mjpeg Invalid pts 的时间序列，保留 1/24 时基。
            vf_index = cmd.index('-vf') + 1
            inject = ("settb=expr=1/24,setpts='if(eq(N,0),"
                      f"{pts[0]},if(eq(N,1),{pts[1]},{pts[2]}))',")
            cmd[vf_index] = cmd[vf_index].replace('showinfo=checksum=0', inject + 'showinfo=checksum=0', 1)
        log = run(cmd, jid, timeout)
        if '-skip_frame' in cmd:
            assert log.frame_numbers == (0, 1, 2)
            assert log.frame_times == pytest.approx([value / 24 for value in pts], abs=0.0001)
            # 无损候选图片必须完整保留原输出顺序，不能只保留排序后的时间戳。
            decoded = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error',
                                       '-start_number', '0', '-i', cmd[-1], '-frames:v', '3',
                                       '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                                      capture_output=True, check=True, timeout=10).stdout
            frame_bytes = pv.WIDTH * pv.HEIGHT * 3
            assert len(decoded) == frame_bytes * 3
            for index in range(3):
                pixel = index * frame_bytes + ((pv.HEIGHT // 2) * pv.WIDTH + pv.WIDTH // 2) * 3
                assert_color(decoded[pixel:pixel + 3], index)
            observed.extend(log.frame_times)
        return log
    monkeypatch.setattr(pv, '_run', capture)
    _, directory = generate(video, interval * 3, interval=interval)
    assert pv._read_manifest(directory)['state'] == 'ready'
    assert len(calls) == 2 and len(observed) == 3
    for rgb, channel in zip(cells(ffmpeg, directory / 'page_0000.jpg'), expected_channels):
        assert_color(rgb, channel)


@pytest.mark.parametrize('force_points', [False, True])
def test_real_hevc_10bit_hdr_with_nonzero_start_time(ffmpeg, tmp_path, monkeypatch, force_points):
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
    run, calls = pv._run, []
    def capture(cmd, jid, timeout=60):
        calls.append(cmd)
        if force_points and '-skip_frame' in cmd:
            raise RuntimeError('window extraction unavailable')
        return run(cmd, jid, timeout)
    monkeypatch.setattr(pv, '_run', capture)
    _, directory = generate(video, 3, info={'hdr': 'hdr10'})
    assert pv._read_manifest(directory)['state'] == 'ready'
    assert all(max(rgb) > 20 for rgb in cells(ffmpeg, directory / 'page_0000.jpg')[:3])
    if force_points:
        assert sum('-noaccurate_seek' in cmd and '-skip_frame' not in cmd for cmd in calls) == 3
        assert sum('-skip_frame' in cmd for cmd in calls) == 1
        inputs = [cmd for cmd in calls if '-ss' in cmd]
        assert all('-noaccurate_seek' in cmd and '-skip_frame' not in cmd for cmd in inputs[1:])


def test_real_widescreen_hdr_uses_even_scaled_dimensions_for_tone_mapping(ffmpeg, tmp_path, monkeypatch):
    video = tmp_path / 'widescreen-hdr.mkv'
    # 192:82 缩进 480x270 时高度为 205；4:2:0 的 HDR zscale 要求偶数尺寸。
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', 'testsrc2=s=192x82:r=10:d=1',
                    '-vf', 'format=yuv420p10le', '-c:v', 'libx265', '-preset', 'ultrafast',
                    '-threads', '1', '-x265-params',
                    'pools=1:frame-threads=1:scenecut=0:colorprim=9:transfer=16:colormatrix=9',
                    '-color_primaries', 'bt2020', '-color_trc', 'smpte2084',
                    '-colorspace', 'bt2020nc', str(video)],
                   capture_output=True, check=True, timeout=30)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    _, directory = generate(video, 1, info={'hdr': 'hdr10'})
    assert pv._read_manifest(directory)['state'] == 'ready'
    assert max(cells(ffmpeg, directory / 'page_0000.jpg')[0]) > 20


@pytest.mark.parametrize('mode', ['batch', 'nearby', 'accurate'])
def test_real_detail_frames_remain_lossless_until_single_final_jpeg(
        ffmpeg, tmp_path, monkeypatch, mode):
    # 细文字般的亮度纹理和单像素彩色边缘，对中间 JPEG 与 4:2:0 色度采样均敏感。
    raw = bytes(channel for y in range(pv.HEIGHT) for x in range(pv.WIDTH)
                for channel in ((255, 12, 30) if (x + y) % 3 == 0 else
                                (16, 220, 250) if x % 5 == 0 else
                                (240, 240, 240) if (x // 2 + y // 2) % 2 else (12, 12, 12)))
    video = tmp_path / 'detail.mkv'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'rawvideo', '-pixel_format', 'rgb24',
                    '-video_size', f'{pv.WIDTH}x{pv.HEIGHT}', '-framerate', '1', '-i', '-',
                    '-frames:v', '1', '-c:v', 'ffv1', '-pix_fmt', 'gbrp', '-threads', '1', str(video)],
                   input=raw, capture_output=True, check=True, timeout=15)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    if mode == 'accurate':
        monkeypatch.setenv('PREVIEW_MODE', 'accurate')
    filter_arg = pv._frame_filter({})
    reference = subprocess.run(
        [ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(video),
         '-vf', filter_arg, '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
        capture_output=True, check=True, timeout=10).stdout
    # 本例无需缩放或 tone-map；RGB→4:2:0→RGB 同样属于不必要的信息丢失。
    assert reference == raw

    def decode(path):
        return subprocess.run(
            [ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(path),
             '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
            capture_output=True, check=True, timeout=10).stdout

    run, observed = pv._run, []
    jid = pv._jobs.create()['job_id']
    def capture(cmd, job_id, timeout=60):
        if mode == 'nearby' and '-skip_frame' in cmd:
            raise RuntimeError('exercise nearby sampling')
        log = run(cmd, job_id, timeout)
        if '-ss' in cmd:
            images = ([Path(cmd[-1] % index) for index in range(len(log.frame_times))]
                      if '%06d' in cmd[-1] else [Path(cmd[-1])])
            for image in images:
                assert image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
                assert decode(image) == reference
                observed.append(image)
        return log
    worker = ex.Extractor(source(video), filter_arg, 10, remote=False, run=capture,
                          jid=jid, progress=lambda _n: None)
    output = tmp_path / 'page.jpg'
    worker.page(0, 1, 1, 1, output)
    assert observed
    assert worker.last_mode == {'batch': 'window_keyframes', 'nearby': 'point_seek',
                                'accurate': 'accurate'}[mode]

    # 用滤镜输出 RGB 直接编码一次的 JPEG 作独立参考；不能只断言命令选项。
    expected = tmp_path / 'single-encode.jpg'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'rawvideo', '-pixel_format', 'rgb24',
                    '-video_size', f'{pv.WIDTH}x{pv.HEIGHT}', '-i', '-', '-frames:v', '1',
                    '-threads', '1', '-pix_fmt', 'yuvj444p', '-q:v', '2', str(expected)],
                   input=reference, capture_output=True, check=True, timeout=10)
    assert decode(output) == decode(expected)
    assert not list(tmp_path.rglob('frame_*.png'))
    assert not list(tmp_path.glob('.keyframes-*'))


def extractor(src, run, **kwargs):
    return ex.Extractor(src, pv._frame_filter({}), 10, remote=kwargs.pop('remote', False),
                        run=run, jid='preview-test', progress=kwargs.pop('progress', lambda _n: None), **kwargs)


def write_frames(cmd, times=(0,), *, numbers=None):
    """模拟 image2 真实输出序号；每张图片留不同内容，防止时间戳重排后配错图。"""
    if '%06d' in cmd[-1]:
        for index, _time in enumerate(times):
            Path(cmd[-1] % index).write_bytes(f'image-{index}'.encode())
    else:
        Path(cmd[-1]).write_bytes(b'jpeg')
    return '\n'.join(f'n: {number} pts: {index} pts_time:{time}'
                     for index, (number, time) in enumerate(zip(numbers or range(len(times)), times)))


@pytest.mark.parametrize('times,expected', [
    ((20, 0, 10), [b'image-1', b'image-2', b'image-0']),
    ((0, 0, 20), [b'image-0', b'image-0', b'image-2']),
    ((10,), [b'image-0', b'image-0', b'image-0']),
    ((-10, 10, 30), [b'image-0', b'image-1', b'image-1']),
])
def test_batch_matches_images_to_pts_and_reuses_nearest_valid_frame(tmp_path, times, expected):
    calls, assembled = [], []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-skip_frame' in cmd:
            return write_frames(cmd, times)
        assert '-ss' not in cmd, 'valid batch frames must not be decoded again'
        assembled.extend((tmp_path / f'frame_{cell:02d}.png').read_bytes() for cell in range(3))
        return write_frames(cmd)
    worker = extractor(source('/isolated/video.mkv'), run)
    worker.page(250, 3, 5, 5, tmp_path / 'page.jpg')
    assert assembled == expected
    assert len(calls) == 2 and worker.mode == 'window_keyframes'
    assert not list(tmp_path.glob('frame_*.png'))
    assert not list(tmp_path.glob('.keyframes-*'))


@pytest.mark.parametrize('bad_numbering,missing_image', [
    ((0, 2), False), ((1, 2), False), ((0, 0), False), ((0, 1), True),
])
def test_batch_with_unreliable_image_pts_pairing_is_never_reused(tmp_path, bad_numbering, missing_image):
    points, assembled = [], []
    def run(cmd, _jid, timeout=60):
        if '-skip_frame' in cmd:
            log = write_frames(cmd, (0, 10), numbers=bad_numbering)
            if missing_image:
                Path(cmd[-1] % 1).unlink()
            return log
        if '-ss' in cmd:
            points.append(float(cmd[cmd.index('-ss') + 1]))
            Path(cmd[-1]).write_bytes(b'verified point')
            return 'n: 0 pts: 0 pts_time:0'
        assembled.extend((tmp_path / f'frame_{cell:02d}.png').read_bytes() for cell in range(2))
        return write_frames(cmd)
    worker = extractor(source('/isolated/video.mkv'), run)
    worker.page(250, 2, 5, 5, tmp_path / 'page.jpg')
    assert points == [250, 260]
    assert assembled == [b'verified point'] * 2
    assert worker.mode == 'point_seek'


@pytest.mark.parametrize('point_valid', [True, False])
def test_batch_preserves_covered_cells_and_only_repairs_gap_without_disabling_next_page(tmp_path, point_valid):
    calls, progress, assembled = [], [], []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-skip_frame' in cmd:
            times = (0, 10, 19, 41) if float(cmd[cmd.index('-ss') + 1]) == 0 else (0,)
            return write_frames(cmd, times)
        if '-ss' in cmd:
            Path(cmd[-1]).write_bytes(b'nearby' if '-noaccurate_seek' in cmd else b'accurate')
            return 'n: 0 pts: 0 pts_time:0' if point_valid else 'n: 0 pts: 11 pts_time:10.01'
        size = 5 if len(assembled) == 0 else 1
        assembled.append([(tmp_path / f'frame_{cell:02d}.png').read_bytes() for cell in range(size)])
        return write_frames(cmd)
    worker = extractor(source('/isolated/video.mkv'), run, progress=progress.append)
    worker.page(0, 5, 5, 5, tmp_path / 'page.jpg')
    worker.page(250, 1, 5, 5, tmp_path / 'page.jpg')
    assert assembled[0] == [b'image-0', b'image-1', b'image-2',
                            b'nearby' if point_valid else b'accurate', b'image-3']
    repaired = [cmd for cmd in calls if '-ss' in cmd and '-skip_frame' not in cmd]
    assert [float(cmd[cmd.index('-ss') + 1]) for cmd in repaired] == ([30] if point_valid else [30, 30])
    assert sum('-skip_frame' in cmd for cmd in calls) == 2
    assert worker.mode == 'window_keyframes'
    assert progress == [1, 2, 3, 4, 5, 26]


@pytest.mark.parametrize('exception', [pv._Cancelled, pv._YieldToPlayback])
def test_interruption_during_single_gap_repair_propagates_and_cleans_temporary_frames(tmp_path, exception):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-skip_frame' in cmd:
            return write_frames(cmd, (0, 10, 19, 41))
        raise exception()
    worker = extractor(source('/isolated/video.mkv'), run)
    with pytest.raises(exception):
        worker.page(0, 5, 5, 5, tmp_path / 'page.jpg')
    assert len(calls) == 2
    assert not (tmp_path / 'page.jpg').exists()
    assert not list(tmp_path.glob('.keyframes-*'))
    assert not list(tmp_path.glob('frame_*.png'))


def test_hardware_failure_retries_software_and_remembers_for_later_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, '_hardware_args', lambda: ['-hwaccel', 'vaapi'])
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-hwaccel' in cmd:
            raise RuntimeError('decoder unavailable')
        return write_frames(cmd, tuple(i * 10 for i in range(27)))
    worker = extractor(source('/isolated/video.mkv'), run)
    worker.page(0, 25, 5, 5, tmp_path / 'page.jpg')
    worker.page(250, 2, 5, 5, tmp_path / 'page.jpg')
    assert len(calls) == 5
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
    assert all('-skip_frame' not in cmd and '-noaccurate_seek' not in cmd and '-t' not in cmd for cmd in inputs)
    if url.startswith('http'):
        assert all(cmd[cmd.index('-probesize') + 1] == '12345' for cmd in inputs)
    assert progress == [26, 27]


def test_explicit_accurate_mode_and_empty_keyframe_output(tmp_path, monkeypatch):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-skip_frame' not in cmd and '-noaccurate_seek' not in cmd:
            Path(cmd[-1]).write_bytes(b'jpeg')
        return ''
    worker = extractor(source('/isolated/video.mkv'), run)
    worker.page(0, 1, 5, 5, tmp_path / 'page.jpg')
    assert len(calls) == 4 and not worker.fast
    calls.clear()
    monkeypatch.setenv('PREVIEW_MODE', 'accurate')
    extractor(source('/isolated/video.mkv'), run).page(0, 1, 5, 5, tmp_path / 'page.jpg')
    assert len(calls) == 2 and all('-skip_frame' not in cmd and '-noaccurate_seek' not in cmd for cmd in calls)


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
        if '-skip_frame' in cmd or '-noaccurate_seek' in cmd:
            raise RuntimeError('keyframe decoder unsupported')
        Path(cmd[-1]).write_bytes(b'jpeg')
        return ''
    worker = extractor(source('/isolated/video.mkv'), run)
    worker.page(0, 1, 5, 5, tmp_path / 'page.jpg')
    worker.page(250, 1, 5, 5, tmp_path / 'page.jpg')
    assert sum('-skip_frame' in cmd for cmd in calls) == 1
    assert sum('-noaccurate_seek' in cmd and '-skip_frame' not in cmd for cmd in calls) == 1
    assert not worker.fast


def test_failed_window_uses_nearby_point_seek_for_this_and_later_pages(tmp_path):
    calls, progress = [], []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-skip_frame' in cmd:
            raise RuntimeError('window decoder failed')
        Path(cmd[-1]).write_bytes(b'jpeg')
        return 'n: 0 pts: -1 pts_time:-0.5' if '-noaccurate_seek' in cmd else ''
    worker = extractor(source('/isolated/video.mkv'), run, progress=progress.append)
    worker.page(0, 2, 5, 5, tmp_path / 'page.jpg')
    worker.page(250, 1, 5, 5, tmp_path / 'page.jpg')
    inputs = [cmd for cmd in calls if '-ss' in cmd]
    assert len(inputs) == 4
    assert '-skip_frame' in inputs[0]
    assert all('-noaccurate_seek' in cmd and '-skip_frame' not in cmd for cmd in inputs[1:])
    assert all(float(cmd[cmd.index('-t') + 1]) == 20 for cmd in inputs[1:])
    assert [cmd[cmd.index('-ss') + 1] for cmd in inputs[1:]] == ['0', '10', '250']
    assert progress == [1, 2, 26]
    assert worker.fast and worker.mode == 'point_seek'


@pytest.mark.parametrize('point_log', [
    '', 'n: 0 pts: 11 pts_time:10.01', 'n: 0 pts: -11 pts_time:-10.01',
    'n: 0 pts: 0 pts_time:nan',
])
def test_unverifiable_nearby_frame_retries_same_point_accurately_and_remembers(tmp_path, point_log):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-skip_frame' in cmd:
            raise RuntimeError('window decoder failed')
        Path(cmd[-1]).write_bytes(b'jpeg')
        return point_log if '-noaccurate_seek' in cmd else ''
    worker = extractor(source('/isolated/video.mkv'), run)
    worker.page(0, 2, 5, 5, tmp_path / 'page.jpg')
    worker.page(250, 1, 5, 5, tmp_path / 'page.jpg')
    inputs = [cmd for cmd in calls if '-ss' in cmd]
    points = [cmd for cmd in inputs if '-noaccurate_seek' in cmd and '-skip_frame' not in cmd]
    accurate = [cmd for cmd in inputs if '-skip_frame' not in cmd and '-noaccurate_seek' not in cmd]
    assert len(points) == 1
    assert [cmd[cmd.index('-ss') + 1] for cmd in accurate] == ['0', '10', '250']
    assert not worker.fast


def test_point_hardware_failure_retries_software_at_same_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, '_hardware_args', lambda: ['-hwaccel', 'vaapi'])
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-noaccurate_seek' in cmd and '-skip_frame' not in cmd and '-hwaccel' in cmd:
            raise RuntimeError('hardware decoder failed')
        if '-skip_frame' in cmd:
            return write_frames(cmd, (1000,))
        Path(cmd[-1]).write_bytes(b'jpeg')
        if '-noaccurate_seek' in cmd:
            return 'n: 0 pts: 0 pts_time:0'
        return ''
    worker = extractor(source('/isolated/video.mkv'), run)
    worker.page(250, 1, 5, 5, tmp_path / 'page.jpg')
    worker.page(500, 1, 5, 5, tmp_path / 'page.jpg')
    points = [cmd for cmd in calls if '-noaccurate_seek' in cmd and '-skip_frame' not in cmd]
    assert [cmd[cmd.index('-ss') + 1] for cmd in points] == ['250', '250', '500']
    assert all('-skip_frame' not in cmd for cmd in points)
    assert '-hwaccel' in points[0] and all('-hwaccel' not in cmd for cmd in points[1:])
    assert worker.fast


def test_point_cancellation_does_not_enter_accurate_fallback(tmp_path):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-skip_frame' not in cmd and '-noaccurate_seek' in cmd:
            raise pv._Cancelled()
        raise RuntimeError('window decoder failed')
    worker = extractor(source('/isolated/video.mkv'), run)
    with pytest.raises(pv._Cancelled):
        worker.page(0, 2, 5, 5, tmp_path / 'page.jpg')
    assert len(calls) == 2
    assert '-skip_frame' in calls[0]
    assert '-noaccurate_seek' in calls[1] and '-skip_frame' not in calls[1]


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
    (tmp_path / 'frame_00.png').write_bytes(b'stale from interrupted page')
    def run(*_args, **_kwargs):
        return ''  # FFmpeg 在 EOF 后可能成功退出却没有输出图片。
    worker = extractor(source('http://127.0.0.1/video'), run, remote=True)
    with pytest.raises(RuntimeError, match='无法从采样位置'):
        worker.page(250, 1, 5, 5, tmp_path / 'page.jpg')
    assert not (tmp_path / 'frame_00.png').exists()
    assert not (tmp_path / 'page.jpg').exists()


def test_real_empty_accurate_tail_recovers_only_a_verified_nearby_frame(ffmpeg, tmp_path, monkeypatch):
    video = tmp_path / 'tail.mkv'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', "nullsrc=s=160x90:r=10:d=3,geq=r='N*7':g=0:b=0",
                    '-c:v', 'ffv1', '-threads', '1', str(video)],
                   capture_output=True, check=True, timeout=15)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    jid, calls = pv._jobs.create()['job_id'], []
    def run(cmd, job_id, timeout=60):
        calls.append(cmd)
        return pv._run(cmd, job_id, timeout)
    worker = ex.Extractor(source(video), pv._frame_filter({}), 10, remote=True,
                          run=run, jid=jid, progress=lambda _n: None)
    frame = tmp_path / 'frame.png'
    assert worker._sample_point(3.05, frame, nearby=False) == 'accurate'
    assert [float(cmd[cmd.index('-ss') + 1]) for cmd in calls] == pytest.approx([3.05, 2.8])
    raw = subprocess.run([ffmpeg, '-loglevel', 'error', '-i', str(frame), '-frames:v', '1',
                          '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                         capture_output=True, check=True, timeout=10).stdout
    assert raw[0] == pytest.approx(28 * 7, abs=3)
    # 视频结束很久以后仍必须失败，不能用更早的画面把容器尾部填满。
    calls.clear()
    with pytest.raises(RuntimeError, match='无法从采样位置'):
        worker._sample_point(6, frame, nearby=False)
    assert [float(cmd[cmd.index('-ss') + 1]) for cmd in calls] == [6, 5.75, 4]
    assert not frame.exists()


@pytest.mark.parametrize('point_log', [
    '', 'n: 0 pts: 20 pts_time:20', 'n: 0 pts: -1 pts_time:-0.01',
    'n: 0 pts: -100 pts_time:-100\nn: 1 pts: 0 pts_time:0',
])
def test_empty_accurate_recovery_rejects_unverified_or_distant_image(tmp_path, point_log):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if len(calls) > 1:
            Path(cmd[-1]).write_bytes(b'wrong image')
        return point_log
    worker = extractor(source('/isolated/video.mkv'), run, remote=True)
    with pytest.raises(RuntimeError, match='无法从采样位置'):
        worker.page(250, 1, 5, 5, tmp_path / 'page.jpg')
    assert [float(cmd[cmd.index('-ss') + 1]) for cmd in calls] == [250, 249.75, 248]
    assert all(float(cmd[cmd.index('-t') + 1]) == 0.5 for cmd in calls[1:])
    assert not list(tmp_path.glob('frame_*.png'))


def test_empty_accurate_hardware_output_retries_software_before_bounded_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, '_hardware_args', lambda: ['-hwaccel', 'qsv'])
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-t' in cmd:
            Path(cmd[-1]).write_bytes(b'verified')
            return 'n: 0 pts: 1 pts_time:0.1'
        return ''
    worker = extractor(source('/isolated/video.mkv'), run, remote=True)
    assert worker._sample_point(250, tmp_path / 'frame.png', nearby=False) == 'accurate'
    assert [float(cmd[cmd.index('-ss') + 1]) for cmd in calls] == [250, 250, 249.75]
    assert '-hwaccel' in calls[0] and all('-hwaccel' not in cmd for cmd in calls[1:])


@pytest.mark.parametrize('exception', [pv._Cancelled, pv._YieldToPlayback])
def test_empty_accurate_recovery_propagates_interruption(tmp_path, exception):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if len(calls) > 1:
            raise exception()
        return ''
    worker = extractor(source('/isolated/video.mkv'), run, remote=True)
    with pytest.raises(exception):
        worker.page(250, 1, 5, 5, tmp_path / 'page.jpg')
    assert len(calls) == 2
    assert not list(tmp_path.glob('frame_*.png'))


def test_empty_point_diagnostics_record_safe_context_and_output_count(tmp_path, caplog):
    def run(cmd, _jid, timeout=60):
        if '-skip_frame' in cmd or '-noaccurate_seek' in cmd:
            raise RuntimeError('https://user:secret@example.invalid/video?token=private-token')
        return ''
    worker = extractor(source('https://user:secret@example.invalid/video?token=private-token'), run,
                       context={'kind': 'movie', 'id': 23})
    with caplog.at_level('INFO', logger=ex.logger.name), pytest.raises(RuntimeError):
        worker.page(250, 1, 5, 5, tmp_path / 'page.jpg')
    assert all(value in caplog.text for value in ('item=movie:23', 'page=1', 'start=250',
                                                  'mode=accurate', 'images=0', 'returncode=0'))
    assert not any(value in caplog.text for value in ('example.invalid', 'secret', 'private-token'))


def test_selected_video_stream_and_converter_device_are_preserved_before_input(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, '_hardware_args', lambda: ['-hwaccel', 'qsv'])
    inputs = []
    def run(cmd, _jid, timeout=60):
        inputs.append(cmd)
        return write_frames(cmd)
    converter = ['-init_hw_device', 'vulkan=preview', '-filter_hw_device', 'preview']
    worker = extractor(source('/isolated/video.mkv'), run, remote=True, video_stream_index=3,
                       input_args=converter, software=True)
    worker._sample_point(250, tmp_path / 'frame.png', nearby=False)
    assert inputs[0][inputs[0].index('-map') + 1] == '0:3'
    assert '-hwaccel' not in inputs[0]
    assert inputs[0][inputs[0].index('-init_hw_device'):inputs[0].index('-init_hw_device') + 4] == converter
    assert inputs[0].index('-filter_hw_device') < inputs[0].index('-i')


@pytest.mark.parametrize('mode', ['batch', 'nearby', 'accurate', 'recovery'])
@pytest.mark.parametrize('trailing_comma', ['', ','])
def test_frame_guard_precedes_color_conversion_and_timestamp_pairing(tmp_path, mode, trailing_comma):
    guard = 'sidedata=mode=select:type=DOVI_METADATA'
    inputs = []
    def run(cmd, _jid, timeout=60):
        if '-ss' in cmd:
            inputs.append(cmd)
            if mode == 'recovery' and len(inputs) == 1:
                return ''
        return write_frames(cmd)
    worker = extractor(source('/isolated/video.mkv'), run, frame_guard=guard + trailing_comma)
    frame = tmp_path / 'frame.png'
    if mode == 'batch':
        worker._keyframe_page(250, 1, 1, 1, tmp_path / 'page.jpg')
    elif mode == 'nearby':
        worker._nearby_point(250, frame)
    else:
        worker._sample_point(250, frame, nearby=False)
    assert len(inputs) == (2 if mode == 'recovery' else 1)
    for input_index, cmd in enumerate(inputs):
        vf = cmd[cmd.index('-vf') + 1]
        assert vf.count(guard) == 1 and ',,' not in vf
        assert vf.index(guard) < vf.index(worker.filter_arg)
        assert vf.index(guard) < vf.index('showinfo=')
        if (('-noaccurate_seek' in cmd and '-skip_frame' not in cmd) or
                (mode == 'recovery' and input_index > 0)):
            assert vf.startswith('select=') and vf.index('select=') < vf.index(guard)


@pytest.mark.parametrize('remote', [False, True])
def test_real_missing_dolby_metadata_never_produces_preview_or_reuses_stale_frame(
        ffmpeg, tmp_path, monkeypatch, remote, require_dovi_metadata):
    require_dovi_metadata(ffmpeg)
    video = tmp_path / 'without-rpu.mkv'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=10:d=3',
                    '-c:v', 'ffv1', '-threads', '1', str(video)],
                   capture_output=True, check=True, timeout=15)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    calls, jid = [], pv._jobs.create()['job_id']
    def run(cmd, job_id, timeout=60):
        calls.append(cmd)
        log = pv._run(cmd, job_id, timeout)
        # 没有 RPU 的帧必须在 showinfo 之前丢弃，不能留下可与其它 PNG 配错的 PTS。
        assert log.frame_times == ()
        assert not list(tmp_path.rglob('key_*.png'))
        if '-skip_frame' not in cmd:
            assert not list(tmp_path.glob('frame_*.png'))
        return log
    worker = ex.Extractor(source(video), pv._frame_filter({}), 10, remote=remote,
                          run=run, jid=jid, progress=lambda _n: None,
                          frame_guard='sidedata=mode=select:type=DOVI_METADATA,')
    (tmp_path / 'frame_00.png').write_bytes(b'stale from interrupted page')
    with pytest.raises(RuntimeError, match='无法从采样位置'):
        worker.page(1, 1, 1, 1, tmp_path / 'page.jpg')
    # Guarded auto mode probes ordinary points first for both local and remote
    # inputs; missing metadata never activates the adjacent-window optimization.
    assert len(calls) <= 6
    assert all('-skip_frame' not in cmd for cmd in calls)
    assert not (tmp_path / 'page.jpg').exists()
    assert not list(tmp_path.glob('frame_*.png'))
    assert not list(tmp_path.glob('.keyframes-*'))


@pytest.mark.parametrize('threshold,interval,target,expected_calls', [
    (5, 1, 0, 1),  # 第一个有效帧位于窗口外；必须及时停止，不能解完整片去找它。
    (1.5, 1, 2, 4),  # 有限预读也不能接受超过原采样间隔的画面。
])
def test_real_guarded_accurate_seek_rejects_distant_first_eligible_frame(
        ffmpeg, tmp_path, monkeypatch, threshold, interval, target, expected_calls):
    video = tmp_path / 'metadata-gap.mkv'
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'lavfi', '-i', 'testsrc2=s=64x64:r=10:d=8',
                    '-c:v', 'ffv1', '-threads', '1', str(video)],
                   capture_output=True, check=True, timeout=15)
    monkeypatch.setattr(media, 'ffmpeg_bin', lambda: ffmpeg)
    jid, calls, observed = pv._jobs.create()['job_id'], [], []
    def run(cmd, job_id, timeout=60):
        # Keep the synthetic metadata gap at the same ORIGINAL video time when
        # the recovery seeks earlier; filter timestamps are input-relative.
        start = float(cmd[cmd.index('-ss') + 1])
        vf = cmd.index('-vf') + 1
        cmd[vf] = cmd[vf].replace(f'gte(t,{threshold})', f'gte(t,{target + threshold - start})')
        calls.append(cmd)
        log = pv._run(cmd, job_id, timeout)
        observed.append(log.frame_times)
        return log
    # select 与缺少 RPU 时的 sidedata 具有相同丢帧行为，无需 Vulkan 或真实媒体。
    worker = ex.Extractor(source(video), pv._frame_filter({}), interval, remote=True,
                          run=run, jid=jid, progress=lambda _n: None,
                          frame_guard=f"select='gte(t,{threshold})'", software=True)
    frame = tmp_path / 'frame.png'
    with pytest.raises(RuntimeError, match='无法从采样位置'):
        worker._sample_point(target, frame, nearby=False)
    assert len(calls) == expected_calls
    assert all('-t' in cmd for cmd in calls)
    assert float(calls[0][calls[0].index('-t') + 1]) == 2
    assert not frame.exists()
    if threshold < 2:
        assert observed[0][0] == pytest.approx(threshold)
    else:
        assert observed[0] == ()


@pytest.mark.parametrize('times', [(), (float('nan'), 0), (float('inf'),), (-0.01,), (10.01,)])
def test_guarded_accurate_seek_requires_verified_first_output_timestamp(tmp_path, times):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b'unverified frame')
        # Preserve each invalid offset relative to the actual selection window.
        match = ex.re.search(r"select='between\(t,([\d.]+),", cmd[cmd.index('-vf') + 1])
        offset = float(match[1]) if match else 0
        return SimpleNamespace(frame_times=tuple(value + offset for value in times))
    worker = extractor(source('/isolated/video.mkv'), run, remote=True,
                       frame_guard='sidedata=mode=select:type=DOVI_METADATA,')
    with pytest.raises(RuntimeError, match='无法从采样位置'):
        worker._sample_point(250, tmp_path / 'frame.png', nearby=False)
    assert len(calls) == 5
    assert not (tmp_path / 'frame.png').exists()


@pytest.mark.parametrize('interval', [1, 10, 60])
def test_guarded_accurate_all_empty_input_and_recovery_windows_are_bounded(tmp_path, interval):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        return ''
    worker = ex.Extractor(source('/isolated/video.mkv'), pv._frame_filter({}), interval,
                          remote=True, run=run, jid='preview-test', progress=lambda _n: None,
                          frame_guard='sidedata=mode=select:type=DOVI_METADATA,')
    with pytest.raises(RuntimeError, match='无法从采样位置'):
        worker._sample_point(250, tmp_path / 'frame.png', nearby=False)
    window = min(interval, 2)
    assert [float(cmd[cmd.index('-t') + 1]) for cmd in calls] == [
        min(2 * interval, 30), 0.5, 0.5, 10 + window, 30 + window]
    assert not (tmp_path / 'frame.png').exists()


@pytest.mark.parametrize('needed_preroll', [10, 30])
def test_guarded_preroll_restores_only_a_verified_frame_at_the_original_target(tmp_path, needed_preroll):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append((cmd, timeout))
        start = float(cmd[cmd.index('-ss') + 1])
        if start == 250:
            Path(cmd[-1]).write_bytes(b'distant metadata frame')
            return SimpleNamespace(frame_times=(17.5,))
        if start == 250 - needed_preroll:
            Path(cmd[-1]).write_bytes(b'verified target frame')
            return SimpleNamespace(frame_times=(needed_preroll + 0.125,))
        return SimpleNamespace(frame_times=())
    worker = extractor(source('/isolated/video.mkv'), run, remote=True,
                       frame_guard='sidedata=mode=select:type=DOVI_METADATA,')
    frame = tmp_path / 'frame.png'
    assert worker._sample_point(250, frame, nearby=False) == 'accurate'
    assert frame.read_bytes() == b'verified target frame'
    assert len(calls) == (4 if needed_preroll == 10 else 5)
    command, timeout = calls[-1]
    assert float(command[command.index('-t') + 1]) == needed_preroll + 2
    assert timeout == 60
    vf = command[command.index('-vf') + 1]
    assert vf.startswith(f"select='between(t,{float(needed_preroll)},{float(needed_preroll + 2)})',sidedata=")
    assert vf.index('DOVI_METADATA') < vf.index('showinfo') < vf.index('scale=')


@pytest.mark.parametrize('interruption', [pv._Cancelled, pv._YieldToPlayback])
def test_guarded_preroll_propagates_cancellation_and_playback_yield(tmp_path, interruption):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if float(cmd[cmd.index('-ss') + 1]) == 240:
            raise interruption()
        return SimpleNamespace(frame_times=())
    worker = extractor(source('/isolated/video.mkv'), run, remote=True,
                       frame_guard='sidedata=mode=select:type=DOVI_METADATA,')
    frame = tmp_path / 'frame.png'
    with pytest.raises(interruption):
        worker._sample_point(250, frame, nearby=False)
    assert len(calls) == 4
    assert not frame.exists()


def test_invalid_video_data_is_reported_only_after_guarded_recovery_fails(tmp_path):
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        return 'Invalid NAL unit 16, skipping. https://private.invalid/token=hidden'
    worker = extractor(source('/isolated/video.mkv'), run, remote=True,
                       frame_guard='sidedata=mode=select:type=DOVI_METADATA,')
    with pytest.raises(ex.ExtractionError) as error:
        worker._sample_point(250, tmp_path / 'frame.png', nearby=False)
    from app.routers.stream.preview_failures import failure_reason
    assert failure_reason(error.value) == ('decoder', '无法解码片源，请检查视频编码或文件是否损坏')
    assert error.value.reason == 'invalid_video_data'
    assert 'private.invalid' not in str(error.value)
    assert 'hidden' not in error.value.diagnostic
    assert len(calls) == 5
    assert not (tmp_path / 'frame.png').exists()


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


def test_run_preserves_all_frame_times_when_decoder_logs_exceed_tail_limit():
    jid = pv._jobs.create()['job_id']
    frames = ''.join(f'[Parsed_showinfo_0 @ 0x123] n: {i} pts: {i} pts_time:{i * 10}\n'
                     for i in range(26))
    script = f'import sys; sys.stderr.write({frames!r}); sys.stderr.write("decoder warning\\n" * 40000)'
    log = pv._run([sys.executable, '-c', script], jid)
    assert log.stderr_bytes > 256 * 1024
    assert len(log.encode('utf-8')) <= 256 * 1024
    assert log.frame_times == tuple(i * 10 for i in range(26))
    assert log.frame_numbers == tuple(range(26))
    assert ex._match_keyframes(log.frame_times, 25, 10) == list(range(25))


@pytest.mark.parametrize('timestamp', ['nan', 'inf', 'N/A'])
def test_run_keeps_invalid_first_timestamp_so_point_cannot_use_next_frame(tmp_path, timestamp):
    jid = pv._jobs.create()['job_id']
    stderr = f'n: 0 pts: 0 pts_time:{timestamp}\nn: 1 pts: 1 pts_time:0\n'
    log = pv._run([sys.executable, '-c', f'import sys; sys.stderr.write({stderr!r})'], jid)
    assert len(log.frame_times) == 2
    assert log.frame_numbers == (0, 1)
    assert not math.isfinite(log.frame_times[0]) and log.frame_times[1] == 0
    calls = []
    def run(cmd, _jid, timeout=60):
        calls.append(cmd)
        if '-skip_frame' in cmd:
            raise RuntimeError('window extraction unavailable')
        Path(cmd[-1]).write_bytes(b'jpeg')
        return log if '-noaccurate_seek' in cmd else ''
    worker = extractor(source('/isolated/video.mkv'), run)
    worker.page(0, 1, 5, 5, tmp_path / 'page.jpg')
    assert not worker.fast
    assert any('-ss' in cmd and '-skip_frame' not in cmd and '-noaccurate_seek' not in cmd for cmd in calls)


def test_run_reads_timestamp_split_at_buffer_boundary_and_final_line_without_newline():
    jid = pv._jobs.create()['job_id']
    prefix = 'n: 0 pts: 0 pts_time:'
    # 首次读取仅包含 12，下一块才包含 .345；最后一条记录没有换行。
    stderr = ('x' * (64 * 1024 - len(prefix) - 3) + '\n' + prefix +
              '12.345\nn: 1 pts: 1 pts_time:20')
    log = pv._run([sys.executable, '-c', f'import sys; sys.stderr.write({stderr!r})'], jid)
    assert log.frame_times == (12.345, 20.0)
    assert log.frame_numbers == (0, 1)


@pytest.mark.parametrize('timeout,script,reason,error_kind', [
    (10, 'import sys; sys.stderr.write("https://user:secret@example.invalid/video?token=private-token\\n"); sys.exit(23)', 'exit', 'unknown'),
    (0.1, 'import sys,time; sys.stderr.write("https://user:secret@example.invalid/video?token=private-token\\n"); sys.stderr.flush(); time.sleep(60)', 'timeout', 'unknown'),
    (10, 'import sys; sys.stderr.write("Error while filtering: image dimensions must be divisible by subsampling factor\\n"); sys.exit(1)', 'exit', 'filter'),
])
def test_run_failure_diagnostics_keep_cause_without_exposing_source(timeout, script, reason, error_kind):
    jid = pv._jobs.create()['job_id']
    with pytest.raises(RuntimeError) as caught:
        pv._run([sys.executable, '-c', script], jid, timeout=timeout)
    error = caught.value
    assert error.reason == reason
    assert error.timed_out == (reason == 'timeout')
    if reason == 'exit':
        assert error.returncode == (1 if error_kind == 'filter' else 23)
    else:
        assert error.returncode is None
    assert error.stderr_bytes > 0 and error.elapsed > 0
    assert reason in error.diagnostic
    assert f'error_kind={error_kind}' in error.diagnostic
    assert not any(value in str(error) + error.diagnostic
                   for value in ('example.invalid', 'secret', 'private-token', script))


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
