package org.jzmedia.tv.data

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.jzmedia.tv.playback.OnlinePlaybackRequest
import org.jzmedia.tv.playback.resolveOnlineNext

class OnlineCacheApiTest {
    private val source = "src_" + "1".repeat(32)
    private val item = "itm_" + "2".repeat(32)
    private val line = "ln_" + "3".repeat(32)
    private val scope = "a".repeat(64)
    private fun identity(index: Int) = OnlineIdentity(source, item, "episode", "ep_" + "%032x".format(index), line)
    private fun detail() = JSONObject().put("source", JSONObject().put("id", source).put("name", "Source")
        .put("provider", "maccms").put("enabled", false))
        .put("item", JSONObject().put("id", item).put("source_id", source).put("kind", "tv").put("title", "Show"))
        .put("description", "Snapshot").put("year", "2026").put("poster_path", "/api/online/cache/artwork/$item")
        .put("attribution", JSONArray()).put("lines", JSONArray().put(JSONObject().put("id", line).put("label", "Main")
            .put("entries", JSONArray((1..3).map { index -> JSONObject().put("label", "第${index}集")
                .put("identity", identity(index).toJson()).put("transport", "hls").put("supported", true).put("reason", "") }))))
    private fun cache(states: List<String> = listOf("ready", "ready", "queued")) = JSONObject()
        .put("external_id", "show_1").put("detail", detail())
        .put("entries", JSONArray(states.mapIndexed { index, state -> JSONObject().put("identity", identity(index + 1).toJson()).put("state", state) }))
    private fun state() = JSONObject().put("item_id", item).put("favorite", false)
        .put("last_progress", JSONObject.NULL).put("selected_progress", JSONObject.NULL).put("available", true)
        .put("reason", "").put("generation", 0).put("scope_token", scope)
    private fun reply(body: JSONObject) = MockResponse().setBody(body.toString())
    private fun api(server: MockWebServer) = OnlineApi(JzApi(server.url("/prefix").toString(), "write-token",
        ViewingSession(mode = "normal", token = "device-token")))
    private fun request() = OnlinePlaybackRequest(identity(1), "Show · 第1集", "show_1", scopeToken = scope,
        autoplay = true, localCache = true)

    @Test fun preferredDetailReadsTheSnapshotBeforeAnyProviderAndCarriesViewingCredentials() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(reply(cache()))
            val opened = api(server).preferredDetail(source, "show_1")
            assertNotNull(opened.cache)
            assertFalse(opened.detail.source.enabled)
            assertTrue(opened.detail.lines.single().entries.first().supported)
            val call = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("/prefix/api/online/cache/lookup?source_id=$source&external_id=show_1", call.path)
            assertEquals("device-token", call.getHeader("X-Viewing-Session"))
            assertEquals("write-token", call.getHeader("X-Api-Token"))
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun onlyAMissingCacheFallsBackToTheExistingProviderDetail() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))
            server.enqueue(reply(detail()))
            val opened = api(server).preferredDetail(source, "show_1")
            assertNull(opened.cache)
            assertEquals(2, server.requestCount)
            assertTrue(server.takeRequest().path!!.contains("/cache/lookup"))
            assertTrue(server.takeRequest().path!!.contains("/catalog/detail"))
        }
    }

    @Test fun deniedCacheAccessNeverTriesTheProvider() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(403).setBody("{}"))
            try { api(server).preferredDetail(source, "show_1"); fail("Must retain access denial") }
            catch (error: ApiException) { assertEquals(403, error.status) }
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun cacheSelectionPreservesOriginalOrderAndNeverMakesIncompleteEpisodesPlayable() {
        val snapshot = OnlineCacheDetail.fromJson(cache(listOf("ready", "downloading", "ready")))
        val entries = snapshot.playableDetail().lines.single().entries
        assertEquals(listOf("第1集", "第2集", "第3集"), entries.map { it.label })
        assertEquals(listOf(true, false, true), entries.map { it.supported })
        assertEquals("cache_not_ready", entries[1].reason)
        assertFalse(snapshot.isReady(identity(2)))
        assertTrue(snapshot.isReady(identity(3)))
    }

    @Test fun offlineArtworkMustBelongToTheSnapshotAndCrossItemEntriesAreRejected() {
        val wrongPoster = cache().also { it.getJSONObject("detail").put("poster_path", "/api/online/cache/artwork/itm_" + "9".repeat(32)) }
        try { OnlineCacheDetail.fromJson(wrongPoster); fail("Cross-item artwork") } catch (_: IllegalArgumentException) { }
        val wrongEntry = cache().also { it.getJSONArray("entries").getJSONObject(0).put("identity", identity(1).copy(itemId = "itm_" + "9".repeat(32)).toJson()) }
        try { OnlineCacheDetail.fromJson(wrongEntry); fail("Cross-item entry") } catch (_: IllegalArgumentException) { }
    }

    @Test fun cachedNextEpisodeReadsOnlyLocalSnapshotAndViewingState() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(reply(cache())); server.enqueue(reply(state()))
            val next = resolveOnlineNext(api(server), request())!!
            assertFalse(next.requiresNetwork)
            assertTrue(next.request.localCache)
            assertEquals(identity(2), next.request.identity)
            assertEquals("/prefix/api/online/cache/items/$item", server.takeRequest().path)
            assertTrue(server.takeRequest().path!!.startsWith("/prefix/api/online/state/items/"))
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun anUncachedAdjacentEpisodeRequiresExplicitNetworkChoiceAndDoesNotSkipToTheNextReadyOne() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(reply(cache(listOf("ready", "paused", "ready")))); server.enqueue(reply(state()))
            val next = resolveOnlineNext(api(server), request())!!
            assertTrue(next.requiresNetwork)
            assertFalse(next.request.localCache)
            assertEquals(identity(2), next.request.identity)
            repeat(2) { assertFalse(server.takeRequest().path!!.contains("/catalog/")) }
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun aDeletedCacheStopsContinuousPlaybackWithoutTryingTheProvider() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(404).setBody("{}"))
            try { resolveOnlineNext(api(server), request()); fail("Deleted snapshot") }
            catch (error: ApiException) { assertEquals(404, error.status) }
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun aDeletedEpisodeSendsTheAtomicCacheOnlyHeaderAndNeverRetriesOnline() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(409).setBody("{\"code\":\"online_cache_not_ready\"}"))
            try { api(server).createSession(identity(1), cacheOnly = true); fail("Deleted entry must stop") }
            catch (error: ApiException) { assertEquals("online_cache_not_ready", error.code) }
            val call = server.takeRequest()
            assertEquals("/prefix/api/online/sessions", call.path)
            assertEquals("true", call.getHeader("X-Online-Cache-Only"))
            assertEquals(identity(1).toJson().toString(), JSONObject(call.body.readUtf8()).toString())
            assertEquals(1, server.requestCount)
        }
    }
}
