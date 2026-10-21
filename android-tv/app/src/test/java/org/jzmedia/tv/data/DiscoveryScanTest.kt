package org.jzmedia.tv.data

import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
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
        val runner = DiscoveryScan(this) { progress ->
            progress(1, 2)
            yield()
            progress(2, 2)
            yield()
            listOf(newServer)
        }
        runner.start(
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
        val runner = DiscoveryScan(this) { progress ->
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
        fun start() = runner.start(
            { done, total -> progressText = "$done/$total" },
            { results += it.single().serverId },
            { fail("Neither scan should report a failure") },
            { scanning = false },
        )
        val oldJob = start()
        try {
            withContext(Dispatchers.IO) { assertTrue(oldStarted.await(5, TimeUnit.SECONDS)) }
            runner.cancel()
            scanning = false
            scanning = true
            val newJob = start()
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
        } finally {
            runner.cancel()
            oldRelease.countDown()
            newRelease.countDown()
        }
    }

    @Test fun failureCompletesTheCurrentScanWithoutReturningResults() = runBlocking {
        val events = mutableListOf<String>()
        val runner = DiscoveryScan(this) { throw IOException("Synthetic scan failure") }
        runner.start(
            { _, _ -> fail("Failed scan must not report progress") },
            { fail("Failed scan must not return results") },
            { events += "failed" },
            { events += "finished" },
        ).join()
        assertEquals(listOf("failed", "finished"), events)
    }
}
