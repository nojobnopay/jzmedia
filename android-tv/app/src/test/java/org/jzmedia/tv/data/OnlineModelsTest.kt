package org.jzmedia.tv.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class OnlineModelsTest {
    private val source = "src_" + "1".repeat(32)
    private val item = "itm_" + "2".repeat(32)
    private val line = "ln_" + "3".repeat(32)
    private val episode = "ep_" + "4".repeat(32)
    private val sid = "s".repeat(43)
    private val movie = OnlineIdentity(source, item, "movie", lineId = line)
    private fun descriptor() = JSONObject().put("identity", movie.toJson()).put("session_id", sid)
        .put("media_url", "/api/online/sessions/$sid/media").put("transport", "mp4")
        .put("processing", "passthrough").put("seekable", true).put("duration_seconds", 24.0)
        .put("time_offset_seconds", 0.0).put("expires_at", 2_147_483_648L)

    @Test fun onlineFeatureRequiresAnExplicitServerFeature() {
        assertFalse(supportsOnlineVod(JSONObject()))
        assertFalse(supportsOnlineVod(JSONObject().put("version", "99.0.0")))
        assertTrue(supportsOnlineVod(JSONObject().put("features", JSONArray().put("online_vod"))))
        assertTrue(supportsOnlineVod(JSONObject().put("features", JSONObject().put("online_vod", true))))
        assertFalse(supportsOnlineVod(JSONObject().put("features", JSONObject().put("online_vod", "true"))))
        assertFalse(supportsOnlineVod(JSONObject().put("features", JSONArray().put("viewing_modes"))))
    }

    @Test fun onlineIdentityCannotBeConfusedWithLocalMediaOrAnotherEpisode() {
        assertEquals(movie, OnlineIdentity.fromJson(movie.toJson()))
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity("1", item, "movie") }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity(source, item, "tv", episode) }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity(source, item, "movie", episode) }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity(source, item, "episode") }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity(source, item, "movie", lineId = "https://cdn.example/file.mp4") }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity.fromJson(movie.toJson().put("media_id", 7)) }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity.fromJson(movie.toJson().put("source_id", 1)) }
    }

    @Test fun providerExternalIdentityRetainsUnicodeSpacesAndAmpersandsWithinTheServerLimit() {
        assertEquals("章节一 & 02", checkedOnlineExternal(" 章节一 & 02 "))
        assertEquals("影".repeat(512), checkedOnlineExternal("影".repeat(512)))
        val supplementary = "\uD83C\uDFAC".repeat(512)
        assertEquals(supplementary, checkedOnlineExternal(supplementary))
        for (invalid in listOf(" ", "x\n", "a\u007fb", "影".repeat(513), supplementary + "a")) {
            assertThrows(IllegalArgumentException::class.java) { checkedOnlineExternal(invalid) }
        }
    }

    @Test fun playbackDescriptorAcceptsOnlyAnOwnedPassthroughPathAndOriginalTimeline() {
        assertEquals("mp4", OnlineDescriptor.fromJson(descriptor(), movie).transport)
        assertEquals(24.0, OnlineDescriptor.fromJson(descriptor(), movie).durationSeconds!!, 0.0)
        val corruptions = listOf(
            "media_url" to "https://cdn.example/file.mp4", "media_url" to "//other.example/file.mp4",
            "media_url" to "/api/stream/sessions/$sid/media", "media_url" to "/api/online/sessions/$sid/media?token=secret",
            "processing" to "transcode", "time_offset_seconds" to 12, "time_offset_seconds" to "0",
            "seekable" to "true", "transport" to "dash", "duration_seconds" to -1,
            "duration_seconds" to "24", "expires_at" to "2147483648", "session_id" to "../other",
            "headers" to JSONObject().put("Cookie", "secret"),
        )
        corruptions.forEach { (field, value) ->
            assertThrows("must reject $field", IllegalArgumentException::class.java) {
                OnlineDescriptor.fromJson(descriptor().put(field, value), movie)
            }
        }
        assertThrows(IllegalArgumentException::class.java) {
            OnlineDescriptor.fromJson(descriptor(), movie.copy(lineId = "ln_" + "9".repeat(32)))
        }
    }

    @Test fun freshDetailNeverSkipsAnUnsupportedNextEpisodeOrChangesSourceAndLine() {
        val first = OnlineIdentity(source, item, "episode", episode, line)
        val second = first.copy(episodeId = "ep_" + "5".repeat(32))
        val third = first.copy(episodeId = "ep_" + "6".repeat(32))
        val entries = listOf(OnlineEntry("1", first, "mp4", true, ""), OnlineEntry("2", second, null, false, "unsupported"),
            OnlineEntry("3", third, "hls", true, ""))
        val detail = OnlineDetail(OnlineSource(source, "Source", "maccms"), OnlineCatalogItem(item, source, "tv", "Show"),
            "", "", listOf(OnlineLine(line, "Line", entries)))
        assertNull(detail.nextEpisode(first))
        assertEquals(third, detail.nextEpisode(second)?.identity)
        assertNull(detail.nextEpisode(third))
        assertNull(detail.nextEpisode(first.copy(lineId = "ln_" + "9".repeat(32))))
        assertNull(detail.copy(source = detail.source.copy(id = "src_" + "8".repeat(32))).nextEpisode(first))
        assertEquals(first, detail.findEntry(first)?.identity)
        assertNull(detail.findEntry(second))
    }

    @Test fun errorsUseTrustedCodesAndNeverReflectArbitraryServerText() {
        assertTrue(onlineErrorMessage(ApiException(409, "proxy password", "online_progress_writer_conflict")).contains("另一播放页"))
        assertTrue(onlineErrorMessage(ApiException(404, "server path secret")).contains("升级服务端"))
        assertTrue(onlineErrorMessage(ApiException(404, "secret", "online_identity_not_found")).contains("线路"))
        assertFalse(onlineErrorMessage(ApiException(502, "proxy user:password")).contains("password"))
        assertFalse(onlineErrorMessage(IllegalArgumentException("secret")).contains("secret"))
    }
}
