package org.jzmedia.tv.data

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class OnlineHostsApiTest {
    private val source = "src_" + "1".repeat(32)
    private val fingerprint = "a".repeat(64)
    private fun reportJson() = JSONObject().put("source_id", source).put("external_id", "show-1")
        .put("missing_hosts", JSONArray().put("media.example").put("images.example"))
        .put("allowed_hosts", JSONArray().put("api.example")).put("rejected_count", 0)
        .put("configuration_fingerprint", fingerprint)
    private fun api(server: MockWebServer) = OnlineApi(JzApi(server.url("/prefix").toString(), "write-token",
        ViewingSession(mode = "normal", enabled = true, managementUnlocked = true, token = "device-token")))

    @Test fun checkAndApprovalKeepSameOriginCredentialsAndOnlySendExplicitSelection() = runBlocking {
        MockWebServer().use { server ->
            val online = api(server)
            server.enqueue(MockResponse().setBody(reportJson().toString()))
            val report = online.checkHosts(source, "show-1")
            val check = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("POST", check.method)
            assertEquals("/prefix/api/online/sources/$source/host-check", check.path)
            assertEquals(setOf("external_id"), JSONObject(check.body.readUtf8()).keys().asSequence().toSet())
            server.enqueue(MockResponse().setBody(JSONObject().put("id", source)
                .put("allowed_hosts", JSONArray().put("api.example").put("images.example"))
                .put("api_base_url", "https://private.example/api").put("provider_options", JSONObject()).toString()))
            online.allowHosts(report, listOf("images.example"))
            val write = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("Approval must open a fresh connection after domain review", 0, write.sequenceNumber)
            assertEquals("POST", write.method)
            assertEquals("/prefix/api/online/sources/$source/allow-hosts", write.path)
            val selected = JSONObject(write.body.readUtf8())
            assertEquals(setOf("hosts", "expected_fingerprint"), selected.keys().asSequence().toSet())
            assertEquals(listOf("images.example"), (0 until selected.getJSONArray("hosts").length()).map { selected.getJSONArray("hosts").getString(it) })
            assertEquals(fingerprint, selected.getString("expected_fingerprint"))
            for (request in listOf(check, write)) {
                assertEquals("write-token", request.getHeader("X-Api-Token"))
                assertEquals("device-token", request.getHeader("X-Viewing-Session"))
                assertEquals("tv", request.getHeader("X-Viewing-Client"))
                assertNull(request.getHeader("X-Online-State-Scope"))
            }
        }
    }

    @Test fun invalidIdentitiesAndUnreviewedSelectionNeverContactTheServer() = runBlocking {
        MockWebServer().use { server ->
            val online = api(server)
            for ((sourceId, externalId) in listOf("../source" to "show-1", source to "show-1&url=private")) {
                try { online.checkHosts(sourceId, externalId); fail("Invalid host check identity") }
                catch (_: IllegalArgumentException) { }
            }
            val report = OnlineHostReport.fromJson(reportJson())
            for (hosts in listOf(emptyList(), listOf("unreviewed.example"), listOf("api.example"), listOf("media.example", "media.example"))) {
                try { online.allowHosts(report, hosts); fail("Only explicit reviewed hosts may be submitted") }
                catch (_: IllegalArgumentException) { }
            }
            assertEquals(0, server.requestCount)
        }
    }

    @Test fun checkRejectsMismatchedSourceOrItemAndDoesNotAdoptAnUnexpectedReport() = runBlocking {
        MockWebServer().use { server ->
            val online = api(server)
            for (bad in listOf(reportJson().put("source_id", "src_" + "2".repeat(32)), reportJson().put("external_id", "show-2"))) {
                server.enqueue(MockResponse().setBody(bad.toString()))
                try { online.checkHosts(source, "show-1"); fail("Mismatched report") }
                catch (error: ApiException) { assertEquals("invalid_online_response", error.code) }
            }
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun approvalRejectsWrongSourceOrLostHostsAndNeverRetriesHttpConflict() = runBlocking {
        MockWebServer().use { server ->
            val online = api(server)
            val report = OnlineHostReport.fromJson(reportJson())
            for (bad in listOf(JSONObject().put("id", "src_" + "2".repeat(32)).put("allowed_hosts", JSONArray().put("api.example").put("media.example")),
                JSONObject().put("id", source).put("allowed_hosts", JSONArray().put("media.example")))) {
                server.enqueue(MockResponse().setBody(bad.toString()))
                try { online.allowHosts(report, listOf("media.example")); fail("Mismatched approval") }
                catch (error: ApiException) { assertEquals("invalid_online_response", error.code) }
            }
            server.enqueue(MockResponse().setResponseCode(409).setBody("{\"code\":\"online_source_changed\"}"))
            try { online.allowHosts(report, listOf("media.example")); fail("Stale report") }
            catch (error: ApiException) { assertEquals("online_source_changed", error.code) }
            assertEquals(3, server.requestCount)
        }
    }

    @Test fun closingReviewCancelsTheCheckWithoutSubmittingAnApproval() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            val online = api(server)
            val check = async { online.checkHosts(source, "show-1") }
            withContext(Dispatchers.IO) { assertNotNull(server.takeRequest(2, TimeUnit.SECONDS)) }
            withTimeout(2000) { check.cancelAndJoin() }
            assertTrue(check.isCancelled)
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun approvalDoesNotFollowRetryAfterOrReplayARequestWithAnUnknownResult() = runBlocking {
        MockWebServer().use { server ->
            val online = api(server)
            val report = OnlineHostReport.fromJson(reportJson())
            val success = JSONObject().put("id", source)
                .put("allowed_hosts", JSONArray().put("api.example").put("media.example"))
            server.enqueue(MockResponse().setResponseCode(503).setHeader("Retry-After", "0").setBody("{}"))
            server.enqueue(MockResponse().setBody(success.toString()))
            try { online.allowHosts(report, listOf("media.example")); fail("A failed write must never be replayed") }
            catch (error: ApiException) { assertEquals(503, error.status) }
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun approvalDisconnectAfterSendDoesNotReplayAnUncertainWrite() = runBlocking {
        MockWebServer().use { server ->
            val online = api(server)
            val report = OnlineHostReport.fromJson(reportJson())
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST))
            server.enqueue(MockResponse().setBody(JSONObject().put("id", source)
                .put("allowed_hosts", JSONArray().put("api.example").put("media.example")).toString()))
            try { online.allowHosts(report, listOf("media.example")); fail("Disconnected writes have an unknown result") }
            catch (error: ApiException) {
                assertEquals(0, error.status)
                assertTrue(onlineHostError(error, applying = true).contains("无法确认"))
            }
            assertEquals(1, server.requestCount)
        }
    }
}
