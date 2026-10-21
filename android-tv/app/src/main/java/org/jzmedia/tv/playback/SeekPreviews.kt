package org.jzmedia.tv.playback

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.produceState
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.withContext
import org.jzmedia.tv.data.JzApi
import org.json.JSONObject

data class SeekPreviewFrame(val page: String, val x: Int, val y: Int, val width: Int, val height: Int)

data class SeekPreviewCoverage(val availableCount: Int, val end: Double, val duration: Double,
                               val warning: String)

data class SeekPreviewManifest(val interval: Double, val count: Int, val width: Int, val height: Int,
                               val columns: Int, val rows: Int, val pages: List<String>,
                               val coverage: SeekPreviewCoverage? = null) {
    fun missingTailAt(time: Double): Boolean =
        coverage?.let { time.isFinite() && time >= it.end } == true

    fun frameAt(time: Double): SeekPreviewFrame? {
        if (!time.isFinite() || time < 0 || count <= 0) return null
        if (missingTailAt(time)) return null
        val index = (time / interval).toInt().coerceIn(0, count - 1)
        if (coverage != null && index >= coverage.availableCount) return null
        val cells = columns * rows
        val page = pages.getOrNull(index / cells) ?: return null
        val cell = index % cells
        return SeekPreviewFrame(page, (cell % columns) * width, (cell / columns) * height, width, height)
    }

    companion object {
        fun fromJson(value: JSONObject): SeekPreviewManifest? {
            val interval = value.optDouble("interval")
            val count = value.optInt("count")
            val width = value.optInt("width")
            val height = value.optInt("height")
            val columns = value.optInt("columns")
            val rows = value.optInt("rows")
            if (!interval.isFinite() || interval <= 0 || count !in 1..10000 || width !in 1..1000 ||
                height !in 1..1000 || columns !in 1..20 || rows !in 1..20) return null
            val source = value.optJSONArray("pages") ?: return null
            val pages = (0 until source.length()).map { source.optString(it) }
            if (pages.any { !it.startsWith("/api/stream/previews/cache/") }) return null
            var coverage: SeekPreviewCoverage? = null
            if (hasCoverageDeclaration(value)) {
                val availableValue = value.opt("available_count")
                if (availableValue !is Int && availableValue !is Long) return null
                val availableLong = (availableValue as Number).toLong()
                if (availableLong !in 1 until count.toLong()) return null
                val available = availableLong.toInt()
                val countValue = value.opt("count")
                if ((countValue !is Int && countValue !is Long)
                        || (countValue as Number).toLong() != count.toLong()) return null
                val duration = (value.opt("duration") as? Number)?.toDouble() ?: return null
                val end = (value.opt("coverage_end") as? Number)?.toDouble() ?: return null
                val warning = value.optJSONObject("warning") ?: return null
                val message = warning.opt("message") as? String ?: return null
                if (value.optString("state") != "ready" || value.optString("completion") != "known_tail_missing"
                        || !duration.isFinite() || duration <= 0 || duration > 86400
                        || kotlin.math.ceil(duration / interval) != count.toDouble()
                        || !end.isFinite() || end <= 0 || end != minOf(available * interval, duration)
                        || pages.size != (available + columns * rows - 1) / (columns * rows)
                        || warning.optString("code") != "confirmed_tail_damage" || message.isBlank()
                        || message.length > 500) return null
                coverage = SeekPreviewCoverage(available, end, duration, message)
            }
            return SeekPreviewManifest(interval, count, width, height, columns, rows, pages, coverage)
        }
    }
}

private fun hasCoverageDeclaration(value: JSONObject): Boolean =
    value.has("available_count") || value.has("coverage_end") || value.has("completion") ||
        value.optJSONObject("warning")?.optString("code") == "confirmed_tail_damage"

internal data class SeekPreviewStatus(val state: String, val manifest: SeekPreviewManifest? = null,
                                     val error: String = "") {
    val actionLabel: String get() = when (state) {
        "ready" -> "进度预览已生成"
        "running" -> "正在生成进度预览"
        else -> if (manifest?.pages?.isNotEmpty() == true) "继续生成进度预览" else "生成进度预览"
    }
    val canGenerate: Boolean get() = state !in setOf("running", "ready", "loading")
    val warning: String? get() = if (state == "ready") manifest?.coverage?.warning else null
    val coverageDescription: String? get() = if (state == "ready") manifest?.coverage?.let {
        "预览覆盖至 ${playbackTime(it.end)} / ${playbackTime(it.duration)}"
    } else null
    fun seekNotice(time: Double): String? = if (manifest?.missingTailAt(time) == true) "片尾缺少预览" else null

    companion object {
        fun fromJson(value: JSONObject): SeekPreviewStatus {
            val manifest = SeekPreviewManifest.fromJson(value)
            if (manifest == null && hasCoverageDeclaration(value)) {
                return SeekPreviewStatus("failed", error = "进度预览覆盖信息无效，请稍后重试")
            }
            return SeekPreviewStatus(value.optString("state", "missing"), manifest, value.optString("error", ""))
        }
    }
}

// A 5×5 sheet of 480×270 ARGB cells uses about 12.4 MiB; retain two nearby pages.
class SeekPreviewImages : LruCache<String, Bitmap>(32 * 1024 * 1024) {
    override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount
}

@Composable
fun SeekPreviewImage(api: JzApi, frame: SeekPreviewFrame, images: SeekPreviewImages) {
    // The page URL stays stable while moving between its 25 cells, so only the first cell loads it.
    val sheet by key(api, frame.page) {
        produceState<Bitmap?>(null) {
            value = images.get(frame.page) ?: try {
                val bytes = api.getBytes(frame.page)
                withContext(Dispatchers.Default) {
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply {
                        inScaled = false
                        inPreferredConfig = Bitmap.Config.ARGB_8888
                    })
                }
                    ?.also { images.put(frame.page, it) }
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { null }
        }
    }
    val bitmap = sheet ?: return
    if (frame.x + frame.width > bitmap.width || frame.y + frame.height > bitmap.height) return
    Canvas(Modifier.width(240.dp).height(135.dp).border(1.dp, Color.White.copy(alpha = .65f))) {
        drawImage(bitmap.asImageBitmap(), srcOffset = IntOffset(frame.x, frame.y),
            srcSize = IntSize(frame.width, frame.height),
            dstOffset = IntOffset.Zero,
            dstSize = IntSize(size.width.toInt(), size.height.toInt()),
            filterQuality = FilterQuality.High)
    }
}
