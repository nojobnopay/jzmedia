"""B5a-6：原子写（tmp+replace）与 NFO/字幕缓存不再半成品。"""
import os
import sys

import pytest
from fastapi import HTTPException

from app import nfo
from app.fsutil import atomic_write_bytes, atomic_write_text
from app.routers.stream import _run_ffmpeg_to_temp


def test_atomic_write_bytes_basic(tmp_path):
    dest = str(tmp_path / "x.bin")
    atomic_write_bytes(dest, b"abc")
    assert open(dest, "rb").read() == b"abc"
    assert not os.path.exists(dest + ".tmp")
    atomic_write_text(dest, "中文")
    assert open(dest, encoding="utf-8").read() == "中文"


def test_atomic_write_failure_keeps_old(tmp_path, monkeypatch):
    dest = str(tmp_path / "y.bin")
    atomic_write_bytes(dest, b"old")
    monkeypatch.setattr(os, "replace", lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        atomic_write_bytes(dest, b"new")
    assert open(dest, "rb").read() == b"old"
    assert not os.path.exists(dest + ".tmp")


def test_nfo_write_failure_keeps_old(tmp_path, monkeypatch):
    dest = str(tmp_path / "movie.nfo")
    nfo.write_movie_nfo({"title": "Old"}, dest)
    old = open(dest, encoding="utf-8").read()

    def _boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(nfo.os, "replace", _boom)
    with pytest.raises(OSError):
        nfo.write_movie_nfo({"title": "New"}, dest)
    assert open(dest, encoding="utf-8").read() == old
    assert not os.path.exists(dest + ".tmp")


def test_run_ffmpeg_to_temp_success_and_failure(tmp_path):
    dest = str(tmp_path / "1.vtt")
    # 用 python 代替 ffmpeg：成功写出 tmp
    _run_ffmpeg_to_temp(
        dest,
        lambda tmp: [sys.executable, "-c",
                     f"open({tmp!r},'wb').write(b'WEBVTT')"],
        timeout=30, err_msg="convert failed")
    assert open(dest, "rb").read() == b"WEBVTT"
    assert not os.path.exists(str(tmp_path / "1.tmp.vtt"))

    dest2 = str(tmp_path / "2.vtt")
    with pytest.raises(HTTPException) as e:      # 进程不产出文件 → 500
        _run_ffmpeg_to_temp(dest2, lambda tmp: [sys.executable, "-c", "pass"],
                            timeout=30, err_msg="convert failed")
    assert e.value.status_code == 500
    assert not os.path.exists(dest2)


def test_run_ffmpeg_to_temp_keeps_old_on_failure(tmp_path):
    dest = str(tmp_path / "3.vtt")
    with open(dest, "wb") as fh:
        fh.write(b"OLD")
    with pytest.raises(HTTPException):
        _run_ffmpeg_to_temp(dest, lambda tmp: [sys.executable, "-c", "pass"],
                            timeout=30, err_msg="convert failed")
    assert open(dest, "rb").read() == b"OLD"
    assert not os.path.exists(str(tmp_path / "3.tmp.vtt"))
