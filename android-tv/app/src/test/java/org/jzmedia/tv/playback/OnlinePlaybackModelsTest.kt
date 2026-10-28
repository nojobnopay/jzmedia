package org.jzmedia.tv.playback

import org.junit.Assert.*
import org.junit.Test
import org.jzmedia.tv.data.OnlineIdentity

class OnlinePlaybackModelsTest {
    private val identity = OnlineIdentity("src_" + "1".repeat(32), "itm_" + "2".repeat(32), "movie")
    @Test fun percentageAndUnknownDurationNeverStandInForANativeEndedFrame() {
        assertFalse(onlineCompletion(true, false, 599.0, 600.0))
        assertFalse(onlineCompletion(false, true, 600.0, 600.0))
        assertFalse(onlineCompletion(true, true, 0.0, 0.0))
        assertFalse(onlineCompletion(true, true, 0.0, 600.0))
        assertFalse(onlineCompletion(true, true, Double.NaN, 600.0))
        assertTrue(onlineCompletion(true, true, 600.0, 600.0))
        assertTrue(onlineCompletion(true, true, 599.9, 600.0))
    }
    @Test fun aFreshSources404IsNotHiddenByAutomaticSessionRenewal() {
        assertFalse(onlineCanRenew(404, false, 9000, 8000, 0))
        assertTrue(onlineCanRenew(404, true, 9000, 8000, 0))
        assertTrue(onlineCanRenew(410, false, 9000, 9000, 1))
        assertFalse(onlineCanRenew(410, true, 9000, 9000, 2))
        assertFalse(onlineCanRenew(403, true, 9000, 9000, 0))
        assertFalse(onlineCanRenew(500, true, 9000, 9000, 0))
    }
    @Test fun playbackInputsKeepTheExactIdentityAndAutoplayOptIn() {
        val request = OnlinePlaybackRequest(identity, "online", "movie-1", scopeToken = "a".repeat(64))
        assertEquals(identity, request.identity); assertFalse(request.autoplay)
        assertEquals(0.0, request.position, 0.0)
        assertThrows(IllegalArgumentException::class.java) { request.copy(position = Double.NaN) }
        assertThrows(IllegalArgumentException::class.java) { request.copy(position = -1.0) }
        assertThrows(IllegalArgumentException::class.java) { request.copy(scopeToken = "malformed") }
        assertEquals("", request.copy(scopeToken = "").scopeToken)
    }
    @Test fun onlineDolbyTracksRequireTheirRealMimeDisplayAndHardwareRatherThanAHevcFallback() {
        assertNull(onlineVideoTrackError("video/avc", "avc1.42C01E", true, false, false))
        assertNotNull(onlineVideoTrackError(null, null, true, true, true))
        assertNotNull(onlineVideoTrackError("video/avc", "avc1.42C01E", false, true, true))
        assertNotNull(onlineVideoTrackError("video/hevc", "dvhe.05.06", true, true, true))
        assertNotNull(onlineVideoTrackError("video/dolby-vision", "dvhe.05.06", true, false, true))
        assertNotNull(onlineVideoTrackError("video/dolby-vision", "dvhe.05.06", true, true, false))
        assertNotNull(onlineVideoTrackError("video/dolby-vision", null, true, true, true))
        assertNull(onlineVideoTrackError("video/dolby-vision", "dvhe.05.06", true, true, true))
    }
}
