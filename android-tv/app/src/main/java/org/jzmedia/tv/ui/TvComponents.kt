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
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.mediaTitle
import org.jzmedia.tv.data.posterPath
import org.jzmedia.tv.data.text
import org.json.JSONObject

val Muted = Color(0xFFAAAAAA)
val Panel = Color(0xFF262626)
val Accent = Color(0xFFE50914)

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
             centerLabel: Boolean = false) {
    Button(
        onClick = onClick, enabled = enabled, modifier = modifier,
        colors = ButtonDefaults.colors(
            containerColor = if (selected) Accent else Panel,
            contentColor = Color.White,
            focusedContainerColor = Color.White,
            focusedContentColor = Color(0xFF141414),
        ),
    ) {
        // Only explicitly sized actions fill their label; natural-width buttons and
        // full-width list entries retain their existing width/alignment contract.
        Text(text, modifier = if (centerLabel) Modifier.fillMaxWidth() else Modifier,
            textAlign = if (centerLabel) TextAlign.Center else TextAlign.Start,
            maxLines = 2, overflow = TextOverflow.Ellipsis)
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
            textStyle = TextStyle(color = Color.White, fontSize = 19.sp),
            cursorBrush = SolidColor(Color.White),
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
                .border(if (focused) 3.dp else 1.dp, if (focused) Color.White else Color(0xFF666666), RoundedCornerShape(8.dp))
                .background(Panel, RoundedCornerShape(8.dp)).padding(16.dp),
        )
    }
}

private object Posters {
    val cache = object : LruCache<String, Bitmap>(20 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap): Int = value.byteCount
    }
}

@Composable
fun Poster(api: JzApi, path: String, title: String, modifier: Modifier = Modifier, fallbackPosterPath: String = "",
           posterPlaceholder: String = "") {
    // A changed source gets fresh state in this composition, before the new load
    // starts. Restarting produceState alone would retain the previous bitmap.
    val bitmap by key(api, path, fallbackPosterPath) {
        produceState<Bitmap?>(null) {
            value = loadPosterWithFallback(path, fallbackPosterPath, onFailure = { attempt, error ->
                // Do not log URLs, response messages or credentials.
                Log.d("JzPoster", "Image attempt ${attempt + 1} failed (${error?.javaClass?.simpleName ?: "invalid image"})")
            }) { candidate ->
                val url = api.absoluteUrl(candidate)
                Posters.cache.get(url) ?: withContext(Dispatchers.IO) {
                    val bytes = api.getBytes(candidate)
                    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                    val options = BitmapFactory.Options().apply {
                        inSampleSize = (maxOf(bounds.outWidth, bounds.outHeight) / 640).coerceAtLeast(1)
                    }
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)?.also { Posters.cache.put(url, it) }
                }
            }
        }
    }
    Box(modifier.background(Panel, RoundedCornerShape(8.dp)), contentAlignment = Alignment.Center) {
        if (bitmap != null) Image(bitmap!!.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        else Text(posterPlaceholder.ifBlank { title.take(2) }, style = MaterialTheme.typography.headlineLarge, color = Muted)
    }
}

@Composable
fun MediaCard(api: JzApi, row: JSONObject, onClick: () -> Unit, modifier: Modifier = Modifier, subtitle: String = "",
              fallbackPosterPath: String = "", posterPlaceholder: String = "") {
    val title = mediaTitle(row)
    Button(
        onClick = onClick, modifier = modifier.width(150.dp),
        contentPadding = PaddingValues(7.dp),
        shape = ButtonDefaults.shape(shape = RoundedCornerShape(10.dp)),
        colors = ButtonDefaults.colors(containerColor = Color(0xFF1C1C1C), contentColor = Color.White,
            focusedContainerColor = Color.White, focusedContentColor = Color.Black),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Poster(api, posterPath(row), title, Modifier.fillMaxWidth().height(180.dp),
                fallbackPosterPath = fallbackPosterPath, posterPlaceholder = posterPlaceholder)
            Text(title, maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall, modifier = Modifier.height(42.dp))
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
fun Status(message: String, retry: (() -> Unit)? = null, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text(message, color = Muted)
        if (retry != null) TvAction("重试", retry)
    }
}

@Composable
fun SectionHeading(title: String, subtitle: String = "") {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = MaterialTheme.typography.headlineSmall)
        if (subtitle.isNotBlank()) Text(subtitle, color = Muted, style = MaterialTheme.typography.labelLarge)
    }
}
