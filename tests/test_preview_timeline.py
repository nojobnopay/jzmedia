"""预览视频时长：隔离探测覆盖尾部空帧、非零时间原点、远程限制及取消。"""
import json
import subprocess
from types import SimpleNamespace

import pytest

from app import media
from app.routers.stream import preview_timeline as timeline
from app.routers.stream.preview_log import ExtractionError


def source(path="/isolated/movie.mkv"):
    return SimpleNamespace(input=str(path))


def metadata(*, duration=None, tags=None, start="0", origin="0", index=0):
    return {"format": {"start_time": origin}, "streams": [
        {"index": index, "codec_type": "video", "codec_name": "h264", "start_time": start,
         "duration": duration, "avg_frame_rate": "24/1", "tags": tags or {}}]}


def resolver(responses):
    calls = []
    def run(cmd, jid, timeout):
        calls.append((cmd, jid, timeout))
        response = responses[len(calls) - 1]
        if isinstance(response, Exception):
            raise response
        return json.dumps(response).encode()
    return run, calls


@pytest.mark.parametrize("reported", ["90", 90])
def test_video_stream_duration_is_separate_from_playback_duration(reported):
    run, calls = resolver([metadata(duration=reported)])
    info = {"duration": 120}
    result = timeline.resolve_timeline(source(), info, run_probe=run, jid="job")
    assert result == {"duration": 90, "video_stream_index": 0, "source": "stream_duration"}
    assert info == {"duration": 120}
    assert len(calls) == 1


def test_duration_tag_and_attached_cover_selection():
    data = metadata(tags={"DURATION": "01:49:30.564000000", "NUMBER_OF_FRAMES": "157536"}, index=2)
    data["streams"][0]["avg_frame_rate"] = "24000/1001"
    data["streams"].insert(0, {"index": 0, "codec_type": "video", "codec_name": "mjpeg",
                               "duration": "9999", "disposition": {"attached_pic": 1}})
    run, calls = resolver([data])
    result = timeline.resolve_timeline(source(), {"duration": 6596.347}, run_probe=run, jid="j")
    assert result["duration"] == pytest.approx(6570.564)
    assert result["video_stream_index"] == 2
    assert result["source"] == "video_tag"
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [("duration", "3"), ("tags", {"DURATION": "00:00:08.000"})])
def test_nonzero_source_origin_is_subtracted_once(field, value):
    data = metadata(start="5", origin="5", **{field: value})
    run, _calls = resolver([data])
    assert timeline.resolve_timeline(source(), {"duration": 8}, run_probe=run, jid="j")["duration"] == 3


@pytest.mark.parametrize("tags", [{}, {"DURATION": "00:00:03.000"},
                                  {"DURATION": "00:16:00.000", "NUMBER_OF_FRAMES": "24"},
                                  {"DURATION": "nan"}])
def test_missing_or_untrustworthy_tags_use_only_the_bounded_video_tail(tags):
    run, calls = resolver([metadata(tags=tags), {"packets": [
        {"pts_time": "699", "duration_time": "0.04"},
        {"pts_time": "899.96", "duration_time": "0.04"},
        {"pts_time": "899.92", "duration_time": "0.04"}]}])
    result = timeline.resolve_timeline(source(), {"duration": 1000}, run_probe=run, jid="j")
    assert result["duration"] == pytest.approx(900)
    assert result["source"] == "tail_packets"
    tail_cmd, jid, timeout = calls[1]
    assert tail_cmd[tail_cmd.index("-read_intervals") + 1] == "700.000000%1001.000000"
    assert tail_cmd[tail_cmd.index("-select_streams") + 1] == "0"
    assert "-show_frames" not in tail_cmd and timeout <= 30 and jid == "j"


def test_tail_timestamps_use_source_origin_and_frame_duration_when_packets_omit_duration():
    run, calls = resolver([metadata(origin="5", start="5", index=2), {"packets": [
        {"pts_time": "899.9"}, {"pts_time": "900"}, {"pts_time": "nan"}]}])
    result = timeline.resolve_timeline(source(), {"duration": 1000}, run_probe=run, jid="j")
    assert result["duration"] == pytest.approx(895 + 1 / 24)
    cmd = calls[1][0]
    assert cmd[cmd.index("-read_intervals") + 1] == "705.000000%1006.000000"
    assert cmd[cmd.index("-select_streams") + 1] == "2"


@pytest.mark.parametrize("packets", [[], [{"pts_time": "100", "duration_time": "0.04"}],
                                     [{"pts_time": "nan"}], [{"pts_time": "10000"}]])
def test_missing_or_early_tail_does_not_shorten_video_to_an_old_keyframe(packets):
    run, _calls = resolver([metadata(), {"packets": packets}])
    result = timeline.resolve_timeline(source(), {"duration": 1000}, run_probe=run, jid="j")
    assert result["duration"] == 1000 and result["source"] == "container"


def test_remote_probe_reuses_http_input_limits(monkeypatch):
    monkeypatch.setattr(media, "probe_limit_args", lambda path: ["-rw_timeout", "1234"])
    run, calls = resolver([metadata(), {"packets": []}])
    timeline.resolve_timeline(source("https://isolated.invalid/media"), {"duration": 1000}, run_probe=run, jid="j")
    assert all(cmd[cmd.index("-rw_timeout") + 1] == "1234" for cmd, _, _ in calls)


@pytest.mark.parametrize("failure", [ExtractionError("probe failed", reason="timeout", timed_out=True),
                                     OSError("unavailable")])
def test_probe_failure_is_bounded_and_preserves_the_playback_duration(failure):
    run, calls = resolver([metadata(), failure])
    result = timeline.resolve_timeline(source(), {"duration": 1000}, run_probe=run, jid="j")
    assert result["duration"] == 1000 and len(calls) == 2


@pytest.mark.parametrize("phase", [0, 1])
def test_cancellation_and_playback_yield_propagate(phase):
    class YieldToPlayback(Exception):
        pass
    run, _calls = resolver(([metadata()] if phase else []) + [YieldToPlayback()])
    with pytest.raises(YieldToPlayback):
        timeline.resolve_timeline(source(), {"duration": 1000}, run_probe=run, jid="j")


def test_real_short_video_with_long_audio_and_nonzero_origin(tmp_path):
    bins = media._static_bins_if_present()
    ffmpeg = bins.get("ffmpeg")
    ffprobe = bins.get("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("isolated ffmpeg/ffprobe unavailable")
    path = tmp_path / "audio-tail.mkv"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-f", "lavfi", "-i", "color=red:s=64x64:r=10:d=3",
                    "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=8",
                    "-c:v", "libx264", "-threads", "1", "-c:a", "pcm_s16le",
                    "-output_ts_offset", "5", str(path)], capture_output=True, check=True, timeout=20)
    def run(cmd, _jid, timeout):
        cmd[0] = ffprobe
        return subprocess.run(cmd, capture_output=True, check=True, timeout=timeout).stdout
    result = timeline.resolve_timeline(source(path), {"duration": 13}, run_probe=run, jid="isolated")
    assert result["duration"] == pytest.approx(3)
    assert result["source"] == "video_tag"


@pytest.mark.parametrize("origin", [0, 5])
def test_real_bounded_tail_recovers_missing_duration_tags_after_195_seconds_of_audio(tmp_path, origin):
    bins = media._static_bins_if_present()
    ffmpeg, ffprobe = bins.get("ffmpeg"), bins.get("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("isolated ffmpeg/ffprobe unavailable")
    path = tmp_path / "long-audio-tail.mkv"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-f", "lavfi", "-i", "color=red:s=64x64:r=1:d=205",
                    "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=400",
                    "-c:v", "libx264", "-threads", "1", "-c:a", "pcm_s16le",
                    "-output_ts_offset", str(origin), str(path)],
                   capture_output=True, check=True, timeout=20)
    calls = []
    def run(cmd, _jid, timeout):
        cmd[0] = ffprobe
        calls.append(cmd)
        output = subprocess.run(cmd, capture_output=True, check=True, timeout=timeout).stdout
        if len(calls) == 1:
            # 模拟没有视频统计标签的合法 MKV；后续尾包探测访问真实隔离片源。
            data = json.loads(output)
            for stream in data["streams"]:
                stream.pop("tags", None)
            return json.dumps(data)
        return output
    result = timeline.resolve_timeline(source(path), {"duration": 400 + origin}, run_probe=run, jid="isolated")
    assert result["duration"] == pytest.approx(205)
    assert result["source"] == "tail_packets" and len(calls) == 2
    interval = calls[1][calls[1].index("-read_intervals") + 1]
    low, high = map(float, interval.split("%"))
    assert high - low <= 301
