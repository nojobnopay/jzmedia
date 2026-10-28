package org.jzmedia.tv.data

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.TimeoutCancellationException
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class OnlineApiTest {
    private val source = "src_" + "1".repeat(32)
    private val item = "itm_" + "2".repeat(32)
    private val line = "ln_" + "3".repeat(32)
    private val sid = "s".repeat(43)
    private val writer = "w".repeat(43)
    private val scope = "a".repeat(64)
    private val movie = OnlineIdentity(source, item, "movie", lineId = line)
    private fun sourceJson() = JSONObject().put("id", source).put("name", "Isolated source").put("provider", "maccms")
    private fun descriptor() = JSONObject().put("identity", movie.toJson()).put("session_id", sid)
        .put("media_url", "/api/online/sessions/$sid/media").put("transport", "mp4").put("processing", "passthrough")
        .put("seekable", true).put("duration_seconds", 24.0).put("time_offset_seconds", 0.0).put("expires_at", 2_147_483_648L)
    private fun progress() = JSONObject().put("identity", movie.toJson()).put("position", 12.5).put("duration", 24.0)
        .put("paused", true).put("completed", false).put("updated_at", 10.0).put("writer_revision", 1)
    private fun scoped(value: JSONObject) = value.put("generation", 0).put("scope_token", scope)
    private fun reply(value: JSONObject) = MockResponse().setBody(value.toString())
    private fun api(server: MockWebServer) = JzApi(server.url("/prefix").toString(), "write-token",
        ViewingSession(mode = "private", token = "device-token"))

    @Test fun successfulHandshakeCarriesOnlineCapabilityAcrossViewingModeClones() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(reply(JSONObject().put("protocol_version", 1).put("features", JSONArray().put("online_vod"))))
            server.enqueue(reply(JSONObject().put("protocol_version", 1)))
            val original = api(server)
            assertFalse(original.supportsOnlineVod)
            original.checkConnection()
            assertTrue(original.supportsOnlineVod)
            val replacement = original.withViewingSession(ViewingSession(mode = "normal", token = "device-token", generation = 1))
            assertTrue(replacement.supportsOnlineVod)
            assertEquals("device-token", replacement.viewingToken)
            assertEquals("write-token", replacement.token)
        }
    }

    @Test fun oldServerFeatureAbsenceDoesNotDisableLocalApiConnection() = runBlocking {
        MockWebServer().use { server ->
            repeat(2) { server.enqueue(reply(JSONObject().put("protocol_version", 1))) }
            val connection = api(server)
            connection.checkConnection()
            assertFalse(connection.supportsOnlineVod)
            server.enqueue(MockResponse().setResponseCode(404).setBody("{\"detail\":\"Not Found\"}"))
            try { OnlineApi(connection).sources(); fail("Expected unsupported online API") }
            catch (error: ApiException) { assertEquals(404, error.status); assertTrue(onlineErrorMessage(error).contains("升级服务端")) }
        }
    }

    @Test fun catalogueRequestsEncodeQueriesAndRetainDeviceAndWriteCredentials() = runBlocking {
        MockWebServer().use { server ->
            val online = OnlineApi(api(server))
            server.enqueue(reply(JSONObject().put("source", sourceJson()).put("items", JSONArray()).put("page", 2).put("page_count", 2).put("total", 20)))
            assertEquals(2, online.search(source, "A&watched=1", 2).page)
            val request = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("/prefix/api/online/catalog/search?source_id=$source&q=A%26watched%3D1&page=2", request.path)
            assertEquals("device-token", request.getHeader("X-Viewing-Session"))
            assertEquals("tv", request.getHeader("X-Viewing-Client"))
            assertEquals("write-token", request.getHeader("X-Api-Token"))
            assertNull(request.getHeader("X-Online-State-Scope"))
        }
    }

    @Test fun createsOnlyStableIdentityAndDeletesOnlyThatOwnedSessionPath() = runBlocking {
        MockWebServer().use { server ->
            val online = OnlineApi(api(server))
            server.enqueue(reply(descriptor()).setResponseCode(201))
            val result = online.createSession(movie)
            assertEquals(movie, result.identity)
            assertEquals("${server.url("/prefix/")}api/online/sessions/$sid/media", online.api.absoluteUrl(result.mediaUrl))
            val created = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("POST", created.method)
            val body = JSONObject(created.body.readUtf8())
            assertEquals(movie, OnlineIdentity.fromJson(body))
            assertFalse(body.has("url")); assertFalse(body.has("movie_id")); assertFalse(body.has("quality"))
            server.enqueue(MockResponse().setResponseCode(204))
            online.deleteSession(sid)
            val deleted = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("DELETE", deleted.method)
            assertEquals("/prefix/api/online/sessions/$sid", deleted.path)
            assertEquals("device-token", deleted.getHeader("X-Viewing-Session"))
        }
    }

    @Test fun legacyLibraryExternalIdsRemainReadableButCannotExpandTheCatalogueProtocol() = runBlocking {
        MockWebServer().use { server ->
            val online = OnlineApi(api(server))
            val legacyId = "章节一 & url=https://foreign.example/"
            val legacy = JSONObject().put("id", item).put("source_id", source).put("kind", "movie").put("title", "Movie")
                .put("external_id", legacyId)
            assertEquals(legacyId, OnlineCatalogItem.fromJson(legacy).externalId)
            for (invalid in listOf(legacyId, "a".repeat(129))) {
                try { online.detail(source, invalid); fail("Unsupported catalogue IDs must not be queried") }
                catch (_: IllegalArgumentException) { }
            }
            assertEquals(0, server.requestCount)
            server.enqueue(reply(JSONObject().put("source", sourceJson()).put("item", legacy.put("external_id", "movie-1"))
                .put("description", "").put("year", "").put("lines", JSONArray()).put("attribution", JSONArray())))
            online.detail(source, "movie-1")
            val request = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("/prefix/api/online/catalog/detail", request.requestUrl!!.encodedPath)
            assertEquals("movie-1", request.requestUrl!!.queryParameter("external_id"))
            assertNull(request.requestUrl!!.queryParameter("url"))
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun frozenScopeIsMergedWithAuthForFavoriteClaimProgressAndHistoryClear() = runBlocking {
        MockWebServer().use { server ->
            val online = OnlineApi(api(server))
            server.enqueue(reply(scoped(JSONObject().put("item_id", item).put("favorite", true))))
            online.favorite(item, true, scope)
            server.enqueue(reply(scoped(JSONObject().put("identity", movie.toJson()).put("writer_id", writer)
                .put("writer_revision", 1).put("expires_at", 10000.0).put("progress", JSONObject.NULL))))
            val claimed = online.claim(sid, 0, scope)
            assertEquals(writer, claimed.writerId)
            server.enqueue(reply(scoped(JSONObject().put("progress", progress()))))
            assertEquals(12.5, online.save(writer, 1, 1, 12.5, 24.0, true, false, scope).progress.position, 0.0)
            val newer = "b".repeat(64)
            server.enqueue(reply(JSONObject().put("deleted", 1).put("generation", 0).put("scope_token", newer)))
            assertEquals(newer, online.deleteHistory(item, scope).scopeToken)
            val requests = (1..4).map { server.takeRequest(1, TimeUnit.SECONDS)!! }
            assertEquals(listOf("PUT", "POST", "POST", "DELETE"), requests.map { it.method })
            requests.forEach {
                assertEquals(scope, it.getHeader("X-Online-State-Scope"))
                assertEquals("write-token", it.getHeader("X-Api-Token"))
                assertEquals("device-token", it.getHeader("X-Viewing-Session"))
            }
            val claimBody = JSONObject(requests[1].body.readUtf8())
            assertEquals(sid, claimBody.getString("session_id")); assertEquals(0, claimBody.getInt("expected_revision"))
            val packet = JSONObject(requests[2].body.readUtf8())
            assertEquals(1, packet.getInt("seq")); assertFalse(packet.has("owner")); assertFalse(packet.has("mode")); assertFalse(packet.has("identity"))
        }
    }

    @Test fun staleScopeOrWriterConflictNeverRetriesWithAnotherIdentity() = runBlocking {
        MockWebServer().use { server ->
            val online = OnlineApi(api(server))
            server.enqueue(reply(JSONObject().put("item_id", item).put("favorite", true).put("generation", 1).put("scope_token", "b".repeat(64))))
            try { online.favorite(item, true, scope); fail("Expected scope mismatch") }
            catch (error: ApiException) { assertEquals("online_state_scope_changed", error.code) }
            server.enqueue(MockResponse().setResponseCode(409).setBody("{\"code\":\"online_progress_writer_conflict\",\"detail\":\"secret\"}"))
            try { online.save(writer, 1, 1, 3.0, null, true, false, scope); fail("Expected writer conflict") }
            catch (error: ApiException) { assertEquals("online_progress_writer_conflict", error.code); assertFalse(onlineErrorMessage(error).contains("secret")) }
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun malformedDescriptorsCannotSelectAnotherSourceOrUpstreamAddress() = runBlocking {
        MockWebServer().use { server ->
            val online = OnlineApi(api(server))
            for (bad in listOf(descriptor().put("processing", "transcode"), descriptor().put("media_url", "https://foreign.example/movie.mp4"),
                descriptor().put("identity", movie.copy(lineId = "ln_" + "7".repeat(32)).toJson()))) {
                server.enqueue(reply(bad))
                server.enqueue(MockResponse().setResponseCode(204))
                try { online.createSession(movie); fail("Expected rejected descriptor") }
                catch (error: ApiException) { assertEquals("invalid_online_response", error.code) }
                assertEquals("POST", server.takeRequest(1, TimeUnit.SECONDS)!!.method)
                val released = server.takeRequest(1, TimeUnit.SECONDS)!!
                assertEquals("DELETE", released.method)
                assertEquals("/prefix/api/online/sessions/$sid", released.path)
            }
            assertEquals(6, server.requestCount)
        }
    }

    @Test fun invalidReturnedSessionIdsNeverBecomeCleanupPaths() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(reply(descriptor().put("session_id", "../another-session")))
            try { OnlineApi(api(server)).createSession(movie); fail("Expected invalid ID") }
            catch (error: ApiException) { assertEquals("invalid_online_response", error.code) }
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun failedCleanupKeepsTheOriginalDescriptorFailure() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(reply(descriptor().put("time_offset_seconds", 12)))
            server.enqueue(MockResponse().setResponseCode(503).setBody("secret upstream"))
            try { OnlineApi(api(server)).createSession(movie); fail("Expected original descriptor failure") }
            catch (error: ApiException) {
                assertEquals("invalid_online_response", error.code)
                assertEquals(0, error.status)
                assertFalse(error.message!!.contains("secret"))
            }
            assertEquals(2, server.requestCount)
            server.takeRequest(1, TimeUnit.SECONDS)
            assertEquals("DELETE", server.takeRequest(1, TimeUnit.SECONDS)!!.method)
        }
    }

    @Test fun rejectedDescriptorCleanupHasItsOwnShortDeadline() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(reply(descriptor().put("processing", "transcode")))
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            withTimeout(4000) {
                try { OnlineApi(api(server)).createSession(movie); fail("Expected descriptor failure") }
                catch (error: ApiException) { assertEquals("invalid_online_response", error.code) }
            }
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun itemSelectionAndLibraryPreserveFullLineIdentityAndDeviceScope() = runBlocking {
        MockWebServer().use { server ->
            val online = OnlineApi(api(server))
            server.enqueue(reply(scoped(JSONObject().put("item_id", item).put("favorite", true).put("last_progress", progress())
                .put("selected_progress", progress()).put("available", true).put("reason", ""))))
            val selected = online.itemState(movie)
            assertEquals(movie, selected.selectedProgress?.identity)
            assertEquals(scope, selected.scopeToken)
            assertEquals("/prefix/api/online/state/items/$item?line_id=$line", server.takeRequest(1, TimeUnit.SECONDS)!!.path)
            val row = JSONObject().put("item", JSONObject().put("id", item).put("source_id", source).put("external_id", "movie-1")
                .put("kind", "movie").put("title", "Movie")).put("source", sourceJson().put("enabled", false)).put("favorite", true)
                .put("progress", progress()).put("available", false).put("reason", "online_source_disabled")
            server.enqueue(reply(scoped(JSONObject().put("items", JSONArray().put(row)).put("total", 1).put("limit", 20).put("offset", 0))))
            val library = online.library("favorites")
            assertEquals("movie-1", library.items.single().item.externalId)
            assertFalse(library.items.single().available)
            assertEquals(movie, library.items.single().progress?.identity)
        }
    }

    @Test fun healthReadsOnlyTheServerObservationEndpointAndKeepsPurposesSeparate() = runBlocking {
        MockWebServer().use { server ->
            fun observation(state: String, mode: String, stage: Any) = JSONObject().put("state", state).put("mode", mode)
                .put("stage", stage).put("category", JSONObject.NULL).put("age_seconds", 1.0).put("observed_at", 10)
                .put("configuration_fingerprint", "f".repeat(64)).put("reason", "observed")
            server.enqueue(reply(JSONObject().put("source_id", source).put("cache_scope", "process").put("max_age_seconds", 1800)
                .put("purposes", JSONObject().put("api", observation("success", "proxy", "catalogue"))
                    .put("media", observation("unknown", "direct", JSONObject.NULL)))))
            val result = OnlineApi(api(server)).health(source)
            assertEquals("查询通过 · 代理", result.api.label)
            assertEquals("未观察 · 直连", result.media.label)
            assertEquals("/prefix/api/online/catalog/health?source_id=$source", server.takeRequest(1, TimeUnit.SECONDS)!!.path)
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun invalidScopesAndIdentitiesAreRejectedBeforeAnyRequest() = runBlocking {
        MockWebServer().use { server ->
            val online = OnlineApi(api(server))
            try { online.favorite(item, true, "bad\r\nHeader: injected"); fail("Expected invalid scope") } catch (_: IllegalArgumentException) { }
            try { online.deleteSession("../other"); fail("Expected invalid session") } catch (_: IllegalArgumentException) { }
            try { online.detail(source, "id\r\nInjected: value"); fail("Expected invalid external ID") } catch (_: IllegalArgumentException) { }
            assertEquals(0, server.requestCount)
        }
    }

    @Test fun optionalPlaybackRecordsCanFailAndRecoverWithoutBlockingMediaSelection() = runBlocking {
        MockWebServer().use { server ->
            val online = OnlineApi(api(server))
            server.enqueue(MockResponse().setResponseCode(500).setBody("private upstream error"))
            assertNull(online.playbackState(movie))
            server.enqueue(reply(scoped(JSONObject().put("item_id", item).put("favorite", true).put("last_progress", progress())
                .put("selected_progress", progress()).put("available", true).put("reason", ""))))
            assertEquals(12.5, online.playbackState(movie)?.selectedProgress?.position)
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun optionalPlaybackRecordsNeverDowngradeAnAccessDenial() = runBlocking {
        MockWebServer().use { server ->
            val online = OnlineApi(api(server))
            for (status in listOf(401, 403)) {
                server.enqueue(MockResponse().setResponseCode(status).setBody("{\"code\":\"online_access_revoked\"}"))
                try { online.playbackState(movie); fail("Access denial must stop playback selection") }
                catch (error: ApiException) { assertEquals(status, error.status); assertEquals("online_access_revoked", error.code) }
            }
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun optionalPlaybackRecordsUseABoundedShortDeadline() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            withTimeout(2500) { assertNull(OnlineApi(api(server)).playbackState(movie, timeoutMillis = 500)) }
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun optionalRecordTimeoutDoesNotSwallowAnOuterCancellation() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            var returned = false
            val request = async {
                OnlineApi(api(server)).playbackState(movie)
                returned = true
            }
            withContext(kotlinx.coroutines.Dispatchers.IO) { assertNotNull(server.takeRequest(2, TimeUnit.SECONDS)) }
            withTimeout(2000) { request.cancelAndJoin() }
            assertTrue(request.isCancelled)
            assertFalse(returned)
        }
    }

    @Test fun optionalRecordDeadlineDoesNotSwallowTheCallersShorterTimeout() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            try {
                withTimeout(500) { OnlineApi(api(server)).playbackState(movie) }
                fail("The caller's shorter timeout must remain a cancellation")
            } catch (_: TimeoutCancellationException) { }
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun onlineRequestsCanBeCancelledWithoutWaitingForServerDeadline() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            val request = async { OnlineApi(api(server)).sources() }
            withContext(kotlinx.coroutines.Dispatchers.IO) { assertNotNull(server.takeRequest(2, TimeUnit.SECONDS)) }
            withTimeout(2000) { request.cancelAndJoin() }
            assertTrue(request.isCancelled)
            assertEquals(1, server.requestCount)
        }
    }
}
