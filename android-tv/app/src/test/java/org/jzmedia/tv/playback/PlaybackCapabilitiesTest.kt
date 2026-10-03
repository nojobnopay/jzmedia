package org.jzmedia.tv.playback

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class PlaybackCapabilitiesTest {
    @Test fun compatibilityKeepsStereoOutputAndRejectsUnsupportedSourceAudio() {
        val sourceVideo = "video/mp4; codecs=\"hvc1.2.4.L153.B0\""
        val sourceAudio = "audio/mp4; codecs=\"mp4a.40.2\""
        val original = JSONObject()
            .put("video", JSONObject().put("h264", true).put("hevc10", true))
            .put("audio", JSONObject().put("aac", true))
            .put("probes", JSONObject().put(sourceVideo, true).put(sourceAudio, false))
            .put("hdr_formats", JSONArray().put("hdr10"))
        val fallback = compatibleCapabilities(original, JSONObject().put("vcaps", JSONArray().put(sourceVideo)))
        assertTrue(fallback.getJSONObject("video").getBoolean("h264"))
        assertTrue(fallback.getJSONObject("audio").getBoolean("aac"))
        assertFalse(fallback.getJSONObject("video").getBoolean("hevc10"))
        assertFalse(fallback.getJSONObject("probes").getBoolean(sourceVideo))
        assertFalse(fallback.getJSONObject("probes").getBoolean(sourceAudio))
        assertEquals(0, fallback.getJSONArray("hdr_formats").length())
        assertTrue(original.getJSONObject("probes").getBoolean(sourceVideo))
    }
}
