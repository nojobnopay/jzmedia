package org.jzmedia.tv.playback

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.*
import org.junit.Test
import org.jzmedia.tv.data.JzApi

class PlaybackSessionLeaseTest {
    @Test fun exitingDuringPreparationReclaimsTheLateSessionWithoutStartingPlayback() = runBlocking {
        latePreparation(cancel = true)
    }

    @Test fun replacedPreparationReclaimsItsSessionWithoutAdoptingIt() = runBlocking {
        latePreparation(cancel = false)
    }

    private suspend fun latePreparation(cancel: Boolean) {
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
            val api = JzApi(server.url("/").toString())
            val current = AtomicBoolean(true)
            val playbackStarts = AtomicInteger()
            val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
            val lease = PlaybackSessionLease(current::get) { api.delete("/api/stream/sessions/$it") }
            val preparing = scope.launch {
                var adopted: String? = null
                try {
                    // Model a session response that wins the transport cancellation race.
                    val session = lease.create(
                        { withContext(NonCancellable) { api.postPlayback("/api/stream/5/sessions") } },
                        { it.getString("session_id") },
                    )
                    adopted = session.getString("session_id")
                    playbackStarts.incrementAndGet()
                } finally { lease.releaseUnlessAdopted(adopted) }
            }
            try {
                withContext(Dispatchers.IO) { assertTrue(requested.await(5, TimeUnit.SECONDS)) }
                if (cancel) preparing.cancel() else current.set(false)
                complete.countDown()
                withTimeout(5000) { preparing.join() }
                assertTrue(preparing.isCancelled)
                assertEquals(0, playbackStarts.get())
                assertEquals("POST", server.takeRequest(1, TimeUnit.SECONDS)!!.method)
                val released = server.takeRequest(1, TimeUnit.SECONDS)!!
                assertEquals("DELETE", released.method)
                assertEquals("/api/stream/sessions/late-this-tv", released.path)
                assertEquals(2, server.requestCount)
            } finally { complete.countDown(); scope.cancel() }
        }
    }

    @Test fun adoptedCurrentSessionRemainsOwnedByThePlayer() = runBlocking {
        val released = mutableListOf<String>()
        val lease = PlaybackSessionLease({ true }) { released += it }
        assertEquals("active", lease.create({ "active" }, { it }))
        lease.releaseUnlessAdopted("active")
        assertTrue(released.isEmpty())
    }
}
