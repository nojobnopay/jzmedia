package org.jzmedia.tv.playback

import java.io.IOException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class LocalPlaybackProgressTest {
    @Test fun pausedTicksAndExitDoNotReplaceAnotherDevicesBookmark() = runBlocking {
        val progress = LocalPlaybackProgress()
        var shared = 600.0
        val writes = mutableListOf<Double>()
        suspend fun save(position: Double) = progress.save(position, progress.nextSequence()) {
            writes.add(position); shared = position
        }
        progress.startAt(shared)
        save(600.0)
        save(610.0)
        shared = 2100.0
        repeat(3) { save(610.0) }
        assertEquals(2100.0, shared, 0.0)
        save(420.0)
        save(420.0)
        assertEquals(listOf(610.0, 420.0), writes)
    }

    @Test fun failedWriteRemainsDirtyAndLatestSeekWinsOverAnInflightPacket() = runBlocking {
        val progress = LocalPlaybackProgress()
        progress.startAt(10.0)
        var shared = 10.0
        try {
            progress.save(12.0, progress.nextSequence()) { throw IOException("disconnect") }
        } catch (_: IOException) { }
        progress.save(12.0, progress.nextSequence()) { shared = 12.0 }
        assertEquals(12.0, shared, 0.0)
        val writing = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        val older = async {
            progress.save(25.0, progress.nextSequence()) { writing.complete(Unit); release.await(); shared = 25.0 }
        }
        writing.await()
        val latest = progress.nextSequence()
        release.complete(Unit)
        progress.save(12.0, latest) { shared = 12.0 }
        older.await()
        assertEquals(12.0, shared, 0.0)
    }
}
