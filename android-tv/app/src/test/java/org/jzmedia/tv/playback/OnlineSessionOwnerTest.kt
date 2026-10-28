package org.jzmedia.tv.playback

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.*
import org.junit.Test
import org.jzmedia.tv.data.ApiException

class OnlineSessionOwnerTest {
    @Test fun finalCloseConfirmsRetiredSessionsEvenWhileAnEarlierDeleteIsStillPending() = runBlocking {
        val pending = CompletableDeferred<Unit>()
        val entered = CompletableDeferred<Unit>()
        var attempts = 0
        val owner = OnlineSessionOwner({
            if (++attempts == 1) { entered.complete(Unit); pending.await() }
        })
        owner.own("retired-session")
        val initial = launch { owner.release("retired-session") }
        entered.await()
        val exit = async { owner.close() }
        exit.await()
        assertEquals(2, attempts)
        assertTrue(owner.pending().isEmpty())
        initial.cancelAndJoin()
    }
    @Test fun confirmedAndAlreadyAbsentSessionsAreNotLeftForTheNextPlayback() = runBlocking {
        val released = mutableListOf<String>()
        val owner = OnlineSessionOwner({ session ->
            released.add(session)
            if (session == "absent") throw ApiException(404, "already released")
        })
        owner.own("first"); owner.own("absent"); owner.own("second")
        owner.release("first")
        owner.close()
        assertEquals(setOf("first", "absent", "second"), released.toSet())
        assertEquals(1, released.count { it == "first" })
        assertTrue(owner.pending().isEmpty())
    }
    @Test fun cancelledUnconfirmedReleaseRemainsOwnedForBoundedFinalCleanup() = runBlocking {
        val entered = CompletableDeferred<Unit>()
        var count = 0
        val owner = OnlineSessionOwner({
            if (++count == 1) { entered.complete(Unit); CompletableDeferred<Unit>().await() }
        })
        owner.own("session")
        val pending = launch { owner.release("session") }
        entered.await(); pending.cancelAndJoin(); yield()
        assertEquals(setOf("session"), owner.pending())
        owner.close()
        assertTrue(owner.pending().isEmpty()); assertEquals(2, count)
    }
}
