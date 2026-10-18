package org.jzmedia.tv.playback

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class DolbyVisionPlaybackTest {
    private val p5 = JSONObject().put("dv_profile", 5)
    private val native = JSONObject().put("hdr_formats", JSONArray().put("dolby_vision"))

    private fun decision(method: String, copy: Any? = null): JSONObject = JSONObject().put("method", method).apply {
        if (copy != null) put("plan", JSONObject().put("vcopy", copy))
    }

    @Test fun originalVideoAndAudioOnlyConversionRemainPlayable() {
        assertNull(dolbyVisionPlaybackError(p5, native, decision("direct")))
        assertNull(dolbyVisionPlaybackError(p5, native, decision("remux", true)))
        assertNull(dolbyVisionPlaybackError(p5, native, decision("audio_transcode", true)))
    }

    @Test fun oldServerVideoConversionIsRejectedBeforeCreatingASession() {
        for (response in listOf(decision("video_transcode", false), decision("video_transcode", true),
                decision("remux"), decision("remux", "true"), decision("audio_transcode", false),
                decision("blocked"), decision("unknown"))) {
            assertNotNull(dolbyVisionPlaybackError(p5, native, response))
        }
    }

    @Test fun compatibilityModeCannotAccidentallyKeepDolbyEnabled() {
        assertNotNull(dolbyVisionPlaybackError(p5, JSONObject(), decision("direct")))
        assertNotNull(dolbyVisionPlaybackError(p5, JSONObject().put("hdr_formats", JSONArray().put("hdr10")),
            decision("direct")))
    }

    @Test fun ordinarySdrHdrAndOtherDolbyProfilesKeepTheirExistingFlow() {
        for (source in listOf(JSONObject(), JSONObject().put("hdr", "hdr10"), JSONObject().put("dv_profile", 7))) {
            assertNull(dolbyVisionPlaybackError(source, JSONObject(), decision("video_transcode", false)))
        }
    }

    @Test fun extractedP5MustCarryDolbyMimeAndValidProfileCodecs() {
        assertTrue(isDolbyVisionP5Format("video/dolby-vision", "dvhe.05.06"))
        assertTrue(isDolbyVisionP5Format("video/dolby-vision", "dvh1.05.09"))
        for ((mime, codecs) in listOf("video/hevc" to "hvc1.2.4.L153.B0",
                "video/hevc" to "dvhe.05.06", "video/dolby-vision" to null,
                "video/dolby-vision" to "dvhe.08.06", "video/dolby-vision" to "dvhe.05.00",
                "video/dolby-vision" to "dvhe.05.99")) {
            assertFalse(isDolbyVisionP5Format(mime, codecs))
        }
    }
}
