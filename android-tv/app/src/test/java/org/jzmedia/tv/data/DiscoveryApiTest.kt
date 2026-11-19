package org.jzmedia.tv.data

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class DiscoveryApiTest {
    private val session = "s".repeat(32)
    private val device = "d".repeat(43)
    private fun result(items: JSONArray = JSONArray(), source: String = "library") = JSONObject()
        .put("ok", true).put("session_id", session).put("status", "results").put("source", source).put("items", items)
    private fun reply(value: JSONObject) = MockResponse().setBody(value.toString())
    private fun api(server: MockWebServer) = JzApi(server.url("/prefix").toString(), "access",
        ViewingSession(mode = "normal", generation = 1, token = "viewing"), initialAiDiscovery = true)

    @Test fun onlyAnAdvertisedCapabilityEnablesDiscoveryAcrossViewingSessions() = runBlocking {
        MockWebServer().use { server ->
            val api = JzApi(server.url("/").toString())
            for (features in listOf(JSONArray().put("tv_search"), JSONArray().put("tv_search").put("ai_discovery"))) {
                val info = JSONObject().put("protocol_version", 1).put("features", features)
                repeat(2) { server.enqueue(reply(info)) }
                api.checkConnection()
                val expected = features.length() > 1
                assertEquals(expected, api.supportsAiDiscovery)
                assertEquals(expected, api.withViewingSession(ViewingSession(token = "device")).supportsAiDiscovery)
                if (!expected) {
                    try { DiscoveryApi(api).search(0, "all", "片名"); fail() }
                    catch (error: ApiException) { assertEquals(404, error.status) }
                }
            }
            val children = api.withViewingSession(ViewingSession(mode = "children", token = "device"))
            try { DiscoveryApi(children).search(0, "all", "推荐"); fail() }
            catch (error: ApiException) { assertEquals("children_restricted", error.code) }
            assertEquals(4, server.requestCount)
        }
    }

    @Test fun resultsRefreshAndPublicContinuationShareOnlyTheScopedDeviceCookie() = runBlocking {
        MockWebServer().use { server ->
            val connection = api(server)
            val search = DiscoveryApi(connection)
            server.enqueue(reply(result()).addHeader("Set-Cookie", "jz_discovery_device=$device; Path=/api/ai/search/results; HttpOnly; SameSite=Strict")
                .addHeader("Set-Cookie", "other=unexpected; Path=/"))
            val first = search.search(13, "collections", " 找一部太空片 ")
            val initial = server.takeRequest()
            assertEquals("POST", initial.method)
            assertEquals("/prefix/api/ai/search/results", initial.path)
            assertNull(initial.getHeader("Cookie"))
            val body = JSONObject(initial.body.readUtf8())
            assertEquals("找一部太空片", body.getString("q"))
            assertEquals("library", body.getString("source"))
            assertFalse(body.getBoolean("allow_query_source"))
            assertEquals(listOf("movie", "tv"), body.getJSONArray("kinds").run { listOf(getString(0), getString(1)) })
            assertEquals(13, body.getInt("media_library_id"))

            server.enqueue(reply(result()))
            search.refresh(13, "collections", first.sessionId)
            val refresh = server.takeRequest()
            assertEquals("/prefix/api/ai/search/results/refresh", refresh.path)
            assertEquals("jz_discovery_device=$device", refresh.getHeader("Cookie"))
            assertEquals("access", refresh.getHeader("X-Api-Token"))
            assertEquals("viewing", refresh.getHeader("X-Viewing-Session"))
            val refreshBody = JSONObject(refresh.body.readUtf8())
            assertEquals(first.sessionId, refreshBody.getString("session_id"))
            assertFalse(refreshBody.has("q"))
            assertEquals(body.getJSONArray("kinds").toString(), refreshBody.getJSONArray("kinds").toString())

            server.enqueue(reply(result(source = "public")))
            search.search(13, "collections", "查公开片目", first.sessionId, "public", reusePlan = true)
            val next = server.takeRequest()
            assertEquals("jz_discovery_device=$device", next.getHeader("Cookie"))
            assertTrue(JSONObject(next.body.readUtf8()).getBoolean("reuse_plan"))

            server.enqueue(reply(JSONObject()))
            connection.get("/api/movies")
            assertNull(server.takeRequest().getHeader("Cookie"))
            val privateSearch = DiscoveryApi(connection.withViewingSession(ViewingSession(mode = "private", token = "viewing", generation = 2)))
            server.enqueue(reply(result()))
            privateSearch.search(0, "shows", "悬疑剧")
            val changed = server.takeRequest()
            assertNull(changed.getHeader("Cookie"))
            val privateBody = JSONObject(changed.body.readUtf8())
            assertFalse(privateBody.has("media_library_id"))
            assertEquals("tv", privateBody.getString("kind"))
        }
    }

    @Test fun modelRequestCannotBeReplayedBy503OrInvalidSpeech() = runBlocking {
        MockWebServer().use { server ->
            val search = DiscoveryApi(api(server))
            for (value in listOf(" ", "太".repeat(501))) {
                try { search.search(0, "all", value); fail() } catch (_: IllegalArgumentException) { }
            }
            server.enqueue(MockResponse().setResponseCode(503).setHeader("Retry-After", "0"))
            server.enqueue(reply(result()))
            try { search.search(0, "all", "找片"); fail() } catch (error: ApiException) { assertEquals(503, error.status) }
            assertEquals(1, server.requestCount)
            assertNotNull(server.takeRequest(1, TimeUnit.SECONDS))
        }
    }

    @Test fun verifiedLocalTargetsUseTypedIdsWithSpecificVersionAndPublicTargetsUseTitles() {
        fun item(key: String, kind: String, id: Int) = JSONObject().put("key", key).put("kind", kind)
            .put("id", id).put("title", "同名").put("in_library", true)
        val items = JSONArray()
            .put(item("film", "movie", 10).put("matched_version_id", 12).put("poster_path", "posters/cover.jpg"))
            .put(item("series", "tv", 21).put("matched_episode_id", 92))
            .put(item("season", "season", 999).put("show_id", 21).put("season", 0))
            .put(item("episode", "episode", 90).put("version_id", 93))
            .put(item("public", "movie", 999).put("in_library", false).put("poster_url", "https://example.org/poster.jpg"))
        val got = readDiscoveryResult(result(items, "public"))
        assertEquals(listOf(DiscoveryTarget.Detail("movie", 12, versionId = 12), DiscoveryTarget.Detail("episode", 92),
            DiscoveryTarget.Detail("season", 21, 0), DiscoveryTarget.Detail("episode", 93), DiscoveryTarget.Online("同名")),
            got.items.map { it.target })
        assertEquals("/posters/cover.jpg", got.items.first().poster)
        assertEquals("", got.items.last().poster)
        assertNull(readDiscoveryResult(result(JSONArray().put(items.getJSONObject(4)))).items.single().target)
    }

    @Test fun rankingShowsThePreciseSourcedScoreOnceAndKeepsAdditionalReasonsAndComparisons() {
        val score = "TMDB 评分 8.84 分"
        val extra = "$score，手持摄影切合写实风格"
        val other = "豆瓣 评分 8.84 分"
        val items = JSONArray()
        listOf(score, extra, other, "叙事节奏舒缓").forEachIndexed { i, reason ->
            items.put(JSONObject().put("key", "film-$i").put("title", "电影 $i")
                .put("rating", 8.84).put("rating_source", "tmdb").put("reason", reason))
        }
        val response = result(items).put("comparison", JSONArray().put(JSONObject()
            .put("key", "film-0").put("title", "电影 0").put("text", score)))
        val list = readDiscoveryResult(response)
        assertTrue(list.items.all { it.ratingLabel == score })
        assertEquals(listOf("", extra, other, "叙事节奏舒缓"), list.items.map { it.reason })
        assertEquals(score, list.comparison.single().text)
    }

    @Test fun expiredSessionAndIncompleteResponsesNeverBecomeEmptyRecommendations() {
        val expired = assertThrows(ApiException::class.java) {
            readDiscoveryResult(JSONObject().put("ok", false).put("code", "session_expired").put("message", "upstream secret"))
        }
        assertEquals("session_expired", expired.code)
        assertFalse(expired.message!!.contains("secret"))
        assertThrows(ApiException::class.java) { readDiscoveryResult(result().put("session_id", "")) }
        assertThrows(ApiException::class.java) { readDiscoveryResult(result(JSONArray().put("not a card"))) }
    }
}
