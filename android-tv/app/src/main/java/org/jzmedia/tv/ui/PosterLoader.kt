package org.jzmedia.tv.ui

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive

internal enum class PosterSize(private val maxEdge: Int, private val maxPixels: Int) {
    CARD(640, 640 * 640),
    BACKDROP(1920, 1920 * 1080);

    /** Power-of-two subsampling preserves aspect ratio and stays inside both bounds. */
    fun sampleSize(width: Int, height: Int): Int {
        require(width > 0 && height > 0)
        var w = width.toLong()
        var h = height.toLong()
        var sample = 1
        while (maxOf(w, h) > maxEdge || w * h > maxPixels) {
            w = (w + 1) / 2
            h = (h + 1) / 2
            sample *= 2
        }
        return sample
    }
}

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
