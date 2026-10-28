package org.jzmedia.tv.playback

import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.Assert.*
import org.junit.Test
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.ViewingSession
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class OnlineRangeReaderTest {
    private fun http() = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).build()
    private fun ranged(body: ByteArray, start: Int, end: Int, total: Int = body.size) = MockResponse().setResponseCode(206)
        .setHeader("Content-Range", "bytes $start-$end/$total").setHeader("ETag", "\"stable\"")
        .setBody(Buffer().write(body, start, end - start + 1))

    @Test fun progressiveReadCrossesBoundedResponsesAndReturnsOnlyRealEndOfFile() {
        MockWebServer().use { server ->
            val content = ByteArray(47) { (it * 17).toByte() }
            val ranges = mutableListOf<String>()
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val range = request.getHeader("Range")!!
                    synchronized(ranges) { ranges.add(range) }
                    val (start, last) = range.removePrefix("bytes=").split('-').map(String::toInt)
                    assertTrue(last - start + 1 <= 16)
                    return ranged(content, start, minOf(last, content.lastIndex))
                }
            }
            val reader = OnlineRangeReader(http(), server.url("/media").toString(), chunkBytes = 16)
            try {
                assertEquals(47L, reader.open(0))
                val output = ByteArrayOutputStream()
                val buffer = ByteArray(7)
                while (true) { val count = reader.read(buffer, 0, buffer.size); if (count < 0) break; output.write(buffer, 0, count) }
                assertArrayEquals(content, output.toByteArray())
                assertEquals(listOf("bytes=0-15", "bytes=16-31", "bytes=32-46"), ranges)
                assertEquals(-1, reader.read(buffer, 0, buffer.size))
                assertEquals(3, server.requestCount)
                assertNull(server.takeRequest(1, TimeUnit.SECONDS)!!.getHeader("If-Range"))
                assertEquals("\"stable\"", server.takeRequest(1, TimeUnit.SECONDS)!!.getHeader("If-Range"))
            } finally { reader.close() }
        }
    }

    @Test fun seekingAndExplicitLengthsDoNotRequestOrExposeUnselectedBytes() {
        MockWebServer().use { server ->
            val body = ByteArray(80) { it.toByte() }
            server.enqueue(ranged(body, 37, 47)); server.enqueue(ranged(body, 69, 79))
            val reader = OnlineRangeReader(http(), server.url("/media").toString(), chunkBytes = 16)
            try {
                assertEquals(11L, reader.open(37, 11))
                val bytes = ByteArray(30)
                assertEquals(11, reader.read(bytes, 0, bytes.size))
                assertArrayEquals(body.copyOfRange(37, 48), bytes.copyOf(11))
                assertEquals(-1, reader.read(bytes, 0, bytes.size))
                reader.close()
                val seeking = OnlineRangeReader(http(), server.url("/media").toString(), chunkBytes = 16)
                try {
                    assertEquals(11L, seeking.open(69))
                    assertEquals(11, seeking.read(bytes, 0, bytes.size))
                    assertEquals(-1, seeking.read(bytes, 0, bytes.size))
                } finally { seeking.close() }
                assertEquals("bytes=37-47", server.takeRequest(1, TimeUnit.SECONDS)!!.getHeader("Range"))
                assertEquals("bytes=69-84", server.takeRequest(1, TimeUnit.SECONDS)!!.getHeader("Range"))
            } finally { reader.close() }
        }
    }

    @Test fun productionRangeCapRemainsEightMiBAndCannotBeRaisedByAReader() {
        assertEquals(8L * 1024 * 1024, ONLINE_RANGE_BYTES)
        assertThrows(IllegalArgumentException::class.java) { OnlineRangeReader(http(), "http://localhost/media", ONLINE_RANGE_BYTES + 1) }
        assertThrows(IllegalArgumentException::class.java) { OnlineRangeReader(http(), "http://localhost/media", 0) }
    }

    @Test fun ignoredRangeChangedLengthAndChangedRepresentationAreRejected() {
        MockWebServer().use { server ->
            val body = ByteArray(32)
            server.enqueue(MockResponse().setBody(Buffer().write(body)))
            val ignored = OnlineRangeReader(http(), server.url("/media").toString(), 16)
            try { assertThrows(OnlineMediaResponseException::class.java) { ignored.open(0) } } finally { ignored.close() }
            server.enqueue(ranged(body, 0, 15)); server.enqueue(ranged(body, 16, 31).setHeader("ETag", "\"changed\""))
            val changed = OnlineRangeReader(http(), server.url("/media").toString(), 16)
            try {
                changed.open(0)
                assertEquals(16, changed.read(ByteArray(16), 0, 16))
                assertThrows(IOException::class.java) { changed.read(ByteArray(16), 0, 16) }
            } finally { changed.close() }
            server.enqueue(ranged(body, 0, 15)); server.enqueue(ranged(body, 16, 31, total = 33))
            val resized = OnlineRangeReader(http(), server.url("/media").toString(), 16)
            try {
                resized.open(0); resized.read(ByteArray(16), 0, 16)
                assertThrows(IOException::class.java) { resized.read(ByteArray(16), 0, 16) }
            } finally { resized.close() }
        }
    }

    @Test fun serverOriginAuthenticationNeverFollowsRedirectsToAnotherHost() {
        MockWebServer().use { server -> MockWebServer().use { foreign ->
            val api = JzApi(server.url("/prefix/").toString(), "write-secret", ViewingSession(mode = "normal", token = "viewing-secret"))
            server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", foreign.url("/media")))
            val client = onlineMediaHttpClient(api)
            val reader = OnlineRangeReader(client, api.absoluteUrl("/api/online/sessions/one/media"), 16)
            try {
                assertThrows(IOException::class.java) { reader.open(0) }
                val sent = server.takeRequest(1, TimeUnit.SECONDS)!!
                assertEquals("write-secret", sent.getHeader("X-Api-Token"))
                assertEquals("viewing-secret", sent.getHeader("X-Viewing-Session"))
                assertEquals(0, foreign.requestCount)
                val rejected = OnlineRangeReader(client, foreign.url("/media").toString(), 16)
                try { assertThrows(IOException::class.java) { rejected.open(0) } } finally { rejected.close() }
                assertEquals(0, foreign.requestCount)
            } finally { reader.close(); client.dispatcher.cancelAll(); client.connectionPool.evictAll() }
        } }
    }

    @Test fun closingAnActiveRangeInterruptsTheBlockingBodyWithoutWaitingForItsTimeout() {
        MockWebServer().use { server ->
            server.enqueue(ranged(ByteArray(16), 0, 15).setBodyDelay(2, TimeUnit.SECONDS))
            val reader = OnlineRangeReader(http(), server.url("/media").toString(), 16)
            val worker = Executors.newSingleThreadExecutor()
            try {
                reader.open(0)
                val task = worker.submit<Boolean> {
                    try { reader.read(ByteArray(16), 0, 16); false } catch (_: IOException) { true }
                }
                reader.close()
                assertTrue(task.get(1, TimeUnit.SECONDS))
            } finally { reader.close(); worker.shutdownNow() }
        }
    }

    @Test fun closeCannotBeOvertakenByTheFirstOpenOrAnotherRangeRegistration() {
        MockWebServer().use { server ->
            val reader = OnlineRangeReader(http(), server.url("/media").toString(), 16)
            reader.close()
            assertThrows(IOException::class.java) { reader.open(0) }
            assertEquals(0, server.requestCount)
            server.enqueue(ranged(ByteArray(32), 0, 15))
            val active = OnlineRangeReader(http(), server.url("/media").toString(), 16)
            active.open(0); active.read(ByteArray(16), 0, 16); active.close()
            assertThrows(IOException::class.java) { active.read(ByteArray(16), 0, 16) }
            assertEquals(1, server.requestCount)
        }
    }
}
