package org.jzmedia.tv.playback

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.OnlineApi
import org.jzmedia.tv.data.OnlineIdentity
import java.util.concurrent.TimeUnit

class OnlineProgressWriterTest {
    private val identity = OnlineIdentity("src_" + "1".repeat(32), "itm_" + "2".repeat(32), "movie", lineId = "ln_" + "3".repeat(32))
    private val scope = "a".repeat(64)
    private val session = "s".repeat(43)
    private val writerId = "w".repeat(43)
    private fun claim(revision: Long) = JSONObject().put("identity", identity.toJson()).put("writer_id", writerId)
        .put("writer_revision", revision).put("expires_at", 9000).put("progress", JSONObject.NULL)
        .put("generation", 0).put("scope_token", scope)
    private fun saved(revision: Long, position: Double) = JSONObject().put("generation", 0).put("scope_token", scope)
        .put("progress", JSONObject().put("identity", identity.toJson()).put("position", position).put("duration", 24)
            .put("paused", true).put("completed", false).put("updated_at", 100).put("writer_revision", revision))

    @Test fun preparingBeforeMediaDoesNotWriteHistoryAndRenewalUsesOnlyItsKnownRevision() = runBlocking {
        MockWebServer().use { server ->
            val api = OnlineApi(JzApi(server.url("/").toString(), "write-token"))
            val notices = mutableListOf<String>()
            val writer = OnlineProgressWriter(api, identity, scope, 0) { notices.add(it) }
            server.enqueue(MockResponse().setBody(claim(1).toString()))
            assertTrue(writer.prepare(session)); assertEquals(1, server.requestCount)
            assertTrue(writer.prepare(session)); assertEquals(1, server.requestCount)
            val first = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("/api/online/state/progress/claim", first.path)
            assertEquals(0L, JSONObject(first.body.readUtf8()).getLong("expected_revision"))
            assertEquals(scope, first.getHeader("X-Online-State-Scope"))
            assertEquals("write-token", first.getHeader("X-Api-Token"))
            server.enqueue(MockResponse().setBody(saved(1, 8.0).toString()))
            assertTrue(writer.save(OnlineProgressSnapshot(8.0, 24.0, true, false)))
            assertEquals(1L, JSONObject(server.takeRequest(1, TimeUnit.SECONDS)!!.body.readUtf8()).getLong("seq"))
            server.enqueue(MockResponse().setBody(claim(2).toString()))
            assertTrue(writer.prepare("n".repeat(43)))
            assertEquals(1L, JSONObject(server.takeRequest(1, TimeUnit.SECONDS)!!.body.readUtf8()).getLong("expected_revision"))
            server.enqueue(MockResponse().setBody(saved(2, 4.0).toString()))
            assertTrue(writer.save(OnlineProgressSnapshot(4.0, 24.0, true, false)))
            assertEquals(1L, JSONObject(server.takeRequest(1, TimeUnit.SECONDS)!!.body.readUtf8()).getLong("seq"))
            assertTrue(notices.isEmpty())
        }
    }

    @Test fun anOwnershipConflictStopsSavingAndNeverReadsOrClaimsANewerWritersRevision() = runBlocking {
        MockWebServer().use { server ->
            val notices = mutableListOf<String>()
            val writer = OnlineProgressWriter(OnlineApi(JzApi(server.url("/").toString())), identity, scope, 0) { notices.add(it) }
            server.enqueue(MockResponse().setBody(claim(1).toString()))
            assertTrue(writer.prepare(session))
            server.enqueue(MockResponse().setResponseCode(409).setBody("""{"code":"online_progress_writer_conflict"}"""))
            assertFalse(writer.save(OnlineProgressSnapshot(2.0, 24.0, false, false)))
            assertTrue(writer.disabled)
            assertFalse(writer.prepare("n".repeat(43)))
            assertFalse(writer.save(OnlineProgressSnapshot(3.0, 24.0, true, false)))
            assertEquals(2, server.requestCount); assertEquals(1, notices.size)
        }
    }

    @Test fun aMissingScopeDisablesOptionalRecordingWithoutSendingAnUnboundMutation() = runBlocking {
        MockWebServer().use { server ->
            val notices = mutableListOf<String>()
            val writer = OnlineProgressWriter(OnlineApi(JzApi(server.url("/").toString())), identity, "", 0) { notices.add(it) }
            assertFalse(writer.prepare(session)); assertTrue(writer.disabled)
            assertEquals(0, server.requestCount); assertEquals(1, notices.size)
        }
    }

    @Test fun cancellingPreparationCancelsTheHttpClaimWithoutCreatingAProgressPacket() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody(claim(1).toString()).setBodyDelay(2, TimeUnit.SECONDS))
            val writer = OnlineProgressWriter(OnlineApi(JzApi(server.url("/").toString())), identity, scope, 0) { }
            val pending = async(Dispatchers.Default) { writer.prepare(session) }
            assertNotNull(server.takeRequest(1, TimeUnit.SECONDS))
            pending.cancelAndJoin()
            assertEquals(1, server.requestCount)
            assertFalse(writer.disabled)
            assertFalse(writer.save(OnlineProgressSnapshot(2.0, 24.0, false, false)))
        }
    }
}
