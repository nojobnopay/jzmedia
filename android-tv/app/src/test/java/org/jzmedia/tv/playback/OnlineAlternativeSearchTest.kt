package org.jzmedia.tv.playback

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.OnlineApi
import org.jzmedia.tv.data.OnlineIdentity

class OnlineAlternativeSearchTest {
    private val original = OnlineIdentity("src_" + "a".repeat(32), "itm_" + "1".repeat(32), "movie", lineId = "ln_" + "1".repeat(32))
    private val first = original.copy(itemId = "itm_" + "2".repeat(32), lineId = "ln_" + "2".repeat(32))
    private val better = first.copy(lineId = "ln_" + "3".repeat(32))
    private val scopeToken = "e".repeat(64)
    private val task = "alt_" + "c".repeat(32)
    private val session = "n".repeat(42)
    private val token = "t".repeat(32)
    private fun row(id: OnlineIdentity, speed: Int) = JSONObject().put("identity", id.toJson()).put("external_id", "show")
        .put("source_name", "来源").put("line_label", id.lineId).put("title", "合成影片").put("entry_label", "正片")
        .put("throughput_kbps", speed).put("recommendation_token", token)
    private fun step(status: String, wait: Int = 0, vararg rows: JSONObject) = MockResponse().setBody(JSONObject()
        .put("task_id", task).put("status", status).put("items", JSONArray(rows.toList())).put("partial", status == "partial")
        .put("retry_after_ms", wait).toString())
    private fun stateResponse() = MockResponse().setBody(JSONObject().put("item_id", first.itemId).put("favorite", false)
        .put("available", true).put("reason", "").put("generation", 1).put("scope_token", scopeToken).put("selected_progress",
            JSONObject().put("identity", first.toJson()).put("position", 11.0).put("duration", 80.0)
                .put("paused", true).put("completed", false).put("updated_at", 3).put("writer_revision", 2)).toString())
    private fun descriptor() = MockResponse().setBody(JSONObject().put("identity", first.toJson()).put("session_id", session)
        .put("media_url", "/api/online/sessions/$session/media").put("transport", "mp4").put("processing", "passthrough")
        .put("seekable", true).put("duration_seconds", 80.0).put("time_offset_seconds", 0).put("expires_at", 2345678900L).toString())
    private fun handshake() = MockResponse().setBody("""{"protocol_version":1,"features":["online_alternatives_v2","online_vod"]}""")

    @Test fun bufferingAfterASeekErrorsPauseAndCacheHaveDistinctProbeLifecycles() {
        val playing = OnlinePlaybackState(networkPlayback = true, loading = false, firstFrame = true)
        assertEquals(AlternativeTrigger.Inactive, alternativeTrigger(playing, true))
        assertEquals(AlternativeTrigger.Waiting, alternativeTrigger(playing.copy(loading = true), true))
        assertEquals(AlternativeTrigger.Waiting, alternativeTrigger(playing.copy(firstFrame = false), true))
        assertEquals(AlternativeTrigger.Waiting, alternativeTrigger(playing.copy(awaitingSeek = true), true))
        assertEquals(AlternativeTrigger.Inactive, alternativeTrigger(playing.copy(awaitingSeek = true, playWhenReady = false), true))
        assertEquals(AlternativeTrigger.Inactive, alternativeTrigger(playing.copy(playWhenReady = false, loading = true), true))
        assertEquals(AlternativeTrigger.Error, alternativeTrigger(playing.copy(error = "网络错误", playWhenReady = false), true))
        assertEquals(AlternativeTrigger.Inactive, alternativeTrigger(playing.copy(networkPlayback = false, error = "缓存错误"), true))
        assertEquals(AlternativeTrigger.Inactive, alternativeTrigger(playing.copy(loading = true), false))
    }

    @Test fun firstResultIsAvailableBeforeComparisonEndsAndASelectionPreparesExactlyOneSession() = runBlocking {
        MockWebServer().use { server ->
            var steps = 0
            var starts = 0
            val firstProgress = java.util.concurrent.CountDownLatch(1)
            var progressReads = 0
            var switchedSessions = 0
            var rejected = true
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val path = request.requestUrl!!.encodedPath
                    return when {
                        path.endsWith("client-info") || path.endsWith("client-check") -> handshake()
                        path.endsWith("alternative-tasks") -> if (starts++ == 0) MockResponse().setResponseCode(429) else step("queued", 250)
                        path.endsWith("advance") -> if (steps++ == 0) step("running", 400, row(first, 2000))
                            else step("done", 0, row(better, 4000), row(first, 2000))
                        path.endsWith(task) && request.method == "DELETE" -> MockResponse().setResponseCode(204)
                        path.contains("/state/items/") -> { progressReads++; firstProgress.countDown(); stateResponse() }
                        path.endsWith("sessions") -> {
                            switchedSessions++
                            assertEquals(token, request.getHeader("X-Online-Recommendation"))
                            assertEquals("cache", request.getHeader("X-Online-Playback-Context"))
                            if (rejected) {
                                // Both parallel reads are now in flight when the selected source changes.
                                check(firstProgress.await(2, TimeUnit.SECONDS))
                                MockResponse().setResponseCode(409).setBody("""{"code":"online_source_changed"}""")
                            }
                            else descriptor()
                        }
                        else -> MockResponse().setResponseCode(404)
                    }
                }
            }
            val api = JzApi(server.url("/").toString())
            api.checkConnection()
            val search = OnlineAlternativeSearch(OnlineApi(api), this, original)
            search.probe()
            withTimeout(4000) { while (search.items.isEmpty()) delay(5) }
            assertEquals(listOf(first), search.items.map { it.identity })
            assertTrue(search.busy)
            val firstShown = search.items.first()
            withTimeout(4000) { while (search.busy) delay(5) }
            assertEquals(listOf(first, better), search.items.map { it.identity })
            val from = OnlinePlaybackRequest(original, "合成影片", "movie", scopeToken = scopeToken)
            search.choose(firstShown, from) { fail("A changed programme was accepted") }
            assertTrue(search.items.isEmpty())
            assertTrue(search.notice.orEmpty().contains("重新探测"))
            assertEquals(1, progressReads); assertEquals(1, switchedSessions)

            rejected = false
            search.probe()
            withTimeout(4000) { while (search.busy) delay(5) }
            var handoff: OnlinePlaybackRequest? = null
            search.choose(search.items.first { it.identity == first }, from) { handoff = it }
            assertEquals(2, progressReads); assertEquals(2, switchedSessions)
            val destination = requireNotNull(handoff)
            assertEquals(11.0, destination.position, 0.0)
            assertEquals(first, destination.identity)
            assertEquals(session, destination.preparedSession?.take()?.sessionId)
            assertNull(destination.preparedSession?.take())
        }
    }

    @Test fun recoveryDeletesQueuedTaskAndCancelledPreparationRetainsOriginalPlayback() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(handshake()); server.enqueue(handshake())
            val online = OnlineApi(JzApi(server.url("/").toString()).also { it.checkConnection() })
            server.takeRequest(); server.takeRequest()
            val search = OnlineAlternativeSearch(online, this, original)
            server.enqueue(step("queued", 2000)); server.enqueue(MockResponse().setResponseCode(204))
            search.probe()
            withTimeout(4000) { while (search.notice != "正在等待探测名额…") delay(5) }
            search.clear()
            assertTrue(search.items.isEmpty()); assertFalse(search.busy)
            delay(100)
            assertEquals("POST", server.takeRequest(2, TimeUnit.SECONDS)?.method)
            assertEquals("DELETE", server.takeRequest(2, TimeUnit.SECONDS)?.method)
            val created = java.util.concurrent.atomic.AtomicBoolean()
            val released = java.util.concurrent.atomic.AtomicBoolean()
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val path = request.requestUrl!!.encodedPath
                    return when {
                        path.endsWith("alternative-tasks") -> step("done", 0, row(first, 2000))
                        path.endsWith(task) && request.method == "DELETE" -> MockResponse().setResponseCode(204)
                        path.contains("/state/items/") -> stateResponse().setBodyDelay(1, TimeUnit.SECONDS)
                        path.endsWith("sessions") -> { created.set(true); descriptor() }
                        path.endsWith(session) && request.method == "DELETE" -> { released.set(true); MockResponse().setResponseCode(204) }
                        else -> MockResponse().setResponseCode(404)
                    }
                }
            }
            search.probe()
            withTimeout(4000) { while (search.busy) delay(5) }
            val selection = launch {
                search.choose(search.items.first(), OnlinePlaybackRequest(original, "影片", "origin", scopeToken = scopeToken)) {
                    fail("Cancelled selection performed a handoff")
                }
            }
            withTimeout(4000) { while (!created.get()) delay(5) }
            // Keep the progress response outstanding but let the session descriptor arrive.
            delay(100)
            search.cancelSelection(); selection.join()
            withTimeout(4000) { while (!released.get()) delay(5) }
            assertFalse(search.selecting)
            assertEquals("已取消切换。", search.notice)
            assertTrue(search.items.isNotEmpty())
        }
    }
}
