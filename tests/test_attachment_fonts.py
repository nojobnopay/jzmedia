"""内嵌字体附件提取（未麻的部屋事故：ffmpeg≥7 拒绝中文/括号附件名做 dump 落盘名，
旧 `-dump_attachment:t ""` 整批路径零产出 → 字体接口全 404 → ASS 空白）。"""
import subprocess
import threading
import time

import pytest

from app import media as _media
from app.routers.stream import subtitles as subs


ATT_NAME = "方正测试[0]_CHECK_0.ttf"
PAYLOAD = b"FAKE-FONT-BYTES-1234567890"


def _ffmpeg_bin():
    if not subs._ffmpeg_ok():
        pytest.skip("ffmpeg not available")
    return _media.ffmpeg_bin()


@pytest.fixture()
def mkv_with_attachment(tmp_path):
    ff = _ffmpeg_bin()
    src = tmp_path / "payload.bin"
    src.write_bytes(PAYLOAD)
    out = tmp_path / "craft.mkv"
    r = subprocess.run(
        [ff, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=1",
         "-c:v", "mpeg4", "-attach", str(src),
         "-metadata:s:t:0", f"filename={ATT_NAME}",
         "-metadata:s:t:0", "mimetype=application/x-truetype-font",
         str(out)], capture_output=True, timeout=120)
    assert r.returncode == 0 and out.is_file()
    info = _media.probe(str(out))
    assert info["playable"]
    atts = [a for a in info["attachments"] if a["name"] == ATT_NAME]
    assert len(atts) == 1
    return out, info


def test_dump_explicit_stream_names(mkv_with_attachment, tmp_path):
    """事故回归：逐流显式输出 → 中文附件名原样落盘、字节一致、写标记。"""
    out, info = mkv_with_attachment
    fdir = tmp_path / "fonts"
    fdir.mkdir()
    subs._dump_attachments(str(out), str(fdir), info["attachments"])
    dest = fdir / ATT_NAME
    assert dest.is_file() and dest.read_bytes() == PAYLOAD
    assert (fdir / ".dumped").is_file()


def test_dump_marker_short_circuits(mkv_with_attachment, tmp_path, monkeypatch):
    """已有 .dumped 标记不再跑 ffmpeg。"""
    out, info = mkv_with_attachment
    fdir = tmp_path / "fonts"
    fdir.mkdir()
    (fdir / ".dumped").write_text("1")

    def _boom(*a, **k):
        raise AssertionError("ffmpeg must not run when marker exists")

    monkeypatch.setattr(subs.subprocess, "run", _boom)
    subs._dump_attachments(str(out), str(fdir), info["attachments"])


def test_dump_argv_explicit_outputs(tmp_path, monkeypatch):
    """argv 结构：-dump_attachment:<序号> <临时文件> 成对出现且位于 -i 之前；
    非字体附件不进 dump；有产出即写标记（新版 ffmpeg 尾部 quirk 非零不阻塞）。"""
    captured = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        for i, a in enumerate(argv):
            if a.startswith("-dump_attachment:"):
                with open(argv[i + 1], "wb") as fh:
                    fh.write(b"F")

        class _P:
            returncode = 1

        return _P()

    monkeypatch.setattr(subs.subprocess, "run", _fake_run)
    fdir = tmp_path / "fonts"
    fdir.mkdir()
    atts = [{"index": 8, "name": "a[0]_X_0.ttf", "mime": "application/x-truetype-font"},
            {"index": 9, "name": "notes.txt", "mime": "text/plain"}]
    subs._dump_attachments("/nonexistent/in.mkv", str(fdir), atts)
    argv = captured["argv"]
    i = argv.index("-dump_attachment:8")
    assert argv[i + 1].endswith("att8.ttf")
    assert argv.index("-i") > i
    assert not any(a == "-dump_attachment:9" for a in argv)
    assert (fdir / "a[0]_X_0.ttf").is_file()
    assert (fdir / ".dumped").is_file()


def test_dump_survives_unsafe_rejection(tmp_path, monkeypatch):
    """模拟 ffmpeg≥7：整批按内嵌名落盘被拒（rc=234 零产出），
    逐流显式输出仍可用 → 有附件清单时必须走新路径成功。"""
    def _fake_run(argv, **kwargs):
        if "-dump_attachment:t" in argv:
            class _P1:
                returncode = 234

            return _P1()
        for i, a in enumerate(argv):
            if a.startswith("-dump_attachment:"):
                with open(argv[i + 1], "wb") as fh:
                    fh.write(b"F")

        class _P2:
            returncode = 1

        return _P2()

    monkeypatch.setattr(subs.subprocess, "run", _fake_run)
    fdir = tmp_path / "fonts"
    fdir.mkdir()
    atts = [{"index": 8, "name": ATT_NAME, "mime": "application/x-truetype-font"}]
    subs._dump_attachments("/in.mkv", str(fdir), atts)
    assert (fdir / ATT_NAME).is_file()
    assert (fdir / ".dumped").is_file()


def test_dump_lock_serializes(tmp_path, monkeypatch):
    """同目录并发 dump 只跑一次 ffmpeg（JASSUB 一次取几十个字体防 I/O 风暴）。"""
    calls = []

    def _slow_run(argv, **kwargs):
        calls.append(1)
        time.sleep(0.3)

        class _P:
            returncode = 0

        return _P()

    monkeypatch.setattr(subs.subprocess, "run", _slow_run)
    fdir = tmp_path / "fonts"
    fdir.mkdir()
    atts = [{"index": 3, "name": "x.ttf", "mime": "application/x-truetype-font"}]
    ts = [threading.Thread(target=subs._dump_attachments,
                           args=("/in.mkv", str(fdir), atts)) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(calls) == 1
