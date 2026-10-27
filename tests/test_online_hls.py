"""Pure VOD manifest plans; the callback never resolves DNS or opens sockets."""

import pytest

from app.online import hls


BASE = "https://source.example/redirected/path/child.m3u8?signed=private-secret"


class Registrar:
    def __init__(self):
        self.calls = []

    def __call__(self, url, *, kind, byte_range):
        self.calls.append((url, kind, byte_range))
        return f"/api/online/sessions/opaque-session/resources/r{len(self.calls)}"


def media(*lines):
    return ("\n".join(["#EXTM3U", "#EXT-X-TARGETDURATION:6", *lines, "#EXT-X-ENDLIST"]) + "\n").encode()


def rewrite(body, *, base=BASE):
    registrar = Registrar()
    return hls.rewrite_manifest(body, base, registrar).decode(), registrar.calls


def test_media_plan_rewrites_from_final_response_url_and_retains_original_timeline():
    output, calls = rewrite(media("#EXT-X-VERSION:6", "#EXT-X-MEDIA-SEQUENCE:15",
                                  "#EXT-X-PLAYLIST-TYPE:VOD", "#EXTINF:5.125,original title",
                                  "../segment.ts?sig=segment-secret", "#EXT-X-DISCONTINUITY",
                                  "#EXTINF:1.75,", "https://cdn.example/tail.ts?token=tail-secret"))
    assert calls == [("https://source.example/redirected/segment.ts?sig=segment-secret", "segment", None),
                     ("https://cdn.example/tail.ts?token=tail-secret", "segment", None)]
    assert "#EXTINF:5.125," in output and "#EXTINF:1.75," in output
    assert "#EXT-X-MEDIA-SEQUENCE:15" in output and "#EXT-X-DISCONTINUITY" in output
    assert "#EXT-X-ENDLIST" in output
    assert not any(secret in output for secret in ("segment-secret", "tail-secret", "private-secret"))
    assert "https://" not in output


def test_master_rewrites_quoted_uri_attributes_audio_subtitle_and_iframe_playlists():
    body = b'''#EXTM3U
#EXT-X-VERSION:7
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English, stereo",DEFAULT=YES,AUTOSELECT=YES,URI="audio/list.m3u8?x=1,y=2"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Chinese",LANGUAGE="zh",URI="//text.example/subs.m3u8"
#EXT-X-MEDIA:TYPE=CLOSED-CAPTIONS,GROUP-ID="cc",NAME="CC1",INSTREAM-ID="CC1"
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=20000,URI="iframes.m3u8",CODECS="avc1.42E01E"
#EXT-X-STREAM-INF:BANDWIDTH=1000000,AVERAGE-BANDWIDTH=900000,CODECS="avc1.42E01E,mp4a.40.2",AUDIO="audio",SUBTITLES="subs",CLOSED-CAPTIONS=NONE,RESOLUTION=1280x720
video/main.m3u8
'''
    output, calls = rewrite(body)
    assert [call[1] for call in calls] == ["manifest"] * 4
    assert [call[0] for call in calls] == [
        "https://source.example/redirected/path/audio/list.m3u8?x=1,y=2",
        "https://text.example/subs.m3u8",
        "https://source.example/redirected/path/iframes.m3u8",
        "https://source.example/redirected/path/video/main.m3u8",
    ]
    assert 'NAME="English, stereo"' in output
    assert 'CODECS="avc1.42E01E,mp4a.40.2"' in output
    assert 'URI="/api/online/sessions/opaque-session/resources/r1"' in output
    assert "audio/list.m3u8" not in output and "text.example" not in output


def test_map_and_segment_byte_ranges_are_explicit_and_registered_as_inclusive_pairs():
    output, calls = rewrite(media(
        '#EXT-X-MAP:URI="all.mp4",BYTERANGE="10@0"',
        "#EXTINF:6,", "#EXT-X-BYTERANGE:100@10", "all.mp4",
        '#EXT-X-MAP:URI="all.mp4",BYTERANGE="5"',
        "#EXTINF:6,", "#EXT-X-BYTERANGE:50", "all.mp4",
    ))
    assert [call[1:] for call in calls] == [("map", (0, 9)), ("segment", (10, 109)),
                                           ("map", (10, 14)), ("segment", (110, 159))]
    assert '#EXT-X-MAP:URI="/api/online/sessions/opaque-session/resources/r3",BYTERANGE="5@10"' in output
    assert "#EXT-X-BYTERANGE:50@110" in output


def test_none_method_is_unencrypted_and_comments_are_not_exposed():
    output, calls = rewrite(media("# comment https://upstream.example/?secret=private-secret",
                                  "#EXT-X-KEY:METHOD=NONE", "#EXT-X-START:TIME-OFFSET=-1.5,PRECISE=YES",
                                  "#EXTINF:6,", "one.ts"))
    assert len(calls) == 1
    assert "#EXT-X-KEY:METHOD=NONE" in output
    assert "#EXT-X-START:TIME-OFFSET=-1.5,PRECISE=YES" in output
    assert "private-secret" not in output


@pytest.mark.parametrize("range_line,previous", [
    ("#EXT-X-BYTERANGE:10", []),
    ("#EXT-X-BYTERANGE:10", ["#EXTINF:6,", "#EXT-X-BYTERANGE:10@0", "different.ts"]),
    ("#EXT-X-BYTERANGE:10", ["#EXTINF:6,", "#EXT-X-BYTERANGE:10@0", "one.ts", "#EXTINF:6,", "one.ts"]),
    ("#EXT-X-BYTERANGE:0@0", []),
    ("#EXT-X-BYTERANGE:-1@0", []),
    ("#EXT-X-BYTERANGE:2@9223372036854775807", []),
])
def test_undefined_or_invalid_byte_ranges_are_rejected_before_registration(range_line, previous):
    registrar = Registrar()
    with pytest.raises(hls.ManifestError):
        hls.rewrite_manifest(media(*previous, "#EXTINF:6,", range_line, "one.ts"), BASE, registrar)
    assert registrar.calls == []


@pytest.mark.parametrize("uri", [
    "file:///etc/passwd", "data:application/octet-stream,secrets", "ftp://source.example/file",
    "https://user:private-secret@source.example/file", "https://source.example/a#frag",
    "https://%73ource.example/file", "segment name.ts", "segment%0d%0aevil.ts", "segment%5cname.ts",
    "segment\\name.ts", "{$variable}.ts", "https://source.example:0/a", "https://source.example:65536/a",
])
def test_unsafe_uri_forms_fail_before_the_registrar_receives_any_reference(uri):
    registrar = Registrar()
    with pytest.raises(hls.ManifestError) as caught:
        hls.rewrite_manifest(media("#EXTINF:6,", "safe.ts", "#EXTINF:6,", uri), BASE, registrar)
    assert registrar.calls == []
    assert "private-secret" not in str(caught.value)


@pytest.mark.parametrize("tag", [
    '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"',
    '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key"',
    '#EXT-X-KEY:METHOD=NONE,URI="key.bin"',
    '#EXT-X-SESSION-KEY:METHOD=AES-128,URI="key.bin"',
    '#EXT-X-SESSION-DATA:DATA-ID="example",URI="data.json"',
    '#EXT-X-CONTENT-STEERING:SERVER-URI="steering.json"',
    '#EXT-X-DEFINE:NAME="token",VALUE="secret"',
    '#EXT-X-PART:DURATION=1,URI="part.m4s"',
    '#EXT-X-PART-INF:PART-TARGET=1',
    '#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES',
    '#EXT-X-RENDITION-REPORT:URI="elsewhere.m3u8"',
    '#EXT-X-PRELOAD-HINT:TYPE=PART,URI="future.m4s"',
    '#EXT-X-SKIP:SKIPPED-SEGMENTS=2',
    '#EXT-X-FUTURE:URI="future.bin"',
])
def test_unsupported_or_network_bearing_extensions_fail_closed(tag):
    registrar = Registrar()
    with pytest.raises(hls.ManifestError):
        hls.rewrite_manifest(media("#EXTINF:6,", "one.ts", tag), BASE, registrar)
    assert registrar.calls == []


@pytest.mark.parametrize("attributes", [
    'TYPE=AUDIO,GROUP-ID="a",NAME="n",URI="one.m3u8",URI="two.m3u8"',
    'TYPE=AUDIO,GROUP-ID="a",NAME="n",URI=one.m3u8',
    'TYPE=AUDIO,GROUP-ID="a",NAME="unterminated,URI="one.m3u8"',
    'TYPE=AUDIO,GROUP-ID="a",NAME="n",URI="one.m3u8"trailing',
    'TYPE=AUDIO,GROUP-ID="a",NAME="n",URI="one.m3u8",',
    'TYPE=AUDIO,GROUP-ID="a",NAME="n", URI="one.m3u8"',
    'TYPE=AUDIO,GROUP-ID="a",NAME="n",URI="one.m3u8",FUTURE-URI="other"',
    'TYPE=SUBTITLES,GROUP-ID="a",NAME="n"',
    'TYPE=CLOSED-CAPTIONS,GROUP-ID="a",NAME="n",URI="one.m3u8"',
])
def test_attribute_grammar_and_rendition_roles_are_validated_before_registration(attributes):
    body = (f'#EXTM3U\n#EXT-X-MEDIA:{attributes}\n#EXT-X-STREAM-INF:BANDWIDTH=100\none.m3u8\n').encode()
    registrar = Registrar()
    with pytest.raises(hls.ManifestError):
        hls.rewrite_manifest(body, BASE, registrar)
    assert registrar.calls == []


@pytest.mark.parametrize("body", [
    b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\none.ts\n",  # Live.
    b"#EXTM3U\n#EXT-X-PLAYLIST-TYPE:EVENT\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\none.ts\n#EXT-X-ENDLIST\n",
    b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\n",  # Missing variant URI.
    b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\none.m3u8\n#EXTINF:6,\none.ts\n#EXT-X-ENDLIST\n",
    b"#EXTM3U\n#EXT-X-TARGETDURATION:6\none.ts\n#EXT-X-ENDLIST\n",  # Missing duration.
    b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:NaN,\none.ts\n#EXT-X-ENDLIST\n",
    b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:0,\none.ts\n#EXT-X-ENDLIST\n",
    b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6\none.ts\n#EXT-X-ENDLIST\n",
    b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\n#EXT-X-ENDLIST\n",  # Dangling duration.
    b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-ENDLIST\n",  # Empty media playlist.
    b"#EXTM3U\n#EXT-X-ENDLIST\n#EXTINF:6,\none.ts\n",  # Entries after end.
    b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\none.ts\n#EXT-X-ENDLIST\n",
    b"#EXTM3U\n#EXTM3U\n",
    b"#EXTM3U trailing\n",
    b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\none.ts\x00\n#EXT-X-ENDLIST\n",
    b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\n one.ts\n#EXT-X-ENDLIST\n",
    b"\xff#EXTM3U\n",
])
def test_incomplete_mixed_live_or_malformed_manifests_have_no_registration_side_effects(body):
    registrar = Registrar()
    with pytest.raises(hls.ManifestError):
        hls.rewrite_manifest(body, BASE, registrar)
    assert registrar.calls == []


def test_bom_and_crlf_are_normalized_without_changing_media_time():
    body = b"\xef\xbb\xbf#EXTM3U\r\n#EXT-X-TARGETDURATION:6\r\n#EXTINF:2.125,\r\nseg.ts\r\n#EXT-X-ENDLIST\r\n"
    output, calls = rewrite(body)
    assert output.startswith("#EXTM3U\n") and "\r" not in output
    assert "#EXTINF:2.125," in output and len(calls) == 1


@pytest.mark.parametrize("body", [None, bytearray(b"#EXTM3U"), b"", b"x" * (hls.MANIFEST_BYTES + 1)])
def test_input_memory_budget_and_type_are_enforced(body):
    registrar = Registrar()
    with pytest.raises(hls.ManifestError, match="^hls_manifest_size_limit$"):
        hls.rewrite_manifest(body, BASE, registrar)
    assert registrar.calls == []


def test_reference_limit_rejects_before_registration(monkeypatch):
    monkeypatch.setattr(hls, "REFERENCE_LIMIT", 2)
    registrar = Registrar()
    with pytest.raises(hls.ManifestError, match="^hls_reference_limit$"):
        hls.rewrite_manifest(media("#EXTINF:6,", "one.ts", "#EXTINF:6,", "two.ts", "#EXTINF:6,", "three.ts"),
                             BASE, registrar)
    assert registrar.calls == []


def test_line_count_and_output_budgets_are_enforced(monkeypatch):
    with pytest.raises(hls.ManifestError, match="^invalid_hls_lines$"):
        rewrite(b"#EXTM3U\n" + b"\n" * hls.LINE_LIMIT)
    monkeypatch.setattr(hls, "OUTPUT_BYTES", 40)
    with pytest.raises(hls.ManifestError, match="^hls_output_size_limit$"):
        rewrite(media("#EXTINF:6,", "one.ts"))


@pytest.mark.parametrize("path", ["https://upstream.example/secret", "//upstream.example/secret",
                                 "relative/path", "/safe/../unsafe", "/safe?token=secret",
                                 '/safe/"broken', "/safe\r\nheader", None])
def test_registrar_must_return_an_opaque_absolute_same_origin_path(path):
    with pytest.raises(hls.ManifestError, match="^invalid_hls_resource_path$"):
        hls.rewrite_manifest(media("#EXTINF:6,", "one.ts"), BASE, lambda *_args, **_kwargs: path)


def test_registrar_policy_failure_is_preserved_for_gateway_revocation():
    class RejectedTarget(RuntimeError):
        pass

    def reject(*_args, **_kwargs):
        raise RejectedTarget("fixed_target_error")

    with pytest.raises(RejectedTarget, match="^fixed_target_error$"):
        hls.rewrite_manifest(media("#EXTINF:6,", "one.ts"), BASE, reject)
