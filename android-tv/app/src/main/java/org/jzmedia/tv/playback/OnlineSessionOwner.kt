package org.jzmedia.tv.playback

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.supervisorScope
import kotlinx.coroutines.withTimeoutOrNull
import org.jzmedia.tv.data.ApiException
import java.util.Collections

/** Retired sessions remain owned until deletion is confirmed, including during final exit. */
internal class OnlineSessionOwner(private val release: suspend (String) -> Unit,
                                   private val timeoutMillis: Long = 3000) {
    init { require(timeoutMillis in 1..3000) }
    private val owned = Collections.synchronizedSet(mutableSetOf<String>())
    fun own(session: String) { owned.add(session) }
    fun pending(): Set<String> = synchronized(owned) { owned.toSet() }
    suspend fun release(session: String) {
        try {
            if (withTimeoutOrNull(timeoutMillis) { release.invoke(session); true } == true) owned.remove(session)
        } catch (error: CancellationException) { throw error }
        catch (error: ApiException) { if (error.status in listOf(404, 410)) owned.remove(session) }
        catch (_: Exception) { /* An unconfirmed release is retried by final close, then server idle cleanup. */ }
    }
    suspend fun close() = supervisorScope {
        pending().forEach { session -> launch { release(session) } }
    }
}
