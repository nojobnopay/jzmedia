package org.jzmedia.tv.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.unit.sp
import androidx.tv.material3.Button
import androidx.tv.material3.ButtonDefaults
import androidx.tv.material3.Icon
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.LocalContentColor
import androidx.tv.material3.Text
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.coroutines.launch
import kotlin.math.roundToInt
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.castActorKey
import org.jzmedia.tv.data.mediaCardSubtitle
import org.jzmedia.tv.data.mediaLibraryLabel
import org.jzmedia.tv.data.mediaRatingLabel
import org.jzmedia.tv.data.mediaTitle
import org.jzmedia.tv.data.mediaWatched
import org.jzmedia.tv.data.posterPath
import org.jzmedia.tv.data.text
import org.jzmedia.tv.ui.generated.DesignIcons
import org.jzmedia.tv.ui.generated.DesignTokens
import org.json.JSONObject

val Muted = DesignTokens.TextDim
val Panel = DesignTokens.SurfaceRaised
val Accent = DesignTokens.Accent
/** “已看”绿与网页 --jz-green 同值，海报/详情/分集共用。 */
val Watched = Color(0xFF7ED321)

internal val MediaCardPadding = 8.dp
internal val MediaCardArtworkGap = 8.dp
internal val MediaCardTextGap = 4.dp

@Composable
internal fun tvDialogMaxHeight(fraction: Float): Dp {
    val height = LocalWindowInfo.current.containerSize.height
    return with(LocalDensity.current) { (height * fraction).toDp() }
}

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
             centerLabel: Boolean = false, icon: String? = null, fillContent: Boolean = false) {
    Button(
        onClick = onClick, enabled = enabled,
        modifier = modifier.heightIn(min = DesignTokens.ControlHeight).tvSelected(selected),
        colors = tvButtonColors(selected), shape = tvButtonShape(), scale = tvButtonScale(),
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp),
    ) {
        // centerLabel: fixed-width keys keep icon+text as one centered group.
        // fillContent: full-width lists fill the button and start-align (icon left, text left).
        // Natural-width actions keep wrap width and rely on Button centering.
        Row(modifier = if (centerLabel || fillContent) Modifier.fillMaxWidth() else Modifier,
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
    Box(modifier.clip(RoundedCornerShape(DesignTokens.CornerRadius)).background(Panel), contentAlignment = Alignment.Center) {
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
fun DetailBackdrop(api: JzApi, path: String, modifier: Modifier = Modifier) {
    if (path.isBlank()) return
    val bitmap by key(api, path) {
        produceState<Bitmap?>(null) {
            value = loadPosterWithFallback(path, "", onFailure = { attempt, error ->
                Log.d("JzBackdrop", "Image attempt ${attempt + 1} failed (${error?.javaClass?.simpleName ?: "invalid image"})")
            }) { candidate ->
                Posters.load(api, candidate)
            }
        }
    }
    if (bitmap == null) return
    Box(modifier) {
        // 网页同款：取图偏上（object-position center 25%），左侧压暗放海报、底部下沉接底色。
        Image(bitmap!!.asImageBitmap(), null, Modifier.matchParentSize(),
            alignment = Alignment.TopCenter, contentScale = ContentScale.Crop)
        Box(Modifier.matchParentSize().background(Brush.horizontalGradient(colorStops = arrayOf(
            0f to DesignTokens.Background.copy(alpha = .96f),
            .48f to DesignTokens.Background.copy(alpha = .86f),
            1f to DesignTokens.Background.copy(alpha = .30f)))))
        Box(Modifier.matchParentSize().background(Brush.verticalGradient(colorStops = arrayOf(
            .30f to Color.Transparent,
            1f to DesignTokens.Background))))
    }
}

@Composable
fun CastAvatar(api: JzApi, path: String, name: String, modifier: Modifier = Modifier) {
    val bitmap by key(api, path) {
        produceState<Bitmap?>(null) {
            value = if (path.isBlank()) null else loadPosterWithFallback(path, "", onFailure = { _, _ -> }) { candidate ->
                Posters.load(api, candidate)
            }
        }
    }
    Box(modifier.clip(CircleShape).background(Panel), contentAlignment = Alignment.Center) {
        if (bitmap != null) Image(bitmap!!.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        else TvIcon("person")
    }
}

@Composable
fun CastCard(api: JzApi, person: JSONObject, character: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    // 对标海报卡：TV Button 进焦点体系，内容换成圆形头像+名字。
    // 卡片贴内容（头像 64 + 两边 12），列内占满居中，头像不可能偏。
    val name = person.text("name").ifBlank { "未知演员" }
    Button(onClick, modifier.width(88.dp).semantics {
        contentDescription = if (character.isBlank()) name else "$name $character"
    }, contentPadding = PaddingValues(8.dp), shape = tvButtonShape(card = true), scale = tvButtonScale(),
        colors = tvButtonColors(surface = DesignTokens.Surface)) {
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(4.dp)) {
            CastAvatar(api, org.jzmedia.tv.data.castAvatarPath(person), name, Modifier.size(64.dp))
            Text(name, style = MaterialTheme.typography.labelMedium, textAlign = TextAlign.Center,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (character.isNotBlank()) Text(character, style = MaterialTheme.typography.labelSmall,
                color = Muted, textAlign = TextAlign.Center,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

@Composable
fun CastWall(api: JzApi, cast: List<JSONObject>, originalLanguage: String, memory: FocusMemory,
             onPerson: (JSONObject) -> Unit, modifier: Modifier = Modifier) {
    if (cast.isEmpty()) return
    // 单行横滚：左右进后排、上下进出；卡片走焦点记忆，与海报墙同手感。
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionHeading("演职员", "${cast.size} 人")
        LazyRow(horizontalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(8.dp)) {
            itemsIndexed(cast, key = { index, person -> "${castActorKey(person)}#$index" }) { _, person ->
                CastCard(api, person, org.jzmedia.tv.data.castCharacter(person, originalLanguage), { onPerson(person) },
                    Modifier.focusMemory(memory, "cast:${castActorKey(person)}"))
            }
        }
    }
}

@Composable
internal fun TextDialog(title: String, lines: List<String>, onClose: () -> Unit) {
    val dialogHeight = tvDialogMaxHeight(.88f)
    val scroll = rememberScrollState()
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current
    val textFocus = remember { FocusRequester() }
    val closeFocus = remember { FocusRequester() }
    var viewportPx by remember { mutableStateOf(0) }
    var textFocused by remember { mutableStateOf(false) }
    // 正文默认样式的行高：实测结果出来前做兜底。
    val styleLinePx = with(density) { MaterialTheme.typography.bodyLarge.lineHeight.toPx().toInt() }
    // 各段正文实测（行数, 文本高度px）：行高一律按实际渲染结果算，不估。
    var measured by remember(lines) { mutableStateOf(mapOf<Int, Pair<Int, Int>>()) }
    val avgLinePx = textDialogAvgLine(
        measured.values.sumOf { it.second }, measured.values.sumOf { it.first }, styleLinePx)
    // 上次已处理按键的系统时间：紧随事件按连发处理，
    // 覆盖“一次按压发多个首按事件”的遥控器。用数组免重组也能读到最新值。
    val lastHandled = remember { longArrayOf(0L) }
    LaunchedEffect(Unit) {
        withFrameNanos { }
        textFocus.requestFocus()
    }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Column(Modifier.fillMaxWidth(.8f).heightIn(max = dialogHeight).background(Panel, RoundedCornerShape(DesignTokens.DialogRadius)).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text(title, style = MaterialTheme.typography.headlineSmall)
            // 长文本单段也可能超出弹窗：整块作为一个可聚焦滚动区，
            // 点按按可见行数的三分之一翻页、按住连发逐行滚；
            // 到底/到顶后按键不再消费，焦点让给关闭按钮。
            Box(Modifier.weight(1f, fill = false).fillMaxWidth()
                .focusRequester(textFocus)
                .focusProperties { down = closeFocus }
                .onFocusChanged { textFocused = it.isFocused }
                .border(2.dp, if (textFocused) DesignTokens.TextStrong else Color.Transparent, RoundedCornerShape(DesignTokens.CornerRadius))
                .drawWithContent {
                    drawContent()
                    // 右缘滚动条只指示位置，不参与焦点；滑块按可视/全文比例定位。
                    val max = scroll.maxValue
                    if (max > 0) {
                        val barW = 4.dp.toPx()
                        val inset = 8.dp.toPx()
                        val trackH = (size.height - inset * 2).coerceAtLeast(0f)
                        val left = size.width - inset - barW
                        drawRoundRect(DesignTokens.Border, Offset(left, inset), Size(barW, trackH),
                            CornerRadius(barW / 2, barW / 2))
                        textDialogThumb(scroll.value, max, trackH.roundToInt(), 32.dp.toPx().roundToInt())?.let { (top, h) ->
                            drawRoundRect(DesignTokens.TextDim, Offset(left, inset + top), Size(barW, h.toFloat()),
                                CornerRadius(barW / 2, barW / 2))
                        }
                    }
                }
                .focusable()
                .onPreviewKeyEvent { event ->
                    if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                    // 首按翻“实测可见行数的三分之一”，紧随连击/按住连发改 1 行瞬时滚：
                    // 点按跟手、连按不断片。落点对齐整行，顶行永远完整显示。
                    val down = when (event.key) {
                        Key.DirectionDown -> true
                        Key.DirectionUp -> false
                        else -> return@onPreviewKeyEvent false
                    }
                    val native = event.nativeKeyEvent
                    val following = textDialogIsRepeat(native.repeatCount, native.eventTime, lastHandled[0])
                    val step = textDialogStepPx(following, viewportPx,
                        with(density) { 400.dp.toPx().toInt() }, avgLinePx)
                    val raw = textDialogScrollTarget(scroll.value, scroll.maxValue, step, down = down)
                        ?: return@onPreviewKeyEvent false
                    val target = textDialogSnapTarget(raw, scroll.value, scroll.maxValue, avgLinePx, down = down)
                    // 连发用瞬时滚动：多个动画叠加是抖动的根因；点按保留动画跟手感。
                    scope.launch { if (following) scroll.scrollTo(target) else scroll.animateScrollTo(target) }
                    lastHandled[0] = native.eventTime
                    true
                }
                .onSizeChanged { viewportPx = it.height }
                .verticalScroll(scroll)
                .semantics { contentDescription = "$title 全文" }) {
                Column(Modifier.fillMaxWidth().padding(start = 12.dp, top = 12.dp, bottom = 12.dp,
                    end = if (scroll.maxValue > 0) 20.dp else 12.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    lines.forEachIndexed { index, text ->
                        Text(text, onTextLayout = { result ->
                            val count = result.lineCount
                            val mark = count to
                                (if (count > 0) result.getLineBottom(count - 1).roundToInt() else 0)
                            if (measured[index] != mark) measured = measured + (index to mark)
                        })
                    }
                }
            }
            // 滚动提示只占位不参与焦点；不可滚时不显示。用派生状态读滚动位置，
            // 滚动中途不重组（逐像素重组在弱芯片上会抖），只在三态切换时更新。
            val canScrollHint by remember { derivedStateOf { scroll.maxValue > 0 } }
            val atEndHint by remember { derivedStateOf { scroll.maxValue > 0 && scroll.value >= scroll.maxValue } }
            val pastTopHint by remember { derivedStateOf { scroll.value > 0 } }
            if (canScrollHint) {
                val hint = when {
                    atEndHint -> "已到末尾，按 ↑ 回看"
                    pastTopHint -> "继续按 ↑ ↓ 滚动"
                    else -> "按 ↓ 滚动查看更多"
                }
                Text(hint, color = Muted, style = MaterialTheme.typography.labelMedium,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            TvAction("关闭", onClose, Modifier.focusRequester(closeFocus).focusProperties { up = textFocus })
        }
    }
}

/** 兜底页高（实测视口和行高不可用时）：约三分之一可视高度，保证首屏按键也有反应。 */
internal fun textDialogPagePx(viewportPx: Int, fallbackPx: Int): Int =
    if (viewportPx > 0) (viewportPx / 3).coerceAtLeast(1) else fallbackPx.coerceAtLeast(1)

/**
 * 本次按键的滚动步长（px）：首按按“可见行数的三分之一行”翻页，
 * 紧随连击/按住连发按 [repeatLines] 行逐行滚。行高由调用方实测传入，跟随系统字号；
 * 行高未知时退回按视口像素步进，保证任何情况都有反应。
 */
internal fun textDialogStepPx(
    repeat: Boolean,
    viewportPx: Int,
    fallbackPx: Int,
    linePx: Int,
    repeatLines: Int = 1,
): Int {
    val viewport = (if (viewportPx > 0) viewportPx else fallbackPx).coerceAtLeast(1)
    val line = if (linePx > 0) linePx else (viewport / 15).coerceAtLeast(1)
    if (repeat) return (line * repeatLines).coerceAtLeast(1)
    if (linePx <= 0) return textDialogPagePx(viewportPx, fallbackPx)
    val visibleLines = (viewport / line).coerceAtLeast(1)
    return ((visibleLines / 3).coerceAtLeast(1) * line).coerceAtLeast(1)
}

/** 实测平均行高：正文 onTextLayout 汇总的总高度/总行数；无实测时用样式行高兜底。 */
internal fun textDialogAvgLine(totalH: Int, totalLines: Int, fallbackPx: Int): Int =
    if (totalLines > 0 && totalH > 0) (totalH / totalLines).coerceAtLeast(1) else fallbackPx.coerceAtLeast(1)

/**
 * 紧随事件判定：系统 repeat 事件，或与上个已处理按键间隔不足 [windowMs]。
 * 后者覆盖“一次按压发多个首按事件”的遥控器；正常点按间隔远大于窗口，不受影响。
 */
internal fun textDialogIsRepeat(repeatCount: Int, eventTimeMs: Long, lastHandledMs: Long, windowMs: Long = 180L): Boolean =
    repeatCount > 0 || (lastHandledMs > 0 && eventTimeMs - lastHandledMs < windowMs)

/**
 * 返回本次按键应滚动到的位置；返回 null 表示已到边界，不消费按键、
 * 把焦点让给下一个控件（到底后下键到关闭按钮）。
 */
internal fun textDialogScrollTarget(value: Int, max: Int, page: Int, down: Boolean): Int? =
    if (down) {
        if (value < max) (value + page).coerceAtMost(max) else null
    } else {
        if (value > 0) (value - page).coerceAtLeast(0) else null
    }

/**
 * 滚动条滑块在轨道内的 (top, height)，均为 px；不可滚时返回 null。
 * 高度按 可视²/全文 占比，设最小值保证短屏上仍可见。
 */
internal fun textDialogThumb(value: Int, max: Int, trackPx: Int, minThumbPx: Int): Pair<Int, Int>? {
    if (max <= 0 || trackPx <= 0) return null
    val height = (trackPx.toFloat() / (trackPx + max) * trackPx).toInt()
        .coerceIn(minThumbPx.coerceAtMost(trackPx), trackPx)
    val top = (value.toFloat() / max * (trackPx - height)).toInt().coerceIn(0, trackPx - height)
    return top to height
}

/**
 * 落点向下对齐到整行高度，保证停住时顶行是完整的；到底时不做对齐、
 * 原样返回 max，下一次下键才能把焦点让给关闭按钮。
 */
internal fun textDialogSnapTarget(target: Int, value: Int, max: Int, linePx: Int, down: Boolean): Int {
    if (target >= max || linePx <= 0) return target.coerceIn(0, max)
    var snapped = (target / linePx) * linePx
    // 下滚且步长不足一行时保证至少前进一步，避免按键无反应；上滚天然有进展。
    if (down && snapped <= value) snapped = value + linePx
    return snapped.coerceIn(0, max)
}

@Composable
fun WatchedBadge(modifier: Modifier = Modifier) {
    Row(modifier = modifier
        .background(DesignTokens.Background.copy(alpha = .9f), RoundedCornerShape(4.dp))
        .padding(horizontal = 6.dp, vertical = 3.dp),
        horizontalArrangement = Arrangement.spacedBy(3.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Icon(DesignIcons.get("check"), contentDescription = null,
            modifier = Modifier.size(12.dp), tint = Watched)
        Text("已看", color = Watched, style = MaterialTheme.typography.labelSmall,
            maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
fun MediaCard(api: JzApi, row: JSONObject, onClick: () -> Unit, modifier: Modifier = Modifier, subtitle: String = "",
              fallbackPosterPath: String = "", posterPlaceholder: String = "", progressFraction: Float? = null,
              posterHeight: Dp = 180.dp, watched: Boolean? = null) {
    val title = mediaTitle(row)
    val library = mediaLibraryLabel(row)
    val rating = mediaRatingLabel(row)
    val caption = subtitle.ifBlank { mediaCardSubtitle(row) }
    val showWatched = watched ?: mediaWatched(row)
    Button(
        onClick = onClick, modifier = modifier.width(150.dp),
        contentPadding = PaddingValues(MediaCardPadding),
        shape = tvButtonShape(card = true), scale = tvButtonScale(),
        colors = tvButtonColors(surface = DesignTokens.Surface),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(MediaCardArtworkGap)) {
            Box(Modifier.fillMaxWidth().height(posterHeight)) {
                Poster(api, posterPath(row), title, Modifier.fillMaxSize(),
                    fallbackPosterPath = fallbackPosterPath, posterPlaceholder = posterPlaceholder)
                if (rating.isNotBlank()) Text(rating,
                    modifier = Modifier.align(Alignment.TopEnd).padding(5.dp)
                        .background(DesignTokens.Background.copy(alpha = .9f), RoundedCornerShape(4.dp))
                        .padding(horizontal = 6.dp, vertical = 3.dp),
                    color = DesignTokens.TextStrong, style = MaterialTheme.typography.labelSmall,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (showWatched) WatchedBadge(Modifier.align(Alignment.TopStart).padding(5.dp))
                if (library.isNotBlank()) Text(library,
                    modifier = Modifier.align(Alignment.BottomStart).fillMaxWidth()
                        .background(DesignTokens.Background.copy(alpha = .9f))
                        .padding(horizontal = 6.dp, vertical = 6.dp),
                    color = DesignTokens.TextStrong, style = MaterialTheme.typography.labelSmall,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
                progressFraction?.takeIf { it.isFinite() }?.let { fraction ->
                    Box(Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(4.dp).background(Color.Black.copy(alpha = .6f))) {
                        Box(Modifier.fillMaxWidth(fraction.coerceIn(0f, 1f)).height(4.dp).background(Accent))
                    }
                }
            }
            // The card ends after its actual text; short titles do not reserve a
            // second line or an empty footer. Grid rows align at the poster top.
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(MediaCardTextGap)) {
                Text(title, maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
                if (caption.isNotBlank()) Text(caption, maxLines = 1,
                    overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.labelSmall,
                    color = LocalContentColor.current.copy(alpha = .72f))
            }
        }
    }
}

@Composable
fun MediaRail(title: String, rows: List<JSONObject>, api: JzApi, memory: FocusMemory, prefix: String, onClick: (JSONObject) -> Unit) {
    if (rows.isEmpty()) return
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(title, style = MaterialTheme.typography.headlineSmall)
        LazyRow(horizontalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(8.dp)) {
            items(rows, key = { it.optLong("id") }) { row ->
                MediaCard(api, row, { onClick(row) }, Modifier.focusMemory(memory, "$prefix:${row.optLong("id")}"),
                    subtitle = row.text("subtitle"))
            }
        }
    }
}

@Composable
fun Status(message: String, retry: (() -> Unit)? = null, modifier: Modifier = Modifier, icon: String = if (retry != null) "error" else "info") {
    Column(modifier.fillMaxWidth().padding(vertical = 20.dp, horizontal = 8.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            TvIcon(icon)
            Text(message, color = Muted)
        }
        if (retry != null) TvAction("重试", retry, icon = "refresh")
    }
}

@Composable
fun SectionHeading(title: String, subtitle: String = "") {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f),
            maxLines = 2, overflow = TextOverflow.Ellipsis)
        if (subtitle.isNotBlank()) Text(subtitle, modifier = Modifier.widthIn(max = 300.dp), color = Muted,
            style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}
