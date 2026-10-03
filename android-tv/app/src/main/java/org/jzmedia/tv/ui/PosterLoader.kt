package org.jzmedia.tv.ui

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive

/** Tries distinct, nonblank paths in order. A null result means decoding failed. */
internal suspend fun <T : Any> loadPosterWithFallback(
    path: String,
    fallbackPath: String,
    onFailure: (attempt: Int, error: Exception?) -> Unit,
    load: suspend (String) -> T?,
): T? {
    currentCoroutineContext().ensureActive()
    for ((attempt, candidate) in listOf(path, fallbackPath).filter { it.isNotBlank() }.distinct().withIndex()) {
        currentCoroutineContext().ensureActive()
        val result = try {
            load(candidate).also { currentCoroutineContext().ensureActive() }
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            currentCoroutineContext().ensureActive()
            onFailure(attempt, error)
            continue
        }
        if (result != null) return result
        onFailure(attempt, null)
    }
    return null
}
