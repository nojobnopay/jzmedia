package org.jzmedia.tv.playback

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class DolbyVisionCapabilitiesTest {
    private val sourceVideo = "video/mp4; codecs=\"hvc1.2.4.L153.B0\""

    private fun source() = JSONObject()
        .put("vcodec", "hevc").put("bit_depth", 10)
        .put("dv_profile", 5).put("dv_bl_compat", 0)
        .put("width", 3840).put("height", 1604).put("fps", 24000.0 / 1001)
        .put("vcaps", JSONArray().put(sourceVideo))

    @Test fun olderServerCanProbeCroppedUhdP5UsingItsActualGeometry() {
        val result = DolbyVisionCapabilities.probe(source())!!
        assertEquals("dvhe.05.06", result.codecs)
        assertEquals(3840, result.width)
        assertEquals(1604, result.height)
        assertEquals(24000.0 / 1001, result.frameRate, 0.000001)
    }

    @Test fun levelInferenceUsesPixelRateAndWidthRatherThanHeightOrFpsAlone() {
        assertEquals(5, DolbyVisionCapabilities.probe(source().put("fps", 20))!!.level)
        assertEquals(7, DolbyVisionCapabilities.probe(source().put("height", 2160).put("fps", 30))!!.level)
        assertEquals(4, DolbyVisionCapabilities.probe(source().put("width", 2560).put("height", 1080).put("fps", 20))!!.level)
        assertNull(DolbyVisionCapabilities.probe(source().put("width", 8192)))
        assertNull(DolbyVisionCapabilities.probe(source().put("fps", 10_000)))
    }

    @Test fun explicitLevelIsRespectedAndNeverSilentlyReplaced() {
        assertEquals("dvhe.05.09", DolbyVisionCapabilities.probe(source().put("dv_level", 9))!!.codecs)
        listOf<Any>(0, 5, 14, -1, 6.5, "bad", false).forEach { invalid ->
            assertNull("dv_level=$invalid", DolbyVisionCapabilities.probe(source().put("dv_level", invalid)))
        }
    }

    @Test fun onlySingleLayerTenBitHevcProfileFiveQualifies() {
        listOf("dv_profile" to 7, "dv_profile" to 8, "vcodec" to "h264", "vcodec" to "av1",
            "bit_depth" to 8, "bit_depth" to 12, "dv_bl_compat" to 1).forEach { (key, value) ->
            assertNull("$key=$value", DolbyVisionCapabilities.probe(source().put(key, value)))
        }
        listOf("dv_profile", "vcodec", "bit_depth", "dv_bl_compat").forEach { key ->
            assertNull("missing $key", DolbyVisionCapabilities.probe(source().apply { remove(key) }))
        }
        assertNull(DolbyVisionCapabilities.probe(null))
    }

    @Test fun explicitLayerOrRpuContradictionsRejectTheSource() {
        assertNotNull(DolbyVisionCapabilities.probe(source().put("dv_rpu_present", 1)
            .put("dv_bl_present", true).put("dv_el_present", false)))
        listOf("dv_rpu_present" to false, "dv_bl_present" to 0, "dv_el_present" to 1,
            "dv_rpu_present" to "bad", "dv_el_present" to 2).forEach { (key, value) ->
            assertNull("$key=$value", DolbyVisionCapabilities.probe(source().put(key, value)))
        }
    }

    @Test fun sourceDimensionsAndRateMustBeKnownFiniteAndPositive() {
        listOf("width" to 0, "height" to -1, "width" to 3840.5,
            "fps" to 0, "fps" to "NaN", "fps" to "Infinity").forEach { (key, value) ->
            assertNull("$key=$value", DolbyVisionCapabilities.probe(source().put(key, value)))
        }
        listOf("width", "height", "fps").forEach { key ->
            assertNull("missing $key", DolbyVisionCapabilities.probe(source().apply { remove(key) }))
        }
    }

    @Test fun displaySupportAloneCannotAdvertiseP5AndTheExactSourceIsProbed() {
        var decoderQueries = 0
        assertFalse(DolbyVisionCapabilities.supportsP5(source(), false) { decoderQueries++; true })
        assertFalse(DolbyVisionCapabilities.supportsP5(source().put("dv_profile", 7), true) { decoderQueries++; true })
        assertEquals(0, decoderQueries)
        assertFalse(DolbyVisionCapabilities.supportsP5(source(), true) { false })
        assertFalse(DolbyVisionCapabilities.supportsP5(source(), true) { error("codec query failed") })
        assertTrue(DolbyVisionCapabilities.supportsP5(source(), true) {
            assertEquals("dvhe.05.06", it.codecs)
            assertEquals(3840, it.width)
            assertEquals(1604, it.height)
            assertEquals(24000.0 / 1001, it.frameRate, 0.000001)
            true
        })
    }

    @Test fun dvSourceProbesOverrideGenericHevcWithoutChangingAudioOrOtherSources() {
        val audio = "audio/mp4; codecs=\"mp4a.40.2\""
        val video = JSONObject().put("hevc10", false).put("h264", true)
        val probes = JSONObject().put(sourceVideo, false).put(audio, false)
        DolbyVisionCapabilities.applySourceVideoSupport(source(), video, probes, true)
        assertTrue(video.getBoolean("hevc10"))
        assertTrue(probes.getBoolean(sourceVideo))
        assertFalse(probes.getBoolean(audio))
        assertTrue(video.getBoolean("h264"))
        DolbyVisionCapabilities.applySourceVideoSupport(source(), video, probes, false)
        assertFalse(video.getBoolean("hevc10"))
        assertFalse(probes.getBoolean(sourceVideo))
        DolbyVisionCapabilities.applySourceVideoSupport(source().put("dv_profile", 7), video, probes, true)
        assertFalse(video.getBoolean("hevc10"))
        assertFalse(probes.getBoolean(sourceVideo))
    }

    @Test fun sourceSpecificVideoSupportDoesNotDependOnProbeStringsBeingPresent() {
        val video = JSONObject().put("hevc10", false)
        val probes = JSONObject()
        DolbyVisionCapabilities.applySourceVideoSupport(source().apply { remove("vcaps") }, video, probes, true)
        assertTrue(video.getBoolean("hevc10"))
        assertEquals(0, probes.length())
    }
}
