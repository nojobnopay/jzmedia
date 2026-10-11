package org.jzmedia.tv.playback

import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import okio.Buffer
import org.json.JSONArray
import org.json.JSONObject
import org.jzmedia.tv.data.ApiException
import org.jzmedia.tv.data.JzApi
import org.junit.Assert.*
import org.junit.Test

class AssSubtitleLoaderTest {
    private val script = """
        [Script Info]
        PlayResX: 1920
        PlayResY: 1080
        [V4+ Styles]
        Format: Name, Fontname, Fontsize, PrimaryColour
        Style: Signs,方正测试,42,&H0000FF00
        [Events]
        Format: Layer, Start, End, Style, Text
        Dialogue: 0,0:00:01.00,0:00:04.00,Signs,{\pos(350,200)\fad(100,100)}招牌\N第二行
    """.trimIndent()

    private class FakeEngine(private val onLoad: () -> Unit = {}) : AssSubtitleEngine {
        val fonts = mutableListOf<Pair<String, ByteArray>>()
        val closed = AtomicInteger()
        val threads = mutableListOf<String>()
        var source: String? = null
        override fun addFont(name: String, bytes: ByteArray) {
            threads += Thread.currentThread().name
            fonts += name to bytes
        }
        override fun load(source: String) {
            threads += Thread.currentThread().name
            this.source = source
            onLoad()
        }
        override fun close() { closed.incrementAndGet() }
    }

    private fun font(name: String = "cjk.ttc", path: String = "/api/stream/7/fonts/cjk.ttc",
                     source: String = "attachment") = JSONObject()
        .put("name", name).put("url", path).put("source", source)

    private fun prepare(server: MockWebServer, fonts: JSONArray = JSONArray()) {
        server.enqueue(MockResponse().setBody(script))
        server.enqueue(MockResponse().setBody(JSONObject().put("fonts", fonts).toString()))
    }

    @Test fun rawAssAndLargeFontCollectionsReachTheEngineWithoutSimplification() = runBlocking {
        MockWebServer().use { server ->
            val bytes = ByteArray(5 * 1024 * 1024) { (it % 251).toByte() }
            prepare(server, JSONArray().put(font()))
            server.enqueue(MockResponse().setBody(Buffer().write(bytes)))
            val callerThread = Thread.currentThread().name
            val engine = FakeEngine()
            assertSame(engine, loadAssSubtitle(JzApi(server.url("/").toString()), 7, "movie", 2) { engine })
            assertEquals(script, engine.source)
            assertEquals("cjk.ttc", engine.fonts.single().first)
            assertArrayEquals(bytes, engine.fonts.single().second)
            assertTrue(engine.threads.all { it != callerThread })
            assertEquals(0, engine.closed.get())
            engine.close()
        }
    }

    @Test fun oldAttachmentUrlsKeepKindEncodingProxyPrefixAndAuthentication() = runBlocking {
        for (kind in listOf("movie", "episode", "extra")) {
            MockWebServer().use { server ->
                val path = "/api/stream/7/fonts/%E6%96%B9%E6%AD%A3%20%5B0%5D.ttf?kind=movie&revision=2"
                prepare(server, JSONArray().put(font("方正 [0].ttf", path, source = "")))
                server.enqueue(MockResponse().setBody(Buffer().write(byteArrayOf(0, 127, -1))))
                val engine = loadAssSubtitle(JzApi(server.url("/prefix").toString(), "font-token"), 7, kind, 2) { FakeEngine() }
                val requests = List(3) { server.takeRequest(1, TimeUnit.SECONDS)!! }
                assertEquals("/prefix/api/stream/7/sub/2.ass?kind=$kind", requests[0].path)
                assertEquals("/prefix/api/stream/7/fonts?kind=$kind", requests[1].path)
                val fontUrl = requests[2].requestUrl!!
                assertEquals("/prefix/api/stream/7/fonts/%E6%96%B9%E6%AD%A3%20%5B0%5D.ttf", fontUrl.encodedPath)
                assertEquals(listOf(kind), fontUrl.queryParameterValues("kind"))
                assertEquals("2", fontUrl.queryParameter("revision"))
                assertTrue(requests.all { it.getHeader("X-Api-Token") == "font-token" })
                engine.close()
            }
        }
    }

    @Test fun builtinFontsKeepTheirReturnedUrlAndBytes() = runBlocking {
        MockWebServer().use { server ->
            prepare(server, JSONArray().put(font("builtin.ttf", "/api/stream/fonts/builtin/builtin.ttf", "builtin")))
            server.enqueue(MockResponse().setBody(Buffer().write(byteArrayOf(1, 2, 3))))
            val engine = loadAssSubtitle(JzApi(server.url("/prefix").toString()), 7, "episode", 2) { FakeEngine() }
            repeat(2) { server.takeRequest(1, TimeUnit.SECONDS)!! }
            assertEquals("/prefix/api/stream/fonts/builtin/builtin.ttf", server.takeRequest(1, TimeUnit.SECONDS)!!.path)
            assertArrayEquals(byteArrayOf(1, 2, 3), engine.fonts.single().second)
            engine.close()
        }
    }

    @Test fun missingFontsStillLoadsWithTheSystemFontFallback() = runBlocking {
        MockWebServer().use { server ->
            prepare(server)
            val engine = loadAssSubtitle(JzApi(server.url("/").toString()), 7, "movie", 2) { FakeEngine() }
            assertEquals(script, engine.source)
            assertTrue(engine.fonts.isEmpty())
            assertEquals(2, server.requestCount)
            engine.close()
        }
    }

    @Test fun fontFailureClosesTheNewEngineAndDoesNotLoadIncompleteAss() = runBlocking {
        MockWebServer().use { server ->
            prepare(server, JSONArray().put(font()))
            server.enqueue(MockResponse().setResponseCode(404))
            val engine = FakeEngine()
            try {
                loadAssSubtitle(JzApi(server.url("/").toString()), 7, "movie", 2) { engine }
                fail("Expected missing font")
            } catch (error: AssSubtitleLoadException) {
                assertEquals(AssLoadStep.FONT_DOWNLOAD, error.step)
                assertEquals(404, (error.cause as ApiException).status)
                assertTrue(error.message!!.contains("HTTP 404"))
            }
            assertEquals(1, engine.closed.get())
            assertNull(engine.source)
        }
    }

    @Test fun otherOriginFontUrlsAreRejectedBeforeSendingCredentials() = runBlocking {
        MockWebServer().use { server -> MockWebServer().use { other ->
            prepare(server, JSONArray().put(font(path = other.url("/api/stream/7/fonts/cjk.ttc").toString())))
            val engine = FakeEngine()
            try {
                loadAssSubtitle(JzApi(server.url("/").toString(), "private-token"), 7, "movie", 2) { engine }
                fail("Expected other-origin URL rejection")
            } catch (error: AssSubtitleLoadException) {
                assertEquals(AssLoadStep.FONT_LIST, error.step)
                assertTrue(error.cause is IllegalArgumentException)
                assertFalse(error.message!!.contains("private-token"))
                assertFalse(error.message!!.contains(other.url("/").toString()))
            }
            assertEquals(0, other.requestCount)
            assertEquals(0, engine.closed.get())
        } }
    }

    @Test fun excessiveFontCountsAreRejectedBeforeNativeAllocation() = runBlocking {
        MockWebServer().use { server ->
            prepare(server, JSONArray().apply { repeat(129) { i -> put(font("f$i.ttf", "/api/stream/7/fonts/f$i.ttf")) } })
            var created = false
            try {
                loadAssSubtitle(JzApi(server.url("/").toString()), 7, "movie", 2) { created = true; FakeEngine() }
                fail("Expected font-count rejection")
            } catch (error: AssSubtitleLoadException) {
                assertEquals(AssLoadStep.FONT_LIST, error.step)
                assertTrue(error.message!!.contains("129"))
                assertTrue(error.message!!.contains("128"))
            }
            assertFalse(created)
            assertEquals(2, server.requestCount)
        }
    }

    @Test fun multitrackMoviesWith96EntriesLoad76UniqueFontsOnce() = runBlocking {
        MockWebServer().use { server ->
            val entries = JSONArray().apply {
                repeat(76) { i -> put(font("f$i.ttf", "/api/stream/7/fonts/f$i.ttf")) }
                repeat(20) { i -> put(font("f$i.ttf", "/api/stream/7/fonts/f$i.ttf?kind=movie")) }
            }
            prepare(server, entries)
            repeat(76) { server.enqueue(MockResponse().setBody(Buffer().write(byteArrayOf(0, 127, -1)))) }
            val engine = loadAssSubtitle(JzApi(server.url("/").toString()), 7, "movie", 2) { FakeEngine() }
            assertEquals(76, engine.fonts.size)
            assertEquals(76, engine.fonts.map { it.first }.toSet().size)
            assertEquals(script, engine.source)
            assertEquals(78, server.requestCount)
            engine.close()
        }
    }

    @Test fun duplicatedEntriesDoNotConsumeTheUniqueFontLimit() = runBlocking {
        MockWebServer().use { server ->
            prepare(server, JSONArray().apply { repeat(200) { put(font()) } })
            server.enqueue(MockResponse().setBody(Buffer().write(byteArrayOf(1))))
            val engine = loadAssSubtitle(JzApi(server.url("/").toString()), 7, "movie", 2) { FakeEngine() }
            assertEquals(1, engine.fonts.size)
            assertEquals(3, server.requestCount)
            engine.close()
        }
    }

    @Test fun engineFailuresIdentifyTheInitializationStep() = runBlocking {
        MockWebServer().use { server ->
            prepare(server)
            try {
                loadAssSubtitle<FakeEngine>(JzApi(server.url("/").toString()), 7, "movie", 2) {
                    throw UnsatisfiedLinkError("sensitive native search path")
                }
                fail("Expected unavailable native library")
            } catch (error: AssSubtitleLoadException) {
                assertEquals(AssLoadStep.ENGINE, error.step)
                assertTrue(error.cause is UnsatisfiedLinkError)
                assertFalse(error.message!!.contains("sensitive"))
            }
        }
    }

    @Test fun totalFontBytesRemainBoundedAfterRaisingTheCountLimit() = runBlocking {
        MockWebServer().use { server ->
            prepare(server, JSONArray().apply {
                repeat(3) { i -> put(font("f$i.ttf", "/api/stream/7/fonts/f$i.ttf")) }
            })
            val bytes = ByteArray(24 * 1024 * 1024) { 42 }
            repeat(3) { server.enqueue(MockResponse().setBody(Buffer().write(bytes))) }
            val engine = FakeEngine()
            try {
                loadAssSubtitle(JzApi(server.url("/").toString()), 7, "movie", 2) { engine }
                fail("Expected total font byte limit")
            } catch (error: AssSubtitleLoadException) {
                assertEquals(AssLoadStep.FONT_DOWNLOAD, error.step)
                assertTrue(error.message!!.contains("64 MiB"))
            }
            assertEquals(2, engine.fonts.size)
            assertEquals(1, engine.closed.get())
            assertNull(engine.source)
        }
    }

    @Test fun cancellingADownloadClosesTheNewEngineWithoutWaitingForTimeout() = runBlocking {
        MockWebServer().use { server ->
            prepare(server, JSONArray().put(font()))
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            val engine = FakeEngine()
            val loading = async { loadAssSubtitle(JzApi(server.url("/").toString()), 7, "movie", 2) { engine } }
            withContext(Dispatchers.IO) {
                repeat(3) { assertNotNull(server.takeRequest(2, TimeUnit.SECONDS)) }
            }
            withTimeout(2000) { loading.cancelAndJoin() }
            assertTrue(loading.isCancelled)
            assertEquals(1, engine.closed.get())
            assertNull(engine.source)
        }
    }

    @Test fun cancellationDuringDispatchBackToTheCallerClosesTheCompletedEngine() = runBlocking {
        MockWebServer().use { server ->
            prepare(server)
            val executor = ThreadPoolExecutor(1, 1, 0, TimeUnit.SECONDS, LinkedBlockingQueue())
            executor.asCoroutineDispatcher().use { caller ->
                val nativeLoaded = CountDownLatch(1)
                val releaseNative = CountDownLatch(1)
                val callerHeld = CountDownLatch(1)
                val releaseCaller = CountDownLatch(1)
                val engine = FakeEngine {
                    nativeLoaded.countDown()
                    check(releaseNative.await(5, TimeUnit.SECONDS))
                }
                val loading = async(caller) { loadAssSubtitle(JzApi(server.url("/").toString()), 7, "movie", 2) { engine } }
                try {
                    withContext(Dispatchers.IO) { assertTrue(nativeLoaded.await(3, TimeUnit.SECONDS)) }
                    executor.execute {
                        callerHeld.countDown()
                        check(releaseCaller.await(5, TimeUnit.SECONDS))
                    }
                    withContext(Dispatchers.IO) { assertTrue(callerHeld.await(2, TimeUnit.SECONDS)) }
                    releaseNative.countDown()
                    withTimeout(2000) { while (executor.queue.isEmpty()) delay(5) }
                    loading.cancel(CancellationException("Subtitle selection changed"))
                    releaseCaller.countDown()
                    withTimeout(2000) { loading.join() }
                    assertTrue(loading.isCancelled)
                    assertEquals(script, engine.source)
                    assertEquals(1, engine.closed.get())
                } finally {
                    releaseNative.countDown()
                    releaseCaller.countDown()
                    loading.cancelAndJoin()
                }
            }
        }
    }
}
