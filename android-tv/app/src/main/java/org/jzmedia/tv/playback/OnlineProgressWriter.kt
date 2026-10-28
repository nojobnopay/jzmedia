package org.jzmedia.tv.playback

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeout
import org.jzmedia.tv.data.OnlineApi
import org.jzmedia.tv.data.OnlineIdentity
import org.jzmedia.tv.data.onlineErrorMessage

/** One writer per explicit playback run. A conflict never triggers an ownership-stealing re-claim. */
internal class OnlineProgressWriter(private val api: OnlineApi, private val identity: OnlineIdentity,
                                     private val scopeToken: String, revision: Long,
                                     private val onNotice: (String) -> Unit) {
    private val lock = Mutex()
    private var revision = revision
    private var writerId: String? = null
    private var sessionId: String? = null
    private var sequence = 0L
    var disabled: Boolean = false
        private set

    suspend fun prepare(session: String): Boolean = lock.withLock {
        if (disabled) return@withLock false
        if (sessionId == session && writerId != null) return@withLock true
        try {
            val claim = withTimeout(4000) { api.claim(session, revision, scopeToken) }
            check(claim.identity == identity && claim.writerRevision > revision && claim.scopeToken == scopeToken)
            writerId = claim.writerId; revision = claim.writerRevision; sessionId = session; sequence = 0
            true
        } catch (error: TimeoutCancellationException) { fail(error) }
        catch (error: CancellationException) { throw error }
        catch (error: Exception) { fail(error) }
    }

    suspend fun save(value: OnlineProgressSnapshot): Boolean = lock.withLock {
        val writer = writerId ?: return@withLock false
        if (disabled) return@withLock false
        try {
            val result = withTimeout(4000) { api.save(writer, revision, ++sequence, value.position,
                value.duration, value.paused, value.completed, scopeToken) }
            check(result.progress.identity == identity && result.progress.writerRevision == revision
                && result.scopeToken == scopeToken)
            true
        } catch (error: TimeoutCancellationException) { fail(error) }
        catch (error: CancellationException) { throw error }
        catch (error: Exception) { fail(error) }
    }

    private fun fail(error: Throwable): Boolean {
        disabled = true
        onNotice(onlineErrorMessage(error))
        return false
    }
}
