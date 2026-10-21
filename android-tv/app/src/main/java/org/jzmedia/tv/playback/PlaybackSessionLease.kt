package org.jzmedia.tv.playback

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

/** Own a newly created server session even if preparation is cancelled before adoption. */
internal class PlaybackSessionLease(
    private val isCurrent: () -> Boolean,
    private val release: suspend (String) -> Unit,
) {
    var sessionId: String? = null
        private set

    suspend fun <T> create(request: suspend () -> T, id: (T) -> String): T {
        val result = request()
        sessionId = id(result)
        currentCoroutineContext().ensureActive()
        if (!isCurrent()) throw CancellationException("Obsolete playback preparation")
        return result
    }

    suspend fun releaseUnlessAdopted(activeSession: String?) {
        val created = sessionId ?: return
        if (!isCurrent() || activeSession != created) {
            sessionId = null
            withContext(NonCancellable) { release(created) }
        }
    }
}
