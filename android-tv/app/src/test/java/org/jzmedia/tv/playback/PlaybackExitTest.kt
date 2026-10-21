package org.jzmedia.tv.playback

import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancel
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.jzmedia.tv.data.ApiException
import org.jzmedia.tv.data.JzApi

class PlaybackExitTest {
    @Test fun latePreparationReleasesItsSessionAfterExitHasCancelledMediaTransport() = runBlocking {
        MockWebServer().use { server ->
            val requested = CountDownLatch(1)
            val complete = CountDownLatch(1)
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    if (request.method == "POST") {
                        requested.countDown()
                        check(complete.await(5, TimeUnit.SECONDS))
                        return MockResponse().setBody("""{"session_id":"late-this-tv"}""")
                    }
                    return MockResponse().setBody("{}")
                }
            }
            // The controller's Media3 transport has its own dispatcher. Session
            // APIs belong to JzApi and must survive closing that media transport.
            val mediaTransport = OkHttpClient.Builder().build()
            val api = JzApi(server.url("/").toString())
            val playback = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            val writes = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            val current = AtomicBoolean(true)
            val localReleased = AtomicBoolean()
            val networkReleased = AtomicBoolean()
            val sessionReleased = AtomicBoolean()
            val adopted = AtomicBoolean()
            val lease = PlaybackSessionLease(current::get) {
                api.delete("/api/stream/sessions/$it")
                sessionReleased.set(true)
            }
            val preparing = playback.launch {
                try {
                    // Force the response to win the transport cancellation race,
                    // so the real lease must reject adoption and reclaim its ID.
                    lease.create(
                        { withContext(NonCancellable) { api.postPlayback("/api/stream/5/sessions") } },
                        { it.getString("session_id") },
                    )
                    adopted.set(true)
                } finally { lease.releaseUnlessAdopted(null) }
            }
            val exit = PlaybackExit(playback, writes,
                releaseLocal = { current.set(false); localReleased.set(true) },
                save = { _, _, _ -> fail("No final position exists") },
                releaseSession = { fail("No session was adopted") },
                awaitPendingWrites = {},
                releaseNetwork = {
                    mediaTransport.dispatcher.cancelAll()
                    mediaTransport.connectionPool.evictAll()
                    networkReleased.set(true)
                },
                onFailure = { fail("Unexpected exit failure") },
            )
            try {
                withContext(Dispatchers.IO) { assertTrue(requested.await(5, TimeUnit.SECONDS)) }
                withTimeout(5000) { exit.close(null, 0.0, 1, null)!!.join() }
                assertTrue(localReleased.get())
                assertTrue(networkReleased.get())
                assertFalse(writes.isActive)
                assertTrue(preparing.isCancelled)
                assertFalse(preparing.isCompleted)
                assertFalse(sessionReleased.get())
                complete.countDown()
                withTimeout(5000) { preparing.join() }
                assertFalse(adopted.get())
                assertTrue(sessionReleased.get())
                assertEquals("/api/stream/5/sessions", server.takeRequest(1, TimeUnit.SECONDS)!!.path)
                val released = server.takeRequest(1, TimeUnit.SECONDS)!!
                assertEquals("DELETE", released.method)
                assertEquals("/api/stream/sessions/late-this-tv", released.path)
                assertEquals(2, server.requestCount)
            } finally { complete.countDown(); playback.cancel(); writes.cancel() }
        }
    }

    @Test fun exitReleasesLocallyThenSavesSourceProgressAndOnlyItsOwnSessionOnce() = runBlocking {
        MockWebServer().use { server ->
            repeat(2) { server.enqueue(MockResponse().setBody("{}")) }
            val api = JzApi(server.url("/prefix/").toString(), "synthetic-token")
            val playback = CoroutineScope(SupervisorJob() + Dispatchers.Default)
            val writes = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            val preparing = playback.launch { awaitCancellation() }
            val events = CopyOnWriteArrayList<String>()
            val exit = PlaybackExit(playback, writes,
                releaseLocal = { events += "pause"; events += "media-session"; events += "player" },
                save = { position, duration, sequence ->
                    assertEquals(7L, sequence)
                    events += "save"
                    api.post("/api/stream/progress?version_id=5&kind=episode",
                        JSONObject().put("position", position).put("duration", duration))
                },
                releaseSession = { events += "release"; api.delete("/api/stream/sessions/$it") },
                awaitPendingWrites = { events += "pending-writes" },
                releaseNetwork = { events += "network" },
                onFailure = { fail("Unexpected cleanup failure: ${it.javaClass.simpleName}") },
            )
            val finished = exit.close(sourcePosition(7000, 116.0, 600.0), 600.0, 7, "this-tv")!!
            assertEquals(listOf("pause", "media-session", "player"), events.take(3))
            assertTrue(preparing.isCancelled)
            assertNull(exit.close(1.0, 600.0, 8, "other-device"))
            withTimeout(5000) { finished.join() }
            val requests = List(2) { server.takeRequest(1, TimeUnit.SECONDS)!! }
            val progress = requests.single { it.method == "POST" }
            assertEquals("/prefix/api/stream/progress?version_id=5&kind=episode", progress.path)
            val saved = JSONObject(progress.body.readUtf8())
            assertEquals(123.0, saved.getDouble("position"), 0.0)
            assertEquals(600.0, saved.getDouble("duration"), 0.0)
            assertEquals("synthetic-token", progress.getHeader("X-Api-Token"))
            assertEquals("/prefix/api/stream/sessions/this-tv", requests.single { it.method == "DELETE" }.path)
            assertEquals(2, server.requestCount)
            assertEquals(listOf("pending-writes", "network"), events.takeLast(2))
            assertFalse(playback.isActive)
            assertFalse(writes.isActive)
        }
    }

    @Test fun failedFinalProgressDoesNotCancelSessionReleaseOrNetworkCleanup() = runBlocking {
        MockWebServer().use { server ->
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest) = MockResponse()
                    .setResponseCode(if (request.method == "POST") 503 else 200).setBody("{}")
            }
            val api = JzApi(server.url("/").toString())
            val events = CopyOnWriteArrayList<String>()
            val exit = PlaybackExit(
                CoroutineScope(SupervisorJob() + Dispatchers.Default),
                CoroutineScope(SupervisorJob() + Dispatchers.IO),
                releaseLocal = { events += "local" },
                save = { _, _, _ -> api.post("/api/stream/progress") },
                releaseSession = { api.delete("/api/stream/sessions/$it"); events += "released" },
                awaitPendingWrites = { events += "pending-writes" },
                releaseNetwork = { events += "network" },
                onFailure = { events += "failure:${(it as ApiException).status}" },
            )
            withTimeout(5000) { exit.close(23.0, 600.0, 1, "this-tv")!!.join() }
            assertTrue("failure:503" in events)
            assertTrue("released" in events)
            assertEquals("local", events.first())
            assertEquals(listOf("pending-writes", "network"), events.takeLast(2))
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun exitingBeforePositionOrSessionExistsStillStopsAndCancelsPreparation() = runBlocking {
        val playback = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val writes = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val preparing = playback.launch { awaitCancellation() }
        val events = CopyOnWriteArrayList<String>()
        val exit = PlaybackExit(playback, writes,
            releaseLocal = { events += "local" },
            save = { _, _, _ -> fail("No playback position is available") },
            releaseSession = { fail("No session has been created") },
            awaitPendingWrites = { events += "pending-writes" },
            releaseNetwork = { events += "network" },
            onFailure = { fail("No network operation should run") },
        )
        withTimeout(5000) { exit.close(null, 0.0, 1, null)!!.join() }
        assertTrue(preparing.isCancelled)
        assertEquals(listOf("local", "pending-writes", "network"), events)
        assertFalse(writes.isActive)
    }

    @Test fun slowFinalProgressDoesNotDelayLocalOrSessionReleaseAndPendingWritesFinishBeforeNetworkCleanup() = runBlocking {
        MockWebServer().use { server ->
            val finishProgress = CountDownLatch(1)
            val sessionReleased = CountDownLatch(1)
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    if (request.method == "POST") check(finishProgress.await(5, TimeUnit.SECONDS))
                    else sessionReleased.countDown()
                    return MockResponse().setBody("{}")
                }
            }
            val api = JzApi(server.url("/").toString())
            val playback = CoroutineScope(SupervisorJob() + Dispatchers.Default)
            val writes = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            val localReleased = AtomicBoolean()
            val networkReleased = AtomicBoolean()
            val pendingReached = CompletableDeferred<Unit>()
            val finishPending = CompletableDeferred<Unit>()
            val exit = PlaybackExit(playback, writes,
                releaseLocal = { localReleased.set(true) },
                save = { _, _, _ -> api.post("/api/stream/progress") },
                releaseSession = { api.delete("/api/stream/sessions/$it") },
                awaitPendingWrites = { pendingReached.complete(Unit); finishPending.await() },
                releaseNetwork = { networkReleased.set(true) },
                onFailure = { fail("Unexpected cleanup failure") },
            )
            val finished = exit.close(23.0, 600.0, 1, "this-tv")!!
            try {
                assertTrue(localReleased.get())
                assertFalse(playback.isActive)
                withContext(Dispatchers.IO) { assertTrue(sessionReleased.await(5, TimeUnit.SECONDS)) }
                assertFalse(finished.isCompleted)
                assertFalse(networkReleased.get())
                finishProgress.countDown()
                withTimeout(5000) { pendingReached.await() }
                assertFalse(networkReleased.get())
                finishPending.complete(Unit)
                withTimeout(5000) { finished.join() }
                assertTrue(networkReleased.get())
                assertFalse(writes.isActive)
            } finally { finishProgress.countDown(); finishPending.complete(Unit) }
        }
    }
}
