package org.jzmedia.tv.playback

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeout
import org.jzmedia.tv.data.OnlineApi
import org.jzmedia.tv.data.ApiException
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
    private var retryNotice = false
    var disabled: Boolean = false
        private set

    suspend fun prepare(session: String): Boolean = lock.withLock {
        if (disabled) return@withLock false
        if (sessionId == session && writerId != null) return@withLock true
        writerId = null
        sessionId = null
        // Do not hold up the first frame for retries. The first decoded snapshot
        // can finish the same idempotent claim if a response was lost.
        val claimed = claim(session, attempts = 1)
        if (!disabled) sessionId = session
        claimed
    }

    private suspend fun claim(session: String, attempts: Int): Boolean =
        write(attempts) {
            val claim = api.claim(session, revision, scopeToken)
            check(claim.identity == identity && claim.writerRevision > revision && claim.scopeToken == scopeToken)
            writerId = claim.writerId; revision = claim.writerRevision; sequence = 0
        }

    suspend fun save(value: OnlineProgressSnapshot): Boolean = lock.withLock {
        if (disabled) return@withLock false
        val session = sessionId ?: return@withLock false
        if (writerId == null && !claim(session, attempts = 3)) return@withLock false
        val writer = checkNotNull(writerId)
        val packetSequence = ++sequence
        val saved = write(attempts = 3) {
            val result = api.save(writer, revision, packetSequence, value.position,
                value.duration, value.paused, value.completed, scopeToken)
            check(result.progress.identity == identity && result.progress.writerRevision == revision
                && result.scopeToken == scopeToken)
        }
        if (saved && retryNotice) { retryNotice = false; onNotice("观看进度已恢复保存。") }
        saved
    }

    private suspend fun write(attempts: Int, operation: suspend () -> Unit): Boolean {
        for (attempt in 1..attempts) {
            try {
                withTimeout(4000) { operation() }
                return true
            } catch (error: Exception) {
                currentCoroutineContext().ensureActive()
                if (error is CancellationException && error !is TimeoutCancellationException) throw error
                val transient = error is TimeoutCancellationException || error is ApiException &&
                    (error.status == 0 && error.code == "connection_failed" || error.status == 408 ||
                        error.status == 429 && !error.code.endsWith("_limit") ||
                        error.status in 500..599 && error.code !in setOf("online_source_disabled", "online_disabled"))
                if (!transient) {
                    disabled = true
                    onNotice(onlineErrorMessage(error))
                    return false
                }
                if (attempt < attempts) delay(250L * attempt)
            }
        }
        if (!retryNotice) { retryNotice = true; onNotice("观看进度暂未保存，将在后续保存时重试。") }
        return false
    }
}
