"""Persist source rates used by Android's per-file decoder capability checks."""
import json
import sqlite3
import subprocess

import pytest

from app import media, store
from app.routers.stream.common import _media_cached_or_probe
from app.routers.stream import media as stream_media
from app.store import _base


def fake_probe(monkeypatch, video=None, audio=None):
    payload = {
        "format": {"duration": "120", "format_name": "matroska"},
        "streams": [{"codec_type": "video", "codec_name": "h264", "index": 0,
                     "width": 1920, "height": 1080, **(video or {})},
                    {"codec_type": "audio", "codec_name": "aac", "index": 1,
                     "channels": 2, **(audio or {})}],
    }
    monkeypatch.setattr(media, "ffprobe_bin", lambda: "fake-ffprobe")
    monkeypatch.setattr(media.subprocess, "run", lambda cmd, **kwargs:
                        subprocess.CompletedProcess(cmd, 0, json.dumps(payload).encode(), b""))
    return media.probe("isolated-test.mkv", size=100)


@pytest.mark.parametrize(("average", "nominal", "expected"), [
    ("60000/1001", "60000/1001", 60000 / 1001),
    ("24000/1001", "60/1", 24000 / 1001),
    ("0/0", "60/1", 60), (None, "25/1", 25), ("N/A", "24", 24),
    ("29.97", "0/0", 29.97), ("1/0", "0/0", 0),
    ("-1/1", "0/0", 0), ("-1/-1", "0/0", 0),
    ("nan", "inf", 0), ("1e999", "1e999/1e999", 0),
    ("24/1/1", None, 0), (True, None, 0), ("9" * 100, None, 0),
])
def test_probe_parses_rational_frame_rate_without_nonfinite_values(monkeypatch, average, nominal, expected):
    result = fake_probe(monkeypatch, {"avg_frame_rate": average, "r_frame_rate": nominal})
    assert result["playable"] is True
    assert result["fps"] == pytest.approx(expected)
    assert result["probe_ver"] == 4
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(("value", "expected"), [
    ("48000", 48000), (96000, 96000), (None, 0), ("N/A", 0), ("0", 0),
    ("-100", 0), (float("inf"), 0), (float("nan"), 0), ("1/2", 0),
])
def test_audio_sample_rate_is_nonnegative_integer(monkeypatch, value, expected):
    result = fake_probe(monkeypatch, audio={"sample_rate": value})
    assert result["audio"][0]["sample_rate"] == expected


def test_rates_survive_store_and_api_payload(monkeypatch):
    info = fake_probe(monkeypatch, {"avg_frame_rate": "60000/1001"}, {"sample_rate": "48000"})
    mid = store.upsert_movie_by_path("native-rates/cached.mkv")
    store.upsert_media_info(mid, info)
    persisted = store.get_media_info(mid)
    monkeypatch.setattr(stream_media, "_sub_list", lambda m, data: data["subs"])
    payload = stream_media._media_payload({"id": mid}, persisted)
    assert payload["fps"] == pytest.approx(60000 / 1001)
    assert payload["audio"][0]["sample_rate"] == 48000


def test_previous_probe_version_reprobes_and_restores_rates(monkeypatch):
    info = fake_probe(monkeypatch, {"avg_frame_rate": "60/1"}, {"sample_rate": "96000"})
    mid = store.upsert_movie_by_path("native-rates/stale.mkv")
    store.upsert_media_info(mid, {**info, "probe_ver": 3, "fps": 0})
    calls = []

    def fresh(path):
        calls.append(path)
        return info

    monkeypatch.setattr(media, "probe", fresh)
    result = _media_cached_or_probe({"id": mid}, "isolated-stale.mkv")
    assert calls == ["isolated-stale.mkv"]
    assert result["fps"] == 60
    assert result["probe_ver"] == media.PROBE_VERSION
    _media_cached_or_probe({"id": mid}, "isolated-stale.mkv")
    assert len(calls) == 1


def test_schema_29_upgrade_preserves_cached_media_and_is_idempotent(tmp_path, monkeypatch):
    dbp = tmp_path / "rates-migration.db"
    monkeypatch.setattr(_base, "DB_PATH", str(dbp))
    monkeypatch.setattr(_base, "ensure_dirs", lambda: None)
    _base.init_db()
    with sqlite3.connect(dbp) as c:
        c.execute("ALTER TABLE media_info DROP COLUMN fps")
        c.execute("INSERT INTO media_info(kind,item_id,duration,probe_ver,audio_json)"
                  " VALUES('movie',5,120,3,'[{\"codec\":\"aac\"}]')")
        c.execute("PRAGMA user_version=29")
    _base.init_db()
    _base.init_db()
    with sqlite3.connect(dbp) as c:
        row = c.execute("SELECT fps,duration,probe_ver,audio_json FROM media_info").fetchone()
        assert row[:3] == (0, 120, 3)
        assert json.loads(row[3]) == [{"codec": "aac"}]
        assert c.execute("PRAGMA user_version").fetchone()[0] == _base.SCHEMA_VERSION
