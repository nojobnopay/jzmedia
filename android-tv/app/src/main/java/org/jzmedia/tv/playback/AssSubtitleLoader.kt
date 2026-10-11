package org.jzmedia.tv.playback

import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.ApiException
import org.json.JSONArray

interface AssSubtitleEngine : AutoCloseable {
    fun addFont(name: String, bytes: ByteArray)
    fun load(source: String)
}

private const val MAX_ASS_FONTS = 128
private const val MAX_ASS_FONT_BYTES = 64L * 1024 * 1024

enum class AssLoadStep(val label: String) {
    SUBTITLE("字幕文件"), FONT_LIST("字体清单"), ENGINE("渲染组件"),
    FONT_DOWNLOAD("字体下载"), FONT_REGISTER("字体注册"), TRACK("字幕解析"),
}

private class AssFontLimitException(message: String) : IOException(message)

/** Controlled diagnostic text: no server URL, token, file path or subtitle contents. */
class AssSubtitleLoadException(val step: AssLoadStep, cause: Throwable) : IOException(
    "${step.label}：${when (cause) {
        is AssFontLimitException -> cause.message
        is ApiException -> if (cause.status > 0) "HTTP ${cause.status}" else cause.message
        else -> cause.javaClass.simpleName
    }}", cause,
)

private data class AssFont(val name: String, val url: String)

private fun uniqueFonts(api: JzApi, id: Long, kind: String, entries: JSONArray): List<AssFont> {
    val result = linkedMapOf<String, AssFont>()
    for (i in 0 until entries.length()) {
        val font = entries.getJSONObject(i)
        val name = font.optString("name")
        val path = font.optString("url")
        if (name.isBlank() || path.isBlank()) throw IOException("字幕字体清单无效")
        // Normalize before deduplicating, including attachment URLs from old servers.
        var url = api.absoluteUrl(path).toHttpUrl()
        if (font.optString("source") == "attachment" ||
            url.encodedPath.contains("/api/stream/$id/fonts/")
        ) url = url.newBuilder().setQueryParameter("kind", kind).build()
        val key = url.toString()
        if (!result.containsKey(key)) result[key] = AssFont(name, key)
    }
    if (result.size > MAX_ASS_FONTS) {
        throw AssFontLimitException("独立字体 ${result.size} 个，超过 $MAX_ASS_FONTS 个上限")
    }
    return result.values.toList()
}

/** Returns an owned engine only after the complete ASS script and its fonts are loaded. */
suspend fun <T : AssSubtitleEngine> loadAssSubtitle(
    api: JzApi, id: Long, kind: String, index: Int, createEngine: () -> T,
): T {
    require(kind in setOf("movie", "episode", "extra")) { "不支持的媒体类型" }
    var owned: T? = null
    var step = AssLoadStep.SUBTITLE
    try {
        val result = withContext(Dispatchers.Default) {
            val source = api.getText("/api/stream/$id/sub/$index.ass?kind=$kind")
            step = AssLoadStep.FONT_LIST
            val entries = api.getPlayback("/api/stream/$id/fonts?kind=$kind")
                .optJSONArray("fonts") ?: JSONArray()
            val fonts = uniqueFonts(api, id, kind, entries)
            currentCoroutineContext().ensureActive()
            step = AssLoadStep.ENGINE
            val engine = createEngine()
            owned = engine
            var totalBytes = 0L
            for (font in fonts) {
                currentCoroutineContext().ensureActive()
                step = AssLoadStep.FONT_DOWNLOAD
                val bytes = api.getFontBytes(font.url)
                totalBytes += bytes.size
                if (totalBytes > MAX_ASS_FONT_BYTES) throw AssFontLimitException("字体总量超过 64 MiB 上限")
                currentCoroutineContext().ensureActive()
                step = AssLoadStep.FONT_REGISTER
                engine.addFont(font.name, bytes)
            }
            // An empty font list uses the native engine's system-font fallback.
            currentCoroutineContext().ensureActive()
            step = AssLoadStep.TRACK
            engine.load(source)
            currentCoroutineContext().ensureActive()
            engine
        }
        currentCoroutineContext().ensureActive()
        return result
    } catch (error: Throwable) {
        // A cancelled withContext can discard its result while dispatching back to the
        // caller. Keep the reference outside that boundary so the native handle is closed.
        owned?.let { engine ->
            withContext(NonCancellable + Dispatchers.Default) {
                try { engine.close() }
                catch (closeError: Throwable) {
                    if (closeError !== error) error.addSuppressed(closeError)
                }
            }
        }
        if (error is CancellationException) throw error
        if (error is Exception || error is LinkageError) throw AssSubtitleLoadException(step, error)
        throw error
    }
}
