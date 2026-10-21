package org.jzmedia.tv.playback

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.supervisorScope

/** Local release is synchronous; final writes survive cancellation of playback preparation. */
internal class PlaybackExit(
    private val playback: CoroutineScope,
    private val writes: CoroutineScope,
    private val releaseLocal: () -> Unit,
    private val save: suspend (Double, Double, Long) -> Unit,
    private val releaseSession: suspend (String) -> Unit,
    private val awaitPendingWrites: suspend () -> Unit,
    private val releaseNetwork: () -> Unit,
    private val onFailure: (Exception) -> Unit,
) {
    private var closed = false

    fun close(position: Double?, duration: Double, sequence: Long, session: String?): Job? {
        if (closed) return null
        closed = true
        releaseLocal()
        playback.cancel()
        return writes.launch {
            suspend fun attempt(action: suspend () -> Unit) {
                try { action() }
                catch (error: CancellationException) { throw error }
                catch (error: Exception) { onFailure(error) }
            }
            try {
                supervisorScope {
                    launch { if (position != null) attempt { save(position, duration, sequence) } }
                    launch { if (session != null) attempt { releaseSession(session) } }
                }
                awaitPendingWrites()
            } finally {
                try { releaseNetwork() }
                finally { writes.cancel() }
            }
        }
    }
}
