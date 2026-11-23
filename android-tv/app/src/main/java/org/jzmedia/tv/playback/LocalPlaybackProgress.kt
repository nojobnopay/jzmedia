package org.jzmedia.tv.playback

import java.util.concurrent.atomic.AtomicLong
import kotlin.math.roundToLong
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** The shared bookmark changes only when this player has actually moved. */
internal class LocalPlaybackProgress {
    private val lock = Mutex()
    private val sequence = AtomicLong()
    private var savedMillis: Long? = null

    fun startAt(position: Double) { savedMillis = (position * 1000).roundToLong() }
    fun nextSequence(): Long = sequence.incrementAndGet()

    suspend fun save(position: Double, ticket: Long, write: suspend () -> Unit) = lock.withLock {
        val millis = (position * 1000).roundToLong()
        if (ticket != sequence.get() || millis == savedMillis) return@withLock
        // On an ambiguous network failure a subsequent explicit seek must be able
        // to return even to the last acknowledged position.
        savedMillis = null
        write()
        savedMillis = millis
    }
}
