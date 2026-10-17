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

data class SeekPreviewManifest(val interval: Double, val count: Int, val width: Int, val height: Int,
                               val columns: Int, val rows: Int, val pages: List<String>) {
    fun frameAt(time: Double): SeekPreviewFrame? {
        if (!time.isFinite() || time < 0 || count <= 0) return null
        val index = (time / interval).toInt().coerceIn(0, count - 1)
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
            return SeekPreviewManifest(interval, count, width, height, columns, rows, pages)
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
