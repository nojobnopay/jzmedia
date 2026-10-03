package org.jzmedia.tv.ui

import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Test

class SharedImageLoadsTest {
    @Test fun visibleConsumersShareOneLoadAndOneCancellationDoesNotCancelTheOther() = runBlocking {
        val loads = SharedImageLoads<String, String>()
        val calls = AtomicInteger()
        val started = CompletableDeferred<Unit>()
        val finish = CompletableDeferred<Unit>()
        val first = async(start = CoroutineStart.UNDISPATCHED) {
            loads.get("poster") { calls.incrementAndGet(); started.complete(Unit); finish.await(); "image" }
        }
        withTimeout(2000) { started.await() }
        val second = async(start = CoroutineStart.UNDISPATCHED) { loads.get("poster") { error("Duplicate request") } }
        first.cancelAndJoin()
        finish.complete(Unit)
        assertEquals("image", withTimeout(2000) { second.await() })
        assertEquals(1, calls.get())
    }

    @Test fun lastConsumerLeavingCancelsRequestAndAllowsRetry() = runBlocking {
        val loads = SharedImageLoads<String, String>()
        val started = CompletableDeferred<Unit>()
        val stopped = CompletableDeferred<Unit>()
        val first = async(start = CoroutineStart.UNDISPATCHED) {
            loads.get("poster") {
                try { started.complete(Unit); awaitCancellation() }
                finally { stopped.complete(Unit) }
            }
        }
        withTimeout(2000) { started.await() }
        first.cancelAndJoin()
        withTimeout(2000) { stopped.await() }
        assertEquals("fresh", loads.get("poster") { "fresh" })
    }

    @Test fun failedImagesDoNotPoisonLaterRequestsOrOtherImages() = runBlocking {
        val loads = SharedImageLoads<String, String>()
        try { loads.get("broken") { throw IllegalStateException("decode failed") }; fail("Expected failure") }
        catch (_: IllegalStateException) { }
        assertEquals("retry", loads.get("broken") { "retry" })
        assertEquals("other", loads.get("other") { "other" })
    }
}
