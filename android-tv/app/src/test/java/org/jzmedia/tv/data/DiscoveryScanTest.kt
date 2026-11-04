package org.jzmedia.tv.data

import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.yield
import org.junit.Assert.*
import org.junit.Test

class DiscoveryScanTest {
    private val oldServer = DiscoveredServer("http://old.test/", "Old", "1.0", "old", false, true)
    private val newServer = DiscoveredServer("http://new.test/", "New", "1.0", "new", false, true)

    @Test fun normalScanPublishesProgressResultsAndCompletion() = runBlocking {
        val events = mutableListOf<String>()
        val runner = DiscoveryScan(this) { port, progress ->
            assertEquals(58080, port)
            progress(1, 2)
            yield()
            progress(2, 2)
            yield()
            listOf(newServer)
        }
        runner.start(
            58080,
            { done, total -> events += "$done/$total" },
            { events += "result:${it.single().serverId}" },
            { fail("Normal scan must not fail") },
            { events += "finished" },
        ).join()
        assertEquals(listOf("1/2", "2/2", "result:new", "finished"), events)
    }

    @Test fun stoppedScanCannotPublishOverItsReplacementOrFinishIt() = runBlocking {
        val oldStarted = CountDownLatch(1)
        val oldRelease = CountDownLatch(1)
        val newStarted = CountDownLatch(1)
        val newRelease = CountDownLatch(1)
        var invocation = 0
        var scanning = true
        var progressText = ""
        val results = mutableListOf<String>()
        val scannedPorts = mutableListOf<Int>()
        val runner = DiscoveryScan(this) { port, progress ->
            scannedPorts += port
            val first = invocation++ == 0
            // Model a blocking source that still delivers callbacks and returns
            // after cancellation. The coordinator must protect the current UI.
            withContext(NonCancellable + Dispatchers.IO) {
                if (first) {
                    oldStarted.countDown()
                    check(oldRelease.await(5, TimeUnit.SECONDS))
                    progress(1, 10)
                    listOf(oldServer)
                } else {
                    progress(7, 10)
                    newStarted.countDown()
                    check(newRelease.await(5, TimeUnit.SECONDS))
                    listOf(newServer)
                }
            }
        }
        fun start(port: Int) = runner.start(
            port,
            { done, total -> progressText = "$done/$total" },
            { results += it.single().serverId },
            { fail("Neither scan should report a failure") },
            { scanning = false },
        )
        val oldJob = start(8080)
        try {
            withContext(Dispatchers.IO) { assertTrue(oldStarted.await(5, TimeUnit.SECONDS)) }
            runner.cancel()
            scanning = false
            scanning = true
            val newJob = start(58080)
            withContext(Dispatchers.IO) { assertTrue(newStarted.await(5, TimeUnit.SECONDS)) }
            yield()
            assertEquals("7/10", progressText)
            oldRelease.countDown()
            withTimeout(5000) { oldJob.join() }
            yield()
            assertTrue(oldJob.isCancelled)
            assertTrue(newJob.isActive)
            assertTrue(scanning)
            assertEquals("7/10", progressText)
            assertTrue(results.isEmpty())
            newRelease.countDown()
            withTimeout(5000) { newJob.join() }
            assertFalse(scanning)
            assertEquals(listOf("new"), results)
            assertEquals(listOf(8080, 58080), scannedPorts)
        } finally {
            runner.cancel()
            oldRelease.countDown()
            newRelease.countDown()
        }
    }

    @Test fun failureCompletesTheCurrentScanWithoutReturningResults() = runBlocking {
        val events = mutableListOf<String>()
        val runner = DiscoveryScan(this) { _, _ -> throw IOException("Synthetic scan failure") }
        runner.start(
            65535,
            { _, _ -> fail("Failed scan must not report progress") },
            { fail("Failed scan must not return results") },
            { events += "failed" },
            { events += "finished" },
        ).join()
        assertEquals(listOf("failed", "finished"), events)
    }

    @Test fun replacedPortScanCannotPublishALateFailureOrCompletion() = runBlocking {
        val oldStarted = CompletableDeferred<Unit>()
        val oldRelease = CompletableDeferred<Unit>()
        val newStarted = CompletableDeferred<Unit>()
        val newRelease = CompletableDeferred<Unit>()
        val events = mutableListOf<String>()
        val runner = DiscoveryScan(this) { port, _ ->
            withContext(NonCancellable) {
                if (port == 8080) {
                    oldStarted.complete(Unit)
                    oldRelease.await()
                    throw IOException("Old port became unreachable")
                }
                newStarted.complete(Unit)
                newRelease.await()
                listOf(newServer)
            }
        }
        fun start(port: Int) = runner.start(
            port,
            { _, _ -> },
            { events += "result:$port" },
            { events += "failure:$port" },
            { events += "finished:$port" },
        )
        val oldJob = start(8080)
        try {
            withTimeout(5000) { oldStarted.await() }
            val newJob = start(58080)
            withTimeout(5000) { newStarted.await() }
            oldRelease.complete(Unit)
            withTimeout(5000) { oldJob.join() }
            assertTrue(oldJob.isCancelled)
            assertTrue(newJob.isActive)
            assertTrue(events.isEmpty())
            newRelease.complete(Unit)
            withTimeout(5000) { newJob.join() }
            assertEquals(listOf("result:58080", "finished:58080"), events)
        } finally {
            runner.cancel()
            oldRelease.complete(Unit)
            newRelease.complete(Unit)
        }
    }

    @Test fun invalidPortDoesNotStartAScanOrPublishCallbacks() = runBlocking {
        var scans = 0
        val runner = DiscoveryScan(this) { _, _ -> scans++; emptyList() }
        listOf(Int.MIN_VALUE, -1, 0, 65536, Int.MAX_VALUE).forEach { port ->
            assertThrows(IllegalArgumentException::class.java) {
                runner.start(
                    port,
                    { _, _ -> fail("Invalid port must not publish progress") },
                    { fail("Invalid port must not publish results") },
                    { fail("Invalid port must not publish failure") },
                    { fail("Invalid port must not publish completion") },
                )
            }
        }
        yield()
        assertEquals(0, scans)
    }
}
