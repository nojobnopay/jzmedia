package org.jzmedia.tv.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.mediaTitle
import org.jzmedia.tv.data.posterPath
import org.jzmedia.tv.data.text
import org.jzmedia.tv.ui.generated.DesignTokens
import org.json.JSONObject

val Muted = DesignTokens.TextDim
val Panel = DesignTokens.SurfaceRaised
val Accent = DesignTokens.Accent

// Geometry remains native to each TV control; colors, corners and focus are shared.
@Composable
internal fun tvButtonColors(selected: Boolean = false, surface: Color = Panel) = ButtonDefaults.colors(
    containerColor = if (selected) Accent else surface,
    contentColor = DesignTokens.Text,
    focusedContainerColor = DesignTokens.FocusBackground,
    focusedContentColor = DesignTokens.FocusForeground,
    disabledContainerColor = surface.copy(alpha = .55f),
    disabledContentColor = DesignTokens.TextDim,
)

@Composable
internal fun tvButtonShape(card: Boolean = false) = ButtonDefaults.shape(
    shape = RoundedCornerShape(if (card) DesignTokens.CardRadius else DesignTokens.CornerRadius),
)

@Composable
internal fun tvButtonScale() = ButtonDefaults.scale(focusedScale = DesignTokens.FocusScale)

/** Selection remains visible on a white focused button, and is announced separately. */
internal fun Modifier.tvSelected(isSelected: Boolean): Modifier = semantics { selected = isSelected }.drawWithContent {
    drawContent()
    if (isSelected) {
        val markerWidth = 20.dp.toPx()
        drawRoundRect(Accent, Offset((size.width - markerWidth) / 2f, size.height - 4.dp.toPx()),
            Size(markerWidth, 3.dp.toPx()), CornerRadius(1.5.dp.toPx()))
    }
}

class FocusMemory(initial: String) {
    var key = initial
    val restoreKey = initial
    var restored = false
    // Removing a focused destination can briefly focus the navigation bar. Keep the
    // user's last target while this screen is leaving the composition.
    var leaving = false
}

@Composable
fun rememberFocusMemory(first: String): FocusMemory = rememberSaveable(saver = Saver(
    save = { it.key }, restore = { FocusMemory(it) },
)) { FocusMemory(first) }

fun Modifier.focusMemory(memory: FocusMemory, key: String, focusRequester: FocusRequester? = null): Modifier = composed {
    val fallback = remember { FocusRequester() }
    val requester = focusRequester ?: fallback
    LaunchedEffect(Unit) {
        if (!memory.restored && memory.restoreKey == key) {
            withFrameNanos { }
            requester.requestFocus()
            memory.restored = true
        }
    }
    this.focusRequester(requester).onFocusChanged { if (it.isFocused && !memory.leaving) memory.key = key }
}

@Composable
fun TvAction(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, selected: Boolean = false, enabled: Boolean = true,
             centerLabel: Boolean = false, icon: String? = null) {
    Button(
        onClick = onClick, enabled = enabled,
        modifier = modifier.heightIn(min = DesignTokens.ControlHeight).tvSelected(selected),
        colors = tvButtonColors(selected), shape = tvButtonShape(), scale = tvButtonScale(),
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp),
    ) {
        // Fixed-width keyboard actions remain centered; natural-width actions retain
        // their width, and full-width options keep their original start alignment.
        Row(modifier = if (centerLabel) Modifier.fillMaxWidth() else Modifier,
            horizontalArrangement = if (centerLabel) Arrangement.Center else Arrangement.Start,
            verticalAlignment = Alignment.CenterVertically) {
            if (icon != null) TvIcon(icon, Modifier.padding(end = 8.dp))
            Text(text, textAlign = if (centerLabel) TextAlign.Center else TextAlign.Start,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
fun TvInput(label: String, value: String, onValue: (String) -> Unit, modifier: Modifier = Modifier, password: Boolean = false) {
    var focused by remember { mutableStateOf(false) }
    val keyboard = LocalSoftwareKeyboardController.current
    val focus = LocalFocusManager.current
    Column(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = modifier) {
        Text(label, style = MaterialTheme.typography.labelLarge, color = Muted)
        BasicTextField(
            value = value, onValueChange = onValue, singleLine = true,
            textStyle = TextStyle(color = DesignTokens.TextStrong, fontSize = 19.sp),
            cursorBrush = SolidColor(DesignTokens.TextStrong),
            visualTransformation = if (password) PasswordVisualTransformation() else VisualTransformation.None,
            keyboardOptions = KeyboardOptions(keyboardType = if (password) KeyboardType.Password else KeyboardType.Text, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { keyboard?.hide(); focus.moveFocus(FocusDirection.Down) }),
            modifier = Modifier.fillMaxWidth().onFocusChanged { focused = it.isFocused }
                .onPreviewKeyEvent {
                    if (it.type == KeyEventType.KeyDown && it.key == Key.DirectionUp) {
                        focus.moveFocus(FocusDirection.Up)
                    } else if (it.type == KeyEventType.KeyDown && it.key == Key.DirectionDown) {
                        focus.moveFocus(FocusDirection.Down)
                    } else if (it.type == KeyEventType.KeyUp && (it.key == Key.DirectionCenter || it.key == Key.Enter)) {
                        keyboard?.show(); true
                    } else false
                }
                .border(if (focused) 3.dp else 1.dp, if (focused) DesignTokens.TextStrong else DesignTokens.Border, RoundedCornerShape(DesignTokens.CornerRadius))
                .background(Panel, RoundedCornerShape(DesignTokens.CornerRadius)).padding(16.dp),
        )
    }
}

private object Posters {
    val cache = object : LruCache<String, Bitmap>(20 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount
    }
    private val loads = SharedImageLoads<Pair<JzApi, String>, Bitmap?>()
    private val slots = Semaphore(4)

    suspend fun load(api: JzApi, path: String): Bitmap? {
        val url = api.absoluteUrl(path)
        cache.get(url)?.let { return it }
        return loads.get(api to url) {
            slots.withPermit {
                cache.get(url) ?: withContext(Dispatchers.IO) {
                    val bytes = api.getBytes(path)
                    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return@withContext null
                    val options = BitmapFactory.Options().apply {
                        inSampleSize = 1
                        while (maxOf(bounds.outWidth, bounds.outHeight) / inSampleSize > 640) inSampleSize *= 2
                    }
                    currentCoroutineContext().ensureActive()
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)?.also {
                        currentCoroutineContext().ensureActive()
                        cache.put(url, it)
                    }
                }
            }
        }
    }
}

@Composable
fun Poster(api: JzApi, path: String, title: String, modifier: Modifier = Modifier, fallbackPosterPath: String = "",
           posterPlaceholder: String = "", placeholderIcon: String = "image", showPlaceholderLabel: Boolean = true) {
    // A changed source gets fresh state in this composition, before the new load
    // starts. Restarting produceState alone would retain the previous bitmap.
    val bitmap by key(api, path, fallbackPosterPath) {
        produceState<Bitmap?>(null) {
            value = loadPosterWithFallback(path, fallbackPosterPath, onFailure = { attempt, error ->
                // Do not log URLs, response messages or credentials.
                Log.d("JzPoster", "Image attempt ${attempt + 1} failed (${error?.javaClass?.simpleName ?: "invalid image"})")
            }) { candidate ->
                Posters.load(api, candidate)
            }
        }
    }
    Box(modifier.background(Panel, RoundedCornerShape(DesignTokens.CornerRadius)), contentAlignment = Alignment.Center) {
        if (bitmap != null) Image(bitmap!!.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        else Column(Modifier.padding(8.dp), horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            TvIcon(placeholderIcon)
            if (showPlaceholderLabel) Text(posterPlaceholder.ifBlank { title }, style = MaterialTheme.typography.labelMedium,
                textAlign = TextAlign.Center, maxLines = 3, overflow = TextOverflow.Ellipsis, color = Muted)
        }
    }
}

@Composable
fun MediaCard(api: JzApi, row: JSONObject, onClick: () -> Unit, modifier: Modifier = Modifier, subtitle: String = "",
              fallbackPosterPath: String = "", posterPlaceholder: String = "", progressFraction: Float? = null,
              posterHeight: Dp = 180.dp) {
    val title = mediaTitle(row)
    Button(
        onClick = onClick, modifier = modifier.width(150.dp),
        contentPadding = PaddingValues(7.dp),
        shape = tvButtonShape(card = true), scale = tvButtonScale(),
        colors = tvButtonColors(surface = DesignTokens.Surface),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Box(Modifier.fillMaxWidth().height(posterHeight)) {
                Poster(api, posterPath(row), title, Modifier.fillMaxSize(),
                    fallbackPosterPath = fallbackPosterPath, posterPlaceholder = posterPlaceholder)
                progressFraction?.takeIf { it.isFinite() }?.let { fraction ->
                    Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(4.dp).background(Color.Black.copy(alpha = .6f))) {
                        Box(Modifier.fillMaxWidth(fraction.coerceIn(0f, 1f)).height(4.dp).background(Accent))
                    }
                }
            }
            Text(title, maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall, modifier = Modifier.heightIn(min = 42.dp))
            Text(subtitle.ifBlank {
                listOf(row.text("year"), row.text("vote_average").takeIf { it != "0" }.orEmpty(), if (row.optInt("watched") == 1) "已看" else "")
                    .filter { it.isNotBlank() }.joinToString(" · ").ifBlank { "查看详情" }
            }, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
fun MediaRail(title: String, rows: List<JSONObject>, api: JzApi, memory: FocusMemory, prefix: String, onClick: (JSONObject) -> Unit) {
    if (rows.isEmpty()) return
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(title, style = MaterialTheme.typography.headlineSmall)
        LazyRow(horizontalArrangement = Arrangement.spacedBy(18.dp), contentPadding = PaddingValues(8.dp)) {
            items(rows, key = { it.optLong("id") }) { row ->
                MediaCard(api, row, { onClick(row) }, Modifier.focusMemory(memory, "$prefix:${row.optLong("id")}"),
                    subtitle = row.text("subtitle"))
            }
        }
    }
}

@Composable
fun Status(message: String, retry: (() -> Unit)? = null, modifier: Modifier = Modifier, icon: String = if (retry != null) "error" else "info") {
    Column(modifier.fillMaxWidth().padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            TvIcon(icon)
            Text(message, color = Muted)
        }
        if (retry != null) TvAction("重试", retry, icon = "refresh")
    }
}

@Composable
fun SectionHeading(title: String, subtitle: String = "") {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = MaterialTheme.typography.headlineSmall)
        if (subtitle.isNotBlank()) Text(subtitle, color = Muted, style = MaterialTheme.typography.labelLarge)
    }
}
