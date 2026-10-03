package org.jzmedia.tv.data

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class JzApiTest {
    @Test fun validatesConnectionAddressAndPreservesProxyPrefix() {
        val api = JzApi("http://localhost:8080/jzmedia", "secret")
        assertEquals("http://localhost:8080/jzmedia/", api.baseUrl)
        assertEquals("http://localhost:8080/jzmedia/api/movies?offset=36", api.absoluteUrl("/api/movies?offset=36"))
        listOf("http://user:password@localhost", "http://localhost/?token=x", "http://localhost/#hash", "file:///tmp/data", "localhost:8080").forEach {
            assertThrows(IllegalArgumentException::class.java) { JzApi(it) }
        }
        listOf("https://localhost:8080/jzmedia/a", "http://other.example/jzmedia/a", "//other.example/a", "../secret", "http://localhost:8080/secret").forEach {
            assertThrows(IllegalArgumentException::class.java) { api.absoluteUrl(it) }
        }
        assertTrue(api.authHeadersFor("http://other.example/").isEmpty())
        assertTrue(api.authHeadersFor("http://localhost:8080/outside/").isEmpty())
        assertEquals("secret", api.authHeadersFor(api.absoluteUrl("/api/health"))["X-Api-Token"])
        assertThrows(IllegalArgumentException::class.java) { JzApi("http://localhost/", "injected\r\nHeader: bad") }
    }

    @Test fun handshakeChecksProtocolAndWriteAuthentication() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("{\"protocol_version\":1,\"auth_required\":true}"))
            server.enqueue(MockResponse().setBody("{\"protocol_version\":1}"))
            val api = JzApi(server.url("/prefix").toString(), "test-token")
            assertEquals(1, api.checkConnection().optInt("protocol_version"))
            val first = server.takeRequest(1, TimeUnit.SECONDS)!!
            val second = server.takeRequest(1, TimeUnit.SECONDS)!!
            assertEquals("/prefix/api/stream/client-info", first.path)
            assertEquals("GET", first.method)
            assertEquals("POST", second.method)
            assertEquals("test-token", second.getHeader("X-Api-Token"))
            assertEquals("android_tv", JSONObject(second.body.readUtf8()).getString("client"))
        }
    }

    @Test fun incompatibleServerDoesNotRunPostHandshake() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("{\"protocol_version\":2}"))
            try { JzApi(server.url("/").toString()).checkConnection(); fail("Expected incompatibility") }
            catch (e: ApiException) { assertTrue(e.message!!.contains("不兼容")) }
            assertEquals(1, server.requestCount)
        }
    }

    @Test fun redirectAndErrorsNeverExposeTokenOrFollowAnotherOrigin() = runBlocking {
        MockWebServer().use { server -> MockWebServer().use { destination ->
            server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", destination.url("/api/movies")))
            val api = JzApi(server.url("/").toString(), "private-token")
            try { api.get("/api/movies"); fail("Expected redirect rejection") }
            catch (e: ApiException) { assertEquals(302, e.status) }
            assertEquals(0, destination.requestCount)
            server.enqueue(MockResponse().setResponseCode(401).setBody("private-token upstream secret"))
            try { api.post("/api/stream/client-check"); fail("Expected authentication failure") }
            catch (e: ApiException) {
                assertEquals(401, e.status)
                assertFalse(e.message!!.contains("private-token"))
                assertFalse(e.message!!.contains("upstream"))
            }
        } }
    }

    @Test fun cancellingRequestCompletesWithoutWaitingForReadTimeout() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            val request = async { JzApi(server.url("/").toString()).get("/api/movies") }
            kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                assertNotNull(server.takeRequest(2, TimeUnit.SECONDS))
            }
            withTimeout(2000) { request.cancelAndJoin() }
            assertTrue(request.isCancelled)
        }
    }

    @Test fun rejectsOversizedAndInvalidResponses() = runBlocking {
        MockWebServer().use { server ->
            val api = JzApi(server.url("/").toString())
            server.enqueue(MockResponse().setBody("<html>Proxy error</html>"))
            try { api.get("/api/movies"); fail("Expected JSON failure") }
            catch (e: ApiException) { assertTrue(e.message!!.contains("无法识别")) }
            server.enqueue(MockResponse().setBody("x".repeat(4 * 1024 * 1024 + 1)))
            try { api.getBytes("/posters/image.jpg"); fail("Expected image size limit") }
            catch (e: ApiException) { assertTrue(e.message!!.contains("过大")) }
        }
    }

    @Test fun queryEncodingDoesNotTurnSearchInputIntoNewParameters() {
        assertEquals("/api/search?q=A%26watched%3D1&limit=36", queryPath("/api/search", linkedMapOf("q" to "A&watched=1", "limit" to 36, "genre" to "", "year" to null)))
    }

    @Test fun slowBrowseTimesOutWhilePlaybackPreparationCanFinish() = runBlocking {
        MockWebServer().use { server ->
            server.dispatcher = object : okhttp3.mockwebserver.Dispatcher() {
                override fun dispatch(request: okhttp3.mockwebserver.RecordedRequest): MockResponse =
                    MockResponse().setHeadersDelay(16, TimeUnit.SECONDS).setBody("{\"ready\":true}")
            }
            val api = JzApi(server.url("/").toString())
            val browse = async {
                try { api.get("/api/movies"); fail("Browsing should time out promptly") }
                catch (e: ApiException) { assertTrue(e.message!!.contains("超时")) }
            }
            val preparing = async { api.postPlayback("/api/stream/1/sessions") }
            withTimeout(25_000) {
                browse.await()
                assertTrue(preparing.await().getBoolean("ready"))
            }
        }
    }
}
