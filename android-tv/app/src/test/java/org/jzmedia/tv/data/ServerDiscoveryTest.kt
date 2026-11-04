package org.jzmedia.tv.data

import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import okhttp3.MediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import okio.BufferedSource
import okio.Source
import okio.Timeout
import okio.buffer
import org.junit.Assert.*
import org.junit.Test

class ServerDiscoveryTest {
    @Test fun parsePortAcceptsTrimmedAsciiDigitsWithinTheTcpRange() {
        assertEquals(1, ServerDiscovery.parsePort("1"))
        assertEquals(8080, ServerDiscovery.parsePort("8080"))
        assertEquals(58080, ServerDiscovery.parsePort(" 58080\n"))
        assertEquals(65535, ServerDiscovery.parsePort("65535"))
        assertEquals(8080, ServerDiscovery.parsePort("0008080"))
    }

    @Test fun parsePortRejectsInvalidOrAmbiguousInput() {
        listOf("", " ", "0", "65536", "2147483648", "-1", "+8080", "80.80",
            "80 80", "58080-58090", "８０８０", "٨٠٨٠", "http://host:8080").forEach { value ->
            assertNull("Invalid discovery port: $value", ServerDiscovery.parsePort(value))
        }
    }

    @Test fun candidatesCoverSlash24ExceptSelfAndReserved() {
        val got = ServerDiscovery.candidatesFor("192.168.1.50")
        assertEquals(253, got.size)
        assertTrue(got.first() == "http://192.168.1.1:8080/")
        assertTrue(got.last() == "http://192.168.1.254:8080/")
        assertTrue(got.none { it.contains(".50:") || it.contains(".0:") || it.contains(".255:") })
    }

    @Test fun candidatesRejectBadInput() {
        assertTrue(ServerDiscovery.candidatesFor("").isEmpty())
        assertTrue(ServerDiscovery.candidatesFor("joey-ds425").isEmpty())
        assertTrue(ServerDiscovery.candidatesFor("::1").isEmpty())
        assertTrue(ServerDiscovery.candidatesFor("192.168.1.999").isEmpty())
    }

    @Test fun candidatesUseTheSelectedPortIncludingTcpBoundaries() {
        listOf(1, 58080, 65535).forEach { port ->
            val candidates = ServerDiscovery.candidatesFor("192.168.1.50", port)
            assertEquals(253, candidates.size)
            assertTrue(candidates.all { it.endsWith(":$port/") })
        }
    }

    @Test fun candidatesRejectPortsOutsideTheTcpRange() {
        listOf(Int.MIN_VALUE, -1, 0, 65536, Int.MAX_VALUE).forEach { port ->
            assertThrows(IllegalArgumentException::class.java) {
                ServerDiscovery.candidatesFor("192.168.1.50", port)
            }
        }
    }

    @Test fun parseAcceptsHandshakeAndFlagsProtocol() {
        val hit = ServerDiscovery.parse(
            "http://192.168.1.51:8080/",
            """{"protocol_version":1,"features":[],"auth_required":true,"name":"NAS","version":"0.20.3","server_id":"abc"}""",
        )!!
        assertEquals("NAS", hit.name)
        assertEquals("0.20.3", hit.version)
        assertEquals("abc", hit.serverId)
        assertTrue(hit.authRequired && hit.protocolOk)
        val old = ServerDiscovery.parse(
            "http://192.168.1.52:8080/",
            """{"protocol_version":0,"server_id":"old"}""",
        )!!
        assertFalse(old.protocolOk)
        assertEquals("jzmedia", old.name)
    }

    @Test fun parseSkipsNonServers() {
        assertNull(ServerDiscovery.parse("http://192.168.1.1:8080/", "<html>gateway</html>"))
        assertNull(ServerDiscovery.parse("http://192.168.1.2:8080/", """{"foo":1}"""))
        assertNull(ServerDiscovery.parse("http://192.168.1.3:8080/", ""))
    }

    @Test fun matchByIdFindsMovedServer() {
        val a = DiscoveredServer("http://192.168.1.51:8080/", "NAS", "0.20.3", "id-1", false, true)
        val b = DiscoveredServer("http://192.168.1.99:8080/", "Other", "0.20.3", "id-2", false, true)
        assertEquals(a, ServerDiscovery.matchById(listOf(a, b), "id-1"))
        assertNull(ServerDiscovery.matchById(listOf(a, b), "missing"))
        assertNull(ServerDiscovery.matchById(listOf(a, b), null))
    }

    @Test fun httpProbeAcceptsNormalAndChunkedHandshakes() {
        MockWebServer().use { server ->
            val handshake = """{"protocol_version":1,"server_id":"test-server","name":"家庭影院"}"""
            listOf(MockResponse().setBody(handshake), MockResponse().setChunkedBody(handshake, 7)).forEach { response ->
                server.enqueue(response)
                val hit = ServerDiscovery.httpProbe(server.url("/").toString())!!
                assertEquals("test-server", hit.serverId)
                assertEquals("家庭影院", hit.name)
                assertTrue(hit.protocolOk)
                assertEquals("/api/stream/client-info", server.takeRequest().path)
            }
        }
    }

    @Test fun httpProbeRejectsDeclaredOversizeWithoutReadingTheBody() {
        val body = CountingBody(declaredLength = 1024 * 1024)
        assertNull(ServerDiscovery.httpProbe("http://discovery.test/", clientReturning(body)))
        assertEquals(0L, body.bytesRead)
        assertTrue(body.closed)
    }

    @Test fun httpProbeBoundsUnknownLengthBodyBeforeDecoding() {
        val body = CountingBody(declaredLength = -1)
        assertNull(ServerDiscovery.httpProbe("http://discovery.test/", clientReturning(body)))
        // Okio may prefetch one 8 KiB segment beyond the 8193-byte check. The
        // virtual 1 MiB body must never be fully read, even without Content-Length.
        assertTrue(body.bytesRead in 8193L..16384L)
        assertTrue(body.closed)
    }

    @Test fun httpProbeRejectsOversizedChunkedHandshake() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setChunkedBody("x".repeat(8193), 1024))
            assertNull(ServerDiscovery.httpProbe(server.url("/").toString()))
        }
    }

    private fun clientReturning(body: ResponseBody) = OkHttpClient.Builder().addInterceptor { chain ->
        Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
            .code(200).message("OK").body(body).build()
    }.build()

    private class CountingBody(private val declaredLength: Long) : ResponseBody() {
        var bytesRead = 0L
            private set
        var closed = false
            private set
        private val buffered = object : Source {
            override fun read(sink: Buffer, byteCount: Long): Long {
                val count = minOf(byteCount, 1024 * 1024 - bytesRead)
                if (count == 0L) return -1
                sink.write(ByteArray(count.toInt()) { 'x'.code.toByte() })
                bytesRead += count
                return count
            }
            override fun timeout(): Timeout = Timeout.NONE
            override fun close() { closed = true }
        }.buffer()

        override fun contentType(): MediaType? = null
        override fun contentLength(): Long = declaredLength
        override fun source(): BufferedSource = buffered
    }

    @Test fun scanUsesInjectedProbeWithoutNetwork() = runBlocking {
        var progress = 0 to 0
        val got = ServerDiscovery.scan(
            locals = listOf("10.0.0.5"),
            probe = { target ->
                if (target == "http://10.0.0.9:8080/") {
                    DiscoveredServer(target, "NAS", "0.20.3", "id-9", false, true)
                } else null
            },
            onProgress = { done, total -> progress = done to total },
        )
        assertEquals(listOf("http://10.0.0.9:8080/"), got.map { it.address })
        assertEquals(253 to 253, progress)
    }

    @Test fun scanProbesOnlyTheSelectedPort() = runBlocking {
        val probes = AtomicInteger()
        val found = ServerDiscovery.scan(
            locals = listOf("10.0.0.5"),
            port = 58080,
            probe = { target ->
                assertTrue(target.endsWith(":58080/"))
                probes.incrementAndGet()
                if (target == "http://10.0.0.9:58080/") {
                    DiscoveredServer(target, "NAS", "1.0", "id-9", false, true)
                } else null
            },
        )
        assertEquals(253, probes.get())
        assertEquals(listOf("http://10.0.0.9:58080/"), found.map { it.address })
    }

    @Test fun scanRejectsInvalidPortBeforeAnyProbeOrProgress() = runBlocking {
        val probes = AtomicInteger()
        val progress = AtomicInteger()
        listOf(Int.MIN_VALUE, -1, 0, 65536, Int.MAX_VALUE).forEach { port ->
            try {
                ServerDiscovery.scan(
                    locals = listOf("10.0.0.5"),
                    port = port,
                    probe = { probes.incrementAndGet(); null },
                    onProgress = { _, _ -> progress.incrementAndGet() },
                )
                fail("Invalid discovery port must be rejected: $port")
            } catch (_: IllegalArgumentException) { }
        }
        assertEquals(0, probes.get())
        assertEquals(0, progress.get())
    }

    @Test fun cancelledScanDoesNotPublishAProbeThatReturnsLate() = runBlocking {
        val started = CountDownLatch(1)
        val release = CountDownLatch(1)
        val updates = AtomicInteger()
        val scan = launch {
            ServerDiscovery.scan(locals = listOf("10.0.0.5"), maxParallel = 1,
                probe = { target ->
                    started.countDown()
                    check(release.await(5, TimeUnit.SECONDS))
                    DiscoveredServer(target, "Late server", "1.0", "late", false, true)
                },
                onProgress = { _, _ -> updates.incrementAndGet() })
        }
        try {
            withContext(Dispatchers.IO) { assertTrue(started.await(5, TimeUnit.SECONDS)) }
            scan.cancel()
        } finally {
            release.countDown()
        }
        withTimeout(5000) { scan.join() }
        assertTrue(scan.isCancelled)
        assertEquals(0, updates.get())
    }
}
