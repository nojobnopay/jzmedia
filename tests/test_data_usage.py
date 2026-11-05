import sqlite3

from app import data_usage


def test_application_usage_counts_data_but_excludes_mounts_and_media(monkeypatch, tmp_path):
    root = tmp_path / 'data'
    root.mkdir()
    media = root / 'media'
    media.mkdir()
    (media / 'movie.mkv').write_bytes(b'x' * 100)
    (root / 'mounts' / 'lib_1').mkdir(parents=True)
    (root / 'mounts' / 'lib_1' / 'remote.mkv').write_bytes(b'x' * 100)
    (root / 'jzmedia.db').write_bytes(b'x' * 25)
    (root / 'jzmedia.db-wal').write_bytes(b'x' * 10)
    (root / 'posters').mkdir()
    (root / 'posters' / 'movie.jpg').write_bytes(b'x' * 15)
    (root / 'previews').mkdir()
    (root / 'previews' / 'page.jpg').write_bytes(b'x' * 20)
    (root / 'transcode').mkdir()
    (root / 'transcode' / 'part.m4s').write_bytes(b'x' * 30)
    (root / 'outside-link').symlink_to(media, target_is_directory=True)
    monkeypatch.setattr(data_usage.settings, 'data_dir', str(root))
    monkeypatch.setattr(data_usage.settings, 'media_root', str(media))
    monkeypatch.setattr(data_usage.cache_settings, 'warning_bytes', lambda: 100)
    result = data_usage.usage(refresh=True)
    assert result['ok'] and result['over_limit']
    assert result['total_bytes'] == 100
    assert result['categories'] == {'database': 35, 'posters': 15, 'previews': 20,
                                    'transcode': 30, 'other': 0}


def test_application_usage_reports_missing_data_root(monkeypatch, tmp_path):
    monkeypatch.setattr(data_usage.settings, 'data_dir', str(tmp_path / 'missing'))
    result = data_usage.usage(refresh=True)
    assert not result['ok'] and not result['over_limit']
    assert result['error']


def test_registered_local_media_inside_data_is_not_counted(monkeypatch, tmp_path):
    root = tmp_path / 'data'
    root.mkdir()
    media = root / 'custom-local-library'
    media.mkdir()
    (media / 'movie.mkv').write_bytes(b'x' * 500)
    with sqlite3.connect(root / 'jzmedia.db') as connection:
        connection.execute('CREATE TABLE media_libraries (source TEXT, path TEXT)')
        connection.execute('INSERT INTO media_libraries VALUES (?, ?)', ('local', str(media)))
    (root / 'previews').mkdir()
    (root / 'previews' / 'page.jpg').write_bytes(b'x' * 5)
    monkeypatch.setattr(data_usage.settings, 'data_dir', str(root))
    monkeypatch.setattr(data_usage.settings, 'media_root', str(tmp_path / 'other-media'))
    result = data_usage.usage(refresh=True)
    assert result['ok']
    assert result['total_bytes'] == (root / 'jzmedia.db').stat().st_size + 5


def test_persistent_online_episodes_are_separate_from_application_cache_budget(monkeypatch, tmp_path):
    root = tmp_path / 'data'
    (root / 'online-cache').mkdir(parents=True)
    (root / 'online-cache' / 'saved.ts').write_bytes(b'x' * 100)
    (root / 'other-cache').mkdir()
    (root / 'other-cache' / 'saved.mp4').write_bytes(b'x' * 200)
    (root / 'settings.json').write_bytes(b'{}')
    monkeypatch.setattr(data_usage.settings, 'data_dir', str(root))
    monkeypatch.setattr(data_usage.settings, 'media_root', str(tmp_path / 'media'))
    monkeypatch.delenv('ONLINE_CACHE_DIR', raising=False)
    assert data_usage.usage(refresh=True)['total_bytes'] == 202
    monkeypatch.setenv('ONLINE_CACHE_DIR', str(root / 'other-cache'))
    assert data_usage.usage(refresh=True)['total_bytes'] == 102
