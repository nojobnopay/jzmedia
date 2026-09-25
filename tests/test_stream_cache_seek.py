from types import SimpleNamespace

import pytest

from app.routers.stream import common, session


@pytest.mark.parametrize('start, cached_start, media_start, initial', [(450, 0, 0, 450), (450, 450, 445, 5)])
def test_resume_reuses_static_cache_and_preserves_source_time(monkeypatch, tmp_path, start, cached_start, media_start, initial):
    plan = {'vcopy': True, 'seg': 'fmp4', 'sub': 'none'}
    monkeypatch.setattr(session, '_version_source', lambda *a: ({'id': 42, 'kind': 'episode'}, SimpleNamespace(input='unused')))
    monkeypatch.setattr(session, '_media_cached_or_probe', lambda *a: {'playable': True})
    monkeypatch.setattr(session, '_media_payload', lambda *a: {})
    monkeypatch.setattr(session._playback, 'plan', lambda *a, **kw: {'method': 'remux', 'reasons': [], 'plan': dict(plan)})
    monkeypatch.setattr(session, '_ffmpeg_ok', lambda: True)
    def directory(vid, key, position, kind):
        p = tmp_path / f'{kind}_{vid}_{key}_{int(position)}'
        p.mkdir(exist_ok=True)
        return str(p)
    monkeypatch.setattr(session, '_session_dir', directory)
    def media_time(*args):
        assert cached_start != 0, 'full cache must not probe remote keyframes'
        return media_start
    monkeypatch.setattr(session, '_media_start_for', media_time)
    from pathlib import Path
    cache = Path(directory(42, common._session_key(plan, 0), cached_start, 'episode'))
    (cache / 'out_video.m3u8').write_text('#EXTM3U\n#EXTINF:4,\nvideo_seg00000.m4s\n#EXT-X-ENDLIST\n')
    (cache / 'video_seg00000.m4s').write_bytes(b'video')
    (cache / 'complete.json').write_text(common._plan_marker(plan, 0, cached_start))
    result = session.hls_session_create(42, session.SessionBody(start=start, kind='episode'))
    try:
        assert result['complete'] is True
        assert result['media_start'] == media_start
        assert result['initial_time'] == initial
        assert common._sessions[result['session_id']]['proc'] is None
        assert common._sessions[result['session_id']]['sdir'] == str(cache)
    finally:
        common._sessions.pop(result['session_id'], None)
