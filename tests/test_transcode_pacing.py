"""HLS pacing must preserve input scoping, startup preroll and fast copy/MP4 paths."""
import pytest

from app.playback import build_cmd


def _plan(seg="fmp4", **values):
    return {"seg": seg, "vcopy": False, "height": 720, "audios": [], **values}


@pytest.mark.parametrize("seg", ["fmp4", "ts"])
@pytest.mark.parametrize("start,preroll", [(0, 0), (5, 5), (60, 15)])
def test_pacing_scopes_to_main_input_and_allows_startup_burst(monkeypatch, seg, start, preroll):
    monkeypatch.setenv("TRANSCODE_READRATE", "2.5")
    cmd = build_cmd("/tmp/movie.mkv", _plan(seg), start=start, force_sw=True)
    assert cmd[cmd.index("-readrate") + 1] == "2.5"
    burst = float(cmd[cmd.index("-readrate_initial_burst") + 1])
    assert burst == preroll + (8 if seg == "fmp4" else 12)
    assert cmd.index("-readrate") < cmd.index("-i")
    assert cmd.count("-readrate") == 1


@pytest.mark.parametrize("value", [None, "0", "-1", "0.5", "bad", "nan", "inf"])
def test_disabled_or_invalid_pacing_keeps_existing_command(monkeypatch, value):
    monkeypatch.delenv("TRANSCODE_READRATE", raising=False)
    baseline = build_cmd("/tmp/movie.mkv", _plan(), force_sw=True)
    if value is not None:
        monkeypatch.setenv("TRANSCODE_READRATE", value)
    assert build_cmd("/tmp/movie.mkv", _plan(), force_sw=True) == baseline


@pytest.mark.parametrize("plan", [_plan(vcopy=True), _plan("ts", vcopy=True), _plan("mp4")])
def test_copy_and_finite_mp4_are_not_throttled(monkeypatch, plan):
    monkeypatch.setenv("TRANSCODE_READRATE", "2")
    assert "-readrate" not in build_cmd("/tmp/movie.mkv", plan, force_sw=True)


@pytest.mark.parametrize("seg", ["fmp4", "ts"])
def test_subtitle_input_does_not_inherit_main_input_pacing(monkeypatch, seg):
    monkeypatch.setenv("TRANSCODE_READRATE", "2")
    cmd = build_cmd("/tmp/movie.mkv", _plan(seg, vcopy=True, sub="burn",
                    sub_sidecar="movie.idx", sub_sidecar_input="/tmp/movie.idx"),
                    start=60, force_sw=True)
    inputs = [i for i, value in enumerate(cmd) if value == "-i"]
    assert len(inputs) == 2
    assert cmd.index("-readrate") < inputs[0]
    assert "-readrate" not in cmd[inputs[0] + 2:inputs[1]]
    assert cmd[cmd.index("-readrate_initial_burst") + 1] == ("23" if seg == "fmp4" else "27")
